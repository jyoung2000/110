"""Generic wallpaper adapter — works on any wallpaper site without site-specific code.

Understands the gallery→detail→download flow:
- Gallery/listing pages have thumbnails linking to detail pages
- Detail pages have one main wallpaper image + optional download buttons
- The scraper should follow thumbnails to detail pages, not download thumbnails
- On detail pages, always picks the HIGHEST resolution version available
"""
import re
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
from .base import BaseAdapter, ScrapedImage
from src.utils.logging import setup_logging

logger = setup_logging("generic_adapter")

# Minimum dimensions to consider an image a wallpaper
MIN_WIDTH = 800
MIN_HEIGHT = 600

# URL patterns that suggest high-resolution images
HIGHRES_PATTERNS = [
    r"original", r"/full/", r"download", r"highres", r"large",
    r"(\d{3,4})x(\d{3,4})", r"4k", r"uhd", r"2160", r"1440", r"1080",
    r"wallpaper", r"wp-content/uploads",
    r"raw", r"source", r"/max/",
    r"hires", r"retina", r"[_-]2x", r"[_-]3x", r"[_-]xl",
    r"full[_-]size", r"[_-]large", r"[_-]big", r"/orig/", r"[_-]orig\b",
]

# URL path segments that indicate non-full-resolution images, with upgrade alternatives.
# When a found image URL contains a preview pattern (left), try the replacements (right)
# to get the full-resolution version before falling back to the preview.
URL_UPGRADE_MAP = [
    ("/wallpaper/nbig/", ["/wallpaper/original/"]),       # Goodfon medium-large preview
    ("/wallpaper/big/", ["/wallpaper/original/"]),         # Goodfon large preview
    ("/resized/", ["/original/"]),
    ("/compressed/", ["/original/"]),
    ("/medium/", ["/original/", "/large/"]),
    ("/small/", ["/original/", "/large/"]),
    ("/thumb/", ["/original/", "/large/"]),
    ("/thumbnails/", ["/original/"]),
    ("/preview/", ["/original/", "/full/"]),
]

# Patterns to exclude (thumbnails, icons, UI elements, ads, e-commerce CDNs)
EXCLUDE_PATTERNS = [
    r"logo", r"icon", r"avatar", r"banner", r"sprite",
    r"placeholder", r"loading", r"spinner", r"ad[_-]",
    r"facebook", r"twitter", r"instagram", r"pinterest",
    r"google", r"analytics", r"tracking", r"pixel",
    r"btn", r"button", r"arrow", r"close", r"menu",
    r"1x1", r"spacer", r"blank", r"transparent",
    r"/thumb[s]?/", r"/small/", r"/preview/", r"/mini/",
    r"[_-]t\.", r"[_-]sq\.", r"[_-]sm\.", r"[_-]xs\.",
    r"\.th\.", r"/tiny/", r"/micro/",
    # URL-embedded tiny dimensions (e.g., image_95x95.webp, 50x50.jpg)
    r"[_/]\d{1,2}x\d{1,2}[_./]",
    r"/compressed/", r"/optimized/", r"/resized/",
    # Stock photo / watermarked image domains
    r"istockphoto\.com", r"gettyimages\.", r"shutterstock\.com",
    r"stock\.adobe\.com", r"depositphotos\.com", r"dreamstime\.com",
    r"123rf\.com", r"alamy\.com", r"bigstockphoto\.com",
    r"canstockphoto\.com", r"photodune\.net", r"pond5\.com",
    r"stockfresh\.com", r"vecteezy\.com/photo", r"freepik\.com",
    r"rf\.com", r"eyeem\.com", r"500px\.com/photo/.*/licensing",
    # Watermark URL indicators
    r"[_-]watermark", r"/watermarked/", r"/comp/", r"/comps/",
    r"/preview/comp", r"/sample/", r"/demo/",
    # Ad/e-commerce CDN domains (appear as ads on wallpaper sites)
    r"alicdn\.com", r"aliexpress\.com", r"alibaba\.com", r"taobao\.com",
    r"amazon\.com/images", r"ebay\.com", r"shopify\.com",
    r"doubleclick\.net", r"googlesyndication", r"adnxs\.com",
    r"adsrvr\.org", r"adservice", r"pagead",
    # Additional ad networks & trackers commonly found on wallpaper sites
    r"outbrain\.com", r"taboola\.com", r"mgid\.com", r"revcontent\.com",
    r"criteo\.", r"pubmatic\.com", r"rubiconproject\.com", r"openx\.net",
    r"bidswitch\.net", r"casalemedia\.com", r"3lift\.com", r"sharethrough\.com",
    r"moatads\.com", r"adform\.net", r"quantserve\.com", r"scorecardresearch",
    r"popads\.net", r"propellerads", r"exoclick\.com", r"juicyads\.com",
    r"trafficjunky", r"adsterra", r"hilltopads",
    r"cdn\.ampproject\.org", r"syndication\.twitter",
    r"/ads/", r"/advert", r"/sponsor",
]

# URL patterns for navigation/non-detail pages to skip in detail page detection
NAV_EXCLUDE_PATTERNS = [
    r"/categor(y|ies)/", r"/tags?/", r"/search", r"/sort", r"/filter",
    r"/login", r"/register", r"/sign[_-]?up", r"/sign[_-]?in",
    r"/about", r"/contact", r"/terms", r"/privacy", r"/faq", r"/help",
    r"/cart", r"/checkout", r"/account", r"/settings", r"/profile",
    r"/feed", r"/trending", r"/popular", r"/latest", r"/top/?$",
    r"^/?$", r"/index\.html?$",
    r"^/@",                # User profile pages (/@username)
    r"^/user/", r"^/u/",  # Other user page patterns
    r"^/author/", r"^/photographer/", r"^/contributor/",
    r"/collections?/?$",   # Collection listing pages
    # Category/listing pages with "-desktop-" prefix (WallpapersWide, etc.)
    r"-desktop-wallpapers", r"-desktop-backgrounds",
    # Resolution listing pages (e.g., /3840x2160-wallpapers-r.html)
    r"^\d+x\d+-wallpapers",
    # Top/new/popular listing pages (common on wallpaper sites)
    r"/top_wallpapers", r"/new_wallpapers", r"/popular_wallpapers",
    r"/hot_wallpapers", r"/best_wallpapers", r"/random_wallpapers",
    # Common page-level listing patterns
    r"/page/\d+", r"[?&]page=\d+",
    # Wallpaper site-specific listing pages
    r"/wallpaper\.html$",   # e.g., wallpaperswide.com/wallpaper.html (landing page)
]

# Image extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}

# Known wallpaper image selectors on detail pages (site-specific but common)
DETAIL_IMAGE_SELECTORS = [
    "img#wallpaper",              # Wallhaven
    "img#show_img",               # WallpaperFlare
    "img.wallpaper__image",       # WallpapersCraft
    ".wallpaper__image",          # WallpapersCraft (may be div with bg)
    "img.main-wallpaper",
    "img.wallpaper-image",
    "img[itemprop='contentUrl']",
    "img[itemprop='image']",
    "img.detail-image",
    "img.full-image",
    # HDWallpapers.in and similar sites
    "img.wallpaper",
    "img.wall",
    "a.download img",             # Download link wrapping an image
    ".wallpaper-preview img",
    ".wallpaper-detail img",
    ".wallpaper-container img",
    "#wallpaper-image",
    "img[alt*='wallpaper']",      # Images with wallpaper in alt text
    ".wall-img img",
    ".preview img",
    ".main-image img",
    "img.photo",
    "img.primary-image",
]

# Selectors for download buttons/links on detail pages
DOWNLOAD_SELECTORS = [
    "a[href*='/download']",
    "a[download]",
    "a.download",
    "a.btn-download",
    "a.download-btn",
    "a.download-button",
    "a[data-action='download']",
    "a[title*='Download']",
    "a[title*='download']",
    # HDWallpapers.in and similar
    "a[href*='download']",
    "a.wallpaper-download",
    "a[class*='download']",
    ".download-links a",
    ".download-resolutions a",
    "a[href*='getwall']",
    "a[href*='original']",
]


class GenericAdapter(BaseAdapter):
    """Universal wallpaper adapter using heuristics to find wallpaper images on any site.

    Understands two page types:
    - Listing/gallery pages: have many thumbnails, each linking to a detail page
    - Detail pages: have one main image + optional download button
    """

    @staticmethod
    def get_upgraded_urls(url: str) -> list[str]:
        """Generate higher-resolution URL alternatives by replacing known preview path segments.

        Returns a list of URLs to try before falling back to the original.
        E.g. '/wallpaper/nbig/...' -> ['/wallpaper/original/...']
        """
        url_lower = url.lower()
        alternatives = []
        for pattern, replacements in URL_UPGRADE_MAP:
            if pattern in url_lower:
                idx = url_lower.index(pattern)
                for replacement in replacements:
                    upgraded = url[:idx] + replacement + url[idx + len(pattern):]
                    if upgraded != url:
                        alternatives.append(upgraded)
        return alternatives

    def __init__(self, min_width: int = MIN_WIDTH, min_height: int = MIN_HEIGHT):
        self.min_width = min_width
        self.min_height = min_height

    # ------------------------------------------------------------------
    # Content-based page classification
    # ------------------------------------------------------------------

    @staticmethod
    def classify_page(html: str, page_url: str = "") -> str:
        """Classify a page as 'listing' or 'detail' based on its content.

        Uses structural heuristics — not URL patterns — to decide whether
        a page is a gallery/listing (many thumbnails in a grid) or a
        wallpaper detail page (one main image + download options).

        Returns 'listing', 'detail', or 'unknown'.
        """
        soup = BeautifulSoup(html, "lxml")

        # --- Collect signals ---
        score = 0  # positive = listing, negative = detail

        # 1. Count images and image-wrapping links
        all_imgs = soup.find_all("img")
        total_imgs = len(all_imgs)

        # Count images wrapped in <a> tags (thumbnail → detail pattern)
        thumb_linked = 0
        for img in all_imgs:
            parent = img.parent
            if parent and parent.name == "a":
                thumb_linked += 1
            elif parent:
                grandparent = getattr(parent, "parent", None)
                if grandparent and grandparent.name == "a":
                    thumb_linked += 1

        # Many thumbnail-linked images strongly indicate a listing page
        if thumb_linked >= 10:
            score += 40
        elif thumb_linked >= 5:
            score += 20
        elif thumb_linked <= 1:
            score -= 15

        # 2. Grid / card / gallery CSS patterns
        grid_classes = soup.find_all(
            class_=re.compile(
                r"grid|gallery|thumbs|thumb-list|wall-list|card-list|"
                r"image-list|photo-list|wallpapers|tiles|mosaic",
                re.I,
            )
        )
        if grid_classes:
            score += 25

        # 3. Repeating item containers (li.wall, div.thumb, etc.)
        item_selectors = [
            "li.wall", "li.thumb", "div.thumb", "div.card",
            "div.grid-item", "div.tile", "article.post",
            "div.wallpaper-item", "div.photo-item", "figure.item",
        ]
        for sel in item_selectors:
            items = soup.select(sel)
            if len(items) >= 4:
                score += 30
                break

        # 4. Download buttons / resolution selectors (strong detail signal)
        download_links = []
        for sel in DOWNLOAD_SELECTORS:
            try:
                download_links.extend(soup.select(sel))
            except Exception:
                pass
        if len(download_links) >= 2:
            score -= 35  # Multiple download/resolution options = detail page
        elif len(download_links) == 1:
            score -= 15

        # 5. Detail-page-specific image selectors
        for sel in DETAIL_IMAGE_SELECTORS:
            try:
                if soup.select(sel):
                    score -= 30
                    break
            except Exception:
                pass

        # 6. Pagination (strong listing signal)
        pagination_patterns = [
            "a[class*='next']", "a[rel='next']", "li.next",
            ".pagination", ".pager", "nav.pages",
            "a[class*='page']",
        ]
        for sel in pagination_patterns:
            try:
                if soup.select(sel):
                    score += 15
                    break
            except Exception:
                pass

        # 7. Image count as a tiebreaker
        if total_imgs > 20:
            score += 10
        elif total_imgs <= 5:
            score -= 10

        # --- Decide ---
        if score >= 15:
            return "listing"
        elif score <= -15:
            return "detail"
        return "unknown"

    async def scrape(self, html: str, page_url: str) -> list[ScrapedImage]:
        """Parse HTML and find wallpaper-quality images.

        On detail pages: looks for the main wallpaper image via known selectors,
        download buttons, and the single largest image.
        On listing pages: finds direct high-res image links (but the engine should
        prefer detail page links over these).
        """
        soup = BeautifulSoup(html, "lxml")
        images = []
        seen_urls = set()

        # FIRST: Try detail-page-specific strategies (targeted, high confidence)
        detail_images = self._find_detail_page_images(soup, page_url, seen_urls)
        if detail_images:
            logger.info(f"Found {len(detail_images)} wallpaper images via detail-page detection on {page_url}")

        # SECOND: Try download buttons/links (common on wallpaper sites)
        download_images = self._find_download_links(soup, page_url, seen_urls)
        if download_images:
            logger.info(f"Found {len(download_images)} wallpaper images via download links on {page_url}")

        # Combine targeted results
        targeted_images = detail_images + download_images

        # If targeted strategies found several images, return them
        # (no need to run general extraction on true detail pages with clear selectors)
        total_page_imgs = len(soup.find_all("img"))
        if targeted_images and (len(targeted_images) >= 3 or total_page_imgs <= 15):
            return self._deduplicate_images(targeted_images)

        # THIRD: Try to find the single largest/main image on the page
        # (detail pages often have one hero image without specific selectors)
        if not targeted_images:
            hero = self._find_hero_image(soup, page_url, seen_urls)
            if hero:
                targeted_images.append(hero)
                logger.info(f"Found hero image on {page_url}")

        # If we have some targeted images but the page has many more <img> tags,
        # this is likely a collection/gallery page — also run general extraction
        # to catch additional wallpapers the selectors missed.
        if targeted_images and total_page_imgs <= 15:
            return self._deduplicate_images(targeted_images)

        # FOURTH: General image extraction (for pages without specific selectors,
        # or collection pages where targeted strategies only found a few)

        # Strategy 1: Find direct high-res image links (<a> pointing to image files)
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            abs_url = urljoin(page_url, href)
            if self._is_image_url(abs_url) and abs_url not in seen_urls:
                if not self._is_excluded(abs_url):
                    score = self._score_url(abs_url)
                    if score > 0:
                        seen_urls.add(abs_url)
                        img_tag = link.find("img")
                        alt = ""
                        title = ""
                        thumb = ""
                        if img_tag:
                            alt = img_tag.get("alt", "") or ""
                            title = img_tag.get("title", "") or alt
                            thumb = urljoin(page_url, img_tag.get("src", ""))
                        images.append(self._make_image(abs_url, thumb, alt, title, page_url, link, score))

        # Strategy 2: Find img tags with large dimensions or high-res src
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "") or img.get("data-original", "") or ""
            if not src or src.startswith("data:"):
                src = img.get("data-src", "") or img.get("data-original", "") or ""
            if not src:
                continue
            abs_url = urljoin(page_url, src)
            if abs_url in seen_urls:
                continue

            # Check for high-res sources in srcset, picture, or data attributes
            highres_src = self._find_highres_source(img, page_url)
            if highres_src:
                abs_url = highres_src

            if abs_url in seen_urls or not self._is_image_url(abs_url):
                continue
            if self._is_excluded(abs_url):
                continue

            width = self._parse_dim(img.get("width", ""))
            height = self._parse_dim(img.get("height", ""))
            score = self._score_url(abs_url)

            # Accept if dimensions are large enough, or URL suggests high-res
            if (width >= self.min_width and height >= self.min_height) or score >= 2:
                seen_urls.add(abs_url)
                alt = img.get("alt", "") or ""
                title = img.get("title", "") or alt
                images.append(self._make_image(abs_url, "", alt, title, page_url, img, score, width, height))

        # Strategy 3: CSS background images in style attributes
        for elem in soup.find_all(style=True):
            style = elem.get("style", "")
            urls = re.findall(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', style)
            for url in urls:
                if url not in seen_urls and self._is_image_url(url) and not self._is_excluded(url):
                    score = self._score_url(url)
                    if score > 0:
                        seen_urls.add(url)
                        images.append(self._make_image(url, "", "", "", page_url, elem, score))

        # Strategy 4: data-download, data-wallpaper, and other site-specific data attributes
        for attr_name in ["data-download", "data-wallpaper", "data-full-src", "data-image", "data-href"]:
            for elem in soup.find_all(attrs={attr_name: True}):
                url = elem.get(attr_name, "")
                if not url:
                    continue
                abs_url = urljoin(page_url, url)
                if abs_url in seen_urls or not self._is_image_url(abs_url):
                    continue
                if self._is_excluded(abs_url):
                    continue
                seen_urls.add(abs_url)
                score = self._score_url(abs_url) + 2
                images.append(self._make_image(abs_url, "", "", "", page_url, elem, score))

        # Merge with any targeted images found earlier
        if targeted_images:
            images = targeted_images + images

        # Deduplicate: multiple quality/size versions of the same wallpaper
        # can appear on a single page. Keep only the best (highest score/area).
        images = self._deduplicate_images(images)

        # Sort by score (highest first) and return
        images.sort(key=lambda x: x.width * x.height if x.width and x.height else 0, reverse=True)
        logger.info(f"Found {len(images)} potential wallpapers on {page_url}")

        if not images:
            # Debug: log page diagnostics
            total_imgs = len(soup.find_all("img"))
            total_links = len(soup.find_all("a", href=True))
            html_len = len(html)
            title_tag = soup.find("title")
            page_title = title_tag.get_text(strip=True)[:100] if title_tag else "(no title)"
            logger.debug(
                f"Empty result diagnostics for {page_url}: "
                f"html={html_len} chars, imgs={total_imgs}, links={total_links}, "
                f"title='{page_title}'"
            )
            body_text = soup.get_text()[:500].lower()
            if any(w in body_text for w in ["captcha", "challenge", "verify you are human", "access denied", "blocked"]):
                logger.warning(f"Page may be blocked/captcha'd: {page_url}")

        return images

    def _find_detail_page_images(self, soup: BeautifulSoup, page_url: str,
                                  seen_urls: set) -> list[ScrapedImage]:
        """Find wallpaper images using detail-page-specific selectors.

        These selectors target known wallpaper site patterns where the main
        image has a specific ID or class (e.g., img#wallpaper on Wallhaven).
        Uses select() (not select_one) to find ALL matching images — collection
        pages can have many wallpaper images matching the same selector.
        Always tries to find the HIGHEST resolution source available.
        """
        images = []
        for selector in DETAIL_IMAGE_SELECTORS:
            try:
                elements = soup.select(selector)
                if not elements:
                    continue

                for elem in elements:
                    # Could be an img tag or a div with background
                    if elem.name == "img":
                        # Try to get the highest-res source first
                        highres = self._find_highres_source(elem, page_url)
                        src = highres or elem.get("src", "") or elem.get("data-src", "") or elem.get("data-original", "") or ""
                        if src and not src.startswith("data:"):
                            abs_url = urljoin(page_url, src)
                            if abs_url not in seen_urls and not self._is_excluded(abs_url):
                                seen_urls.add(abs_url)
                                alt = elem.get("alt", "") or ""
                                title = elem.get("title", "") or alt
                                width = self._parse_dim(elem.get("width", ""))
                                height = self._parse_dim(elem.get("height", ""))
                                images.append(self._make_image(
                                    abs_url, "", alt, title, page_url, elem,
                                    score=5, width=width, height=height
                                ))
                    else:
                        # Div/element — check background-image
                        style = elem.get("style", "")
                        bg_urls = re.findall(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', style)
                        for url in bg_urls:
                            if url not in seen_urls and not self._is_excluded(url):
                                seen_urls.add(url)
                                images.append(self._make_image(url, "", "", "", page_url, elem, score=5))
            except Exception:
                continue
        return images

    def _find_hero_image(self, soup: BeautifulSoup, page_url: str,
                          seen_urls: set) -> Optional[ScrapedImage]:
        """Find the single main/hero image on a detail page.

        Looks for the largest image by explicit dimensions, container context,
        or URL quality score. Works even when images lack width/height attributes.
        """
        candidates = []
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "") or img.get("data-original", "") or ""
            if not src or src.startswith("data:"):
                src = img.get("data-src", "") or img.get("data-original", "") or ""
            if not src:
                continue

            # Try to get highest-res source
            highres = self._find_highres_source(img, page_url)
            abs_url = urljoin(page_url, highres or src)
            if abs_url in seen_urls or not self._is_image_url(abs_url):
                continue
            if self._is_excluded(abs_url):
                continue

            width = self._parse_dim(img.get("width", ""))
            height = self._parse_dim(img.get("height", ""))
            area = width * height if width and height else 0

            # Check container/wrapper clues
            parent_classes = ""
            parent_ids = ""
            img_classes = " ".join(img.get("class", []))
            img_id = img.get("id", "")
            parent = img.parent
            for _ in range(4):
                if parent and hasattr(parent, 'get'):
                    parent_classes += " " + " ".join(parent.get("class", []))
                    parent_ids += " " + (parent.get("id", "") or "")
                    parent = getattr(parent, 'parent', None)
                else:
                    break

            all_context = (parent_classes + " " + parent_ids + " " + img_classes + " " + img_id).lower()
            is_hero_context = any(k in all_context for k in
                                  ["wallpaper", "hero", "main", "detail", "full", "preview",
                                   "show", "view", "content-image", "single", "primary",
                                   "featured", "cover", "display", "zoom"])

            # Skip images with explicitly small dimensions (icons, logos, etc.)
            if width and height and width < 200 and height < 200:
                continue

            score = self._score_url(abs_url)
            if is_hero_context:
                score += 3
            if area > 0:
                score += 1

            # Accept any non-excluded image as a candidate — let scoring decide
            # (many detail pages have images without explicit dimensions)
            if score >= 1:
                candidates.append((abs_url, img, width, height, area, score))

        if not candidates:
            return None

        # Pick the image with the highest score, breaking ties by area
        candidates.sort(key=lambda x: (x[5], x[4]), reverse=True)
        best_url, best_img, w, h, _, best_score = candidates[0]

        # Accept if: has good dimensions, has hero context, has high URL score,
        # or is one of very few image candidates (typical for detail pages)
        total_images = len(soup.find_all("img"))
        if (w >= self.min_width or h >= self.min_height
                or best_score >= 3
                or total_images <= 10):
            seen_urls.add(best_url)
            alt = best_img.get("alt", "") or ""
            title = best_img.get("title", "") or alt
            return self._make_image(best_url, "", alt, title, page_url, best_img,
                                    score=4, width=w, height=h)
        return None

    def _find_download_links(self, soup: BeautifulSoup, page_url: str,
                              seen_urls: set) -> list[ScrapedImage]:
        """Find wallpaper download links/buttons on detail pages.

        When multiple resolution options exist (e.g., Original, Large, Medium),
        picks only the HIGHEST resolution to avoid downloading thumbnails.
        """
        candidates = []  # (abs_url, text, link_elem, score)
        page_root = self._root_domain(urlparse(page_url).netloc)

        # Collect all download link candidates
        for selector in DOWNLOAD_SELECTORS:
            try:
                links = soup.select(selector)
                for link in links[:10]:
                    href = link.get("href", "")
                    if not href:
                        continue
                    abs_url = urljoin(page_url, href)
                    # Skip links that resolve to the site homepage/root
                    link_path = urlparse(abs_url).path.rstrip("/")
                    if not link_path:
                        continue
                    text = link.get_text(strip=True)
                    if self._is_image_url(abs_url) and not self._is_excluded(abs_url):
                        candidates.append((abs_url, text, link, 6))
                    elif "/download" in abs_url.lower():
                        link_root = self._root_domain(urlparse(abs_url).netloc)
                        if link_root == page_root:
                            candidates.append((abs_url, text, link, 6))
            except Exception:
                continue

        # Also look for links with download-related text
        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True).lower()
            if any(w in text for w in ["download original", "download full", "full size",
                                        "original size", "download wallpaper"]):
                href = link.get("href", "")
                abs_url = urljoin(page_url, href)
                link_path = urlparse(abs_url).path.rstrip("/")
                if not link_path:
                    continue  # Skip homepage links
                link_root = self._root_domain(urlparse(abs_url).netloc)
                if link_root == page_root:
                    candidates.append((abs_url, text, link, 7))

        if not candidates:
            return []

        # Score each candidate by resolution (highest wins)
        scored = []
        for abs_url, text, link_elem, base_score in candidates:
            res_score = self._resolution_score(abs_url, text)
            scored.append((abs_url, text, link_elem, base_score + res_score, res_score))

        # Sort by resolution score (highest first)
        scored.sort(key=lambda x: x[4], reverse=True)

        # Group by base URL (strip dimension parameters) to deduplicate
        # different resolutions of the same image
        best_per_base = {}
        for abs_url, text, link_elem, total_score, res_score in scored:
            base = self._strip_dimension_params(abs_url)
            if base not in best_per_base:
                best_per_base[base] = (abs_url, text, link_elem, total_score)

        # Return only the best candidates (deduplicated)
        images = []
        for base, (abs_url, text, link_elem, score) in best_per_base.items():
            if abs_url not in seen_urls:
                seen_urls.add(abs_url)
                images.append(self._make_image(
                    abs_url, "", "", text, page_url, link_elem, score=score
                ))

        logger.debug(f"Download links: {len(candidates)} candidates → {len(images)} after dedup/best-res")
        return images

    @staticmethod
    def _resolution_score(url: str, text: str) -> int:
        """Score a download link by how likely it is the highest resolution.

        Higher score = higher resolution. Used to pick the best download
        from multiple resolution options (Original > Large > Medium > Small).
        """
        score = 0
        combined = (url + " " + text).lower()

        # Highest priority: explicit "original" or "full size"
        if any(w in combined for w in ["original", "full size", "full_size", "fullsize"]):
            score += 100

        # High priority: very high resolution keywords
        if any(w in combined for w in ["4k", "uhd", "2160", "8k", "5k"]):
            score += 80

        # Medium-high: large resolution indicators
        if any(w in combined for w in ["1440", "2k", "qhd", "wqhd"]):
            score += 60

        # Medium: standard HD
        if "1080" in combined or "fhd" in combined:
            score += 40

        # Extract explicit width from URL params (w=NNNN, width=NNNN)
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            for key in ["w", "width", "dw"]:
                if key in params:
                    w = int(params[key][0])
                    score += w // 10  # e.g., w=1920 → +192
        except (ValueError, IndexError):
            pass

        # Extract explicit dimensions from URL path (e.g., 1920x1080)
        dim_match = re.search(r"(\d{3,5})x(\d{3,5})", url)
        if dim_match:
            w, h = int(dim_match.group(1)), int(dim_match.group(2))
            score += (w * h) // 10000  # e.g., 1920×1080 → +207

        # Penalize known small-size keywords
        if any(w in combined for w in ["small", "tiny", "thumb", "preview", "mini"]):
            score -= 50
        if any(w in combined for w in ["medium", "med "]):
            score -= 20

        # Penalize known preview URL path segments (non-full-res variants)
        if any(p in combined for p in ["/nbig/", "/wallpaper/big/", "/resized/", "/compressed/"]):
            score -= 30

        return score

    @staticmethod
    def _strip_dimension_params(url: str) -> str:
        """Strip dimension/size parameters from URL to identify the base image.

        Used to group different resolutions of the same image together.
        Handles query-parameter dimensions (photo.jpg?w=1920&h=1080),
        path-based dimensions (photo-1920x1080.jpg), AND quality/size path
        segments (/original/, /big/, /preview/, etc.).
        """
        try:
            parsed = urlparse(url)

            # Strip dimension-related query parameters
            params = parse_qs(parsed.query)
            dim_keys = {"w", "h", "width", "height", "dw", "dh", "size", "resize",
                        "dpr", "fit", "crop", "quality", "q", "auto", "cs", "fm"}
            filtered = {k: v for k, v in params.items() if k.lower() not in dim_keys}
            if not filtered:
                new_query = ""
            else:
                from urllib.parse import urlencode
                new_query = urlencode(filtered, doseq=True)

            path = parsed.path
            # Strip dimension patterns from the URL path (e.g., -1920x1080 in filename)
            path = re.sub(r"[_.\-]?\d{3,5}x\d{3,5}", "", path)
            # Strip quality/size path segments so /original/img.jpg and
            # /big/img.jpg produce the same base key
            path = re.sub(
                r"/(original|preview|thumb(?:nail)?|full|big|nbig|large|medium|"
                r"small|hd|[248]k|uhd|hires|lowres|compressed|raw|source|"
                r"web|mobile|desktop|retina)/",
                "/",
                path,
                flags=re.I,
            )

            return parsed._replace(path=path, query=new_query).geturl()
        except Exception:
            return url

    def get_detail_page_links(self, html: str, page_url: str) -> list[dict]:
        """Find links to detail/individual wallpaper pages (not image files).

        On gallery/listing pages, thumbnails are wrapped in <a> tags linking
        to individual pages where full-size images live.

        Uses TWO approaches:
        1. Links containing/near an <img> tag (thumbnail → detail page)
        2. Links with URL patterns that suggest detail pages (/w/xxx, /wallpaper-, etc.)

        Filters out links inside navigation/sidebar/footer sections to avoid
        following category or utility links.
        """
        soup = BeautifulSoup(html, "lxml")
        links = []
        seen = set()
        page_parsed = urlparse(page_url)
        page_root = self._root_domain(page_parsed.netloc)

        # Identify navigation/sidebar/footer regions to deprioritise links
        # found there.  Links inside these are likely category or utility
        # links, not wallpaper detail links.
        _nav_containers = set()
        for sel in ("nav", "header", "footer", ".sidebar", ".side-panel",
                     ".left-panel", ".right-panel", "[role='navigation']",
                     ".menu", ".nav", ".footer", ".header"):
            for el in soup.select(sel):
                _nav_containers.add(id(el))

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            abs_url = urljoin(page_url, href)

            # Skip if it's an image file URL (we want page links, not images)
            if self._is_image_url(abs_url):
                continue

            # Skip external links (different root domain)
            link_parsed = urlparse(abs_url)
            link_root = self._root_domain(link_parsed.netloc)
            if link_root and page_root and link_root != page_root:
                continue

            # Skip if same as current page
            if abs_url.rstrip("/") == page_url.rstrip("/"):
                continue

            if abs_url in seen:
                continue

            # Skip navigation/category/utility pages
            path = link_parsed.path.lower()
            if any(re.search(p, path) for p in NAV_EXCLUDE_PATTERNS):
                continue

            # Try to find associated thumbnail image
            img = link.find("img")
            if not img:
                # Check sibling/parent layouts (Wallhaven's <figure><img/><a/></figure>)
                parent = link.parent
                if parent and parent.name in ("figure", "li", "article", "div", "span"):
                    img = parent.find("img")
                # Try grandparent too (common in nested card layouts)
                if not img and parent:
                    grandparent = getattr(parent, 'parent', None)
                    if grandparent and grandparent.name in ("figure", "li", "article", "div"):
                        classes = " ".join(grandparent.get("class", [])).lower()
                        if any(k in classes for k in ("thumb", "card", "item", "wallpaper",
                                                       "preview", "grid", "pic", "cell",
                                                       "entry", "post", "tile")):
                            img = grandparent.find("img")

            # Check for background-image on the link or its container
            has_bg_thumb = False
            if not img:
                for check_elem in [link, link.parent]:
                    if check_elem and hasattr(check_elem, 'get'):
                        style = check_elem.get("style", "")
                        if "background-image" in style or "background:" in style:
                            has_bg_thumb = True
                            break

            # Get thumbnail info
            thumb_src = ""
            alt = ""
            title = link.get("title", "") or ""
            if img:
                # Try all common lazy-load attributes
                thumb_src = (img.get("data-src", "") or img.get("src", "")
                             or img.get("data-original", "") or img.get("data-lazy-src", "") or "")
                if thumb_src.startswith("data:"):
                    thumb_src = (img.get("data-src", "") or img.get("data-original", "")
                                 or img.get("data-lazy-src", "") or "")
                alt = img.get("alt", "") or ""
                if not title:
                    title = img.get("title", "") or ""

            # Accept link if it has an associated image/background OR has a detail-page URL pattern
            has_img = bool((img and thumb_src) or has_bg_thumb)
            has_detail_pattern = self._looks_like_detail_url(path)

            if not has_img and not has_detail_pattern:
                continue

            # Links without thumbnails that live inside nav/sidebar/footer
            # are almost certainly category or utility links — skip them.
            if not has_img and _nav_containers:
                in_nav = False
                for ancestor in link.parents:
                    if id(ancestor) in _nav_containers:
                        in_nav = True
                        break
                if in_nav:
                    continue

            seen.add(abs_url)
            links.append({
                "url": abs_url,
                "thumbnail_url": urljoin(page_url, thumb_src) if thumb_src else "",
                "alt": alt,
                "title": title,
                "_has_thumb": has_img,  # used for sorting priority
            })

        # Prioritize links with actual thumbnails (real wallpaper entries) over
        # links matched only by URL pattern (may be category/navigation links).
        links.sort(key=lambda l: (not l.get("_has_thumb", False),))
        # Strip internal sorting key
        for l in links:
            l.pop("_has_thumb", None)

        logger.info(f"Found {len(links)} detail page links on {page_url}")
        return links

    @staticmethod
    def _looks_like_detail_url(path: str) -> bool:
        """Check if a URL path looks like a single-wallpaper detail page.

        Matches patterns like: /w/abc123, /wallpaper-name-123,
        /photo/123, /image/123, /long-descriptive-slug.html, etc.

        Rejects known listing/category page patterns.
        """
        path_lower = path.lower()

        # Reject known listing/category patterns before checking detail patterns
        listing_patterns = [
            r"-desktop-wallpapers", r"-desktop-backgrounds",
            r"^\d+x\d+-wallpapers",           # Resolution listings
            r"/top_wallpapers", r"/new_wallpapers", r"/popular_wallpapers",
            r"/hot_wallpapers", r"/best_wallpapers", r"/random_wallpapers",
            r"/page/\d+", r"[?&]page=\d+",
            r"/wallpaper\.html$",              # Landing pages
        ]
        if any(re.search(p, path_lower) for p in listing_patterns):
            return False

        detail_patterns = [
            r"^/w/[a-z0-9]+$",                  # Wallhaven: /w/abc123
            r"wallpaper[s]?[/-]",                 # Generic: /wallpaper/..., /wallpapers/..., -wallpapers.html
            r"/photo/\d+",                         # Photo detail pages
            r"/image/\d+",                         # Image detail pages
            r"/pic/\d+",                           # Pic detail pages
            r"^/[^/]+-\d+\.html$",                # 4KWallpapers: /name-123.html
            r"/download/\d+",                      # Download pages
            r"^/[^/]+/[^/]+-\d+$",                # Category/slug-id pattern
            # Long descriptive slugs ending in .html (HDWallpapers.in pattern)
            r"^/[a-z0-9_-]{20,}\.html$",
            # Path with "hd" or resolution hints (common in wallpaper detail URLs)
            r"_hd[_-]",
            r"_4k[_-]",
            r"_uhd[_-]",
            # Single deep path (not nested like /category/subcategory/page)
            r"^/[^/]+\.html$",
        ]
        return any(re.search(p, path_lower) for p in detail_patterns)

    @staticmethod
    def _root_domain(netloc: str) -> str:
        """Extract root domain from netloc (e.g. 'th.wallhaven.cc' -> 'wallhaven.cc')."""
        parts = netloc.lower().split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return netloc.lower()

    async def get_next_page_url(self, html: str, current_url: str, page_num: int) -> Optional[str]:
        """Find pagination link for next page."""
        soup = BeautifulSoup(html, "lxml")

        # Strategy 1: Look for explicit "next" links
        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True).lower()
            classes = " ".join(link.get("class", []))
            rel = link.get("rel", [])

            if (
                "next" in text
                or "next" in classes
                or "next" in rel
                or "\u00bb" in text
                or "\u203a" in text
                or ">" == text
            ):
                href = link.get("href", "")
                if href:
                    return urljoin(current_url, href)

        # Strategy 2: Look for page number links
        current_page_match = re.search(r"[?&]page=(\d+)", current_url)
        if current_page_match:
            next_num = int(current_page_match.group(1)) + 1
            return re.sub(r"([?&]page=)\d+", f"\\g<1>{next_num}", current_url)

        # Strategy 3: Path-based pagination (e.g., /page/2/)
        path_match = re.search(r"/page/(\d+)", current_url)
        if path_match:
            next_num = int(path_match.group(1)) + 1
            return re.sub(r"/page/\d+", f"/page/{next_num}", current_url)

        return None

    def _is_image_url(self, url: str) -> bool:
        """Check if URL looks like an image file."""
        parsed = urlparse(url.split("?")[0])
        ext = parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
        return f".{ext}" in IMAGE_EXTENSIONS

    def _is_excluded(self, url: str) -> bool:
        """Check if URL matches exclusion patterns."""
        url_lower = url.lower()
        return any(re.search(p, url_lower) for p in EXCLUDE_PATTERNS)

    def _score_url(self, url: str) -> int:
        """Score a URL based on how likely it is to be a high-res wallpaper."""
        score = 1  # Base score for being an image
        url_lower = url.lower()
        for pattern in HIGHRES_PATTERNS:
            if re.search(pattern, url_lower):
                score += 1
        # Penalize very short paths (likely thumbnails)
        path = urlparse(url).path
        if len(path) < 10:
            score -= 1
        # Penalize known preview/non-full-res URL path segments
        if any(p in url_lower for p in ["/nbig/", "/wallpaper/big/", "/resized/",
                                         "/compressed/", "/medium/"]):
            score -= 2
        return max(0, score)

    def _find_highres_source(self, img, page_url: str) -> Optional[str]:
        """Look for higher-resolution versions in srcset, picture, or data attributes."""
        if img.parent and img.parent.name == "picture":
            for source_elem in img.parent.find_all("source"):
                srcset = source_elem.get("srcset", "")
                best = self._parse_srcset(srcset, page_url)
                if best:
                    return best

        srcset = img.get("srcset", "")
        if srcset:
            best = self._parse_srcset(srcset, page_url)
            if best:
                return best

        for attr in ["data-src", "data-original", "data-full", "data-large",
                     "data-zoom", "data-hires", "data-raw-src", "data-2x"]:
            val = img.get(attr, "")
            if val and not val.startswith("data:"):
                return urljoin(page_url, val)

        return None

    def _parse_srcset(self, srcset: str, page_url: str) -> Optional[str]:
        """Parse srcset attribute and return the highest-resolution URL."""
        if not srcset:
            return None
        candidates = []
        for entry in srcset.split(","):
            parts = entry.strip().split()
            if not parts:
                continue
            url = urljoin(page_url, parts[0])
            sort_val = 0
            if len(parts) >= 2:
                descriptor = parts[1]
                if descriptor.endswith("w"):
                    try:
                        sort_val = int(descriptor[:-1])
                    except ValueError:
                        pass
                elif descriptor.endswith("x"):
                    try:
                        sort_val = int(float(descriptor[:-1]) * 1000)
                    except ValueError:
                        pass
            candidates.append((url, sort_val))
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]
        return None

    def _parse_dim(self, val: str) -> int:
        """Parse dimension value from HTML attribute."""
        if not val:
            return 0
        try:
            return int(re.sub(r"[^\d]", "", str(val)))
        except (ValueError, TypeError):
            return 0

    def _make_image(
        self, url: str, thumb: str, alt: str, title: str,
        page_url: str, element=None, score: int = 0,
        width: int = 0, height: int = 0,
    ) -> ScrapedImage:
        """Create a ScrapedImage from parsed data."""
        artist = ""
        artist_link = ""
        tags = ""

        if element is not None:
            parent = element.parent if hasattr(element, 'parent') else None
            for _ in range(3):
                if parent is None:
                    break
                for link in parent.find_all("a", limit=5) if hasattr(parent, 'find_all') else []:
                    href = link.get("href", "") or ""
                    text = link.get_text(strip=True)
                    if any(k in href.lower() for k in ["user", "artist", "profile", "author", "photographer"]):
                        artist = text
                        artist_link = href
                        break
                if artist:
                    break
                parent = getattr(parent, 'parent', None)

            if hasattr(element, 'parent') and element.parent:
                tag_elems = element.parent.find_all("a", class_=re.compile(r"tag", re.I), limit=20)
                if tag_elems:
                    tags = ", ".join(t.get_text(strip=True) for t in tag_elems if t.get_text(strip=True))

        return ScrapedImage(
            url=url,
            thumbnail_url=thumb,
            alt=alt,
            title=title,
            artist=artist,
            artist_link=artist_link,
            tags=tags,
            width=width,
            height=height,
            page_url=page_url,
        )

    def _deduplicate_images(self, images: list[ScrapedImage]) -> list[ScrapedImage]:
        """Deduplicate images that are different quality/size versions of the same wallpaper.

        Groups images by their base filename (stripping size/quality path segments),
        then keeps only the highest-scoring version from each group.
        """
        if len(images) <= 1:
            return images

        groups: dict[str, list[ScrapedImage]] = {}
        for img in images:
            key = self._image_base_key(img.url)
            if key not in groups:
                groups[key] = []
            groups[key].append(img)

        deduped = []
        for key, group in groups.items():
            if len(group) == 1:
                deduped.append(group[0])
            else:
                # Pick the best: prefer largest area, then highest URL score
                best = max(group, key=lambda i: (
                    i.width * i.height if i.width and i.height else 0,
                    self._score_url(i.url),
                ))
                deduped.append(best)
                if len(group) > 1:
                    logger.debug(
                        f"Deduped {len(group)} versions of '{key}' → kept {best.url[:80]}"
                    )
        return deduped

    # Quality/size keywords stripped from filenames during deduplication so that
    # "preview-sunset.jpg" and "original-sunset.jpg" produce the same key.
    _QUALITY_KEYWORD_RE = re.compile(
        r"[_.\-]?"
        r"(original|preview|thumb(?:nail)?|full|big|nbig|large|medium|small|"
        r"hd|[248]k|uhd|[12]k|1080p|hires|hi[_-]?res|lo[_-]?res|lowres|"
        r"compressed|optimized|raw|source|mini|micro|tiny|xl|xxl|"
        r"high[_-]?quality|low[_-]?quality|web|mobile|desktop|retina)"
        r"[_.\-]?",
        re.I,
    )

    @staticmethod
    def _image_base_key(url: str) -> str:
        """Extract a deduplication key from an image URL.

        Strips quality/size path segments, dimensions, AND quality keywords
        so that different versions of the same image produce the same key.
        e.g., /wallpaper/nbig/foo-bar.webp     → foo-bar.webp
              /wallpaper/big/foo-bar.webp      → foo-bar.webp
              /download/foo-1920x1080.jpg      → foo.jpg
              /download/1920x1080-foo.jpg      → foo.jpg
              /img/preview-sunset.jpg          → sunset.jpg
              /img/original-sunset.jpg         → sunset.jpg
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        # Get filename
        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        # Strip dimension patterns like _1920x1080, -800x600, 1920x1080- (at start)
        filename = re.sub(r"[_.\-]?\d{3,5}x\d{3,5}[_.\-]?", "", filename)
        # Strip quality/size keywords (original, preview, full, hd, 4k, etc.)
        filename = GenericAdapter._QUALITY_KEYWORD_RE.sub("", filename)
        # Clean up leftover double separators (e.g., "sunset--beach" → "sunset-beach")
        filename = re.sub(r"[_.\-]{2,}", "-", filename)
        filename = re.sub(r"^[_.\-]+|[_.\-]+(?=\.)", "", filename)
        # Avoid returning empty key (e.g., if filename was just "1920x1080.jpg")
        if not filename or filename == "." or filename.startswith("."):
            filename = path.rsplit("/", 1)[-1] if "/" in path else path
        return filename.lower()

    def discover_categories(self, html: str, page_url: str) -> list[dict]:
        """Find category, tag, and collection navigation links on a page.

        These are gallery-type pages that likely contain more wallpapers.
        Returns list of {"url": str, "label": str, "type": str}.
        """
        soup = BeautifulSoup(html, "lxml")
        categories = []
        seen = set()
        page_parsed = urlparse(page_url)
        page_root = self._root_domain(page_parsed.netloc)

        # Patterns that suggest category/collection/tag navigation
        cat_patterns = [
            (r"/categor(y|ies)/", "category"),
            (r"/tags?/", "tag"),
            (r"/collections?/", "collection"),
            (r"/gallery/", "gallery"),
            (r"/album/", "collection"),
            (r"/topic/", "category"),
            (r"/genre/", "category"),
            (r"/type/", "category"),
            (r"/resolution/", "category"),
        ]

        # Also look for nav elements with wallpaper-related links
        nav_keywords = {"nature", "landscape", "abstract", "anime", "gaming",
                        "space", "city", "dark", "minimal", "car", "animal",
                        "fantasy", "sci-fi", "movie", "art", "photography",
                        "4k", "hd", "uhd", "popular", "trending", "newest",
                        "latest", "top", "best", "random"}

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            abs_url = urljoin(page_url, href)
            link_parsed = urlparse(abs_url)

            # Must be same domain
            link_root = self._root_domain(link_parsed.netloc)
            if link_root != page_root:
                continue

            # Skip image file URLs
            if self._is_image_url(abs_url):
                continue

            if abs_url in seen:
                continue

            path = link_parsed.path.lower()
            text = link.get_text(strip=True).lower()

            # Check against category URL patterns
            for pattern, cat_type in cat_patterns:
                if re.search(pattern, path):
                    # Don't add the listing index itself (e.g., /categories/)
                    if path.rstrip("/").count("/") >= 2:
                        seen.add(abs_url)
                        categories.append({
                            "url": abs_url,
                            "label": link.get_text(strip=True)[:50],
                            "type": cat_type,
                        })
                    break

            # Check for navigation links with wallpaper-related keywords
            if abs_url not in seen and text:
                text_words = set(text.split())
                if text_words & nav_keywords and len(text) < 40:
                    # This looks like a category/topic navigation link
                    seen.add(abs_url)
                    categories.append({
                        "url": abs_url,
                        "label": link.get_text(strip=True)[:50],
                        "type": "category",
                    })

        return categories
