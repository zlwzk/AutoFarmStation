"""配置持久化.

所有用户设置保存在 %APPDATA%\\AutoFarmStation\\config.json.
线程安全,内部用 RLock 保护.
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

from .paths import atomic_write, config_path


# === 默认配置(每次新装都用这份) ===
DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "ui": {
        "theme": "dark",  # dark / light
        "language": "zh-CN",
        "preview_fps": 1.0,  # 预览刷新频率
        "preview_columns": 0,  # 0=自适应
        "minimize_to_tray": True,
        "start_minimized": False,
        "confirm_exit": True,
        "restore_session_ask": True,  # 启动时弹框问是否恢复上次的窗口
        # 卡片尺寸(用户可在设置里自行调节)
        "card_min_width": 240,
        "card_min_height": 190,
        # 聚焦时把游戏窗口铺满所在显示器工作区,保证整个界面完整可见
        "focus_fit_screen": True,
        # 卡片角落显示该窗口的 CPU / 内存占用
        "show_resource_on_card": True,
        # Steam 叠加层:勾选后写 %APPDATA%\Steam\steam.cfg 关闭 Shift+Tab
        # 实际生效需要重启 Steam 客户端(配置会被 store 进程读取)
        "disable_steam_overlay": False,
        "steam_overlay_backup": "",  # 上次写入前的原文件内容(留空表示尚未备份)
        "steam_overlay_applied_at": "",
    },
    "global_hotkeys": {
        "start_all": "F9",
        "stop_all": "F10",
        "panic": "Ctrl+Alt+P",  # 一键急停
    },
    "tracked_processes": [],  # list[dict]: {hwnd, pid, name, title, exe, added_at}
    "presets": [],  # list[dict]: 用户保存的预设
    # 新建追踪窗口时,自动给「连点器」填充的默认参数
    "defaults": {
        "clicker_interval_ms": 200,
        "clicker_jitter_ms": 30,
        "clicker_button": "left",  # left / right / middle
        "clicker_mode": "fixed",   # fixed / random / hold
    },
    "settings": {
        "check_updates": True,
        "update_check_interval_hours": 1,  # 自动检查频率:1/6/12/24
        "last_update_check_at": "",  # ISO 时间戳,记录上次检查时间
        "last_update_found": "",  # 上次检查发现的新版本
        "skipped_version": "",  # 用户点过「稍后」跳过的版本,不再提示
        "auto_start_windows": False,
        "log_level": "INFO",
    },
    # 游戏进程的启动/结束策略
    "launch": {
        "auto_wake_on_restore": True,  # 恢复会话时,游戏没开就自动唤醒
        "steam_timeout_sec": 90,  # 等待 Steam 客户端起来的秒数
        "close_games_on_exit": True,  # 关闭本软件时一并结束被追踪的游戏
        "kill_tree": True,  # 结束进程时连同子进程(Steam 启动的游戏通常有 launcher 父进程)
    },
    # 音量(取值范围 0.0~1.0;-1 表示「不接管」,保持原样)
    #
    # v1.6.0 起「整体音量」分成两件互不相干的事:
    #   * group_*  —— 只作用于**本软件已加入的窗口**的音频会话,
    #                 绝不影响其它程序(默认就是这一套);
    #   * system_* —— 真正的系统总音量(任务栏音量条),**默认不接管**,
    #                 只有显式打开 control_system_master 才会去动它。
    "audio": {
        "group_volume": -1,  # 所有已加入窗口的统一音量
        "group_mute": False,  # 把所有已加入窗口静音
        "apply_group_on_start": False,  # 启动时对已加入窗口套用上面的值
        "control_system_master": False,  # 是否允许本软件改系统总音量(默认关)
        "system_master_volume": -1,
        "system_master_mute": False,
        "default_session_volume": -1,  # 新加入窗口时默认设的单窗口音量
        "apply_session_on_add": False,
        "show_volume_on_card": True,  # 卡片上显示音量按钮
    },
    # 挂机时段:只在指定时间段内允许挂机,时段外自动暂停
    "farm_window": {
        "enabled": False,
        "start": "22:00",
        "end": "08:00",
        "action": "pause",  # pause = 时段外暂停、回到时段自动恢复;stop = 只停不自动恢复
    },
    # 进程监控与资源告警
    "monitor": {
        "resource_alert_enabled": True,  # 单个窗口 CPU/内存超阈值时提醒一次
        "cpu_alert_pct": 90.0,
        "mem_alert_mb": 4096.0,
    },
    # Steam 状态联动:挂机时自动切换 Steam 在线状态,停止后还原
    "steam": {
        "enabled": False,
        "farm_state": "online",  # online / away / invisible
        "restore_previous": True,  # 挂机结束后还原挂机前的状态
        "verify": True,  # 切换后回读本地配置校验是否生效
    },
    "stats": {
        "total_runtime_ms": 0,
        "total_clicks": 0,
        "total_keys": 0,
        "first_run_iso": "",
        "last_run_iso": "",
        "run_count": 0,
    },
}


def migrate_legacy(raw: dict[str, Any]) -> dict[str, Any]:
    """把老版本配置里的字段搬到新字段上(就地修改并返回).

    v1.6.0 把「整体音量」拆成两件事:
      * 老的 ``master_volume`` / ``master_mute`` 其实是**系统总音量**,
        会连带把 QQ、浏览器、视频一起静音 —— 这不是用户想要的;
      * 新的 ``group_volume`` / ``group_mute`` 只作用于**本软件已加入的窗口**。

    因此这里把老值迁到 ``group_*``(语义更符合直觉),而系统总音量改成
    必须显式打开 ``control_system_master`` 才碰。老键位直接删掉,
    避免设置界面再读到两套冲突的值。
    """
    if not isinstance(raw, dict):
        return raw
    audio = raw.get("audio")
    if isinstance(audio, dict):
        for old, new in (
            ("master_volume", "group_volume"),
            ("master_mute", "group_mute"),
            ("apply_master_on_start", "apply_group_on_start"),
        ):
            if old in audio:
                if new not in audio:
                    audio[new] = audio[old]
                audio.pop(old, None)
    return raw


class Config:
    """线程安全的配置管理器."""

    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._path = path or config_path()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    # --- I/O ---
    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._data = copy.deepcopy(DEFAULT_CONFIG)
                return
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception:
                # 配置文件损坏时,保留损坏副本并重建
                try:
                    bad = self._path.with_suffix(".corrupt.json")
                    self._path.rename(bad)
                except Exception:
                    pass
                self._data = copy.deepcopy(DEFAULT_CONFIG)
                return
            self._data = self._merge_with_defaults(migrate_legacy(raw))

    def save(self) -> None:
        with self._lock:
            atomic_write(self._path, json.dumps(self._data, indent=2, ensure_ascii=False))

    # --- 访问 ---
    def get(self, key_path: str, default: Any = None) -> Any:
        """点分路径读取,如 'ui.theme'."""
        with self._lock:
            cur: Any = self._data
            for k in key_path.split("."):
                if not isinstance(cur, dict) or k not in cur:
                    return default
                cur = cur[k]
            return cur

    def set(self, key_path: str, value: Any) -> None:
        """点分路径写入(自动建中间节点)."""
        with self._lock:
            cur = self._data
            parts = key_path.split(".")
            for k in parts[:-1]:
                if k not in cur or not isinstance(cur[k], dict):
                    cur[k] = {}
                cur = cur[k]
            cur[parts[-1]] = value

    def raw(self) -> dict[str, Any]:
        """获取完整数据副本(线程安全)."""
        with self._lock:
            return copy.deepcopy(self._data)

    def replace(self, new_data: dict[str, Any]) -> None:
        """整体替换(自动与默认值合并补齐)."""
        with self._lock:
            self._data = self._merge_with_defaults(migrate_legacy(new_data))

    # --- 内部 ---
    def _merge_with_defaults(self, raw: dict[str, Any]) -> dict[str, Any]:
        """递归合并:缺字段补默认,多余字段保留."""
        def merge(d: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
            out = dict(base)
            for k, v in d.items():
                if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                    out[k] = merge(v, out[k])
                else:
                    out[k] = v
            return out
        if not isinstance(raw, dict):
            return copy.deepcopy(DEFAULT_CONFIG)
        return merge(raw, DEFAULT_CONFIG)


# 全局单例
_singleton: Config | None = None
_singleton_lock = threading.Lock()


def instance() -> Config:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = Config()
    return _singleton