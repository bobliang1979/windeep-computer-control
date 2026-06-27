"""
windeep — Windows Deep Desktop Control
=======================================

Pure ctypes Win32 API bindings for window management, keystroke injection,
registry access, and process control. Zero external dependencies.

Subcommands:
  windows    List all top-level windows
  focus      Bring a window to foreground
  move       Move/resize a window
  keys       Send keystrokes to a window
  exec       Launch an executable
  reg        Read a registry key
  find       Find windows by title/class pattern
  close      Close a window gracefully
  click      Send mouse click to window coordinates
"""

__version__ = "1.0.0"

from windeep.core import (
    WinDeep,
    WindowInfo,
    list_windows,
    focus_window,
    move_window,
    send_keys,
    exec_process,
    read_registry,
    find_windows,
    close_window,
    window_click,
    get_window_info,
)

__all__ = [
    "WinDeep", "WindowInfo", "list_windows", "focus_window",
    "move_window", "send_keys", "exec_process", "read_registry",
    "find_windows", "close_window", "window_click", "get_window_info",
]
