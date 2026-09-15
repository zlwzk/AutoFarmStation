"""Steam 在线状态自动切换.

挂机时把 Steam 状态自动切到指定值(如「在线」),挂机全部停止后恢复成挂机前的状态。

机制(全程本地,不需要账号密码、不访问网络):

1. 当前登录账号从注册表读:
       HKCU\\Software\\Valve\\Steam\\ActiveProcess → ActiveUser
2. 状态切换用 Steam 自带的 URL 协议触发(等价于在 Steam 里手动点好友状态):
       steam://friends/status/<online|away|invisible>
3. 回读校验(可选):Steam 把当前状态写在
       <Steam>\\userdata\\<accountid>\\config\\localconfig.vdf
   的 ``"FriendStoreLocalPrefs_<accountid>" → ePersonaState``,用它确认切换是否生效。

实测(Steam 客户端):
    online → ePersonaState 1,away → 3,invisible → 7;
    ``offline`` 走该 URL 不生效(实测 20s 内状态未变),因此不对外开放,
    需要「离线」请在 Steam 里手动切。

本模块不依赖 Qt,可在后台线程里用。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

try:  # pragma: no cover - 仅 Windows 有
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]


# === 对外状态名 ===
STATE_ONLINE = "online"
STATE_AWAY = "away"
STATE_INVISIBLE = "invisible"

# 可通过 steam://friends/status/<state> 生效的状态(offline 不生效,已验证)
SUPPORTED_STATES: tuple[str, ...] = (STATE_ONLINE, STATE_AWAY, STATE_INVISIBLE)

# 状态名 → 中文
STATE_LABELS: dict[str, str] = {
    STATE_ONLINE: "在线",
    STATE_AWAY: "离开",
    STATE_INVISIBLE: "隐身",
    "offline": "离线",
    "busy": "忙碌",
    "snooze": "打盹",
    "lookingtotrade": "想交易",
    "lookingtoplay": "想一起玩",
}

# steam://friends/status/<state> → ePersonaState(实测值)
_STATE_TO_PERSONA: dict[str, int] = {
    STATE_ONLINE: 1,
    STATE_AWAY: 3,
    STATE_INVISIBLE: 7,
}

# ePersonaState → 状态名
_PERSONA_TO_STATE: dict[int, str] = {
    0: "offline",
    1: STATE_ONLINE,
    2: "busy",
    3: STATE_AWAY,
    4: "snooze",
    5: "lookingtotrade",
    6: "lookingtoplay",
    7: STATE_INVISIBLE,
}

_STEAM_KEY = r"Software\Valve\Steam"
_STEAM_ACTIVE_KEY = r"Software\Valve\Steam\ActiveProcess"


# === 便捷函数 ===
def state_label(state: str | None) -> str:
    """状态名 → 中文标签(None / 未知都返回「未知」)."""
    if not state:
        return "未知"
    return STATE_LABELS.get(state, state)


def persona_state_name(code: int | None) -> str:
    """ePersonaState 数值 → 状态名."""
    if code is None:
        return "unknown"
    return _PERSONA_TO_STATE.get(int(code), f"state{code}")


def is_supported_state(state: str) -> bool:
    """是否是本模块能通过 URL 切换的状态."""
    return state in SUPPORTED_STATES


def steam_install_path() -> Path | None:
    """从注册表取 Steam 安装目录(取不到返回 None)."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STEAM_KEY) as k:
            raw = str(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        return None
    p = Path(raw.replace("/", "\\"))
    return p if p.exists() else None


def active_account_id() -> int:
    """当前登录的 Steam 32 位 accountid(未登录 / 读不到返回 0)."""
    if winreg is None:
        return 0
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STEAM_ACTIVE_KEY) as k:
            val, _ = winreg.QueryValueEx(k, "ActiveUser")
        aid = int(val)
    except (OSError, TypeError, ValueError):
        return 0
    return aid if aid > 0 else 0


def is_steam_running() -> bool:
    """Steam 客户端是否在跑."""
    try:
        import psutil  # type: ignore

        for p in psutil.process_iter(["name"]):
            try:
                if (p.info.get("name") or "").lower() == "steam.exe":
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False
    except Exception:  # noqa: BLE001
        # psutil 不可用时退化成「注册表里有没有已登录账号」
        return active_account_id() > 0


def localconfig_path(account_id: int | None = None) -> Path | None:
    """<Steam>\\userdata\\<accountid>\\config\\localconfig.vdf."""
    aid = int(account_id or active_account_id())
    if aid <= 0:
        return None
    root = steam_install_path()
    if root is None:
        return None
    p = root / "userdata" / str(aid) / "config" / "localconfig.vdf"
    return p if p.is_file() else None


def parse_persona_state(text: str, account_id: int) -> int | None:
    """从 localconfig.vdf 文本里解析 ePersonaState(纯函数,便于自检)."""
    m = re.search(
        rf'"FriendStoreLocalPrefs_{account_id}"\s*"((?:[^"\\]|\\.)*)"', text
    )
    if not m:
        return None
    raw = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
    try:
        data: Any = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or "ePersonaState" not in data:
        return None
    try:
        return int(data["ePersonaState"])
    except (TypeError, ValueError):
        return None


def read_persona_state(account_id: int | None = None) -> int | None:
    """读当前 ePersonaState(读不到返回 None)."""
    aid = int(account_id or active_account_id())
    if aid <= 0:
        return None
    path = localconfig_path(aid)
    if path is None:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    return parse_persona_state(text, aid)


def read_current_state(account_id: int | None = None) -> str | None:
    """读当前状态名(online / away / invisible / busy ... ,读不到返回 None)."""
    code = read_persona_state(account_id)
    if code is None:
        return None
    return persona_state_name(code)


def open_state_url(state: str) -> None:
    """触发 steam://friends/status/<state>。

    只接受白名单内的状态名,避免把任意字符串塞进 URL。
    """
    if state not in SUPPORTED_STATES:
        raise ValueError(f"不支持的状态:{state!r}(可选 {', '.join(SUPPORTED_STATES)})")
    url = f"steam://friends/status/{state}"
    os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606


# === 控制器 ===
class SteamStatusController:
    """挂机 ↔ Steam 状态联动.

    用法(UI 侧):

        ctrl = SteamStatusController(cfg, logger)
        ctrl.on_running_changed(len(hub.running_hwnds()))   # 每 2s 调一次即可(边沿触发)
        for msg in ctrl.poll_messages():                    # 后台线程产出的提示
            statusbar.showMessage(msg)

    所有切换动作都在后台线程执行(URL 触发 + 回读校验需要 1~2 秒),
    结果以文本形式放进消息队列,由 UI 线程取走,避免跨线程操作控件。
    """

    def __init__(
        self,
        cfg: Any = None,
        logger: logging.Logger | None = None,
        *,
        verify_timeout: float = 6.0,
    ) -> None:
        self._cfg = cfg
        self._log = logger or logging.getLogger("autofarmstation.steam")
        self._lock = threading.RLock()
        self._verify_timeout = float(verify_timeout)
        self._retry_interval = 10.0  # 条件不满足时的重试间隔(秒)

        self._messages: deque[str] = deque(maxlen=20)
        self._once_keys: set[str] = set()

        self._busy = False  # 有切换任务在跑
        self._applied = False  # 当前是否已把状态改成挂机状态
        self._previous_state: str | None = None  # 挂机前的状态(用于还原)
        self._last_attempt = float("-inf")  # 上次尝试进入挂机的时刻(节流用)

    # ---------- 配置 ----------
    def _cfg_get(self, key: str, default: Any) -> Any:
        if self._cfg is None:
            return default
        try:
            val = self._cfg.get(f"steam.{key}", default)
        except Exception:  # noqa: BLE001
            return default
        return default if val is None else val

    @property
    def enabled(self) -> bool:
        return bool(self._cfg_get("enabled", False))

    @property
    def farm_state(self) -> str:
        state = str(self._cfg_get("farm_state", STATE_ONLINE))
        return state if is_supported_state(state) else STATE_ONLINE

    @property
    def restore_previous(self) -> bool:
        return bool(self._cfg_get("restore_previous", True))

    @property
    def verify(self) -> bool:
        return bool(self._cfg_get("verify", True))

    # ---------- 消息 ----------
    def poll_messages(self) -> list[str]:
        """取走后台线程产出的提示(UI 线程调用)."""
        with self._lock:
            out = list(self._messages)
            self._messages.clear()
        return out

    def _notify(self, text: str, *, once_key: str | None = None) -> None:
        """记一条提示;带 once_key 时同一原因只提示一次."""
        with self._lock:
            if once_key is not None:
                if once_key in self._once_keys:
                    return
                self._once_keys.add(once_key)
            self._messages.append(text)
        self._log.info("Steam 状态:%s", text)

    def _clear_once(self, prefix: str) -> None:
        with self._lock:
            self._once_keys = {k for k in self._once_keys if not k.startswith(prefix)}

    # ---------- 查询 ----------
    def current_state(self) -> str | None:
        """当前 Steam 状态名(Steam 未运行 / 读不到返回 None)."""
        try:
            return read_current_state()
        except Exception:  # noqa: BLE001
            return None

    def describe(self) -> str:
        """给设置面板用的一行描述."""
        if not is_steam_running():
            return "Steam 未运行(启动 Steam 并登录后可用)"
        aid = active_account_id()
        if aid <= 0:
            return "未检测到已登录的 Steam 账号"
        name = self.current_state()
        if name is None:
            return f"账号 {aid} · 状态读取失败(可能尚未写入本地配置)"
        return f"账号 {aid} · 当前状态:{state_label(name)}"

    @property
    def applied(self) -> bool:
        with self._lock:
            return self._applied

    # ---------- 切换 ----------
    def set_state_async(self, state: str, *, note: str | None = None) -> bool:
        """异步切换状态;返回是否已排入后台任务."""
        if not is_supported_state(state):
            self._notify(f"不支持的状态:{state}")
            return False
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        text = note or f"Steam 状态切换为「{state_label(state)}」"
        threading.Thread(
            target=self._worker, args=(state, text), daemon=True, name="SteamStatus",
        ).start()
        return True

    def _worker(self, state: str, note: str) -> None:
        try:
            ok, detail = self.apply_state(state)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"出错:{e}"
        finally:
            with self._lock:
                self._busy = False
        self._notify(f"{note} · {detail}" if ok else f"{note} 失败 · {detail}")

    def apply_state(self, state: str) -> tuple[bool, str]:
        """同步切换并校验(会被后台线程调用,也可直接调用做测试)."""
        if not is_supported_state(state):
            return False, f"不支持的状态:{state}"
        aid = active_account_id()
        before = read_persona_state(aid) if aid > 0 else None
        expect = _STATE_TO_PERSONA[state]
        if before == expect:
            return True, f"本来就处于「{state_label(state)}」"

        try:
            open_state_url(state)
        except Exception as e:  # noqa: BLE001
            return False, f"触发 steam:// 失败:{e}"

        if not self.verify:
            return True, f"已触发「{state_label(state)}」(未校验)"

        deadline = time.monotonic() + self._verify_timeout
        while time.monotonic() < deadline:
            time.sleep(0.25)
            cur = read_persona_state(aid)
            if cur is None:
                continue
            if cur == expect:
                return True, f"已确认生效({persona_state_name(cur)})"
            if cur != before:
                # 变了但不是目标值(Steam 对非法状态会回落到「离开」)
                return True, f"已切换({persona_state_name(cur)})"
        if before is None:
            return True, f"已触发「{state_label(state)}」(本地状态读不到,无法校验)"
        return False, (
            f"未在 {self._verify_timeout:.0f}s 内确认,可能被 Steam 拒绝;"
            f"请检查 Steam 是否正常运行"
        )

    # ---------- 挂机联动 ----------
    def on_running_changed(self, running: int) -> None:
        """挂机运行数变化时调用(边沿触发,可每 2s 无脑调一次)."""
        active = int(running) > 0
        with self._lock:
            enabled = self.enabled
            applied = self._applied
            if not enabled:
                if applied:
                    self._restore_locked("已关闭 Steam 状态联动")
                self._clear_once("skip")
                return
            if active and not applied:
                # 节流:Steam 没开时每 2s 轮询一次进程没必要,10s 重试一次即可
                now = time.monotonic()
                if now - self._last_attempt >= self._retry_interval:
                    self._last_attempt = now
                    self._enter_locked()
            elif not active and applied:
                self._restore_locked("挂机已全部停止")

    def _enter_locked(self) -> None:
        if not is_steam_running():
            self._notify("Steam 未运行,暂时跳过状态切换", once_key="skip:not-running")
            return
        if active_account_id() <= 0:
            self._notify("未检测到已登录的 Steam 账号,跳过状态切换", once_key="skip:no-account")
            return
        self._clear_once("skip")
        prev = self.current_state()
        self._previous_state = prev
        self._applied = True
        target = self.farm_state
        self._worker_async(
            target,
            f"开始挂机 → Steam 状态设为「{state_label(target)}」"
            + (f"(挂机前:{state_label(prev)})" if prev else ""),
        )

    def _restore_locked(self, reason: str) -> None:
        self._applied = False
        self._last_attempt = float("-inf")  # 下一轮挂机可以立刻再切
        prev = self._previous_state
        self._previous_state = None
        target: str | None
        if not self.restore_previous:
            target = None
        elif prev and is_supported_state(prev):
            target = prev
        elif prev and prev == self.farm_state:
            target = prev
        else:
            target = None
        if target is None:
            self._notify(f"{reason};保持当前 Steam 状态不动")
            return
        self._worker_async(
            target,
            f"{reason} → Steam 状态还原为「{state_label(target)}」",
        )

    def _worker_async(self, state: str, note: str) -> None:
        with self._lock:
            if self._busy:
                # 上一次切换还没完,排队等它结束后再跑
                threading.Thread(
                    target=self._delayed_worker, args=(state, note), daemon=True,
                    name="SteamStatusQ",
                ).start()
                return
            self._busy = True
        threading.Thread(
            target=self._worker, args=(state, note), daemon=True, name="SteamStatus",
        ).start()

    def _delayed_worker(self, state: str, note: str) -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            with self._lock:
                if not self._busy:
                    self._busy = True
                    break
            time.sleep(0.2)
        else:
            return
        self._worker(state, note)

    def restore_if_needed(self, *, reason: str = "手动还原") -> bool:
        """如果当前处于「挂机状态」,还原成挂机前的状态."""
        with self._lock:
            if not self._applied:
                return False
            self._restore_locked(reason)
        return True

    def shutdown(self) -> None:
        """退出程序时调用:需要还原就立即触发(不做校验,瞬间返回)."""
        with self._lock:
            if not self._applied:
                return
            self._applied = False
            prev = self._previous_state
            self._previous_state = None
            restore = self.restore_previous
        if not restore or not prev or not is_supported_state(prev):
            return
        try:
            open_state_url(prev)
            self._log.info("退出前还原 Steam 状态:%s", state_label(prev))
        except Exception as e:  # noqa: BLE001
            self._log.warning("退出前还原 Steam 状态失败: %s", e)
