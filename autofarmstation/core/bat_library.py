"""bat 脚本库.

仓库内置一份默认库(``bat/`` 目录,含 ``manifest.json`` + N 个 ``.bat``),
用户可以往 ``%APPDATA%\\AutoFarmStation\\bats\\`` 丢自己的脚本,本模块把
两份合并:用户同名 id 覆盖默认。

设计要点:

- ``.bat`` 之外还有 ``manifest.json`` 描述元数据(id/title/desc/category/args...)。
  ``manifest.json`` 可写多份,程序启动时合并所有 ``manifest*.json``。
- 列表合并按 (category, title) 排序展示。
- 运行时 ``subprocess.Popen`` + ``CREATE_NO_WINDOW`` 不弹黑色 cmd 窗口。
- **只删用户目录**里的脚本(默认库只读),防止用户误删内置功能。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils.paths import bundled_bat_dir, user_bat_dir


_log = logging.getLogger("autofarmstation.bat_library")

# 让 subprocess 启动 .bat 不弹黑窗(Windows CREATE_NO_WINDOW = 0x08000000)
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# manifest 单文件超过这个大小就视为可疑,跳过
_MAX_MANIFEST_BYTES = 256 * 1024


@dataclass(frozen=True)
class BatArg:
    name: str           # 参数名(命令行里用 --name=value 传给 bat)
    label: str          # UI 显示
    default: str = ""   # 默认值
    required: bool = False


@dataclass
class BatEntry:
    id: str
    title: str
    desc: str
    file_name: str              # bat 文件名(在 manifest 所在目录)
    category: str = "其他"
    tags: list[str] = field(default_factory=list)
    confirm: bool = True        # 运行前是否弹确认
    args: list[BatArg] = field(default_factory=list)
    builtin: bool = False       # True = 仓库内置(只读)
    # 运行时填
    manifest_dir: Path | None = None
    full_path: Path | None = None

    def resolved_path(self) -> Path | None:
        if self.manifest_dir is None:
            return None
        p = self.manifest_dir / self.file_name
        return p if p.exists() else None


class BatLibrary:
    """合并(内置 + 用户)bat 库的查询/运行入口."""

    def __init__(self, *, builtin_dir: Path | None = None,
                 user_dir: Path | None = None) -> None:
        self._builtin_dir = builtin_dir or bundled_bat_dir()
        self._user_dir = user_dir or user_bat_dir()

    # --- 列表 / 读取 ---
    def list(self) -> list[BatEntry]:
        entries: dict[str, BatEntry] = {}
        # 内置先加载(用户覆盖)
        for e in self._load_dir(self._builtin_dir, builtin=True):
            entries[e.id] = e
        for e in self._load_dir(self._user_dir, builtin=False):
            entries[e.id] = e  # 同 id 直接覆盖
        out = list(entries.values())
        out.sort(key=lambda e: (e.category, e.title, e.id))
        return out

    def get(self, entry_id: str) -> BatEntry | None:
        for e in self.list():
            if e.id == entry_id:
                return e
        return None

    # --- 增 / 删 ---
    def create(self, title: str, *, file_name: str | None = None,
               category: str = "我的脚本", desc: str = "") -> BatEntry:
        """在用户目录新建一个空 bat + manifest 项."""
        self._user_dir.mkdir(parents=True, exist_ok=True)
        safe_title = _safe(title)
        if not file_name:
            file_name = f"{safe_title}.bat"
        file_name = _safe_filename(file_name)
        if not file_name.lower().endswith(".bat"):
            file_name += ".bat"
        bat_path = self._user_dir / file_name
        # 不覆盖:若同名已存在,加后缀
        if bat_path.exists():
            stem = bat_path.stem
            for i in range(2, 100):
                cand = self._user_dir / f"{stem}_{i}.bat"
                if not cand.exists():
                    bat_path = cand
                    file_name = bat_path.name
                    break
        bat_path.write_text(_BAT_TEMPLATE.format(
            title=safe_title, desc=desc or "(无)", date=time.strftime("%Y-%m-%d"),
        ), encoding="utf-8")
        eid = f"user_{safe_title}_{uuid.uuid4().hex[:6]}"
        manifest = _read_or_init_manifest(self._user_dir)
        manifest.setdefault("entries", []).append({
            "id": eid, "title": safe_title, "desc": desc,
            "file": file_name, "category": category, "tags": [],
            "confirm": True, "args": [],
        })
        _write_manifest(self._user_dir, manifest)
        return _materialize({
            "id": eid, "title": safe_title, "desc": desc,
            "file": file_name, "category": category, "tags": [],
            "confirm": True, "args": [],
        }, builtin=False, manifest_dir=self._user_dir)

    def delete(self, entry_id: str) -> bool:
        """只能删用户目录里的脚本."""
        e = self.get(entry_id)
        if e is None or e.builtin:
            return False
        ok = True
        if e.full_path is not None and e.full_path.exists():
            try:
                e.full_path.unlink()
            except OSError as ex:
                _log.warning("删除 bat 失败: %s", ex)
                ok = False
        # 从 manifest.json 移除该条目
        if e.manifest_dir is not None:
            manifest = _read_or_init_manifest(e.manifest_dir)
            before = len(manifest.get("entries", []))
            manifest["entries"] = [
                x for x in manifest.get("entries", []) if x.get("id") != entry_id
            ]
            if len(manifest["entries"]) != before:
                _write_manifest(e.manifest_dir, manifest)
        return ok

    # --- 运行 / 打开 ---
    def run(self, entry_id: str, args: dict[str, str] | None = None
            ) -> subprocess.Popen | None:
        """非阻塞启动 .bat,args 拼成 ``--key=value`` 形式传给脚本."""
        e = self.get(entry_id)
        if e is None or e.full_path is None or not e.full_path.exists():
            return None
        cmd = [str(e.full_path)]
        for a in (args or {}).items():
            cmd.append(f"--{a[0]}={a[1]}")
        try:
            return subprocess.Popen(
                cmd,
                creationflags=_CREATE_NO_WINDOW,
                cwd=str(e.manifest_dir or e.full_path.parent),
            )
        except OSError as ex:
            _log.warning("启动 bat 失败: %s", ex)
            return None

    def reveal(self, entry_id: str) -> bool:
        """在文件管理器中定位脚本."""
        e = self.get(entry_id)
        if e is None or e.full_path is None or not e.full_path.exists():
            return False
        try:
            if sys.platform == "win32":
                # explorer.exe /select, 高亮文件
                subprocess.Popen(
                    ["explorer", "/select,", str(e.full_path)],
                    creationflags=_CREATE_NO_WINDOW,
                )
            else:
                subprocess.Popen(["xdg-open", str(e.full_path.parent)])
            return True
        except OSError as ex:
            _log.warning("打开目录失败: %s", ex)
            return False

    def edit(self, entry_id: str) -> bool:
        """用系统默认编辑器打开脚本."""
        e = self.get(entry_id)
        if e is None or e.full_path is None or not e.full_path.exists():
            return False
        try:
            if sys.platform == "win32":
                os.startfile(str(e.full_path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(e.full_path)])
            return True
        except OSError as ex:
            _log.warning("打开编辑器失败: %s", ex)
            return False

    def open_user_dir(self) -> bool:
        """打开用户 bat 目录(给用户手工复制脚本用)."""
        self._user_dir.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(self._user_dir))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(self._user_dir)])
            return True
        except OSError as ex:
            _log.warning("打开用户目录失败: %s", ex)
            return False

    def user_dir(self) -> Path:
        return self._user_dir

    def builtin_dir(self) -> Path:
        return self._builtin_dir

    def ensure_seeded(self) -> int:
        """首次启动时把内置 manifest 拷到用户目录(用户可改).已存在则跳过."""
        if not self._builtin_dir.exists():
            return 0
        self._user_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for src in self._builtin_dir.iterdir():
            if not src.is_file():
                continue
            if src.suffix.lower() not in (".bat", ".json"):
                continue
            dst = self._user_dir / src.name
            if dst.exists():
                continue
            try:
                shutil.copy2(src, dst)
                n += 1
            except OSError as ex:
                _log.warning("拷贝 %s 失败: %s", src, ex)
        return n

    # --- 内部 ---
    def _load_dir(self, d: Path, *, builtin: bool) -> list[BatEntry]:
        if not d.exists():
            return []
        out: list[BatEntry] = []
        for mf in sorted(d.glob("manifest*.json")):
            try:
                if mf.stat().st_size > _MAX_MANIFEST_BYTES:
                    _log.warning("manifest 过大,跳过: %s", mf)
                    continue
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                _log.warning("manifest 解析失败 %s: %s", mf, e)
                continue
            for raw in data.get("entries") or []:
                if not isinstance(raw, dict):
                    continue
                out.append(_materialize(raw, builtin=builtin, manifest_dir=d))
        return out


# ---------- helpers ----------
def _materialize(raw: dict[str, Any], *, builtin: bool,
                 manifest_dir: Path | None) -> BatEntry:
    args = []
    for a in raw.get("args") or []:
        if not isinstance(a, dict):
            continue
        args.append(BatArg(
            name=str(a.get("name") or ""),
            label=str(a.get("label") or a.get("name") or ""),
            default=str(a.get("default") or ""),
            required=bool(a.get("required", False)),
        ))
    file_name = str(raw.get("file") or f"{raw.get('id')}.bat")
    full = (manifest_dir / file_name) if manifest_dir else None
    return BatEntry(
        id=str(raw.get("id") or file_name),
        title=str(raw.get("title") or raw.get("id") or file_name),
        desc=str(raw.get("desc") or ""),
        file_name=file_name,
        category=str(raw.get("category") or "其他"),
        tags=list(raw.get("tags") or []),
        confirm=bool(raw.get("confirm", True)),
        args=args,
        builtin=builtin,
        manifest_dir=manifest_dir,
        full_path=full,
    )


def _read_or_init_manifest(d: Path) -> dict:
    mf = d / "manifest.json"
    if not mf.exists():
        return {"version": 1, "entries": []}
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": []}


def _write_manifest(d: Path, data: dict) -> None:
    mf = d / "manifest.json"
    tmp = mf.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if mf.exists():
        try:
            mf.unlink()
        except OSError:
            pass
    tmp.replace(mf)


def _safe(s: str) -> str:
    return "".join(ch for ch in s.strip() if ch not in '<>:"/\\|?*\x00').strip() or "未命名"


def _safe_filename(name: str) -> str:
    name = name.strip()
    name = "".join("_" if ch in '<>:"/\\|?*\x00' else ch for ch in name)
    return name.strip(" .") or "unnamed.bat"


# 一个空 bat 模板,带中文注释提示用户怎么用
_BAT_TEMPLATE = """@echo off
rem ============================================
rem   {title}
rem   {desc}
rem   创建于 {date}
rem   —— AutoFarmStation Bat 脚本库
rem ============================================
rem 用法:本脚本可直接双击运行,或被 AutoFarmStation 调用。
rem     通过参数传入可在脚本里用 %1 %2 ... 或解析 --key=value 形式。
rem     例:auto-farm-station.bat --target=window --interval=200

echo [{title}] 开始执行...
echo.

rem —— 在下面写你的逻辑 ——
echo TODO: 在这里加你的命令(复制 mod、解压补丁、启服务...)

echo.
echo [{title}] 完成。
pause
"""