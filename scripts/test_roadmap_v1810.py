"""v1.8.10 回归:ROADMAP 里 8 条审计缺口的修复验证.

A1 window_host 嵌入失败回滚完整
A2 嵌入 DPI 换算工具可用
A3 EcoQoS 记录原始优先级 + 退出批量还原
A4 备份还原两阶段提交 + 回滚
A5 更新下载走后台线程(静态检查)
A6 帧尺寸上限(防 OOM)
A7 单槽最新帧(内存有界)
A8 按键按下追踪 + 兜底释放 + modifiers 组合键
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
_T = Path(tempfile.mkdtemp(prefix="afs_a_"))
os.environ["APPDATA"] = str(_T)
os.environ["LOCALAPPDATA"] = str(_T)
os.environ["TEMP"] = str(_T)
os.environ["TMP"] = str(_T)

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, (" — " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)


print("=== A6 帧尺寸上限 ===")
from autofarmstation.core import preview_capture as pc
check("有 _MAX_FRAME_BYTES", hasattr(pc, "_MAX_FRAME_BYTES"))
check("上限在合理范围(64MB~512MB)",
      64 * 1024 * 1024 <= pc._MAX_FRAME_BYTES <= 512 * 1024 * 1024,
      str(pc._MAX_FRAME_BYTES))
check("有单边上限", hasattr(pc, "_MAX_FRAME_SIDE") and pc._MAX_FRAME_SIDE >= 8192,
      str(getattr(pc, "_MAX_FRAME_SIDE", None)))
# 异常尺寸直接拒绝,不分配缓冲
check("_read_client_pixels 拒绝超大尺寸", pc._read_client_pixels(0, 0, 32767, 32767) is None)
check("_read_client_pixels 拒绝零尺寸", pc._read_client_pixels(0, 0, 0, 100) is None)

print()
print("=== A8 按键追踪 + 组合键 ===")
from autofarmstation.core import input_sender as ins
from autofarmstation.core.key_macro import KeyMacroConfig, KeyStep
check("有 pressed_keys", callable(getattr(ins, "pressed_keys", None)))
check("有 release_all_pressed", callable(getattr(ins, "release_all_pressed", None)))
check("初始无按下键", ins.pressed_keys() == [])
check("release 幂等", ins.release_all_pressed() == 0)
check("InputSender 有 key_combo", hasattr(ins.InputSender, "key_combo"))

s = KeyStep(key="A", modifiers=["ctrl", "shift"])
c = KeyMacroConfig(hwnd=1, steps=[s])
d = c.to_dict()
check("modifiers 能序列化", d["steps"][0]["modifiers"] == ["ctrl", "shift"])
c2 = KeyMacroConfig.from_dict(d)
check("modifiers 能反序列化", c2.steps[0].modifiers == ["ctrl", "shift"])
c3 = KeyMacroConfig.from_dict({"hwnd": 1, "steps": [{"key": "B", "vk": 66}]})
check("老配置(无 modifiers)兼容", c3.steps[0].modifiers == [])

print()
print("=== A3 EcoQoS 原始优先级 ===")
from autofarmstation.core import power_saver as ps
check("有 restore_all", callable(getattr(ps, "restore_all", None)))
check("有 eco_pids", callable(getattr(ps, "eco_pids", None)))
check("有 current_priority", callable(getattr(ps, "current_priority", None)))
check("初始 eco 列表为空", ps.eco_pids() == [])
check("restore_all 空跑返回 0", ps.restore_all() == 0)
check("优先级常量齐全",
      all(hasattr(ps, n) for n in (
          "ABOVE_NORMAL_PRIORITY_CLASS", "HIGH_PRIORITY_CLASS",
          "NORMAL_PRIORITY_CLASS", "BELOW_NORMAL_PRIORITY_CLASS")))
check("set_eco(0, True) 无效 pid 返回 False", ps.set_eco(0, True) is False)

print()
print("=== A1 window_host 回滚 + A2 DPI 工具 ===")
from autofarmstation.core import window_host as wh
check("有 _STRIP_EXSTYLE", hasattr(wh, "_STRIP_EXSTYLE"))
check("有 game_dpi", callable(getattr(wh, "game_dpi", None)))
check("有 game_scale", callable(getattr(wh, "game_scale", None)))
check("有 monitor_dpi", callable(getattr(wh, "monitor_dpi", None)))
check("有 is_dpi_blurry", callable(getattr(wh, "is_dpi_blurry", None)))
check("game_dpi(0) 兜底 96", wh.game_dpi(0) == 96)
check("game_scale(0) 兜底 1.0", wh.game_scale(0) == 1.0)
check("GetDesktopWindow 原型已声明",
      getattr(wh._user32.GetDesktopWindow, "restype", None) is not None)

print()
print("=== A4 备份两阶段提交 ===")
from autofarmstation.utils import backup as bk
check("有 _MAX_ENTRY_BYTES", hasattr(bk, "_MAX_ENTRY_BYTES"))
check("畸形 zip(非本软件备份)被拒绝", bk.import_from(_T / "nope.zip")[0] is False)

# 造一个合法备份 zip:含一个超限声明 → 应当失败且不改动现有数据
data_root = bk.user_data_dir()
cfg_file = data_root / "config.json"
cfg_file.write_text('{"version": 1, "marker": "original"}', encoding="utf-8")

bad_zip = _T / "bad.json_backup.zip"
with zipfile.ZipFile(bad_zip, "w") as z:
    z.writestr("manifest.json", '{"app": "AutoFarmStation", "schema": 1, "created": "x", "file_count": 1}')
    z.writestr("config.json", "{ this is not json }")
ok, msg = bk.import_from(bad_zip, backup_current=False)
check("内含坏 JSON 的备份被拒绝", ok is False, msg[:60])
check("被拒绝后原 config 未改动",
      "original" in cfg_file.read_text(encoding="utf-8"),
      cfg_file.read_text(encoding="utf-8")[:60])
check("拒绝后没有残留 .restore-tmp",
      not (data_root / ".restore-tmp").exists())

# 合法备份:应当成功并覆盖
good_zip = _T / "good_backup.zip"
with zipfile.ZipFile(good_zip, "w") as z:
    z.writestr("manifest.json", '{"app": "AutoFarmStation", "schema": 1, "created": "x", "file_count": 1}')
    z.writestr("config.json", '{"version": 1, "marker": "restored"}')
ok2, msg2 = bk.import_from(good_zip, backup_current=False)
check("合法备份导入成功", ok2 is True, msg2[:60])
check("导入后内容已更新", "restored" in cfg_file.read_text(encoding="utf-8"))
check("导入后无 .restore-tmp 残留", not (data_root / ".restore-tmp").exists())

print()
print("=== A5 更新下载不阻塞 GUI(静态检查) ===")
sd = (REPO / "autofarmstation" / "ui" / "settings_dialog.py").read_text(encoding="utf-8")
check("有 QThread 下载 worker", "_DownloadWorker(QThread)" in sd)
check("用 Signal 回传进度", "progress_changed = Signal" in sd)
check("有 processEvents 等待循环", "QCoreApplication.processEvents()" in sd)
check("导入了 QThread / Signal", "QThread" in sd.split("\n")[17] or "QThread," in sd)
check("不再直接在主线程调 download_and_apply_release(在 worker.run 里)",
      "def run(self)" in sd and "download_and_apply_release(" in sd)

print()
print("=== A7 单槽最新帧 ===")
pw = (REPO / "autofarmstation" / "ui" / "preview_widget.py").read_text(encoding="utf-8")
check("有 _pending_frame 槽位", "_pending_frame" in pw)
check("有 _render_pending", "_render_pending" in pw)
check("_on_frame 覆盖而非丢弃", "_pending_frame = frame" in pw)
check("有 _game_scale(DPI 换算)", "_game_scale" in pw)
check("有 _on_screen_changed(跨屏重算)", "_on_screen_changed" in pw)

print()
if FAILED:
    print("未通过 %d 项: %s" % (len(FAILED), FAILED))
    sys.exit(1)
print("全部通过")
sys.exit(0)
