"""Playwright browser manager — stealth mode with human-like behavior.

Key anti-detection features:
- Reuses a single browser tab per site (like a real user clicking links)
- Proper referer chain (gallery → detail page, not always the homepage)
- Waits for full page load + challenge auto-solve
- Human-like scrolling with variable speed
- Stealth JS patches to hide automation fingerprints
"""
import asyncio
import base64
import random
from typing import Optional
from urllib.parse import urlparse, parse_qs, quote_plus, unquote
import httpx
from bs4 import BeautifulSoup
from src.utils.logging import setup_logging

logger = setup_logging("browser")

# Rotate through recent, realistic user agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# Viewport variations to avoid fingerprinting
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 2560, "height": 1440},
]


def jitter(base_ms: int, variance: float = 0.3) -> int:
    """Add random jitter to a millisecond value. variance=0.3 means +/- 30%."""
    low = int(base_ms * (1 - variance))
    high = int(base_ms * (1 + variance))
    return random.randint(max(low, 0), high)


class BrowserManager:
    """Manages a single Playwright browser instance and context with stealth.

    Reuses a single browser tab for same-domain navigation (like a real user
    clicking links) to preserve JS state, cookies, and Cloudflare clearance.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._lock = asyncio.Lock()
        self._initialized = False
        self._stealth_fn = None
        self._current_ua = ""
        # Persistent page for same-domain navigation
        self._persistent_page = None
        self._persistent_domain = None

    async def initialize(self) -> bool:
        """Launch browser with stealth patches. Returns False on failure (non-fatal)."""
        async with self._lock:
            if self._initialized:
                return True
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--no-first-run",
                        "--no-zygote",
                        "--single-process",
                        "--disable-extensions",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )

                ua = random.choice(USER_AGENTS)
                vp = random.choice(VIEWPORTS)
                self._current_ua = ua

                self._context = await self._browser.new_context(
                    viewport=vp,
                    user_agent=ua,
                    locale="en-US",
                    timezone_id="America/New_York",
                    extra_http_headers={
                        "Accept-Language": "en-US,en;q=0.9",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "none",
                        "Sec-Fetch-User": "?1",
                        "Upgrade-Insecure-Requests": "1",
                    },
                )

                # Apply stealth patches: manual JS-level anti-detection
                # These override common headless browser fingerprints
                await self._context.add_init_script("""
                    // Hide webdriver flag
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    // Fake plugins (headless Chrome has 0 plugins)
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5],
                    });
                    // Fake languages
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en'],
                    });
                    // Fix chrome object
                    window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}};
                    // Fix permissions query
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications' ?
                        Promise.resolve({state: Notification.permission}) :
                        originalQuery(parameters)
                    );
                    // Hide automation-related properties
                    delete navigator.__proto__.webdriver;
                """)

                # Also try the stealth library if available (extra patches)
                try:
                    from playwright_stealth import stealth_async
                    self._stealth_fn = stealth_async
                    logger.info("Playwright stealth library also loaded")
                except ImportError:
                    self._stealth_fn = None

                logger.info("Browser stealth patches applied")

                self._initialized = True
                logger.info(f"Browser initialized (UA: {ua[:50]}..., VP: {vp['width']}x{vp['height']})")
                return True
            except Exception as e:
                logger.warning(f"Browser initialization failed: {e}")
                self._initialized = False
                return False

    async def _get_or_create_page(self, url: str) -> tuple:
        """Get a persistent page for same-domain navigation, or create a new one.

        Returns (page, is_reused) — is_reused tells the caller whether this page
        already has state from a previous navigation on the same domain.

        If the browser or context has died, reinitializes the entire browser
        before creating a fresh page.
        """
        if not self._initialized:
            success = await self.initialize()
            if not success:
                raise RuntimeError("Browser not available")

        target_domain = self._root_domain(urlparse(url).netloc)

        # Reuse existing page if same domain (like clicking a link on the same site)
        if (self._persistent_page and self._persistent_domain == target_domain):
            try:
                # Check page is still alive
                await self._persistent_page.evaluate("1")
                return self._persistent_page, True
            except Exception:
                # Page crashed or was closed — create new one
                self._persistent_page = None
                self._persistent_domain = None

        # Close old page if switching domains
        if self._persistent_page:
            try:
                await self._persistent_page.close()
            except Exception:
                pass
            self._persistent_page = None
            self._persistent_domain = None

        # Create new page — if context/browser died, reinitialize first
        try:
            page = await self._context.new_page()
        except Exception as e:
            logger.warning(f"Context/browser dead ({e}), reinitializing browser...")
            await self._reinitialize_browser()
            page = await self._context.new_page()

        if self._stealth_fn:
            await self._stealth_fn(page)

        self._persistent_page = page
        self._persistent_domain = target_domain
        return page, False

    async def _reinitialize_browser(self):
        """Tear down and recreate the browser + context after a crash."""
        self._persistent_page = None
        self._persistent_domain = None
        self._initialized = False

        # Best-effort cleanup of old resources
        for resource in (self._context, self._browser, self._playwright):
            if resource:
                try:
                    await resource.close() if hasattr(resource, 'close') else None
                except Exception:
                    pass
        # Also try stopping playwright (uses __aexit__)
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass

        self._context = None
        self._browser = None
        self._playwright = None

        success = await self.initialize()
        if not success:
            raise RuntimeError("Browser reinitialisation failed")

    async def get_page(self):
        """Get a new page from the browser context with stealth applied.

        For one-off use (e.g., search). Use get_page_content() for site scraping
        which reuses pages per domain.
        """
        if not self._initialized:
            success = await self.initialize()
            if not success:
                raise RuntimeError("Browser not available")
        page = await self._context.new_page()
        if self._stealth_fn:
            await self._stealth_fn(page)
        return page

    async def get_page_content(self, url: str, wait_time: int = 3000,
                               scroll_count: int = 5, scroll_wait_ms: int = 800,
                               referer: str = "") -> str:
        """Navigate to URL and return page HTML content with human-like behavior.

        Reuses the same browser tab for same-domain requests (like a real user
        clicking links). Detects and waits through Cloudflare/CAPTCHA challenges.

        Args:
            url: The URL to navigate to.
            wait_time: Base wait time in ms after page load.
            scroll_count: Number of scroll actions.
            scroll_wait_ms: Base wait between scrolls.
            referer: The referring page URL (e.g., the gallery page).
                     If empty, uses the site's homepage.
        """
        page, is_reused = await self._get_or_create_page(url)

        # Build referer: use provided referer, or site homepage
        if not referer:
            parsed = urlparse(url)
            referer = f"{parsed.scheme}://{parsed.netloc}/"

        for attempt in range(2):
            try:
                # Navigate — use 'load' event to wait for full page rendering
                # (domcontentloaded is too early for JS-heavy sites with challenges)
                await page.goto(url, wait_until="load", timeout=30000, referer=referer)
                await page.wait_for_timeout(jitter(wait_time))

                # Check if we landed on a challenge/captcha page and wait it out
                challenge_detected = await self._wait_through_challenge(page)
                if challenge_detected:
                    # After challenge resolves, wait a bit longer for the real page
                    await page.wait_for_timeout(jitter(1500))

                # Human-like scrolling with variable speed
                for i in range(scroll_count):
                    # Randomize scroll distance (75%-125% of viewport height)
                    vh = page.viewport_size["height"] if page.viewport_size else 900
                    scroll_amount = random.randint(int(vh * 0.75), int(vh * 1.25))
                    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                    await page.wait_for_timeout(jitter(scroll_wait_ms))

                    # Occasional longer pause (simulates reading)
                    if random.random() < 0.2:
                        await page.wait_for_timeout(jitter(1500, 0.5))

                # Scroll back to top
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(jitter(500))
                return await page.content()
            except Exception as e:
                error_str = str(e).lower()
                is_recoverable = any(s in error_str for s in [
                    "err_socket_not_connected", "err_connection_reset",
                    "err_connection_refused", "err_connection_closed",
                    "err_aborted", "err_timed_out",
                    "target closed",
                    "target page", "context or browser",
                    "browser has been closed",
                    "page has been closed",
                    "execution context was destroyed",
                    "session closed",
                    "connection closed",
                ])
                # On recoverable errors, discard page and retry once
                if is_recoverable and attempt == 0:
                    logger.info(f"Browser/page error, recreating page and retrying: {str(e)[:100]}")
                    try:
                        await page.close()
                    except Exception:
                        pass
                    self._persistent_page = None
                    self._persistent_domain = None
                    page, _ = await self._get_or_create_page(url)
                    continue
                # Non-retryable or second attempt — discard and raise
                logger.debug(f"Page navigation error: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                self._persistent_page = None
                self._persistent_domain = None
                raise

    # Strings that indicate a challenge/captcha page (Cloudflare, DDoS-Guard, etc.)
    _CHALLENGE_SIGNALS = [
        "checking your browser",   # Cloudflare classic
        "just a moment",           # Cloudflare
        "verify you are human",    # hCaptcha / Cloudflare Turnstile
        "attention required",      # Cloudflare block page
        "enable javascript and cookies", # DDoS-Guard
        "ddos protection",         # Generic
        "challenge-platform",      # Cloudflare Turnstile iframe
        "cf-browser-verification", # Cloudflare old
        "_cf_chl",                 # Cloudflare challenge parameter
        "access denied",           # WAF block
        "ray id",                  # Cloudflare error pages
    ]

    async def _wait_through_challenge(self, page, max_wait_s: int = 15) -> bool:
        """Detect Cloudflare/CAPTCHA challenge pages and wait for auto-solve.

        Many challenge pages auto-solve via JS within 5-10 seconds. We check the
        page content for challenge signals and wait for them to disappear (indicating
        the real page loaded). Returns True if a challenge was detected and resolved.
        """
        try:
            body_text = await page.evaluate("document.body?.innerText?.substring(0, 1500)?.toLowerCase() || ''")
            page_title = await page.evaluate("document.title?.toLowerCase() || ''")
            combined = body_text + " " + page_title

            is_challenge = any(signal in combined for signal in self._CHALLENGE_SIGNALS)
            if not is_challenge:
                return False

            logger.info(f"Challenge page detected on {page.url}, waiting for auto-solve...")

            # Wait for the page to change (challenge auto-solves → redirect or content swap)
            waited = 0
            check_interval = 2000  # ms
            while waited < max_wait_s * 1000:
                await page.wait_for_timeout(check_interval)
                waited += check_interval

                # Re-check if challenge is still present
                try:
                    body_text = await page.evaluate("document.body?.innerText?.substring(0, 1500)?.toLowerCase() || ''")
                    page_title = await page.evaluate("document.title?.toLowerCase() || ''")
                    combined = body_text + " " + page_title

                    still_challenge = any(signal in combined for signal in self._CHALLENGE_SIGNALS)
                    if not still_challenge:
                        logger.info(f"Challenge resolved after {waited/1000:.1f}s on {page.url}")
                        # Give the real page a moment to finish rendering
                        await page.wait_for_timeout(jitter(2000))
                        return True
                except Exception:
                    # Page might be navigating — wait and retry
                    continue

            logger.warning(f"Challenge did not auto-resolve after {max_wait_s}s on {page.url}")
            return True  # Still a challenge, but we tried
        except Exception as e:
            logger.debug(f"Challenge detection error: {e}")
            return False

    async def release_site_page(self):
        """Close the persistent page for the current site.

        Call this when done scraping a site to free resources.
        """
        if self._persistent_page:
            try:
                await self._persistent_page.close()
            except Exception:
                pass
            self._persistent_page = None
            self._persistent_domain = None

    @staticmethod
    def _root_domain(netloc: str) -> str:
        """Extract root domain from netloc (e.g. 'th.wallhaven.cc' -> 'wallhaven.cc')."""
        parts = netloc.lower().split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return netloc.lower()

    async def web_search(self, query: str) -> list[str]:
        """Search the web for URLs matching a query.

        Tries multiple approaches:
        1. httpx-based searches (DDG Lite, DDG HTML, Bing, Google)
        2. If all httpx searches fail (common in Docker), falls back to
           Playwright browser-based DDG search which handles JS/captchas.
        """
        headers = {
            "User-Agent": self._current_ua or USER_AGENTS[0],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        # Try DuckDuckGo Lite (simplest HTML, hardest to block)
        urls = await self._search_ddg_lite(query, headers)
        if urls:
            return urls

        # Try DuckDuckGo HTML version
        urls = await self._search_ddg_http(query, headers)
        if urls:
            return urls

        # Fallback to Bing
        logger.info(f"DDG returned 0 results for '{query}', trying Bing")
        urls = await self._search_bing_http(query, headers)
        if urls:
            return urls

        # Try Google as last resort
        urls = await self._search_google_http(query, headers)
        if urls:
            return urls

        # All httpx-based searches failed — try Playwright browser-based search
        # (Docker containers often get blocked by search engines via httpx,
        # but a full browser with stealth patches can get through)
        logger.info(f"All httpx searches returned 0 for '{query}', trying Playwright browser search")
        urls = await self._search_ddg_browser(query)
        if urls:
            return urls

        logger.warning(f"All search engines returned 0 results for '{query}'")
        return []

    async def _search_ddg_lite(self, query: str, headers: dict) -> list[str]:
        """Search DuckDuckGo Lite — minimal HTML, most resilient."""
        urls = []
        try:
            async with httpx.AsyncClient(
                headers=headers, follow_redirects=True, timeout=20
            ) as client:
                resp = await client.post(
                    "https://lite.duckduckgo.com/lite/",
                    data={"q": query},
                )

            if resp.status_code != 200:
                logger.warning(f"DDG Lite returned status {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "lxml")
            title = soup.find("title")
            title_text = title.get_text(strip=True) if title else ""
            logger.debug(f"DDG Lite response: {len(resp.text)} chars, title='{title_text[:80]}'")

            # DDG Lite uses simple table layout with result links
            result_links = soup.select("a.result-link")
            if not result_links:
                # Also try result__a (shared with HTML version)
                result_links = soup.select("a.result__a")
            if not result_links:
                # Generic: links inside result table rows
                result_links = []
                for td in soup.select("td"):
                    a_tag = td.find("a", href=True)
                    if a_tag:
                        href = a_tag.get("href", "")
                        if href.startswith("http") and "duckduckgo" not in href:
                            result_links.append(a_tag)

            urls = self._filter_search_urls(result_links, ddg=True)
            logger.info(f"DDG Lite search '{query}': {len(urls)} URLs from {len(result_links)} links")

        except Exception as e:
            logger.warning(f"DDG Lite search failed for '{query}': {e}")
        return urls

    async def _search_ddg_http(self, query: str, headers: dict) -> list[str]:
        """Search DuckDuckGo HTML version via httpx + BeautifulSoup."""
        urls = []
        try:
            async with httpx.AsyncClient(
                headers=headers, follow_redirects=True, timeout=20
            ) as client:
                resp = await client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query, "b": ""},
                )

            if resp.status_code != 200:
                logger.warning(f"DDG HTML returned status {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "lxml")
            title = soup.find("title")
            title_text = title.get_text(strip=True) if title else ""
            logger.debug(f"DDG HTML response: {len(resp.text)} chars, title='{title_text[:80]}'")

            # DDG HTML results: <a class="result__a" href="//duckduckgo.com/l/?uddg=...">
            result_links = soup.select("a.result__a")
            if not result_links:
                result_links = soup.select(".result__body a[href]")
            if not result_links:
                result_links = soup.select("a.result-link")

            urls = self._filter_search_urls(result_links, ddg=True)
            logger.info(f"DDG HTML search '{query}': {len(urls)} URLs from {len(result_links)} links")

            if len(urls) == 0:
                # Log diagnostics
                all_links = soup.find_all("a", href=True)
                forms = soup.find_all("form")
                logger.debug(
                    f"DDG HTML diagnostics: total_links={len(all_links)}, "
                    f"forms={len(forms)}, body_len={len(resp.text)}"
                )

        except Exception as e:
            logger.warning(f"DDG HTML search failed for '{query}': {e}")
        return urls

    async def _search_bing_http(self, query: str, headers: dict) -> list[str]:
        """Search Bing via httpx + BeautifulSoup."""
        urls = []
        try:
            search_url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en&cc=us"
            async with httpx.AsyncClient(
                headers=headers, follow_redirects=True, timeout=20
            ) as client:
                resp = await client.get(search_url)

            if resp.status_code != 200:
                logger.warning(f"Bing HTTP returned status {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "lxml")
            title = soup.find("title")
            title_text = title.get_text(strip=True) if title else ""
            logger.debug(f"Bing response: {len(resp.text)} chars, title='{title_text[:80]}'")

            # Bing organic results: <li class="b_algo"><h2><a href="...">
            result_links = []
            for li in soup.select("li.b_algo"):
                a_tag = li.select_one("h2 a[href]")
                if a_tag:
                    result_links.append(a_tag)

            if not result_links:
                # Broader fallback — any h2 link
                result_links = soup.select("h2 a[href^='http']")

            if not result_links:
                # Even broader — look for <cite> URLs and nearby links
                for cite in soup.select("cite"):
                    parent = cite.parent
                    if parent:
                        a_tag = parent.find("a", href=True)
                        if a_tag:
                            result_links.append(a_tag)

            bing_skip = {
                "bing.com", "microsoft.com", "msn.com", "live.com",
                "microsoftonline.com", "office.com",
            }
            urls = self._filter_search_urls(result_links, extra_skip=bing_skip)
            logger.info(f"Bing HTTP search '{query}': {len(urls)} URLs from {len(result_links)} results")

            if len(urls) == 0:
                all_links = soup.find_all("a", href=True)
                logger.debug(f"Bing diagnostics: total_links={len(all_links)}, body_len={len(resp.text)}")

        except Exception as e:
            logger.warning(f"Bing HTTP search failed for '{query}': {e}")
        return urls

    async def _search_google_http(self, query: str, headers: dict) -> list[str]:
        """Search Google as last resort via httpx + BeautifulSoup."""
        urls = []
        try:
            search_url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en"
            google_headers = dict(headers)
            google_headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            async with httpx.AsyncClient(
                headers=google_headers, follow_redirects=True, timeout=20
            ) as client:
                resp = await client.get(search_url)

            if resp.status_code != 200:
                logger.debug(f"Google returned status {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "lxml")

            # Google wraps results in <div class="g"> or various containers
            result_links = []
            # Try multiple selector strategies
            for selector in ["div.g a[href^='http']", "div.tF2Cxc a[href^='http']",
                             "div[data-hveid] a[href^='http']", "h3 a[href^='http']"]:
                result_links = soup.select(selector)
                if result_links:
                    break

            if not result_links:
                # Broad: any link starting with /url?q= (Google redirect)
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag.get("href", "")
                    if href.startswith("/url?q="):
                        params = parse_qs(urlparse(href).query)
                        if "q" in params:
                            real_url = params["q"][0]
                            if real_url.startswith("http"):
                                a_tag["href"] = real_url  # Replace for filter
                                result_links.append(a_tag)

            google_skip = {"google.com", "googleapis.com", "gstatic.com", "google.co"}
            urls = self._filter_search_urls(result_links, extra_skip=google_skip)
            logger.info(f"Google HTTP search '{query}': {len(urls)} URLs")

        except Exception as e:
            logger.debug(f"Google search failed for '{query}': {e}")
        return urls

    async def _search_ddg_browser(self, query: str) -> list[str]:
        """Search DuckDuckGo using Playwright browser (stealth mode).

        Uses the HTML-only version at html.duckduckgo.com/html/ which has
        stable selectors and works server-side. Playwright handles any JS
        challenges that block httpx requests from Docker containers.
        """
        urls = []
        page = None
        try:
            page = await self.get_page()

            # Navigate to DDG HTML search
            await page.goto("https://html.duckduckgo.com/html/",
                            wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(jitter(1500))

            # Type query with human-like delays
            search_input = await page.query_selector('input[name="q"]')
            if search_input:
                await search_input.click()
                await page.wait_for_timeout(jitter(300))
                # Type each character with random delay (human-like)
                for char in query:
                    await search_input.type(char, delay=random.randint(50, 150))
                await page.wait_for_timeout(jitter(500))
                await page.keyboard.press("Enter")
            else:
                # Fallback: navigate directly with query in URL
                search_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

            # Wait for results to load
            await page.wait_for_timeout(jitter(3000))

            # Extract results from the page
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            # DDG HTML version selectors (try multiple for resilience)
            result_links = soup.select("a.result__a")
            if not result_links:
                result_links = soup.select("a.result-link")
            if not result_links:
                # Fallback: links inside result containers
                result_links = []
                for div in soup.select(".result, .web-result, .links_main"):
                    a_tag = div.find("a", href=True)
                    if a_tag:
                        href = a_tag.get("href", "")
                        if href.startswith("http") or "uddg=" in href:
                            result_links.append(a_tag)
            if not result_links:
                # Broadest fallback: any link that looks like a search result
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag.get("href", "")
                    if "uddg=" in href:
                        result_links.append(a_tag)

            ddg_skip = {"duckduckgo.com", "html.duckduckgo.com", "lite.duckduckgo.com"}
            urls = self._filter_search_urls(result_links, ddg=True, extra_skip=ddg_skip)
            logger.info(f"DDG browser search '{query}': {len(urls)} URLs from {len(result_links)} links")

            if len(urls) == 0:
                # Log diagnostics
                all_links = soup.find_all("a", href=True)
                page_title = soup.find("title")
                title_text = page_title.get_text(strip=True)[:80] if page_title else "(no title)"
                body_len = len(html)
                logger.debug(
                    f"DDG browser diagnostics: total_links={len(all_links)}, "
                    f"body_len={body_len}, title='{title_text}'"
                )

        except Exception as e:
            logger.warning(f"DDG browser search failed for '{query}': {e}")
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
        return urls

    def _filter_search_urls(self, links, ddg: bool = False, extra_skip: set = None) -> list[str]:
        """Filter and deduplicate search result URLs."""
        urls = []
        skip_domains = {
            "facebook.com", "twitter.com", "x.com",
            "youtube.com", "instagram.com", "linkedin.com", "tiktok.com",
            "reddit.com", "wikipedia.org", "amazon.com",
        }
        if extra_skip:
            skip_domains |= extra_skip

        for link in links[:30]:
            href = link.get("href", "")

            # DDG wraps URLs: //duckduckgo.com/l/?uddg=ENCODED_URL&rut=...
            if ddg and "duckduckgo.com" in href and "uddg=" in href:
                parsed = urlparse(href)
                params = parse_qs(parsed.query)
                if "uddg" in params:
                    href = unquote(params["uddg"][0])

            # Bing wraps URLs: bing.com/ck/a?...&u=a1BASE64_URL&ntb=1
            if "bing.com/ck/a" in href:
                href = self._decode_bing_redirect(href) or href

            # Google wraps URLs: /url?q=REAL_URL
            if href.startswith("/url?q="):
                parsed = urlparse(href)
                params = parse_qs(parsed.query)
                if "q" in params and params["q"][0].startswith("http"):
                    href = params["q"][0]

            if href.startswith("//"):
                href = "https:" + href
            elif not href.startswith("http"):
                continue

            try:
                domain = urlparse(href).netloc.lower()
                # Skip search engine internal links
                if ddg and "duckduckgo.com" in domain:
                    continue
                if "bing.com" in domain or "microsoft.com" in domain:
                    continue
                if "google.com" in domain or "googleapis.com" in domain:
                    continue
                if not any(skip in domain for skip in skip_domains):
                    if href not in urls:
                        urls.append(href)
            except Exception:
                pass

            if len(urls) >= 15:
                break

        return urls

    @staticmethod
    def _decode_bing_redirect(url: str) -> str | None:
        """Decode Bing click-tracking redirect URL to get the real destination.

        Bing wraps result URLs as: bing.com/ck/a?...&u=a1BASE64URL&ntb=1
        The 'u' parameter is 'a1' prefix + base64url-encoded real URL.
        """
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if "u" in params:
                u_val = params["u"][0]
                if u_val.startswith("a1"):
                    encoded = u_val[2:]
                    # Add padding if needed
                    padding = 4 - len(encoded) % 4
                    if padding != 4:
                        encoded += "=" * padding
                    decoded = base64.urlsafe_b64decode(encoded).decode("utf-8")
                    if decoded.startswith("http"):
                        return decoded
        except Exception:
            pass
        return None

    # Keep old name for backward compatibility
    async def search_duckduckgo(self, query: str) -> list[str]:
        """Search the web. Delegates to web_search() with multi-engine fallback."""
        return await self.web_search(query)

    @property
    def is_available(self) -> bool:
        return self._initialized

    @property
    def current_user_agent(self) -> str:
        """Current user agent string, shared with downloader for consistency."""
        return self._current_ua or USER_AGENTS[0]

    async def close(self):
        """Clean up browser resources."""
        try:
            if self._persistent_page:
                await self._persistent_page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.warning(f"Browser cleanup error: {e}")
        finally:
            self._initialized = False
            self._stealth_fn = None
            self._current_ua = ""
            self._playwright = None
            self._browser = None
            self._context = None
            self._persistent_page = None
            self._persistent_domain = None


# Singleton
browser_manager = BrowserManager()
