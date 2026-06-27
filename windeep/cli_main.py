"""
windeep CLI — Windows Deep Desktop Control.

Subcommands:
  windows   List all visible windows
  focus     Focus a window by title
  move      Move/resize a window
  keys      Send text to a window
  exec      Launch a program
  reg       Read a registry key
  find      Find windows by pattern
  close     Close a window
  click     Click at coordinates in a window
  info      Show detailed window info
  sendinput Send keystrokes globally via SendInput
  desktop   Show desktop resolution
"""

import argparse
import json
import sys
import time

from windeep import __version__
from windeep.core import (
    WinDeep, list_windows, find_windows, get_window_info,
    focus_window, move_window, send_keys, send_input_keys,
    exec_process, read_registry, close_window, window_click,
    get_desktop_resolution, minimize_window, maximize_window,
    restore_window,
)


def _eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def _print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def cmd_windows(args):
    """List windows."""
    wins = list_windows()
    visible_only = not args.all

    if args.title:
        lower = args.title.lower()
        wins = [w for w in wins if lower in w.title.lower()]
    if args.class_name:
        lower = args.class_name.lower()
        wins = [w for w in wins if lower in w.class_name.lower()]

    if visible_only:
        wins = [w for w in wins if w.visible]

    if args.json:
        _print_json([w.to_dict() for w in wins])
        return

    total = len(wins)
    _eprint(f"Found {total} window(s):\n")

    for w in wins[:args.limit or total]:
        rect = w.rect
        dims = f"{rect[2]-rect[0]}x{rect[3]-rect[1]}"
        pos = f"@({rect[0]},{rect[1]})"
        state = ""
        if w.minimized: state += " [MIN]"
        if w.maximized: state += " [MAX]"
        if not w.visible: state += " [HIDDEN]"

        title = w.title or "(no title)"
        print(f"  HWND {w.hwnd:8d} | {title[:50]:50s} | {dims:10s} {pos:16s}{state}")
        if args.verbose:
            print(f"          Class: {w.class_name}  PID: {w.pid}")

    print()
    _eprint(f"Tip: use `windeep focus {wins[0].hwnd if wins else '<hwnd>'}` to focus a window")


def cmd_focus(args):
    """Focus a window."""
    hwnd = args.hwnd
    if not hwnd:
        matches = find_windows(title_pattern=args.title, visible_only=True)
        if not matches:
            _eprint(f"❌ No window found matching: {args.title}")
            sys.exit(1)
        hwnd = matches[0].hwnd
        _eprint(f"  Found: HWND {hwnd} — '{matches[0].title[:40]}'")

    ok = focus_window(hwnd)
    if ok:
        info = get_window_info(hwnd)
        _eprint(f"✅ Focused HWND {hwnd} — '{info.title if info else ''}'")
    else:
        _eprint(f"❌ Failed to focus HWND {hwnd}")
        sys.exit(1)


def cmd_move(args):
    """Move/resize a window."""
    hwnd = args.hwnd
    if not hwnd:
        matches = find_windows(title_pattern=args.title, visible_only=True)
        if not matches:
            _eprint(f"❌ No window found matching: {args.title}")
            sys.exit(1)
        hwnd = matches[0].hwnd
        _eprint(f"  Found: HWND {hwnd} — '{matches[0].title[:40]}'")

    # Use -1 for "keep current"
    x = args.x if args.x is not None else -1
    y = args.y if args.y is not None else -1
    w = args.w if args.w is not None else 0
    h = args.height if args.height is not None else 0

    ok = move_window(hwnd, x, y, w, h)
    if ok:
        info = get_window_info(hwnd)
        if info:
            r = info.rect
            _eprint(f"✅ Moved HWND {hwnd} to ({r[0]},{r[1]}) {r[2]-r[0]}x{r[3]-r[1]}")
    else:
        _eprint(f"❌ Failed to move HWND {hwnd}")
        sys.exit(1)


def cmd_keys(args):
    """Send text to a window."""
    hwnd = args.hwnd
    if not hwnd:
        matches = find_windows(title_pattern=args.title, visible_only=True)
        if not matches:
            _eprint(f"❌ No window found matching: {args.title}")
            sys.exit(1)
        hwnd = matches[0].hwnd
        _eprint(f"  Found: HWND {hwnd} — '{matches[0].title[:40]}'")

    text = args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()

    ok = send_keys(hwnd, text)
    if ok:
        _eprint(f"✅ Sent {len(text)} chars to HWND {hwnd}")
        if args.json:
            _print_json({"hwnd": hwnd, "chars": len(text), "text": text[:100]})
    else:
        _eprint(f"❌ Failed to send keys")
        sys.exit(1)


def cmd_sendinput(args):
    """Send keystrokes globally."""
    text = args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    n = send_input_keys(text)
    _eprint(f"✅ Sent {len(text)} chars ({n} events)")


def cmd_exec(args):
    """Launch a program."""
    ok = exec_process(args.path, args.args)
    if ok:
        _eprint(f"✅ Launched: {args.path}")
    else:
        _eprint(f"❌ Failed to launch: {args.path}")
        sys.exit(1)


def cmd_reg(args):
    """Read a registry key."""
    result = read_registry(args.path, args.value)
    # Format based on type
    if args.json:
        _print_json(result)
        return

    if "error" in result:
        _eprint(f"❌ {result['error']}")
        sys.exit(1)

    val = result.get("value", "")
    print(f"  Path:  {result['path']}")
    print(f"  Name:  {result['value_name']}")
    print(f"  Type:  {result['type']}")
    print(f"  Value: {val}")


def cmd_find(args):
    """Find windows by pattern."""
    wins = find_windows(
        title_pattern=args.title or "",
        class_pattern=args.class_name or "",
        visible_only=not args.hidden,
    )

    if args.json:
        _print_json([w.to_dict() for w in wins])
        return

    if not wins:
        _eprint(f"❌ No windows found")
        sys.exit(1)

    _eprint(f"Found {len(wins)} window(s):\n")
    for w in wins[:args.limit or len(wins)]:
        r = w.rect
        print(f"  HWND {w.hwnd:8d} | Title: {w.title[:50]:50s}")
        print(f"           Class: {w.class_name:30s} PID: {w.pid}")
        print(f"           Rect: ({r[0]},{r[1]})-({r[2]},{r[3]})  Visible: {w.visible}")
        print()


def cmd_close(args):
    """Close a window."""
    hwnd = args.hwnd
    if not hwnd:
        matches = find_windows(title_pattern=args.title, visible_only=True)
        if not matches:
            _eprint(f"❌ No window found matching: {args.title}")
            sys.exit(1)
        hwnd = matches[0].hwnd
        _eprint(f"  Found: HWND {hwnd} — '{matches[0].title[:40]}'")

    ok = close_window(hwnd)
    if ok:
        _eprint(f"✅ Close sent to HWND {hwnd}")
    else:
        _eprint(f"❌ Failed to close HWND {hwnd}")
        sys.exit(1)


def cmd_click(args):
    """Click at coordinates in a window."""
    hwnd = args.hwnd
    if not hwnd:
        matches = find_windows(title_pattern=args.title, visible_only=True)
        if not matches:
            _eprint(f"❌ No window found matching: {args.title}")
            sys.exit(1)
        hwnd = matches[0].hwnd
        _eprint(f"  Found: HWND {hwnd} — '{matches[0].title[:40]}'")

    ok = window_click(hwnd, args.x, args.y, args.button)
    if ok:
        _eprint(f"✅ Click ({args.x},{args.y}) sent to HWND {hwnd}")
        if args.json:
            _print_json({"hwnd": hwnd, "x": args.x, "y": args.y, "button": args.button})
    else:
        _eprint(f"❌ Failed to click HWND {hwnd}")
        sys.exit(1)


def cmd_info(args):
    """Show window info."""
    hwnd = args.hwnd
    if not hwnd:
        matches = find_windows(title_pattern=args.title, visible_only=True)
        if not matches:
            _eprint(f"❌ No window found matching: {args.title}")
            sys.exit(1)
        hwnd = matches[0].hwnd

    info = get_window_info(hwnd)
    if not info:
        _eprint(f"❌ Window HWND {hwnd} not found")
        sys.exit(1)

    if args.json:
        _print_json(info.to_dict())
        return

    r = info.rect
    print(f"  HWND:       {info.hwnd}")
    print(f"  Title:      {info.title}")
    print(f"  Class:      {info.class_name}")
    print(f"  PID:        {info.pid}")
    print(f"  Visible:    {info.visible}")
    print(f"  Minimized:  {info.minimized}")
    print(f"  Maximized:  {info.maximized}")
    print(f"  Position:   ({r[0]},{r[1]})")
    print(f"  Size:       {r[2]-r[0]}x{r[3]-r[1]}")


def cmd_desktop(args):
    """Show desktop resolution."""
    w, h = get_desktop_resolution()
    info = {"width": w, "height": h}
    if args.json:
        _print_json(info)
    else:
        _eprint(f"Desktop: {w}x{h}")
        _eprint(f"Pixels:  {w*h:,}")


def cmd_state(args):
    """Show/minimize/maximize/restore window state."""
    hwnd = args.hwnd
    if not hwnd:
        matches = find_windows(title_pattern=args.title, visible_only=True)
        if not matches:
            _eprint(f"❌ No window found matching: {args.title}")
            sys.exit(1)
        hwnd = matches[0].hwnd

    action = args.action
    if action == "minimize":
        ok = minimize_window(hwnd)
    elif action == "maximize":
        ok = maximize_window(hwnd)
    elif action == "restore":
        ok = restore_window(hwnd)
    else:
        _eprint(f"Unknown action: {action}")
        sys.exit(1)

    if ok:
        _eprint(f"✅ {action.capitalize()} HWND {hwnd}")
    else:
        _eprint(f"❌ Failed to {action} HWND {hwnd}")
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="windeep",
        description="Windows Deep Desktop Control — pure ctypes Win32 API. "
                    "Zero external dependencies.",
        epilog="Examples:\n"
               "  windeep windows                          # list visible windows\n"
               "  windeep windows --all                      # list ALL windows\n"
               "  windeep find --title Notepad                # find Notepad windows\n"
               "  windeep focus --title Notepad              # focus Notepad\n"
               "  windeep info 0x1234                        # window details\n"
               "  windeep move 0x1234 --x 100 --y 100 --w 800 --h 600\n"
               "  windeep keys 0x1234 --text 'Hello World'\n"
               "  windeep click 0x1234 --x 100 --y 200\n"
               "  windeep exec notepad.exe\n"
               "  windeep reg 'HKCU\\\\Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Explorer'\n"
               "  windeep sendinput --text 'Hello'\n"
               "  windeep desktop\n"
               "  windeep state 0x1234 minimize",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    p.add_argument("--json", "-j", action="store_true", help="JSON output")

    sub = p.add_subparsers(dest="command")

    # windows
    wp = sub.add_parser("windows", aliases=["w", "list"],
                        help="List all visible windows")
    wp.add_argument("--all", "-a", action="store_true", help="Include hidden windows")
    wp.add_argument("--title", "-t", default="", help="Filter by title substring")
    wp.add_argument("--class-name", "-c", default="", help="Filter by class name substring")
    wp.add_argument("--limit", "-n", type=int, default=50, help="Max results")

    # find
    fp = sub.add_parser("find", aliases=["f", "search"],
                        help="Find windows by pattern")
    fp.add_argument("--title", "-t", default="", help="Title substring")
    fp.add_argument("--class-name", "-c", default="", help="Class name substring")
    fp.add_argument("--hidden", action="store_true", help="Include hidden windows")
    fp.add_argument("--limit", "-n", type=int, default=20, help="Max results")

    # info
    ip = sub.add_parser("info", aliases=["i"],
                        help="Show detailed window info")
    ip.add_argument("hwnd", type=lambda s: int(s, 0), nargs="?",
                    default=0, help="Window handle (hex or decimal)")
    ip.add_argument("--title", "-t", default="", help="Find window by title")

    # focus
    fp2 = sub.add_parser("focus", aliases=["fg"],
                         help="Bring window to foreground")
    fp2.add_argument("hwnd", type=lambda s: int(s, 0), nargs="?",
                     default=0, help="Window handle")
    fp2.add_argument("--title", "-t", default="", help="Find window by title")

    # move
    mp = sub.add_parser("move", aliases=["mv"],
                        help="Move/resize window")
    mp.add_argument("hwnd", type=lambda s: int(s, 0), nargs="?",
                    default=0, help="Window handle")
    mp.add_argument("--title", "-t", default="", help="Find window by title")
    mp.add_argument("--x", type=int, default=None, help="X position (-1 = keep)")
    mp.add_argument("--y", type=int, default=None, help="Y position (-1 = keep)")
    mp.add_argument("--w", "-w", type=int, default=0, help="Width (0 = keep)")
    mp.add_argument("--height", type=int, default=0, help="Height (0 = keep)")

    # keys
    kp = sub.add_parser("keys", aliases=["k", "send"],
                        help="Send text to a window")
    kp.add_argument("hwnd", type=lambda s: int(s, 0), nargs="?",
                    default=0, help="Window handle")
    kp.add_argument("--title", "-t", default="", help="Find window by title")
    kp.add_argument("--text", "-s", default="", help="Text to send")
    kp.add_argument("--file", "-f", default="", help="Read text from file")

    # sendinput
    si = sub.add_parser("sendinput", aliases=["si"],
                        help="Send keystrokes globally via SendInput")
    si.add_argument("--text", "-s", default="", help="Text to type")
    si.add_argument("--file", "-f", default="", help="Read text from file")

    # exec
    ep = sub.add_parser("exec", aliases=["e", "run", "launch"],
                        help="Launch a program")
    ep.add_argument("path", help="Executable path or name")
    ep.add_argument("args", nargs="?", default="", help="Command line arguments")

    # reg
    rp = sub.add_parser("reg", aliases=["registry"],
                        help="Read registry key")
    rp.add_argument("path", help="Registry path, e.g. 'HKCU\\Software\\Microsoft'")
    rp.add_argument("--value", "-v", default="", help="Value name (default = '(default)')")

    # click
    cp = sub.add_parser("click", aliases=["clk"],
                        help="Click at coordinates in a window")
    cp.add_argument("hwnd", type=lambda s: int(s, 0), nargs="?",
                    default=0, help="Window handle")
    cp.add_argument("--title", "-t", default="", help="Find window by title")
    cp.add_argument("x", type=int, help="X coordinate (relative to window)")
    cp.add_argument("y", type=int, help="Y coordinate (relative to window)")
    cp.add_argument("--button", "-b", default="left",
                    choices=["left", "right"], help="Mouse button")

    # close
    clp = sub.add_parser("close", aliases=["kill"],
                         help="Send close message to a window")
    clp.add_argument("hwnd", type=lambda s: int(s, 0), nargs="?",
                     default=0, help="Window handle")
    clp.add_argument("--title", "-t", default="", help="Find window by title")

    # desktop
    dp = sub.add_parser("desktop", aliases=["d", "screen"],
                        help="Show desktop resolution")
    dp.add_argument("--json", "-j", action="store_true", help="JSON output")

    # state
    sp = sub.add_parser("state", aliases=["st"],
                        help="Change window state (minimize/maximize/restore)")
    sp.add_argument("hwnd", type=lambda s: int(s, 0), nargs="?",
                    default=0, help="Window handle")
    sp.add_argument("--title", "-t", default="", help="Find window by title")
    sp.add_argument("action", choices=["minimize", "maximize", "restore"],
                    help="Action to perform")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "windows": cmd_windows,
        "find": cmd_find,
        "info": cmd_info,
        "focus": cmd_focus,
        "move": cmd_move,
        "keys": cmd_keys,
        "sendinput": cmd_sendinput,
        "exec": cmd_exec,
        "reg": cmd_reg,
        "click": cmd_click,
        "close": cmd_close,
        "desktop": cmd_desktop,
        "state": cmd_state,
    }

    handler = dispatch.get(args.command)
    if handler:
        try:
            handler(args)
        except KeyboardInterrupt:
            _eprint("\n  ⚠️  Interrupted")
            sys.exit(130)
        except Exception as e:
            _eprint(f"❌ Error: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
