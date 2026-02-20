"""NSFW/adult content filter for the wallpaper scraper.

Detects content inappropriate for young teens via URL patterns, HTML meta tags,
page text, and image tags/metadata. Covers:
- Sexual/nudity content
- Gore/violence/disturbing imagery
- Drug/substance imagery
- Other age-restricted content

Used to skip inappropriate images when allow_nsfw is False (the default).
"""
import re
from urllib.parse import urlparse
from src.utils.logging import setup_logging

logger = setup_logging("nsfw_filter")

# ---------------------------------------------------------------------------
# URL path/query keywords that strongly indicate inappropriate content
# ---------------------------------------------------------------------------
NSFW_URL_KEYWORDS = [
    # Sexual / nudity
    "nsfw", "adult", "xxx", "porn", "hentai", "nude", "naked",
    "erotic", "sexy", "lewd", "ecchi", "r18", "r-18",
    "explicit", "mature-content", "not-safe-for-work",
    "topless", "lingerie", "bondage", "fetish", "bdsm",
    "playboy", "onlyfans", "fansly", "rule34", "ahegao",
    "uncensored", "stripclub", "camgirl", "boobs", "ass",
    "bikini-babe", "pin-up", "pinup",
    # Gore / violence / disturbing
    "gore", "guro", "brutal", "mutilation", "torture",
    "snuff", "dismember", "decapitat", "bloodbath",
    "graphic-violence", "graphic_violence",
    "crime-scene", "crime_scene", "autopsy",
    "self-harm", "self_harm", "suicide",
    # Drug / substance
    "drug-use", "drug_use", "cocaine", "heroin", "meth",
    "crack-pipe", "bong-hit",
    # Generic age-gate paths
    "/nsfw/", "/adult/", "/18+/", "/xxx/", "/r18/",
    "/gore/", "/mature/", "/explicit/",
]

# ---------------------------------------------------------------------------
# Words in page text, titles, or tags that indicate inappropriate content
# ---------------------------------------------------------------------------
NSFW_TEXT_KEYWORDS = [
    # Sexual / nudity
    "nsfw", "nude", "naked", "porn", "hentai", "xxx",
    "erotic", "topless", "explicit content", "adult content",
    "18+", "r18", "r-18", "lewd", "ecchi",
    "not safe for work", "sexually explicit",
    "nudity", "sexual", "provocative",
    "bondage", "fetish", "bdsm", "dominatrix",
    "stripclub", "strip club", "burlesque",
    "rule34", "rule 34", "ahegao",
    "playboy", "playmate", "centerfold",
    "uncensored", "risque", "risqué",
    # Gore / violence / disturbing
    "gore", "guro", "gory", "graphic violence",
    "mutilation", "dismemberment", "decapitation",
    "torture", "brutal killing", "bloodbath",
    "crime scene", "autopsy photo",
    "self-harm", "self harm", "suicide method",
    "animal cruelty", "animal abuse",
    "graphic injury", "graphic death",
    "snuff", "death photo", "dead body",
    # Drug / substance depictions
    "drug use", "drug abuse", "cocaine", "heroin",
    "methamphetamine", "crack pipe", "shooting up",
    "drug paraphernalia",
    # Wallhaven-specific purity flags
    "sketchy", "purity:nsfw", "purity:sketchy",
]

# ---------------------------------------------------------------------------
# Tag keywords — matched against image tags/categories specifically
# These can be shorter/more aggressive since tags are structured metadata
# ---------------------------------------------------------------------------
NSFW_TAG_KEYWORDS = [
    # Sexual
    "nsfw", "nude", "naked", "hentai", "ecchi", "lewd",
    "erotic", "topless", "bikini babe", "sexy",
    "porn", "xxx", "adult", "bondage", "fetish", "bdsm",
    "rule34", "rule 34", "ahegao", "lingerie model",
    "playboy", "pin-up", "pinup", "provocative",
    "nudity", "sexual", "uncensored", "r18",
    "ass", "boobs", "butt", "cleavage",
    "suggestive", "risque", "sensual",
    # Gore / violence
    "gore", "guro", "gory", "blood", "bloody",
    "violence", "violent", "brutal", "torture",
    "mutilation", "dismemberment", "death",
    "horror gore", "graphic", "disturbing",
    "crime scene", "corpse", "dead body",
    "self-harm", "self harm", "suicide",
    "animal cruelty",
    # Drugs
    "drugs", "drug use", "cocaine", "heroin", "weed",
    "marijuana", "cannabis", "smoking weed", "bong",
    # Wallhaven purity
    "sketchy",
]

# ---------------------------------------------------------------------------
# HTML meta tag values indicating adult/NSFW ratings
# ---------------------------------------------------------------------------
NSFW_META_RATINGS = [
    "adult", "mature", "rta-5042-1996-1400-1577-",
    "restricted", "18+", "x-rated",
]

# ---------------------------------------------------------------------------
# Known NSFW domains — if the page or image is hosted here, skip it
# ---------------------------------------------------------------------------
NSFW_DOMAINS = [
    "rule34.xxx", "rule34.paheal.net", "e-hentai.org", "nhentai.net",
    "gelbooru.com", "danbooru.donmai.us", "sankaku", "yande.re",
    "konachan.com", "chan.sankakucomplex.com",
    "pornhub.com", "xvideos.com", "xhamster.com", "redtube.com",
    "hentaifoundry.com", "literotica.com",
    "bestgore.com", "theync.com", "liveleak.com",
    "efukt.com", "motherless.com",
    "onlyfans.com", "fansly.com",
]

# ---------------------------------------------------------------------------
# Compiled patterns for efficiency
# ---------------------------------------------------------------------------

# URL pattern: match whole words or known path segments
_URL_PATTERN = re.compile(
    r"(?:\b|/)(" + "|".join(re.escape(k) for k in NSFW_URL_KEYWORDS) + r")(?:\b|/)",
    re.IGNORECASE,
)

# Text pattern: match whole words in page text/titles
_TEXT_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in NSFW_TEXT_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Tag pattern: more aggressive matching for structured tag metadata
_TAG_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in NSFW_TAG_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Domain set for fast lookup
_NSFW_DOMAINS = {d.lower() for d in NSFW_DOMAINS}


def _is_nsfw_domain(url: str) -> bool:
    """Check if a URL belongs to a known NSFW domain."""
    try:
        netloc = urlparse(url).netloc.lower()
        # Check exact match and parent domain match
        for domain in _NSFW_DOMAINS:
            if netloc == domain or netloc.endswith("." + domain):
                return True
    except Exception:
        pass
    return False


def is_nsfw_url(url: str) -> bool:
    """Check if a URL contains NSFW indicators (keywords or known domains)."""
    if _is_nsfw_domain(url):
        return True
    return bool(_URL_PATTERN.search(url))


def is_nsfw_page(html: str, page_url: str) -> bool:
    """Check if a page's HTML contains NSFW indicators.

    Checks: known domains, meta tags (rating, classification), page title,
    and body text keywords.
    """
    if not html:
        return False

    # Check URL / domain first (fast)
    if is_nsfw_url(page_url):
        return True

    html_lower = html[:15000].lower()  # Check first 15KB for speed

    # Check meta tags for adult ratings
    # <meta name="rating" content="adult">
    meta_patterns = [
        r'<meta\s+[^>]*name\s*=\s*["\']rating["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
        r'<meta\s+[^>]*content\s*=\s*["\']([^"\']+)["\'][^>]*name\s*=\s*["\']rating["\']',
        r'<meta\s+[^>]*property\s*=\s*["\']rating["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
    ]
    for pattern in meta_patterns:
        match = re.search(pattern, html_lower)
        if match:
            rating = match.group(1).strip().lower()
            if any(r in rating for r in NSFW_META_RATINGS):
                logger.debug(f"NSFW meta rating detected: {rating} on {page_url}")
                return True

    # Check page title
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html_lower)
    if title_match:
        title = title_match.group(1)
        if _TEXT_PATTERN.search(title):
            logger.debug(f"NSFW keyword in page title on {page_url}")
            return True

    # Check for age-gate / NSFW interstitials common on wallpaper sites
    age_gate_patterns = [
        r"you must be (18|21)\+?\s*(or older|years)",
        r"(are you|confirm).{0,20}(18|21|legal age|of age)",
        r"age[\s-]?verif",
        r"content[\s-]?warning",
        r"viewer discretion",
        r"this (page|content|section) (contains|is|has).{0,30}(adult|mature|explicit|nsfw)",
        r"(adult|mature|explicit)\s+content\s+(warning|ahead|notice)",
    ]
    for pattern in age_gate_patterns:
        if re.search(pattern, html_lower):
            logger.debug(f"Age-gate / content warning detected on {page_url}")
            return True

    # Check Wallhaven purity indicators in page HTML
    # Wallhaven marks images with data-purity="nsfw" or data-purity="sketchy"
    if 'data-purity="nsfw"' in html_lower or "data-purity='nsfw'" in html_lower:
        logger.debug(f"Wallhaven NSFW purity tag on {page_url}")
        return True
    if 'purity=110' in html_lower or 'purity=010' in html_lower:
        # Wallhaven URL params: purity=100 (SFW), 010 (sketchy), 001 (NSFW), 110 (SFW+sketchy)
        logger.debug(f"Wallhaven NSFW purity param on {page_url}")
        return True

    return False


def is_nsfw_image(url: str, alt: str = "", title: str = "",
                  tags: str = "", page_url: str = "") -> bool:
    """Check if an image's metadata suggests NSFW/inappropriate content.

    Checks the image URL, alt text, title, tags, and source page URL.
    Tags are checked more aggressively since they are structured metadata.
    """
    # Check image URL and domain
    if is_nsfw_url(url):
        return True

    # Check source page URL / domain
    if page_url and is_nsfw_url(page_url):
        return True

    # Check tags with the more aggressive tag pattern
    if tags and _TAG_PATTERN.search(tags):
        return True

    # Check alt text and title with the standard text pattern
    combined_text = f"{alt} {title}".strip()
    if combined_text and _TEXT_PATTERN.search(combined_text):
        return True

    return False
