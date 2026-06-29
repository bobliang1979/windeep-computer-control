"""SendInput wrapper — 真实鼠标/键盘事件 (替代 BM_CLICK/PostMessage)"""
import ctypes
from ctypes import wintypes

# ── Structures ──────────────────────────────────────────────────────────────
PUL = ctypes.POINTER(ctypes.c_ulong)

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUT_UNION),
    ]

# ── Constants ────────────────────────────────────────────────────────────────
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

KEYEVENTF_KEYDOWN = 0x0000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

user32 = ctypes.windll.user32

# ── Coordinate conversion ───────────────────────────────────────────────────
def _abs_coord(x: int, y: int) -> tuple:
    """Convert screen pixel coords to SendInput absolute coords (0-65535)."""
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    return (int(x * 65535 // sw), int(y * 65535 // sh))


def send_click(x: int, y: int, button: str = "left") -> dict:
    """Send real mouse click via SendInput. Works on XAML/UWP/SPA apps.

    Args:
        x, y: Screen pixel coordinates.
        button: 'left', 'right', or 'middle'
    """
    ax, ay = _abs_coord(x, y)
    flags_map = {
        "left":  (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle":(MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }
    down_flag, up_flag = flags_map.get(button, flags_map["left"])

    # Move mouse + click (3 inputs)
    inputs = (INPUT * 3)()
    
    # Input 0: Move to position (absolute)
    inputs[0].type = INPUT_MOUSE
    inputs[0].union.mi = MOUSEINPUT(ax, ay, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)
    
    # Input 1: Button down
    inputs[1].type = INPUT_MOUSE
    inputs[1].union.mi = MOUSEINPUT(ax, ay, 0, down_flag, 0, None)
    
    # Input 2: Button up
    inputs[2].type = INPUT_MOUSE
    inputs[2].union.mi = MOUSEINPUT(ax, ay, 0, up_flag, 0, None)

    sent = user32.SendInput(3, inputs, ctypes.sizeof(INPUT))
    return {"ok": sent == 3, "method": f"sendinput_{button}", "x": x, "y": y}


def send_type_text(text: str, delay_ms: int = 10) -> dict:
    """Type text via SendInput keybd events (real keyboard events).

    Works on XAML/UWP/SPA apps where PostMessage WM_CHAR is ignored.
    """
    # Convert string to Unicode keystrokes
    inputs_list = []
    for char in text:
        vk = ctypes.windll.user32.VkKeyScanW(ord(char))
        scan = ctypes.windll.user32.MapVirtualKeyW(vk & 0xFF, 0)
        
        # Key down
        ki_down = INPUT()
        ki_down.type = INPUT_KEYBOARD
        ki_down.union.ki = KEYBDINPUT(vk & 0xFF, scan, KEYEVENTF_KEYDOWN, 0, None)
        inputs_list.append(ki_down)
        
        # Key up
        ki_up = INPUT()
        ki_up.type = INPUT_KEYBOARD
        ki_up.union.ki = KEYBDINPUT(vk & 0xFF, scan, KEYEVENTF_KEYUP, 0, None)
        inputs_list.append(ki_up)

    # Send all at once
    arr = (INPUT * len(inputs_list))()
    for i, inp in enumerate(inputs_list):
        arr[i] = inp
    
    sent = user32.SendInput(len(inputs_list), arr, ctypes.sizeof(INPUT))
    return {"ok": sent == len(inputs_list), "chars": len(text), "method": "sendinput_type"}


def send_hotkey(modifiers: list, key: str) -> dict:
    """Send keyboard shortcut via SendInput (e.g. Ctrl+S, Alt+F4).

    Args:
        modifiers: List of modifier names: 'ctrl', 'alt', 'shift', 'win'
        key: Virtual key code or character
    """
    VK_MAP = {
        'ctrl': 0x11, 'alt': 0x12, 'shift': 0x10, 'win': 0x5B,
        's': 0x53, 'f': 0x46, 'a': 0x41, 'c': 0x43, 'v': 0x56,
        'x': 0x58, 'z': 0x5A, 'y': 0x59, 'n': 0x4E, 'o': 0x4F,
        'p': 0x50, 't': 0x54, 'w': 0x57, 'esc': 0x1B, 'return': 0x0D,
        'tab': 0x09, 'space': 0x20, 'delete': 0x2E, 'backspace': 0x08,
        'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
        'f4': 0x73, 'f5': 0x74,
    }
    mod_vks = [VK_MAP.get(m.lower(), 0) for m in modifiers]
    key_vk = VK_MAP.get(key.lower(), ord(key[0].upper()) if key else 0)

    # Build input sequence
    inputs_list = []
    
    # Modifiers down
    for vk in mod_vks:
        ki = INPUT(); ki.type = INPUT_KEYBOARD
        ki.union.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYDOWN, 0, None)
        inputs_list.append(ki)
    
    # Key down + up
    ki = INPUT(); ki.type = INPUT_KEYBOARD
    ki.union.ki = KEYBDINPUT(key_vk, 0, KEYEVENTF_KEYDOWN, 0, None)
    inputs_list.append(ki)
    ki = INPUT(); ki.type = INPUT_KEYBOARD
    ki.union.ki = KEYBDINPUT(key_vk, 0, KEYEVENTF_KEYUP, 0, None)
    inputs_list.append(ki)
    
    # Modifiers up (reverse order)
    for vk in reversed(mod_vks):
        ki = INPUT(); ki.type = INPUT_KEYBOARD
        ki.union.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None)
        inputs_list.append(ki)

    arr = (INPUT * len(inputs_list))()
    for i, inp in enumerate(inputs_list):
        arr[i] = inp
    
    sent = user32.SendInput(len(inputs_list), arr, ctypes.sizeof(INPUT))
    return {"ok": sent == len(inputs_list), "method": "sendinput_hotkey"}
