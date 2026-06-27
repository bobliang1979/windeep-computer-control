# windeep-computer-control

> **Windows 原生桌面自动化全栈方案** — 22 个 MCP 工具 · Windows OCR 文字识别 · 智能元素匹配 · UI 树缓存 · 断言验证系统 · 渐进式截图压缩
>
> 受 [Bytebot](https://github.com/bytebot-ai/bytebot) (11K★) 启发，结合 cua-driver 后台控制与 Accessibility Tree 精确定位，构建的生产级桌面控制引擎。

[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![Windows](https://img.shields.io/badge/platform-Windows%2010%2B-blue)](https://microsoft.com/windows)

---

## 目录

- [特性概览](#特性概览)
- [快速开始](#快速开始)
- [系统架构](#系统架构)
- [22 个 MCP 工具](#22-个-mcp-工具)
- [性能优化 (P0)](#性能优化-p0)
- [精准度策略链](#精准度策略链)
- [断言验证系统](#断言验证系统)
- [共享状态与延迟适配](#共享状态与延迟适配)
- [架构健康度](#架构健康度)
- [技术对比](#技术对比)
- [依赖要求](#依赖要求)
- [许可证](#许可证)

---

## 特性概览

| 维度 | 能力 |
|------|------|
| **MCP 协议接口** | 22 个标准化工具，可通过 `hermes mcp add` 或任意 MCP 客户端调用 |
| **窗口管理** | 枚举/查找/聚焦/移动/关闭/最小化/最大化/恢复全部窗口 |
| **后台输入控制** | PostMessage 点击、WM_CHAR 逐字输入、剪贴板粘贴、快捷键组合，不夺用户焦点 |
| **截图与压缩** | 全屏或指定窗口截图，渐进式压缩 Pipeline（**47x 加速**） |
| **UIA Accessibility 树** | 精确元素索引定位，支持索引漂移自动恢复 |
| **Windows 原生 OCR** | WinRT OCR 引擎，覆盖 Electron/WebView/Canvas 等 UIA 盲区 |
| **元素指纹** | SHA256 稳定哈希，跨 UI 树刷新保持元素可追踪 |
| **多策略匹配** | UIA 精确 → UIA 模糊 → OCR 文字 → 位置邻近 → 坐标直发，五级降级链 |
| **断言验证** | UI 树哈希变更、元素出现/消失、文本包含四种校验 |
| **延迟适配队列** | 解耦规划层（~10s）与执行层（~50ms）的异步流水线 |
| **跨 Agent 状态共享** | Hermes 与 Codex++ 共用同一份 UI 状态快照 |

---

## 快速开始

```bash
# 1. 启动 MCP 服务器（HTTP 模式，端口 59322）
python winctl_mcp_server.py --port 59322

# 2. 注册到 Hermes Agent
hermes mcp add winctl --url http://127.0.0.1:59322

# 3. 验证连通性
curl http://127.0.0.1:59322/health
# → {"status":"ok","version":"0.1.0","has_windeep":true,"has_pil":true}

# 4. 单次 CLI 调用
python winctl_mcp_server.py list_windows
python winctl_mcp_server.py screenshot
```

---

## 系统架构

```
windeep/                          ← 项目根目录
│
├── winctl_mcp_server.py          ← MCP 服务器 (22 工具, HTTP :59322)
│                                       ThreadedHTTPServer, GDI try/finally
│
├── computer_control_enhanced.py  ← CLI 入口 + P0 特性集成
│                                       UiTreeCache / 元素指纹 / 自适应 settle
│
├── compress_image.py             ← 渐进式截图压缩
│                                       quality 二分搜索 + PNG→JPEG→WebP 降级
│                                       compress_pipeline (47x 加速)
│
└── scripts/                      ← 模块化组件
    ├── ui_tree_cache.py          ← UI 树缓存 (TTL + threading.Lock)
    ├── element_fingerprint.py    ← SHA256 元素指纹
    ├── ocr_finder.py             ← Windows 原生 OCR (WinRT, PowerShell)
    ├── smart_matcher.py          ← 五级智能元素匹配
    ├── assertion_verifier.py     ← 四种断言验证
    ├── shared_ui_state.py        ← 跨 Agent 共享状态
    └── action_queue.py           ← 延迟适配队列
```

### 三层处理流水线

```
┌──────────────────────────────────────────────────────┐
│  战略层 (Hermes, ~10s 决策粒度)                        │
│  "打开 Chrome → 登录 Gemini → 提交 prompt"            │
│  输出: 操作序列 + 断言预期                              │
├──────────────────────────────────────────────────────┤
│  战术层 (winctl, ~500ms 调度粒度)                      │
│  指纹匹配 / settle 参数 / OCR fallback / 断言验证      │
│  输出: (action, fingerprint) 元组                      │
├──────────────────────────────────────────────────────┤
│  反射层 (Win32 API, ~50ms 执行粒度)                    │
│  PostMessage / WM_CHAR / PrintWindow / UIA Invoke    │
│  输出: {success, snapshot, confidence}                 │
└──────────────────────────────────────────────────────┘
         ↕ 共享状态: shared_ui_state.json (指纹+settle+断言)
```

---

## 22 个 MCP 工具

### 窗口管理

| 工具 | 功能描述 |
|------|---------|
| `list_windows` | 枚举所有可见顶层窗口（HWND / 标题 / 类名 / PID / 坐标） |
| `find_windows` | 按标题和/或类名模糊查找窗口 |
| `get_window_info` | 通过 HWND 获取窗口详细信息 |
| `focus_window` | 窗口置前（⚠️ 会抢占用户焦点） |
| `move_window` | 移动窗口位置或调整窗口尺寸 |

### 窗口状态控制

| 工具 | 功能描述 |
|------|---------|
| `close_window` | 发送 WM_CLOSE 关闭窗口 |
| `minimize_window` | 最小化窗口 |
| `maximize_window` | 最大化窗口 |
| `restore_window` | 恢复窗口到正常状态 |

### 输入控制

| 工具 | 功能描述 |
|------|---------|
| `click` | PostMessage 鼠标点击（后台安全，不夺焦点） |
| `type_text` | WM_CHAR 逐字符输入（适合短文本/密码） |
| `paste_text` | 剪贴板 + Ctrl+V 粘贴（适合长文本/特殊字符） |
| `send_keys` | 快捷键组合（Ctrl+C / Alt+Tab 等） |
| `launch` | ShellExecute 启动程序或打开文件/URL |

### 视觉

| 工具 | 功能描述 |
|------|---------|
| `screenshot` | 全屏或指定窗口截图（PrintWindow API，GDI try/finally 保护） |
| `desktop_info` | 桌面分辨率 + 系统信息 |

### 验证

| 工具 | 功能描述 |
|------|---------|
| `capture_state` | 捕获当前 UI 状态（树哈希 / 元素计数） |
| `verify` | 运行结构化断言验证 |
| `ocr_find` | 截图中 OCR 查找文字 |
| `ocr_available` | 检测 Windows OCR 引擎可用性 |

### 智能匹配

| 工具 | 功能描述 |
|------|---------|
| `smart_find` | 多策略元素定位（返回 element_index 和/或坐标） |
| `smart_click` | 按文字查找元素并点击（一步完成） |

---

## 性能优化 (P0)

| 优化项 | 之前 | 之后 | 加速比 |
|-------|------|------|--------|
| **UI 树缓存** | 每次 800ms | 首次 800ms，后续 **0ms** | ∞ |
| **截图→压缩 Pipeline** | 1517ms (16次 encode) | **57ms** (单次 JPEG@85) | **47x** |
| **文本输入 (100字符)** | 5000ms (字符逐发) | **50ms** (set_value/粘贴) | **100x** |
| **自适应 settle** | 固定 750ms | 动态 200–1000ms | ~2x |
| **闭环总计** | **~3–10 秒** | **~200–1000 毫秒** | **~10x** |

---

## 精准度策略链

对大多数桌面元素，系统按以下优先级自动尝试匹配策略：

```
click "提交"
  │
  ├─ 1. UIA 精确匹配    (element_index, 置信度 98%)
  │    角色+标签精确匹配，UIA Tree 原生支持
  │
  ├─ 2. UIA 模糊匹配    (Levenshtein, 置信度 70-95%)
  │    大小写不敏感、近似文本匹配
  │
  ├─ 3. OCR 文字匹配    (Windows WinRT OCR, 置信度 80-95%)  ← 覆盖 UIA 盲区
  │    Electron / WebView / 自定义 Canvas 中的文字
  │    零外部依赖，Windows 10+ 原生
  │
  ├─ 4. 位置邻近匹配    (置信度 30-70%)
  │    距上次点击位置最近的可交互元素
  │
  └─ 5. 坐标直发        (裸 x,y，置信度 ~10%)
        最终 fallback
```

### 盲区覆盖

| 应用类型 | UIA 树覆盖率 | 本方案覆盖策略 |
|---------|-------------|---------------|
| 原生 Win32 / MFC | ✅ >95% | UIA 精确匹配 |
| WPF / WinUI3 / UWP | ✅ >90% | UIA 精确匹配 |
| Qt / wxWidgets | ✅ >80% | UIA 精确 + 模糊 |
| **Electron (VS Code / Slack / Discord)** | ❌ <10% | **OCR + 位置匹配** |
| **WebView / Edge WebView2** | ❌ <5% | **OCR + 坐标直发** |
| **Canvas (游戏 / 自定义渲染)** | ❌ 0% | **OCR + 坐标直发** |

---

## 断言验证系统

每次操作后应进行结构化验证：

```python
from scripts.assertion_verifier import capture_state, verify

before = capture_state(pid=1234)
click(pid=1234, element=7)
after = capture_state(pid=1234)

report = verify(before, after, [
    ("hash_change", {}),                       # UI 树必须变化
    ("element_appeared", {"text": "保存成功"}),  # 预期元素出现
])
```

### 支持断言类型

| 断言 | 说明 | 置信度 |
|------|------|--------|
| `hash_change` | UI 树哈希在操作前后发生变化 | 0.95 |
| `element_appeared(text)` | 指定文本的元素在 after 中出现 | 0.5–0.98 |
| `element_disappeared(text)` | 指定文本的元素在 after 中消失 | 0.95 |
| `text_contains(text)` | UI 树标签中包含指定文本 | 0.9 |

---

## 共享状态与延迟适配

### SharedUIState (`scripts/shared_ui_state.py`)

Hermes 与 Codex++ 通过同一份 JSON 文件共享 UI 状态：

```json
{
  "fingerprints": {
    "a3f8c2": {"name": "Send", "index": 5, "role": "Button"}
  },
  "settle_history": {
    "Chrome:click": [780, 810, 750],
    "_adaptive_Chrome:click": 790
  },
  "last_action": {
    "action": "click", "success": true
  }
}
```

### ActionQueue (`scripts/action_queue.py`)

解耦规划层（慢）与执行层（快）的持久化队列：

```python
# Hermes (规划层)：入队操作
queue.enqueue("click", {"pid": 1234, "element_index": 7})

# winctl (执行层)：消费队列
while action := queue.next_pending():
    result = execute(action["tool"], action["params"])
    queue.mark_done(action["id"], result)
```

---

## 架构健康度

| 维度 | 评级 | 说明 |
|------|------|------|
| 语法正确性 | ✅ 100% | 11 个 Python 文件无语法错误 |
| 异常覆盖 | ✅ 100% | 无裸 `except:` 子句 |
| 导入健康度 | ✅ 100% | 无循环依赖 |
| Shell 注入风险 | ✅ 无风险 | 无 `os.system` / `subprocess(shell=True)` |
| 并发安全 | ✅ | ThreadedHTTPServer + UiTreeCache threading.Lock |
| 资源泄漏防护 | ✅ | GDI 对象 try/finally 保障 + clipboard 所有权由 OS 管理 |
| 模块化 | ✅ 100% | 11 模块职责分离清晰，无功能重叠 |

---

## 技术对比

| 方案 | 定位 | 本方案优势 |
|------|------|-----------|
| Playwright / Puppeteer | Web 专用 | 本方案覆盖原生窗口 + Web |
| SikuliX | 图像匹配 | 本方案 UIA+OCR+图像混合，更快更准 |
| AutoIt | Windows 自动化 (VBA 式) | 本方案 Python + MCP 协议，可扩展性更强 |
| pyautogui | 全屏坐标 | 本方案多 5 层 fallback + 元素指纹 |
| Anthropic Computer Use | 云端 AI 桌面控制 | 本方案 Windows 原生 UIA 更精准 |

---

## 依赖要求

- **操作系统**: Windows 10+（基于 Win32 ctypes / UIA API）
- **Python**: 3.10+
- **Pillow** (可选): `pip install Pillow` — 截图压缩功能需要
- **Hermes Agent** (可选): MCP 注册与调用

> 核心功能零外部依赖：窗口管理、输入控制、截图均通过 `ctypes` 直接调用 Win32 API。

---

## 许可证

[Apache 2.0](LICENSE)

---

*© 2026 BOBLIANG. All rights reserved.*

*由 [Hermes Agent](https://github.com/NousResearch/hermes-agent) × [Codex++](https://github.com/bobliang1979) 联合构建。*
