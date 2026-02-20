"""Central data path resolver with fallback for permission-restricted mounts."""
import os
import shutil
from pathlib import Path

# Primary data directory (volume mount)
_PRIMARY = os.environ.get("SCRAPER_DATA_DIR", "/app/data")
# Fallback (persisted via named Docker volume)
_FALLBACK = os.environ.get("SCRAPER_FALLBACK_DIR", "/tmp/scraper-data")

_resolved_base: str = ""

_SUBDIRS = ["logs", "config", "temp", "wallpapers", "thumbnails"]
_CONFIG_FILES = [
    "config/config.json", "config/sources.json",
    "config/activity.json", "config/jobs.json",
]


def _check_writable(path: str) -> bool:
    """Check if a directory is writable."""
    try:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        test = p / ".write_test"
        test.write_text("ok")
        test.unlink()
        return True
    except Exception:
        return False


def _migrate_fallback_to_primary():
    """If fallback has data and primary becomes writable, copy config files over."""
    fallback = Path(_FALLBACK)
    primary = Path(_PRIMARY)
    if not fallback.exists():
        return
    for rel in _CONFIG_FILES:
        src = fallback / rel
        dst = primary / rel
        if src.exists() and not dst.exists():
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
            except Exception:
                pass


def get_data_dir() -> Path:
    """Get the base data directory, using fallback if primary isn't writable."""
    global _resolved_base
    if _resolved_base:
        return Path(_resolved_base)

    if _check_writable(_PRIMARY):
        _resolved_base = _PRIMARY
        # If user previously ran on fallback, migrate data to primary
        _migrate_fallback_to_primary()
    else:
        _resolved_base = _FALLBACK
        # Ensure fallback subdirs exist
        for sub in _SUBDIRS:
            Path(_FALLBACK, sub).mkdir(parents=True, exist_ok=True)

    return Path(_resolved_base)


def data_path(*parts: str) -> Path:
    """Get a path under the data directory. E.g., data_path('config', 'config.json')."""
    base = get_data_dir()
    result = base.joinpath(*parts)
    # Ensure parent directory exists
    try:
        result.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return result
