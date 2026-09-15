"""一键下载并替换当前 exe 的自动更新模块.

调用方(settings_dialog._on_update_now)只关心一个入口:
    download_and_apply_release(info: UpdateInfo,
                               progress_cb=..., cancel_cb=..., parent_log=...)
        -> ApplyResult

其余都是辅助:

- `current_exe_path()` 返回当前正在运行的 exe(打包时);源码运行时返回 None
- `download_with_progress(url, dest, ...)` 通用流式下载(可取消、可回调)
- `apply_pending_update(cfg, main_window)` 由主窗口 closeEvent 调用:
    若 cfg 有 pending_update_path → spawn swap helper 后返回 True
- `SWAP_HELPER_PS1` / `launch_swap_helper(...)` 由 PowerShell 干
    「等父进程退出 → 覆盖 → 启动」

整体安全要点:
1. 只在打包后(`sys.frozen`)替换运行中的 exe;源码运行只能打开下载页
2. 下载落地路径 = %TEMP%\\<APP>_<version>.exe,完成即转,失败删
3. swap helper 启动 30 秒内等不到父进程退出 → 放弃(不冒险覆盖)
4. 校验下载文件大小 < 30MB 视为不完整 → 放弃
5. 拷贝时 -Force 覆盖目标,启动新版本后顺手清掉 %TEMP% 里的临时 exe
"""
from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from ..__init__ import __app_name__, __version__
from .paths import temp_dir
from .update_checker import UpdateInfo

# === 常量 ===
USER_AGENT: str = f"{__app_name__}/{__version__} (Windows)"
MIN_VALID_EXE_SIZE: int = 30 * 1024 * 1024  # < 30MB 视为下载不完整
PROGRESS_CHUNK: int = 64 * 1024  # 64KB
HELPER_WAIT_TIMEOUT_SEC: int = 30


@dataclasses.dataclass
class ApplyResult:
    """下载并触发替换的最终结果."""

    ok: bool
    downloaded_to: str = ""    # 本次下载的 exe 落地路径(若成功)
    error: str = ""            # 用户可读错误,空表示成功
    target_exe: str = ""       # 当前正在运行的 exe 路径


# === 路径探测 ===
def current_exe_path() -> Optional[Path]:
    """当前正在运行的 exe 路径。

    打包运行:sys.executable 就是 AutoFarmStation.exe
    源码运行:返回 None(无法自动替换,只能打开下载页)
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None


def _pending_dest(target_exe: Path, version: str) -> Path:
    """下载到 %TEMP%/<APP>_<version>.exe。"""
    safe = version.lstrip("v").replace("/", "_").replace("\\", "_")
    return temp_dir() / f"{__app_name__}_{safe}.exe"


# === 通用流式下载(可取消、可进度回调) ===
def download_with_progress(
    url: str,
    dest: Path,
    *,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> None:
    """把 url 流到 dest(覆盖)。progress_cb(recv_bytes, total_bytes)。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            recv = 0
            with open(dest, "wb") as f:
                while True:
                    if cancel_cb and cancel_cb():
                        raise _DownloadCancelled()
                    chunk = resp.read(PROGRESS_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    recv += len(chunk)
                    if progress_cb:
                        progress_cb(recv, total)
    except _DownloadCancelled:
        # 半截文件清理交给调用方
        raise
    except urllib.error.URLError as e:
        raise DownloadError(f"网络错误:{e.reason}") from e
    except Exception as e:  # noqa: BLE001
        raise DownloadError(f"下载失败:{type(e).__name__}:{e}") from e


class DownloadError(Exception):
    """下载阶段失败(网络、HTTP 错误、解码错误)。"""


class _DownloadCancelled(Exception):
    """用户取消。"""


# === 顶层入口 ===
def download_and_apply_release(
    info: UpdateInfo,
    *,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    log=None,
) -> ApplyResult:
    """下载 info 指向的 release exe,落地到 %TEMP%。

    settings_dialog 在用户确认后调用本函数:
    - 返回 ok=False:仅展示 error,不动 cfg
    - 返回 ok=True:settings_dialog 把 pending_update_path 写进 cfg,然后调主窗口 close()
    """
    if not info.download_url:
        return ApplyResult(ok=False, error="远端 release 没有 .exe 资产可供下载")
    target = current_exe_path()
    if target is None:
        return ApplyResult(
            ok=False,
            error="当前是源码运行,无法自动替换 exe。请到 release 页面手动下载。",
        )
    if not target.exists():
        return ApplyResult(ok=False, error=f"目标 exe 不存在:{target}")

    dest = _pending_dest(target, info.latest_version)
    try:
        # 同名旧文件先删(避免占用)
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        download_with_progress(
            info.download_url, dest,
            progress_cb=progress_cb, cancel_cb=cancel_cb,
        )
    except _DownloadCancelled:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return ApplyResult(ok=False, error="已取消下载")
    except DownloadError as e:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return ApplyResult(ok=False, error=str(e))

    # 体积自检
    try:
        size = dest.stat().st_size
    except OSError as e:
        return ApplyResult(ok=False, error=f"无法读取下载文件:{e}")
    if size < MIN_VALID_EXE_SIZE:
        try:
            dest.unlink()
        except OSError:
            pass
        return ApplyResult(
            ok=False,
            error=f"下载文件过小({size // 1024 // 1024}MB),疑似不完整,已删除",
        )

    if log:
        try:
            log.info("下载完成: %s (%.1fMB)", dest, size / 1024 / 1024)
        except Exception:  # noqa: BLE001
            pass
    return ApplyResult(ok=True, downloaded_to=str(dest), target_exe=str(target))


# === 关闭时:实际触发替换 ===
def apply_pending_update(cfg, main_window) -> bool:
    """主窗口 closeEvent 调用:若 cfg 有 pending_update_path → spawn 助手并返回 True。

    返回 True 表示「正在去更新」,调用方应该紧接着 super().closeEvent(ev) / app.quit()。
    返回 False 表示没有 pending 更新,正常走旧路径。

    注意:调用前 cfg.save() 必须已经完成(若用户改了设置)。
    """
    pending = (cfg.get("settings.pending_update_path") or "").strip()
    if not pending:
        return False
    target = (cfg.get("settings.pending_update_target_exe") or "").strip()
    if not target:
        # 没记目标 → 重新探测一次(理论上 cfg 里有,但防丢失)
        cur = current_exe_path()
        if cur is None:
            return False
        target = str(cur)

    pending_p = Path(pending)
    target_p = Path(target)
    if not pending_p.exists():
        cfg.set("settings.pending_update_path", "")
        cfg.set("settings.pending_update_target_exe", "")
        try:
            cfg.save()
        except Exception:  # noqa: BLE001
            pass
        return False

    parent_pid = os.getpid()
    try:
        launch_swap_helper(pending_p, target_p, parent_pid)
    except Exception as e:  # noqa: BLE001
        # 助手起不来 → 清掉 pending,主程序正常退出
        try:
            cfg.set("settings.pending_update_path", "")
            cfg.set("settings.pending_update_target_exe", "")
            cfg.save()
        except Exception:  # noqa: BLE001
            pass
        if hasattr(main_window, "_log"):
            try:
                main_window._log.error("无法启动更新助手:%s", e)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        return False

    # 标记主窗口「正在去更新」,closeEvent 别再弹确认 / 别杀游戏进程
    if main_window is not None:
        try:
            main_window._updating = True  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return True


# === 交换助手 ===
SWAP_HELPER_PS1 = r"""
# AutoFarmStation 一键更新助手(由 utils/updater.py 写入临时文件并启动)
param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$NewExe,
    [Parameter(Mandatory=$true)][string]$TargetExe
)
$ErrorActionPreference = 'Stop'

function Write-UpdLog([string]$msg) {
    try {
        $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
        if ($env:AFS_UPDATER_LOG -and $env:AFS_UPDATER_LOG -ne 'NUL') {
            Add-Content -LiteralPath $env:AFS_UPDATER_LOG -Value $line -ErrorAction SilentlyContinue
        }
    } catch {}
}

try {
    Write-UpdLog ("启动 ParentPid=" + $ParentPid + " NewExe=" + $NewExe + " TargetExe=" + $TargetExe)
    # 1) 等父进程退出,最多 30 秒
    $exited = $false
    try {
        $null = Wait-Process -Id $ParentPid -Timeout $env:AFS_UPDATER_TIMEOUT -ErrorAction Stop
        $exited = $true
    } catch {
        $proc = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
        if (-not $proc) { $exited = $true }
    }
    if (-not $exited) {
        Write-UpdLog "父进程未在超时内退出 exit=2"
        exit 2
    }
    Write-UpdLog "父进程已退出"

    # 2) 校验下载文件存在 + 大小
    if (-not (Test-Path -LiteralPath $NewExe)) {
        Write-UpdLog "下载文件不存在 exit=3"
        exit 3
    }
    $size = (Get-Item -LiteralPath $NewExe).Length
    $minSize = [int]$env:AFS_UPDATER_MIN_SIZE
    if ($size -lt $minSize) {
        Write-UpdLog ("下载文件过小 " + $size + " bytes exit=4")
        exit 4
    }

    # 3) 拷贝新 exe 覆盖目标
    Copy-Item -LiteralPath $NewExe -Destination $TargetExe -Force
    Write-UpdLog ("已覆盖目标 " + $TargetExe)

    # 4) 启动新版本
    Start-Process -FilePath $TargetExe
    Write-UpdLog "已启动新版本"

    # 5) 清掉 %TEMP% 里的临时下载
    try {
        Remove-Item -LiteralPath $NewExe -Force -ErrorAction SilentlyContinue
    } catch {}
    Write-UpdLog "完成 exit=0"
    exit 0
} catch {
    Write-UpdLog ("异常 exit=1: " + $_.Exception.Message)
    exit 1
}
"""


def _detect_log_path() -> Optional[Path]:
    """把助手日志写到 %APPDATA%\\<APP>\\logs\\updater.log,失败则返回 None(助手不写日志)。"""
    try:
        from .paths import user_log_dir
        log = user_log_dir() / "updater.log"
        return log
    except Exception:  # noqa: BLE001
        return None


def launch_swap_helper(new_exe: Path, target_exe: Path, parent_pid: int) -> None:
    """启动 PowerShell 助手:detach、不开窗、参数透传。"""
    # 1) 脚本落地到 %TEMP%(UTF-8 BOM,让 PS5.1 也能识别中文路径)
    tmpdir = Path(tempfile.gettempdir())
    script_path = tmpdir / f"{__app_name__}_updater_{os.getpid()}.ps1"
    script_path.write_text(SWAP_HELPER_PS1, encoding="utf-8-sig")

    log_path = _detect_log_path()
    env = os.environ.copy()
    env["AFS_UPDATER_TIMEOUT"] = str(HELPER_WAIT_TIMEOUT_SEC)
    env["AFS_UPDATER_MIN_SIZE"] = str(MIN_VALID_EXE_SIZE)
    env["AFS_UPDATER_LOG"] = str(log_path) if log_path is not None else "NUL"

    # DETACHED_PROCESS(0x8)+CREATE_NO_WINDOW(0x08000000):不挂控制台、不弹窗
    flags = 0x00000008 | 0x08000000
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-File", str(script_path),
        "-ParentPid", str(parent_pid),
        "-NewExe", str(new_exe),
        "-TargetExe", str(target_exe),
    ]
    subprocess.Popen(  # noqa: S603  (启动的是 PowerShell 助手,非用户输入)
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


# === 自检辅助 ===
def build_fake_release(tag: str = "v9.9.9", asset_name: str = "AutoFarmStation.exe",
                       asset_size: int = 65_000_000) -> UpdateInfo:
    """构造一个 UpdateInfo 给 UI/自检用(不去 GitHub 联网)。"""
    return UpdateInfo(
        has_update=True,
        latest_version=tag,
        current_version=__version__,
        release_url=f"https://github.com/zlwzk/AutoFarmStation/releases/tag/{tag}",
        release_notes="(fake release notes)",
        download_url=f"https://example.invalid/{asset_name}",
        asset_name=asset_name,
        asset_size=asset_size,
    )