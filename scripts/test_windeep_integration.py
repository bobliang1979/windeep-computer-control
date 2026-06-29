#!/usr/bin/env python3
"""Integration test: full Notepad workflow regression for WindEep v3.3."""
import sys, os, subprocess, time, json, ctypes, uuid

ROOT = r"C:\Users\10074\Desktop\_Projects\电脑控制\windeep"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PASS, FAIL = 0, 0
def test(name, fn):
    global PASS, FAIL
    t0 = time.perf_counter()
    try:
        fn()
        PASS += 1
        dt = (time.perf_counter() - t0) * 1000
        print(f"  [OK] {name} ({dt:.0f}ms)")
    except Exception as e:
        FAIL += 1
        dt = (time.perf_counter() - t0) * 1000
        print(f"  [X] {name} ({dt:.0f}ms): {e}")

def find_hwnd(pid):
    from ctypes import byref, c_ulong, c_bool, c_int
    user32 = ctypes.windll.user32
    ws = []
    def cb(h, _):
        po = c_ulong()
        user32.GetWindowThreadProcessId(h, byref(po))
        if po.value == pid: ws.append(h)
        return 1
    user32.EnumWindows(ctypes.WINFUNCTYPE(c_bool, c_int, c_int)(cb), 0)
    return ws[0] if ws else None

# Setup
u = ctypes.windll.user32
assert u.GetSystemMetrics(80) >= 2, "Need 2+ screens"
subprocess.run(['taskkill', '/f', '/im', 'notepad.exe'], capture_output=True)
time.sleep(0.5)

print(f"=== WindEep v3.3 Integration Test ===  "
      f"{u.GetSystemMetrics(80)} screens  "
      f"{u.GetSystemMetrics(78)}x{u.GetSystemMetrics(79)}\n")

# Import
from computer_control_enhanced import (
    _check_routing_integrity, _get_route_status,
    _uia_com_invoke, _uia_com_findall,
    _enqueue_verify, _get_verify_result,
)
from scripts.sendinput import send_click, send_type_text, send_hotkey

# Test 1: System health
test("路由完整性", lambda: _check_routing_integrity()["ok"])
test("能量状态", lambda: _get_route_status()["energy_ok"])
test("无锁定路由", lambda: not _get_route_status()["locked_routes"])

# Test 2: UIA COM object creation
import comtypes
from comtypes.client import CreateObject
from comtypes.gen import UIAutomationClient as UIA
test("UIA COM对象创建", lambda: CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation))

# Test 3: Launch Notepad
p = subprocess.Popen(['notepad.exe'])
time.sleep(1)
hwnd = find_hwnd(p.pid)
test("启动记事本", lambda: hwnd is not None)

# Test 4: SendInput (after Notepad is up, safe coords)
test("SendInput点击", lambda: send_click(100, 100)["ok"])
test("SendInput热键", lambda: send_hotkey(["ctrl"], "t")["ok"])

# Test 5: UIA COM type text
r = _uia_com_invoke(pid=p.pid, automation_id="15", hwnd=hwnd)
test("UIA COM输入文本", lambda: r.get("ok"))

# Test 6: UIA COM expand menu
r = _uia_com_invoke(pid=p.pid, element_name="文件(F)", hwnd=hwnd)
test("UIA COM展开菜单", lambda: r.get("ok"))
time.sleep(0.3)

# Test 7: UIA COM findall
items = _uia_com_findall(pid=p.pid, control_type=50011, hwnd=hwnd)
test("UIA COM查找菜单项", lambda: len(items) > 5)

# Test 8: UIA COM save
r = _uia_com_invoke(pid=p.pid, element_name="保存(S)", hwnd=hwnd)
test("UIA COM保存文件", lambda: r.get("ok"))
time.sleep(0.5)

# Test 9: Async verify
tid = str(uuid.uuid4())
_enqueue_verify(pid=p.pid, window_id=hwnd, settle_ms=500,
                expected={"text_substring": "保存"}, task_id=tid)
time.sleep(1.5)
vr = _get_verify_result(tid)
test("异步验证", lambda: vr and vr.get("status") in ("passed","skipped"))

# Test 10: Cleanup
subprocess.run(['taskkill', '/f', '/pid', str(p.pid)], capture_output=True)
test("结束进程", lambda: True)

# Results
total = PASS + FAIL
print(f"\n{'='*40}")
print(f"结果: {PASS}/{total} 通过 ({PASS/total*100:.0f}%)")
sys.exit(0 if FAIL == 0 else 1)
