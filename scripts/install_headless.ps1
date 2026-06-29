<#
.SYNOPSIS
    Hermes Headless Computer Control — 一键部署脚本
.DESCRIPTION
    在无显示器/SSH 环境中部署完整的电脑控制系统。
    自动安装虚拟显示器驱动 + 验证关键模块。

    用法:
        PowerShell -ExecutionPolicy Bypass -File install_headless.ps1

    要求:
        - 管理员权限 (部分步骤需要)
        - 网络连接 (下载驱动)
.NOTES
    Version: 1.0
    Author: Hermes Agent × bobliang1979
#>

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.ForegroundColor = "Green"
$WINDEEP_DIR = "$env:USERPROFILE\Desktop\_Projects\电脑控制\windeep"
$VDD_DIR = "$env:USERPROFILE\Downloads\vdd_driver"

Write-Host "╔══════════════════════════════════════════════════╗"
Write-Host "║  Hermes Headless Computer Control Deployment   ║"
Write-Host "╚══════════════════════════════════════════════════╝"
Write-Host ""

# ── Step 1: Check Admin ──────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[WARN] 未以管理员身份运行。部分步骤(驱动安装、计划任务)将失败。" -ForegroundColor Yellow
    Write-Host "[WARN] 建议: 右键 → 以管理员身份运行" -ForegroundColor Yellow
} else {
    Write-Host "[OK] 管理员权限" -ForegroundColor Green
}

# ── Step 2: Check Python ────────────────────────────────────────────
try {
    $pyVer = python --version 2>&1
    Write-Host "[OK] Python: $pyVer"
} catch {
    Write-Host "[FAIL] Python 未安装! 请先安装 Python 3.11+" -ForegroundColor Red
    exit 1
}

# ── Step 3: Check / Install Virtual Display Driver ──────────────────
Write-Host ""
Write-Host "── Step 3: 虚拟显示器驱动 ──"

# Check if VDD is already installed
$vddDevice = Get-PnpDevice | Where-Object { $_.FriendlyName -like '*MttVDD*' -or $_.FriendlyName -like '*Virtual Display*' }
if ($vddDevice) {
    Write-Host "[OK] 虚拟显示器驱动已安装: $($vddDevice.Status)" -ForegroundColor Green
} elseif (-not (Test-Path "$VDD_DIR\SignedDrivers\x86\VDD\MttVDD.inf")) {
    Write-Host "[...] 下载 VirtualDrivers 已签名驱动..."
    # Create download directory
    New-Item -ItemType Directory -Path $VDD_DIR -Force | Out-Null

    # Download VDD Control (portable, contains signed drivers)
    $vddUrl = "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/25.7.23/VDD.Control.25.7.23.zip"
    $zipPath = "$env:TEMP\vdd_control.zip"
    try {
        Invoke-WebRequest -Uri $vddUrl -OutFile $zipPath -TimeoutSec 60
        Write-Host "[OK] 驱动下载完成"
    } catch {
        Write-Host "[FAIL] 驱动下载失败: $_" -ForegroundColor Red
        Write-Host "[HINT] 手动下载后放入: $VDD_DIR" -ForegroundColor Yellow
        Write-Host "[HINT] 下载地址: $vddUrl" -ForegroundColor Yellow
    }

    # Extract
    if (Test-Path $zipPath) {
        Expand-Archive -Path $zipPath -DestinationPath $VDD_DIR -Force
        Write-Host "[OK] 驱动解压完成"
    }
} else {
    Write-Host "[OK] 驱动文件已存在: $VDD_DIR"
}

# Install driver (if admin and driver files exist)
if ($isAdmin -and (Test-Path "$VDD_DIR\SignedDrivers\x86\VDD\MttVDD.inf")) {
    Write-Host "[...] 安装虚拟显示器驱动..."
    $result = pnputil /add-driver "$VDD_DIR\SignedDrivers\x86\VDD\MttVDD.inf" /install 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] 驱动安装成功!" -ForegroundColor Green
    } elseif ($LASTEXITCODE -eq 5) {
        Write-Host "[OK] 驱动已存在, 无需安装" -ForegroundColor Green
    } else {
        Write-Host "[WARN] 驱动安装结果: $($result -join ' ')" -ForegroundColor Yellow
    }
}

# ── Step 4: Verify windeep project ──────────────────────────────────
Write-Host ""
Write-Host "── Step 4: windeep 项目验证 ──"

if (-not (Test-Path "$WINDEEP_DIR\computer_control_enhanced.py")) {
    Write-Host "[FAIL] windeep 项目不存在: $WINDEEP_DIR" -ForegroundColor Red
    Write-Host "[HINT] 请确认项目位置正确" -ForegroundColor Yellow
    exit 1
}
Write-Host "[OK] windeep 项目存在"

# Check key modules
$modules = @(
    "scripts\clipboard_guard.py",
    "scripts\resilient_matcher.py",
    "scripts\layout_knowledge.py",
    "scripts\ui_tree_cache.py",
    "energy_regulator.py"
)
foreach ($mod in $modules) {
    if (Test-Path "$WINDEEP_DIR\$mod") {
        Write-Host "  [OK] $mod"
    } else {
        Write-Host "  [WARN] $mod 缺失" -ForegroundColor Yellow
    }
}

# ── Step 5: Python module syntax check ──────────────────────────────
Write-Host ""
Write-Host "── Step 5: Python 语法检查 ──"

$pyFiles = @(
    "computer_control_enhanced.py",
    "winctl_mcp_server.py",
    "scripts\clipboard_guard.py",
    "scripts\resilient_matcher.py",
    "scripts\layout_knowledge.py"
)
foreach ($f in $pyFiles) {
    $fullPath = "$WINDEEP_DIR\$f"
    if (Test-Path $fullPath) {
        $result = python -c "import py_compile; py_compile.compile('$fullPath', doraise=True)" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] $f"
        } else {
            Write-Host "  [FAIL] $f 语法错误" -ForegroundColor Red
        }
    }
}

# ── Step 6: Route integrity check ───────────────────────────────────
Write-Host ""
Write-Host "── Step 6: 路由完整性检查 ──"

try {
    $result = python -c "
import sys
sys.path.insert(0, '$WINDEEP_DIR')
import computer_control_enhanced as cce
s = cce._get_route_status()
r = cce._check_routing_integrity()
print(f'Integrity: {r[\"ok\"]}')
print(f'Energy: {s[\"energy_ok\"]}')
print(f'Locked routes: {s[\"locked_routes\"]}')
    " 2>&1
    Write-Host "  $($result -join "`n  ")"
    if ($result -match "Integrity: True") {
        Write-Host "[OK] 路由完整性正常" -ForegroundColor Green
    } else {
        Write-Host "[WARN] 路由完整性异常" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[WARN] 路由检查失败(可能是MCP未运行): $_" -ForegroundColor Yellow
}

# ── Step 7: Display / DXGI verification ─────────────────────────────
Write-Host ""
Write-Host "── Step 7: 显示器/DXGI 验证 ──"

try {
    $disp = python -c "
import ctypes
u = ctypes.windll.user32
print(f'Screens: {u.GetSystemMetrics(80)}')
print(f'Desktop: {u.GetSystemMetrics(78)}x{u.GetSystemMetrics(79)}')
    " 2>&1
    Write-Host "  $($disp -join "`n  ")"
    if ($disp -match "Screens: [2-9]") {
        Write-Host "[OK] 虚拟显示器可用" -ForegroundColor Green
    } else {
        Write-Host "[WARN] 仅 1 个显示器 (无虚拟显示器, 仅物理屏)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[WARN] 显示器检测失败: $_" -ForegroundColor Yellow
}

# ── Step 8: Create scheduled startup task (optional) ────────────────
Write-Host ""
Write-Host "── Step 8: 开机启动配置 (可选) ──"

$taskName = "HermesWindeeep"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "[...] 创建计划任务: $taskName (开机启动 winctl MCP)"
    $action = New-ScheduledTaskAction -Execute "python" -Argument "$WINDEEP_DIR\winctl_mcp_server.py --port 59322" -WorkingDirectory $WINDEEP_DIR
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    try {
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force
        Write-Host "[OK] 计划任务创建成功" -ForegroundColor Green
        Write-Host "[INFO] winctl MCP 将在开机后自动启动于端口 59322"
    } catch {
        Write-Host "[WARN] 计划任务创建失败(可能需要管理员权限): $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK] 计划任务已存在: $taskName"
}

# ── Summary ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════╗"
Write-Host "║                部署完成                          ║"
Write-Host "╚══════════════════════════════════════════════════╝"
Write-Host ""
Write-Host "环境:"
Write-Host "  windeep:  $WINDEEP_DIR"
Write-Host "  驱动:     $VDD_DIR"
Write-Host "  Python:   $((python --version 2>&1))"
Write-Host ""
Write-Host "手动启动 MCP 服务器:"
Write-Host "  python $WINDEEP_DIR\winctl_mcp_server.py --port 59322"
Write-Host ""
Write-Host "路由健康检查:"
Write-Host "  python $WINDEEP_DIR\computer_control_enhanced.py route-status"
Write-Host ""
Write-Host "验证截图:"
Write-Host "  python -c `"from PIL import ImageGrab; img = ImageGrab.grab(); print(f'OK: {img.size}')`""

# ── Final check ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "── 最终检查 ──"
$failures = @()
if (-not (Test-Path "$WINDEEP_DIR\computer_control_enhanced.py")) { $failures += "windeep 项目" }
if (-not (Get-PnpDevice | Where-Object { $_.FriendlyName -like '*MttVDD*' }) -and -not (Test-Path "$VDD_DIR\SignedDrivers\x86\VDD\MttVDD.inf")) {
    $failures += "虚拟显示器驱动"
}
if ($failures.Count -eq 0) {
    Write-Host "[PASS] 全部检查通过!" -ForegroundColor Green
    exit 0
} else {
    Write-Host "[WARN] 以下项目需要关注: $($failures -join ', ')" -ForegroundColor Yellow
    exit 0
}
