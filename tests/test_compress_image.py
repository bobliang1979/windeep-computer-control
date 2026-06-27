"""Tests for compress_image.py."""

import base64
import io
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image
from compress_image import compress_base64


def _make_b64(mode, size, color, fmt="PNG"):
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_small_image_stays_same():
    b64 = _make_b64("RGB", (32, 32), (255, 0, 0))
    out_b64, ratio, fmt = compress_base64(b64, max_bytes=10*1024*1024)
    assert ratio == 1.0


def test_gradient_compresses():
    img = Image.new("RGB", (3000, 3000))
    for y in range(3000):
        for x in range(0, 3000, 16):
            r = (x*255)//3000; g = (y*255)//3000; b = ((x+y)*255)//6000
            for dx in range(16):
                if x+dx < 3000:
                    img.putpixel((x+dx, y), (r, g, b))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    out_b64, ratio, fmt = compress_base64(b64, max_bytes=80*1024)
    assert len(base64.b64decode(out_b64)) <= 80*1024


def test_transparent_png():
    b64 = _make_b64("RGBA", (500, 500), (0, 255, 0, 64))
    out_b64, ratio, fmt = compress_base64(b64, max_bytes=50*1024)
    assert len(base64.b64decode(out_b64)) <= 50*1024


def test_format_fallback():
    buf = io.BytesIO()
    Image.new("RGB", (2000, 2000), (200, 100, 50)).save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    out_b64, ratio, fmt = compress_base64(b64, max_bytes=20*1024)
    assert fmt in ("JPEG", "WEBP", "PNG")


def test_invalid_base64_raises():
    try:
        compress_base64("!!!not-base64!!!")
        assert False
    except ValueError:
        pass


def test_data_uri_prefix():
    b64 = _make_b64("RGB", (100, 100), (10, 20, 30))
    prefix = "data:image/png;base64,"
    out_b64, ratio, fmt = compress_base64(prefix + b64)
    assert len(out_b64) > 0


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); passed += 1; print(f"  PASS  {name}")
            except Exception as e:
                failed += 1; print(f"  FAIL  {name}: {e}")
    print(f"Results: {passed} passed, {failed} failed")
