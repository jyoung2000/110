"""Extract metadata from HTML elements for wallpaper images."""
from typing import Optional
from bs4 import Tag


def extract_image_metadata(img_tag: Tag, page_url: str = "") -> dict:
    """Extract metadata from an img tag or link element."""
    metadata = {
        "alt": "",
        "title": "",
        "artist": "",
        "artist_link": "",
        "tags": "",
    }

    if img_tag.name == "img":
        metadata["alt"] = img_tag.get("alt", "") or ""
        metadata["title"] = img_tag.get("title", "") or img_tag.get("alt", "") or ""

    # Check parent elements for artist/source info
    parent = img_tag.parent
    for _ in range(5):
        if parent is None:
            break
        # Look for links that might be artist attribution
        for link in parent.find_all("a", limit=5):
            href = link.get("href", "") or ""
            text = link.get_text(strip=True)
            if any(k in href.lower() for k in ["user", "artist", "profile", "author", "photographer"]):
                metadata["artist"] = text
                metadata["artist_link"] = href
                break
        if metadata["artist"]:
            break
        parent = parent.parent

    return metadata
