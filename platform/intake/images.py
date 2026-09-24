"""Private v1 image normalization (`AGENTCORE_CONTRACT.md` image profile; SRC-05).

Accepts PNG/JPEG only, at most 20 MiB, 20 megapixels and 8 192 px per side.
Rejects animation, decompression bombs, parse failures and unsupported color
profiles. Applies the EXIF orientation, converts to sRGB RGBA, strips metadata
and re-encodes PNG. The original and derivative hashes are both reported.

Sanitized SVG belongs to the v1 contract profile but no reviewed sanitizer
exists yet, so SVG inputs are blocked (`svg-sanitizer-unavailable`).
"""
from __future__ import annotations

import hashlib
import io
import threading
import warnings

PROFILE = "image-normalization-1"
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_SIDE = 8192
_MODES = frozenset({"1", "L", "LA", "P", "PA", "RGB", "RGBA"})
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_lock = threading.Lock()


class ImageRejected(Exception):
    """The input is blocked for admission; `code` is the blocking reason."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _looks_like_svg(data):
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith((b"<?xml", b"<svg", b"<!doctype svg")) or b"<svg" in head


def _srgb(image):
    """Return (image, "srgb"|"none") or reject an unusable embedded profile."""
    from PIL import ImageCms
    icc = image.info.get("icc_profile")
    if not icc:
        return image, "none"
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        description = (ImageCms.getProfileDescription(source) or "").lower()
        if "srgb" in description:
            return image, "srgb"
        target = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        mode = "RGBA" if "A" in image.mode or image.mode == "P" and "transparency" in image.info else "RGB"
        converted = ImageCms.profileToProfile(image.convert(mode), source, target, outputMode=mode)
        if converted is None or converted.size != image.size:
            raise ValueError()
        return converted, "srgb"
    except Exception:  # noqa: BLE001 - unparseable or non-convertible profile
        raise ImageRejected("unsupported-color-profile") from None


def normalize_image(data: bytes) -> dict:
    from PIL import Image, ImageOps
    if not isinstance(data, (bytes, bytearray)):
        raise ImageRejected("unsupported-format")
    data = bytes(data)
    if len(data) > MAX_BYTES:
        raise ImageRejected("image-too-large")
    if _looks_like_svg(data):
        raise ImageRejected("svg-sanitizer-unavailable")
    expected = "PNG" if data.startswith(_PNG) else "JPEG" if data.startswith(_JPEG) else None
    if expected is None:
        raise ImageRejected("unsupported-format")
    with _lock, warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        previous = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        try:
            try:
                image = Image.open(io.BytesIO(data), formats=[expected])
            except (Image.DecompressionBombWarning, Image.DecompressionBombError):
                raise ImageRejected("decompression-bomb") from None
            except Exception:  # noqa: BLE001
                raise ImageRejected("image-parse-failed") from None
            width, height = image.size
            if not 1 <= width <= MAX_SIDE or not 1 <= height <= MAX_SIDE or width * height > MAX_PIXELS:
                raise ImageRejected("image-dimensions")
            if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                raise ImageRejected("animated-image")
            if image.mode not in _MODES:
                raise ImageRejected("unsupported-color-profile")
            try:
                image.load()
                orientation = image.getexif().get(0x0112, 1)
                transposed = ImageOps.exif_transpose(image)
            except (Image.DecompressionBombWarning, Image.DecompressionBombError):
                raise ImageRejected("decompression-bomb") from None
            except ImageRejected:
                raise
            except Exception:  # noqa: BLE001
                raise ImageRejected("image-parse-failed") from None
            # exif_transpose drops the orientation tag; carry the ICC profile over.
            if image.info.get("icc_profile"):
                transposed.info["icc_profile"] = image.info["icc_profile"]
            if image.mode == "P" and "transparency" in image.info:
                transposed.info["transparency"] = image.info["transparency"]
            converted, icc = _srgb(transposed)
            rgba = converted.convert("RGBA")
            # A fresh image carries no EXIF, ICC, text chunks or other metadata.
            clean = Image.frombytes("RGBA", rgba.size, rgba.tobytes())
            buffer = io.BytesIO()
            clean.save(buffer, "PNG", optimize=False, compress_level=6)
        finally:
            Image.MAX_IMAGE_PIXELS = previous
    out = buffer.getvalue()
    return {"bytes": out, "sha256": hashlib.sha256(out).hexdigest(),
            "originalSha256": hashlib.sha256(data).hexdigest(),
            "width": clean.width, "height": clean.height, "mode": "RGBA",
            "exifTransposed": orientation not in (None, 1), "iccProfile": icc}


def region_ok(region, image) -> bool:
    """Integer top-left region that fits the normalized image."""
    if not isinstance(region, dict) or not {"left", "top", "width", "height"} <= region.keys():
        return False
    values = [region[key] for key in ("left", "top", "width", "height")]
    if any(type(value) is not int for value in values):
        return False
    left, top, width, height = values
    bounds = (image["width"], image["height"]) if isinstance(image, dict) else image.size
    return (left >= 0 and top >= 0 and width >= 1 and height >= 1
            and left + width <= bounds[0] and top + height <= bounds[1])
