"""连点器.

特性:
- 多点位轮询(每个点位有相对该窗口的 x, y;并额外记一份 0~1 的比例坐标,
  窗口被缩放 / 铺满 / 换显示器后依然能打中同一个位置)
- 间隔时间 + 随机抖动(±%)
- 总次数(0 = 无限)
- 启停控制,线程安全
- 回调:每次点击/启动/停止触发
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .input_sender import InputEventType, InputSender, MouseButton


@dataclass
class ClickPoint:
    """一个点击点位(相对目标窗口客户区坐标).

    ``x`` / ``y`` 是**客户区像素**;``rx`` / ``ry`` 是同一位置在该窗口
    客户区里的**比例**(0.0~1.0),-1 表示没有比例信息(老数据)。

    为什么两套都存:本软件「聚焦」默认会把游戏窗口铺满整个显示器工作区
    (见 ``window_finder.fit_to_work_area``),窗口一变大,原来记的像素
    坐标就打在别的地方了。存了比例之后,点击前按当前客户区尺寸重新换算,
    窗口放大 / 缩小 / 挪到别的分辨率显示器上都能打中同一个「位置」。
    """

    x: int
    y: int
    button: MouseButton = MouseButton.LEFT
    double: bool = False
    rx: float = -1.0
    ry: float = -1.0

    def has_ratio(self) -> bool:
        return self.rx >= 0.0 and self.ry >= 0.0

    def resolve(self, client_w: int, client_h: int) -> tuple[int, int]:
        """算出当前该点下去的客户区像素坐标."""
        if self.has_ratio() and client_w > 0 and client_h > 0:
            return (int(round(self.rx * client_w)), int(round(self.ry * client_h)))
        return (int(self.x), int(self.y))

    def set_ratio(self, client_w: int, client_h: int) -> bool:
        """按给定客户区尺寸记录比例.尺寸无效时返回 False."""
        if client_w <= 0 or client_h <= 0:
            return False
        self.rx = max(0.0, min(1.0, self.x / float(client_w)))
        self.ry = max(0.0, min(1.0, self.y / float(client_h)))
        return True

    def space_text(self) -> str:
        if self.has_ratio():
            return f"比例 {self.rx * 100:.1f}% , {self.ry * 100:.1f}%"
        return f"像素 {self.x}, {self.y}"


@dataclass
class AutoClickerConfig:
    """连点器配置."""

    hwnd: int = 0
    points: list[ClickPoint] = field(default_factory=list)
    interval_ms: int = 100  # 点击间隔(毫秒)
    jitter_pct: int = 0  # 抖动百分比(0~100)
    jitter_px: int = 0  # 位置抖动(像素,0=关闭;每次点击在点位 ±N px 内随机偏移,更像真人)
    total_clicks: int = 0  # 0 = 无限
    run_minutes: int = 0  # 0 = 不限时;>0 表示运行满 N 分钟后自动停止
    loop: bool = True  # 到尾后回到第一点
    mode: str = "auto"  # 'auto' (默认先后台投递,失败改真实输入) / 'post' / 'send'
    # 记点位时该窗口的客户区尺寸,用来给老点位补算比例 / 「按当前窗口重算比例」
    client_w: int = 0
    client_h: int = 0

    def ratio_ready(self) -> bool:
        """是否所有点位都带比例信息(带比例的才不怕窗口缩放)."""
        return bool(self.points) and all(p.has_ratio() for p in self.points)

    def rebase_ratios(self, client_w: int, client_h: int) -> int:
        """按给定客户区尺寸重算所有点位的比例,返回成功的个数."""
        n = 0
        for p in self.points:
            if p.set_ratio(client_w, client_h):
                n += 1
        if n:
            self.client_w = int(client_w)
            self.client_h = int(client_h)
        return n

    # --- 序列化 ---
    def to_dict(self) -> dict:
        return {
            "hwnd": self.hwnd,
            "points": [
                {
                    "x": p.x, "y": p.y, "button": p.button.value, "double": p.double,
                    "rx": p.rx, "ry": p.ry,
                }
                for p in self.points
            ],
            "interval_ms": self.interval_ms,
            "jitter_pct": self.jitter_pct,
            "jitter_px": self.jitter_px,
            "total_clicks": self.total_clicks,
            "run_minutes": self.run_minutes,
            "loop": self.loop,
            "mode": self.mode,
            "client_w": self.client_w,
            "client_h": self.client_h,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AutoClickerConfig":
        pts: list[ClickPoint] = []
        for raw in d.get("points", []) or []:
            try:
                btn = MouseButton(str(raw.get("button", "left")))
            except ValueError:
                btn = MouseButton.LEFT
            try:
                rx = float(raw.get("rx", -1.0))
                ry = float(raw.get("ry", -1.0))
            except (TypeError, ValueError):
                rx = ry = -1.0
            pts.append(ClickPoint(
                x=int(raw.get("x", 0)),
                y=int(raw.get("y", 0)),
                button=btn,
                double=bool(raw.get("double", False)),
                rx=rx,
                ry=ry,
            ))
        return cls(
            hwnd=int(d.get("hwnd", 0) or 0),
            points=pts,
            interval_ms=int(d.get("interval_ms", 100)),
            jitter_pct=int(d.get("jitter_pct", 0)),
            jitter_px=max(0, int(d.get("jitter_px", 0) or 0)),
            total_clicks=int(d.get("total_clicks", 0)),
            run_minutes=max(0, int(d.get("run_minutes", 0) or 0)),
            loop=bool(d.get("loop", True)),
            mode=str(d.get("mode", "auto")),
            client_w=int(d.get("client_w", 0) or 0),
            client_h=int(d.get("client_h", 0) or 0),
        )


class AutoClicker:
    """连点器线程.

    用法:
        c = AutoClicker(AutoClickerConfig(hwnd=..., points=[...], interval_ms=100))
        c.on_click = lambda: print('click!')
        c.start()
        ...
        c.stop()
    """

    def __init__(
        self,
        config: AutoClickerConfig,
        sender: InputSender | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self._sender = sender or InputSender(default_mode=config.mode)
        self._log = logger or logging.getLogger("autofarmstation.autoclicker")
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()  # 默认非暂停
        self._done_count = 0
        self._fail_count = 0
        self._lock = threading.Lock()
        self._started_at: float = 0.0
        # 回调
        self.on_click: Callable[[int, ClickPoint], None] | None = None  # (count, point)
        self.on_start: Callable[[], None] | None = None
        self.on_stop: Callable[[], None] | None = None
        self.on_pause: Callable[[bool], None] | None = None  # (paused)

    # --- 状态 ---
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return not self._pause.is_set()

    @property
    def click_count(self) -> int:
        with self._lock:
            return self._done_count

    @property
    def fail_count(self) -> int:
        """本次运行里投递失败的点击次数(连续失败说明发送方式不适用)."""
        with self._lock:
            return self._fail_count

    @property
    def runtime_seconds(self) -> float:
        if not self._started_at:
            return 0.0
        return time.time() - self._started_at

    # --- 控制 ---
    def start(self) -> bool:
        if self.is_running:
            return False
        if not self.config.points:
            self._log.warning("连点器没有点位,不启动")
            return False
        if not self.config.hwnd:
            self._log.warning("连点器缺少目标窗口")
            return False
        self._stop.clear()
        self._pause.set()
        self._done_count = 0
        self._fail_count = 0
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._run, name=f"AutoClicker-{self.config.hwnd}", daemon=True
        )
        self._thread.start()
        if self.on_start:
            try:
                self.on_start()
            except Exception as e:  # noqa: BLE001
                self._log.exception("on_start 回调出错: %s", e)
        self._log.info("连点器启动: hwnd=%s 点位=%d 间隔=%dms",
                       self.config.hwnd, len(self.config.points), self.config.interval_ms)
        return True

    def stop(self) -> bool:
        if not self.is_running:
            return False
        self._stop.set()
        self._pause.set()  # 解除暂停,让线程能快速退出
        # join 但不阻塞 UI
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        if self.on_stop:
            try:
                self.on_stop()
            except Exception as e:  # noqa: BLE001
                self._log.exception("on_stop 回调出错: %s", e)
        self._log.info("连点器停止: 已点击 %d 次", self.click_count)
        return True

    def pause(self, paused: bool = True) -> None:
        if paused:
            self._pause.clear()
        else:
            self._pause.set()
        if self.on_pause:
            try:
                self.on_pause(paused)
            except Exception as e:  # noqa: BLE001
                self._log.exception("on_pause 回调出错: %s", e)

    def toggle_pause(self) -> bool:
        new_state = not self.is_paused
        self.pause(new_state)
        return new_state

    # --- 内部 ---
    def _sleep_interval(self) -> None:
        """睡眠 interval_ms,可被 pause/stop 中断."""
        base = max(1, self.config.interval_ms) / 1000.0
        jitter_pct = max(0, min(100, self.config.jitter_pct))
        if jitter_pct > 0:
            factor = 1.0 + random.uniform(-jitter_pct / 100.0, jitter_pct / 100.0)
            base *= factor
        # 分片 sleep 以快速响应 stop
        end = time.time() + base
        while time.time() < end:
            if self._stop.is_set():
                return
            if not self._pause.is_set():
                # 暂停时,等信号
                self._pause.wait(timeout=0.05)
                continue
            time.sleep(min(0.05, end - time.time()))

    def _run(self) -> None:
        idx = 0
        try:
            while not self._stop.is_set():
                # 暂停
                while not self._pause.is_set() and not self._stop.is_set():
                    self._pause.wait(timeout=0.1)

                # 检查窗口是否还存在(窗口销毁才停;最小化不停 —— post 消息
                # 对最小化窗口照样有效,挂机时把游戏最小化是常态,
                # 之前「最小化连点器就死」是个大坑)
                if self._stop.is_set():
                    return
                from . import window_finder as wf
                info = wf.get_window_info(self.config.hwnd)
                if info is None:
                    self._log.info("窗口已销毁,连点器自动停止 (hwnd=%s)", self.config.hwnd)
                    return
                if info.is_minimized and self._sender.default_mode == "send":
                    # send 模式必须前台,最小化时打不到 —— 跳过这一轮,不清空计数
                    self._sleep_interval()
                    continue

                pts = self.config.points
                if not pts:
                    return
                if idx >= len(pts):
                    if self.config.loop:
                        idx = 0
                    else:
                        return

                p = pts[idx]
                idx += 1
                # 限流:次数
                if self.config.total_clicks and self._done_count >= self.config.total_clicks:
                    return
                # 限流:运行时长(分钟)
                if self.config.run_minutes and \
                        self.runtime_seconds >= self.config.run_minutes * 60:
                    self._log.info("已达运行时长上限 %d 分钟,连点器自动停止 (hwnd=%s)",
                                   self.config.run_minutes, self.config.hwnd)
                    return

                # 带比例的点位按「当前」客户区尺寸换算成像素,这样窗口被
                # 聚焦铺满 / 手动缩放 / 换显示器之后依然打中同一个位置。
                cx, cy = p.x, p.y
                if p.has_ratio():
                    cw, ch = wf.client_size(self.config.hwnd)
                    if cw > 0 and ch > 0:
                        cx, cy = p.resolve(cw, ch)

                # 位置抖动:每次点击在 ±jitter_px 内随机偏移,更像真人,
                # 也能规避一些游戏「完全同坐标连点」的检测。
                jpx = max(0, int(self.config.jitter_px))
                if jpx > 0:
                    cw2, ch2 = wf.client_size(self.config.hwnd)
                    cx += random.randint(-jpx, jpx)
                    cy += random.randint(-jpx, jpx)
                    if cw2 > 0 and ch2 > 0:
                        cx = max(0, min(int(cw2) - 1, cx))
                        cy = max(0, min(int(ch2) - 1, cy))

                try:
                    ok, reason = self._sender.click((self.config.hwnd, cx, cy), p.button, double=p.double)
                    if not ok:
                        with self._lock:
                            self._fail_count += 1
                        self._log.warning("点击失败: %s", reason or "未知原因")
                    elif reason:
                        # auto 模式回退提示,不是错误
                        self._log.info("连点回退: %s", reason)
                except Exception as e:  # noqa: BLE001
                    with self._lock:
                        self._fail_count += 1
                    self._log.warning("点击异常: %s", e)

                with self._lock:
                    self._done_count += 1
                if self.on_click:
                    try:
                        self.on_click(self._done_count, p)
                    except Exception as e:  # noqa: BLE001
                        self._log.exception("on_click 回调出错: %s", e)

                self._sleep_interval()
        finally:
            self._log.debug("连点器线程退出")