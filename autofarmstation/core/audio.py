"""系统音量 / 单进程音量控制(Windows Core Audio).

两路需求:
1. **整体音量** —— 直接调默认输出设备的 master volume(等价于任务栏音量条)。
2. **单个窗口的音量** —— 调该进程的音频会话音量(等价于「音量合成器」里那一栏),
   不影响系统整体音量,也不影响其它游戏。

实现基于 pycaw(Windows Core Audio 的 Python 封装)。
pycaw 不可用时所有函数返回 None / False,不抛异常 —— 音量功能整体降级,
其余功能不受影响。

注意:音频会话是「进程开始发声后」才出现的。进程还没出声时
``get_session_volume`` 返回 None(不是 0),UI 需据此提示而不是显示成静音。
"""

from __future__ import annotations

import logging

_log = logging.getLogger("autofarmstation.audio")

# COM 初始化(每个使用线程都要一次;重复调用是计数式,安全)
_com_ready = False


def available() -> bool:
    """pycaw 是否可用(打包时未带 comtypes/pycaw 时返回 False)."""
    try:
        import pycaw.pycaw  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _ensure_com() -> None:
    global _com_ready
    if _com_ready:
        return
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:  # noqa: BLE001
        pass
    _com_ready = True


def _clamp(v: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 1.0
    return 0.0 if f < 0 else (1.0 if f > 1 else f)


# ==================== 整体音量 ====================
def get_master_volume() -> float | None:
    """默认输出设备的整体音量(0.0~1.0).失败返回 None."""
    if not available():
        return None
    _ensure_com()
    try:
        from pycaw.pycaw import AudioUtilities

        sp = AudioUtilities.GetSpeakers()
        return _clamp(sp.EndpointVolume.GetMasterVolumeLevelScalar())
    except Exception as e:  # noqa: BLE001
        _log.debug("读取整体音量失败: %s", e)
        return None


def set_master_volume(volume: float) -> bool:
    """设置整体音量(0.0~1.0).成功返回 True."""
    if not available():
        return False
    _ensure_com()
    try:
        from pycaw.pycaw import AudioUtilities

        sp = AudioUtilities.GetSpeakers()
        sp.EndpointVolume.SetMasterVolumeLevelScalar(_clamp(volume), None)
        return True
    except Exception as e:  # noqa: BLE001
        _log.debug("设置整体音量失败: %s", e)
        return False


def get_master_mute() -> bool | None:
    if not available():
        return None
    _ensure_com()
    try:
        from pycaw.pycaw import AudioUtilities

        return bool(AudioUtilities.GetSpeakers().EndpointVolume.GetMute())
    except Exception:  # noqa: BLE001
        return None


def set_master_mute(mute: bool) -> bool:
    if not available():
        return False
    _ensure_com()
    try:
        from pycaw.pycaw import AudioUtilities

        AudioUtilities.GetSpeakers().EndpointVolume.SetMute(1 if mute else 0, None)
        return True
    except Exception:  # noqa: BLE001
        return False


def toggle_master_mute() -> bool | None:
    """切换静音,返回切换后的静音状态."""
    cur = get_master_mute()
    if cur is None:
        return None
    if not set_master_mute(not cur):
        return None
    return not cur


# ==================== 单进程音量 ====================
def _session_pid(sess) -> int:
    """从 AudioSession 取 pid(不同 pycaw 版本属性名不同)."""
    pid = getattr(sess, "ProcessId", None)
    if pid:
        return int(pid)
    proc = getattr(sess, "Process", None)
    return int(getattr(proc, "pid", 0) or 0)


def _iter_sessions():
    """产出 (pid, 进程名, SimpleAudioVolume)."""
    from pycaw.pycaw import AudioUtilities

    for sess in AudioUtilities.GetAllSessions():
        vol = getattr(sess, "SimpleAudioVolume", None)
        if vol is None:
            continue
        pid = _session_pid(sess)
        name = ""
        proc = getattr(sess, "Process", None)
        if proc is not None:
            try:
                name = proc.name()
            except Exception:  # noqa: BLE001
                name = ""
        yield pid, name, vol


def find_session(pid: int):
    """按 pid 找 SimpleAudioVolume.找不到返回 None."""
    if not available() or not pid:
        return None
    _ensure_com()
    try:
        for spid, _name, vol in _iter_sessions():
            if spid == int(pid):
                return vol
    except Exception as e:  # noqa: BLE001
        _log.debug("枚举音频会话失败: %s", e)
    return None


def get_session_volume(pid: int) -> float | None:
    """该进程的会话音量(0.0~1.0);进程没有音频会话时 None."""
    vol = find_session(pid)
    if vol is None:
        return None
    try:
        return _clamp(vol.GetMasterVolume())
    except Exception:  # noqa: BLE001
        return None


def set_session_volume(pid: int, volume: float) -> bool:
    """设置该进程的会话音量.进程还没发声(无会话)时返回 False."""
    vol = find_session(pid)
    if vol is None:
        return False
    try:
        vol.SetMasterVolume(_clamp(volume), None)
        return True
    except Exception:  # noqa: BLE001
        return False


def get_session_mute(pid: int) -> bool | None:
    vol = find_session(pid)
    if vol is None:
        return None
    try:
        return bool(vol.GetMute())
    except Exception:  # noqa: BLE001
        return None


def set_session_mute(pid: int, mute: bool) -> bool:
    vol = find_session(pid)
    if vol is None:
        return False
    try:
        vol.SetMute(1 if mute else 0, None)
        return True
    except Exception:  # noqa: BLE001
        return False


def list_sessions() -> list[dict]:
    """当前所有音频会话:[{pid, name, volume, muted}]."""
    if not available():
        return []
    _ensure_com()
    out: list[dict] = []
    try:
        for pid, name, vol in _iter_sessions():
            try:
                out.append(
                    {
                        "pid": pid,
                        "name": name,
                        "volume": _clamp(vol.GetMasterVolume()),
                        "muted": bool(vol.GetMute()),
                    }
                )
            except Exception:  # noqa: BLE001
                continue
    except Exception as e:  # noqa: BLE001
        _log.debug("枚举音频会话失败: %s", e)
    return out


def set_volume_for_pids(pids: list[int], volume: float) -> int:
    """对一批 pid 设置会话音量,返回成功的个数."""
    n = 0
    for pid in pids:
        if set_session_volume(int(pid), volume):
            n += 1
    return n


def describe() -> str:
    """一行描述(设置面板用)."""
    if not available():
        return "需要 pycaw(未安装,音量功能不可用)"
    mv = get_master_volume()
    if mv is None:
        return "无法读取默认输出设备音量"
    mute = get_master_mute()
    return f"整体音量 {int(round(mv * 100))}%" + ("(已静音)" if mute else "")
