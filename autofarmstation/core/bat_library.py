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

    # --- 拖拽导入 ---
    def import_bat(self, src_path: str | Path,
                   *, category: str = "我的脚本",
                   title: str | None = None,
                   desc: str | None = None) -> BatEntry:
        """从外部路径(用户拖进来的 .bat / .cmd)导入到用户目录。

        行为:
          - 仅接受真实文件(.bat / .cmd 后缀),否则 ValueError
          - 读源文件内容(先 utf-8,失败回退 gbk/utf-16,照顾 Windows 中文批处理)
          - 目标文件名:沿用源 stem(去除不安全字符),后缀统一 .bat;
            若已存在则自动加 _2 / _3 ...
          - 写 manifest 条目(id 形如 user_<safe_title>_<hex6>),返回新条目

        失败抛出 ValueError / FileNotFoundError / OSError,调用方自己捕获。
        """
        src = Path(src_path)
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"源文件不存在:{src}")
        suffix = src.suffix.lower()
        if suffix not in (".bat", ".cmd"):
            raise ValueError(f"只支持 .bat / .cmd 文件,收到:{src.name}")

        # 读源文件内容(优先 utf-8,回退 gbk → utf-16)
        raw_bytes = src.read_bytes()
        text = _decode_bat_bytes(raw_bytes)

        self._user_dir.mkdir(parents=True, exist_ok=True)
        # 目标文件名:沿用 stem
        target_stem = _safe_filename(src.stem) or "imported"
        file_name = f"{target_stem}.bat"
        bat_path = self._user_dir / file_name
        if bat_path.exists():
            for i in range(2, 1000):
                cand = self._user_dir / f"{target_stem}_{i}.bat"
                if not cand.exists():
                    bat_path = cand
                    file_name = bat_path.name
                    break
            else:
                raise OSError(f"用户目录里 {target_stem}_N.bat 都已存在,导入失败")

        # 在原内容前加一段说明,标明这是拖进来的
        header = (
            f"rem ============================================\n"
            f"rem   来源:{src.name}\n"
            f"rem   导入于:{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"rem   路径:{bat_path}\n"
            f"rem ============================================\n"
        )
        bat_path.write_text(header + text, encoding="utf-8")

        # 默认 title/desc
        if not title:
            title = src.stem
        if desc is None:
            desc = f"从外部拖入:{src}"
        safe_title = _safe(title) or "imported"
        eid = f"user_{safe_title}_{uuid.uuid4().hex[:6]}"
        manifest = _read_or_init_manifest(self._user_dir)
        manifest.setdefault("entries", []).append({
            "id": eid, "title": safe_title, "desc": desc,
            "file": file_name, "category": category, "tags": ["拖拽导入"],
            "confirm": True, "args": [],
        })
        _write_manifest(self._user_dir, manifest)
        return _materialize({
            "id": eid, "title": safe_title, "desc": desc,
            "file": file_name, "category": category, "tags": ["拖拽导入"],
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
        """首次启动时把内置 manifest 拷到用户目录(用户可改).已存在则跳过.

        v1.6.2:除了拷贝缺失的 ``.bat`` 文件,还把内置 ``manifest*.json`` 里
        **新增的条目**(按 id)合并进用户的 ``manifest.json`` —— 这样新版本
        加了内置脚本后老用户也能看到,但用户自己改过的条目、用户自加的条目
        都不会被覆盖(只追加不存在的 id)。
        """
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
        # v1.6.2:合并新版本里内置的新条目到用户 manifest
        n += self._merge_builtin_manifest_into_user()
        return n

    def _merge_builtin_manifest_into_user(self) -> int:
        """内置 ``manifest*.json`` 里有、用户 manifest 里没有的条目 → 追加进去.

        不动用户已有条目(用户改的 title / desc / args 都保留)。
        """
        if not self._builtin_dir.exists():
            return 0
        # 用户 manifest(只动 manifest.json,不碰 manifest-xxx.json 之类的备份)
        user_mf = self._user_dir / "manifest.json"
        try:
            if user_mf.exists():
                user_data = json.loads(user_mf.read_text(encoding="utf-8") or "{}")
            else:
                user_data = {"version": 1, "entries": []}
        except (OSError, json.JSONDecodeError):
            user_data = {"version": 1, "entries": []}
        if not isinstance(user_data.get("entries"), list):
            user_data["entries"] = []
        existing_ids = {
            str(e.get("id") or "")
            for e in user_data["entries"]
            if isinstance(e, dict)
        }

        added = 0
        for mf in sorted(self._builtin_dir.glob("manifest*.json")):
            try:
                if mf.stat().st_size > _MAX_MANIFEST_BYTES:
                    continue
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for raw in data.get("entries") or []:
                if not isinstance(raw, dict):
                    continue
                eid = str(raw.get("id") or "")
                if not eid or eid in existing_ids:
                    continue
                user_data["entries"].append(dict(raw))
                existing_ids.add(eid)
                added += 1
                # 文件本身也得拷到用户目录
                src_file = self._builtin_dir / str(raw.get("file") or "")
                if raw.get("file") and src_file.exists():
                    dst_file = self._user_dir / src_file.name
                    if not dst_file.exists():
                        try:
                            shutil.copy2(src_file, dst_file)
                        except OSError:
                            pass
        if added:
            try:
                _write_manifest(self._user_dir, user_data)
                _log.info("内置脚本库更新:新增 %d 个条目到用户 manifest", added)
            except OSError as ex:
                _log.warning("写用户 manifest 失败: %s", ex)
        return added

    def user_data_dir(self) -> Path:
        """返回整个用户数据目录(%APPDATA%\\AutoFarmStation),便于 UI 提供入口."""
        return self._user_dir.parent

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


def _decode_bat_bytes(raw: bytes) -> str:
    """尝试用 utf-8 / gbk / utf-16 解码 bat 文件字节,任意一个成功就返回。

    中文 Windows 上用户写的 .bat 经常是 GBK(系统默认记事本编码);
    偶有 PowerShell 写的用 UTF-16 LE BOM。
    """
    for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # 兜底:latin-1 必不会失败,虽然乱码但不至于丢内容
    return raw.decode("latin-1", errors="replace")


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