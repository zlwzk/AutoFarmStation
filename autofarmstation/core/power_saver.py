"""游戏进程省电降帧(EcoQoS).

把嵌入 / 后台挂机的游戏进程设为「能效模式」+ 低于正常的优先级:
    * SetProcessInformation(ProcessPowerThrottling)
      → 标记为 EcoQoS 进程(Win10 1809+),Windows 会把它压到能效核 /
        降低调度权重,CPU 占用明显下降,游戏帧率随之降低;
    * SetPriorityClass(BELOW_NORMAL_PRIORITY_CLASS)
      → 进一步让出 CPU,保证多开时系统与主控软件流畅。

目的不是让游戏跑得快,而是让「放在软件里挂着」的游戏不抢资源、
不把整个系统拖崩 —— 帧率降低对挂机类场景是可接受的。

所有调用失败都静默返回 False(老系统 / 权限不足时不影响主流程)。
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

# === 常量 ===
PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400

ProcessPowerThrottling = 4  # PROCESS_INFORMATION_CLASS
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 1  # 开:能效模式(EcoQoS)
PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION = 4  # 关时附带忽略

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020
IDLE_PRIORITY_CLASS = 0x00000040
ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
HIGH_PRIORITY_CLASS = 0x00000080
REALTIME_PRIORITY_CLASS = 0x00000100

# 优先级类 → 可读名(日志 / 测试用)
CLASS_NAME = {
    IDLE_PRIORITY_CLASS: "IDLE",
    BELOW_NORMAL_PRIORITY_CLASS: "BELOW_NORMAL",
    NORMAL_PRIORITY_CLASS: "NORMAL",
    ABOVE_NORMAL_PRIORITY_CLASS: "ABOVE_NORMAL",
    HIGH_PRIORITY_CLASS: "HIGH",
    REALTIME_PRIORITY_CLASS: "REALTIME",
}

# pid → 进入省电前的原始优先级类。
# 不记这个的话,取消省电时一律设成 NORMAL —— 本来跑在 HIGH / ABOVE_NORMAL 的
# 游戏会被**永久降级**,用户会觉得「点了恢复怎么还是卡」。
_orig_priority: dict[int, int] = {}
_lock = threading.Lock()


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.ULONG),
        ("ControlMask", wintypes.ULONG),
        ("StateMask", wintypes.ULONG),
    ]


def _open_process(pid: int, *, query_only: bool = False) -> int:
    """以「改设置 + 查询」权限打开进程;失败返回 0."""
    if not pid or int(pid) <= 0:
        return 0
    try:
        access = (PROCESS_QUERY_LIMITED_INFORMATION if query_only
                  else PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION)
        h = _kernel32.OpenProcess(access, False, int(pid))
    except Exception:  # noqa: BLE001
        return 0
    return int(h or 0)


def current_priority(pid: int) -> int:
    """读进程当前优先级类;失败返回 0."""
    hproc = _open_process(int(pid) if pid else 0, query_only=True)
    if not hproc:
        return 0
    try:
        return int(_kernel32.GetPriorityClass(hproc) or 0)
    except Exception:  # noqa: BLE001
        return 0
    finally:
        try:
            _kernel32.CloseHandle(hproc)
        except Exception:  # noqa: BLE001
            pass


def _set_throttling(hproc: int, on: bool) -> bool:
    """开关 EcoQoS 电源节流.老系统没有 SetProcessInformation 时静默失败."""
    try:
        state = PROCESS_POWER_THROTTLING_STATE()
        state.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION
        state.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
        state.StateMask = (
            PROCESS_POWER_THROTTLING_EXECUTION_SPEED if on else 0
        )
        ok = _kernel32.SetProcessInformation(
            hproc,
            ProcessPowerThrottling,
            ctypes.byref(state),
            ctypes.sizeof(state),
        )
    except Exception:  # noqa: BLE001
        return False
    return bool(ok)


def _set_priority_class(hproc: int, cls: int) -> bool:
    try:
        ok = _kernel32.SetPriorityClass(hproc, int(cls))
    except Exception:  # noqa: BLE001
        return False
    return bool(ok)


def set_eco(pid: int, on: bool) -> bool:
    """把指定进程设为 / 取消「省电降帧」模式.

    开启时把**原始优先级类**记进 ``_orig_priority``;关闭时按记录精确还原,
    而不是一律设成 NORMAL(那会把 HIGH / ABOVE_NORMAL 的游戏永久降级)。

    返回 True 表示至少电源节流设置成功(优先级失败不视为整体失败)。
    任何异常都静默吞掉 —— 这是锦上添花的功能,绝不影响主流程。
    """
    pid = int(pid) if pid else 0
    hproc = _open_process(pid)
    if not hproc:
        return False
    try:
        if on:
            # 只在第一次进入省电时记录原始优先级;重复调用不覆盖
            with _lock:
                if pid not in _orig_priority:
                    cur = 0
                    try:
                        cur = int(_kernel32.GetPriorityClass(hproc) or 0)
                    except Exception:  # noqa: BLE001
                        cur = 0
                    _orig_priority[pid] = cur
            ok = _set_throttling(hproc, True)
            _set_priority_class(hproc, BELOW_NORMAL_PRIORITY_CLASS)
            return ok
        # 关闭:优先还原原始优先级
        with _lock:
            orig = _orig_priority.pop(pid, 0)
        ok = _set_throttling(hproc, False)
        if orig:
            _set_priority_class(hproc, orig)
        else:
            # 没记录(比如重启软件后)就退回 NORMAL,至少不会留在 BELOW_NORMAL
            _set_priority_class(hproc, NORMAL_PRIORITY_CLASS)
        return ok
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            _kernel32.CloseHandle(hproc)
        except Exception:  # noqa: BLE001
            pass


def restore_all() -> int:
    """把所有还在省电模式的进程还原,返回成功个数.

    程序退出时必须调 —— 否则被降频的游戏会一直停在 BELOW_NORMAL + EcoQoS,
    用户下次开游戏发现「怎么这么卡」却找不到原因。
    """
    with _lock:
        pids = list(_orig_priority.keys())
    n = 0
    for pid in pids:
        try:
            if set_eco(pid, False):
                n += 1
            else:
                with _lock:
                    _orig_priority.pop(pid, None)
        except Exception:  # noqa: BLE001
            continue
    return n


def eco_pids() -> list[int]:
    """当前处于省电模式的 pid(调试 / 测试用)."""
    with _lock:
        return sorted(_orig_priority)


def is_supported() -> bool:
    """当前系统是否支持 EcoQoS(有 SetProcessInformation 即可)."""
    try:
        return bool(getattr(_kernel32, "SetProcessInformation", None))
    except Exception:  # noqa: BLE001
        return False
