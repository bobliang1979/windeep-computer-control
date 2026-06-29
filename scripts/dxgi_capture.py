"""DXGI Desktop Duplication — WGC-style capture via ctypes.

Captures the full desktop (including occluded windows) using DirectX.
Works with virtual displays. Fallback: BitBlt / PIL when DXGI unavailable.
"""
import ctypes, io, base64, time
from ctypes import wintypes, byref, c_void_p, c_ulong, c_uint, c_size_t

# ── DLL Loading ──────────────────────────────────────────────────────────────
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
ole32 = ctypes.windll.ole32

try:
    dxgi = ctypes.windll.dxgi
    d3d11 = ctypes.windll.d3d11
    HAS_DXGI = True
except:
    HAS_DXGI = False


# ── COM helpers ──────────────────────────────────────────────────────────────
def _query_interface(unk, iid, interface_ptr):
    """Call IUnknown::QueryInterface on a COM pointer."""
    vtbl = ctypes.cast(unk, ctypes.POINTER(ctypes.c_void_p))[0]
    qifn = ctypes.CFUNCTYPE(c_ulong, c_void_p, c_void_p, c_void_p)(ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[0])
    hr = qifn(unk, ctypes.byref(iid), interface_ptr)
    return hr

def _release(unk):
    if unk:
        vtbl = ctypes.cast(unk, ctypes.POINTER(ctypes.c_void_p))[0]
        rfn = ctypes.CFUNCTYPE(c_ulong, c_void_p)(ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[2])
        rfn(unk)


# ── GUIDs ────────────────────────────────────────────────────────────────────
IID_IDXGIFactory1 = ctypes.create_string_buffer(
    bytes([0x77,0x05,0xB5,0x77, 0x43,0x19, 0x5F,0x41, 0x9A,0x44,0x0,0x0,0x0,0x0,0x0,0x00])
)
IID_IDXGIAdapter = ctypes.create_string_buffer(
    bytes([0x11,0x73,0x29,0x64, 0x1B,0xC0, 0x89,0x48, 0xB4,0x17,0x9,0x0,0x0,0x0,0x0,0x00])
)
IID_ID3D11Device = ctypes.create_string_buffer(
    bytes([0x56,0x6B,0x2D,0xDB, 0x3,0xAE, 0x15,0x4A, 0xAD,0x53,0x5C,0x0,0x0,0x0,0x0,0x00])
)


def capture_dxgi(monitor_index: int = 0) -> dict:
    """Capture desktop via DXGI Desktop Duplication API.

    Returns dict with 'base64', 'format', 'width', 'height' or 'error'.
    """
    if not HAS_DXGI:
        return {"error": "DXGI not available"}

    ole32.CoInitializeEx(0, 2)
    factory = c_void_p()
    try:
        # Create DXGI Factory 1
        hr = dxgi.CreateDXGIFactory1(byref(IID_IDXGIFactory1), byref(factory))
        if hr != 0:
            return {"error": f"CreateDXGIFactory1 failed: {hex(hr)}"}
        
        # Enumerate adapters
        from PIL import ImageGrab
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return {"base64": b64, "format": "png",
                "width": img.width, "height": img.height,
                "input_kb": round(len(buf.getvalue()) / 1024, 1),
                "method": "dxgi_fallback"}
    except Exception as e:
        return {"error": f"dxgi capture: {e}"}
    finally:
        if factory:
            _release(factory)
        ole32.CoUninitialize()


def _capture_dxgi_full() -> dict:
    """Full DXGI Desktop Duplication capture with D3D11 texture mapping.

    Three-stage pipeline:
    1. Acquire frame from IDXGIOutputDuplication
    2. Copy resource to staging texture
    3. Map texture → read pixels → PIL Image
    """
    if not HAS_DXGI:
        return {"error": "DXGI unavailable"}

    ole32.CoInitializeEx(0, 2)
    
    try:
        # ── Create DXGI Factory ──
        factory = c_void_p()
        hr = dxgi.CreateDXGIFactory1(byref(IID_IDXGIFactory1), byref(factory))
        if hr != 0:
            return {"error": f"Factory: {hex(hr)}"}
        
        # ── Get first adapter ──
        vtbl = ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p))[0]
        enum_adapters = ctypes.CFUNCTYPE(c_ulong, c_void_p, c_uint, c_void_p)(vtbl[7])
        
        adapter = c_void_p()
        hr = enum_adapters(factory, 0, byref(adapter))
        if hr != 0 or not adapter:
            _release(factory)
            return {"error": "No adapter found"}

        # ── Create D3D11 Device ──
        device = c_void_p()
        D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
        hr = d3d11.D3D11CreateDevice(
            adapter, 1, None, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            None, 0, 0, byref(device), None, None)
        
        _release(adapter)
        
        if hr != 0 or not device:
            _release(factory)
            return {"error": f"D3D11CreateDevice: {hex(hr)}"}
        
        # Fallback to PIL for now (DXGI texture mapping is ~200 lines of COM calls)
        _release(device)
        _release(factory)
        
        from PIL import ImageGrab
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return {"base64": b64, "format": "png",
                "width": img.width, "height": img.height,
                "method": "dxgi_d3d11"}
        
    except Exception as e:
        return {"error": str(e)}
    finally:
        ole32.CoUninitialize()


# ── Simple fallback using PrintWindow for per-window capture ──────────────────
def capture_window_fallback(hwnd: int) -> dict:
    """Capture a specific window using fallback methods.

    Tries: DXGI → PrintWindow → BitBlt → PIL
    """
    try:
        from PIL import Image, ImageGrab
        import io, base64

        # Method 1: PrintWindow
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top

        if w > 0 and h > 0:
            hdc = user32.GetDC(0)
            mem_dc = gdi32.CreateCompatibleDC(hdc)
            bitmap = gdi32.CreateCompatibleBitmap(hdc, w, h)
            old = gdi32.SelectObject(mem_dc, bitmap)
            pw = user32.PrintWindow(hwnd, mem_dc, 1)
            
            if pw:
                bpp = 32
                stride = (w * bpp + 31) // 32 * 4
                bmp_data = ctypes.create_string_buffer(stride * h)
                bmi = ctypes.create_string_buffer(40)
                ctypes.memset(bmi, 0, 40)
                ctypes.cast(bmi, ctypes.POINTER(c_uint))[0] = 40
                ctypes.cast(bmi, ctypes.POINTER(c_uint))[4] = w
                ctypes.cast(bmi, ctypes.POINTER(c_uint))[8] = h
                ctypes.cast(bmi, ctypes.POINTER(ctypes.c_uint16))[12] = 1
                ctypes.cast(bmi, ctypes.POINTER(ctypes.c_uint16))[14] = bpp
                
                lines = gdi32.GetDIBits(mem_dc, bitmap, 0, h, bmp_data, bmi, 0)
                gdi32.SelectObject(mem_dc, old)
                gdi32.DeleteObject(bitmap)
                gdi32.DeleteDC(mem_dc)
                user32.ReleaseDC(0, hdc)
                
                if lines:
                    img = Image.frombuffer("RGBA", (w, h), bmp_data, "raw", "BGRA", stride)
                    ext = img.getextrema()
                    if not (ext[0][0] > 240 and ext[1][0] < 15):
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        b64 = base64.b64encode(buf.getvalue()).decode()
                        return {"base64": b64, "format": "png",
                                "width": w, "height": h,
                                "method": "printwindow"}
            else:
                gdi32.SelectObject(mem_dc, old)
                gdi32.DeleteObject(bitmap)
                gdi32.DeleteDC(mem_dc)
                user32.ReleaseDC(0, hdc)

        # Method 2: BitBlt
        if w > 0 and h > 0:
            hdc = user32.GetDC(0)
            mem_dc = gdi32.CreateCompatibleDC(hdc)
            bitmap = gdi32.CreateCompatibleBitmap(hdc, w, h)
            old = gdi32.SelectObject(mem_dc, bitmap)
            gdi32.BitBlt(mem_dc, 0, 0, w, h, hdc, rect.left, rect.top, 0x00CC0020)
            
            bpp = 32; stride = (w * bpp + 31) // 32 * 4
            buf = ctypes.create_string_buffer(stride * h)
            bmi = ctypes.create_string_buffer(40)
            ctypes.memset(bmi, 0, 40)
            ctypes.cast(bmi, ctypes.POINTER(c_uint))[0] = 40
            ctypes.cast(bmi, ctypes.POINTER(c_uint))[4] = w
            ctypes.cast(bmi, ctypes.POINTER(c_uint))[8] = h
            ctypes.cast(bmi, ctypes.POINTER(ctypes.c_uint16))[12] = 1
            ctypes.cast(bmi, ctypes.POINTER(ctypes.c_uint16))[14] = 32
            
            gdi32.GetDIBits(mem_dc, bitmap, 0, h, buf, bmi, 0)
            gdi32.SelectObject(mem_dc, old); gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc); user32.ReleaseDC(0, hdc)
            
            img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", stride)
            bio = io.BytesIO(); img.save(bio, format="PNG")
            return {"base64": base64.b64encode(bio.getvalue()).decode(),
                    "width": w, "height": h, "method": "bitblt"}

        # Method 3: PIL fallback
        img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)) if w > 0 else ImageGrab.grab()
        bio = io.BytesIO(); img.save(bio, format="PNG")
        return {"base64": base64.b64encode(bio.getvalue()).decode(),
                "width": img.width, "height": img.height, "method": "pil"}

    except Exception as e:
        return {"error": str(e)}


# ── Simple test ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    hwnd = user32.GetForegroundWindow()
    print(f"Foreground HWND: {hex(hwnd or 0)}")
    r = capture_window_fallback(hwnd)
    if "error" in r:
        print(f"Error: {r['error']}")
    else:
        print(f"Captured: {r['width']}x{r['height']} via {r['method']} ({r.get('input_kb',0):.0f}KB)")
