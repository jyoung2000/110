"""Source discovery engine — finds new wallpaper sources and characters via web search."""
import asyncio
import re
import random
from datetime import datetime
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from src.scraper.browser import browser_manager
from src.scraper.adapters.generic import GenericAdapter
from src.scheduler.source_manager import source_manager
from src.storage.config_store import config_store
from src.storage.character_store import character_store
from src.utils.logging import setup_logging

logger = setup_logging("discovery")

# Domains to never add as sources
BLOCKED_DOMAINS = {
    "google.com", "youtube.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "reddit.com", "pinterest.com", "amazon.com", "ebay.com",
    "wikipedia.org", "tiktok.com", "linkedin.com", "duckduckgo.com",
    # Ad networks and trackers (show up as outbound links on wallpaper sites)
    "freestar.com", "ads.freestar.com", "doubleclick.net", "googlesyndication.com",
    "googleadservices.com", "adnxs.com", "adsrvr.org", "criteo.com",
    "taboola.com", "outbrain.com", "mgid.com", "revcontent.com",
    # AI art generators (not wallpaper galleries)
    "openart.ai", "midjourney.com", "civitai.com", "lexica.art",
    "playground.com", "ideogram.ai", "nightcafe.studio",
    # Cloud storage / CDN / utility
    "cloudflare.com", "jsdelivr.net", "cdnjs.com", "bootstrapcdn.com",
    "wordpress.org", "wordpress.com", "w3.org", "schema.org",
    # App stores
    "play.google.com", "apps.apple.com", "microsoft.com",
    # Social / sharing
    "t.me", "discord.gg", "discord.com", "whatsapp.com",
}
MIN_VALIDATION_SCORE = 1
MAX_QUERIES_PER_RUN = 5

# Known wallpaper sites — primary discovery mechanism since search engines
# are unreliable from Docker containers (rate limits, CAPTCHAs, etc.)
KNOWN_WALLPAPER_SITES = [
    # High-quality curated wallpaper sites
    {"url": "https://wallpaperscraft.com/all", "name": "WallpapersCraft"},
    {"url": "https://www.pixel4k.com/latest.html", "name": "Pixel4K"},
    {"url": "https://4kwallpapers.com/", "name": "4KWallpapers"},
    {"url": "https://www.wallpapermania.eu/", "name": "WallpaperMania"},
    {"url": "https://wallpaperbat.com/new-wallpapers", "name": "WallpaperBat"},
    {"url": "https://free4kwallpapers.com/", "name": "Free4KWallpapers"},
    {"url": "https://www.hdwallpapers.in/latest_wallpapers.html", "name": "HDWallpapers.in"},
    {"url": "https://wallpaperaccess.com/latest", "name": "WallpaperAccess"},
    {"url": "https://www.setaswall.com/", "name": "SetAsWall"},
    {"url": "https://getwallpapers.com/", "name": "GetWallpapers"},
    {"url": "https://www.uhdpaper.com/", "name": "UHDPaper"},
    {"url": "https://www.wallpaperbetter.com/", "name": "WallpaperBetter"},
    # Large wallpaper aggregators
    {"url": "https://wallhaven.cc/latest", "name": "Wallhaven"},
    {"url": "https://www.wallpaperflare.com/search?wallpaper=nature", "name": "WallpaperFlare"},
    {"url": "https://www.peakpx.com/en/hd-wallpapers", "name": "PeakPx"},
    {"url": "https://wallpapercave.com/", "name": "WallpaperCave"},
    {"url": "https://www.hdwallpapers.net/latest-wallpapers", "name": "HDWallpapers.net"},
    {"url": "https://www.desktopbackground.org/", "name": "DesktopBackground"},
    {"url": "https://rare-gallery.com/", "name": "RareGallery"},
    # Photography / nature focused
    {"url": "https://unsplash.com/t/wallpapers", "name": "Unsplash Wallpapers"},
    {"url": "https://www.pexels.com/search/wallpaper/", "name": "Pexels Wallpapers"},
    {"url": "https://pixabay.com/images/search/wallpaper/", "name": "Pixabay Wallpapers"},
    # High-res / 4K+ focused
    {"url": "https://www.bhmpics.com/", "name": "BHMPics"},
    {"url": "https://www.wallpaperswide.com/", "name": "WallpapersWide"},
    {"url": "https://www.goodfon.com/catalog/nature/", "name": "Goodfon"},
    {"url": "https://www.artstation.com/search?sort_by=trending&category=wallpaper", "name": "ArtStation Wallpapers"},
    # Themed / niche
    {"url": "https://www.dualmonitorbackgrounds.com/", "name": "DualMonitorBGs"},
    {"url": "https://www.ultrawidewallpapers.com/", "name": "UltrawideWallpapers"},
    {"url": "https://www.fonwall.com/en/", "name": "FonWall"},
    {"url": "https://www.wallpaperhub.app/", "name": "WallpaperHub"},
    {"url": "https://www.10wallpaper.com/", "name": "10Wallpaper"},
    {"url": "https://wallpapers.com/", "name": "Wallpapers.com"},
    {"url": "https://www.nawpic.com/", "name": "NawPic"},
    {"url": "https://www.backiee.com/", "name": "Backiee"},
    {"url": "https://www.positrondream.com/wallpapers-all", "name": "PositronDream"},
    # Anime / character wallpaper sites
    {"url": "https://www.zerochan.net/", "name": "Zerochan"},
    {"url": "https://anime-pictures.net/", "name": "AnimePictures"},
    {"url": "https://www.wallpaperflare.com/search?wallpaper=anime", "name": "WallpaperFlare Anime"},
    {"url": "https://wallpapercave.com/anime-wallpapers", "name": "WallpaperCave Anime"},
    {"url": "https://4kwallpapers.com/anime/", "name": "4KWallpapers Anime"},
    {"url": "https://wallhaven.cc/search?categories=010&sorting=favorites", "name": "Wallhaven Anime"},
    {"url": "https://www.wallpaperflare.com/search?wallpaper=gaming", "name": "WallpaperFlare Gaming"},
    {"url": "https://wallpapercave.com/gaming-wallpapers", "name": "WallpaperCave Gaming"},
    {"url": "https://www.wallpaperflare.com/search?wallpaper=superhero", "name": "WallpaperFlare Superhero"},
]


class DiscoveryEngine:
    """Discovers new wallpaper sources by searching the web."""

    def __init__(self):
        self._running = False
        self._last_results: list[dict] = []
        self._last_char_results: list[dict] = []

    def _get_adapter(self) -> GenericAdapter:
        """Get adapter configured with current settings."""
        min_w = config_store.get("scraping", "min_width", default=800)
        min_h = config_store.get("scraping", "min_height", default=600)
        return GenericAdapter(min_width=min_w, min_height=min_h)

    async def run_discovery(self) -> list[dict]:
        """Run one discovery cycle: pick query, search, validate, add sources."""
        if self._running:
            logger.info("Discovery already running, skipping")
            return []

        self._running = True
        results = []

        try:
            if not browser_manager.is_available:
                logger.info("Browser not available, attempting initialization...")
                success = await browser_manager.initialize()
                if not success:
                    logger.warning("Browser init failed, discovery cannot proceed")
                    return []
                logger.info("Browser initialized successfully for discovery")

            # Try multiple queries per discovery run for better results
            total_new_sources = 0
            queries_tried = 0
            used_query_ids = set()

            while queries_tried < MAX_QUERIES_PER_RUN:
                query_data = source_manager.get_next_query()
                if not query_data:
                    logger.info("No enabled discovery queries")
                    break

                query_id = query_data["id"]
                # Avoid re-using the same query in one run
                if query_id in used_query_ids:
                    queries_tried += 1
                    continue
                used_query_ids.add(query_id)

                query_text = query_data["query"]
                queries_tried += 1
                logger.info(f"Discovery query {queries_tried}/{MAX_QUERIES_PER_RUN}: '{query_text}'")

                # Search DuckDuckGo
                urls = await browser_manager.search_duckduckgo(query_text)
                logger.info(f"Found {len(urls)} candidate URLs for '{query_text}'")

                new_sources = 0
                for url in urls:
                    try:
                        domain = urlparse(url).netloc
                        # Skip blocked and known domains
                        if any(blocked in domain for blocked in BLOCKED_DOMAINS):
                            continue
                        if source_manager.domain_exists(domain):
                            continue

                        # Validate: load page and count wallpaper images + detail links
                        score = await self._validate_source(url)
                        result = {
                            "url": url,
                            "domain": domain,
                            "score": score,
                            "added": False,
                            "query": query_text,
                            "timestamp": datetime.now().isoformat(),
                        }

                        if score >= MIN_VALIDATION_SCORE:
                            source = source_manager.add_source(
                                url=url,
                                name=f"{domain} (discovered)",
                                category="discovered",
                                discovered_by_query=query_text,
                                validation_score=score,
                            )
                            result["added"] = True
                            result["source_id"] = source["id"]
                            new_sources += 1
                            logger.info(f"Discovered new source: {domain} (score={score})")

                        results.append(result)

                    except Exception as e:
                        logger.warning(f"Error validating {url}: {e}")

                    # Be polite between validations (randomized)
                    await asyncio.sleep(3 + random.random() * 3)

                # Record query usage
                source_manager.record_query_use(query_id, new_sources)
                total_new_sources += new_sources

                # If we found new sources, stop trying more queries
                if new_sources > 0:
                    logger.info(f"Found {new_sources} new sources with '{query_text}', stopping query loop")
                    break

                # Brief pause between queries
                await asyncio.sleep(2 + random.random() * 2)

            # Always try known wallpaper sites when search didn't find new sources.
            # Search engines are unreliable from Docker (rate limits, CAPTCHAs),
            # so known sites are the primary discovery mechanism.
            if total_new_sources == 0:
                logger.info("No new sources from search, trying known wallpaper sites")
                fallback_urls = self._get_fallback_sites()
                for url, name in fallback_urls:
                    try:
                        domain = urlparse(url).netloc
                        if source_manager.domain_exists(domain):
                            continue
                        if any(blocked in domain for blocked in BLOCKED_DOMAINS):
                            continue

                        score = await self._validate_source(url)
                        result = {
                            "url": url,
                            "domain": domain,
                            "score": score,
                            "added": False,
                            "query": "(known-site-fallback)",
                            "timestamp": datetime.now().isoformat(),
                        }

                        if score >= MIN_VALIDATION_SCORE:
                            source = source_manager.add_source(
                                url=url,
                                name=name,
                                category="discovered",
                                discovered_by_query="(known-site-fallback)",
                                validation_score=score,
                            )
                            result["added"] = True
                            result["source_id"] = source["id"]
                            total_new_sources += 1
                            logger.info(f"Added known wallpaper site: {name} ({domain}, score={score})")

                        results.append(result)

                        if total_new_sources >= 5:
                            break

                    except Exception as e:
                        logger.warning(f"Error validating fallback {url}: {e}")

                    await asyncio.sleep(3 + random.random() * 3)

            # Always stamp discovery as "run" to avoid tight retry loops
            source_manager.update_discovery_state(
                last_discovery_run=datetime.now().isoformat()
            )
            if not results and total_new_sources == 0:
                logger.info("Discovery produced no new sources this cycle")

            self._last_results = results[-10:]
            logger.info(f"Discovery complete: {total_new_sources} new sources from {queries_tried} queries")

            # Auto-generate new queries based on productive discoveries
            if total_new_sources > 0:
                try:
                    self._generate_new_queries()
                except Exception as e:
                    logger.warning(f"Query generation failed: {e}")

            # Run character discovery alongside source discovery
            try:
                char_results = await self.discover_characters()
                new_chars = sum(1 for r in char_results if r.get("added"))
                if new_chars:
                    logger.info(f"Character discovery found {new_chars} new characters")
            except Exception as e:
                logger.warning(f"Character discovery failed: {e}")

        except Exception as e:
            logger.error(f"Discovery error: {e}")
        finally:
            self._running = False

        return results

    async def _validate_source(self, url: str) -> float:
        """Validate a URL as a wallpaper source. Returns score.

        Score combines direct images found + half-credit for detail page links
        (thumbnail-wrapped links to same-domain pages). Pagination gives 1.5x bonus.
        """
        try:
            adapter = self._get_adapter()
            html = await browser_manager.get_page_content(url, wait_time=4000)
            images = await adapter.scrape(html, url)
            detail_links = adapter.get_detail_page_links(html, url)

            # Direct images count fully, detail page links count as 0.5 each
            score = len(images) + len(detail_links) * 0.5
            logger.info(f"Validation {url}: {len(images)} images, {len(detail_links)} detail links, base score={score}")

            # Pagination bonus
            next_page = await adapter.get_next_page_url(html, url, 1)
            if next_page:
                score *= 1.5

            return score

        except Exception as e:
            logger.debug(f"Validation failed for {url}: {e}")
            return 0

    def _get_fallback_sites(self) -> list[tuple[str, str]]:
        """Get known wallpaper sites not already in sources, randomly shuffled."""
        sites = [(s["url"], s["name"]) for s in KNOWN_WALLPAPER_SITES]
        random.shuffle(sites)
        # Filter out already-known domains
        result = []
        for url, name in sites:
            domain = urlparse(url).netloc
            if not source_manager.domain_exists(domain):
                result.append((url, name))
        return result[:8]  # Try up to 8 new sites per discovery run

    def _generate_new_queries(self):
        """Generate new search queries based on productive discovered sources."""
        sources = source_manager.get_all_sources()
        discovered = [s for s in sources if s.get("category") == "discovered"
                      and s.get("total_uploaded", 0) > 0]
        if not discovered:
            return

        existing_queries = {q["query"].lower() for q in source_manager.get_all_queries()}

        for source in discovered[:5]:
            domain = source.get("domain", "")
            if not domain:
                continue
            candidates = [
                f"sites like {domain} wallpaper",
                f"wallpaper sites similar to {domain}",
                f"{domain} alternatives free wallpaper",
            ]
            for candidate in candidates:
                if candidate.lower() not in existing_queries:
                    source_manager.add_query(candidate)
                    existing_queries.add(candidate.lower())
                    logger.info(f"Auto-generated query: '{candidate}'")
                    break  # One new query per productive source

    def discover_outbound_links(self, html: str, page_url: str) -> list[dict]:
        """Find links to other wallpaper sites while scraping a page.

        Looks for outbound links (different domain) that point to wallpaper-related
        sites — blogrolls, "similar sites" sections, partner links, etc.
        Returns list of {"url": str, "domain": str} for new wallpaper domains.
        """
        soup = BeautifulSoup(html, "lxml")
        page_parsed = urlparse(page_url)
        page_root = self._root_domain(page_parsed.netloc)
        found = []
        seen_domains = set()

        # Wallpaper-related URL/text indicators
        wallpaper_hints = re.compile(
            r"wallpaper|background|desktop|hd.?image|4k|uhd|"
            r"screen.?saver|wall.?art|backdrop|"
            r"anime.?wall|character.?wall|fanart.?wall",
            re.I
        )

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            abs_url = urljoin(page_url, href)
            try:
                link_parsed = urlparse(abs_url)
                if not link_parsed.netloc:
                    continue
                link_root = self._root_domain(link_parsed.netloc)

                # Only outbound links (different domain)
                if link_root == page_root:
                    continue
                if link_root in seen_domains:
                    continue

                # Skip blocked domains
                if any(blocked in link_parsed.netloc for blocked in BLOCKED_DOMAINS):
                    continue

                # Skip already-known domains
                if source_manager.domain_exists(link_parsed.netloc):
                    continue

                # Check if the link looks wallpaper-related.
                # Only check domain + path + anchor text, NOT query params.
                # Query params often contain the source site's name (tracking),
                # which causes false positives like ads.freestar.com?utm_source=wallpapersite
                text = link.get_text(strip=True)
                title = link.get("title", "") or ""
                domain_and_path = f"{link_parsed.netloc}{link_parsed.path}"
                combined = f"{domain_and_path} {text} {title}"

                if wallpaper_hints.search(combined):
                    seen_domains.add(link_root)
                    found.append({
                        "url": abs_url,
                        "domain": link_parsed.netloc,
                    })

            except Exception:
                continue

        if found:
            logger.info(f"Found {len(found)} outbound wallpaper links on {page_url}")
        return found

    @staticmethod
    def _root_domain(netloc: str) -> str:
        """Extract root domain from netloc."""
        parts = netloc.lower().split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return netloc.lower()

    # ------------------------------------------------------------------
    # Character discovery — search the web for trending characters
    # ------------------------------------------------------------------

    # Search queries rotated for character discovery.  Each targets a
    # different medium/genre so discoveries are broad.
    _CHAR_QUERIES = [
        "most popular anime characters {year}",
        "trending anime characters right now",
        "top video game characters {year}",
        "popular manga characters list {year}",
        "best new anime characters this season",
        "most popular cartoon characters {year}",
        "trending movie characters {year}",
        "popular comic book characters list",
        "top superhero characters ranked {year}",
        "new anime characters this season {year}",
        "popular genshin impact characters",
        "popular jujutsu kaisen characters",
        "popular one piece characters list",
        "popular demon slayer characters",
        "popular naruto characters list",
        "trending k-pop idols {year}",
        "popular video game characters list",
        "iconic anime characters of all time",
        "best isekai anime characters {year}",
        "popular league of legends characters",
    ]

    # Pattern to extract "Character — Franchise" or "Character (Franchise)" etc.
    _CHAR_EXTRACT_RE = re.compile(
        r"^(?P<name>[A-Z][A-Za-z\s\.\-']{1,30})"
        r"(?:\s*[—–\-|/]\s*|\s+from\s+|\s*\()"
        r"(?P<franchise>[A-Z][A-Za-z\s\.\-:']{1,40})\)?\s*$",
    )

    # Media type indicators found in surrounding text
    _MEDIA_HINTS = {
        "anime": "anime",
        "manga": "anime",
        "light novel": "anime",
        "game": "game",
        "video game": "game",
        "genshin": "game",
        "league of legends": "game",
        "fortnite": "game",
        "valorant": "game",
        "zelda": "game",
        "mario": "game",
        "pokemon": "game",
        "pokémon": "game",
        "movie": "movie",
        "film": "movie",
        "disney": "movie",
        "pixar": "movie",
        "marvel": "comic",
        "dc comics": "comic",
        "comic": "comic",
        "cartoon": "cartoon",
        "nickelodeon": "cartoon",
        "tv show": "tv",
        "tv series": "tv",
        "k-pop": "celebrity",
        "kpop": "celebrity",
        "idol": "celebrity",
        "actor": "celebrity",
        "actress": "celebrity",
        "singer": "celebrity",
        "superhero": "comic",
    }

    async def discover_characters(self) -> list[dict]:
        """Search the web for popular/trending characters and add them.

        Returns a list of result dicts with keys:
            name, franchise, media_type, added (bool), source (query used).
        """
        if not browser_manager.is_available:
            logger.info("Browser not available for character discovery")
            return []

        year = datetime.now().year
        # Pick 3 random queries from the pool
        queries = random.sample(
            self._CHAR_QUERIES,
            min(3, len(self._CHAR_QUERIES)),
        )

        results = []
        total_added = 0

        for raw_query in queries:
            query = raw_query.format(year=year)
            logger.info(f"Character discovery search: '{query}'")

            try:
                urls = await browser_manager.web_search(query)
                if not urls:
                    logger.info(f"No results for character query: '{query}'")
                    continue

                # Visit up to 3 result pages and extract character names
                for url in urls[:3]:
                    try:
                        html = await browser_manager.get_page_content(
                            url, wait_time=4000,
                        )
                        if not html:
                            continue

                        found = self._extract_characters_from_html(
                            html, url, query,
                        )
                        for char_info in found:
                            name = char_info["name"]
                            franchise = char_info["franchise"]
                            media_type = char_info["media_type"]

                            # Skip if already known
                            existing = character_store.search(name)
                            if existing:
                                # Bump discovery count
                                for e in existing:
                                    if e["name"].lower() == name.lower():
                                        character_store.discover(
                                            name=e["name"],
                                            franchise=e.get("franchise", ""),
                                        )
                                        break
                                continue

                            # Generate a CLIP description
                            clip_desc = self._build_clip_description(
                                name, franchise, media_type,
                            )

                            entry = character_store.discover(
                                name=name,
                                franchise=franchise,
                                media_type=media_type,
                                clip_description=clip_desc,
                            )
                            added = entry is not None and entry.source == "discovered"
                            if added:
                                total_added += 1
                                logger.info(
                                    f"Discovered character: {name}"
                                    + (f" ({franchise})" if franchise else "")
                                )

                            results.append({
                                "name": name,
                                "franchise": franchise,
                                "media_type": media_type,
                                "added": added,
                                "source": query,
                                "timestamp": datetime.now().isoformat(),
                            })

                    except Exception as e:
                        logger.debug(f"Error extracting characters from {url}: {e}")

                    await asyncio.sleep(2 + random.random() * 2)

            except Exception as e:
                logger.warning(f"Character discovery search failed for '{query}': {e}")

            await asyncio.sleep(2 + random.random() * 2)

        if total_added > 0:
            character_store.save()
            # Refresh CLIP embeddings so new characters are usable
            try:
                from src.scraper.engine import scraper_engine
                scraper_engine.captioner.refresh_character_embeddings()
            except Exception:
                pass

        self._last_char_results = results[-20:]
        logger.info(
            f"Character discovery complete: {total_added} new characters "
            f"from {len(queries)} queries"
        )
        return results

    def _extract_characters_from_html(
        self, html: str, page_url: str, query: str,
    ) -> list[dict]:
        """Parse an HTML page and extract character names + metadata.

        Looks for common list patterns:
        - <li> items, <h2>/<h3> headings, <td> cells
        - Numbered lists like "1. Gojo Satoru — Jujutsu Kaisen"
        - Bold or linked character names
        """
        soup = BeautifulSoup(html, "lxml")
        characters = []
        seen_names: set = set()

        # Detect media type from query + page title
        page_text = (
            (soup.title.string if soup.title else "") + " " + query
        ).lower()
        default_media = ""
        for hint, mtype in self._MEDIA_HINTS.items():
            if hint in page_text:
                default_media = mtype
                break

        # Extract from list items, headings, table cells
        candidates = []
        for tag in soup.find_all(["li", "h2", "h3", "h4", "td"]):
            text = tag.get_text(strip=True)
            if text and 3 < len(text) < 80:
                candidates.append(text)

        # Also check <strong>/<b> inside <p>
        for tag in soup.find_all(["strong", "b"]):
            text = tag.get_text(strip=True)
            if text and 3 < len(text) < 50:
                candidates.append(text)

        for raw_text in candidates:
            parsed = self._parse_character_line(raw_text, default_media)
            if not parsed:
                continue

            name = parsed["name"]
            name_lower = name.lower()
            if name_lower in seen_names:
                continue

            # Filter out non-character junk
            if self._is_junk_name(name):
                continue

            seen_names.add(name_lower)
            characters.append(parsed)

            # Cap per page to avoid noise
            if len(characters) >= 15:
                break

        return characters

    def _parse_character_line(self, text: str, default_media: str) -> dict | None:
        """Try to parse a character name (and optional franchise) from a text line.

        Handles formats like:
        - "1. Gojo Satoru — Jujutsu Kaisen"
        - "Tanjiro Kamado (Demon Slayer)"
        - "Naruto Uzumaki from Naruto Shippuden"
        - "Spider-Man - Marvel Comics"
        - "Goku"  (plain name)
        """
        # Strip leading numbering: "1.", "1)", "#1", "#3 Name", "01."
        text = re.sub(r"^[\s#]*\d{1,3}[\.\):\-]?\s+", "", text).strip()
        if not text:
            return None

        # Try structured pattern: Name — Franchise
        m = self._CHAR_EXTRACT_RE.match(text)
        if m:
            name = m.group("name").strip().rstrip("—–-|/ ")
            franchise = m.group("franchise").strip().rstrip(")")
            media_type = default_media
            # Detect media from franchise text
            fran_lower = franchise.lower()
            for hint, mtype in self._MEDIA_HINTS.items():
                if hint in fran_lower:
                    media_type = mtype
                    break
            return {
                "name": self._title_case_name(name),
                "franchise": self._title_case_name(franchise),
                "media_type": media_type,
            }

        # Try "Name from Franchise" pattern
        from_match = re.match(
            r"^([A-Z][A-Za-z\s\.\-']{1,30})\s+from\s+(.+)$", text,
        )
        if from_match:
            name = from_match.group(1).strip()
            franchise = from_match.group(2).strip()
            if not self._is_junk_name(name):
                return {
                    "name": self._title_case_name(name),
                    "franchise": self._title_case_name(franchise),
                    "media_type": default_media,
                }

        # Plain name (must be 2-4 words, start with uppercase)
        words = text.split()
        if 1 <= len(words) <= 5 and text[0].isupper():
            # Ensure it looks like a proper name (each word capitalized)
            if all(w[0].isupper() or w.lower() in ("de", "van", "von", "the", "of", "no") for w in words if w):
                name = " ".join(words)
                if not self._is_junk_name(name):
                    return {
                        "name": self._title_case_name(name),
                        "franchise": "",
                        "media_type": default_media,
                    }

        return None

    @staticmethod
    def _is_junk_name(name: str) -> bool:
        """Return True if name is clearly not a character name."""
        lower = name.lower().strip()
        # Too short
        if len(lower) < 2:
            return True
        # Common non-character strings found in list pages
        junk_patterns = {
            "table of contents", "read more", "see also", "related",
            "advertisement", "subscribe", "comments", "share",
            "next", "previous", "home", "menu", "search",
            "source", "image", "photo", "video", "watch",
            "top", "best", "list", "rank", "tier",
            "characters", "anime", "manga", "game", "movie",
            "season", "episode", "chapter", "arc", "series",
            "conclusion", "introduction", "overview", "summary",
            "final thoughts", "honorable mention", "frequently asked",
            "about", "contact", "privacy", "terms", "copyright",
            "loading", "click here", "download", "sign up",
        }
        if lower in junk_patterns:
            return True
        # Purely numeric or very short gibberish
        if lower.isdigit() or len(lower) < 3:
            return True
        # URL fragments
        if "http" in lower or "www." in lower or ".com" in lower:
            return True
        return False

    @staticmethod
    def _title_case_name(name: str) -> str:
        """Proper title case for character names, preserving particles."""
        particles = {"de", "van", "von", "the", "of", "no", "la", "le"}
        words = name.strip().split()
        result = []
        for i, w in enumerate(words):
            if i > 0 and w.lower() in particles:
                result.append(w.lower())
            else:
                result.append(w[0].upper() + w[1:] if w else "")
        return " ".join(result)

    @staticmethod
    def _build_clip_description(
        name: str, franchise: str, media_type: str,
    ) -> str:
        """Generate a CLIP-friendly visual description for a character."""
        parts = [name]
        if franchise:
            parts.append(f"from {franchise}")
        if media_type:
            type_labels = {
                "anime": "anime character",
                "game": "video game character",
                "movie": "movie character",
                "tv": "TV show character",
                "comic": "comic book character",
                "cartoon": "cartoon character",
                "celebrity": "celebrity, real person",
            }
            parts.append(type_labels.get(media_type, f"{media_type} character"))
        else:
            parts.append("fictional character")
        return ", ".join(parts)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_results(self) -> list[dict]:
        return list(self._last_results)

    @property
    def last_character_results(self) -> list[dict]:
        return list(self._last_char_results)


# Singleton
discovery_engine = DiscoveryEngine()
