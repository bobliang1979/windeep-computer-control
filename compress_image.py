#!/usr/bin/env python3
"""
compress_image.py — Progressive image compressor for computer-use screenshots.

Canonical API (bytebot spec):
    from compress_image import compress_base64
    compressed_b64, ratio, fmt = compress_base64(b64_str, max_bytes=500*1024)

Legacy API:
    compress_to_target(base64_str, target_kb=1024) -> CompressionResult
    compress_from_file(input_path, ...) -> CompressionResult

Strategy (strict order — Codex++ review fix #2):
  Phase A — quality-only at scale=1.0:
    1. Try PNG at max compression (1 encode, skip binary search — fix #3)
    2. Binary search quality [95..10] for JPEG
    3. Format fallback: PNG at lower quality → JPEG → WebP
  Phase B — dimension downscale (only if Phase A failed):
    4. Scale down in 10% steps, try JPEG then WebP at each size

CLI:
    python compress_image.py --input <base64_file|path_to_png>
    python compress_image.py --input screenshot.png --target-kb 512

Requirements: pip install Pillow
"""

import base64
import io
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Optional

try:
    from PIL import Image
except ImportError:
    Image = None


@dataclass
class CompressionResult:
    """Result of a compression operation."""
    input_kb: float
    output_kb: float
    ratio: float
    format: str
    quality: int
    width: int
    height: int
    iterations: int
    base64: str


def _base64_to_image(b64_str: str) -> Image.Image:
    if ',' in b64_str and b64_str.startswith('data:'):
        b64_str = b64_str.split(',', 1)[1]
    raw = base64.b64decode(b64_str)
    return Image.open(io.BytesIO(raw))


def _image_to_base64(img: Image.Image, fmt: str, quality: int = 95) -> str:
    buf = io.BytesIO()
    save_kwargs = {}
    if fmt == 'png':
        save_kwargs['compress_level'] = 9
    elif fmt == 'jpeg':
        save_kwargs['quality'] = quality
        save_kwargs['optimize'] = True
        save_kwargs['progressive'] = True
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
    elif fmt == 'webp':
        save_kwargs['quality'] = quality
        save_kwargs['lossless'] = False
        save_kwargs['method'] = 6
    else:
        raise ValueError(f"Unsupported format: {fmt}")
    img.save(buf, format=fmt.upper(), **save_kwargs)
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _b64_size(b64: str) -> int:
    """Return decoded byte length of a base64 string."""
    return len(base64.b64decode(b64))


def _quality_binary_search(
    img: Image.Image, fmt: str, target_bytes: int,
    lo: int = 10, hi: int = 95, max_iter: int = 15,
    max_time_ms: float = 0,
) -> Optional[tuple[str, int, int]]:
    """Binary search quality for given format.

    Returns (base64, byte_size, quality) or None if even lo quality exceeds target.
    If max_time_ms > 0, stops early and returns best result so far.
    """
    deadline = (time.monotonic() + max_time_ms / 1000) if max_time_ms > 0 else None
    # For PNG: quality parameter is ignored by Pillow (uses compress_level=9).
    # Just do a single encode at max compression — Codex++ review fix #3.
    if fmt == 'png':
        b64_out = _image_to_base64(img, 'png', 95)
        out_bytes = _b64_size(b64_out)
        if out_bytes <= target_bytes:
            return (b64_out, out_bytes, 95)
        return None  # PNG won't get smaller; skip to JPEG

    best = None
    for _ in range(max_iter):
        if lo > hi:
            break
        # Early exit if deadline exceeded
        if deadline is not None and time.monotonic() >= deadline:
            if best is not None:
                return best
            break
        mid = (lo + hi) // 2
        try:
            b64_out = _image_to_base64(img, fmt, mid)
            out_bytes = _b64_size(b64_out)
            if out_bytes <= target_bytes:
                best = (b64_out, out_bytes, mid)
                lo = mid + 1  # Try higher quality
            else:
                hi = mid - 1
        except Exception:
            lo = mid + 1
    return best


def compress_image_direct(
    img: Image.Image,
    target_kb: float = 512.0,
    initial_quality: int = 95,
    min_quality: int = 10,
    max_iterations: int = 12,
) -> CompressionResult:
    """Compress a PIL Image directly, skipping base64 encode/decode step.

    Pipeline optimization (P1): eliminates the base64 round-trip that adds
    ~100-300ms for a full screenshot. Direct pixel→encoded→base64.
    """
    if Image is None:
        raise ImportError("Pillow is required")

    target_bytes = int(target_kb * 1024)
    orig_w, orig_h = img.size
    total_iterations = 0
    best = None
    formats = ['png', 'jpeg', 'webp']

    # Phase A: quality-only at full resolution
    img_rgb = img.convert('RGB') if img.mode in ('RGBA', 'LA', 'P') else img

    for fmt in formats:
        result = _quality_binary_search(
            img_rgb, fmt, target_bytes,
            lo=min_quality, hi=initial_quality, max_iter=max_iterations,
        )
        total_iterations += 1 if fmt == 'png' else max_iterations
        if result:
            b64_out, out_bytes, quality = result
            return CompressionResult(
                input_kb=0, output_kb=out_bytes / 1024,
                ratio=0, format=fmt, quality=quality,
                width=orig_w, height=orig_h,
                iterations=total_iterations, base64=b64_out,
            )

    # Phase B: scale down
    scale = 0.9
    while scale >= 0.3:
        nw, nh = max(64, int(orig_w * scale)), max(64, int(orig_h * scale))
        scaled = img_rgb.resize((nw, nh), Image.LANCZOS)
        for fmt in ['jpeg', 'webp']:
            result = _quality_binary_search(
                scaled, fmt, target_bytes,
                lo=min_quality, hi=initial_quality, max_iter=8,
            )
            total_iterations += 8
            if result:
                b64_out, out_bytes, quality = result
                return CompressionResult(
                    input_kb=0, output_kb=out_bytes / 1024,
                    ratio=0, format=fmt, quality=quality,
                    width=nw, height=nh,
                    iterations=total_iterations, base64=b64_out,
                )
        scale -= 0.1

    # Worst-case fallback
    scaled = img_rgb.resize((64, 64), Image.LANCZOS)
    fb64 = _image_to_base64(scaled, 'webp', min_quality)
    return CompressionResult(
        input_kb=0, output_kb=_b64_size(fb64) / 1024,
        ratio=0, format='webp', quality=min_quality,
        width=64, height=64,
        iterations=total_iterations, base64=fb64,
   )


def compress_pipeline(
    img: Image.Image,
    target_kb: float = 512.0,
    fast_format: str = 'jpeg',
    fast_quality: int = 85,
) -> CompressionResult:
    """Faster pipeline: single-encode at fixed quality, no binary search.

    For scenarios where latency matters more than optimal compression.
    ~50ms vs ~500ms for full binary search.

    Falls back to compress_image_direct if the result is over target.
    """
    # Quick single encode
    try:
        b64 = _image_to_base64(img, fast_format, fast_quality)
        out_bytes = _b64_size(b64)
        if out_bytes <= int(target_kb * 1024):
            return CompressionResult(
                input_kb=0, output_kb=out_bytes / 1024,
                ratio=0, format=fast_format, quality=fast_quality,
                width=img.width, height=img.height,
                iterations=1, base64=b64,
            )
    except Exception:
        pass
    # Fallback to full search
    return compress_image_direct(img, target_kb)


def compress_to_target(
    base64_str: str,
    target_kb: float = 1024.0,
    initial_quality: int = 95,
    min_quality: int = 10,
    max_iterations: int = 15,
    formats: list = None,
) -> CompressionResult:
    """Compress image to fit within target_kb.

    Phase A: quality-only at full resolution.
    Phase B: scale down + retry (Codex++ review fix #2).
    """
    if Image is None:
        raise ImportError("Pillow is required. Install: pip install Pillow")

    if formats is None:
        formats = ['png', 'jpeg', 'webp']

    raw_b64 = base64_str.split(',', 1)[1] if (',' in base64_str and base64_str.startswith('data:')) else base64_str
    input_bytes = base64.b64decode(raw_b64)
    input_kb = len(input_bytes) / 1024
    target_bytes = int(target_kb * 1024)

    if input_kb <= target_kb:
        return CompressionResult(
            input_kb=input_kb, output_kb=input_kb, ratio=1.0,
            format='png', quality=100, width=0, height=0,
            iterations=0, base64=raw_b64,
        )

    img = _base64_to_image(base64_str)
    orig_w, orig_h = img.size
    total_iterations = 0
    best = None

    # ── Phase A: quality-only at full resolution ──
    img_rgb = img.convert('RGB') if img.mode in ('RGBA', 'LA', 'P') else img
    for fmt in formats:
        result = _quality_binary_search(
            img_rgb, fmt, target_bytes,
            lo=min_quality, hi=initial_quality, max_iter=max_iterations,
        )
        total_iterations += 1 if fmt == 'png' else 15  # PNG=1 encode, others=15 max
        if result:
            b64_out, out_bytes, quality = result
            best = CompressionResult(
                input_kb=input_kb, output_kb=out_bytes / 1024,
                ratio=out_bytes / len(input_bytes), format=fmt,
                quality=quality, width=orig_w, height=orig_h,
                iterations=total_iterations, base64=b64_out,
            )
            return best  # Found at full resolution — done

    # ── Phase B: dimension downscale ──
    scale = 0.9
    while scale >= 0.3:
        nw, nh = max(64, int(orig_w * scale)), max(64, int(orig_h * scale))
        scaled = img_rgb.resize((nw, nh), Image.LANCZOS)
        for fmt in ['jpeg', 'webp']:  # PNG not useful scaled — JPEG/WebP better
            result = _quality_binary_search(
                scaled, fmt, target_bytes,
                lo=min_quality, hi=initial_quality, max_iter=8,
            )
            total_iterations += 1 if fmt == 'jpeg' else 8
            if result:
                b64_out, out_bytes, quality = result
                best = CompressionResult(
                    input_kb=input_kb, output_kb=out_bytes / 1024,
                    ratio=out_bytes / len(input_bytes), format=fmt,
                    quality=quality, width=nw, height=nh,
                    iterations=total_iterations, base64=b64_out,
                )
                return best
        scale -= 0.1

    # Worst-case fallback
    scaled = img_rgb.resize((64, 64), Image.LANCZOS)
    fb64 = _image_to_base64(scaled, 'webp', min_quality)
    fb_bytes = _b64_size(fb64)
    if fb_bytes < len(input_bytes):
        best = CompressionResult(
            input_kb=input_kb, output_kb=fb_bytes / 1024,
            ratio=fb_bytes / len(input_bytes), format='webp',
            quality=min_quality, width=64, height=64,
            iterations=total_iterations, base64=fb64,
        )
    else:
        best = CompressionResult(
            input_kb=input_kb, output_kb=input_kb, ratio=1.0,
            format='png', quality=100, width=orig_w, height=orig_h,
            iterations=0, base64=raw_b64,
        )
    return best


def compress_from_file(
    input_path: str,
    output_path: Optional[str] = None,
    target_kb: float = 1024.0,
    output_format: str = 'auto',
) -> CompressionResult:
    if Image is None:
        raise ImportError("Pillow is required. Install: pip install Pillow")
    with open(input_path, 'rb') as f:
        raw = f.read()
    b64_str = base64.b64encode(raw).decode('ascii')
    if output_format == 'auto':
        ext = os.path.splitext(input_path)[1].lower()
        fmt_map = {'.png': 'png', '.jpg': 'jpeg', '.jpeg': 'jpeg', '.webp': 'webp'}
        preferred = fmt_map.get(ext, 'png')
        formats = [preferred] + [f for f in ['png', 'jpeg', 'webp'] if f != preferred]
    else:
        formats = [output_format]
    result = compress_to_target(b64_str, target_kb=target_kb, formats=formats)
    if output_path:
        with open(output_path, 'wb') as f:
            f.write(base64.b64decode(result.base64))
    return result


# ── bytebot canonical API (Codex++ review fix #1: thin wrapper) ──

def compress_base64(
    b64: str,
    max_bytes: int = 500 * 1024,
) -> tuple[str, float, str]:
    """Compress a base64-encoded image to fit within max_bytes.

    Thin wrapper over compress_to_target (Codex++ review fix #1).

    Returns
    -------
    (base64_str, ratio, format_name)
        ratio = compressed_size / original_size (0.0–1.0)
        format_name = "PNG" | "JPEG" | "WEBP"
    """
    raw_b64 = b64.split(",", 1)[1] if ("," in b64 and b64.startswith("data:")) else b64
    input_bytes = _b64_size(raw_b64)
    if input_bytes <= max_bytes:
        return (b64, 1.0, "PNG")

    res = compress_to_target(b64, target_kb=max_bytes / 1024, max_iterations=15)
    return (res.base64, res.ratio, res.format.upper())


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Progressive image compressor (bytebot-inspired)',
    )
    parser.add_argument('--input', '-i', required=True,
                        help='Input: base64 file, PNG file path, or "-" for stdin')
    parser.add_argument('--output', '-o', help='Output file path')
    parser.add_argument('--target-kb', type=float, default=1024.0,
                        help='Target size in KB (default: 1024)')
    parser.add_argument('--format', choices=['png', 'jpeg', 'webp', 'auto'],
                        default='auto', help='Output format (default: auto)')
    parser.add_argument('--json', action='store_true',
                        help='Output full result as JSON')
    parser.add_argument('--summary', action='store_true',
                        help='Print human-readable summary')

    args = parser.parse_args()

    if args.input == '-':
        raw_input = sys.stdin.read().strip()
    elif args.input.endswith(('.png', '.jpg', '.jpeg', '.webp')):
        result = compress_from_file(args.input, args.output, args.target_kb, args.format)
    else:
        with open(args.input) as f:
            raw_input = f.read().strip()
        result = compress_to_target(raw_input, target_kb=args.target_kb)

    if args.json:
        output = {
            'input_kb': round(result.input_kb, 1),
            'output_kb': round(result.output_kb, 1),
            'ratio': round(result.ratio, 3),
            'format': result.format,
            'quality': result.quality,
            'dimensions': f'{result.width}x{result.height}',
            'iterations': result.iterations,
            'base64': result.base64,
        }
        out_text = json.dumps(output, indent=2)
    elif args.summary:
        saved = 100 * (1 - result.ratio)
        out_text = (
            f"Compression: {result.input_kb:.0f}KB -> {result.output_kb:.0f}KB "
            f"({saved:.0f}% saved)\n"
            f"Format: {result.format} @ quality {result.quality}\n"
            f"Dimensions: {result.width}x{result.height}\n"
            f"Iterations: {result.iterations}"
        )
    else:
        out_text = result.base64

    if args.output and not args.output.endswith(('.png', '.jpg', '.jpeg', '.webp')):
        with open(args.output, 'w') as f:
            f.write(out_text)
    else:
        print(out_text)


if __name__ == '__main__':
    main()
