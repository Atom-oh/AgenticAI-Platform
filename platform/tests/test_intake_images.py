"""Private image normalization (Task I5; SRC-05 partial: SVG sanitizer unavailable)."""
from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageCms

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intake import images  # noqa: E402


def png(image, **options):
    buffer = io.BytesIO()
    image.save(buffer, "PNG", **options)
    return buffer.getvalue()


def jpeg(image, **options):
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", **options)
    return buffer.getvalue()


def test_rotated_jpeg_is_transposed_and_exif_is_removed():
    source = Image.new("RGB", (40, 20), (200, 10, 10))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise on display
    exif[0x010F] = "SyntheticCamera"
    data = jpeg(source, exif=exif.tobytes())
    result = images.normalize_image(data)
    assert result["exifTransposed"] is True
    assert (result["width"], result["height"]) == (20, 40)
    assert result["mode"] == "RGBA" and result["iccProfile"] == "none"
    out = Image.open(io.BytesIO(result["bytes"]))
    assert out.format == "PNG" and out.size == (20, 40)
    assert not out.getexif() and "exif" not in out.info and b"SyntheticCamera" not in result["bytes"]


def test_hashes_differ_and_both_are_reported():
    data = png(Image.new("RGB", (8, 8), (1, 2, 3)), pnginfo=None)
    result = images.normalize_image(data)
    assert result["originalSha256"] == hashlib.sha256(data).hexdigest()
    assert result["sha256"] == hashlib.sha256(result["bytes"]).hexdigest()
    assert result["sha256"] != result["originalSha256"]
    assert result["exifTransposed"] is False


def test_normalization_is_deterministic():
    data = png(Image.new("RGBA", (16, 9), (1, 2, 3, 128)))
    assert images.normalize_image(data) == images.normalize_image(data)


def test_animated_png_is_rejected():
    frames = [Image.new("RGB", (8, 8), color) for color in ((255, 0, 0), (0, 255, 0))]
    buffer = io.BytesIO()
    frames[0].save(buffer, "PNG", save_all=True, append_images=frames[1:], duration=100, loop=0)
    with pytest.raises(images.ImageRejected) as error:
        images.normalize_image(buffer.getvalue())
    assert error.value.code == "animated-image"


@pytest.mark.parametrize("size", [(8193, 1), (1, 8193)])
def test_oversized_dimensions_are_rejected(size):
    with pytest.raises(images.ImageRejected) as error:
        images.normalize_image(png(Image.new("L", size)))
    assert error.value.code == "image-dimensions"


def test_twenty_five_megapixels_are_rejected_even_under_the_side_limit():
    data = png(Image.new("L", (5000, 5000)))
    with pytest.raises(images.ImageRejected) as error:
        images.normalize_image(data)
    assert error.value.code in ("image-dimensions", "decompression-bomb")


def test_oversized_bytes_are_rejected():
    with pytest.raises(images.ImageRejected) as error:
        images.normalize_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * (20 * 1024 * 1024))
    assert error.value.code == "image-too-large"


@pytest.mark.parametrize("data", [b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
                                  b'\xef\xbb\xbf  <?xml version="1.0"?><svg/>'])
def test_svg_is_blocked_until_a_reviewed_sanitizer_lands(data):
    with pytest.raises(images.ImageRejected) as error:
        images.normalize_image(data)
    assert error.value.code == "svg-sanitizer-unavailable"


@pytest.mark.parametrize("data", [b"GIF89a....", b"not an image", b"\x89PNG\r\n\x1a\ntruncated"])
def test_unsupported_or_corrupt_inputs_are_rejected(data):
    with pytest.raises(images.ImageRejected) as error:
        images.normalize_image(data)
    assert error.value.code in ("unsupported-format", "image-parse-failed")


def test_embedded_srgb_profile_is_accepted():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    result = images.normalize_image(png(Image.new("RGB", (4, 4), (9, 9, 9)), icc_profile=profile))
    assert result["iccProfile"] == "srgb"
    assert "icc_profile" not in Image.open(io.BytesIO(result["bytes"])).info


def test_non_srgb_profile_is_converted_to_srgb_or_rejected():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
    data = png(Image.new("RGB", (4, 4), (9, 9, 9)), icc_profile=profile)
    try:
        result = images.normalize_image(data)
    except images.ImageRejected as error:
        assert error.code == "unsupported-color-profile"
    else:
        assert result["iccProfile"] == "srgb"


def test_unparseable_color_profile_is_rejected():
    data = png(Image.new("RGB", (4, 4)), icc_profile=b"not a real icc profile")
    with pytest.raises(images.ImageRejected) as error:
        images.normalize_image(data)
    assert error.value.code == "unsupported-color-profile"


def test_region_must_use_integer_top_left_coordinates_inside_the_normalized_image():
    image = images.normalize_image(png(Image.new("RGB", (100, 50))))
    assert images.region_ok({"left": 0, "top": 0, "width": 100, "height": 50}, image)
    assert images.region_ok({"left": 10, "top": 5, "width": 90, "height": 45}, image)
    for region in ({"left": 1, "top": 0, "width": 100, "height": 50},
                   {"left": 0, "top": 1, "width": 100, "height": 50},
                   {"left": -1, "top": 0, "width": 10, "height": 10},
                   {"left": 0.5, "top": 0, "width": 10, "height": 10},
                   {"left": True, "top": 0, "width": 10, "height": 10},
                   {"left": 0, "top": 0, "width": 0, "height": 10},
                   {"left": 0, "top": 0, "width": 10}):
        assert not images.region_ok(region, image)
