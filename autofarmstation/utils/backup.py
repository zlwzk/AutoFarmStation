"""配置备份 / 恢复.

把 `%APPDATA%\\AutoFarmStation` 下的**用户数据**打成一个 zip,
用于换机、重装、或者把一套调好的配置分享给朋友。

打包内容::

    manifest.json     备份元信息(版本、时间、条目数)
    config.json       全部设置(含窗口列表、连点参数、定时相关的开关)
    stats.json        运行统计
    session.json      上次会话快照(窗口布局 + 启动信息)
    schedules.json    定时任务
    presets/*.json    自定义预设
    macros/*.json     录制的宏
    bats/*            Bat 脚本库

**不含**日志目录(体积大、且带着环境信息,没必要外传)。

恢复时会先把当前数据整体备份到 ``backups/`` 下,再覆盖 ——
万一导入的是别人的配置,还能一键退回自己原来的。
"""

from __future__ import annotations

import datetime
import json
import zipfile
from pathlib import Path

from .paths import user_data_dir

APP_NAME = "AutoFarmStation"
SCHEMA = 1
MANIFEST = "manifest.json"
# 顶层单文件
DATA_FILES = ("config.json", "stats.json", "session.json", "schedules.json")
# 子目录(整个打包)
DATA_DIRS = ("presets", "macros", "bats")
# 允许出现在备份里的文件大小上限,防止误把小电影打进来
MAX_FILE_BYTES = 32 * 1024 * 1024


def default_name() -> str:
    """默认文件名,例如 afs-backup-20260915-1430.zip."""
    return f"afs-backup-{datetime.datetime.now().strftime('%Y%m%d-%H%M')}.zip"


def _collect(root: Path) -> list[tuple[Path, str]]:
    """收集 (绝对路径, zip 内相对路径)."""
    out: list[tuple[Path, str]] = []
    for name in DATA_FILES:
        p = root / name
        if p.is_file():
            out.append((p, name))
    for d in DATA_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_file():
                out.append((f, f.relative_to(root).as_posix()))
    return out


def export_to(dest: Path | str) -> tuple[bool, str]:
    """把用户数据导出到 ``dest``(zip).返回 (是否成功, 提示语)."""
    dest = Path(dest)
    root = user_data_dir()
    files = _collect(root)
    if not files:
        return (False, "没有可导出的数据(还没保存过设置)。")
    manifest = {
        "app": APP_NAME,
        "schema": SCHEMA,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "file_count": len(files),
        "files": [rel for _p, rel in files],
    }
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
            skipped = 0
            for path, rel in files:
                try:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        skipped += 1
                        continue
                    z.write(path, rel)
                except OSError:
                    skipped += 1
        msg = f"已导出 {len(files) - skipped} 个文件到\n{dest}"
        if skipped:
            msg += f"\n(跳过 {skipped} 个读不到或过大的文件)"
        return (True, msg)
    except OSError as e:
        return (False, f"导出失败:{e}")


def read_manifest(src: Path | str) -> dict:
    """读取备份里的 manifest(失败返回空 dict)."""
    try:
        with zipfile.ZipFile(Path(src)) as z:
            raw = z.read(MANIFEST).decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


def describe(src: Path | str) -> str:
    """给确认框用的一行描述."""
    info = read_manifest(src)
    if not info:
        return "不是本软件导出的备份(缺少 manifest.json),无法识别内容。"
    at = str(info.get("exported_at", ""))[:19].replace("T", " ")
    n = int(info.get("file_count", 0) or 0)
    kind = "本软件" if info.get("app") == APP_NAME else f"未知应用({info.get('app')})"
    return f"来源:{kind}\n导出时间:{at}\n包含 {n} 个文件"


def _safe_target(root: Path, rel: str) -> Path | None:
    """把 zip 内的相对路径映射到 root 下,越界一律拒绝(zip-slip 防护)."""
    rel = str(rel).replace("\\", "/").lstrip("/")
    if not rel or rel.startswith("../") or ".." in Path(rel).parts:
        return None
    top = Path(rel).parts[0]
    if top not in DATA_FILES and top not in DATA_DIRS:
        return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def import_from(src: Path | str, *, backup_current: bool = True) -> tuple[bool, str]:
    """从备份恢复.返回 (是否成功, 提示语).

    默认先给当前数据做一份整体备份放到 ``backups/``,导入失败时数据不会更糟。
    """
    src = Path(src)
    root = user_data_dir()
    info = read_manifest(src)
    if not info:
        return (False, "这个 zip 不是本软件导出的备份,已中止(没有改动任何数据)。")

    saved = ""
    if backup_current:
        bdir = root / "backups"
        ok, _msg = export_to(bdir / default_name())
        if ok:
            saved = str(bdir)

    try:
        with zipfile.ZipFile(src) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            written = 0
            for name in names:
                if name == MANIFEST:
                    continue
                target = _safe_target(root, name)
                if target is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as fh:
                    data = fh.read()
                # 原子写入,避免中途失败留下半截配置
                tmp = target.with_suffix(target.suffix + ".tmp")
                tmp.write_bytes(data)
                if target.exists():
                    target.unlink()
                tmp.replace(target)
                written += 1
    except (OSError, zipfile.BadZipFile) as e:
        return (False, f"导入失败:{e}")

    if written == 0:
        return (False, "备份里没有任何可识别的文件,已中止。")
    msg = f"已恢复 {written} 个文件。\n重启软件后全部生效。"
    if saved:
        msg += f"\n\n导入前的原数据已备份到:\n{saved}"
    return (True, msg)
