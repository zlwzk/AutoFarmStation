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
import os
import shutil
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


# 单个文件解压上限。备份里都是 JSON / 小文件,超过这个数一定不是正常备份,
# 继续 read() 会把内存吃爆(恶意或损坏的 zip 能塞一个几 GB 的条目)。
_MAX_ENTRY_BYTES = 64 * 1024 * 1024


def import_from(src: Path | str, *, backup_current: bool = True) -> tuple[bool, str]:
    """从备份恢复.返回 (是否成功, 提示语).

    两阶段提交:
      1. 先把 zip 里所有文件解到 ``<数据目录>/.restore-tmp/``;
      2. 全部成功才逐个 move 到最终位置;任何一步失败就把已 move 的回滚。

    旧实现是「边解边覆盖」:第 5 个文件失败时,前 4 个已经被换掉了 ——
    原配置回不去,而 docstring 还写着「导入失败时数据不会更糟」。
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

    stage = root / ".restore-tmp"
    # 清掉上次异常中断留下的暂存目录
    try:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return (False, f"无法创建暂存目录:{e}")

    staged: list[tuple[Path, Path]] = []  # (暂存文件, 最终目标)
    try:
        # ---------- 阶段 1:全部解到暂存区 ----------
        with zipfile.ZipFile(src) as z:
            for name in z.namelist():
                if name.endswith("/") or name == MANIFEST:
                    continue
                target = _safe_target(root, name)
                if target is None:
                    continue
                rel = target.relative_to(root.resolve())
                tmp = stage / rel
                tmp.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as fh:
                    # 分块读 + 上限,避免异常条目把内存打爆
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = fh.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > _MAX_ENTRY_BYTES:
                            raise OSError(
                                f"备份内文件过大(>{_MAX_ENTRY_BYTES // (1024*1024)}MB):{name}")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                # 提前校验 JSON 类文件,别等到重启才发现导入了一份坏配置
                if target.suffix.lower() == ".json":
                    try:
                        json.loads(data.decode("utf-8"))
                    except Exception as e:  # noqa: BLE001
                        raise OSError(f"备份内 {name} 不是合法 JSON:{e}")
                tmp.write_bytes(data)
                staged.append((tmp, target))
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as e:
        shutil.rmtree(stage, ignore_errors=True)
        return (False, f"导入失败(未改动任何现有数据):{e}")

    if not staged:
        shutil.rmtree(stage, ignore_errors=True)
        return (False, "备份里没有任何可识别的文件,已中止。")

    # ---------- 阶段 2:逐个提交,失败回滚 ----------
    committed: list[tuple[Path, bytes | None]] = []  # (目标, 原内容或 None=原本不存在)
    try:
        for tmp, target in staged:
            old: bytes | None = None
            if target.exists():
                try:
                    old = target.read_bytes()
                except OSError:
                    old = None
            target.parent.mkdir(parents=True, exist_ok=True)
            # os.replace 在 Windows 上是原子的,不需要先 unlink
            os.replace(str(tmp), str(target))
            committed.append((target, old))
    except OSError as e:
        # 回滚:把已经换上去的恢复成原内容(原本不存在的删掉)
        for target, old in reversed(committed):
            try:
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(old)
            except OSError:
                continue
        shutil.rmtree(stage, ignore_errors=True)
        return (False, f"导入中途失败,已回滚到导入前状态:{e}")

    shutil.rmtree(stage, ignore_errors=True)
    msg = f"已恢复 {len(committed)} 个文件。\n重启软件后全部生效。"
    if saved:
        msg += f"\n\n导入前的原数据已备份到:\n{saved}"
    return (True, msg)
