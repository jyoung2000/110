"""FastAPI application — wallpaper scraper with non-fatal startup."""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from src.utils.paths import data_path, get_data_dir
from src.utils.logging import setup_logging

logger = setup_logging("main")


def _ensure_data_dirs():
    """Try to create data directories using the resolved base. Non-fatal."""
    for sub in ["logs", "config", "temp", "wallpapers", "thumbnails", "config/site_profiles"]:
        try:
            data_path(sub).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Could not create {sub}: {e}")
    logger.info(f"Data directory resolved to: {get_data_dir()}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown — ALL wrapped in try/except."""
    logger.info("Wallpaper Scraper starting up...")

    # Ensure data directories exist (best-effort)
    _ensure_data_dirs()

    # Load config
    try:
        from src.storage.config_store import config_store
        config_store.load()
        logger.info("Configuration loaded")
    except Exception as e:
        logger.warning(f"Config load failed (non-fatal): {e}")

    # Load activity store
    try:
        from src.storage.activity_store import activity_store
        activity_store.load()
        logger.info("Activity store loaded")
    except Exception as e:
        logger.warning(f"Activity store load failed (non-fatal): {e}")

    # Load character dictionary
    try:
        from src.storage.character_store import character_store
        character_store.load()
        logger.info("Character store loaded")
    except Exception as e:
        logger.warning(f"Character store load failed (non-fatal): {e}")

    # Load source manager
    try:
        from src.scheduler.source_manager import source_manager
        source_manager.load()
        logger.info("Source manager loaded")
    except Exception as e:
        logger.warning(f"Source manager load failed (non-fatal): {e}")

    # Initialize scraper engine (browser + AI)
    try:
        from src.scraper.engine import scraper_engine
        await scraper_engine.initialize()
        logger.info("Scraper engine initialized")
    except Exception as e:
        logger.warning(f"Scraper engine init failed (non-fatal): {e}")

    # Initialize seed sources and discovery queries (always, not gated on Baserow)
    try:
        from src.scheduler.source_manager import source_manager
        source_manager.initialize_seeds()
        logger.info("Seeds initialized")
    except Exception as e:
        logger.warning(f"Seed initialization failed (non-fatal): {e}")

    # Start scheduler
    try:
        from src.scheduler.scheduler import scheduler
        from src.storage.config_store import config_store
        if config_store.get("scheduler", "enabled", default=True):
            await scheduler.start()
            logger.info("Scheduler started")
    except Exception as e:
        logger.warning(f"Scheduler start failed (non-fatal): {e}")

    logger.info("Wallpaper Scraper ready on port 1629")

    yield

    # Shutdown
    logger.info("Shutting down...")
    try:
        from src.scheduler.scheduler import scheduler
        await scheduler.stop()
    except Exception:
        pass
    try:
        from src.scraper.browser import browser_manager
        await browser_manager.close()
    except Exception:
        pass
    try:
        from src.scraper.engine import scraper_engine
        await scraper_engine.downloader.close()
    except Exception:
        pass
    logger.info("Shutdown complete")


app = FastAPI(title="Wallpaper Scraper", lifespan=lifespan)

# Include API routes
from src.api.routes import router
app.include_router(router)

# Serve web GUI static files (these are inside /app/src — container-owned, always accessible)
static_dir = Path(__file__).parent / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/thumbnails/{filename:path}")
async def serve_thumbnail(filename: str):
    """Fallback thumbnail serving — works even if StaticFiles mount failed."""
    thumb_path = data_path("thumbnails") / filename
    if thumb_path.is_file():
        return FileResponse(str(thumb_path), media_type="image/jpeg")
    return Response(status_code=404)


@app.get("/favicon.ico")
async def favicon_ico():
    """Serve favicon for browsers that request /favicon.ico."""
    favicon_path = static_dir / "favicon.svg"
    if favicon_path.is_file():
        return FileResponse(str(favicon_path), media_type="image/svg+xml")
    return Response(status_code=204)


@app.get("/")
async def index():
    """Serve the main HTML page."""
    return FileResponse(str(static_dir / "index.html"))
