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
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

# === 常量 ===
PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

ProcessPowerThrottling = 4  # PROCESS_INFORMATION_CLASS
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 1  # 开:能效模式(EcoQoS)
PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION = 4  # 关时附带忽略

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.ULONG),
        ("ControlMask", wintypes.ULONG),
        ("StateMask", wintypes.ULONG),
    ]


def _open_process(pid: int) -> int:
    """以「改设置 + 受限查询」权限打开进程;失败返回 0."""
    if not pid or int(pid) <= 0:
        return 0
    try:
        h = _kernel32.OpenProcess(
            PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            int(pid),
        )
    except Exception:  # noqa: BLE001
        return 0
    return int(h or 0)


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


def _set_priority(hproc: int, on: bool) -> bool:
    """开关低于正常的进程优先级."""
    try:
        cls = BELOW_NORMAL_PRIORITY_CLASS if on else NORMAL_PRIORITY_CLASS
        ok = _kernel32.SetPriorityClass(hproc, cls)
    except Exception:  # noqa: BLE001
        return False
    return bool(ok)


def set_eco(pid: int, on: bool) -> bool:
    """把指定进程设为 / 取消「省电降帧」模式.

    返回 True 表示至少电源节流设置成功(优先级失败不视为整体失败)。
    任何异常都静默吞掉 —— 这是锦上添花的功能,绝不影响主流程。
    """
    hproc = _open_process(int(pid) if pid else 0)
    if not hproc:
        return False
    try:
        ok = _set_throttling(hproc, bool(on))
        _set_priority(hproc, bool(on))
        return ok
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            _kernel32.CloseHandle(hproc)
        except Exception:  # noqa: BLE001
            pass


def is_supported() -> bool:
    """当前系统是否支持 EcoQoS(有 SetProcessInformation 即可)."""
    try:
        return bool(getattr(_kernel32, "SetProcessInformation", None))
    except Exception:  # noqa: BLE001
        return False
