"""Scraper engine — full pipeline from URL to Baserow."""
import re
import uuid
import asyncio
import random
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from src.scraper.browser import browser_manager
from src.scraper.adapters.generic import GenericAdapter
from src.scraper.nsfw_filter import is_nsfw_page, is_nsfw_image
from src.downloader.manager import DownloadManager
from src.downloader.compressor import ImageCompressor
from src.downloader.validator import ImageValidator
from src.downloader.enhancer import ImageEnhancer
from src.ai.captioner import AICaptioner
from src.storage.baserow import BaserowClient
from src.storage.config_store import config_store
from src.storage.activity_store import ActivityStore, ActivityEntry, activity_store
from src.storage.site_profiles import site_profiles
from src.scraper.discovery import discovery_engine
from src.metadata.schemas import WallpaperMetadata
from src.utils.aspect_ratio import calculate_aspect_ratio, is_mobile
from src.utils.paths import data_path
from src.utils.logging import setup_logging

logger = setup_logging("engine")

TEMP_DIR = data_path("temp")


class ScrapeResult:
    """Result of a single image scrape."""
    def __init__(self):
        self.success = False
        self.status = "error"
        self.error = ""
        self.img_hash = ""
        self.baserow_row_id = None


class ScrapeJob:
    """Represents a scrape job with progress tracking."""
    def __init__(self, job_id: str, url: str, source_id: str = "", source_name: str = "", max_pages: int = 10):
        self.id = job_id
        self.url = url
        self.source_id = source_id
        self.source_name = source_name
        self.max_pages = max_pages
        self.status = "pending"
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.pages_scraped = 0
        self.images_found = 0
        self.images_downloaded = 0
        self.images_uploaded = 0
        self.duplicates = 0
        self.errors = 0
        self.error_log: list[str] = []
        self.progress = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "pages_scraped": self.pages_scraped,
            "images_found": self.images_found,
            "images_downloaded": self.images_downloaded,
            "images_uploaded": self.images_uploaded,
            "duplicates": self.duplicates,
            "errors": self.errors,
            "error_log": self.error_log[-20:],
            "progress": self.progress,
            "max_pages": self.max_pages,
        }


class ScraperEngine:
    """Main scraper engine orchestrating the full pipeline."""

    def __init__(self):
        max_dl = config_store.get("scraping", "max_concurrent_downloads", default=3)
        dl_delay = config_store.get("scraping", "download_delay_seconds", default=1.0)
        self.downloader = DownloadManager(max_concurrent=max_dl, min_delay=dl_delay)
        self.compressor = ImageCompressor()
        self.captioner = AICaptioner()
        self.baserow = BaserowClient()
        self._job_lock = asyncio.Lock()
        self._current_job: Optional[ScrapeJob] = None
        self._job_history: list[dict] = []
        self._stop_event = asyncio.Event()
        self._engine_paused = False
        # Cross-job dedup caches — survive across scheduler runs as long as
        # the process is alive.  Prevents re-downloading images that were
        # already processed in a previous job.
        # Maps hash → max pixel count (width*height) so upgrades still work.
        # Seeded from the activity store on startup so dedup survives restarts.
        self._processed_hashes: dict[str, int] = {}
        self._processed_urls: set[str] = set()
        self._seed_hash_cache()

    def _seed_hash_cache(self):
        """Seed the in-memory hash cache from persisted activity data.

        This ensures the dedup cache survives container/process restarts.
        Without this, previously-scraped wallpapers would be re-downloaded
        and potentially re-uploaded as duplicates after every restart.
        """
        try:
            from src.storage.activity_store import activity_store
            stored = activity_store.get_all_hash_pixels()
            if stored:
                self._processed_hashes.update(stored)
                logger.info(f"Seeded hash cache with {len(stored)} entries from activity store")
        except Exception as e:
            logger.warning(f"Failed to seed hash cache: {e}")

    def _get_adapter(self) -> GenericAdapter:
        """Get adapter configured with current scraping settings."""
        min_w = config_store.get("scraping", "min_width", default=800)
        min_h = config_store.get("scraping", "min_height", default=600)
        return GenericAdapter(min_width=min_w, min_height=min_h)

    def _get_validator(self) -> ImageValidator:
        """Get validator configured with current scraping settings."""
        min_w = config_store.get("scraping", "min_width", default=800)
        min_h = config_store.get("scraping", "min_height", default=600)
        allowed_aspects = config_store.get("scraping", "allowed_aspects", default=[])
        allow_mobile = config_store.get("scraping", "allow_mobile", default=True)
        watermark_detection = config_store.get("scraping", "watermark_detection", default=True)
        return ImageValidator(
            min_width=min_w, min_height=min_h,
            allowed_aspects=allowed_aspects, allow_mobile=allow_mobile,
            watermark_detection=watermark_detection,
        )

    def _get_enhancer(self) -> ImageEnhancer:
        """Get enhancer configured with current scraping settings."""
        min_w = config_store.get("scraping", "min_width", default=800)
        min_h = config_store.get("scraping", "min_height", default=600)
        max_upscale = config_store.get("scraping", "max_enhance_upscale", default=2.5)
        return ImageEnhancer(min_width=min_w, min_height=min_h, max_upscale=max_upscale)

    async def initialize(self):
        """Initialize engine components (non-fatal)."""
        try:
            await browser_manager.initialize()
        except Exception as e:
            logger.warning(f"Browser init failed (non-fatal): {e}")

        try:
            await self.captioner.initialize()
        except Exception as e:
            logger.warning(f"AI captioner init failed (non-fatal): {e}")

        # Load Baserow config
        try:
            cfg = config_store.get_section("baserow")
            self.baserow = BaserowClient(
                api_url=cfg.get("api_url", ""),
                api_token=cfg.get("api_token", ""),
                table_id=cfg.get("table_id", 0),
            )
            self.baserow.field_mapping = cfg.get("field_mapping", {})
        except Exception as e:
            logger.warning(f"Baserow config load failed: {e}")

    def update_baserow_config(self):
        """Reload Baserow config from store."""
        cfg = config_store.get_section("baserow")
        self.baserow = BaserowClient(
            api_url=cfg.get("api_url", ""),
            api_token=cfg.get("api_token", ""),
            table_id=cfg.get("table_id", 0),
        )
        self.baserow.field_mapping = cfg.get("field_mapping", {})

    # ------------------------------------------------------------------
    # Stop / Resume controls
    # ------------------------------------------------------------------

    def stop_all(self):
        """Stop the current job immediately and pause the engine.

        The running pipeline checks the stop event at every major loop
        iteration and exits gracefully.  The engine stays paused (no new
        jobs accepted) until ``resume_engine()`` is called.
        """
        self._stop_event.set()
        self._engine_paused = True
        if self._current_job and self._current_job.status == "running":
            self._current_job.status = "stopped"
            logger.info(f"Stop requested — aborting job {self._current_job.id}")
        else:
            logger.info("Stop requested — engine paused (no running job)")

    def resume_engine(self):
        """Un-pause the engine so new jobs can be accepted again."""
        self._stop_event.clear()
        self._engine_paused = False
        logger.info("Engine resumed")

    @property
    def is_paused(self) -> bool:
        return self._engine_paused

    def _is_stopped(self) -> bool:
        """Return True if stop was requested (checked in pipeline loops)."""
        return self._stop_event.is_set()

    async def scrape_url(self, url: str, source_id: str = "", source_name: str = "", max_pages: int = 10) -> ScrapeJob:
        """Scrape a URL and process all found wallpapers."""
        job_id = uuid.uuid4().hex[:12]
        job = ScrapeJob(job_id, url, source_id, source_name or urlparse(url).netloc, max_pages)

        # Reject when engine is paused
        if self._engine_paused:
            job.status = "stopped"
            logger.info(f"Engine paused — rejecting job for {url}")
            return job

        async with self._job_lock:
            if self._current_job and self._current_job.status == "running":
                job.status = "queued"
                return job

        # Clear any leftover stop signal from a previous stop_all()
        self._stop_event.clear()

        job.status = "running"
        job.started_at = datetime.now().isoformat()
        self._current_job = job

        try:
            await self._run_pipeline(job)
        except Exception as e:
            logger.error(f"Pipeline error for {url}: {e}")
            job.error_log.append(str(e))
            job.errors += 1
        finally:
            # Keep the "stopped" status if stop_all() set it; otherwise "completed"
            if job.status != "stopped":
                job.status = "completed"
            job.completed_at = datetime.now().isoformat()
            job.progress = 100.0
            self._job_history.append(job.to_dict())
            if len(self._job_history) > 100:
                self._job_history = self._job_history[-100:]
            self._current_job = None

        return job

    async def _run_pipeline(self, job: ScrapeJob):
        """Execute the full scrape pipeline."""
        if not browser_manager.is_available:
            logger.warning("Browser not available, attempting to initialize...")
            success = await browser_manager.initialize()
            if not success:
                job.error_log.append("Browser not available")
                return

        # Refresh downloader with current config each run
        max_dl = config_store.get("scraping", "max_concurrent_downloads", default=3)
        dl_delay = config_store.get("scraping", "download_delay_seconds", default=1.0)
        self.downloader = DownloadManager(max_concurrent=max_dl, min_delay=dl_delay)

        # Create adapter/validator from current config each run
        adapter = self._get_adapter()
        validator = self._get_validator()
        scroll_count = config_store.get("scraping", "scroll_count", default=5)
        scroll_wait = config_store.get("scraping", "scroll_wait_ms", default=800)

        # Load site profile for this domain — remembers site structure
        profile = site_profiles.get(job.url)

        # Track processed image URLs across all pages in this job to avoid
        # downloading the same wallpaper multiple times
        job_seen_urls = set()

        current_url = job.url
        consecutive_blocked = 0
        for page_num in range(1, job.max_pages + 1):
            # --- Stop check ---
            if self._is_stopped():
                logger.info(f"Stop requested — exiting pipeline at page {page_num}")
                break

            logger.info(f"Scraping page {page_num}: {current_url}")
            job.pages_scraped = page_num

            try:
                html = await browser_manager.get_page_content(
                    current_url, scroll_count=scroll_count, scroll_wait_ms=scroll_wait
                )
            except Exception as e:
                logger.error(f"Failed to load page {current_url}: {e}")
                job.error_log.append(f"Page load failed: {e}")
                break

            # Check if the gallery page itself is blocked
            if self._page_is_blocked(html):
                consecutive_blocked += 1
                logger.warning(
                    f"Gallery page {page_num} appears blocked ({consecutive_blocked} in a row): {current_url}"
                )
                if consecutive_blocked >= 2:
                    logger.warning("Site is blocking gallery pages — stopping")
                    break
                # Wait longer before trying next page
                await asyncio.sleep(5 + random.random() * 5)
                # Try to continue to next page anyway
                try:
                    next_url = await adapter.get_next_page_url(html, current_url, page_num)
                    if next_url:
                        current_url = next_url
                        continue
                except Exception:
                    pass
                break
            else:
                consecutive_blocked = 0

            # Discover categories/collections on the page for future variety
            try:
                cats = adapter.discover_categories(html, current_url)
                if cats:
                    profile.add_categories(cats)
                    # Also register category URLs as gallery URLs for future visits
                    for cat in cats:
                        profile.add_gallery_url(cat["url"], cat.get("label", ""))
                    logger.info(f"Discovered {len(cats)} category/collection links on {current_url}")
            except Exception as e:
                logger.debug(f"Category discovery error: {e}")

            # Discover outbound links to other wallpaper sites (passive discovery)
            try:
                outbound = discovery_engine.discover_outbound_links(html, current_url)
                for ob in outbound[:3]:  # Limit to 3 per page to avoid spam
                    from src.scheduler.source_manager import source_manager as sm
                    if not sm.domain_exists(ob["domain"]):
                        sm.add_source(
                            url=ob["url"],
                            name=f"{ob['domain']} (outbound)",
                            category="discovered",
                            discovered_by_query="(outbound-link)",
                            validation_score=0,  # Will be validated on first scrape
                        )
                        logger.info(f"Auto-added outbound wallpaper site: {ob['domain']}")
            except Exception as e:
                logger.debug(f"Outbound link discovery error: {e}")

            # Check for detail page links FIRST — listing pages have thumbnails
            # linking to detail pages where full-size images live.
            detail_links = adapter.get_detail_page_links(html, current_url)
            if detail_links:
                # Filter out already-visited detail pages (save time on revisits)
                fresh_links = [
                    dl for dl in detail_links
                    if not profile.is_visited(dl["url"])
                ]
                skipped = len(detail_links) - len(fresh_links)
                if skipped > 0:
                    logger.info(f"Skipping {skipped} already-visited detail pages")

                if fresh_links:
                    logger.info(f"Found {len(fresh_links)} fresh detail page links on page {page_num} — following for full-res images")
                    # Process images immediately as each detail page is scraped
                    # (instead of collecting all first, then processing)
                    await self._scrape_and_process_detail_pages(
                        fresh_links, adapter, job, validator,
                        scroll_count, scroll_wait,
                        profile, gallery_url=current_url,
                        job_seen_urls=job_seen_urls,
                    )
                else:
                    logger.info(f"All {len(detail_links)} detail links already visited — skipping")
            else:
                # No detail links — this might be a detail page itself
                allow_nsfw = config_store.get("scraping", "allow_nsfw", default=False)
                # NSFW page check for direct-image pages
                if not allow_nsfw and is_nsfw_page(html, current_url):
                    logger.info(f"Skipping NSFW page: {current_url}")
                    images = []
                else:
                    images = await adapter.scrape(html, current_url)
                    # Filter NSFW images
                    if not allow_nsfw:
                        images = [
                            img for img in images
                            if not is_nsfw_image(img.url, alt=img.alt, title=img.title,
                                                 tags=img.tags, page_url=current_url)
                        ]
                    logger.info(f"Found {len(images)} direct images on page {page_num} (no detail links)")

                job.images_found += len(images)

                # Process images (skip already-seen URLs within this job)
                for i, img in enumerate(images):
                    if self._is_stopped():
                        logger.info("Stop requested — halting image processing")
                        break

                    norm_url = self._normalize_image_url(img.url)
                    if norm_url in job_seen_urls:
                        logger.debug(f"Skipping already-processed image in this job: {img.url[:80]}")
                        job.duplicates += 1
                        continue
                    job_seen_urls.add(norm_url)

                    try:
                        result = await self._process_image(img, job, validator)
                        if result.status in ("uploaded", "upgraded"):
                            job.images_uploaded += 1
                        elif result.status == "duplicate":
                            job.duplicates += 1
                        elif result.status == "error":
                            job.errors += 1
                            if result.error:
                                logger.debug(f"Image skip: {result.error} - {img.url[:80]}")
                    except Exception as e:
                        logger.error(f"Image processing error: {e}")
                        job.errors += 1
                        job.error_log.append(f"Image error: {e}")

                    # Update progress
                    total_expected = job.images_found
                    done = job.images_uploaded + job.duplicates + job.errors
                    job.progress = min(95.0, (done / max(total_expected, 1)) * 100)

            # Try to find next page
            try:
                next_url = await adapter.get_next_page_url(html, current_url, page_num)
                if not next_url:
                    logger.info("No more pages found")
                    # Update hints about pagination
                    if page_num == 1:
                        profile.update_hints(has_pagination=False)
                    break
                else:
                    profile.update_hints(has_pagination=True)
                current_url = next_url
            except Exception:
                break

            # Human-like delay between gallery pages (configurable base + jitter)
            page_delay = config_store.get("scraping", "page_delay_seconds", default=2)
            await asyncio.sleep(page_delay + random.random() * 3)

        # Release the persistent browser page for this site
        await browser_manager.release_site_page()

        # Record gallery URL scrape stats and overall stats
        profile.record_gallery_scrape(job.url, job.images_found)
        profile.record_scrape_stats(job.images_found, job.images_uploaded)
        profile.save()

    @staticmethod
    def _page_is_blocked(html: str) -> bool:
        """Check if the returned HTML is a challenge/block page, not real content."""
        if not html or len(html) < 500:
            return True
        text = html[:2000].lower()
        signals = [
            "checking your browser", "just a moment", "verify you are human",
            "attention required", "enable javascript and cookies",
            "ddos protection", "access denied", "cf-browser-verification",
            "_cf_chl", "challenge-platform",
        ]
        # If challenge signals are present AND there are very few images, it's blocked
        has_challenge = any(s in text for s in signals)
        if has_challenge:
            return True
        # Also check for very sparse pages (captcha pages have almost no <img> tags)
        img_count = html.lower().count("<img")
        if img_count == 0 and len(html) < 5000:
            return True
        return False

    async def _scrape_and_process_detail_pages(
        self, detail_links: list[dict], adapter, job: ScrapeJob,
        validator: ImageValidator, scroll_count: int, scroll_wait: int,
        profile=None, gallery_url: str = "", job_seen_urls: set = None,
    ):
        """Visit detail pages and process each wallpaper immediately upon discovery.

        Instead of collecting all images first then processing, this method
        downloads/validates/uploads each wallpaper right after finding it on
        the detail page. This means the user sees results appear in real-time
        rather than waiting for all detail pages to be visited first.

        Uses the gallery URL as referer (like clicking a thumbnail on the listing page).
        Includes adaptive back-off if consecutive pages return 0 images.
        Deduplicates images across detail pages via job_seen_urls.
        """
        if job_seen_urls is None:
            job_seen_urls = set()

        allow_nsfw = config_store.get("scraping", "allow_nsfw", default=False)
        max_details = config_store.get("scraping", "max_pages", default=10)
        # Limit detail pages per listing page to avoid runaway scraping
        links_to_visit = detail_links[:min(len(detail_links), max_details * 3)]

        consecutive_failures = 0
        base_delay = max(config_store.get("scraping", "page_delay_seconds", default=2), 1.0)

        for i, link_info in enumerate(links_to_visit):
            # --- Stop check ---
            if self._is_stopped():
                logger.info("Stop requested — halting detail page crawl")
                break

            detail_url = link_info["url"]
            try:
                logger.info(f"Visiting detail page {i+1}/{len(links_to_visit)}: {detail_url}")
                html = await browser_manager.get_page_content(
                    detail_url, scroll_count=max(scroll_count, 8),
                    scroll_wait_ms=scroll_wait,
                    referer=gallery_url,
                )

                # Detect blocked/challenge pages before parsing
                if self._page_is_blocked(html):
                    consecutive_failures += 1
                    logger.warning(
                        f"Detail page appears blocked ({consecutive_failures} in a row): {detail_url}"
                    )
                    if consecutive_failures >= 3:
                        logger.warning(
                            f"Site is blocking requests — stopping detail page crawl after "
                            f"{consecutive_failures} consecutive blocked pages"
                        )
                        break
                    backoff = base_delay * (2 ** consecutive_failures) + random.random() * 3
                    logger.info(f"Backing off {backoff:.1f}s before next detail page")
                    await asyncio.sleep(backoff)
                    continue

                # NSFW page check — skip entire page if NSFW and not allowed
                if not allow_nsfw and is_nsfw_page(html, detail_url):
                    logger.info(f"Skipping NSFW detail page: {detail_url}")
                    if profile:
                        profile.mark_visited(detail_url)
                    continue

                images = await adapter.scrape(html, detail_url)

                # Mark page as visited in site profile
                if profile:
                    profile.mark_visited(detail_url)

                if images:
                    consecutive_failures = 0  # Reset on success

                    # Deduplicate, filter NSFW, and process each image immediately
                    for img in images:
                        if self._is_stopped():
                            break

                        base_url = self._normalize_image_url(img.url)
                        if base_url in job_seen_urls:
                            logger.debug(f"Skipping duplicate image: {img.url[:80]}")
                            job.duplicates += 1
                            continue
                        # NSFW image check
                        if not allow_nsfw and is_nsfw_image(
                            img.url, alt=img.alt, title=img.title,
                            tags=img.tags, page_url=detail_url
                        ):
                            logger.info(f"Skipping NSFW image: {img.url[:80]}")
                            continue
                        job_seen_urls.add(base_url)

                        # Carry forward metadata from the thumbnail link
                        if not img.alt and link_info.get("alt"):
                            img.alt = link_info["alt"]
                        if not img.title and link_info.get("title"):
                            img.title = link_info["title"]

                        # Process immediately — download, validate, caption, upload
                        job.images_found += 1
                        try:
                            result = await self._process_image(img, job, validator)
                            if result.status in ("uploaded", "upgraded"):
                                job.images_uploaded += 1
                                logger.info(f"Wallpaper {'upgraded' if result.status == 'upgraded' else 'uploaded'} from {detail_url}")
                            elif result.status == "duplicate":
                                job.duplicates += 1
                            elif result.status == "error":
                                job.errors += 1
                                if result.error:
                                    logger.debug(f"Image skip: {result.error} - {img.url[:80]}")
                        except Exception as e:
                            logger.error(f"Image processing error: {e}")
                            job.errors += 1
                            job.error_log.append(f"Image error: {e}")

                        # Update progress
                        total_expected = max(job.images_found, len(links_to_visit))
                        done = job.images_uploaded + job.duplicates + job.errors
                        job.progress = min(95.0, (done / max(total_expected, 1)) * 100)

                    # Record hints about what worked on this site
                    if profile:
                        profile.update_hints(has_download_buttons=True)
                else:
                    consecutive_failures += 1
                    logger.debug(f"No images found on detail page {detail_url} ({consecutive_failures} in a row)")
                    if consecutive_failures >= 5:
                        logger.warning(
                            f"Stopping detail crawl — {consecutive_failures} consecutive pages "
                            f"with no images (adapter may not match this site's layout)"
                        )
                        break
            except Exception as e:
                consecutive_failures += 1
                logger.warning(f"Failed to scrape detail page {detail_url}: {e}")
                job.error_log.append(f"Detail page error: {e}")

                # If the browser/page itself died, give it a moment to recover
                # (the browser manager will reinitialize on next call)
                err_lower = str(e).lower()
                is_browser_crash = any(s in err_lower for s in [
                    "target page", "browser has been closed",
                    "page has been closed", "context or browser",
                    "session closed", "target closed",
                ])
                if is_browser_crash:
                    logger.warning("Browser/page crash detected — pausing before retry")
                    await asyncio.sleep(3)

                if consecutive_failures >= 5:
                    logger.warning(
                        f"Stopping detail crawl — {consecutive_failures} consecutive errors"
                    )
                    break

            # Adaptive delay: longer when failures are accumulating
            delay = base_delay + random.random() * 2
            if consecutive_failures > 0:
                delay += consecutive_failures * 1.5  # Extra 1.5s per consecutive failure
            await asyncio.sleep(delay)

        # Batch save visited pages
        if profile:
            profile.save()

    async def _process_image(self, img, job: ScrapeJob, validator: ImageValidator = None) -> ScrapeResult:
        """Process a single image through the full pipeline."""
        result = ScrapeResult()
        temp_files = []
        if validator is None:
            validator = self._get_validator()

        try:
            # Cross-job URL dedup: skip if this URL was already processed in
            # a previous job during this engine lifecycle (saves bandwidth)
            norm = self._normalize_image_url(img.url)
            if norm in self._processed_urls:
                result.status = "duplicate"
                logger.debug(f"Cross-job URL duplicate: {img.url[:80]}")
                return result

            # Try upgraded URLs first (e.g., /wallpaper/nbig/ -> /wallpaper/original/)
            # This ensures we get full-resolution images instead of previews/thumbnails
            dl_path = None
            used_url = img.url
            upgraded_urls = GenericAdapter.get_upgraded_urls(img.url)
            for upgraded_url in upgraded_urls:
                logger.info(f"Trying full-res URL: {upgraded_url[:100]}")
                dl_path = await self.downloader.download(upgraded_url, referer=img.page_url)
                if dl_path is not None:
                    used_url = upgraded_url
                    logger.info(f"Full-res URL succeeded: {upgraded_url[:100]}")
                    break

            # Fall back to original URL if no upgrade worked
            if dl_path is None:
                logger.info(f"Downloading: {img.url[:100]}")
                dl_path = await self.downloader.download(img.url, referer=img.page_url)

            if dl_path is None:
                result.error = "Download failed"
                logger.warning(f"Download failed: {img.url[:100]}")
                return result
            temp_files.append(dl_path)
            job.images_downloaded += 1

            # Validate
            is_valid, reason = validator.validate(dl_path)
            if not is_valid:
                # Try AI enhancement for near-miss images (too small or wrong aspect ratio)
                enhanced_path = None
                if "Too small" in reason or "Aspect ratio" in reason:
                    enhance_enabled = config_store.get("scraping", "enhance_near_miss", default=True)
                    if enhance_enabled:
                        enhancer = self._get_enhancer()
                        enhanced = enhancer.enhance(dl_path)
                        if enhanced:
                            enhanced_path, _, _ = enhanced
                            temp_files.append(enhanced_path)
                            # Re-validate the enhanced image
                            is_valid, reason = validator.validate(enhanced_path)
                            if is_valid:
                                logger.info(f"Enhancement rescued image: {img.url[:80]}")
                                dl_path = enhanced_path
                if not is_valid:
                    result.error = f"Validation failed: {reason}"
                    logger.info(f"Validation failed ({reason}): {img.url[:80]}")
                    return result

            # Compress — use quality from current config
            self.compressor.quality = config_store.get("jpeg", "quality", default=85)
            compress_result = self.compressor.compress(dl_path, job.source_name)
            if compress_result is None:
                result.error = "Compression failed"
                return result
            compressed_path, img_hash, width, height, file_size_kb = compress_result
            temp_files.append(compressed_path)
            result.img_hash = img_hash

            # Generate thumbnail
            thumb_path = self.compressor.generate_thumbnail(compressed_path, img_hash)

            # Fast cross-job hash dedup: skip Baserow API call if we already
            # know this hash from a previous job AND the cached version has
            # equal or higher resolution (so upgrades still pass through).
            new_pixels = width * height
            cached_pixels = self._processed_hashes.get(img_hash, 0)
            if cached_pixels >= new_pixels and cached_pixels > 0:
                result.status = "duplicate"
                logger.debug(f"Cross-job hash duplicate: {img_hash}")
                self._log_activity(job, img, img_hash, width, height, file_size_kb,
                                   thumb_path or "", None, "duplicate")
                return result

            # Resolution-aware dedup check against Baserow
            if self.baserow.is_configured:
                existing = await self.baserow.find_duplicate(img_hash)
                if existing:
                    # Compare resolutions — keep the higher-res version
                    w_field = self.baserow.field_mapping.get("Width", "Width")
                    h_field = self.baserow.field_mapping.get("Height", "Height")
                    existing_w = int(existing.get(w_field, 0) or 0)
                    existing_h = int(existing.get(h_field, 0) or 0)
                    existing_pixels = existing_w * existing_h

                    if new_pixels > existing_pixels and existing_pixels > 0:
                        # New image is higher resolution — upgrade the existing entry
                        logger.info(
                            f"Upgrading wallpaper: {existing_w}x{existing_h} → {width}x{height}"
                        )
                        row_id = existing.get("id")

                        file_obj = await self.baserow.upload_file(compressed_path)

                        title, alt_text, tags = await self.captioner.caption(
                            compressed_path,
                            scraped_title=img.title or "",
                            scraped_alt=img.alt or "",
                            scraped_tags=img.tags or "",
                            source_url=img.url or "",
                        )
                        if img.title and not title:
                            title = self._clean_scraped_text(img.title)
                        if img.alt and not alt_text:
                            alt_text = self._clean_scraped_text(img.alt)
                        if img.tags and not tags:
                            tags = self._clean_scraped_tags(img.tags)

                        aspect = calculate_aspect_ratio(width, height)
                        mobile = is_mobile(width, height)

                        fm = self.baserow.field_mapping
                        update_data = {
                            fm.get("Width", "Width"): width,
                            fm.get("Height", "Height"): height,
                            fm.get("imgUrl", "imgUrl"): used_url,
                            fm.get("aspect_ratio", "aspect_ratio"): aspect,
                            fm.get("isMobile", "isMobile"): mobile,
                        }
                        if file_obj:
                            update_data[fm.get("imageFile", "imageFile")] = [
                                {"name": file_obj.get("name", ""),
                                 "visible_name": file_obj.get("visible_name", "")}
                            ]
                        # Only update title/alt if they pass quality checks
                        if title and self._is_human_readable(title):
                            update_data[fm.get("wallpaperTitle", "wallpaperTitle")] = title
                        else:
                            logger.info(f"Upgrade: keeping existing title (new one not readable: {title!r})")
                        if alt_text and alt_text.strip():
                            update_data[fm.get("altText", "altText")] = alt_text
                        else:
                            logger.info("Upgrade: keeping existing alt text (new one empty)")
                        if tags:
                            update_data[fm.get("categoryTags", "categoryTags")] = tags

                        updated = await self.baserow.update_row(row_id, update_data)
                        if updated:
                            result.success = True
                            result.status = "upgraded"
                            result.baserow_row_id = row_id
                            logger.info(f"Upgraded wallpaper to {width}x{height} (row {row_id})")
                        else:
                            result.status = "duplicate"

                        self._processed_hashes[img_hash] = max(new_pixels, cached_pixels)
                        self._processed_urls.add(norm)
                        self._log_activity(
                            job, img, img_hash, width, height, file_size_kb,
                            thumb_path or "", row_id, result.status,
                            title=title, alt_text=alt_text, tags=tags,
                            aspect=aspect, mobile=mobile,
                        )
                        return result
                    else:
                        # Same or lower resolution — skip as duplicate
                        self._processed_hashes[img_hash] = max(new_pixels, cached_pixels)
                        self._processed_urls.add(norm)
                        result.status = "duplicate"
                        self._log_activity(job, img, img_hash, width, height, file_size_kb,
                                           thumb_path or "", None, "duplicate")
                        return result

            # Local-only hash dedup when Baserow isn't configured.
            # The activity store persists hashes to disk, so this survives
            # restarts — preventing re-uploads of wallpapers already in the
            # gallery even without Baserow.
            if not self.baserow.is_configured:
                from src.storage.activity_store import activity_store
                stored_pixels = activity_store.get_hash_pixels(img_hash)
                if stored_pixels >= new_pixels and stored_pixels > 0:
                    self._processed_hashes[img_hash] = max(new_pixels, stored_pixels)
                    self._processed_urls.add(norm)
                    result.status = "duplicate"
                    logger.debug(f"Local hash duplicate: {img_hash}")
                    self._log_activity(job, img, img_hash, width, height, file_size_kb,
                                       thumb_path or "", None, "duplicate")
                    return result

            # AI captioning — pass scraped metadata so the captioner can
            # identify characters, media franchises, and art styles
            title, alt_text, tags = await self.captioner.caption(
                compressed_path,
                scraped_title=img.title or "",
                scraped_alt=img.alt or "",
                scraped_tags=img.tags or "",
                source_url=img.url or "",
            )

            # Safety-net fallback: if the captioner returned nothing, use
            # cleaned scraped metadata directly
            if img.title and not title:
                title = self._clean_scraped_text(img.title)
            if img.alt and not alt_text:
                alt_text = self._clean_scraped_text(img.alt)
            if img.tags and not tags:
                tags = self._clean_scraped_tags(img.tags)

            # Build metadata
            aspect = calculate_aspect_ratio(width, height)
            mobile = is_mobile(width, height)

            metadata = WallpaperMetadata(
                wallpaperTitle=title,
                Width=width,
                Height=height,
                imgUrl=used_url,
                altText=alt_text,
                artistText=img.artist,
                artistLink=img.artist_link,
                isMobile=mobile,
                isReported=False,
                isVip=False,
                categoryTags=tags,
                imgHash=img_hash,
                aspect_ratio=aspect,
            )

            # Upload to Baserow
            row_id = None
            if self.baserow.is_configured:
                # Upload file first
                file_obj = await self.baserow.upload_file(compressed_path)
                if file_obj:
                    metadata.imageFile = [{"name": file_obj.get("name", ""), "visible_name": file_obj.get("visible_name", "")}]

                # Validate metadata before sending to Baserow
                scraped_ctx = {
                    "clean_title": self._clean_scraped_text(img.title or ""),
                    "clean_alt": self._clean_scraped_text(img.alt or ""),
                }
                title, alt_text, upload_ok, fail_reason = self._validate_for_upload(
                    title, alt_text, file_obj, context=scraped_ctx,
                )
                if not upload_ok:
                    logger.warning(
                        f"Skipping Baserow upload: {fail_reason} — {img.url[:80]}"
                    )
                    result.status = "error"
                    result.error = fail_reason
                    self._processed_hashes[img_hash] = new_pixels
                    self._processed_urls.add(norm)
                    self._log_activity(
                        job, img, img_hash, width, height, file_size_kb,
                        thumb_path or "", None, "error", error=fail_reason,
                    )
                    return result

                # Update metadata with validated title/alt
                metadata.wallpaperTitle = title
                metadata.altText = alt_text

                # Create row with mapped fields
                row_data = metadata.to_baserow_dict(self.baserow.field_mapping)
                row = await self.baserow.create_row(row_data)
                if row:
                    row_id = row.get("id")
                    result.baserow_row_id = row_id
                    logger.info(f"Uploaded to Baserow: row {row_id}")

            result.success = True
            result.status = "uploaded"
            self._processed_hashes[img_hash] = new_pixels
            self._processed_urls.add(norm)
            logger.info(f"Processed {width}x{height} wallpaper: {title or img.url[:60]}")

            # Log activity
            self._log_activity(job, img, img_hash, width, height, file_size_kb,
                               thumb_path or "", row_id, "uploaded",
                               title=title, alt_text=alt_text, tags=tags, aspect=aspect, mobile=mobile)

            return result

        except Exception as e:
            result.error = str(e)
            result.status = "error"
            self._log_activity(job, img, result.img_hash, 0, 0, 0, "", None, "error", error=str(e))
            return result

        finally:
            # Clean up temp files (NOT thumbnails)
            for tf in temp_files:
                try:
                    if tf.exists():
                        tf.unlink()
                except Exception:
                    pass

    # Quality/size keywords stripped from filenames during URL dedup
    _URL_QUALITY_RE = re.compile(
        r"[_.\-]?"
        r"(original|preview|thumb(?:nail)?|full|big|nbig|large|medium|small|"
        r"hd|[248]k|uhd|[12]k|1080p|hires|hi[_-]?res|lo[_-]?res|lowres|"
        r"compressed|optimized|raw|source|mini|micro|tiny|xl|xxl|"
        r"high[_-]?quality|low[_-]?quality|web|mobile|desktop|retina)"
        r"[_.\-]?",
        re.I,
    )

    @staticmethod
    def _normalize_image_url(url: str) -> str:
        """Normalize an image URL for deduplication.

        Extracts the base filename, stripping dimensions AND quality keywords
        so that different-quality versions of the same wallpaper map to the
        same key.
        e.g., /wallpaper/nbig/foo.webp  and /wallpaper/big/foo.webp   → foo.webp
              /download/foo-1920x1080.jpg and /download/foo-2560x1440.jpg → foo.jpg
              /img/preview-sunset.jpg    and /img/original-sunset.jpg  → sunset.jpg
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        # Extract just the filename (last path segment)
        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        # Strip dimension patterns like _1920x1080, -800x600, 1920x1080- (at start)
        filename = re.sub(r"[_.\-]?\d{3,5}x\d{3,5}[_.\-]?", "", filename)
        # Strip quality/size keywords (original, preview, full, hd, 4k, etc.)
        filename = ScraperEngine._URL_QUALITY_RE.sub("", filename)
        # Clean up double separators
        filename = re.sub(r"[_.\-]{2,}", "-", filename)
        filename = re.sub(r"^[_.\-]+|[_.\-]+(?=\.)", "", filename)
        # Avoid returning empty key (e.g., if filename was just "1920x1080.jpg")
        if not filename or filename == "." or filename.startswith("."):
            filename = path.rsplit("/", 1)[-1] if "/" in path else path
        # Strip query params for dedup purposes
        return filename.lower() if filename else url.lower()

    # Regex for stripping site names and junk from scraped HTML metadata
    _SITE_NAME_RE = re.compile(
        r"\b(wallpaper[s]?|background[s]?|desktop|hd|4k|uhd|1080p|2k|"
        r"free download|download|stock photo|royalty.?free|"
        r"wallhaven|unsplash|pexels|pixabay|wallpaperscraft|wallpaperflare|"
        r"wallpaperaccess|wallpaperbat|wallpapercave|wallpaperbetter|"
        r"hdwallpapers|getwallpapers|peakpx|setaswall|pixel4k|"
        r"4kwallpapers|uhdpaper|goodfon|fonwall|rawpixel|freepik)\b",
        re.I,
    )
    _JUNK_RE = re.compile(
        r"^\d+x\d+$|^[\w-]{20,}$|^\d+$|^IMG_|^DSC_|^DSCN|"
        r"^photo-\d|\.jpe?g$|\.png$|\.webp$",
        re.I,
    )
    _DIMENSION_RE = re.compile(r"\b\d{3,5}\s*[x×]\s*\d{3,5}\b")
    _PREFIX_RE = re.compile(
        r"^(a\s+)?(photo|image|picture|wallpaper|background)\s+(of|from)\s+",
        re.I,
    )

    # Robotic AI-sounding adjectives to strip from fallback titles.
    # Atmospheric adjectives (vibrant, colorful, scenic, serene, etc.) are
    # kept — they describe what you actually see and add genuine mood.
    _FLUFF_RE = re.compile(
        r"\b(beautiful|stunning|amazing|awesome|incredible|gorgeous|wonderful|"
        r"perfect|majestic|breathtaking|spectacular|magnificent|lovely|"
        r"picturesque|nice|great|cool|best|ultra|highly|extremely|very)\b",
        re.I,
    )

    def _clean_scraped_text(self, text: str) -> str:
        """Clean scraped title/alt text — remove site names, dimensions, junk.

        Returns a descriptive (max 12 word), human-sounding title.
        """
        if not text:
            return ""
        # Skip pure junk
        if self._JUNK_RE.search(text.strip()):
            return ""
        # Remove dimensions like "1920x1080"
        text = self._DIMENSION_RE.sub("", text)
        # Remove site names
        text = self._SITE_NAME_RE.sub("", text)
        # Remove generic prefixes
        text = self._PREFIX_RE.sub("", text)
        # Remove robotic adjectives (keep atmospheric ones)
        text = self._FLUFF_RE.sub("", text)
        # Remove separators commonly used in page titles: " | SiteName", " - SiteName"
        text = re.sub(r"\s*[|–—-]\s*$", "", text)
        text = re.sub(r"\s*[|–—-]\s*\S+\.(com|net|org|io|cc)\b.*$", "", text, flags=re.I)
        # Clean whitespace
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^[,.\-–—:;|]+\s*", "", text).strip()
        text = re.sub(r"[,.\-–—:;|]+\s*$", "", text).strip()
        if not text or len(text) < 3:
            return ""
        # Title case, max 12 words
        words = text.split()[:12]
        # Strip trailing minor words from truncation
        minor = {"in", "on", "at", "of", "with", "and", "or", "for", "to", "by"}
        while len(words) > 1 and words[-1].lower() in minor:
            words.pop()
        titled = []
        for i, w in enumerate(words):
            if i == 0 or w.lower() not in minor:
                titled.append(w[0].upper() + w[1:] if w else "")
            else:
                titled.append(w.lower())
        return " ".join(titled)

    def _clean_scraped_tags(self, tags: str) -> str:
        """Clean scraped tags — remove site names and junk tags."""
        if not tags:
            return ""
        cleaned = []
        for tag in tags.split(","):
            tag = tag.strip().lower()
            tag = self._SITE_NAME_RE.sub("", tag).strip()
            if tag and len(tag) > 1 and not self._JUNK_RE.search(tag):
                cleaned.append(tag)
        return ", ".join(cleaned[:20])

    # ------------------------------------------------------------------
    # Pre-upload validation
    # ------------------------------------------------------------------

    # Matches strings that look like hashes, slugs, or random IDs rather
    # than human-readable text.  Examples: "Xlp7po", "a3f9c2b", "IMG_2031",
    # "photo-1699553429655-1e4a0a43acb7", "5cKvVpq".
    _GIBBERISH_RE = re.compile(
        r"^[A-Za-z0-9_\-]{1,4}$"           # Too short to be meaningful
        r"|^[A-Fa-f0-9]{6,}$"              # Hex hash
        r"|^[A-Za-z0-9]{5,}$"              # AlphaNum slug without spaces/vowel pattern
        r"|^[A-Za-z0-9_\-]+$"              # Single slug token (no spaces at all)
    )

    @staticmethod
    def _is_human_readable(text: str) -> bool:
        """Return True if *text* looks like a human-written title.

        Rejects hashes, URL slugs, random IDs, and single meaningless
        tokens.  Accepts normal multi-word titles and even short but
        real single words (e.g. "Sunset", "Sakura").
        """
        if not text or not text.strip():
            return False
        text = text.strip()
        # Must be at least 3 characters
        if len(text) < 3:
            return False
        # Multi-word text is almost always human readable
        if " " in text and len(text.split()) >= 2:
            return True
        # Single token — check for gibberish patterns
        # Pure digits
        if text.isdigit():
            return False
        # Hex hash (6+ hex chars)
        if re.fullmatch(r"[A-Fa-f0-9]{6,}", text):
            return False
        # Mixed-case single token with digits usually = hash/slug
        # e.g. "Xlp7po", "5cKvVpq", "a3f9c2b"
        if re.fullmatch(r"[A-Za-z0-9_\-]{3,}", text):
            has_digit = any(c.isdigit() for c in text)
            has_upper = any(c.isupper() for c in text)
            has_lower = any(c.islower() for c in text)
            # Mixed case + digits = almost certainly a hash/ID
            if has_digit and (has_upper or has_lower):
                return False
            # CamelCase without spaces that isn't a known word pattern
            if has_upper and has_lower and len(text) > 12:
                return False
        # Filename patterns
        if re.match(r"^(IMG|DSC|DSCN|photo|image|pic)[_\-]?\d", text, re.I):
            return False
        # URL/file extension leftovers
        if re.search(r"\.(jpe?g|png|webp|gif|bmp|svg)$", text, re.I):
            return False
        return True

    def _validate_for_upload(self, title: str, alt_text: str, file_obj,
                             ai: dict = None, context: dict = None) -> tuple:
        """Validate metadata quality before sending to Baserow.

        Returns (title, alt_text, ok, reason).
        - Ensures title is human-readable (not a hash/slug/gibberish)
        - Ensures alt_text is present
        - Ensures file_obj (image) was successfully uploaded

        When title fails validation, tries to derive one from alt_text
        or AI data.  When alt_text is missing, tries to derive from title
        or AI data.
        """
        # --- Fix title if gibberish or missing ---
        if not self._is_human_readable(title):
            # Try deriving from alt_text
            if alt_text and self._is_human_readable(alt_text):
                words = alt_text.split()[:12]
                title = " ".join(words)
                logger.info(f"Title was gibberish, derived from alt text: {title}")
            # Try AI subject/short caption
            elif ai:
                for key in ("subject", "short", "scene", "description"):
                    candidate = ai.get(key, "")
                    if candidate and self._is_human_readable(candidate):
                        title = self._clean_scraped_text(candidate)
                        if self._is_human_readable(title):
                            logger.info(f"Title was gibberish, derived from AI {key}: {title}")
                            break
            # Try context clean_title
            if not self._is_human_readable(title) and context:
                for key in ("clean_title", "clean_alt"):
                    candidate = context.get(key, "")
                    if candidate and self._is_human_readable(candidate):
                        title = self._clean_scraped_text(candidate)
                        if self._is_human_readable(title):
                            logger.info(f"Title was gibberish, derived from context {key}: {title}")
                            break

        # --- Fix alt_text if missing ---
        if not alt_text or not alt_text.strip():
            if title and self._is_human_readable(title):
                alt_text = title
                logger.info(f"Alt text was empty, copied from title: {alt_text}")
            elif ai:
                for key in ("description", "scene", "short"):
                    candidate = ai.get(key, "")
                    if candidate and len(candidate.strip()) > 5:
                        alt_text = candidate.strip()
                        logger.info(f"Alt text was empty, derived from AI {key}")
                        break

        # --- Final checks ---
        if not self._is_human_readable(title):
            return title, alt_text, False, "Title is not human-readable"
        if not alt_text or not alt_text.strip():
            return title, alt_text, False, "Alt text is missing"
        if file_obj is None:
            return title, alt_text, False, "Image file upload failed"

        return title, alt_text, True, ""

    def _log_activity(self, job, img, img_hash, width, height, file_size_kb,
                      thumb_path, row_id, status, title="", alt_text="", tags="",
                      aspect="", mobile=False, error=""):
        """Log an activity entry."""
        try:
            entry = ActivityEntry(
                id=uuid.uuid4().hex[:12],
                timestamp=datetime.now().isoformat(),
                source_id=job.source_id,
                source_name=job.source_name,
                job_id=job.id,
                thumbnail_path=thumb_path,
                title=title,
                alt_text=alt_text,
                tags=tags,
                width=width,
                height=height,
                aspect_ratio=aspect,
                is_mobile=mobile,
                img_url=img.url if img else "",
                img_hash=img_hash,
                baserow_row_id=row_id,
                file_size_kb=file_size_kb,
                status=status,
                error_message=error,
            )
            activity_store.add_entry(entry)
        except Exception as e:
            logger.warning(f"Failed to log activity: {e}")

    @property
    def current_job(self) -> Optional[dict]:
        if self._current_job:
            return self._current_job.to_dict()
        return None

    @property
    def job_history(self) -> list[dict]:
        return list(self._job_history)

    @property
    def is_busy(self) -> bool:
        return self._current_job is not None and self._current_job.status == "running"

    def clear_stale_job(self):
        """Reset _current_job if it's been stuck in 'running' too long.

        If a background task crashes or the pipeline hangs, _current_job
        can stay in 'running' forever, blocking all future scrapes.
        This detects that case and forces a cleanup.
        """
        job = self._current_job
        if job is None or job.status != "running":
            return
        if not job.started_at:
            return
        try:
            started = datetime.fromisoformat(job.started_at)
            elapsed = (datetime.now() - started).total_seconds()
            # 30 minutes is generous — most scrapes finish in under 10 min
            if elapsed > 1800:
                logger.warning(
                    f"Clearing stale job {job.id} — stuck in 'running' for "
                    f"{elapsed/60:.0f} minutes"
                )
                job.status = "failed"
                job.completed_at = datetime.now().isoformat()
                job.error_log.append("Job timed out (stale job recovery)")
                self._job_history.append(job.to_dict())
                self._current_job = None
        except Exception:
            pass

    def force_clear_job(self):
        """Unconditionally clear _current_job after a crash.

        Called from the API when a background task catches an exception
        to ensure the engine isn't permanently stuck.
        """
        job = self._current_job
        if job is not None:
            logger.warning(f"Force-clearing job {job.id} (status was '{job.status}')")
            if job.status == "running":
                job.status = "failed"
            job.completed_at = datetime.now().isoformat()
            job.error_log.append("Job cleared after background task failure")
            self._job_history.append(job.to_dict())
            self._current_job = None


# Singleton
scraper_engine = ScraperEngine()
