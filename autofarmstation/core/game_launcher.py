"""游戏进程的「探测 → 启动 → 结束」.

解决的问题:
1. 关闭本软件时可选地一并关掉被追踪的游戏进程;
2. 恢复上次会话时,如果游戏没在跑,自动把它唤醒;
3. Steam 游戏要先唤起 Steam 客户端,再通过 `steam://rungameid/<appid>` 拉起游戏。

Steam AppID 的两种探测方式(先环境变量、后安装目录反查):
    a) Steam 启动的进程会带有 ``SteamAppId`` / ``SteamGameId`` 环境变量;
    b) 进程 exe 落在某个 Steam 库的 ``steamapps\\common\\<installdir>`` 下,
       用同目录的 ``appmanifest_<appid>.acf`` 反查。

全程本地,不联网、不读账号密码。
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_log = logging.getLogger("autofarmstation.launcher")


# ==================== 极简 KeyValues(VDF)解析 ====================
def _tokenize_vdf(text: str) -> Iterator[str]:
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "{":
            yield "{"
            i += 1
            continue
        if c == "}":
            yield "}"
            i += 1
            continue
        if c == '"':
            i += 1
            buf: list[str] = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                buf.append(text[i])
                i += 1
            i += 1  # 跳过结尾引号
            yield "".join(buf)
            continue
        j = i
        while j < n and text[j] not in ' \t\r\n{}"':
            j += 1
        yield text[i:j]
        i = j


def parse_vdf(text: str) -> dict:
    """解析 KeyValues 文本成嵌套 dict(纯函数,便于自检)."""
    root: dict = {}
    stack: list[dict] = [root]
    key: str | None = None
    for tok in _tokenize_vdf(text):
        if tok == "{":
            child: dict = {}
            if key is not None:
                stack[-1][key] = child
            stack.append(child)
            key = None
        elif tok == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            if key is None:
                key = tok
            else:
                stack[-1][key] = tok
                key = None
    return root


# ==================== Steam 库定位 ====================
def steam_install_dir() -> Path | None:
    """Steam 安装目录(注册表;取不到返回 None)."""
    try:
        from .steam_status import steam_install_path

        return steam_install_path()
    except Exception:  # noqa: BLE001
        return None


def steam_exe() -> Path | None:
    root = steam_install_dir()
    if root is None:
        return None
    exe = root / "steam.exe"
    return exe if exe.is_file() else None


def steam_libraries() -> list[Path]:
    """所有 Steam 库目录(含主目录).不存在/解析失败时返回尽可能多的候选."""
    root = steam_install_dir()
    out: list[Path] = []
    if root is None:
        return out
    out.append(root)
    lf = root / "steamapps" / "libraryfolders.vdf"
    if lf.is_file():
        try:
            data = parse_vdf(lf.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            data = {}
        folders = data.get("libraryfolders") or {}
        if isinstance(folders, dict):
            for val in folders.values():
                raw = val.get("path") if isinstance(val, dict) else val
                if not raw:
                    continue
                p = Path(str(raw).replace("\\\\", "\\"))
                if p not in out:
                    out.append(p)
    return out


def iter_appmanifests() -> Iterator[tuple[Path, dict]]:
    """产出 (库目录, appmanifest 解析结果)."""
    for lib in steam_libraries():
        sa = lib / "steamapps"
        if not sa.is_dir():
            continue
        for acf in sa.glob("appmanifest_*.acf"):
            try:
                data = parse_vdf(acf.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            state = data.get("AppState")
            if isinstance(state, dict):
                yield lib, state


def find_appid_by_exe(exe: str) -> int:
    """按 exe 路径反查 Steam AppID(找不到返回 0)."""
    if not exe:
        return 0
    try:
        target = str(Path(exe).resolve()).lower()
    except (OSError, ValueError):
        target = str(exe).lower()
    for lib, state in iter_appmanifests():
        installdir = str(state.get("installdir") or "")
        appid = str(state.get("appid") or "")
        if not installdir or not appid.isdigit():
            continue
        base = str((lib / "steamapps" / "common" / installdir)).lower()
        if target == base or target.startswith(base + os.sep):
            return int(appid)
    return 0


# ==================== 启动信息 ====================
@dataclass
class LaunchInfo:
    """怎样把某个被追踪的进程重新拉起来."""

    exe: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str = ""
    name: str = ""
    title: str = ""
    steam_appid: int = 0
    source: str = ""  # 'steam' / 'exe' / ''

    @property
    def usable(self) -> bool:
        return bool(self.steam_appid) or bool(self.exe)

    def to_dict(self) -> dict:
        return {
            "exe": self.exe,
            "args": list(self.args),
            "cwd": self.cwd,
            "name": self.name,
            "title": self.title,
            "steam_appid": int(self.steam_appid),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LaunchInfo":
        d = d or {}
        args = d.get("args") or []
        return cls(
            exe=str(d.get("exe") or ""),
            args=[str(a) for a in args] if isinstance(args, list) else [],
            cwd=str(d.get("cwd") or ""),
            name=str(d.get("name") or ""),
            title=str(d.get("title") or ""),
            steam_appid=int(d.get("steam_appid") or 0),
            source=str(d.get("source") or ""),
        )

    def describe(self) -> str:
        if self.steam_appid:
            return f"Steam 应用 (appid={self.steam_appid})"
        if self.exe:
            return Path(self.exe).name
        return "启动信息未知"


def _appid_from_environ(proc) -> int:
    try:
        env = proc.environ()
    except Exception:  # noqa: BLE001
        return 0
    for key in ("SteamAppId", "SteamGameId", "SteamAppID"):
        raw = str(env.get(key) or "")
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
    return 0


def detect_launch_info(pid: int, *, title: str = "") -> LaunchInfo | None:
    """读取某进程的启动信息(进程不在了返回 None)."""
    if not pid:
        return None
    try:
        import psutil  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        proc = psutil.Process(int(pid))
        exe = str(proc.exe() or "")
        cmdline = list(proc.cmdline() or [])
        cwd = str(proc.cwd() or "")
        name = str(proc.name() or "")
    except Exception:  # noqa: BLE001
        return None

    args = cmdline[1:] if len(cmdline) > 1 else []
    # cmdline[0] 与 exe 相同时不重复带上
    if args and exe and os.path.normcase(args[0]) == os.path.normcase(exe):
        args = args[1:]

    appid = _appid_from_environ(proc) or find_appid_by_exe(exe)
    source = "steam" if appid else ("exe" if exe else "")
    return LaunchInfo(
        exe=exe,
        args=args,
        cwd=cwd,
        name=name,
        title=title,
        steam_appid=appid,
        source=source,
    )


# ==================== 进程状态 / 结束 ====================
def is_process_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        import psutil  # type: ignore

        proc = psutil.Process(int(pid))
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except Exception:  # noqa: BLE001
        return False


def kill_process(
    pid: int,
    *,
    tree: bool = True,
    timeout: float = 6.0,
) -> tuple[bool, str]:
    """结束进程(默认连同子进程).返回 (成功?, 描述)."""
    if not pid:
        return False, "没有 pid"
    try:
        import psutil  # type: ignore
    except Exception:  # noqa: BLE001
        return False, "psutil 不可用"
    try:
        proc = psutil.Process(int(pid))
    except Exception:  # noqa: BLE001
        return True, "进程已不在"

    targets: list = []
    if tree:
        try:
            targets = proc.children(recursive=True)
        except Exception:  # noqa: BLE001
            targets = []
    targets.append(proc)

    for p in targets:
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            continue
    _gone, alive = psutil.wait_procs(targets, timeout=max(0.5, float(timeout)))
    for p in alive:
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            continue
    if alive:
        psutil.wait_procs(alive, timeout=2.0)

    if is_process_alive(pid):
        return False, "进程仍在运行(可能受保护或权限不足)"
    return True, "已结束进程"


# ==================== 启动 ====================
def ensure_steam_running(timeout: float = 90.0) -> tuple[bool, str]:
    """确保 Steam 客户端在运行(不在则启动并等待)."""
    try:
        from .steam_status import is_steam_running
    except Exception:  # noqa: BLE001
        return False, "Steam 检测模块不可用"
    if is_steam_running():
        return True, "Steam 已在运行"
    exe = steam_exe()
    if exe is None:
        return False, "找不到 steam.exe(未安装 Steam?)"
    try:
        creation = 0
        if os.name == "nt":
            creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000008
        subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=creation)
    except Exception as e:  # noqa: BLE001
        return False, f"启动 Steam 失败:{e}"

    deadline = time.monotonic() + max(5.0, float(timeout))
    while time.monotonic() < deadline:
        if is_steam_running():
            return True, "已唤起 Steam"
        time.sleep(0.5)
    return False, f"Steam 在 {timeout:.0f}s 内没有起来"


def launch(info: LaunchInfo | None, *, steam_timeout: float = 90.0) -> tuple[bool, str]:
    """按启动信息把进程拉起来(Steam 游戏先唤起 Steam)."""
    if info is None or not info.usable:
        return False, "缺少启动信息,无法唤醒(请在游戏运行时重新添加窗口)"

    # ① Steam 游戏:先 Steam,再 rungameid
    if info.steam_appid:
        ok, msg = ensure_steam_running(steam_timeout)
        if not ok:
            return False, msg
        time.sleep(1.0)  # 给 Steam 客户端一点时间注册 shell 关联
        try:
            os.startfile(f"steam://rungameid/{int(info.steam_appid)}")  # noqa: S606
        except Exception as e:  # noqa: BLE001
            return False, f"触发 steam:// 失败:{e}"
        return True, f"已请求 Steam 启动 appid={info.steam_appid}({msg})"

    # ② 普通程序:直接拉 exe
    if not info.exe:
        return False, "缺少 exe 路径"
    exe = Path(info.exe)
    if not exe.is_file():
        return False, f"exe 不存在:{exe.name}"
    try:
        subprocess.Popen(
            [str(exe), *[str(a) for a in info.args]],
            cwd=info.cwd or str(exe.parent),
        )
    except Exception as e:  # noqa: BLE001
        return False, f"启动失败:{e}"
    return True, f"已启动 {exe.name}"


def launch_async(info: LaunchInfo | None, *, steam_timeout: float = 90.0, on_done=None) -> None:
    """后台线程启动(避免阻塞 UI).on_done(ok, msg) 在主线程外被调用."""

    def _worker() -> None:
        try:
            ok, msg = launch(info, steam_timeout=steam_timeout)
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"启动出错:{e}"
        if on_done is not None:
            try:
                on_done(ok, msg)
            except Exception:  # noqa: BLE001
                pass

    import threading

    threading.Thread(target=_worker, daemon=True, name="GameLaunch").start()
