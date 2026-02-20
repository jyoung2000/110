"""Image enhancer — smart crop + upscale for near-miss wallpapers.

When a downloaded image is slightly too small or has a non-standard aspect
ratio, this module tries to salvage it by:
1. Cropping to the nearest standard ratio (16:9 or 9:16)
2. Upscaling with high-quality Lanczos resampling to meet minimum dimensions
3. Applying a light sharpen to counteract upscale softness
"""
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageFilter
from src.utils.logging import setup_logging

logger = setup_logging("enhancer")

# Standard wallpaper aspect ratios to target, as (w_ratio, h_ratio, label)
# Only 16:9 and 9:16 — these are the universal wallpaper ratios that work everywhere.
TARGET_RATIOS = [
    (16, 9, "16:9"),
    (9, 16, "9:16"),
]

# Enhancement limits
MAX_UPSCALE_FACTOR = 2.5      # Don't upscale more than 2.5x (quality degrades)
MIN_SOURCE_FRACTION = 0.45    # Source must be at least 45% of target min dimensions
MAX_CROP_LOSS = 0.25          # Don't crop away more than 25% of pixels


class ImageEnhancer:
    """Enhance near-miss images to meet wallpaper validation criteria."""

    def __init__(self, min_width: int = 800, min_height: int = 600,
                 max_upscale: float = MAX_UPSCALE_FACTOR):
        self.min_width = min_width
        self.min_height = min_height
        self.max_upscale = max_upscale

    def can_enhance(self, width: int, height: int) -> bool:
        """Check if an image with these dimensions is a candidate for enhancement.

        Returns True if the image is large enough to produce acceptable quality
        after cropping and upscaling, but currently fails minimum requirements.
        """
        # Already meets minimums — no enhancement needed
        if width >= self.min_width and height >= self.min_height:
            return False

        # Too small to produce acceptable quality
        if (width < self.min_width * MIN_SOURCE_FRACTION or
                height < self.min_height * MIN_SOURCE_FRACTION):
            return False

        # Check if any target ratio works within crop/upscale limits
        best = self._find_best_target(width, height)
        return best is not None

    def enhance(self, file_path: Path) -> Optional[Tuple[Path, int, int]]:
        """Enhance image: crop to standard ratio + upscale to minimum dimensions.

        Returns (enhanced_path, new_width, new_height) or None if enhancement
        isn't possible or would degrade quality too much.
        """
        try:
            img = Image.open(file_path)
            width, height = img.size

            best = self._find_best_target(width, height)
            if best is None:
                logger.debug(f"No viable enhancement target for {width}x{height}")
                return None

            target_w, target_h, ratio_label, crop_box = best

            # Step 1: Crop to target aspect ratio
            img = img.crop(crop_box)
            crop_w, crop_h = img.size
            logger.debug(f"Cropped {width}x{height} -> {crop_w}x{crop_h} ({ratio_label})")

            # Step 2: Upscale to meet minimum dimensions
            scale = max(self.min_width / crop_w, self.min_height / crop_h)
            if scale > 1.0:
                new_w = max(int(crop_w * scale), self.min_width)
                new_h = max(int(crop_h * scale), self.min_height)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                logger.debug(f"Upscaled {crop_w}x{crop_h} -> {new_w}x{new_h} ({scale:.2f}x)")
            else:
                new_w, new_h = crop_w, crop_h

            # Step 3: Light sharpen to counteract upscale softness
            if scale > 1.2:
                img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=80, threshold=2))

            # Step 4: Save enhanced image
            if img.mode != "RGB":
                img = img.convert("RGB")
            output_path = file_path.parent / f"enhanced_{file_path.stem}.jpg"
            img.save(output_path, "JPEG", quality=90, optimize=True, subsampling=0)

            final_w, final_h = img.size
            logger.info(f"Enhanced {width}x{height} -> {final_w}x{final_h} ({ratio_label}, {scale:.1f}x upscale)")
            return output_path, final_w, final_h

        except Exception as e:
            logger.error(f"Enhancement failed for {file_path}: {e}")
            return None

    def _find_best_target(self, width: int, height: int) -> Optional[Tuple[int, int, str, Tuple[int, int, int, int]]]:
        """Find the best target ratio that requires minimal cropping and acceptable upscaling.

        Returns (target_w, target_h, ratio_label, crop_box) or None.
        """
        current_ratio = width / height
        candidates = []

        for rw, rh, label in TARGET_RATIOS:
            target_ratio = rw / rh

            # Calculate crop dimensions to match this ratio
            if current_ratio > target_ratio:
                # Image is wider than target — crop width
                crop_h = height
                crop_w = int(height * target_ratio)
            else:
                # Image is taller than target — crop height
                crop_w = width
                crop_h = int(width / target_ratio)

            # Ensure crop doesn't exceed original dimensions
            crop_w = min(crop_w, width)
            crop_h = min(crop_h, height)

            # Check crop loss
            original_pixels = width * height
            cropped_pixels = crop_w * crop_h
            crop_loss = 1.0 - (cropped_pixels / original_pixels)
            if crop_loss > MAX_CROP_LOSS:
                continue

            # Check upscale factor needed
            scale_w = self.min_width / crop_w
            scale_h = self.min_height / crop_h
            scale = max(scale_w, scale_h, 1.0)
            if scale > self.max_upscale:
                continue

            # Calculate center crop box
            x_offset = (width - crop_w) // 2
            y_offset = (height - crop_h) // 2
            crop_box = (x_offset, y_offset, x_offset + crop_w, y_offset + crop_h)

            # Score: prefer less cropping and less upscaling
            # Lower score = better candidate
            score = crop_loss * 100 + (scale - 1.0) * 50 + abs(current_ratio - target_ratio) * 10
            candidates.append((score, crop_w, crop_h, label, crop_box))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0])
        _, crop_w, crop_h, label, crop_box = candidates[0]
        return crop_w, crop_h, label, crop_box
