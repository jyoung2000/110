"""Scheduler — background task that scrapes due sources and runs discovery."""
import asyncio
from datetime import datetime, timedelta
from src.scraper.engine import scraper_engine
from src.scraper.discovery import discovery_engine
from src.scheduler.source_manager import source_manager
from src.storage.config_store import config_store
from src.storage.site_profiles import site_profiles
from src.utils.logging import setup_logging

logger = setup_logging("scheduler")


class Scheduler:
    """Background scheduler for automatic scraping and discovery."""

    def __init__(self):
        self._running = False
        self._paused = False
        self._task: asyncio.Task = None
        self._discovery_task: asyncio.Task = None
        self._last_check: str = ""
        self._next_discovery: str = ""

    async def start(self):
        """Start the scheduler background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Scheduler started")

    async def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped")

    def pause(self):
        self._paused = True
        logger.info("Scheduler paused")

    def resume(self):
        self._paused = False
        logger.info("Scheduler resumed")

    async def _run_loop(self):
        """Main scheduler loop — handles scraping. Discovery runs concurrently."""
        # Initial delay to let everything start up
        await asyncio.sleep(10)

        # Launch discovery as a concurrent background task
        self._discovery_task = asyncio.create_task(self._discovery_loop())

        while self._running:
            try:
                if not self._paused:
                    self._last_check = datetime.now().isoformat()
                    await self._check_and_scrape()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}")

            # Check interval from config
            interval = config_store.get("scheduler", "check_interval_minutes", default=5)
            await asyncio.sleep(interval * 60)

    async def _discovery_loop(self):
        """Independent discovery loop running concurrently with scraping."""
        # Initial delay — let scraping start first
        await asyncio.sleep(30)

        while self._running:
            try:
                if not self._paused:
                    await self._check_discovery()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Discovery loop error: {e}")

            # Poll every 5 minutes; _check_discovery checks its own interval
            await asyncio.sleep(300)

    async def _check_and_scrape(self):
        """Check all enabled sources and scrape any that are due."""
        if scraper_engine.is_paused:
            logger.debug("Engine paused, skipping scheduled scrape")
            return

        if scraper_engine.is_busy:
            logger.debug("Engine busy, skipping scheduled scrape")
            return

        sources = source_manager.get_enabled_sources()
        now = datetime.now()

        # Cap schedule_hours to prevent adaptive scheduling from making sources dormant
        MAX_SCHEDULE_HOURS = 48

        due_sources = []
        skipped_not_due = []
        skipped_failures = []

        for source in sources:
            name = source.get("name", source.get("url", "unknown"))

            # Auto-disable sources with too many consecutive failures
            if source.get("consecutive_failures", 0) >= 5:
                if source.get("enabled", True):
                    source_manager.update_source(source["id"], enabled=False)
                    logger.info(f"Auto-disabled source {name}: too many consecutive failures")
                skipped_failures.append(name)
                continue

            if not source.get("enabled", True):
                continue

            # Cap runaway schedule_hours
            schedule_hours = source.get("schedule_hours", 12)
            if schedule_hours > MAX_SCHEDULE_HOURS:
                schedule_hours = MAX_SCHEDULE_HOURS
                source_manager.update_source(source["id"], schedule_hours=MAX_SCHEDULE_HOURS)
                logger.info(f"Capped schedule_hours to {MAX_SCHEDULE_HOURS}h for {name}")

            # Check if due
            last_scraped = source.get("last_scraped")
            if last_scraped:
                try:
                    last_dt = datetime.fromisoformat(last_scraped)
                    remaining = timedelta(hours=schedule_hours) - (now - last_dt)
                    if remaining > timedelta(0):
                        skipped_not_due.append((name, remaining))
                        continue
                except (ValueError, TypeError):
                    pass  # Invalid timestamp — treat as due

            due_sources.append(source)

        # Log summary of what happened
        logger.info(
            f"Source check: {len(due_sources)} due, "
            f"{len(skipped_not_due)} not due, "
            f"{len(skipped_failures)} failed/disabled"
        )
        if skipped_not_due:
            for sname, remaining in skipped_not_due[:5]:
                hours_left = remaining.total_seconds() / 3600
                logger.debug(f"  Not due: {sname} ({hours_left:.1f}h remaining)")

        # If no sources are due but we have sources, force-scrape the one
        # that was scraped longest ago (prevents permanent stall)
        if not due_sources and sources:
            oldest = None
            oldest_dt = now
            for source in sources:
                if source.get("consecutive_failures", 0) >= 5:
                    continue
                if not source.get("enabled", True):
                    continue
                last_scraped = source.get("last_scraped")
                if not last_scraped:
                    oldest = source
                    break  # Never scraped — top priority
                try:
                    dt = datetime.fromisoformat(last_scraped)
                    if dt < oldest_dt:
                        oldest_dt = dt
                        oldest = source
                except (ValueError, TypeError):
                    oldest = source
                    break
            if oldest:
                age = now - oldest_dt if oldest.get("last_scraped") else None
                age_str = f"{age.total_seconds()/3600:.1f}h ago" if age else "never"
                logger.info(f"No sources due — force-scraping oldest: {oldest.get('name')} (last: {age_str})")
                due_sources = [oldest]

        # Scrape due sources
        for source in due_sources:
            if scraper_engine.is_busy:
                break

            name = source.get("name", source.get("url", "unknown"))

            # Pick a URL for this scrape — use a fresh gallery/category page
            # from the site profile if available, to get varied wallpapers
            scrape_url = source["url"]
            try:
                profile = site_profiles.get(source["url"])
                # Ensure the source's main URL is in the gallery rotation
                profile.add_gallery_url(source["url"], source.get("name", "main"))
                fresh_url = profile.get_fresh_gallery_url()
                if fresh_url and fresh_url != source["url"]:
                    logger.info(f"Using fresh gallery URL for {name}: {fresh_url}")
                    scrape_url = fresh_url
            except Exception:
                pass

            logger.info(f"Scheduled scrape: {name} -> {scrape_url}")
            try:
                job = await scraper_engine.scrape_url(
                    url=scrape_url,
                    source_id=source["id"],
                    source_name=source.get("name", ""),
                    max_pages=source.get("max_pages", 10),
                )
                # Record results
                source_manager.record_scrape(
                    source["id"],
                    uploaded=job.images_uploaded,
                    dupes=job.duplicates,
                    errors=job.errors,
                )

                # Adaptive scheduling based on source quality
                try:
                    if job.images_uploaded > 0:
                        source_manager.record_source_productive(source["id"])
                        quality = source_manager.compute_source_quality(source["id"])
                        if quality >= 70:
                            new_hours = max(4, source.get("schedule_hours", 12) - 2)
                            source_manager.update_source(source["id"], schedule_hours=new_hours)
                    elif job.images_found == 0:
                        quality = source_manager.compute_source_quality(source["id"])
                        if quality < 20:
                            new_hours = min(MAX_SCHEDULE_HOURS, source.get("schedule_hours", 12) * 2)
                            source_manager.update_source(source["id"], schedule_hours=new_hours)
                except Exception as e:
                    logger.debug(f"Adaptive scheduling error: {e}")

            except Exception as e:
                logger.error(f"Scheduled scrape failed for {name}: {e}")
                source_manager.record_scrape(source["id"], 0, 0, 1)

            # Small delay between sources
            await asyncio.sleep(5)

    async def _check_discovery(self):
        """Run discovery if interval has elapsed."""
        disc = source_manager.discovery_state
        if not disc.get("enabled", True):
            return

        interval = disc.get("interval_hours", 6)
        last_run = disc.get("last_discovery_run")

        should_run = False
        if not last_run:
            should_run = True
        else:
            try:
                last_dt = datetime.fromisoformat(last_run)
                if datetime.now() - last_dt >= timedelta(hours=interval):
                    should_run = True
            except (ValueError, TypeError):
                should_run = True

        if should_run:
            logger.info("Running scheduled discovery")
            try:
                await discovery_engine.run_discovery()
            except Exception as e:
                logger.error(f"Discovery failed: {e}")

    @property
    def status(self) -> dict:
        return {
            "running": self._running,
            "paused": self._paused,
            "last_check": self._last_check,
            "discovery_state": source_manager.discovery_state,
        }


# Singleton
scheduler = Scheduler()
