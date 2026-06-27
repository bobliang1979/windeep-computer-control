# © 2026 BOBLIANG. All rights reserved.
"""
ocr_finder.py — Native Windows OCR for UIA blind spot coverage.

Uses Windows.Media.Ocr via PowerShell (zero Python OCR dependencies).
Windows 10+ built-in OCR engine supports Chinese, English, Japanese, etc.

Strategy:
  1. PowerShell subprocess → Windows.Media.Ocr API → JSON result
  2. Falls back gracefully if OCR is unavailable

Usage:
    from scripts.ocr_finder import ocr_find_text, ocr_get_all_text

    # Find a button by text
    result = ocr_find_text("提交", screenshot_b64)
    # -> {"found": True, "x": 500, "y": 300, "w": 80, "h": 30, "text": "提交", "confidence": 0.95}

    # Get all text with positions
    all_text = ocr_get_all_text(screenshot_b64)
    # -> [{"text": "...", "x": ..., "y": ..., "confidence": ...}, ...]
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
from typing import Optional

_PS_SCRIPT = r"""
param([string]$Base64Image, [string]$FindText)

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntimeContent]

# Decode base64 to bitmap
$bytes = [Convert]::FromBase64String($Base64Image)
$stream = [System.IO.MemoryStream]::new($bytes)
$bitmap = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream).GetAwaiter().GetResult()
$softwareBitmap = $bitmap.GetSoftwareBitmapAsync().GetAwaiter().GetResult()

# Run OCR
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = $engine.RecognizeAsync($softwareBitmap).GetAwaiter().GetResult()

$output = @()
foreach ($line in $result.Lines) {
    $text = $line.Text
    $rect = $line.BoundingRect
    $entry = @{
        text = $text
        x = [int]$rect.X
        y = [int]$rect.Y
        w = [int]$rect.Width
        h = [int]$rect.Height
        confidence = 0.9  # OCR engine doesn't expose per-line confidence
    }
    $output += $entry
}

# If FindText provided, filter and return first match
if ($FindText) {
    $lower = $FindText.ToLower()
    $match = $output | Where-Object { $_.text.ToLower().Contains($lower) } | Select-Object -First 1
    if ($match) {
        $match | ConvertTo-Json -Compress
    } else {
        '{"found":false}'
    }
} else {
    $output | ConvertTo-Json -Compress
}
"""


def _run_ocr(base64_img: str, find_text: str = "") -> Optional[dict]:
    """Run Windows.Media.Ocr via PowerShell. Returns parsed JSON result."""
    # Strip data URL prefix if present
    if "," in base64_img and base64_img.startswith("data:"):
        base64_img = base64_img.split(",", 1)[1]

    # PowerShell doesn't handle very long argument strings well.
    # Save base64 to a temp file and have PS read it.
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".b64", delete=False) as f:
            f.write(base64_img)
            b64_path = f.name

        ps_code = _PS_SCRIPT.replace('param([string]$Base64Image, [string]$FindText)',
                                      f'param([string]$B64Path, [string]$FindText)')
        ps_code = ps_code.replace(
            '$bytes = [Convert]::FromBase64String($Base64Image)',
            '$bytes = [Convert]::FromBase64String((Get-Content $B64Path -Raw).Trim())')

        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_code, "-B64Path", b64_path,
             "-FindText", find_text],
            capture_output=True, text=True, timeout=30,
        )

        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        return None

    except subprocess.TimeoutExpired:
        return None
    except json.JSONDecodeError:
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None
    finally:
        try:
            os.unlink(b64_path)
        except Exception:
            pass


def ocr_find_text(text: str, screenshot_b64: str) -> dict:
    """Find text in screenshot using Windows native OCR.

    Args:
        text: Text to search for (case-insensitive).
        screenshot_b64: Base64-encoded PNG screenshot.

    Returns:
        {"found": bool, "x": int, "y": int, "w": int, "h": int,
         "text": str, "confidence": float}
        If not found: {"found": False}
        If OCR unavailable: {"found": False, "error": "..."}
    """
    try:
        result = _run_ocr(screenshot_b64, find_text=text)
        if result is None:
            return {"found": False, "error": "OCR unavailable or timed out"}
        if result.get("found") is False:
            return {"found": False}
        # Single match result
        return {
            "found": True,
            "x": result["x"],
            "y": result["y"],
            "w": result.get("w", 0),
            "h": result.get("h", 0),
            "text": result.get("text", text),
            "confidence": result.get("confidence", 0.9),
        }
    except Exception as e:
        return {"found": False, "error": str(e)}


def ocr_get_all_text(screenshot_b64: str) -> list:
    """Get all recognized text with positions from screenshot.

    Returns:
        List of {"text": str, "x": int, "y": int, "w": int, "h": int, "confidence": float}
        or [] if OCR unavailable.
    """
    try:
        result = _run_ocr(screenshot_b64)
        if result is None:
            return []
        if isinstance(result, list):
            return result
        # Might be a dict with found=false
        return []
    except Exception:
        return []


def ocr_available() -> bool:
    """Check if Windows native OCR is available (Windows 10+)."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             r"""try {
                 Add-Type -AssemblyName System.Runtime.WindowsRuntime;
                 $null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntimeContent];
                 $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages();
                 if ($engine -ne $null) { Write-Output 'ok' } else { Write-Output 'no_engine' }
             } catch { Write-Output ('error: ' + $_.Exception.Message) }"""],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() == "ok"
    except Exception:
        return False
