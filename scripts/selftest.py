"""自检脚本.

不依赖 GUI,跑纯核心逻辑。
每项独立 try/except,失败不影响后续项。

跑法:`python -m scripts.selftest` 或 `python scripts/selftest.py`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

# 让脚本可独立跑(在仓库根目录)
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# 设置一个临时数据目录,避免污染真实 %APPDATA%
_TMP = Path(tempfile.mkdtemp(prefix="afs_selftest_"))
os.environ["APPDATA"] = str(_TMP)
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["TEMP"] = str(_TMP)
os.environ["TMP"] = str(_TMP)


def _div(title: str) -> None:
    print(f"\n=== {title} ===")


def _ok(name: str) -> None:
    print(f"  [OK] {name}")


def _fail(name: str, e: Exception) -> None:
    print(f"  [FAIL] {name}: {type(e).__name__}: {e}")
    traceback.print_exc(limit=3)


# === 测试用例 ===
def t_paths() -> bool:
    _div("paths.py")
    try:
        from autofarmstation.utils import paths
        assert paths.user_data_dir().exists()
        assert paths.user_log_dir().exists()
        assert paths.user_cache_dir().exists()
        assert paths.user_macro_dir().exists()
        assert paths.user_preset_dir().exists()
        # 原子写入
        test = paths.user_data_dir() / "test.txt"
        paths.atomic_write(test, "hello")
        assert test.read_text(encoding="utf-8") == "hello"
        # 安全文件名
        assert paths.safe_filename("a/b\\c|d") == "a_b_c_d"
        _ok("paths")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("paths", e)
        return False


def t_sanitize() -> bool:
    _div("sanitize.py")
    try:
        from autofarmstation.utils import sanitize as s
        # 基本替换:占位符 = %APPDATA% 等,验证不报错
        out = s.sanitize("hello world")
        assert out == "hello world"
        # 路径形式
        out = s.sanitize("C:\\Users\\alice\\AppData\\Local\\Temp")
        assert "%USERPROFILE%" in out or "alice" not in out
        # 路径正则兜底
        out = s.sanitize("see D:\\Users\\bob\\file.txt")
        assert "%USERPROFILE%" in out
        # 显示目录
        assert s.PLACEHOLDER_APPDATA == "%APPDATA%"
        _ok("sanitize")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("sanitize", e)
        return False


def t_logger() -> bool:
    _div("logger.py")
    try:
        from autofarmstation.utils.logger import setup, get
        setup()
        log = get()
        log.info("hello")
        log.warning("warn")
        # 日志文件生成
        from autofarmstation.utils.paths import user_log_dir
        files = list(user_log_dir().glob("*.log"))
        assert files, "日志文件未生成"
        _ok("logger")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("logger", e)
        return False


def t_config() -> bool:
    _div("config.py")
    try:
        from autofarmstation.utils.config import Config
        cfg = Config()
        assert cfg.get("ui.theme") == "dark"
        cfg.set("ui.theme", "light")
        assert cfg.get("ui.theme") == "light"
        cfg.save()
        # 重新加载
        cfg2 = Config()
        assert cfg2.get("ui.theme") == "light"
        # 损坏文件不会崩
        from autofarmstation.utils.paths import config_path
        config_path().write_text("{not valid", encoding="utf-8")
        cfg3 = Config()
        assert cfg3.get("ui.theme") == "dark"  # 重新补默认值
        _ok("config")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("config", e)
        return False


def t_update_checker() -> bool:
    _div("update_checker.py")
    try:
        from autofarmstation.utils.update_checker import is_newer
        assert is_newer("v1.0.0", "v0.9.0")
        assert is_newer("1.0.1", "1.0.0")
        assert not is_newer("v1.0.0", "v1.0.0")
        assert not is_newer("v0.9.0", "v1.0.0")
        assert is_newer("v2.0.0", "v1.99.99")
        _ok("update_checker(version compare)")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("update_checker", e)
        return False


def t_input_sender() -> bool:
    _div("input_sender.py")
    try:
        from autofarmstation.core.input_sender import parse_vk, format_vk, MouseButton, VK
        # 解析常见按键
        assert parse_vk("F1") == VK["F1"]
        assert parse_vk("space") == VK["SPACE"]
        assert parse_vk("ctrl") == VK["CONTROL"]
        assert parse_vk("a") == ord("A")
        assert parse_vk("ENTER") == VK["RETURN"]
        assert parse_vk("ESC") == VK["ESCAPE"]
        assert parse_vk(0x71) == 0x71
        # 反向格式化
        assert format_vk(VK["F1"]) == "F1"
        # 错误
        try:
            parse_vk("")
            return False
        except ValueError:
            pass
        try:
            parse_vk("无此键")
            return False
        except ValueError:
            pass
        _ok("input_sender")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("input_sender", e)
        return False


def t_window_finder() -> bool:
    _div("window_finder.py")
    try:
        from autofarmstation.core import window_finder as wf
        wins = wf.list_visible_windows(title_filter="")
        assert isinstance(wins, list)
        # 自身窗口(由 _is_window 测试)
        assert wf._is_window(0) is False
        _ok(f"window_finder: 找到 {len(wins)} 个可见窗口")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("window_finder", e)
        return False


def t_autoclicker() -> bool:
    _div("autoclicker.py")
    try:
        from autofarmstation.core.autoclicker import AutoClicker, AutoClickerConfig, ClickPoint
        from autofarmstation.core.input_sender import MouseButton
        cfg = AutoClickerConfig(
            hwnd=0,  # 无效 hwnd,线程会立即退出
            points=[ClickPoint(10, 10)],
            interval_ms=10,
        )
        c = AutoClicker(cfg)
        # 不启动;测试 start 在无效 hwnd 下不会崩
        started = c.start()
        assert started is False or c.is_running
        time.sleep(0.2)
        c.stop()
        # 多点位
        cfg2 = AutoClickerConfig(
            hwnd=0,
            points=[
                ClickPoint(0, 0, MouseButton.LEFT),
                ClickPoint(10, 10, MouseButton.RIGHT, double=True),
            ],
            interval_ms=10,
        )
        c2 = AutoClicker(cfg2)
        assert c2.start() is False
        _ok("autoclicker")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("autoclicker", e)
        return False


def t_key_macro() -> bool:
    _div("key_macro.py")
    try:
        from autofarmstation.core.key_macro import KeyMacro, KeyMacroConfig, KeyStep
        cfg = KeyMacroConfig(hwnd=0, steps=[KeyStep(key="F1", vk=0x70)])
        m = KeyMacro(cfg)
        assert m.start() is False  # 无效 hwnd
        # 步骤 vk 自动解析
        cfg2 = KeyMacroConfig(hwnd=0, steps=[KeyStep(key="space")])
        m2 = KeyMacro(cfg2)
        assert cfg2.steps[0].vk != 0
        _ok("key_macro")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("key_macro", e)
        return False


def t_macro_recorder() -> bool:
    _div("macro_recorder.py")
    try:
        from autofarmstation.core.macro_recorder import MacroScript, MacroEvent, MacroEventType
        # to_json / from_json
        script = MacroScript(
            name="test", events=[
                MacroEvent(type=MacroEventType.MOUSE_CLICK.value, t_ms=0, x=10, y=20, button="left"),
                MacroEvent(type=MacroEventType.KEY_DOWN.value, t_ms=100, vk=0x70),
            ],
        )
        j = script.to_json()
        loaded = MacroScript.from_json(j)
        assert loaded.name == "test"
        assert len(loaded.events) == 2
        assert loaded.events[0].button == "left"
        _ok("macro_recorder")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("macro_recorder", e)
        return False


def t_process_manager() -> bool:
    _div("process_manager.py")
    try:
        from autofarmstation.core.process_manager import ProcessManager, TrackedProcess
        pm = ProcessManager()
        assert len(pm) == 0
        # 序列化
        tp = TrackedProcess(hwnd=12345, pid=999, name="test.exe", title="T", exe="x.exe")
        d = tp.to_dict()
        assert d["hwnd"] == 12345
        tp2 = TrackedProcess.from_dict(d)
        assert tp2.hwnd == 12345
        # clear
        pm.clear()
        _ok("process_manager")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("process_manager", e)
        return False


def t_scheduler() -> bool:
    _div("scheduler.py")
    try:
        from autofarmstation.core.scheduler import Scheduler, ScheduledTask, TaskFreq
        s = Scheduler()
        fired = []
        s.register_handler("noop", lambda t: fired.append(t.name))
        s.add(ScheduledTask(name="t1", action="noop", freq=TaskFreq.ONCE))
        assert len(s.list()) == 1
        s.remove("t1")
        assert len(s.list()) == 0
        _ok("scheduler")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("scheduler", e)
        return False


def t_monitor() -> bool:
    _div("monitor.py")
    try:
        from autofarmstation.core.monitor import ProcessMonitor, MonitorEvent, MonitorStatus
        ev = MonitorEvent(hwnd=0, pid=0, status=MonitorStatus.MISSING)
        assert ev.status.value == "missing"
        # 不会启动真实监控(无 GUI)
        from autofarmstation.core.process_manager import ProcessManager
        m = ProcessMonitor(ProcessManager())
        m.on_event(lambda e: None)
        _ok("monitor")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("monitor", e)
        return False


def t_statistics() -> bool:
    _div("statistics.py")
    try:
        from autofarmstation.core.statistics import Statistics
        s = Statistics()
        s.on_session_start()
        s.add_click(10, hwnd=1)
        s.add_key(5, hwnd=2)
        s.add_runtime(1000, hwnd=1)
        snap = s.snapshot()
        assert snap.total_clicks == 10
        assert snap.total_keys == 5
        assert snap.run_count >= 1
        assert snap.per_process.get("1", {}).get("clicks") == 10
        s.save()
        _ok("statistics")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("statistics", e)
        return False


def t_presets() -> bool:
    _div("preset_library.py")
    try:
        from autofarmstation.core.preset_library import PresetLibrary, BUILTIN_PRESETS, autoclicker_dict_to_points, key_macro_dict_to_steps
        lib = PresetLibrary()
        assert len(lib.builtin()) == len(BUILTIN_PRESETS)
        # 匹配
        p = lib.find_match(process_name="melvoridle", title="Melvor Idle")
        assert p is not None
        # 通用
        p2 = lib.find_match(process_name="unknown.exe", title="Whatever")
        assert p2 is not None  # generic 兜底
        # 转换
        pts = autoclicker_dict_to_points({"points": [{"x": 1, "y": 2}]})
        assert len(pts) == 1 and pts[0].x == 1
        steps = key_macro_dict_to_steps({"steps": [{"key": "f1", "hold_ms": 50}]})
        assert len(steps) == 1
        _ok(f"presets: 内置 {len(BUILTIN_PRESETS)} 个")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("presets", e)
        return False


def t_hub() -> bool:
    """自动化中枢:每窗口独立配置 + 多窗口同步执行."""
    _div("automation_hub.py")
    try:
        from autofarmstation.core import AutomationHub, AutoClickerConfig, KeyMacroConfig, ClickPoint, KeyStep
        hub = AutomationHub()

        cfg = AutoClickerConfig(points=[ClickPoint(10, 20), ClickPoint(30, 40)], interval_ms=250)
        # 1) 单窗口独立配置
        hub.set_clicker_config(101, cfg)
        assert hub.clicker_config(101) is not None
        assert hub.clicker_config(101).interval_ms == 250
        assert hub.clicker_config(999) is None  # 未配置的窗口互不影响

        # 2) 广播到多个窗口 —— 每个窗口各存一份,互相独立
        targets = [101, 102, 103]
        hub.broadcast_clicker_config(targets, cfg)
        for h in targets:
            c = hub.clicker_config(h)
            assert c is not None and len(c.points) == 2
        # 改一个窗口不影响其它窗口
        hub.clicker_config(102).interval_ms = 999
        assert hub.clicker_config(101).interval_ms == 250
        assert hub.clicker_config(103).interval_ms == 250
        assert hub.clicker_config(102).interval_ms == 999

        # 3) 键盘宏配置
        kcfg = KeyMacroConfig(steps=[KeyStep(key="f1", vk=0x70, hold_ms=30)])
        hub.broadcast_key_macro_config([201, 202], kcfg)
        assert hub.key_macro_config(201) is not None
        assert hub.key_macro_config(202) is not None

        # 4) 序列化往返
        d = cfg.to_dict()
        back = AutoClickerConfig.from_dict(d)
        assert back.interval_ms == cfg.interval_ms and len(back.points) == 2
        kd = kcfg.to_dict()
        kback = KeyMacroConfig.from_dict(kd)
        assert kback.steps[0].key == "f1" and kback.steps[0].vk == 0x70

        # 5) 导出/导入
        hub2 = AutomationHub()
        hub2.import_configs(hub.export_configs())
        assert hub2.clicker_config(101) is not None
        assert hub2.key_macro_config(201) is not None

        # 6) 状态查询(窗口无效时不应崩溃)
        assert hub.is_running(101) is False
        assert hub.status_text(101) == ""
        assert hub.running_hwnds() == []
        assert 101 in hub.configured_hwnds()
        assert hub.has_config(101) is True
        assert hub.has_config(999) is False

        # 7) 缺配置时 start_many 应优雅失败(不抛异常)
        res = hub.start_many([99991, 99992], clicker=True, key_macro=True)
        assert res.started == 0 and res.failed == 2

        # 8) forget 清理
        hub.forget(101)
        assert hub.clicker_config(101) is None
        assert hub.has_config(101) is False

        _ok("hub: 单窗口独立 + 多窗口同步 + 序列化")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("automation_hub", e)
        return False


def t_io_smoke() -> bool:
    _div("IO: 离线冒烟(不进 GUI)")
    try:
        # 仅确认所有模块都能 import
        import autofarmstation  # noqa
        from autofarmstation import core  # noqa
        from autofarmstation import ui  # noqa
        from autofarmstation import utils  # noqa
        from autofarmstation.utils import paths, config, logger, sanitize, update_checker
        from autofarmstation.core import (
            window_finder, input_sender, autoclicker, key_macro, macro_recorder,
            preview_capture, process_manager, scheduler, monitor, statistics, preset_library,
            automation_hub,
        )
        from autofarmstation.ui import main_window, preview_grid, action_panel, preview_widget
        _ok("imports ok")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("imports", e)
        return False


def t_gui_smoke() -> bool:
    """离屏构建整窗口(不进入事件循环),验证 UI 装配无异常."""
    _div("UI: 离屏装配(offscreen)")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from autofarmstation.ui.main_window import MainWindow
        w = MainWindow()
        # 核心部件都在
        assert w._left is not None and w._right is not None
        assert w._right.hub is w._hub
        # 同步目标联动
        w._right.set_sync_targets([111, 222])
        assert w._right._sync_targets == [111, 222]
        w._right.set_sync_targets([])
        assert w._right._sync_targets == []
        # 勾选窗口解析:无勾选时退回当前目标
        w._right._target_hwnd = 555
        assert w._right._resolve_targets() == [555]
        w._right.set_sync_targets([7, 8])
        assert w._right._resolve_targets() == [7, 8]
        title = w.windowTitle()
        w._saved = True
        # 不调用 close()(避免确认弹窗),直接销毁
        w.deleteLater()
        app.processEvents()
        _ok(f"GUI 装配 ok · {title}")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("gui_smoke", e)
        return False


def t_app_help() -> bool:
    """应用帮助信息(无 GUI,不打开窗口)."""
    _div("app: __version__")
    try:
        from autofarmstation import __version__, __app_name__, __app_name_cn__
        assert __version__
        assert __app_name__
        _ok(f"{__app_name_cn__} v{__version__}")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("app", e)
        return False


# === 入口 ===
def run_all() -> int:
    t0 = time.time()
    tests = [
        t_paths, t_sanitize, t_logger, t_config, t_update_checker,
        t_input_sender, t_window_finder, t_autoclicker, t_key_macro,
        t_macro_recorder, t_process_manager, t_scheduler, t_monitor,
        t_statistics, t_presets, t_hub, t_io_smoke, t_gui_smoke, t_app_help,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            if t():
                passed += 1
            else:
                failed += 1
        except Exception as e:  # noqa: BLE001
            _fail(t.__name__, e)
            failed += 1
    dt = time.time() - t0
    print(f"\n========== 结果 ==========")
    print(f"通过 {passed} / {passed + failed},耗时 {dt:.2f}s")
    if failed:
        print(f"!!! {failed} 项失败")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_all())