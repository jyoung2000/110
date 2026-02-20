"""Site profile store — persistent per-domain memory for scraping intelligence.

Each domain gets a JSON profile that remembers:
- Which gallery/category/collection pages exist on the site
- Which pages have been visited (avoid re-scraping the same pages)
- Which CSS selectors worked for finding images and download links
- URL patterns for detail pages
- Scraping hints/notes the scraper learns over time
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from src.utils.paths import data_path
from src.utils.logging import setup_logging

logger = setup_logging("site_profiles")

PROFILES_DIR = data_path("config", "site_profiles")


class SiteProfile:
    """Persistent profile for a single website domain."""

    def __init__(self, domain: str):
        self.domain = self._normalize(domain)
        self._path = PROFILES_DIR / f"{self.domain}.json"
        self._data = self._load()

    @staticmethod
    def _normalize(domain: str) -> str:
        d = domain.lower().strip()
        if d.startswith("www."):
            d = d[4:]
        # Sanitize for filesystem
        return "".join(c if c.isalnum() or c in ".-" else "_" for c in d)

    def _load(self) -> dict:
        try:
            if self._path.exists():
                with open(self._path, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load profile for {self.domain}: {e}")
        return self._default()

    def _default(self) -> dict:
        return {
            "domain": self.domain,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            # Gallery/category/collection URLs discovered on the site
            "gallery_urls": [],
            # Pages already visited with timestamps (URL -> last_visited ISO)
            "visited_pages": {},
            # CSS selectors that successfully found images on this site
            "working_image_selectors": [],
            # CSS selectors that successfully found download links
            "working_download_selectors": [],
            # Detail page URL patterns that worked (regex strings)
            "detail_url_patterns": [],
            # Scraping hints learned about this site
            "hints": {
                "has_download_buttons": None,  # True/False/None
                "has_pagination": None,
                "has_lazy_loading": None,
                "primary_image_selector": None,  # The best selector for this site
                "avg_images_per_page": 0,
                "best_sort_param": None,  # e.g., "?sorting=date_added"
            },
            # Categories/tags/collections found on the site
            "categories": [],
            # Stats
            "total_visits": 0,
            "total_images_found": 0,
            "total_uploaded": 0,
            "last_scrape": None,
        }

    def save(self):
        """Persist profile to disk."""
        try:
            PROFILES_DIR.mkdir(parents=True, exist_ok=True)
            self._data["updated_at"] = datetime.now().isoformat()
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Failed to save profile for {self.domain}: {e}")

    # --- Gallery URLs ---

    def add_gallery_url(self, url: str, label: str = ""):
        """Remember a gallery/category/collection URL found on this site."""
        urls = self._data["gallery_urls"]
        # Avoid duplicates
        for entry in urls:
            if entry["url"] == url:
                return
        urls.append({
            "url": url,
            "label": label,
            "discovered_at": datetime.now().isoformat(),
            "times_scraped": 0,
            "last_scraped": None,
            "images_found": 0,
        })
        # Cap at 100 gallery URLs per site
        if len(urls) > 100:
            # Remove least productive ones
            urls.sort(key=lambda x: x.get("images_found", 0), reverse=True)
            self._data["gallery_urls"] = urls[:100]
        self.save()

    def get_fresh_gallery_url(self) -> Optional[str]:
        """Get a gallery URL that hasn't been scraped recently, preferring variety.

        Returns the gallery URL that was scraped least recently, or one never scraped.
        """
        urls = self._data.get("gallery_urls", [])
        if not urls:
            return None

        # Prefer never-scraped URLs first
        never_scraped = [u for u in urls if not u.get("last_scraped")]
        if never_scraped:
            return never_scraped[0]["url"]

        # Otherwise pick the one scraped longest ago
        urls_sorted = sorted(urls, key=lambda u: u.get("last_scraped", ""))
        return urls_sorted[0]["url"]

    def record_gallery_scrape(self, url: str, images_found: int):
        """Record that a gallery URL was scraped."""
        for entry in self._data.get("gallery_urls", []):
            if entry["url"] == url:
                entry["times_scraped"] = entry.get("times_scraped", 0) + 1
                entry["last_scraped"] = datetime.now().isoformat()
                entry["images_found"] = images_found
                self.save()
                return

    # --- Visited Pages ---

    def is_visited(self, url: str, max_age_hours: int = 72) -> bool:
        """Check if a page was visited recently (within max_age_hours)."""
        visited = self._data.get("visited_pages", {})
        last = visited.get(url)
        if not last:
            return False
        try:
            last_dt = datetime.fromisoformat(last)
            age_hours = (datetime.now() - last_dt).total_seconds() / 3600
            return age_hours < max_age_hours
        except (ValueError, TypeError):
            return False

    def mark_visited(self, url: str):
        """Mark a page as visited."""
        visited = self._data.setdefault("visited_pages", {})
        visited[url] = datetime.now().isoformat()
        # Prune old entries (keep last 2000)
        if len(visited) > 2000:
            sorted_items = sorted(visited.items(), key=lambda x: x[1], reverse=True)
            self._data["visited_pages"] = dict(sorted_items[:2000])
        # Don't save on every mark — caller should batch save

    # --- Working Selectors ---

    def record_working_selector(self, selector: str, selector_type: str = "image"):
        """Record a CSS selector that successfully found images or download links."""
        key = "working_image_selectors" if selector_type == "image" else "working_download_selectors"
        selectors = self._data.get(key, [])
        if selector not in selectors:
            selectors.insert(0, selector)  # Most recent first
            self._data[key] = selectors[:10]  # Keep top 10
            self.save()

    def get_working_selectors(self, selector_type: str = "image") -> list[str]:
        """Get selectors that previously worked on this site (most recent first)."""
        key = "working_image_selectors" if selector_type == "image" else "working_download_selectors"
        return self._data.get(key, [])

    # --- Categories ---

    def add_categories(self, categories: list[dict]):
        """Add discovered category/tag/collection links.

        Each entry: {"url": str, "label": str, "type": "category"|"tag"|"collection"}
        """
        existing_urls = {c["url"] for c in self._data.get("categories", [])}
        added = 0
        for cat in categories:
            if cat.get("url") and cat["url"] not in existing_urls:
                self._data.setdefault("categories", []).append({
                    "url": cat["url"],
                    "label": cat.get("label", ""),
                    "type": cat.get("type", "category"),
                    "discovered_at": datetime.now().isoformat(),
                })
                existing_urls.add(cat["url"])
                added += 1
        if added > 0:
            # Cap at 200 categories
            cats = self._data["categories"]
            if len(cats) > 200:
                self._data["categories"] = cats[:200]
            self.save()
            logger.debug(f"Added {added} categories for {self.domain}")

    def get_unvisited_categories(self, limit: int = 10) -> list[dict]:
        """Get category URLs not recently visited, for variety."""
        cats = self._data.get("categories", [])
        visited = self._data.get("visited_pages", {})
        unvisited = []
        for cat in cats:
            url = cat["url"]
            if url not in visited:
                unvisited.append(cat)
            else:
                try:
                    last_dt = datetime.fromisoformat(visited[url])
                    age_hours = (datetime.now() - last_dt).total_seconds() / 3600
                    if age_hours > 48:  # 48h cooldown for categories
                        unvisited.append(cat)
                except (ValueError, TypeError):
                    unvisited.append(cat)
        return unvisited[:limit]

    # --- Hints ---

    def update_hints(self, **kwargs):
        """Update scraping hints for this site."""
        hints = self._data.setdefault("hints", {})
        for k, v in kwargs.items():
            hints[k] = v
        self.save()

    @property
    def hints(self) -> dict:
        return self._data.get("hints", {})

    # --- Stats ---

    def record_scrape_stats(self, images_found: int, uploaded: int):
        """Record aggregate scrape stats."""
        self._data["total_visits"] = self._data.get("total_visits", 0) + 1
        self._data["total_images_found"] = self._data.get("total_images_found", 0) + images_found
        self._data["total_uploaded"] = self._data.get("total_uploaded", 0) + uploaded
        self._data["last_scrape"] = datetime.now().isoformat()
        self.save()


class SiteProfileStore:
    """Cache of loaded site profiles."""

    def __init__(self):
        self._cache: dict[str, SiteProfile] = {}

    def get(self, domain_or_url: str) -> SiteProfile:
        """Get or create a site profile for a domain or URL."""
        if "/" in domain_or_url:
            domain = urlparse(domain_or_url).netloc
        else:
            domain = domain_or_url

        normalized = SiteProfile._normalize(domain)
        if normalized not in self._cache:
            self._cache[normalized] = SiteProfile(domain)
        return self._cache[normalized]

    def list_profiles(self) -> list[str]:
        """List all stored profile domains."""
        try:
            PROFILES_DIR.mkdir(parents=True, exist_ok=True)
            return [p.stem for p in PROFILES_DIR.glob("*.json")]
        except Exception:
            return []


# Singleton
site_profiles = SiteProfileStore()
