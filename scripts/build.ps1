# AutoFarmStation 打包脚本
#
# 注意:此文件必须保存为 UTF-8 with BOM,否则 PowerShell 5.1 会把脚本中的
# 字符串读成乱码并解析报错。
#
# 用法(在仓库根目录):
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1
#
# 产物:dist\AutoFarmStation.exe
# 可选参数:
#   -SkipClean  跳过 clean(增量打包,加快二次构建)

[CmdletBinding()]
param(
    [switch]$SkipClean = $false,
    [string]$Spec = "AutoFarmStation.spec"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# === 路径 ===
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $RepoRoot
Write-Host "[build] RepoRoot: $RepoRoot" -ForegroundColor Cyan

# === Step 0: 自检 ===
Write-Host "[build] 运行自检..." -ForegroundColor Cyan
& python scripts\selftest.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[build] 自检失败,中止打包" -ForegroundColor Red
    exit 1
}

# === Step 1: 清理 ===
if (-not $SkipClean) {
    Write-Host "[build] 清理 build/dist..." -ForegroundColor Cyan
    if (Test-Path build) { Remove-Item -Recurse -Force build }
    if (Test-Path dist) { Remove-Item -Recurse -Force dist }
}

# === Step 2: PyInstaller ===
Write-Host "[build] PyInstaller 打包(单文件,可能 3-5 分钟)..." -ForegroundColor Cyan

# 用 --name 显式指定 exe 名(覆盖 spec 中的 name)
# 用 --noconfirm 避免提示
# 用 --collect-all mss / pynput 把隐藏子模块打包进(避免漏资源)
$pyiArgs = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "AutoFarmStation",
    "--collect-all", "mss",
    "--collect-all", "pynput",
    "--collect-submodules", "pynput.keyboard",
    "--collect-submodules", "pynput.mouse",
    "--hidden-import", "PySide6.QtCore",
    "--hidden-import", "PySide6.QtGui",
    "--hidden-import", "PySide6.QtWidgets",
    "--exclude-module", "tkinter",
    "--exclude-module", "unittest",
    "--exclude-module", "pytest",
    "--paths", ".",
    "main.py"
)
& python -m PyInstaller @pyiArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "[build] PyInstaller 失败" -ForegroundColor Red
    exit 1
}

# === Step 3: 校验 ===
$exe = Join-Path $RepoRoot "dist\AutoFarmStation.exe"
if (-not (Test-Path $exe)) {
    Write-Host "[build] 找不到产物 $exe" -ForegroundColor Red
    exit 1
}
$size = (Get-Item $exe).Length / 1MB
Write-Host ("[build] OK 产物: {0} ({1:N1} MB)" -f $exe, $size) -ForegroundColor Green

# === Step 4: 版本号写入校验 ===
Write-Host "[build] 校验 exe 内版本号..." -ForegroundColor Cyan
$ver = & python -c "from autofarmstation import __version__; print(__version__)"
$out = & python -c @"
import re, sys
with open(r'$exe', 'rb') as f:
    data = f.read()
needles = [b'__version__', b'$([System.Text.Encoding]::UTF8.GetBytes($ver))']
hits = [n for n in needles if n in data]
print(' | '.join(n.decode('latin-1', errors='ignore') for n in hits))
"@
Write-Host "[build] 版本号指纹: $out" -ForegroundColor Yellow
if ($out -notmatch [regex]::Escape($ver)) {
    Write-Host "[build] !! 警告: exe 内未找到版本号 $ver" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[build] OK 完成。可执行: $exe" -ForegroundColor Green