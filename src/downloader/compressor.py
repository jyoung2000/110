"""JPEG compression and thumbnail generation."""
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image
from src.utils.paths import data_path
from src.utils.logging import setup_logging

logger = setup_logging("compressor")

TEMP_DIR = data_path("temp")
THUMBNAIL_DIR = data_path("thumbnails")


class ImageCompressor:
    """Compress images to JPEG and generate thumbnails."""

    def __init__(self, quality: int = 85, thumbnail_size: int = 400, thumbnail_quality: int = 80):
        self.quality = quality
        self.thumbnail_size = thumbnail_size
        self.thumbnail_quality = thumbnail_quality

    def compress(self, input_path: Path, site_name: str = "unknown") -> Optional[Tuple[Path, str, int, int, int]]:
        """Compress image to JPEG. Returns (output_path, img_hash, width, height, file_size_kb) or None."""
        try:
            img = Image.open(input_path)
            width, height = img.size

            # Convert to RGB if needed
            if img.mode in ("RGBA", "P", "LA"):
                background = Image.new("RGB", img.size, (0, 0, 0))
                if img.mode == "P":
                    img = img.convert("RGBA")
                if img.mode in ("RGBA", "LA"):
                    background.paste(img, mask=img.split()[-1])
                    img = background
                else:
                    img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Generate hash from image content
            img_hash = self._hash_image(img)

            # Save compressed JPEG
            safe_site = "".join(c if c.isalnum() else "_" for c in site_name)[:20]
            output_name = f"{safe_site}_{img_hash}_{width}x{height}.jpg"
            output_path = TEMP_DIR / output_name

            img.save(output_path, "JPEG", quality=self.quality, optimize=True, subsampling=0)
            file_size_kb = output_path.stat().st_size // 1024

            logger.debug(f"Compressed {input_path.name} -> {output_name} ({file_size_kb} KB)")
            return output_path, img_hash, width, height, file_size_kb

        except Exception as e:
            logger.error(f"Compression failed for {input_path}: {e}")
            return None

    def generate_thumbnail(self, input_path: Path, img_hash: str) -> Optional[str]:
        """Generate a thumbnail (default 400px). Returns relative path or None."""
        try:
            THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
            thumb_path = THUMBNAIL_DIR / f"{img_hash}.jpg"

            if thumb_path.exists():
                return f"thumbnails/{img_hash}.jpg"

            img = Image.open(input_path)
            img.thumbnail((self.thumbnail_size, self.thumbnail_size), Image.LANCZOS)

            if img.mode != "RGB":
                img = img.convert("RGB")

            img.save(thumb_path, "JPEG", quality=self.thumbnail_quality, optimize=True)
            logger.debug(f"Generated thumbnail: {thumb_path}")
            return f"thumbnails/{img_hash}.jpg"

        except Exception as e:
            logger.error(f"Thumbnail generation failed: {e}")
            return None

    def _hash_image(self, img: Image.Image) -> str:
        """Generate a perceptual hash (dHash) invariant to resolution and compression.

        Uses a hardened three-step approach so that the same wallpaper at
        1920x1080 and 3840x2160 produces an *identical* hash:

        1. Normalize to 32x32 grayscale  (resolution-independent base)
        2. Gaussian blur radius 1.5       (smooths ±1-3 level resampling noise)
        3. Resize to 9x8 for dHash        (gradient comparison → 64-bit hash)

        The blur step is critical: LANCZOS downsampling from different source
        resolutions can produce pixel values that differ by 1-3 levels at the
        same 32x32 position.  The Gaussian smoothing collapses those tiny
        differences so the subsequent 9x8 gradient comparisons are stable.
        """
        try:
            from PIL import ImageFilter
            gray = img.convert("L")
            # Step 1 — normalize to a fixed intermediate size
            normalized = gray.resize((32, 32), Image.LANCZOS)
            # Step 2 — smooth out resampling artifacts
            smoothed = normalized.filter(ImageFilter.GaussianBlur(radius=1.5))
            # Step 3 — final resize for dHash
            small = smoothed.resize((9, 8), Image.LANCZOS)
            pixels = list(small.getdata())
            bits = 0
            for y in range(8):
                for x in range(8):
                    bits = (bits << 1) | (1 if pixels[y * 9 + x] > pixels[y * 9 + x + 1] else 0)
            return format(bits, '016x')
        except Exception:
            import uuid
            return uuid.uuid4().hex[:16]
