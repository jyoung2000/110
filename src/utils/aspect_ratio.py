"""Aspect ratio utilities for wallpaper classification."""
from math import gcd

# Known aspect ratios as (w, h) tuples and their labels
KNOWN_RATIOS = [
    (16, 9, "16:9"),
    (16, 10, "16:10"),
    (8, 5, "16:10"),    # 8:5 is equivalent to 16:10 (e.g. 1920x1200)
    (4, 3, "4:3"),
    (21, 9, "21:9"),
    (32, 9, "32:9"),
    (3, 2, "3:2"),
    (5, 4, "5:4"),
    (9, 16, "9:16"),
    (9, 19, "9:19"),
    (9, 20, "9:20"),
    (1, 1, "1:1"),
]


def calculate_aspect_ratio(width: int, height: int) -> str:
    """Return simplified aspect ratio string like '16:9'.

    Uses approximate matching (~2% tolerance) so that non-standard
    resolutions like 901x1600 map to the nearest known ratio (9:16).
    """
    if width <= 0 or height <= 0:
        return "unknown"

    # Try exact GCD match first
    divisor = gcd(width, height)
    w = width // divisor
    h = height // divisor
    exact = {(r[0], r[1]): r[2] for r in KNOWN_RATIOS}
    if (w, h) in exact:
        return exact[(w, h)]

    # Approximate match — compare actual ratio to known ratios within 2% tolerance
    actual = width / height
    best_label = None
    best_diff = float("inf")
    for rw, rh, label in KNOWN_RATIOS:
        known = rw / rh
        diff = abs(actual - known) / known
        if diff < best_diff:
            best_diff = diff
            best_label = label

    if best_diff < 0.02:  # Within 2%
        return best_label

    return f"{w}:{h}"


def is_mobile(width: int, height: int) -> bool:
    """Return True if dimensions suggest a mobile wallpaper (portrait, height > width)."""
    return height > width


def classify_resolution(width: int, height: int) -> str:
    """Classify resolution into human-readable category."""
    pixels = width * height
    if pixels >= 3840 * 2160:
        return "4K+"
    elif pixels >= 2560 * 1440:
        return "1440p"
    elif pixels >= 1920 * 1080:
        return "1080p"
    elif pixels >= 1280 * 720:
        return "720p"
    else:
        return "SD"
