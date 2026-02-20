"""REST API routes for the wallpaper scraper."""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
import httpx
from src.api.models import (
    ScrapeRequest, SourceCreate, SourceUpdate, SourceReorder,
    QueryCreate, QueryUpdate, BaserowConfig,
    FieldMappingUpdate, SettingsUpdate,
    CharacterCreate, CharacterUpdate,
)
from src.api.jobs import job_queue
from src.scraper.engine import scraper_engine
from src.scraper.discovery import discovery_engine
from src.scheduler.scheduler import scheduler
from src.scheduler.source_manager import source_manager
from src.storage.config_store import config_store
from src.storage.activity_store import activity_store
from src.storage.baserow import BaserowClient
from src.metadata.schemas import DEFAULT_FIELD_MAPPING
from src.utils.paths import data_path
from src.utils.logging import setup_logging

logger = setup_logging("api")
router = APIRouter(prefix="/api")

STATS_PATH = data_path("config", "stats.json")


# === Health ===

@router.get("/health")
async def health():
    from src.scraper.browser import browser_manager
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "browser_available": browser_manager.is_available,
        "ai_available": scraper_engine.captioner.is_available,
        "baserow_configured": scraper_engine.baserow.is_configured,
    }


# === Manual Scraping ===

@router.post("/scrape")
async def start_scrape(req: ScrapeRequest, background_tasks: BackgroundTasks, force: bool = False):
    if scraper_engine.is_paused:
        raise HTTPException(400, "Engine is paused. Resume before starting a new scrape.")
    scraper_engine.clear_stale_job()
    if scraper_engine.is_busy:
        if not force:
            raise HTTPException(409, "A scrape job is already running. Use force to override.")
        # Force mode: stop current job, wait for it to clear, then start new one
        scraper_engine.stop_all()
        for _ in range(25):
            await asyncio.sleep(0.2)
            if not scraper_engine.is_busy:
                break
        scraper_engine.force_clear_job()
        scraper_engine.resume_engine()
        logger.info(f"Force-overriding scrape: stopped current job, starting {req.url}")

    async def run_job():
        try:
            logger.info(f"Background task starting scrape: {req.url}")
            job = await scraper_engine.scrape_url(
                url=req.url,
                source_id=req.source_id,
                source_name=req.source_name,
                max_pages=req.max_pages,
            )
            logger.info(f"Scrape finished: {job.status} — {job.images_uploaded} uploaded")
            job_queue.add_job(job.to_dict() if hasattr(job, 'to_dict') else job)
            if req.source_id:
                source_manager.record_scrape(
                    req.source_id,
                    uploaded=job.images_uploaded,
                    dupes=job.duplicates,
                    errors=job.errors,
                )
        except Exception as e:
            logger.error(f"Background scrape task failed: {e}", exc_info=True)
            # Ensure current_job is cleared so future scrapes aren't blocked
            scraper_engine.force_clear_job()

    background_tasks.add_task(run_job)
    return {"status": "started", "message": "Scrape job queued"}


@router.get("/jobs")
async def list_jobs():
    current = scraper_engine.current_job
    history = scraper_engine.job_history
    return {
        "current": current,
        "history": history,
        "saved": job_queue.get_all(),
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    current = scraper_engine.current_job
    if current and current.get("id") == job_id:
        return current
    for j in scraper_engine.job_history:
        if j.get("id") == job_id:
            return j
    saved = job_queue.get_job(job_id)
    if saved:
        return saved
    raise HTTPException(404, "Job not found")


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    if job_queue.delete_job(job_id):
        return {"status": "deleted"}
    raise HTTPException(404, "Job not found")


# === Gallery + Activity ===

@router.get("/gallery")
async def gallery(limit: int = 50, offset: int = 0, source_id: str = "",
                  status: str = "", search: str = ""):
    if search:
        entries = activity_store.search(search, limit=limit)
    elif source_id:
        entries = activity_store.get_by_source(source_id, limit=limit)
    elif status:
        entries = activity_store.get_by_status(status, limit=limit)
    else:
        # Default view: exclude duplicates so the gallery only shows
        # wallpapers that were actually uploaded or upgraded.  Users can
        # still see duplicates by selecting "Duplicate" in the filter.
        entries = activity_store.get_recent(
            limit=limit, offset=offset, exclude_status="duplicate",
        )
    return {
        "entries": entries,
        "total": activity_store.count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/gallery/summary")
async def gallery_summary():
    return activity_store.get_summary()


@router.get("/gallery/{entry_id}")
async def gallery_entry(entry_id: str):
    for e in activity_store.get_recent(limit=5000):
        if e.get("id") == entry_id:
            return e
    raise HTTPException(404, "Entry not found")


# === Sources ===

@router.get("/sources")
async def list_sources():
    return {"sources": source_manager.get_all_sources()}


@router.post("/sources")
async def create_source(data: SourceCreate):
    source = source_manager.add_source(
        url=data.url,
        name=data.name,
        schedule_hours=data.schedule_hours,
    )
    return source


@router.put("/sources/reorder")
async def reorder_sources(data: SourceReorder):
    source_manager.reorder_sources(data.source_ids)
    return {"status": "reordered"}


@router.put("/sources/{source_id}")
async def update_source(source_id: str, data: SourceUpdate):
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    source = source_manager.update_source(source_id, **updates)
    if source:
        return source
    raise HTTPException(404, "Source not found")


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str):
    if source_manager.remove_source(source_id):
        return {"status": "deleted"}
    raise HTTPException(404, "Source not found")


@router.post("/sources/{source_id}/scrape")
async def scrape_source(source_id: str, background_tasks: BackgroundTasks, force: bool = False):
    source = source_manager.get_source(source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    if scraper_engine.is_paused:
        raise HTTPException(400, "Engine is paused. Resume before starting a new scrape.")
    scraper_engine.clear_stale_job()
    if scraper_engine.is_busy:
        if not force:
            raise HTTPException(409, "A scrape job is already running. Use force to override.")
        # Force mode: stop current job, wait for it to clear, then start new one
        prev_name = ""
        if scraper_engine._current_job:
            prev_name = scraper_engine._current_job.source_name or "Unknown"
        scraper_engine.stop_all()
        # Wait up to 5 seconds for the running pipeline to exit
        for _ in range(25):
            await asyncio.sleep(0.2)
            if not scraper_engine.is_busy:
                break
        scraper_engine.force_clear_job()
        scraper_engine.resume_engine()
        logger.info(f"Force-overriding scrape: stopped '{prev_name}', starting '{source.get('name', '')}'")

    async def run_job():
        try:
            logger.info(f"Background task starting source scrape: {source['name']} ({source['url']})")
            job = await scraper_engine.scrape_url(
                url=source["url"],
                source_id=source["id"],
                source_name=source.get("name", ""),
                max_pages=source.get("max_pages", 10),
            )
            logger.info(
                f"Source scrape finished: {source['name']} — "
                f"{job.status}, {job.images_uploaded} uploaded"
            )
            job_queue.add_job(job.to_dict() if hasattr(job, 'to_dict') else job)
            source_manager.record_scrape(
                source_id,
                uploaded=job.images_uploaded,
                dupes=job.duplicates,
                errors=job.errors,
            )
        except Exception as e:
            logger.error(f"Background source scrape task failed: {e}", exc_info=True)
            scraper_engine.force_clear_job()

    background_tasks.add_task(run_job)
    return {"status": "started", "source": source["name"]}


@router.post("/sources/{source_id}/toggle")
async def toggle_source(source_id: str):
    source = source_manager.toggle_source(source_id)
    if source:
        return source
    raise HTTPException(404, "Source not found")


# === Discovery Queries ===

@router.get("/discovery/queries")
async def list_queries():
    return {"queries": source_manager.get_all_queries()}


@router.post("/discovery/queries")
async def create_query(data: QueryCreate):
    query = source_manager.add_query(data.query)
    return query


@router.put("/discovery/queries/{query_id}")
async def update_query(query_id: str, data: QueryUpdate):
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    query = source_manager.update_query(query_id, **updates)
    if query:
        return query
    raise HTTPException(404, "Query not found")


@router.delete("/discovery/queries/{query_id}")
async def delete_query(query_id: str):
    if source_manager.remove_query(query_id):
        return {"status": "deleted"}
    raise HTTPException(400, "Cannot delete query (builtin or not found)")


# === Scheduler + Discovery ===

@router.get("/scheduler/status")
async def scheduler_status():
    from src.scraper.browser import browser_manager
    status = scheduler.status
    status["browser_available"] = browser_manager.is_available
    return status


@router.post("/scheduler/pause")
async def scheduler_pause():
    scheduler.pause()
    return {"status": "paused"}


@router.post("/scheduler/resume")
async def scheduler_resume():
    scheduler.resume()
    return {"status": "resumed"}


# === Engine Stop / Resume ===

@router.post("/engine/stop")
async def engine_stop():
    """Stop the current scrape job and pause the engine until resumed."""
    scraper_engine.stop_all()
    scheduler.pause()
    return {"status": "stopped", "message": "Current job stopped and engine paused"}


@router.post("/engine/resume")
async def engine_resume():
    """Resume the engine so new jobs can run again."""
    scraper_engine.resume_engine()
    scheduler.resume()
    return {"status": "resumed", "message": "Engine and scheduler resumed"}


@router.get("/discovery/status")
async def discovery_status():
    return {
        "running": discovery_engine.is_running,
        "last_results": discovery_engine.last_results,
        "state": source_manager.discovery_state,
    }


@router.post("/discovery/run")
async def run_discovery(background_tasks: BackgroundTasks):
    if discovery_engine.is_running:
        raise HTTPException(400, "Discovery already running")

    background_tasks.add_task(discovery_engine.run_discovery)
    return {"status": "started"}


@router.post("/discovery/characters")
async def run_character_discovery(background_tasks: BackgroundTasks):
    """Trigger character discovery — searches the web for popular characters."""
    if discovery_engine.is_running:
        raise HTTPException(400, "Discovery already running")

    background_tasks.add_task(discovery_engine.discover_characters)
    return {"status": "started"}


@router.get("/discovery/characters/results")
async def character_discovery_results():
    """Get the results from the last character discovery run."""
    return {
        "results": discovery_engine.last_character_results,
        "running": discovery_engine.is_running,
    }


@router.get("/status/live")
async def live_status():
    """Comprehensive live status for the GUI — current job, discovery, per-source status."""
    from src.scraper.browser import browser_manager
    from datetime import timedelta

    current_job = scraper_engine.current_job
    disc_state = source_manager.discovery_state

    # Compute next discovery time
    next_discovery = None
    last_run = disc_state.get("last_discovery_run")
    interval_hours = disc_state.get("interval_hours", 6)
    if last_run:
        try:
            last_dt = datetime.fromisoformat(last_run)
            next_dt = last_dt + timedelta(hours=interval_hours)
            next_discovery = next_dt.isoformat()
        except (ValueError, TypeError):
            pass

    # Compute per-source status and next scrape time
    sources_status = []
    now = datetime.now()
    for s in source_manager.get_all_sources():
        status = "idle"
        if current_job and current_job.get("source_id") == s["id"]:
            status = "scraping"
        elif not s.get("enabled", True):
            status = "disabled"
        elif s.get("consecutive_failures", 0) >= 5:
            status = "error"

        next_scrape = None
        if s.get("last_scraped") and s.get("enabled", True):
            try:
                last_dt = datetime.fromisoformat(s["last_scraped"])
                next_dt = last_dt + timedelta(hours=s.get("schedule_hours", 12))
                next_scrape = next_dt.isoformat()
            except (ValueError, TypeError):
                pass

        sources_status.append({
            "id": s["id"],
            "name": s.get("name", ""),
            "status": status,
            "next_scrape": next_scrape,
        })

    return {
        "current_job": current_job,
        "engine_paused": scraper_engine.is_paused,
        "discovery_running": discovery_engine.is_running,
        "discovery_last_results": discovery_engine.last_results,
        "discovery_last_char_results": discovery_engine.last_character_results,
        "next_discovery": next_discovery,
        "browser_available": browser_manager.is_available,
        "scheduler": scheduler.status,
        "sources_status": sources_status,
    }


# === Baserow + Field Mapping ===

@router.get("/baserow/status")
async def baserow_status():
    cfg = config_store.get_section("baserow")
    return {
        "configured": bool(cfg.get("api_url") and cfg.get("api_token") and cfg.get("table_id")),
        "api_url": cfg.get("api_url", ""),
        "table_id": cfg.get("table_id", 0),
        "field_mapping_verified": cfg.get("field_mapping_verified", False),
    }


@router.put("/baserow/config")
async def update_baserow_config(data: BaserowConfig):
    config_store.set("baserow", "api_url", data.api_url)
    config_store.set("baserow", "api_token", data.api_token)
    config_store.set("baserow", "table_id", data.table_id)
    config_store.save()
    scraper_engine.update_baserow_config()
    return {"status": "saved"}


@router.post("/baserow/test")
async def test_baserow():
    cfg = config_store.get_section("baserow")
    client = BaserowClient(
        api_url=cfg.get("api_url", ""),
        api_token=cfg.get("api_token", ""),
        table_id=cfg.get("table_id", 0),
    )
    result = await client.test_connection()
    await client.close()
    return result


@router.get("/baserow/fields")
async def get_baserow_fields():
    cfg = config_store.get_section("baserow")
    client = BaserowClient(
        api_url=cfg.get("api_url", ""),
        api_token=cfg.get("api_token", ""),
        table_id=cfg.get("table_id", 0),
    )
    try:
        match_result = await client.auto_match_fields()
        # Cache table fields
        config_store.set("baserow", "table_fields_cache", match_result.get("table_fields", []))
        config_store.save()
        return match_result
    except Exception as e:
        raise HTTPException(400, f"Failed to fetch fields: {e}")
    finally:
        await client.close()


@router.get("/baserow/field-mapping")
async def get_field_mapping():
    mapping = config_store.get("baserow", "field_mapping", default=DEFAULT_FIELD_MAPPING)
    return {
        "field_mapping": mapping,
        "defaults": DEFAULT_FIELD_MAPPING,
        "verified": config_store.get("baserow", "field_mapping_verified", default=False),
    }


@router.put("/baserow/field-mapping")
async def update_field_mapping(data: FieldMappingUpdate):
    config_store.set("baserow", "field_mapping", data.field_mapping)
    config_store.set("baserow", "field_mapping_verified", True)
    config_store.save()
    scraper_engine.update_baserow_config()
    return {"status": "saved", "field_mapping": data.field_mapping}


@router.get("/baserow/image-proxy")
async def baserow_image_proxy(url: str = Query(..., description="Baserow file URL to proxy")):
    """Proxy Baserow-hosted images to avoid CORS/auth issues."""
    cfg = config_store.get_section("baserow")
    api_url = cfg.get("api_url", "")
    api_token = cfg.get("api_token", "")
    if not api_url or not api_token:
        raise HTTPException(400, "Baserow not configured")

    # Resolve relative URLs against the Baserow API URL
    if url.startswith("/"):
        url = api_url.rstrip("/") + url

    # Security check: only proxy Baserow media paths, not arbitrary URLs.
    # Self-hosted Baserow may return URLs with internal hostnames (e.g.,
    # http://localhost:8000/media/...) so we check the path, not just the origin.
    from urllib.parse import urlparse
    parsed = urlparse(url)
    api_parsed = urlparse(api_url)
    is_media_path = parsed.path.startswith("/media/") or parsed.path.startswith("/api/user-files/")
    is_same_host = parsed.netloc == api_parsed.netloc
    is_known_baserow_host = url.startswith(api_url.rstrip("/"))

    if not (is_known_baserow_host or (is_media_path and is_same_host)):
        # If the host doesn't match but it's a Baserow media path, re-route
        # through the configured API URL (handles internal Docker hostnames)
        if is_media_path:
            url = api_url.rstrip("/") + parsed.path
        else:
            raise HTTPException(400, "URL does not belong to configured Baserow instance")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            headers={"Authorization": f"Token {api_token}"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg")
            return StreamingResponse(
                iter([resp.content]),
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, f"Baserow returned {e.response.status_code}")
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch image: {e}")


@router.get("/baserow/rows")
async def list_baserow_rows(page: int = 1, size: int = 50, search: str = "",
                             order_by: str = ""):
    """Browse wallpapers stored in Baserow."""
    import math

    cfg = config_store.get_section("baserow")
    if not (cfg.get("api_url") and cfg.get("api_token") and cfg.get("table_id")):
        raise HTTPException(400, "Baserow not configured")

    field_mapping = cfg.get("field_mapping", {})

    client = BaserowClient(
        api_url=cfg.get("api_url", ""),
        api_token=cfg.get("api_token", ""),
        table_id=cfg.get("table_id", 0),
    )
    client.field_mapping = field_mapping

    # Determine if we need reverse pagination (newest first).
    # "__newest" or empty → reverse pagination (no Baserow field dependency).
    newest_first = (not order_by) or order_by == "__newest"

    try:
        if newest_first:
            # Reverse pagination: Baserow returns rows oldest-first by default.
            # To show newest first we calculate which items belong on this
            # browse page and fetch the correct Baserow page(s).
            #
            # The naive approach (just reverse the page number) breaks when
            # total is not a multiple of page size — page 1 would show the
            # small remainder instead of a full page of newest items.
            probe = await client.list_rows(page=1, size=1, search=search)
            total = probe.get("count", 0)
            if total == 0:
                data = {"count": 0, "results": [], "next": None, "previous": None}
            else:
                total_pages = math.ceil(total / size)
                if page > total_pages:
                    data = {"count": total, "results": [], "next": None, "previous": None}
                else:
                    # Calculate the 0-based item range for this browse page.
                    # Browse page 1 shows the last `size` items, page 2 the
                    # next-to-last `size` items, etc.
                    end_offset = total - (page - 1) * size
                    start_offset = max(0, end_offset - size)

                    # Which Baserow page(s) contain items [start_offset, end_offset)?
                    first_bpage = (start_offset // size) + 1
                    last_bpage = ((end_offset - 1) // size) + 1 if end_offset > 0 else 1

                    # Fetch the needed Baserow page(s)
                    all_rows = []
                    for bp in range(first_bpage, last_bpage + 1):
                        page_data = await client.list_rows(
                            page=bp, size=size, search=search,
                        )
                        all_rows.extend(page_data.get("results", []))

                    # Slice out just the items for this browse page
                    combined_start = (first_bpage - 1) * size
                    slice_start = start_offset - combined_start
                    slice_end = end_offset - combined_start
                    page_rows = all_rows[slice_start:slice_end]

                    # Reverse for newest-first ordering
                    page_rows.reverse()

                    data = {"count": total, "results": page_rows,
                            "next": None, "previous": None}
        else:
            # Map sort field through field_mapping for Baserow column names.
            desc = order_by.startswith("-")
            raw_field = order_by.lstrip("-")
            mapped = field_mapping.get(raw_field, raw_field)
            actual_order = ("-" if desc else "") + mapped

            data = await client.list_rows(
                page=page, size=size, search=search, order_by=actual_order,
            )
            # If the sort field didn't exist, retry without sorting.
            if data.get("error") and data.get("count", 0) == 0:
                data = await client.list_rows(
                    page=page, size=size, search=search,
                )

        data["field_mapping"] = field_mapping
        data["api_url"] = cfg.get("api_url", "")
        return data
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch rows: {e}")
    finally:
        await client.close()


@router.get("/baserow/rows/{row_id}")
async def get_baserow_row(row_id: int):
    """Get a single Baserow row by ID."""
    cfg = config_store.get_section("baserow")
    if not (cfg.get("api_url") and cfg.get("api_token") and cfg.get("table_id")):
        raise HTTPException(400, "Baserow not configured")
    client = BaserowClient(
        api_url=cfg.get("api_url", ""),
        api_token=cfg.get("api_token", ""),
        table_id=cfg.get("table_id", 0),
    )
    client.field_mapping = cfg.get("field_mapping", {})
    try:
        row = await client.get_row(row_id)
        if not row:
            raise HTTPException(404, "Row not found")
        row["field_mapping"] = client.field_mapping
        row["api_url"] = cfg.get("api_url", "")
        return row
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch row: {e}")
    finally:
        await client.close()


@router.post("/baserow/field-mapping/reset")
async def reset_field_mapping():
    config_store.set("baserow", "field_mapping", dict(DEFAULT_FIELD_MAPPING))
    config_store.set("baserow", "field_mapping_verified", False)
    config_store.save()
    scraper_engine.update_baserow_config()
    return {"status": "reset", "field_mapping": DEFAULT_FIELD_MAPPING}


# === Cloud AI Provider ===

@router.post("/ai/test-connection")
async def test_ai_connection():
    """Test the configured cloud AI provider connection."""
    from src.ai.provider import cloud_ai
    cfg = config_store.get_section("ai")
    provider = cfg.get("cloud_provider", "")
    api_key = cfg.get("cloud_api_key", "")
    if not provider or not api_key:
        raise HTTPException(400, "No cloud AI provider configured. Set a provider and API key first.")
    result = await cloud_ai.test_connection(provider, api_key)
    return result


@router.get("/ai/openrouter-models")
async def get_openrouter_models(api_key: str = Query("", description="OpenRouter API key")):
    """Fetch available vision models from OpenRouter (cached 10 min)."""
    from src.ai.provider import cloud_ai
    if not api_key:
        cfg = config_store.get_section("ai")
        api_key = cfg.get("cloud_api_key", "")
    if not api_key:
        raise HTTPException(400, "No API key provided")
    try:
        result = await cloud_ai.get_openrouter_models(api_key)
        return result
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch models: {e}")


@router.post("/ai/demo-caption")
async def demo_caption(image: UploadFile = File(...)):
    """Upload a wallpaper and get a demo title/alt/tags from the cloud AI.

    Only works when a cloud provider is configured and tested.
    """
    from src.ai.provider import cloud_ai
    cfg = config_store.get_section("ai")
    provider = cfg.get("cloud_provider", "")
    api_key = cfg.get("cloud_api_key", "")
    model = cfg.get("cloud_model", "")
    if not provider or not api_key:
        raise HTTPException(400, "No cloud AI provider configured")

    content_type = image.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    temp_dir = data_path("temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    import uuid
    temp_path = temp_dir / f"demo_{uuid.uuid4().hex[:8]}.tmp"

    try:
        contents = await image.read()
        if len(contents) > 20 * 1024 * 1024:
            raise HTTPException(400, "Image too large (max 20 MB)")
        temp_path.write_bytes(contents)

        title, alt, tags = await cloud_ai.caption(
            provider=provider,
            api_key=api_key,
            image_path=temp_path,
            model=model,
        )
        return {
            "title": title,
            "alt": alt,
            "tags": tags,
            "provider": provider,
            "model": model or (
                "gemini-2.0-flash" if provider == "gemini"
                else "claude-haiku-4-5-20251001" if provider == "claude"
                else "auto-selected"
            ),
        }
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:300] if e.response else str(e)
        raise HTTPException(e.response.status_code if e.response else 500, f"AI API error: {detail}")
    except Exception as e:
        raise HTTPException(500, f"Caption failed: {e}")
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass


# === Characters ===

@router.get("/characters")
async def list_characters(source: str = "", confirmed: bool = False):
    from src.storage.character_store import character_store
    return {
        "characters": character_store.list_all(
            source=source,
            confirmed_only=confirmed,
        ),
        "stats": character_store.stats(),
    }


@router.get("/characters/stats")
async def character_stats():
    from src.storage.character_store import character_store
    return character_store.stats()


@router.get("/characters/search")
async def search_characters(q: str = ""):
    from src.storage.character_store import character_store
    if not q:
        return {"characters": []}
    return {"characters": character_store.search(q)}


@router.post("/characters/describe-image")
async def describe_character_image(
    image: UploadFile = File(...),
    character_name: str = Form(""),
    media_type: str = Form(""),
):
    """Upload an image and get an AI-generated visual description for CLIP.

    The image is saved temporarily, described by BLIP, then deleted.
    Returns a description suitable for the clip_description field.
    """
    if not scraper_engine.captioner.is_available:
        raise HTTPException(503, "AI models not loaded yet")

    # Validate file type
    content_type = image.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    # Save to temp dir
    temp_dir = data_path("temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    import uuid
    temp_path = temp_dir / f"char_upload_{uuid.uuid4().hex[:8]}.tmp"

    try:
        contents = await image.read()
        if len(contents) > 20 * 1024 * 1024:  # 20 MB limit
            raise HTTPException(400, "Image too large (max 20 MB)")
        temp_path.write_bytes(contents)

        # Generate description on a background thread (BLIP is CPU-bound)
        description = await asyncio.get_event_loop().run_in_executor(
            None,
            scraper_engine.captioner.describe_image_for_clip,
            temp_path,
            character_name,
            media_type,
        )

        if not description:
            raise HTTPException(500, "Failed to generate description")

        return {"description": description}
    finally:
        # Always clean up the temp file
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass


@router.post("/characters")
async def add_character(data: CharacterCreate):
    from src.storage.character_store import character_store
    entry = character_store.add(
        name=data.name,
        franchise=data.franchise,
        media_type=data.media_type,
        clip_description=data.clip_description,
        source="user",
    )
    if not entry:
        raise HTTPException(400, "Character already exists")
    # Rebuild CLIP embeddings to include the new character
    try:
        scraper_engine.captioner.refresh_character_embeddings()
    except Exception:
        pass
    return entry.to_dict()


@router.put("/characters/{entry_id}")
async def update_character(entry_id: str, data: CharacterUpdate):
    from src.storage.character_store import character_store
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    entry = character_store.update(entry_id, **fields)
    if not entry:
        raise HTTPException(404, "Character not found")
    try:
        scraper_engine.captioner.refresh_character_embeddings()
    except Exception:
        pass
    return entry.to_dict()


@router.delete("/characters/{entry_id}")
async def delete_character(entry_id: str):
    from src.storage.character_store import character_store
    if not character_store.delete(entry_id):
        raise HTTPException(404, "Character not found")
    try:
        scraper_engine.captioner.refresh_character_embeddings()
    except Exception:
        pass
    return {"status": "deleted"}


@router.post("/characters/{entry_id}/confirm")
async def confirm_character(entry_id: str):
    from src.storage.character_store import character_store
    entry = character_store.confirm(entry_id)
    if not entry:
        raise HTTPException(404, "Character not found")
    try:
        scraper_engine.captioner.refresh_character_embeddings()
    except Exception:
        pass
    return entry.to_dict()


@router.post("/characters/{entry_id}/reject")
async def reject_character(entry_id: str):
    from src.storage.character_store import character_store
    if not character_store.reject(entry_id):
        raise HTTPException(404, "Character not found")
    try:
        scraper_engine.captioner.refresh_character_embeddings()
    except Exception:
        pass
    return {"status": "rejected"}


# === Settings ===

@router.get("/settings")
async def get_settings():
    return {
        "scraping": config_store.get_section("scraping"),
        "jpeg": config_store.get_section("jpeg"),
        "scheduler": config_store.get_section("scheduler"),
        "gallery": config_store.get_section("gallery"),
        "ai": config_store.get_section("ai"),
    }


@router.put("/settings")
async def update_settings(data: SettingsUpdate):
    if data.scraping:
        config_store.update_section("scraping", data.scraping)
    if data.jpeg:
        config_store.update_section("jpeg", data.jpeg)
    if data.scheduler:
        config_store.update_section("scheduler", data.scheduler)
    if data.gallery:
        config_store.update_section("gallery", data.gallery)
    if data.ai:
        config_store.update_section("ai", data.ai)
        # Recompile regex patterns if AI word lists were changed
        try:
            from src.ai.captioner import reload_ai_config
            reload_ai_config()
        except Exception:
            pass
    config_store.save()
    return {"status": "saved"}


@router.get("/ai/defaults")
async def get_ai_defaults():
    """Return factory-default values for all editable AI prompts/word lists."""
    from src.ai.provider import DEFAULT_SYSTEM_PROMPT
    from src.ai.captioner import (
        DEFAULT_STRIP_WORDS, DEFAULT_ROBOTIC_ADJECTIVES,
        DEFAULT_JUNK_TAGS, DEFAULT_GENERIC_PREFIXES,
    )
    return {
        "cloud_system_prompt": DEFAULT_SYSTEM_PROMPT,
        "strip_words": "\n".join(DEFAULT_STRIP_WORDS),
        "robotic_adjectives": "\n".join(DEFAULT_ROBOTIC_ADJECTIVES),
        "junk_tags": "\n".join(DEFAULT_JUNK_TAGS),
        "generic_prefixes": "\n".join(
            p.rstrip() for p in DEFAULT_GENERIC_PREFIXES
        ),
    }


# === Stats ===

@router.get("/stats")
async def get_stats():
    summary = activity_store.get_summary()
    sources = source_manager.get_all_sources()
    queries = source_manager.get_all_queries()

    source_stats = []
    for s in sources:
        source_stats.append({
            "name": s.get("name", ""),
            "category": s.get("category", ""),
            "total_uploaded": s.get("total_uploaded", 0),
            "total_dupes": s.get("total_dupes", 0),
            "total_errors": s.get("total_errors", 0),
            "last_scraped": s.get("last_scraped"),
            "consecutive_failures": s.get("consecutive_failures", 0),
        })

    discovery_stats = {
        "total_queries": len(queries),
        "builtin_queries": len([q for q in queries if q.get("builtin")]),
        "user_queries": len([q for q in queries if not q.get("builtin")]),
        "total_sources_discovered": len([s for s in sources if s.get("category") == "discovered"]),
        "discovery_state": source_manager.discovery_state,
    }

    return {
        "summary": summary,
        "sources": source_stats,
        "discovery": discovery_stats,
        "total_sources": len(sources),
        "enabled_sources": len([s for s in sources if s.get("enabled")]),
    }


# === Logs ===

@router.get("/logs")
async def get_logs(lines: int = 200, level: str = ""):
    """Return recent log lines from the scraper log file."""
    log_file = data_path("logs") / "scraper.log"
    if not log_file.exists():
        return {"lines": [], "total": 0, "file": str(log_file)}
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        # Filter by level if specified
        if level:
            level_upper = level.upper()
            all_lines = [l for l in all_lines if f"| {level_upper}" in l]
        # Return last N lines
        recent = all_lines[-lines:]
        return {
            "lines": [l.rstrip("\n") for l in recent],
            "total": len(all_lines),
            "file": str(log_file),
        }
    except Exception as e:
        return {"lines": [f"Error reading logs: {e}"], "total": 0, "file": str(log_file)}
