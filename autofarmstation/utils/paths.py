"""路径与用户目录处理.

全部使用 %APPDATA% / %USERPROFILE% / %LOCALAPPDATA% / %TEMP% 占位符,
绝不写死任何盘符或用户名。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Final

# === 应用元数据 ===
APP_DIR_NAME: Final[str] = "AutoFarmStation"


def _env(name: str, default: str) -> str:
    """读取环境变量,空值用 fallback."""
    val = os.environ.get(name)
    if val:
        return val
    return default


def user_data_dir() -> Path:
    """返回 %APPDATA%\\AutoFarmStation,不存在则创建."""
    base = _env("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    p = Path(base) / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_log_dir() -> Path:
    """返回 %APPDATA%\\AutoFarmStation\\logs."""
    p = user_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_cache_dir() -> Path:
    """返回 %APPDATA%\\AutoFarmStation\\cache."""
    p = user_data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_macro_dir() -> Path:
    """返回 %APPDATA%\\AutoFarmStation\\macros(用户录制的宏)."""
    p = user_data_dir() / "macros"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_preset_dir() -> Path:
    """返回 %APPDATA%\\AutoFarmStation\\presets(用户自定义预设)."""
    p = user_data_dir() / "presets"
    p.mkdir(parents=True, exist_ok=True)
    return p


def temp_dir() -> Path:
    """返回系统临时目录,确保存在."""
    p = Path(tempfile.gettempdir())
    p.mkdir(parents=True, exist_ok=True)
    return p


def resource_dir() -> Path:
    """返回资源目录(PyInstaller 打包后指向 _MEIPASS,源码时指向仓库根)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # 源码运行时,utils/paths.py 在 autofarmstation/utils/ 下,父级是 autofarmstation/,再父级是仓库根
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    """主配置文件路径."""
    return user_data_dir() / "config.json"


def stats_path() -> Path:
    """统计数据文件路径."""
    return user_data_dir() / "stats.json"


def session_path() -> Path:
    """上次会话快照路径(用于「是否恢复上次的窗口」)."""
    return user_data_dir() / "session.json"


def user_bat_dir() -> Path:
    """%APPDATA%\\AutoFarmStation\\bats —— 用户自加的 bat 脚本."""
    p = user_data_dir() / "bats"
    p.mkdir(parents=True, exist_ok=True)
    return p


def schedule_path() -> Path:
    """定时任务列表路径(与主配置分开存,避免和窗口列表一起被覆盖)."""
    return user_data_dir() / "schedules.json"


def bundled_bat_dir() -> Path:
    """内置 bat 库:PyInstaller 时指向 _MEIPASS/bat,源码运行时指向仓库根/bat."""
    return resource_dir() / "bat"


def steam_config_path() -> Path:
    """Steam 的 steam.cfg 路径(平台 store 进程读取)."""
    base = _env("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return Path(base) / "Steam" / "steam.cfg"


def steam_userdata_dir() -> Path:
    """Steam 用户数据根目录(localconfig.vdf 等)."""
    base = _env("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    return Path(base) / "Steam" / "htmlcache"  # 占位,实际路径见具体子模块调用方


# === 工具函数 ===
def safe_filename(name: str) -> str:
    """把任意字符串规整为合法文件名."""
    bad = '<>:"/\\|?*\x00'
    out = "".join("_" if ch in bad else ch for ch in name)
    out = out.strip(" .")
    return out or "unnamed"


def _locked(path: Path):
    """跨进程写锁:多开本软件 / 与更新助手并发写时避免 last-write-wins.

    进程内的 RLock 管不了第二个进程。用**独立的 .lock 文件**加
    ``msvcrt.locking`` 来串行化「读-改-写」临界区。

    为什么不锁目标文件本身:锁住 config.json 后 ``os.replace`` 要替换它,
    Windows 会直接抛 WinError 5(拒绝访问)—— 等于为了并发安全把正常写入
    也搞坏了。所以锁必须落在旁边的 .lock 上,不能落在被替换的目标上。

    非 Windows / 无 msvcrt 时降级为 no-op(不影响原有行为)。
    """
    if sys.platform != "win32":
        from contextlib import nullcontext
        return nullcontext()
    try:
        import msvcrt
    except ImportError:  # pragma: no cover
        from contextlib import nullcontext
        return nullcontext()

    lock_path = path.with_name(path.name + ".lock")

    class _Lock:
        def __enter__(self):
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                # "a+b":不存在则建,存在则打开,不会截断别人的锁
                self._fh = open(lock_path, "a+b")
                # LK_LOCK = 阻塞等待,最多等到对面写完
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
            except OSError:
                # 拿不到锁就降级为无锁写:宁可冒一次并发风险,
                # 也不能让配置因为锁故障而完全写不进去
                if getattr(self, "_fh", None) is not None:
                    try:
                        self._fh.close()
                    except OSError:
                        pass
                    self._fh = None
            return self

        def __exit__(self, *exc) -> None:
            fh = getattr(self, "_fh", None)
            if fh is not None:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                try:
                    fh.close()
                except OSError:
                    pass
                self._fh = None
            # .lock 只是锁的载体,内容永远为空;不留垃圾在用户数据目录里
            try:
                if lock_path.exists() and lock_path.stat().st_size == 0:
                    lock_path.unlink()
            except OSError:
                # 删不掉无所谓(可能正被别的进程锁着),空文件无害
                pass

    _Lock._fh = None
    return _Lock()


def atomic_write(path: Path, content: bytes | str) -> None:
    """原子写入:先写 .tmp,fsync 落盘,再 os.replace 覆盖。

    Windows 上 ``os.replace`` 是原子的(内部用 MoveFileEx + WRITE_THROUGH),
    覆盖已存在的目标文件不需要先 unlink。

    之前的实现是「flush() → unlink 原文件 → shutil.move(tmp, path)」:
      * 没有 fsync,崩溃/断电时 tmp 里可能是半截内容;
      * unlink 与 move 之间原文件**不存在**,这期间一崩,config.json
        连同 .corrupt.json 备份一起彻底消失 —— 用户所有设置/窗口列表
        全部归零,且没有任何可恢复的残留。
    这是「数据丢失级」缺陷,不是体验问题。

    另外用独立的 .lock 文件串行化并发写:多进程/多线程同时 replace
    同一路径时 Windows 会抛 PermissionError,真实场景就是
    「多开软件时配置偶发写丢」。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # tmp 名带上 pid:多实例/多线程并发写同一路径时不会互踩
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    mode = "wb" if isinstance(content, bytes) else "w"
    enc = None if isinstance(content, bytes) else "utf-8"
    with _locked(path):
        try:
            with open(tmp, mode, encoding=enc) as f:
                f.write(content)  # type: ignore[arg-type]
                f.flush()
                # 真正压到磁盘,而不是留在 OS 缓存里等崩溃
                os.fsync(f.fileno())
            os.replace(str(tmp), str(path))
        except BaseException:
            # 失败时清掉半截 tmp,别在用户数据目录里留垃圾
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise