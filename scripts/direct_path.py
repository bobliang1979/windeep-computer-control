"""direct_path.py — 直连执行通道 (Skip MCP stack for simple ops)

Provides a localhost socket server that accepts simple commands
and executes them directly via SendInput. Bypasses the entire
MCP/routing/verification stack for sub-millisecond latency.

Designed after Computer Use plugin's named pipe architecture.

Port: 59324 (WindEep direct path)

Protocol: Send a single line, get JSON back.
  "click x y"       → send_click(x, y)
  "type text"       → send_type_text(text)
  "hotkey mod key"  → send_hotkey([mod], key)
  "ping"            → health check
  "capture path"    → save screenshot to path (returns base64)

Response: {"ok": true, "ms": 0.3, ...}
"""
import json, socket, threading, time, base64, io, struct
from typing import Optional

PORT = 59324

# Lazy import SendInput to avoid circular deps
_si = None
def _ensure_si():
    global _si
    if _si is None:
        from scripts.sendinput import send_click, send_type_text, send_hotkey, user32
        _si = {"click": send_click, "type": send_type_text, "hotkey": send_hotkey}

def _handle(line: str) -> str:
    """Parse and execute a single command. Returns JSON string."""
    t0 = time.perf_counter()
    line = line.strip()
    parts = line.split(maxsplit=2) if line else []
    if not parts:
        return '{"ok":false,"error":"empty"}'

    cmd = parts[0].lower()
    _ensure_si()

    try:
        if cmd == "ping":
            result = {"ok": True, "pong": True}

        elif cmd == "click" and len(parts) >= 3:
            x, y = int(parts[1]), int(parts[2])
            result = _si["click"](x, y)

        elif cmd == "type" and len(parts) >= 2:
            text = parts[1]
            result = _si["type"](text)

        elif cmd == "hotkey" and len(parts) >= 3:
            mods = [m.strip() for m in parts[1].split(",") if m.strip()]
            key = parts[2]
            result = _si["hotkey"](mods, key)

        elif cmd == "capture":
            path = parts[1] if len(parts) >= 2 else ""
            try:
                import mss
                with mss.mss() as sct:
                    monitors = sct.monitors
                    idx = min(1, len(monitors) - 1)
                    img = sct.grab(monitors[idx])
                    from PIL import Image
                    pil = Image.frombytes("RGB", img.size, img.rgb)
                    buf = io.BytesIO()
                    pil.save(buf, format="PNG")
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    result = {"ok": True, "width": img.size[0], "height": img.size[1],
                              "base64": b64, "format": "png"}
                    if path:
                        pil.save(path)
                        result["path"] = path
            except Exception as e:
                result = {"ok": False, "error": f"capture: {e}"}

        else:
            result = {"ok": False, "error": f"unknown: {cmd}"}

    except Exception as e:
        result = {"ok": False, "error": str(e)}

    dt = (time.perf_counter() - t0) * 1000
    result["ms"] = round(dt, 1)
    return json.dumps(result, ensure_ascii=False)


class DirectPathServer:
    """Single-threaded localhost socket server for low-latency commands."""

    def __init__(self, port: int = PORT):
        self.port = port
        self._server = None
        self._thread = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", self.port))
        self._server.listen(5)
        self._server.settimeout(1.0)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while self._running:
            try:
                conn, addr = self._server.accept()
                with conn:
                    data = conn.recv(4096)
                    if not data:
                        continue
                    cmd = data.decode("utf-8", errors="replace").strip()
                    response = _handle(cmd)
                    conn.sendall(response.encode("utf-8"))
            except socket.timeout:
                continue
            except Exception:
                pass

    def stop(self):
        self._running = False
        if self._server:
            self._server.close()
            self._server = None


# ── Convenience: call the server from any process ──
def call(cmd: str, timeout: float = 5.0) -> dict:
    """Send a command to the direct path server and return parsed JSON."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("127.0.0.1", PORT))
        s.sendall(cmd.encode("utf-8"))
        resp = s.recv(65536).decode("utf-8")
        s.close()
        return json.loads(resp)
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── CLI test ──
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # Client mode
        result = call(" ".join(sys.argv[1:]))
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Server mode
        svr = DirectPathServer()
        svr.start()
        print(f"DirectPath server on :{PORT}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            svr.stop()
            print("stopped")
