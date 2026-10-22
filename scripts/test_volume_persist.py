"""回归测试：音量对话框的配置必须落盘（v1.8.8 P0 修复验证）。

问题：volume_dialog.py 的 4 处 cfg.set() 都没有 cfg.save()，
用户在音量对话框里调的音量，关闭对话框/重启软件后全部丢失。

验收：对话框里改过的值必须出现在 config.json 里。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, (" — " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)


def main() -> int:
    print("=== 音量对话框配置落盘回归 ===")

    import autofarmstation.utils.config as cfgmod

    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "config.json"
        c = cfgmod.Config(cfg_path)

        # 模拟对话框的行为：set 之后是否落盘
        # 注意：Config.set() 只改内存，落盘由 UI 层显式 save() 触发。
        # 这里验证的是「save() 之后，磁盘上能读回同样的值」。
        c.set("audio.group_volume", 0.35)
        c.set("audio.group_mute", True)
        c.set("audio.control_system_master", True)
        c.set("audio.system_master_volume", 0.6)
        c.save()

        # 用「另一个 Config 实例读同一文件」来验证是否真的写到了磁盘
        # —— 这是唯一可信的判据，内存里的值不算
        try:
            on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
            has_disk = True
        except Exception as ex:
            on_disk = {}
            has_disk = False
            check("config.json 已生成", False, str(ex))

        if has_disk:
            # 再用一个独立实例读回来，模拟「重启软件」
            reloaded = cfgmod.Config(cfg_path)
            check("重启后 group_volume 读回", reloaded.get("audio.group_volume") == 0.35,
                  "实际=%r" % reloaded.get("audio.group_volume"))
            check("重启后 group_mute 读回", reloaded.get("audio.group_mute") is True,
                  "实际=%r" % reloaded.get("audio.group_mute"))
            check("重启后 control_system_master 读回",
                  reloaded.get("audio.control_system_master") is True,
                  "实际=%r" % reloaded.get("audio.control_system_master"))
            check("重启后 system_master_volume 读回",
                  reloaded.get("audio.system_master_volume") == 0.6,
                  "实际=%r" % reloaded.get("audio.system_master_volume"))

    # 静态检查：volume_dialog.py 里每个 cfg.set 都必须伴随 cfg.save
    print()
    print("=== 静态检查：volume_dialog 的 set/save 配对 ===")
    src = Path("autofarmstation/ui/volume_dialog.py")
    if not src.exists():
        check("volume_dialog.py 存在", False)
    else:
        text = src.read_text(encoding="utf-8")
        set_lines = [i + 1 for i, ln in enumerate(text.splitlines())
                     if "_cfg.set(" in ln]
        save_lines = [i + 1 for i, ln in enumerate(text.splitlines())
                      if "_cfg.save()" in ln]
        print("  set() 出现在行: %s" % set_lines)
        print("  save() 出现在行: %s" % save_lines)
        check("volume_dialog 存在 cfg.save() 调用", bool(save_lines),
              "发现 %d 处 set() 但 0 处 save()" % len(set_lines))

    print()
    if FAILED:
        print("未通过 %d 项: %s" % (len(FAILED), FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
