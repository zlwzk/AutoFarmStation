"""窗口嵌入(宿主)管理.

把外部进程的顶层窗口「收进」本软件的容器里显示:
- 原窗口从桌面/任务栏消失,变成软件内的一块画面(用户诉求:原窗口不再单独显示)
- 自动去边框标题栏并铺满容器,容器缩放时跟随
- 任何时刻都可以完整还原(原样式、原位置、原父窗口)

技术要点(纯 ctypes,不依赖 pywin32):
- 跨进程 SetParent:Windows 允许同用户同完整性级别的进程互相收编窗口
- embed 时快照还原所需状态(原 parent / 样式 / 矩形 / 是否最小化)
- 修改 GWL_STYLE 后必须 SWP_FRAMECHANGED,非客户区才会立即重绘
- 全部操作幂等且线程安全;release 对未嵌入的窗口是安全的 no-op
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from . import window_finder as wf

# 需要去掉的「顶层窗口」装饰:标题栏/系统菜单/可缩放边框/最小化/最大化按钮
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
_STRIP_STYLE = (
    wf.WS_CAPTION | wf.WS_THICKFRAME | wf.WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
)

SWP_FRAMECHANGED = 0x0020
SWP_NOZORDER = 0x0004
GA_PARENT = 1  # window_finder 只定义了 GA_ROOT,这里补上

# ctypes 原型(复用 window_finder 里已声明的 _user32)
_user32 = wf._user32
_user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
_user32.SetParent.restype = wintypes.HWND
_user32.GetParent.argtypes = [wintypes.HWND]
_user32.GetParent.restype = wintypes.HWND
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long

_lock = threading.RLock()


@dataclass
class _EmbedState:
    """嵌入时的窗口快照(还原用)."""

    hwnd: int
    container: int
    orig_parent: int
    orig_style: int
    orig_ex_style: int
    orig_rect: tuple[int, int, int, int]
    orig_minimized: bool
    ts: float


_states: dict[int, _EmbedState] = {}


def is_embedded(hwnd: int) -> bool:
    with _lock:
        return int(hwnd) in _states


def embedded_hwnds() -> list[int]:
    with _lock:
        return list(_states.keys())


def embed_window(hwnd: int, container_hwnd: int) -> bool:
    """把外部顶层窗口嵌进 container_hwnd 内部.

    成功后窗口成为容器的视觉子区域并铺满它;返回 False 表示失败(窗口无效等)。
    已嵌入的窗口重复调用只做安全返回(True),不会重复快照。
    """
    hwnd = int(hwnd)
    container_hwnd = int(container_hwnd)
    if not wf._is_window(hwnd) or not wf._is_window(container_hwnd):
        return False

    with _lock:
        if hwnd in _states:
            return True

    info = wf.get_window_info(hwnd)
    if info is None:
        return False
    if info.is_minimized:
        # 最小化状态下 SetParent 会得到怪异几何,先还原
        wf.show_window(hwnd, wf.SW_RESTORE)
        time.sleep(0.12)

    try:
        style = _user32.GetWindowLongW(hwnd, wf.GWL_STYLE)
        ex_style = _user32.GetWindowLongW(hwnd, wf.GWL_EXSTYLE)
        # 注意:对 WS_POPUP 窗口 GetParent 返回的是 owner 而非 parent,
        # 必须用 GetAncestor(GA_PARENT) 拿真实父窗口
        orig_parent = int(_user32.GetAncestor(hwnd, GA_PARENT) or 0)
    except Exception:
        return False

    state = _EmbedState(
        hwnd=hwnd,
        container=container_hwnd,
        orig_parent=orig_parent,
        orig_style=style,
        orig_ex_style=ex_style,
        orig_rect=info.rect,
        orig_minimized=info.is_minimized,
        ts=time.time(),
    )

    # 1) 去装饰(非客户区由容器提供)
    _user32.SetWindowLongW(hwnd, wf.GWL_STYLE, style & ~_STRIP_STYLE)
    # 2) 换父(跨进程)
    if not _user32.SetParent(hwnd, container_hwnd):
        # 失败要回滚样式
        _user32.SetWindowLongW(hwnd, wf.GWL_STYLE, style)
        return False
    # 3) 铺满容器 + 立即重算非客户区
    rect = wintypes.RECT(0, 0, 0, 0)
    _user32.GetClientRect(container_hwnd, ctypes.byref(rect))
    w = max(16, rect.right - rect.left)
    h = max(16, rect.bottom - rect.top)
    _user32.SetWindowPos(
        hwnd, 0, 0, 0, w, h,
        SWP_NOZORDER | SWP_FRAMECHANGED | wf.SWP_SHOWWINDOW,
    )

    with _lock:
        _states[hwnd] = state
    return True


def fit_to(hwnd: int, w: int, h: int) -> bool:
    """把已嵌入的窗口调整为 w×h(物理像素,容器客户区坐标系)."""
    hwnd = int(hwnd)
    with _lock:
        st = _states.get(hwnd)
    if st is None or not wf._is_window(hwnd):
        return False
    return wf.move_window(hwnd, 0, 0, max(16, int(w)), max(16, int(h)))


def release_window(hwnd: int) -> bool:
    """还原窗口(样式/位置/父窗口).未嵌入或窗口已销毁时返回 False."""
    hwnd = int(hwnd)
    with _lock:
        state = _states.pop(hwnd, None)
    if state is None:
        return False
    if not wf._is_window(hwnd):
        return False  # 窗口没了,无需还原
    try:
        # 1) 先换回原父(0 = 桌面)
        _user32.SetParent(hwnd, state.orig_parent or None)
        # 2) 恢复样式
        _user32.SetWindowLongW(hwnd, wf.GWL_STYLE, state.orig_style)
        _user32.SetWindowLongW(hwnd, wf.GWL_EXSTYLE, state.orig_ex_style)
        # 3) 回原位置原大小
        l, t, r, b = state.orig_rect
        _user32.SetWindowPos(
            hwnd, 0, l, t, max(16, r - l), max(16, b - t),
            SWP_NOZORDER | SWP_FRAMECHANGED | wf.SWP_SHOWWINDOW,
        )
        if state.orig_minimized:
            wf.show_window(hwnd, wf.SW_SHOWMINIMIZED)
    except Exception:
        return False
    return True


def release_all() -> int:
    """还原所有嵌入窗口(程序退出时调用).返回成功还原的数量."""
    with _lock:
        hwnds = list(_states.keys())
    n = 0
    for h in hwnds:
        if release_window(h):
            n += 1
    return n
