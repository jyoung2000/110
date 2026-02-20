"""Source and query management — CRUD for wallpaper sources and discovery queries."""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from src.utils.paths import data_path
from src.utils.logging import setup_logging

logger = setup_logging("source_manager")

SOURCES_PATH = data_path("config", "sources.json")

SEED_SOURCES = [
    {"url": "https://wallhaven.cc/search?sorting=views&order=desc", "name": "Wallhaven - Top Views", "schedule_hours": 6},
    {"url": "https://wallhaven.cc/search?sorting=date_added&order=desc", "name": "Wallhaven - Latest", "schedule_hours": 4},
    {"url": "https://www.wallpaperflare.com/search?wallpaper=landscape", "name": "WallpaperFlare - Landscape", "schedule_hours": 12},
    {"url": "https://www.wallpaperflare.com/search?wallpaper=nature", "name": "WallpaperFlare - Nature", "schedule_hours": 12},
    {"url": "https://hdqwalls.com/latest-wallpapers", "name": "HDQWalls - Latest", "schedule_hours": 8},
    {"url": "https://www.pexels.com/search/wallpaper/", "name": "Pexels - Wallpaper", "schedule_hours": 12},
    {"url": "https://unsplash.com/s/photos/wallpaper", "name": "Unsplash - Wallpaper", "schedule_hours": 12},
]

DEFAULT_DISCOVERY_QUERIES = [
    {"query": "free 4k wallpaper download site", "builtin": True},
    {"query": "HD desktop wallpaper website", "builtin": True},
    {"query": "best wallpaper sites 2024 2025", "builtin": True},
    {"query": "free phone wallpaper download", "builtin": True},
    {"query": "nature landscape wallpaper site", "builtin": True},
    {"query": "anime wallpaper download site", "builtin": True},
    {"query": "abstract wallpaper HD free", "builtin": True},
    {"query": "minimalist wallpaper site", "builtin": True},
    {"query": "dark wallpaper AMOLED site", "builtin": True},
    {"query": "ultrawide wallpaper 3440x1440", "builtin": True},
    {"query": "wallpaper download no watermark", "builtin": True},
    {"query": "gaming wallpaper 4k site", "builtin": True},
    {"query": "aesthetic wallpaper HD download", "builtin": True},
    {"query": "space wallpaper 4k free", "builtin": True},
    {"query": "car wallpaper HD download site", "builtin": True},
    {"query": "free wallpaper gallery website no login", "builtin": True},
    {"query": "wallpaper download site 1920x1080", "builtin": True},
    {"query": "high resolution wallpaper collection free", "builtin": True},
    {"query": "dual monitor wallpaper download site", "builtin": True},
    {"query": "photography wallpaper website free download", "builtin": True},
    {"query": "retro vintage wallpaper desktop HD", "builtin": True},
    {"query": "movie TV show wallpaper 4k download", "builtin": True},
    {"query": "cyberpunk neon wallpaper download free", "builtin": True},
    {"query": "mountain ocean wallpaper 4k site", "builtin": True},
    {"query": "art illustration wallpaper download HD", "builtin": True},
    # Character / fandom wallpapers
    {"query": "anime character wallpaper HD download site", "builtin": True},
    {"query": "manga character wallpaper 4k free", "builtin": True},
    {"query": "video game character wallpaper download", "builtin": True},
    {"query": "superhero character wallpaper HD site", "builtin": True},
    {"query": "jujutsu kaisen wallpaper 4k download", "builtin": True},
    {"query": "demon slayer wallpaper HD free download", "builtin": True},
    {"query": "one piece wallpaper 4k desktop", "builtin": True},
    {"query": "naruto wallpaper HD download site", "builtin": True},
    {"query": "genshin impact wallpaper 4k free", "builtin": True},
    {"query": "final fantasy wallpaper HD download", "builtin": True},
    {"query": "marvel DC wallpaper 4k download site", "builtin": True},
    {"query": "studio ghibli wallpaper HD free", "builtin": True},
    {"query": "dragon ball wallpaper 4k desktop download", "builtin": True},
    {"query": "attack on titan wallpaper HD free site", "builtin": True},
    # Wallpaper website discovery
    {"query": "best free wallpaper websites list 2025", "builtin": True},
    {"query": "top wallpaper download sites no signup", "builtin": True},
    {"query": "curated wallpaper gallery website free HD", "builtin": True},
]


class WallpaperSource(BaseModel):
    id: str
    url: str
    name: str
    domain: str
    category: str = "curated"  # curated | discovered | user
    enabled: bool = True
    schedule_hours: int = 12
    last_scraped: Optional[str] = None
    last_scrape_count: int = 0
    total_scraped: int = 0
    total_uploaded: int = 0
    total_dupes: int = 0
    total_errors: int = 0
    consecutive_failures: int = 0
    validation_score: float = 0.0
    discovered_at: str = ""
    discovered_by_query: str = ""
    max_pages: int = 10
    notes: str = ""

    @property
    def is_healthy(self) -> bool:
        return self.consecutive_failures < 5


class DiscoveryQuery(BaseModel):
    id: str
    query: str
    enabled: bool = True
    builtin: bool = False
    times_used: int = 0
    sources_found: int = 0
    sources_productive: int = 0
    last_used: Optional[str] = None
    added_at: str = ""


class SourceManager:
    """Manages wallpaper sources and discovery queries."""

    def __init__(self):
        self._sources: list[dict] = []
        self._queries: list[dict] = []
        self._discovery: dict = {
            "enabled": True,
            "interval_hours": 2,
            "last_discovery_run": None,
            "known_domains": [],
            "query_index": 0,
        }
        self._loaded = False

    def load(self):
        """Load sources and queries from disk."""
        try:
            if SOURCES_PATH.exists():
                with open(SOURCES_PATH, "r") as f:
                    data = json.load(f)
                self._sources = data.get("sources", [])
                self._queries = data.get("queries", [])
                self._discovery = data.get("discovery", self._discovery)
            self._loaded = True
            logger.info(f"Loaded {len(self._sources)} sources, {len(self._queries)} queries")
        except Exception as e:
            logger.warning(f"Failed to load sources: {e}")
            self._loaded = True

    def save(self):
        """Save sources and queries to disk."""
        try:
            SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "sources": self._sources,
                "queries": self._queries,
                "discovery": self._discovery,
            }
            with open(SOURCES_PATH, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save sources: {e}")

    def initialize_seeds(self):
        """Add seed sources and default queries on first launch."""
        if not self._loaded:
            self.load()

        # Only seed if no sources exist
        if not self._sources:
            from urllib.parse import urlparse
            for seed in SEED_SOURCES:
                domain = urlparse(seed["url"]).netloc
                source = WallpaperSource(
                    id=uuid.uuid4().hex[:12],
                    url=seed["url"],
                    name=seed["name"],
                    domain=domain,
                    category="curated",
                    schedule_hours=seed["schedule_hours"],
                    discovered_at=datetime.now().isoformat(),
                )
                self._sources.append(source.model_dump())
                if domain not in self._discovery["known_domains"]:
                    self._discovery["known_domains"].append(domain)
            logger.info(f"Seeded {len(SEED_SOURCES)} sources")

        # Seed queries if none exist, or add missing builtins to existing list
        if not self._queries:
            for dq in DEFAULT_DISCOVERY_QUERIES:
                query = DiscoveryQuery(
                    id=f"b_{uuid.uuid4().hex[:8]}",
                    query=dq["query"],
                    enabled=True,
                    builtin=True,
                    added_at=datetime.now().isoformat(),
                )
                self._queries.append(query.model_dump())
            logger.info(f"Seeded {len(DEFAULT_DISCOVERY_QUERIES)} discovery queries")
        else:
            # Re-seed: add any new builtin queries that don't exist yet
            existing_texts = {q["query"].lower() for q in self._queries}
            added = 0
            for dq in DEFAULT_DISCOVERY_QUERIES:
                if dq["query"].lower() not in existing_texts:
                    query = DiscoveryQuery(
                        id=f"b_{uuid.uuid4().hex[:8]}",
                        query=dq["query"],
                        enabled=True,
                        builtin=True,
                        added_at=datetime.now().isoformat(),
                    )
                    self._queries.append(query.model_dump())
                    existing_texts.add(dq["query"].lower())
                    added += 1
            if added:
                logger.info(f"Added {added} new builtin discovery queries")

        self.save()

    # === Source CRUD ===

    def get_all_sources(self) -> list[dict]:
        if not self._loaded:
            self.load()
        return list(self._sources)

    def get_enabled_sources(self) -> list[dict]:
        if not self._loaded:
            self.load()
        return [s for s in self._sources if s.get("enabled", True)]

    def get_source(self, source_id: str) -> Optional[dict]:
        if not self._loaded:
            self.load()
        for s in self._sources:
            if s["id"] == source_id:
                return s
        return None

    def add_source(self, url: str, name: str = "", category: str = "user",
                   schedule_hours: int = 12, discovered_by_query: str = "",
                   validation_score: float = 0.0) -> dict:
        """Add a new source."""
        if not self._loaded:
            self.load()
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        source = WallpaperSource(
            id=uuid.uuid4().hex[:12],
            url=url,
            name=name or domain,
            domain=domain,
            category=category,
            schedule_hours=schedule_hours,
            validation_score=validation_score,
            discovered_at=datetime.now().isoformat(),
            discovered_by_query=discovered_by_query,
        )
        d = source.model_dump()
        self._sources.append(d)
        normalized = self._normalize_domain(domain)
        if not any(self._normalize_domain(kd) == normalized for kd in self._discovery["known_domains"]):
            self._discovery["known_domains"].append(domain)
        self.save()
        return d

    def update_source(self, source_id: str, **kwargs) -> Optional[dict]:
        """Update a source's fields."""
        if not self._loaded:
            self.load()
        for i, s in enumerate(self._sources):
            if s["id"] == source_id:
                for k, v in kwargs.items():
                    if k in s:
                        s[k] = v
                self.save()
                return s
        return None

    def remove_source(self, source_id: str) -> bool:
        if not self._loaded:
            self.load()
        initial = len(self._sources)
        self._sources = [s for s in self._sources if s["id"] != source_id]
        if len(self._sources) < initial:
            self.save()
            return True
        return False

    def toggle_source(self, source_id: str) -> Optional[dict]:
        for s in self._sources:
            if s["id"] == source_id:
                s["enabled"] = not s.get("enabled", True)
                self.save()
                return s
        return None

    def reorder_sources(self, source_ids: list[str]) -> bool:
        """Reorder sources to match the given list of IDs.

        Any sources not in the list are appended at the end in their
        current relative order.
        """
        if not self._loaded:
            self.load()
        by_id = {s["id"]: s for s in self._sources}
        reordered = []
        seen = set()
        for sid in source_ids:
            if sid in by_id and sid not in seen:
                reordered.append(by_id[sid])
                seen.add(sid)
        # Append any sources not included in the request
        for s in self._sources:
            if s["id"] not in seen:
                reordered.append(s)
        self._sources = reordered
        self.save()
        return True

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        """Normalize domain for comparison (strip www., lowercase)."""
        d = domain.lower()
        if d.startswith("www."):
            d = d[4:]
        return d

    def domain_exists(self, domain: str) -> bool:
        if not self._loaded:
            self.load()
        normalized = self._normalize_domain(domain)
        return any(
            self._normalize_domain(d) == normalized
            for d in self._discovery.get("known_domains", [])
        )

    def record_scrape(self, source_id: str, uploaded: int, dupes: int, errors: int):
        """Record scrape results for a source."""
        for s in self._sources:
            if s["id"] == source_id:
                s["last_scraped"] = datetime.now().isoformat()
                s["last_scrape_count"] = uploaded
                s["total_scraped"] = s.get("total_scraped", 0) + uploaded + dupes
                s["total_uploaded"] = s.get("total_uploaded", 0) + uploaded
                s["total_dupes"] = s.get("total_dupes", 0) + dupes
                s["total_errors"] = s.get("total_errors", 0) + errors
                if errors > 0 and uploaded == 0:
                    s["consecutive_failures"] = s.get("consecutive_failures", 0) + 1
                else:
                    s["consecutive_failures"] = 0
                self.save()
                return

    # === Query CRUD ===

    def get_all_queries(self) -> list[dict]:
        if not self._loaded:
            self.load()
        return list(self._queries)

    def add_query(self, query_text: str) -> dict:
        """Add a user query."""
        if not self._loaded:
            self.load()
        query = DiscoveryQuery(
            id=f"u_{uuid.uuid4().hex[:8]}",
            query=query_text,
            enabled=True,
            builtin=False,
            added_at=datetime.now().isoformat(),
        )
        d = query.model_dump()
        self._queries.append(d)
        self.save()
        return d

    def remove_query(self, query_id: str) -> bool:
        """Remove a query. Fails for builtin queries."""
        if not self._loaded:
            self.load()
        for q in self._queries:
            if q["id"] == query_id:
                if q.get("builtin", False):
                    return False
                self._queries = [q2 for q2 in self._queries if q2["id"] != query_id]
                self.save()
                return True
        return False

    def update_query(self, query_id: str, **kwargs) -> Optional[dict]:
        """Update a query. Builtin queries can only toggle enabled."""
        if not self._loaded:
            self.load()
        for q in self._queries:
            if q["id"] == query_id:
                is_builtin = q.get("builtin", False)
                for k, v in kwargs.items():
                    if is_builtin and k not in ("enabled",):
                        continue
                    if k in q:
                        q[k] = v
                self.save()
                return q
        return None

    def get_next_query(self) -> Optional[dict]:
        """Get next enabled query using weighted selection based on effectiveness."""
        import random
        if not self._loaded:
            self.load()
        enabled = [q for q in self._queries if q.get("enabled", True)]
        if not enabled:
            return None

        weights = []
        for q in enabled:
            times = q.get("times_used", 0)
            found = q.get("sources_found", 0)
            productive = q.get("sources_productive", 0)

            # Base weight ensures every query gets a chance
            weight = 1.0
            if times > 0:
                find_rate = found / times
                weight += find_rate * 2.0
                if found > 0:
                    productive_rate = productive / found
                    weight += productive_rate * 3.0
            else:
                # Untried queries get exploration bonus
                weight += 0.5
            weights.append(weight)

        query = random.choices(enabled, weights=weights, k=1)[0]
        self.save()
        return query

    def record_query_use(self, query_id: str, sources_found: int = 0):
        """Record that a query was used in discovery."""
        for q in self._queries:
            if q["id"] == query_id:
                q["times_used"] = q.get("times_used", 0) + 1
                q["sources_found"] = q.get("sources_found", 0) + sources_found
                q["last_used"] = datetime.now().isoformat()
                self.save()
                return

    # === Source quality + learning ===

    def compute_source_quality(self, source_id: str) -> float:
        """Compute a quality score (0-100) for a source based on its history."""
        source = self.get_source(source_id)
        if not source:
            return 0.0
        total_scraped = source.get("total_scraped", 0)
        total_uploaded = source.get("total_uploaded", 0)
        total_errors = source.get("total_errors", 0)
        consecutive_failures = source.get("consecutive_failures", 0)

        if total_scraped == 0:
            return 50.0  # Unknown, neutral score

        upload_ratio = total_uploaded / max(total_scraped, 1)
        error_ratio = total_errors / max(total_scraped, 1)
        failure_penalty = min(consecutive_failures * 10, 50)

        score = (upload_ratio * 60) + (1 - error_ratio) * 20 + 20 - failure_penalty
        return max(0.0, min(100.0, score))

    def record_source_productive(self, source_id: str):
        """Mark the query that discovered a source as having produced a productive source."""
        source = self.get_source(source_id)
        if not source:
            return
        discovered_by = source.get("discovered_by_query", "")
        if not discovered_by:
            return
        for q in self._queries:
            if q["query"] == discovered_by:
                q["sources_productive"] = q.get("sources_productive", 0) + 1
                self.save()
                return

    # === Discovery state ===

    @property
    def discovery_state(self) -> dict:
        if not self._loaded:
            self.load()
        return dict(self._discovery)

    def update_discovery_state(self, **kwargs):
        for k, v in kwargs.items():
            if k in self._discovery:
                self._discovery[k] = v
        self.save()


# Singleton
source_manager = SourceManager()
