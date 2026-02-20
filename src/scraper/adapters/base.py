"""Base adapter interface for wallpaper scraping."""
from abc import ABC, abstractmethod
from typing import Optional


class ScrapedImage:
    """Represents a scraped image with its metadata."""

    def __init__(
        self,
        url: str,
        thumbnail_url: str = "",
        alt: str = "",
        title: str = "",
        artist: str = "",
        artist_link: str = "",
        tags: str = "",
        width: int = 0,
        height: int = 0,
        page_url: str = "",
    ):
        self.url = url
        self.thumbnail_url = thumbnail_url
        self.alt = alt
        self.title = title
        self.artist = artist
        self.artist_link = artist_link
        self.tags = tags
        self.width = width
        self.height = height
        self.page_url = page_url

    def __repr__(self):
        return f"ScrapedImage(url={self.url[:60]}...)"


class BaseAdapter(ABC):
    """Base class for wallpaper site adapters."""

    @abstractmethod
    async def scrape(self, html: str, page_url: str) -> list[ScrapedImage]:
        """Parse HTML and return list of scraped images."""
        ...

    @abstractmethod
    async def get_next_page_url(self, html: str, current_url: str, page_num: int) -> Optional[str]:
        """Return URL for next page, or None if no more pages."""
        ...
