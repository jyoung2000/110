"""Persistent character / celebrity dictionary for CLIP recognition.

Merges builtin entries from ``src.ai.characters`` with user-added and
auto-discovered entries.  Stored in ``config/characters.json``.

Auto-discovered characters are found by analysing scraped page metadata
during scraping runs.  They start with ``confirmed: false`` and can be
confirmed/rejected from the UI.
"""
import json
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional
from src.utils.paths import data_path
from src.utils.logging import setup_logging

logger = setup_logging("character_store")

CHARACTERS_PATH = data_path("config", "characters.json")


class CharacterEntry:
    """Single character/celebrity entry."""

    __slots__ = (
        "id", "name", "franchise", "media_type", "clip_description",
        "source", "confirmed", "discovery_count", "created_at",
    )

    def __init__(
        self,
        name: str,
        franchise: str = "",
        media_type: str = "",
        clip_description: str = "",
        source: str = "builtin",      # builtin | user | discovered
        confirmed: bool = True,
        discovery_count: int = 0,
        id: str = "",
        created_at: float = 0.0,
    ):
        self.id = id or uuid.uuid4().hex[:12]
        self.name = name
        self.franchise = franchise
        self.media_type = media_type
        self.clip_description = clip_description
        self.source = source
        self.confirmed = confirmed
        self.discovery_count = discovery_count
        self.created_at = created_at or time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "franchise": self.franchise,
            "media_type": self.media_type,
            "clip_description": self.clip_description,
            "source": self.source,
            "confirmed": self.confirmed,
            "discovery_count": self.discovery_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CharacterEntry":
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            franchise=d.get("franchise", ""),
            media_type=d.get("media_type", ""),
            clip_description=d.get("clip_description", ""),
            source=d.get("source", "builtin"),
            confirmed=d.get("confirmed", True),
            discovery_count=d.get("discovery_count", 0),
            created_at=d.get("created_at", 0.0),
        )

    def clip_tuple(self):
        """Return the tuple format the captioner expects."""
        return (self.name, self.franchise, self.media_type, self.clip_description)


class CharacterStore:
    """JSON-backed character dictionary that survives restarts."""

    def __init__(self):
        self._entries: Dict[str, CharacterEntry] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self):
        """Load user/discovered entries from disk, merge with builtins."""
        from src.ai.characters import KNOWN_CHARACTERS

        # 1. Seed from builtins (always present, never deleted)
        for name, franchise, media_type, desc in KNOWN_CHARACTERS:
            if name.startswith("_generic_"):
                continue  # generics handled separately by captioner
            key = self._key(name, franchise)
            if key not in self._entries:
                self._entries[key] = CharacterEntry(
                    name=name,
                    franchise=franchise,
                    media_type=media_type,
                    clip_description=desc,
                    source="builtin",
                    confirmed=True,
                )

        # 2. Load saved entries (user + discovered) from disk
        if CHARACTERS_PATH.exists():
            try:
                with open(CHARACTERS_PATH, "r") as f:
                    saved = json.load(f)
                for d in saved:
                    entry = CharacterEntry.from_dict(d)
                    key = self._key(entry.name, entry.franchise)
                    if entry.source == "builtin":
                        # Don't overwrite builtin with stale saved copy
                        continue
                    self._entries[key] = entry
                logger.info(f"Loaded {len(saved)} saved character entries")
            except Exception as e:
                logger.warning(f"Failed to load characters: {e}")

        self._loaded = True
        logger.info(
            f"Character dictionary ready: {len(self._entries)} entries "
            f"({self._count_by_source('builtin')} builtin, "
            f"{self._count_by_source('user')} user, "
            f"{self._count_by_source('discovered')} discovered)"
        )

    def save(self) -> bool:
        """Persist user-added and discovered entries to disk."""
        try:
            # Only save non-builtin entries (builtins are always re-seeded)
            to_save = [
                e.to_dict() for e in self._entries.values()
                if e.source != "builtin"
            ]
            CHARACTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CHARACTERS_PATH, "w") as f:
                json.dump(to_save, f, indent=2, default=str)
            return True
        except Exception as e:
            logger.error(f"Failed to save characters: {e}")
            return False

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(
        self,
        name: str,
        franchise: str = "",
        media_type: str = "",
        clip_description: str = "",
        source: str = "user",
        confirmed: bool = True,
    ) -> Optional[CharacterEntry]:
        """Add a new character. Returns the entry, or None if duplicate."""
        if not self._loaded:
            self.load()
        key = self._key(name, franchise)
        if key in self._entries:
            return None  # already exists
        if not clip_description:
            # Auto-generate a basic CLIP description
            parts = [name]
            if franchise:
                parts.append(f"from {franchise}")
            if media_type:
                parts.append(f"{media_type} character")
            clip_description = ", ".join(parts)
        entry = CharacterEntry(
            name=name,
            franchise=franchise,
            media_type=media_type,
            clip_description=clip_description,
            source=source,
            confirmed=confirmed,
        )
        self._entries[key] = entry
        self.save()
        logger.info(f"Added character: {name} ({source})")
        return entry

    def update(self, entry_id: str, **fields) -> Optional[CharacterEntry]:
        """Update an existing entry by ID. Returns updated entry or None."""
        if not self._loaded:
            self.load()
        entry = self._find_by_id(entry_id)
        if not entry:
            return None
        # Remove old key
        old_key = self._key(entry.name, entry.franchise)
        for field, value in fields.items():
            if hasattr(entry, field) and field not in ("id", "source", "created_at"):
                setattr(entry, field, value)
        # Re-key if name/franchise changed
        new_key = self._key(entry.name, entry.franchise)
        if old_key != new_key:
            del self._entries[old_key]
        self._entries[new_key] = entry
        self.save()
        return entry

    def delete(self, entry_id: str) -> bool:
        """Delete a character by ID. Builtins can be deleted (hidden)."""
        if not self._loaded:
            self.load()
        entry = self._find_by_id(entry_id)
        if not entry:
            return False
        key = self._key(entry.name, entry.franchise)
        del self._entries[key]
        self.save()
        logger.info(f"Deleted character: {entry.name}")
        return True

    def confirm(self, entry_id: str) -> Optional[CharacterEntry]:
        """Confirm an auto-discovered character."""
        return self.update(entry_id, confirmed=True)

    def reject(self, entry_id: str) -> bool:
        """Reject (delete) an auto-discovered character."""
        return self.delete(entry_id)

    # ------------------------------------------------------------------
    # Auto-discovery
    # ------------------------------------------------------------------

    def discover(
        self,
        name: str,
        franchise: str = "",
        media_type: str = "",
        clip_description: str = "",
    ) -> Optional[CharacterEntry]:
        """Record an auto-discovered character. Increments count if seen before."""
        if not self._loaded:
            self.load()
        key = self._key(name, franchise)
        if key in self._entries:
            existing = self._entries[key]
            existing.discovery_count += 1
            # Don't save on every increment — batch via periodic save
            return existing

        return self.add(
            name=name,
            franchise=franchise,
            media_type=media_type,
            clip_description=clip_description,
            source="discovered",
            confirmed=False,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_all(
        self, source: str = "", confirmed_only: bool = False,
    ) -> List[dict]:
        """List all entries, optionally filtered by source/confirmed."""
        if not self._loaded:
            self.load()
        results = []
        for entry in self._entries.values():
            if source and entry.source != source:
                continue
            if confirmed_only and not entry.confirmed:
                continue
            results.append(entry.to_dict())
        results.sort(key=lambda e: (e["source"] != "user", e["source"] != "discovered", e["name"].lower()))
        return results

    def get_clip_entries(self) -> list:
        """Get all confirmed entries as (name, franchise, media_type, desc) tuples.

        Used by the captioner to build CLIP embeddings.
        """
        if not self._loaded:
            self.load()
        # Include builtins + confirmed user/discovered
        entries = []
        for e in self._entries.values():
            if e.confirmed:
                entries.append(e.clip_tuple())
        return entries

    def get_by_id(self, entry_id: str) -> Optional[dict]:
        if not self._loaded:
            self.load()
        entry = self._find_by_id(entry_id)
        return entry.to_dict() if entry else None

    def search(self, query: str) -> List[dict]:
        """Search characters by name or franchise."""
        if not self._loaded:
            self.load()
        q = query.lower()
        return [
            e.to_dict() for e in self._entries.values()
            if q in e.name.lower() or q in e.franchise.lower()
        ]

    def stats(self) -> dict:
        if not self._loaded:
            self.load()
        return {
            "total": len(self._entries),
            "builtin": self._count_by_source("builtin"),
            "user": self._count_by_source("user"),
            "discovered": self._count_by_source("discovered"),
            "unconfirmed": sum(1 for e in self._entries.values() if not e.confirmed),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(name: str, franchise: str) -> str:
        return f"{name.lower().strip()}|{franchise.lower().strip()}"

    def _find_by_id(self, entry_id: str) -> Optional[CharacterEntry]:
        for entry in self._entries.values():
            if entry.id == entry_id:
                return entry
        return None

    def _count_by_source(self, source: str) -> int:
        return sum(1 for e in self._entries.values() if e.source == source)


# Singleton
character_store = CharacterStore()
