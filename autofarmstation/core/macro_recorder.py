"""宏录制与回放.

录制:pynput 监听鼠标 + 键盘事件,转成 MacroEvent 列表。
回放:在指定 hwnd 上按时间序列回放。

注意:录制的坐标是屏幕绝对坐标;回放时可选择
- 'absolute':按原始屏幕坐标(用 send 模式)
- 'relative' :相对当前窗口客户区偏移(用 post 模式)
"""

from __future__ import annotations

import enum
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from .input_sender import InputSender, parse_vk


class MacroEventType(str, enum.Enum):
    MOUSE_MOVE = "mouse_move"
    MOUSE_CLICK = "mouse_click"
    MOUSE_SCROLL = "mouse_scroll"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"
    DELAY = "delay"  # 显式延时


@dataclass
class MacroEvent:
    type: str  # MacroEventType 值
    t_ms: int = 0  # 相对录制开始的偏移(毫秒)
    x: int = 0  # 鼠标 x(屏幕绝对坐标)
    y: int = 0
    button: str = ""  # left/right/middle
    vk: int = 0  # 虚拟键码
    scroll: int = 0
    note: str = ""


@dataclass
class MacroScript:
    """一个宏脚本."""

    name: str = "未命名宏"
    description: str = ""
    created_at: str = ""
    duration_ms: int = 0
    events: list[MacroEvent] = field(default_factory=list)
    loop: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "MacroScript":
        d = json.loads(text)
        evs = [MacroEvent(**e) for e in d.get("events", [])]
        return cls(
            name=d.get("name", "未命名宏"),
            description=d.get("description", ""),
            created_at=d.get("created_at", ""),
            duration_ms=d.get("duration_ms", 0),
            events=evs,
            loop=d.get("loop", False),
        )


class MacroRecorder:
    """基于 pynput 的录制器.

    注:在 PyInstaller 打包后,pynput 的 keyboard hook 依赖
    `pyHook` 或系统级钩子,部分杀软会拦截。代码用 try-import 隔离,
    失败时给出明确错误。
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("autofarmstation.recorder")
        self._listeners = []
        self._mouse_listener = None
        self._key_listener = None
        self._recording = False
        self._start_ts = 0.0
        self._events: list[MacroEvent] = []
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def event_count(self) -> int:
        with self._lock:
            return len(self._events)

    def start(self) -> bool:
        if self._recording:
            return False
        try:
            from pynput import keyboard, mouse  # type: ignore
        except Exception as e:  # noqa: BLE001
            self._log.error("pynput 不可用,录制失败: %s", e)
            return False

        self._events = []
        self._start_ts = time.time()
        self._recording = True

        def on_move(x, y):
            if not self._recording:
                return
            with self._lock:
                self._events.append(MacroEvent(
                    type=MacroEventType.MOUSE_MOVE.value,
                    t_ms=int((time.time() - self._start_ts) * 1000),
                    x=int(x), y=int(y),
                ))

        def on_click(x, y, button, pressed):
            if not pressed or not self._recording:
                return
            from pynput.mouse import Button  # type: ignore
            name = {Button.left: "left", Button.right: "right", Button.middle: "middle"}.get(
                button, "left"
            )
            with self._lock:
                self._events.append(MacroEvent(
                    type=MacroEventType.MOUSE_CLICK.value,
                    t_ms=int((time.time() - self._start_ts) * 1000),
                    x=int(x), y=int(y),
                    button=name,
                ))

        def on_scroll(x, y, dx, dy):
            if not self._recording:
                return
            with self._lock:
                self._events.append(MacroEvent(
                    type=MacroEventType.MOUSE_SCROLL.value,
                    t_ms=int((time.time() - self._start_ts) * 1000),
                    x=int(x), y=int(y),
                    scroll=int(dy or dx),
                ))

        def on_key_press(k):
            if not self._recording:
                return False
            vk = self._vk_from_key(k)
            with self._lock:
                self._events.append(MacroEvent(
                    type=MacroEventType.KEY_DOWN.value,
                    t_ms=int((time.time() - self._start_ts) * 1000),
                    vk=vk,
                ))

        def on_key_release(k):
            if not self._recording:
                return False
            vk = self._vk_from_key(k)
            with self._lock:
                self._events.append(MacroEvent(
                    type=MacroEventType.KEY_UP.value,
                    t_ms=int((time.time() - self._start_ts) * 1000),
                    vk=vk,
                ))

        try:
            self._mouse_listener = mouse.Listener(  # type: ignore
                on_move=on_move, on_click=on_click, on_scroll=on_scroll
            )
            self._key_listener = keyboard.Listener(  # type: ignore
                on_press=on_key_press, on_release=on_key_release
            )
            self._mouse_listener.start()
            self._key_listener.start()
            return True
        except Exception as e:  # noqa: BLE001
            self._log.error("启动监听器失败: %s", e)
            self._recording = False
            return False

    @staticmethod
    def _vk_from_key(k) -> int:
        try:
            from pynput.keyboard import Key, KeyCode  # type: ignore
        except Exception:
            return 0
        if isinstance(k, KeyCode):
            return int(k.vk or 0) or (ord(k.char.upper()) if k.char and len(k.char) == 1 else 0)
        # 特殊键映射
        special = {
            Key.space: 0x20, Key.enter: 0x0D, Key.tab: 0x09, Key.esc: 0x1B,
            Key.backspace: 0x08, Key.delete: 0x2E, Key.insert: 0x2D,
            Key.home: 0x24, Key.end: 0x23, Key.page_up: 0x21, Key.page_down: 0x22,
            Key.up: 0x26, Key.down: 0x28, Key.left: 0x25, Key.right: 0x27,
            Key.shift: 0x10, Key.shift_l: 0xA0, Key.shift_r: 0xA1,
            Key.ctrl: 0x11, Key.ctrl_l: 0xA2, Key.ctrl_r: 0xA3,
            Key.alt: 0x12, Key.alt_l: 0xA4, Key.alt_r: 0xA5,
            Key.caps_lock: 0x14,
            Key.f1: 0x70, Key.f2: 0x71, Key.f3: 0x72, Key.f4: 0x73,
            Key.f5: 0x74, Key.f6: 0x75, Key.f7: 0x76, Key.f8: 0x77,
            Key.f9: 0x78, Key.f10: 0x79, Key.f11: 0x7A, Key.f12: 0x7B,
        }
        return int(special.get(k, 0))

    def stop(self) -> MacroScript | None:
        if not self._recording:
            return None
        self._recording = False
        try:
            if self._mouse_listener:
                self._mouse_listener.stop()
            if self._key_listener:
                self._key_listener.stop()
        except Exception:
            pass
        self._mouse_listener = None
        self._key_listener = None
        # 按时间排序
        with self._lock:
            events = sorted(self._events, key=lambda e: e.t_ms)
            duration = int((time.time() - self._start_ts) * 1000)
        return MacroScript(
            name=f"宏-{time.strftime('%Y%m%d-%H%M%S')}",
            duration_ms=duration,
            events=events,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )


class MacroPlayer:
    """宏回放器.

    target_mode:
    - 'absolute':用 send 模式按原始屏幕坐标播放(对前台窗口,需要前台)
    - 'window'  :用 post 模式,坐标变换为当前目标 hwnd 客户区坐标
    """

    def __init__(
        self,
        script: MacroScript,
        hwnd: int,
        *,
        target_mode: str = "window",
        speed: float = 1.0,
        loop: bool = False,
        sender: InputSender | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.script = script
        self.hwnd = hwnd
        self.target_mode = target_mode
        self.speed = max(0.05, min(10.0, speed))
        self.loop = loop
        self._sender = sender or InputSender(default_mode="post" if target_mode == "window" else "send")
        self._log = logger or logging.getLogger("autofarmstation.macroplayer")
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self._started_at = 0.0
        self.on_finish: Callable[[], None] | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return not self._pause.is_set()

    def start(self) -> bool:
        if self.is_running:
            return False
        if not self.script.events:
            return False
        self._stop.clear()
        self._pause.set()
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._run, name=f"MacroPlayer-{self.hwnd}", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> bool:
        if not self.is_running:
            return False
        self._stop.set()
        self._pause.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        return True

    def pause(self, paused: bool = True) -> None:
        if paused:
            self._pause.clear()
        else:
            self._pause.set()

    def _sleep_until(self, deadline: float) -> None:
        while time.time() < deadline:
            if self._stop.is_set():
                return
            if not self._pause.is_set():
                self._pause.wait(timeout=0.05)
                continue
            time.sleep(min(0.05, deadline - time.time()))

    def _run(self) -> None:
        from . import window_finder as wf
        # 录制时的初始屏幕位置 → 当前 hwnd 客户区偏移
        if self.target_mode == "window":
            base_event = next(
                (e for e in self.script.events
                 if e.type in (MacroEventType.MOUSE_CLICK.value, MacroEventType.MOUSE_MOVE.value)),
                None,
            )
            ox, oy = wf.client_origin(self.hwnd) if self.hwnd else (0, 0)
            base_x, base_y = (base_event.x, base_event.y) if base_event else (0, 0)
            dx, dy = ox - base_x, oy - base_y
        else:
            dx, dy = 0, 0

        try:
            while not self._stop.is_set():
                if self._stop.is_set():
                    return
                if not wf._is_window(self.hwnd):  # type: ignore[attr-defined]
                    self._log.warning("目标窗口已失效")
                    return
                t0 = time.time()
                for ev in self.script.events:
                    if self._stop.is_set():
                        return
                    # 计算该事件的实际 deadline
                    when = t0 + ev.t_ms / 1000.0 / self.speed
                    self._sleep_until(when)
                    try:
                        if ev.type == MacroEventType.MOUSE_MOVE.value:
                            self._sender.move((self.hwnd, ev.x + dx, ev.y + dy))
                        elif ev.type == MacroEventType.MOUSE_CLICK.value:
                            from .input_sender import MouseButton
                            btn = MouseButton(ev.button or "left")
                            self._sender.click((self.hwnd, ev.x + dx, ev.y + dy), btn)
                        elif ev.type == MacroEventType.MOUSE_SCROLL.value:
                            self._sender.scroll(ev.scroll, hwnd=self.hwnd)
                        elif ev.type == MacroEventType.KEY_DOWN.value:
                            self._sender.key_down(ev.vk, hwnd=self.hwnd)
                        elif ev.type == MacroEventType.KEY_UP.value:
                            self._sender.key_up(ev.vk, hwnd=self.hwnd)
                    except Exception as e:  # noqa: BLE001
                        self._log.warning("回放事件失败: %s", e)
                if not self.loop:
                    break
        finally:
            self._log.debug("宏回放线程退出")
            if self.on_finish:
                try:
                    self.on_finish()
                except Exception:
                    pass