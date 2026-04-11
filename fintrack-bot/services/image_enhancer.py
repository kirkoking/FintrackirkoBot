"""
Image enhancement for receipt OCR — Wave D.3
Preprocesses photos before sending to Gemini/Claude to improve parse quality.
Uses PIL only (already in requirements.txt as Pillow).
"""

import io
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# If a receipt image is larger than this, it's probably fine as-is.
# Below this, we upscale to help OCR.
MIN_DIMENSION_PX = 800

# JPEG quality for output (high enough to preserve text detail)
OUTPUT_QUALITY = 92


def enhance(image_bytes: bytes) -> bytes:
    """
    Enhance a receipt image for better OCR readability.
    Steps: auto-rotate → upscale if small → contrast boost → sharpen.
    Returns enhanced JPEG bytes, or original bytes if PIL fails.
    """
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        img = Image.open(io.BytesIO(image_bytes))

        # 1. Auto-rotate based on EXIF orientation
        img = ImageOps.exif_transpose(img)

        # 2. Convert to RGB (handles RGBA/P mode files)
        if img.mode != "RGB":
            img = img.convert("RGB")

        # 3. Upscale if too small (helps OCR on tiny/blurry receipts)
        w, h = img.size
        if min(w, h) < MIN_DIMENSION_PX:
            scale = MIN_DIMENSION_PX / min(w, h)
            new_size = (int(w * scale), int(h * scale))
            img = img.resize(new_size, Image.LANCZOS)
            logger.debug("image_enhancer: upscaled %dx%d → %dx%d", w, h, *new_size)

        # 4. Contrast boost (1.0 = original, 1.4 = moderate boost)
        img = ImageEnhance.Contrast(img).enhance(1.4)

        # 5. Sharpness boost (helps with text edges)
        img = ImageEnhance.Sharpness(img).enhance(2.0)

        # 6. Apply unsharp mask for fine detail
        img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=OUTPUT_QUALITY, optimize=True)
        enhanced = buf.getvalue()
        logger.debug("image_enhancer: %d bytes → %d bytes", len(image_bytes), len(enhanced))
        return enhanced

    except Exception as exc:
        logger.warning("image_enhancer: failed (%s), using original", exc)
        return image_bytes


def should_enhance(image_bytes: bytes) -> bool:
    """
    Heuristic: return True if the image might benefit from enhancement.
    Skips very large high-quality images (they're already good).
    """
    # If the file is > 3 MB, assume it's already high quality
    if len(image_bytes) > 3 * 1024 * 1024:
        return False

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        # Enhance if either dimension is small
        return min(w, h) < MIN_DIMENSION_PX * 1.5
    except Exception:
        return True  # Enhance by default if we can't read the image
