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
    },
    "global_hotkeys": {
        "start_all": "F9",
        "stop_all": "F10",
        "panic": "Ctrl+Alt+P",  # 一键急停
    },
    "tracked_processes": [],  # list[dict]: {hwnd, pid, name, title, exe, added_at}
    "presets": [],  # list[dict]: 用户保存的预设
    "settings": {
        "check_updates": True,
        "auto_start_windows": False,
        "log_level": "INFO",
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
            self._data = self._merge_with_defaults(raw)

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
            self._data = self._merge_with_defaults(new_data)

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