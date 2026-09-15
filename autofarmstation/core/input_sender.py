"""输入发送.

封装两种发送模式:
- `post` 模式:PostMessage 给指定 hwnd(对窗口化游戏有效,不抢焦点)
- `send` 模式:ctypes SendInput 真实输入(对全屏独占游戏有效,但需抢焦点)
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import ctypes as c
import enum
import time
from dataclasses import dataclass

from . import window_finder as wf

# === Windows 输入常量 ===
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

# Mouse event flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
MOUSEEVENTF_ABSOLUTE = 0x8000

# Keyboard event flags
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_UNICODE = 0x0004

# 虚拟键码(Virtual-Key Codes) - 完整表
VK = {
    "LBUTTON": 0x01, "RBUTTON": 0x02, "CANCEL": 0x03, "MBUTTON": 0x04,
    "XBUTTON1": 0x05, "XBUTTON2": 0x06, "BACK": 0x08, "TAB": 0x09,
    "CLEAR": 0x0C, "RETURN": 0x0D, "SHIFT": 0x10, "CONTROL": 0x11,
    "MENU": 0x12, "PAUSE": 0x13, "CAPITAL": 0x14,
    "ESCAPE": 0x1B, "SPACE": 0x20, "PRIOR": 0x21, "NEXT": 0x22,
    "END": 0x23, "HOME": 0x24, "LEFT": 0x25, "UP": 0x26,
    "RIGHT": 0x27, "DOWN": 0x28, "SELECT": 0x29, "PRINT": 0x2A,
    "EXECUTE": 0x2B, "SNAPSHOT": 0x2C, "INSERT": 0x2D, "DELETE": 0x2E,
    "HELP": 0x2F,
    **{f"{c}": i for c, i in zip("0123456789", range(0x30, 0x3A))},
    **{c: i for c, i in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", range(0x41, 0x5B))},
    "LWIN": 0x5B, "RWIN": 0x5C, "APPS": 0x5D, "SLEEP": 0x5F,
    "NUMPAD0": 0x60, "NUMPAD1": 0x61, "NUMPAD2": 0x62, "NUMPAD3": 0x63,
    "NUMPAD4": 0x64, "NUMPAD5": 0x65, "NUMPAD6": 0x66, "NUMPAD7": 0x67,
    "NUMPAD8": 0x68, "NUMPAD9": 0x69,
    "MULTIPLY": 0x6A, "ADD": 0x6B, "SEPARATOR": 0x6C, "SUBTRACT": 0x6D,
    "DECIMAL": 0x6E, "DIVIDE": 0x6F,
    **{f"F{i}": 0x6F + i for i in range(1, 25)},
    "NUMLOCK": 0x90, "SCROLL": 0x91,
    "LSHIFT": 0xA0, "RSHIFT": 0xA1, "LCONTROL": 0xA2, "RCONTROL": 0xA3,
    "LMENU": 0xA4, "RMENU": 0xA5,
    "OEM_1": 0xBA, "OEM_PLUS": 0xBB, "OEM_COMMA": 0xBC, "OEM_MINUS": 0xBD,
    "OEM_PERIOD": 0xBE, "OEM_2": 0xBF, "OEM_3": 0xC0, "OEM_4": 0xDB,
    "OEM_5": 0xDC, "OEM_6": 0xDD, "OEM_7": 0xDE, "OEM_8": 0xDF,
    "OEM_102": 0xE2,
}
# 反向:码 → 名称
VK_NAME: dict[int, str] = {v: k for k, v in VK.items()}


def parse_vk(name_or_code: str | int) -> int:
    """把 'F1' / 'ctrl' / '0x71' / '113' 都解析为 vk 整数."""
    if isinstance(name_or_code, int):
        return name_or_code & 0xFFFF
    s = str(name_or_code).strip()
    if not s:
        raise ValueError("空按键名")
    # 数字(十进制 / 十六进制)
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16) & 0xFFFF
    if s.isdigit():
        return int(s) & 0xFFFF
    # 名称(大小写不敏感,允许 'ctrl' / 'esc' / 'return' 等别名)
    key = s.upper().replace(" ", "_")
    # 常用别名
    aliases = {
        "ENTER": "RETURN", "ESC": "ESCAPE", "DEL": "DELETE", "INS": "INSERT",
        "PGUP": "PRIOR", "PGDN": "NEXT", "CAPSLOCK": "CAPITAL", "CTRL": "CONTROL",
        "ALT": "MENU", "WIN": "LWIN", "ARROWLEFT": "LEFT", "ARROWRIGHT": "RIGHT",
        "ARROWUP": "UP", "ARROWDOWN": "DOWN",
        "N0": "NUMPAD0", "N1": "NUMPAD1", "N2": "NUMPAD2", "N3": "NUMPAD3",
        "N4": "NUMPAD4", "N5": "NUMPAD5", "N6": "NUMPAD6", "N7": "NUMPAD7",
        "N8": "NUMPAD8", "N9": "NUMPAD9",
        "+": "OEM_PLUS", "-": "OEM_MINUS", ",": "OEM_COMMA", ".": "OEM_PERIOD",
        "/": "OEM_2", ";": "OEM_1", "'": "OEM_7", "`": "OEM_3",
        "[": "OEM_4", "]": "OEM_6", "\\": "OEM_5", "=": "OEM_PLUS",
    }
    key = aliases.get(key, key)
    if key in VK:
        return VK[key]
    raise ValueError(f"未知按键: {name_or_code}")


def format_vk(vk: int) -> str:
    return VK_NAME.get(vk & 0xFFFF, f"0x{vk & 0xFFFF:02X}")


# === ctypes 结构体 ===
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", w.LONG),
        ("dy", w.LONG),
        ("mouseData", w.DWORD),
        ("dwFlags", w.DWORD),
        ("time", w.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", w.WORD),
        ("wScan", w.WORD),
        ("dwFlags", w.DWORD),
        ("time", w.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", w.DWORD),
        ("wParamL", w.WORD),
        ("wParamH", w.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", w.DWORD),
        ("u", INPUT_UNION),
    ]


# === ctypes 函数原型 ===
_user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
_user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = w.UINT
_user32.GetSystemMetrics.argtypes = [c.c_int]
_user32.GetSystemMetrics.restype = c.c_int  # type: ignore[arg-type]
_user32.SetCursorPos.argtypes = [c.c_int, c.c_int]  # type: ignore[arg-defined]
_user32.SetCursorPos.restype = w.BOOL
_user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
_user32.GetCursorPos.restype = w.BOOL
_user32.mouse_event.argtypes = [w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.c_size_t]
_user32.mouse_event.restype = None

# 修正 SetCursorPos 类型
_user32.SetCursorPos.argtypes = [ctypes.c_long, ctypes.c_long]
_user32.SetCursorPos.restype = w.BOOL


# === 枚举 ===
class InputEventType(str, enum.Enum):
    MOUSE_MOVE = "mouse_move"
    MOUSE_CLICK = "mouse_click"
    MOUSE_SCROLL = "mouse_scroll"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"
    KEY_PRESS = "key_press"  # down + up
    DELAY = "delay"


class MouseButton(str, enum.Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


@dataclass
class _InputHelper:
    """对 SendInput 的一层薄封装."""

    @staticmethod
    def screen_size() -> tuple[int, int]:
        SM_CXSCREEN, SM_CYSCREEN = 0, 1
        return (
            _user32.GetSystemMetrics(SM_CXSCREEN),
            _user32.GetSystemMetrics(SM_CYSCREEN),
        )

    @staticmethod
    def move_abs(x: int, y: int) -> None:
        """鼠标绝对移动(0..65535 归一化坐标)."""
        sw, sh = _InputHelper.screen_size()
        nx = int(x * 65535 / max(1, sw - 1))
        ny = int(y * 65535 / max(1, sh - 1))
        mi = MOUSEINPUT(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, 0)
        _send_inputs([INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi))])

    @staticmethod
    def click_at(x: int, y: int, button: MouseButton = MouseButton.LEFT) -> None:
        """绝对坐标点击(对前台窗口)."""
        _InputHelper.move_abs(x, y)
        time.sleep(0.005)
        if button == MouseButton.LEFT:
            down = MOUSEEVENTF_LEFTDOWN
            up = MOUSEEVENTF_LEFTUP
        elif button == MouseButton.RIGHT:
            down = MOUSEEVENTF_RIGHTDOWN
            up = MOUSEEVENTF_RIGHTUP
        else:
            down = MOUSEEVENTF_MIDDLEDOWN
            up = MOUSEEVENTF_MIDDLEUP
        mi_down = MOUSEINPUT(0, 0, 0, down, 0, 0)
        mi_up = MOUSEINPUT(0, 0, 0, up, 0, 0)
        _send_inputs([
            INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi_down)),
            INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi_up)),
        ])

    @staticmethod
    def move_relative(dx: int, dy: int) -> None:
        mi = MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, 0)
        _send_inputs([INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi))])

    @staticmethod
    def scroll(delta: int, *, horizontal: bool = False) -> None:
        flag = MOUSEEVENTF_WHEEL | (MOUSEEVENTF_HWHEEL if horizontal else 0)
        mi = MOUSEINPUT(0, 0, delta & 0xFFFFFFFF, flag, 0, 0)
        _send_inputs([INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi))])

    @staticmethod
    def key_down(vk: int) -> None:
        ki = KEYBDINPUT(vk & 0xFFFF, 0, 0, 0, 0)
        _send_inputs([INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=ki))])

    @staticmethod
    def key_up(vk: int) -> None:
        ki = KEYBDINPUT(vk & 0xFFFF, 0, KEYEVENTF_KEYUP, 0, 0)
        _send_inputs([INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=ki))])

    @staticmethod
    def key_press(vk: int, hold_ms: int = 30) -> None:
        _InputHelper.key_down(vk)
        time.sleep(max(0, hold_ms) / 1000.0)
        _InputHelper.key_up(vk)


def _send_inputs(inputs: list[INPUT]) -> int:
    """底层 SendInput 封装,返回成功发送数."""
    if not inputs:
        return 0
    arr = (INPUT * len(inputs))(*inputs)
    return int(_user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)))


# === 公共入口 ===
class InputSender:
    """对外的高级输入发送器."""

    def __init__(self, *, default_mode: str = "post") -> None:
        """default_mode: 'post' = PostMessage 模式;'send' = 真实输入模式."""
        self.default_mode = default_mode if default_mode in ("post", "send") else "post"

    # --- 模式 ---
    def click(
        self,
        target: int | tuple[int, int, int],
        button: MouseButton = MouseButton.LEFT,
        *,
        double: bool = False,
        mode: str | None = None,
    ) -> bool:
        """点击.

        target:
        - int = hwnd(用 post 模式按客户区坐标 (0,0) 点;或 send 模式按窗口中心点)
        - (hwnd, x, y) = 按客户区坐标 (x, y) 精确点
        """
        m = mode or self.default_mode
        if isinstance(target, int):
            info = wf.get_window_info(target)
            if info is None:
                return False
            ox, oy = wf.client_origin(target)
            cw, ch = wf.client_size(target)
            x, y = cw // 2, ch // 2
        else:
            hwnd, x, y = target
            ox, oy = wf.client_origin(hwnd)

        if m == "post":
            ok = wf.post_click(int(target) if isinstance(target, int) else int(target[0]), int(x), int(y), button.value, double=double)
            return bool(ok)
        # send 模式:需要前台
        hwnd = int(target) if isinstance(target, int) else int(target[0])
        wf.set_foreground(hwnd)
        time.sleep(0.02)
        sx, sy = ox + int(x), oy + int(y)
        _InputHelper.click_at(sx, sy, button)
        if double:
            _InputHelper.click_at(sx, sy, button)
        return True

    def move(
        self,
        target: tuple[int, int, int],
        *,
        mode: str | None = None,
    ) -> bool:
        """移动鼠标到客户区坐标."""
        hwnd, x, y = target
        m = mode or self.default_mode
        if m == "post":
            return wf.post_mouse_move(hwnd, int(x), int(y))
        ox, oy = wf.client_origin(hwnd)
        wf.set_foreground(hwnd)
        time.sleep(0.005)
        _InputHelper.move_abs(ox + int(x), oy + int(y))
        return True

    def key_down(self, vk: int, *, hwnd: int | None = None, mode: str | None = None) -> bool:
        m = mode or self.default_mode
        if m == "post" and hwnd is not None:
            return wf.post_key(hwnd, vk, down=True)
        _InputHelper.key_down(vk)
        return True

    def key_up(self, vk: int, *, hwnd: int | None = None, mode: str | None = None) -> bool:
        m = mode or self.default_mode
        if m == "post" and hwnd is not None:
            return wf.post_key(hwnd, vk, down=False)
        _InputHelper.key_up(vk)
        return True

    def key_press(self, vk: int, *, hwnd: int | None = None, hold_ms: int = 30, mode: str | None = None) -> bool:
        m = mode or self.default_mode
        if m == "post" and hwnd is not None:
            self.key_down(vk, hwnd=hwnd, mode=m)
            time.sleep(max(0, hold_ms) / 1000.0)
            self.key_up(vk, hwnd=hwnd, mode=m)
            return True
        _InputHelper.key_press(vk, hold_ms=hold_ms)
        return True

    def type_text(self, text: str, *, hwnd: int | None = None, mode: str | None = None) -> bool:
        """输入纯文本(键盘事件)."""
        m = mode or self.default_mode
        for ch in text:
            if m == "post" and hwnd is not None:
                wf.post_key(hwnd, ord(ch.upper()) & 0xFF, down=True)
                wf.post_key(hwnd, ord(ch.upper()) & 0xFF, down=False)
            else:
                _InputHelper.key_press(ord(ch.upper()) & 0xFF)
        return True

    def scroll(self, delta: int, *, hwnd: int | None = None, mode: str | None = None) -> bool:
        """滚轮."""
        m = mode or self.default_mode
        if m == "post" and hwnd is not None:
            # 滚轮 PostMessage 比较麻烦,这里简化用 send
            wf.set_foreground(hwnd)
            time.sleep(0.01)
        _InputHelper.scroll(delta)
        return True