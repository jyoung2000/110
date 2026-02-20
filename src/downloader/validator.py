"""Image validation — checks dimensions, aspect ratio, and file integrity."""
import numpy as np
from pathlib import Path
from typing import List, Tuple
from PIL import Image, ImageFilter
from src.utils.aspect_ratio import calculate_aspect_ratio
from src.utils.logging import setup_logging

logger = setup_logging("validator")

MIN_WIDTH = 800
MIN_HEIGHT = 600


class ImageValidator:
    """Validate downloaded images meet wallpaper criteria."""

    def __init__(self, min_width: int = MIN_WIDTH, min_height: int = MIN_HEIGHT,
                 allowed_aspects: List[str] = None, allow_mobile: bool = True,
                 watermark_detection: bool = True):
        self.min_width = min_width
        self.min_height = min_height
        self.allowed_aspects = allowed_aspects or []
        self.allow_mobile = allow_mobile
        self.watermark_detection = watermark_detection

    def validate(self, file_path: Path) -> Tuple[bool, str]:
        """Validate an image file. Returns (is_valid, reason)."""
        try:
            if not file_path.exists():
                return False, "File does not exist"

            file_size = file_path.stat().st_size
            if file_size < 5000:
                return False, f"File too small ({file_size} bytes)"
            if file_size > 50 * 1024 * 1024:
                return False, f"File too large ({file_size} bytes)"

            img = Image.open(file_path)
            img.verify()

            # Reopen after verify
            img = Image.open(file_path)
            width, height = img.size

            if width < self.min_width or height < self.min_height:
                return False, f"Too small ({width}x{height})"

            # Mobile/portrait check
            if not self.allow_mobile and height > width:
                return False, f"Mobile/portrait not allowed ({width}x{height})"

            # Aspect ratio filter
            if self.allowed_aspects:
                aspect = calculate_aspect_ratio(width, height)
                if aspect != "unknown" and aspect not in self.allowed_aspects:
                    return False, f"Aspect ratio {aspect} not in allowed list"

            # Watermark detection (can be disabled via settings)
            if self.watermark_detection:
                has_watermark, wm_reason = self._detect_watermark(img)
                if has_watermark:
                    return False, f"Watermark detected: {wm_reason}"

            return True, "OK"

        except Exception as e:
            return False, f"Invalid image: {e}"

    def _detect_watermark(self, img: Image.Image) -> Tuple[bool, str]:
        """Detect watermarked images using pixel analysis.

        Checks for:
        1. Repeating semi-transparent diagonal patterns (stock photo watermarks)
        2. Uniform low-opacity overlays across the center (shutterstock-style)
        3. Grid patterns of identical small marks (depositphotos-style)
        """
        try:
            # Work on a smaller version for speed
            w, h = img.size
            scale = min(1.0, 600 / max(w, h))
            if scale < 1.0:
                small = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            else:
                small = img.copy()

            if small.mode != "RGB":
                small = small.convert("RGB")

            arr = np.array(small, dtype=np.float32)
            sw, sh = small.size

            # --- Check 1: Diagonal stripe pattern detection ---
            # Watermarks like Shutterstock/iStock use repeating diagonal text.
            # Convert to grayscale, apply edge detection, then check for
            # regular diagonal patterns in the frequency domain.
            gray = np.mean(arr, axis=2)

            # Extract center region (watermarks are typically centered)
            cy, cx = gray.shape[0] // 2, gray.shape[1] // 2
            crop_h, crop_w = min(200, gray.shape[0] // 2), min(200, gray.shape[1] // 2)
            center = gray[cy - crop_h:cy + crop_h, cx - crop_w:cx + crop_w]

            if center.size == 0:
                return False, ""

            # Apply Sobel-like edge detection on the center crop
            edges_h = np.abs(np.diff(center, axis=0))
            edges_v = np.abs(np.diff(center, axis=1))

            # Watermarked images have higher edge density in the center
            # compared to natural images, because the watermark text
            # creates artificial edges on top of the photo content.
            # Use the ratio of high-edge pixels to total pixels.
            h_edges = edges_h[:, :min(edges_h.shape[1], edges_v.shape[1])]
            v_edges = edges_v[:min(edges_h.shape[0], edges_v.shape[0]), :]
            combined_edges = (h_edges[:v_edges.shape[0], :] + v_edges[:h_edges.shape[0], :]) / 2

            # --- Check 2: Semi-transparent overlay detection ---
            # Stock watermarks reduce contrast in the center region.
            # Compare center vs edge contrast/variance.
            edge_band = 50  # pixels from edge
            if gray.shape[0] > edge_band * 4 and gray.shape[1] > edge_band * 4:
                border_top = gray[:edge_band, :]
                border_bottom = gray[-edge_band:, :]
                border_left = gray[:, :edge_band]
                border_right = gray[:, -edge_band:]

                border_var = np.mean([
                    np.var(border_top), np.var(border_bottom),
                    np.var(border_left), np.var(border_right),
                ])
                center_var = np.var(center)

                # If center has MUCH lower variance than edges, it suggests
                # a semi-transparent overlay flattening the center
                if border_var > 0 and center_var > 0:
                    ratio = center_var / border_var
                    # Very washed-out centers (ratio < 0.3) suggest watermark overlay
                    if ratio < 0.25 and border_var > 500:
                        logger.debug(f"Watermark suspect: center/border variance ratio={ratio:.2f}")
                        return True, "semi-transparent overlay (low center contrast)"

            # --- Check 3: Repeating pattern via autocorrelation ---
            # Stock watermarks tile the same text/logo diagonally.
            # Check if the center region has strong periodic correlations.
            if center.shape[0] >= 100 and center.shape[1] >= 100:
                # Sample a horizontal stripe from the center
                stripe = center[center.shape[0] // 2, :]
                stripe = stripe - np.mean(stripe)
                norm = np.sum(stripe ** 2)
                if norm > 0:
                    # Check autocorrelation at various lags
                    high_corr_count = 0
                    for lag in range(30, min(150, len(stripe) // 2), 10):
                        corr = np.sum(stripe[:-lag] * stripe[lag:]) / norm
                        if corr > 0.4:
                            high_corr_count += 1

                    if high_corr_count >= 3:
                        logger.debug(f"Watermark suspect: repeating pattern ({high_corr_count} correlated lags)")
                        return True, "repeating diagonal pattern"

        except Exception as e:
            logger.debug(f"Watermark detection error: {e}")

        return False, ""
