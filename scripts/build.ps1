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
# logger 写 stderr → 默认 ErrorActionPreference=Stop 会把脚本中断,
# 临时改 Continue 跑完 selftest,只以退出码判断
$ErrorActionPreference = "Continue"
& python scripts\selftest.py 2>&1 | Out-Null
$selftestExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($selftestExit -ne 0) {
    Write-Host "[build] 自检失败,中止打包" -ForegroundColor Red
    exit 1
}

# === Step 1: 清理 ===
if (-not $SkipClean) {
    Write-Host "[build] 清理 build/dist..." -ForegroundColor Cyan
    if (Test-Path build) { Remove-Item -Recurse -Force build }
    if (Test-Path dist) { Remove-Item -Recurse -Force dist }
}

# === Step 1.5: 生成 exe 版本资源 ===
Write-Host "[build] 生成版本资源文件..." -ForegroundColor Cyan
& python scripts\build_exe_version.py --out build\version_info.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[build] 版本资源生成失败,中止打包" -ForegroundColor Red
    exit 1
}

# === Step 2: PyInstaller ===
Write-Host "[build] PyInstaller 打包(单文件,可能 3-5 分钟)..." -ForegroundColor Cyan

# 用 --name 显式指定 exe 名(覆盖 spec 中的 name)
# 用 --noconfirm 避免提示
# 用 --collect-all mss / pynput 把隐藏子模块打包进(避免漏资源)
# 用 --version-file 写入 Windows 版本资源(FileVersion = 当前版本号)
$pyiArgs = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "AutoFarmStation",
    "--version-file", "build\version_info.txt",
    "--collect-all", "mss",
    "--collect-all", "pynput",
    "--collect-all", "pycaw",
    "--collect-all", "comtypes",
    "--collect-submodules", "pynput.keyboard",
    "--collect-submodules", "pynput.mouse",
    "--hidden-import", "PySide6.QtCore",
    "--hidden-import", "PySide6.QtGui",
    "--hidden-import", "PySide6.QtWidgets",
    "--add-data", "bat;bat",
    "--exclude-module", "tkinter",
    "--exclude-module", "unittest",
    "--exclude-module", "pytest",
    "--paths", ".",
    "main.py"
)
$ErrorActionPreference = "Continue"
& python -m PyInstaller @pyiArgs 2>&1 | Out-Null
$pyiExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($pyiExit -ne 0) {
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
Write-Host "[build] 校验 exe 版本资源..." -ForegroundColor Cyan
$ver = (& python -c "from autofarmstation import __version__; print(__version__)").Trim()
$info = (Get-Item $exe).VersionInfo
Write-Host ("[build] FileVersion={0}  ProductVersion={1}" -f $info.FileVersion, $info.ProductVersion) -ForegroundColor Yellow
if (-not $info.FileVersion) {
    Write-Host "[build] !! 失败: exe 未写入版本资源(检查 build\version_info.txt)" -ForegroundColor Red
    exit 1
}
if ($info.FileVersion -notlike "$ver*") {
    Write-Host ("[build] !! 失败: exe 版本 {0} 与源码版本 {1} 不一致" -f $info.FileVersion, $ver) -ForegroundColor Red
    exit 1
}
Write-Host "[build] OK 版本资源校验通过: $ver" -ForegroundColor Green

Write-Host ""
Write-Host "[build] OK 完成。可执行: $exe" -ForegroundColor Green