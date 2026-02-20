"""Activity store — tracks all scraped wallpapers with thumbnails."""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from src.utils.paths import data_path
from src.utils.logging import setup_logging

logger = setup_logging("activity")

ACTIVITY_PATH = data_path("config", "activity.json")
THUMBNAIL_DIR = data_path("thumbnails")
DEFAULT_MAX_ENTRIES = 5000
DEFAULT_MAX_THUMBNAILS = 5000


def _max_entries() -> int:
    """Read max_entries from config (imported lazily to avoid circular imports)."""
    try:
        from src.storage.config_store import config_store
        return config_store.get("gallery", "max_entries", default=DEFAULT_MAX_ENTRIES)
    except Exception:
        return DEFAULT_MAX_ENTRIES


def _max_thumbnails() -> int:
    """Read max_thumbnails from config (imported lazily to avoid circular imports)."""
    try:
        from src.storage.config_store import config_store
        return config_store.get("gallery", "max_thumbnails", default=DEFAULT_MAX_THUMBNAILS)
    except Exception:
        return DEFAULT_MAX_THUMBNAILS


class ActivityEntry(BaseModel):
    id: str
    timestamp: str
    source_id: str
    source_name: str
    job_id: str
    thumbnail_path: str
    title: str
    alt_text: str
    tags: str
    width: int
    height: int
    aspect_ratio: str
    is_mobile: bool
    img_url: str
    img_hash: str
    baserow_row_id: Optional[int] = None
    file_size_kb: int = 0
    status: str = "uploaded"
    error_message: str = ""


class ActivityStore:
    """Manages activity log and thumbnails."""

    def __init__(self):
        self._entries: list[dict] = []
        self._hash_pixels: dict[str, int] = {}
        self._loaded = False

    def load(self):
        """Load activity from disk and build hash index."""
        try:
            if ACTIVITY_PATH.exists():
                with open(ACTIVITY_PATH, "r") as f:
                    data = json.load(f)
                self._entries = data if isinstance(data, list) else data.get("entries", [])
            else:
                self._entries = []
            self._loaded = True
            self._rebuild_hash_index()
        except Exception as e:
            logger.warning(f"Failed to load activity: {e}")
            self._entries = []
            self._loaded = True

    def _rebuild_hash_index(self):
        """Build hash → max pixel count index for fast dedup lookups."""
        self._hash_pixels: dict[str, int] = {}
        for e in self._entries:
            h = e.get("img_hash", "")
            if h and e.get("status") in ("uploaded", "upgraded"):
                pixels = e.get("width", 0) * e.get("height", 0)
                if pixels > self._hash_pixels.get(h, 0):
                    self._hash_pixels[h] = pixels

    def save(self):
        """Save activity to disk."""
        try:
            ACTIVITY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(ACTIVITY_PATH, "w") as f:
                json.dump(self._entries, f, indent=1, default=str)
        except Exception as e:
            logger.error(f"Failed to save activity: {e}")

    def add_entry(self, entry: ActivityEntry):
        """Add an activity entry, deduplicating by img_hash.

        When a wallpaper with the same perceptual hash already exists:
        - "uploaded" or "upgraded": replace the old entry if the new one
          has higher resolution, otherwise skip.
        - "duplicate" or "error": always add (they're informational).
        This prevents the gallery from showing the same wallpaper at
        multiple resolutions.
        """
        if not self._loaded:
            self.load()

        new_data = entry.model_dump()
        img_hash = entry.img_hash

        # Hash-based dedup for actual uploads/upgrades
        if img_hash and entry.status in ("uploaded", "upgraded"):
            new_pixels = entry.width * entry.height
            for i, existing in enumerate(self._entries):
                if (
                    existing.get("img_hash") == img_hash
                    and existing.get("status") in ("uploaded", "upgraded")
                ):
                    old_pixels = existing.get("width", 0) * existing.get("height", 0)
                    if new_pixels >= old_pixels:
                        # Replace the old entry with the higher-res version
                        self._entries.pop(i)
                        logger.info(
                            f"Gallery dedup: replacing {existing.get('width')}x"
                            f"{existing.get('height')} with {entry.width}x"
                            f"{entry.height} ({img_hash})"
                        )
                    else:
                        # Old entry is already higher-res — just mark this as dup
                        new_data["status"] = "duplicate"
                    break

        self._entries.insert(0, new_data)

        # Keep hash index up to date
        if img_hash and new_data.get("status") in ("uploaded", "upgraded"):
            new_pixels = entry.width * entry.height
            if new_pixels > self._hash_pixels.get(img_hash, 0):
                self._hash_pixels[img_hash] = new_pixels

        # Prune if over max (reads limit from config)
        max_e = _max_entries()
        if len(self._entries) > max_e:
            self._entries = self._entries[:max_e]
        self.save()

    def get_recent(self, limit: int = 50, offset: int = 0,
                   exclude_status: str = "") -> list[dict]:
        """Get recent activity entries.

        When *exclude_status* is set (e.g. ``"duplicate"``), entries with
        that status are skipped so the gallery only shows actually-uploaded
        wallpapers by default.
        """
        if not self._loaded:
            self.load()
        if not exclude_status:
            return self._entries[offset:offset + limit]
        # Filter while respecting offset/limit
        results = []
        skipped = 0
        for entry in self._entries:
            if entry.get("status") == exclude_status:
                continue
            if skipped < offset:
                skipped += 1
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    def get_by_source(self, source_id: str, limit: int = 50) -> list[dict]:
        """Get activity entries for a specific source."""
        if not self._loaded:
            self.load()
        return [e for e in self._entries if e.get("source_id") == source_id][:limit]

    def get_by_status(self, status: str, limit: int = 50) -> list[dict]:
        """Get activity entries by status."""
        if not self._loaded:
            self.load()
        return [e for e in self._entries if e.get("status") == status][:limit]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Search activity entries by title or tags."""
        if not self._loaded:
            self.load()
        query_lower = query.lower()
        results = []
        for entry in self._entries:
            if (
                query_lower in entry.get("title", "").lower()
                or query_lower in entry.get("tags", "").lower()
                or query_lower in entry.get("source_name", "").lower()
            ):
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    def get_summary(self) -> dict:
        """Get summary statistics."""
        if not self._loaded:
            self.load()
        today = datetime.now().strftime("%Y-%m-%d")
        total = len(self._entries)
        today_count = sum(1 for e in self._entries if e.get("timestamp", "").startswith(today))
        uploaded = sum(1 for e in self._entries if e.get("status") == "uploaded")
        upgraded = sum(1 for e in self._entries if e.get("status") == "upgraded")
        duplicates = sum(1 for e in self._entries if e.get("status") == "duplicate")
        errors = sum(1 for e in self._entries if e.get("status") == "error")

        # Per-source counts
        source_counts = {}
        for e in self._entries:
            sname = e.get("source_name", "Unknown")
            source_counts[sname] = source_counts.get(sname, 0) + 1

        return {
            "total": total,
            "today": today_count,
            "uploaded": uploaded,
            "upgraded": upgraded,
            "duplicates": duplicates,
            "errors": errors,
            "by_source": source_counts,
        }

    def get_hash_pixels(self, img_hash: str) -> int:
        """Return the highest pixel count stored for a hash, or 0 if unknown.

        Used by the scraping engine for persistent dedup: if the hash is
        already in the gallery at the same or higher resolution, the
        download can be skipped entirely.
        """
        if not self._loaded:
            self.load()
        return self._hash_pixels.get(img_hash, 0)

    def get_all_hash_pixels(self) -> dict[str, int]:
        """Return the full hash → max pixel count index.

        Used to seed the engine's in-memory dedup cache on startup so it
        survives container/process restarts.
        """
        if not self._loaded:
            self.load()
        return dict(self._hash_pixels)

    def prune(self, max_entries: int = None, max_thumbnails: int = None):
        """Prune old entries and orphaned thumbnails."""
        if not self._loaded:
            self.load()
        if max_entries is None:
            max_entries = _max_entries()
        if max_thumbnails is None:
            max_thumbnails = _max_thumbnails()

        # Prune entries
        if len(self._entries) > max_entries:
            self._entries = self._entries[:max_entries]
            self.save()

        # Prune thumbnails
        try:
            if not THUMBNAIL_DIR.exists():
                return
            referenced = {e.get("img_hash", "") for e in self._entries}
            thumbs = sorted(THUMBNAIL_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
            if len(thumbs) > max_thumbnails:
                for thumb in thumbs[max_thumbnails:]:
                    hash_name = thumb.stem
                    if hash_name not in referenced:
                        thumb.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Thumbnail pruning error: {e}")

    @property
    def count(self) -> int:
        if not self._loaded:
            self.load()
        return len(self._entries)


# Singleton
activity_store = ActivityStore()
