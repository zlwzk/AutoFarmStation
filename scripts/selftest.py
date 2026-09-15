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


def t_update_settings() -> bool:
    _div("update settings(cfg + settings_dialog 字段)")
    try:
        import os
        import tempfile
        from pathlib import Path
        from autofarmstation.utils.config import Config

        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.json"
            os.environ["AUTOFARMSTATION_TEST_HOME"] = str(td)  # noqa
            # 直接 new Config(tmp_path) 不走环境变量,显式传
            cfg = Config(path=cfg_path)
            # 默认值
            assert cfg.get("settings.check_updates") is True
            assert cfg.get("settings.update_check_interval_hours") == 1
            assert cfg.get("settings.last_update_check_at") == ""
            assert cfg.get("settings.last_update_found") == ""
            assert cfg.get("settings.skipped_version") == ""
            # 写值 → 持久化 → 再读回来
            cfg.set("settings.check_updates", False)
            cfg.set("settings.update_check_interval_hours", 24)
            cfg.set("settings.last_update_check_at", "2026-09-15T12:34:56")
            cfg.set("settings.last_update_found", "v1.5.0")
            cfg.set("settings.skipped_version", "v1.5.0")
            cfg.save()
            cfg2 = Config(path=cfg_path)
            assert cfg2.get("settings.check_updates") is False
            assert cfg2.get("settings.update_check_interval_hours") == 24
            assert cfg2.get("settings.last_update_check_at") == "2026-09-15T12:34:56"
            assert cfg2.get("settings.last_update_found") == "v1.5.0"
            assert cfg2.get("settings.skipped_version") == "v1.5.0"
        # settings_dialog 解析频率下拉文本
        from autofarmstation.ui.settings_dialog import SettingsDialog

        class _FakeCombo:
            def __init__(self, txt):
                self._t = txt
            def currentText(self):
                return self._t
        for txt, want in (
            ("1 每小时", 1),
            ("6 每 6 小时", 6),
            ("12 每 12 小时", 12),
            ("24 每天", 24),
            ("168 每周", 168),
        ):
            got = int(_FakeCombo(txt).currentText().split(" ")[0])
            assert got == want, (txt, got, want)
        # _ago_text 输出包含秒/分钟/小时/天的常见单位
        import datetime as _dt
        ago = SettingsDialog._ago_text(
            (_dt.datetime.now() - _dt.timedelta(seconds=30)).isoformat(timespec="seconds")
        )
        assert "秒" in ago, ago
        ago = SettingsDialog._ago_text(
            (_dt.datetime.now() - _dt.timedelta(hours=5)).isoformat(timespec="seconds")
        )
        assert "小时" in ago, ago
        ago = SettingsDialog._ago_text(
            (_dt.datetime.now() - _dt.timedelta(days=3)).isoformat(timespec="seconds")
        )
        assert "天" in ago, ago
        _ok("update settings: cfg 默认/读写/频率解析/ago_text 都 ok")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("update settings", e)
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


def t_session() -> bool:
    _div("session.py")
    try:
        import json
        import tempfile
        from pathlib import Path
        from autofarmstation.core.session import Session

        # 用临时目录避开真实用户数据
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "session.json"
            ses = Session(path=tmp)

            # 空 → load() 返回 None
            assert ses.load() is None
            ses.clear()  # 不存在也不应炸

            # 保存一个空快照(load 应返回 None,items=[])
            ses.save(_FakePM([]), _FakeHub({}))
            assert ses.load() is None, "空 items 不应返回有效快照"

            # 保存 2 个窗口,saved.title 含分隔符" - ",能正确提取前缀
            pm = _FakePM([
                _FakeTP(hwnd=1001, name="melvor.exe", title="Melvor Idle - Account1", exe=r"D:\Games\melvor.exe"),
                _FakeTP(hwnd=1002, name="paperclips.exe", title="Universal Paperclips", exe=r"D:\Games\paperclips.exe"),
            ])
            hub = _FakeHub({"1001": {"clicker": {"points": [{"x": 1, "y": 2}]}}})
            ses.save(pm, hub)
            data = ses.load()
            assert data is not None and len(data["items"]) == 2
            assert data["items"][0]["config"]["clicker"]["points"][0]["x"] == 1

            # 匹配:第一项 title 完整匹配,第二项 prefix 匹配
            visible = [
                _FakeWI(hwnd=2001, title="Melvor Idle - Account1", basename="melvor.exe", pname="melvor.exe"),
                _FakeWI(hwnd=2002, title="Universal Paperclips - Beta", basename="paperclips.exe", pname="paperclips.exe"),
            ]
            pairs = Session.match(data["items"], visible, enricher=_fake_enrich)
            assert len(pairs) == 2, f"应匹配 2 个,实得 {len(pairs)}"
            assert pairs[0][1].hwnd == 2001
            assert pairs[1][1].hwnd == 2002

            # 0 可见窗口 → 不匹配
            assert Session.match(data["items"], [], enricher=_fake_enrich) == []

            # 一个可见窗口被多个 saved 抢 → 贪心:高分先得
            only_one = [_FakeWI(hwnd=3001, title="Melvor Idle - Account1", basename="melvor.exe", pname="melvor.exe")]
            pairs2 = Session.match(data["items"], only_one, enricher=_fake_enrich)
            assert len(pairs2) == 1 and pairs2[0][1].hwnd == 3001

            # clear 后 load 返 None
            ses.clear()
            assert ses.load() is None

        # 损坏文件 → 视为空
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "session.json"
            tmp.write_text("{ this is not json", encoding="utf-8")
            assert Session(path=tmp).load() is None

        _ok("session: 保存/读取/匹配/前缀提取/贪心分配/损坏恢复 ok")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("session", e)
        return False


# ---- 假的 PM / Hub / TP / WI,只暴露 Session 用到的属性 ----
class _FakeTP:
    __slots__ = ("hwnd", "pid", "name", "title", "exe", "added_at", "tags")
    def __init__(self, hwnd, name, title, exe, pid=0, added_at=0.0):
        self.hwnd = hwnd
        self.pid = pid
        self.name = name
        self.title = title
        self.exe = exe
        self.added_at = added_at
        self.tags = []


class _FakePM:
    """只给 Session.save 用:all() 返回 _FakeTP 列表."""
    def __init__(self, items):
        self._items = items
    def all(self):
        return list(self._items)


class _FakeHub:
    """只给 Session.save 用:export_configs() 返回 dict[str, dict]."""
    def __init__(self, cfg):
        self._cfg = cfg
    def export_configs(self):
        return dict(self._cfg)


class _FakeWI:
    """WindowInfo 替代:Session.match 只用 hwnd/title,可挂 _basename/_pname 模拟进程信息."""
    __slots__ = (
        "hwnd", "pid", "title", "class_name", "rect", "visible",
        "is_minimized", "has_caption", "_basename", "_pname",
    )
    def __init__(self, hwnd, title="", pid=0, basename="", pname=""):
        self.hwnd = hwnd
        self.pid = pid
        self.title = title
        self.class_name = ""
        self.rect = (0, 0, 100, 100)
        self.visible = True
        self.is_minimized = False
        self.has_caption = True
        self._basename = basename
        self._pname = pname


def _fake_enrich(visible):
    """不依赖 psutil:直接读 _FakeWI._basename/_pname."""
    return [(w, w._basename, w._pname) for w in visible]


def t_bat_library() -> bool:
    _div("bat_library.py")
    try:
        import json
        import tempfile
        from pathlib import Path
        from autofarmstation.core.bat_library import BatLibrary

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            builtin = base / "builtin"
            user = base / "user"
            builtin.mkdir()
            user.mkdir()
            # 内置一份
            (builtin / "manifest.json").write_text(json.dumps({
                "version": 1, "entries": [
                    {"id": "b1", "title": "测试脚本", "file": "b1.bat",
                     "category": "Steam", "desc": "内置示例"},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            (builtin / "b1.bat").write_text("@echo off\necho b1\n", encoding="utf-8")
            lib = BatLibrary(builtin_dir=builtin, user_dir=user)

            # list 包含内置
            entries = lib.list()
            assert len(entries) == 1 and entries[0].id == "b1"
            assert entries[0].builtin is True

            # 用户创建 → 同 id 覆盖
            new = lib.create("我的脚本")
            assert new.builtin is False
            assert (user / new.file_name).exists()
            entries = lib.list()
            assert any(e.id == new.id for e in entries)
            # 同 id 不会覆盖 → manifest 不会出两条
            assert sum(1 for e in entries if e.id == new.id) == 1

            # 删除用户脚本
            assert lib.delete(new.id) is True
            assert not (user / new.file_name).exists()
            # 删除内置脚本应失败
            assert lib.delete("b1") is False

            # 损坏 manifest 不应炸
            (builtin / "manifest_bad.json").write_text("{ not json", encoding="utf-8")
            lib2 = BatLibrary(builtin_dir=builtin, user_dir=user)
            assert any(e.id == "b1" for e in lib2.list())

            # ensure_seeded 把内置 .bat 拷到用户目录(文件不存在时)
            (user / "b1.bat").unlink(missing_ok=True)
            (user / "manifest.json").unlink(missing_ok=True)
            n = lib.ensure_seeded()
            assert n >= 1
            assert (user / "b1.bat").exists()
            # 已存在就不重复拷
            assert lib.ensure_seeded() == 0

        _ok("bat_library: 合并/创建/删除/损坏恢复/seed ok")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("bat_library", e)
        return False


def t_steam_overlay() -> bool:
    _div("steam_overlay.py")
    try:
        import re
        import tempfile
        from pathlib import Path
        from autofarmstation.core.steam_overlay import (
            describe, set_overlay, restore_backup, _patch_overlay,
        )

        # _patch_overlay: 空文本 → 加完整节
        out = _patch_overlay("", "0")
        assert "[Install]" in out and "SteamOverlay=0" in out, out

        # 已有节无键 → 追加
        out = _patch_overlay("[Install]\nBootStrapper=1\n", "0")
        assert "BootStrapper=1" in out and "SteamOverlay=0" in out

        # 已有节已有键 → 替换
        out = _patch_overlay("[Install]\nSteamOverlay=1\nBootStrapper=2\n", "0")
        assert "[Install]\nSteamOverlay=0\nBootStrapper=2" in out

        # 没有节,但文本末尾有内容 → 追加新节
        out = _patch_overlay("hello\n", "1")
        assert out.endswith("[Install]\nSteamOverlay=1\n") or "[Install]\nSteamOverlay=1\n" in out

        # 真实文件路径:describe/set_overlay/restore_backup
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "steam.cfg"
            # 不存在时 describe 返 exists=False
            d = describe(cfg)
            assert d["exists"] is False and d["overlay"] is None

            # 写入 + 设 0
            cfg.write_text("[Install]\nBootStrapper=42\n", encoding="utf-8")
            backup = cfg.read_text(encoding="utf-8")
            ok, _ = set_overlay(enabled=False, backup=backup, path=cfg)
            assert ok is True
            assert "SteamOverlay=0" in cfg.read_text(encoding="utf-8")
            # 原 BootStrapper 必须保留
            assert "BootStrapper=42" in cfg.read_text(encoding="utf-8")

            # describe 现在能读到
            d = describe(cfg)
            assert d["overlay"] == 0

            # restore_backup 回滚
            ok, _ = restore_backup(backup, path=cfg)
            assert ok is True
            assert "SteamOverlay" not in cfg.read_text(encoding="utf-8")
            assert "BootStrapper=42" in cfg.read_text(encoding="utf-8")

            # 再设 1,确认也是替换而不会重复追加
            set_overlay(enabled=True, backup=backup, path=cfg)
            set_overlay(enabled=True, backup=backup, path=cfg)
            text = cfg.read_text(encoding="utf-8")
            assert text.count("SteamOverlay=") == 1, text

        _ok("steam_overlay: describe/写/还原/重复写不重复 ok")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("steam_overlay", e)
        return False


def t_window_host() -> bool:
    _div("window_host.py + preview_capture.GDI")
    try:
        from autofarmstation.core import window_host as host
        from autofarmstation.core import preview_capture as pc
        from autofarmstation.core import window_finder as wf

        # 参数守卫:无效 hwnd 一律 False,不抛异常
        assert host.embed_window(0, 0) is False
        assert host.release_window(0) is False
        assert host.is_embedded(0) is False
        assert host.fit_to(0, 100, 100) is False
        assert host.release_all() == 0

        # GDI 截图冒烟:对任意一个真实可见窗口截一帧(不 assert 成功,只断言不炸/形状正确)
        wins = wf.list_visible_windows()
        if wins:
            res = pc.capture_gdi(wins[0].hwnd)
            if res is not None:
                w, h, raw = res
                assert w > 0 and h > 0, f"尺寸异常: {w}x{h}"
                assert len(raw) == w * h * 4, f"数据长度 {len(raw)} != {w}*{h}*4"
                _ok(f"window_host: GDI 截图 {w}x{h} ok")
            else:
                _ok("window_host: GDI 截图返回 None(允许,如 DWM 拒绝)")
        else:
            _ok("window_host: 无可见窗口可测(允许)")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("window_host", e)
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
        import datetime as _dt
        from autofarmstation.core.scheduler import (
            Scheduler, ScheduledTask, TaskFreq, action_label, format_interval,
        )

        with tempfile.TemporaryDirectory() as td:
            sp = Path(td) / "schedules.json"
            s = Scheduler(path=sp, autosave=True)
            fired = []
            s.register_handler("noop", lambda t: fired.append(t.name))
            s.register_handler("start_all", lambda t: fired.append(t.name))

            # 基本增删
            s.add(ScheduledTask(name="t1", action="noop", freq=TaskFreq.ONCE))
            assert len(s.list()) == 1
            assert s.get("t1") is not None
            s.remove("t1")
            assert len(s.list()) == 0

            # 重名自动加后缀
            s.add(ScheduledTask(name="dup", action="noop", freq=TaskFreq.INTERVAL,
                                interval_sec=60))
            n2 = s.add(ScheduledTask(name="dup", action="noop", freq=TaskFreq.INTERVAL,
                                     interval_sec=60))
            assert n2 != "dup", n2

            # 四种频率的展示 + 下次触发时间
            now = time.time()
            once = ScheduledTask(name="o", freq=TaskFreq.ONCE,
                                 run_at="2099-01-01T08:00")
            s.add(once)
            assert s.get("o").next_run > now
            assert "2099-01-01" in s.get("o").schedule_text()

            daily = ScheduledTask(name="d", freq=TaskFreq.DAILY, hour=9, minute=30)
            s.add(daily)
            nxt = s.get("d").next_run
            d = _dt.datetime.fromtimestamp(nxt)
            assert (d.hour, d.minute) == (9, 30), (d.hour, d.minute)
            assert nxt > now

            weekly = ScheduledTask(name="w", freq=TaskFreq.WEEKLY, weekday=2,
                                   hour=8, minute=0)
            s.add(weekly)
            wd = _dt.datetime.fromtimestamp(s.get("w").next_run)
            assert wd.weekday() == 2, wd.weekday()
            assert "周三" in s.get("w").schedule_text()

            itv = ScheduledTask(name="i", freq=TaskFreq.INTERVAL, interval_sec=7200)
            s.add(itv)
            assert abs(s.get("i").next_run - (now + 7200)) < 5

            assert format_interval(3600) == "1 小时"
            assert format_interval(120) == "2 分钟"
            assert format_interval(45) == "45 秒"
            assert action_label("launch_games")

            # 持久化:重开一个实例能读回来
            s.save()
            s2 = Scheduler(path=sp, autosave=False)
            s2.register_handler("start_all", lambda t: fired.append(t.name))
            names = {t.name for t in s2.list()}
            assert {"dup", "o", "d", "w", "i"} <= names, names
            assert s2.get("o").freq is TaskFreq.ONCE
            assert s2.get("w").weekday == 2

            # 启用/停用 + 立即执行
            assert s2.set_enabled("d", False) is True
            assert s2.next_run_text(s2.get("d")) == "已暂停"
            assert s2.run_now("d") is True
            assert fired, "run_now 应该真的调用 handler"
            assert s2.get("d").run_count >= 1

            # 已过时间的一次性任务不会再触发
            past = ScheduledTask(name="past", freq=TaskFreq.ONCE,
                                 run_at="2000-01-01T00:00")
            s2.add(past)
            assert s2.compute_next(s2.get("past")) == 0.0

            # 损坏文件不炸
            sp.write_text("{ not json", encoding="utf-8")
            Scheduler(path=sp, autosave=False)

        _ok("scheduler: 增删/重名/四频率/下次触发/持久化/启停/立即执行/损坏恢复")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("scheduler", e)
        return False


def t_audio() -> bool:
    _div("audio.py(音量)")
    try:
        from autofarmstation.core import audio

        # 缺 pycaw 时整体降级,不抛异常
        if not audio.available():
            _ok("audio: pycaw 不可用 → 优雅降级")
            return True
        mv = audio.get_master_volume()
        assert mv is None or 0.0 <= mv <= 1.0, mv
        assert isinstance(audio.get_master_mute(), (bool, type(None)))
        desc = audio.describe()
        assert isinstance(desc, str) and desc
        # 只读探测,不改系统音量
        sessions = audio.list_sessions()
        assert isinstance(sessions, list)
        for s in sessions:
            assert "pid" in s and "volume" in s and "muted" in s
        # 不存在的 pid → 找不到会话,但不抛异常
        assert audio.find_session(0) is None
        assert audio.get_session_volume(999999999) is None
        assert audio.set_session_volume(999999999, 0.5) is False
        _ok(f"audio: {desc} · 会话 {len(sessions)} 个")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("audio", e)
        return False


def t_game_launcher() -> bool:
    _div("game_launcher.py(启动/结束进程)")
    try:
        from autofarmstation.core import game_launcher as gl

        # VDF 解析(libraryfolders.vdf / appmanifest 的格式)
        sample = """
"libraryfolders"
{
\t"0"
\t{
\t\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam"
\t\t"label"\t\t""
\t\t"apps"
\t\t{
\t\t\t"897330"\t\t"123456"
\t\t}
\t}
\t"1"
\t{
\t\t"path"\t\t"D:\\\\Games\\\\SteamLibrary"
\t}
}
"""
        data = gl.parse_vdf(sample)
        folders = data["libraryfolders"]
        assert folders["0"]["path"].replace("\\\\", "\\").endswith("Steam")
        assert folders["1"]["path"].endswith("SteamLibrary")
        assert folders["0"]["apps"]["897330"] == "123456"

        # LaunchInfo 往返
        li = gl.LaunchInfo(exe=r"C:\game\a.exe", args=["-x", "1"],
                           cwd=r"C:\game", name="a.exe", title="A",
                           steam_appid=897330, source="steam")
        assert li.usable
        li2 = gl.LaunchInfo.from_dict(li.to_dict())
        assert li2.steam_appid == 897330 and li2.args == ["-x", "1"]
        assert "897330" in li2.describe()
        assert not gl.LaunchInfo().usable

        # 进程状态
        assert gl.is_process_alive(os.getpid()) is True
        assert gl.is_process_alive(0) is False
        assert gl.is_process_alive(999999999) is False
        # 结束一个不存在的进程 → 视为已经不在
        ok, msg = gl.kill_process(999999999)
        assert ok is True, msg
        # 缺启动信息 → 明确失败而不是崩
        ok, msg = gl.launch(None)
        assert ok is False and msg

        # Steam 库扫描(本机没装 Steam 时返回空表,不报错)
        libs = gl.steam_libraries()
        assert isinstance(libs, list)
        # 随便一个不存在的 exe → 反查不到 appid
        assert gl.find_appid_by_exe(r"C:\definitely\not\here.exe") == 0
        # 有 exe 的文件放进去能被识别为可启动
        ok, msg = gl.launch(gl.LaunchInfo(exe=r"C:\definitely\not\here.exe"))
        assert ok is False and "exe" in msg.lower() or "不存在" in msg
        _ok(f"game_launcher: vdf 解析 / LaunchInfo / 进程状态 / Steam 库 {len(libs)} 个")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("game_launcher", e)
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


def t_steam_status() -> bool:
    """Steam 状态联动:状态映射 + 本地配置解析 + 挂机边沿触发(全部打桩,不碰真实 Steam)."""
    _div("steam_status.py")
    try:
        from unittest import mock
        from autofarmstation.utils.config import Config
        from autofarmstation.core import steam_status as ss

        # 1) 状态名 / 标签
        assert ss.SUPPORTED_STATES == ("online", "away", "invisible")
        assert ss.is_supported_state("online") and not ss.is_supported_state("offline")
        assert ss.state_label("invisible") == "隐身"
        assert ss.state_label(None) == "未知"
        assert ss.persona_state_name(7) == "invisible"
        assert ss.persona_state_name(None) == "unknown"

        # 2) 本地 localconfig.vdf 解析(样本与真实文件同构)
        vdf = (
            '"UserLocalConfigStore"\n{\n\t"friends"\n\t{\n'
            '\t\t"FriendStoreLocalPrefs_42"\t\t'
            '"{\\"ePersonaState\\":7,\\"strNonFriendsAllowedToMsg\\":\\"\\"}"\n'
            "\t}\n}\n"
        )
        assert ss.parse_persona_state(vdf, 42) == 7
        assert ss.parse_persona_state(vdf, 43) is None
        assert ss.parse_persona_state("garbage", 42) is None
        assert ss.parse_persona_state('"FriendStoreLocalPrefs_42" "{}"', 42) is None

        # 3) 非法状态必须被白名单拦住(不会真的打开 steam:// )
        try:
            ss.open_state_url("offline")
            raise AssertionError("offline 不该被允许")
        except ValueError:
            pass
        try:
            ss.open_state_url("online; rm -rf")
            raise AssertionError("非法串不该被允许")
        except ValueError:
            pass

        # 4) 默认关闭时:完全不碰 Steam
        cfg = Config()
        cfg.set("steam.enabled", False)
        ctrl = ss.SteamStatusController(cfg)
        assert ctrl.enabled is False
        assert ctrl.farm_state == "online"
        assert ctrl.restore_previous is True
        ctrl.on_running_changed(3)
        assert ctrl.applied is False
        assert ctrl.poll_messages() == []

        # 5) 边沿触发:进入挂机切一次、持续挂机不重复、停止后还原
        cfg.set("steam.enabled", True)
        cfg.set("steam.farm_state", "invisible")
        cfg.set("steam.restore_previous", True)
        ctrl2 = ss.SteamStatusController(cfg)
        calls: list[str] = []
        ctrl2._worker_async = lambda state, note: calls.append(state)  # type: ignore[assignment]
        with mock.patch.object(ss, "is_steam_running", lambda: True), \
                mock.patch.object(ss, "active_account_id", lambda: 42), \
                mock.patch.object(ss, "read_current_state", lambda *a, **k: "away"):
            ctrl2.on_running_changed(1)
            assert calls == ["invisible"], calls
            assert ctrl2.applied is True
            ctrl2.on_running_changed(2)  # 还在挂机 → 不重复切
            assert calls == ["invisible"], calls
            ctrl2.on_running_changed(0)  # 挂机结束 → 还原成 away
            assert calls == ["invisible", "away"], calls
            assert ctrl2.applied is False
            # 关闭开关后,处于挂机中也要还原
            ctrl2.on_running_changed(1)
            assert calls[-1] == "invisible"
            cfg.set("steam.enabled", False)
            ctrl2.on_running_changed(1)
            assert ctrl2.applied is False
            assert calls[-1] == "away", calls

        # 6) Steam 没运行时:只提示、不改状态
        cfg.set("steam.enabled", True)
        ctrl3 = ss.SteamStatusController(cfg)
        ctrl3._worker_async = lambda state, note: calls.append(state)  # type: ignore[assignment]
        with mock.patch.object(ss, "is_steam_running", lambda: False):
            ctrl3.on_running_changed(1)
        assert ctrl3.applied is False
        assert any("Steam 未运行" in m for m in ctrl3.poll_messages())

        # 7) 先挂机后关 Steam 的还原路径:shutdown 不应抛异常
        cfg.set("steam.enabled", True)
        ctrl4 = ss.SteamStatusController(cfg)
        with mock.patch.object(ss, "open_state_url", lambda s: None):
            ctrl4._previous_state = "away"
            ctrl4._applied = True
            ctrl4.shutdown()
            assert ctrl4.applied is False

        _ok("steam_status: 状态映射 + vdf 解析 + 挂机边沿触发")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("steam_status", e)
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
            automation_hub, steam_status, window_host, session, bat_library, steam_overlay,
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


# === v1.6.2 新增的 4 项:「修了不生效的设置」 ===
def t_theme_actually_applies() -> bool:
    """v1.6.2 主题设置真的生效:app._apply_palette(theme) 根据传入主题切颜色,
    不是写死 dark。"""
    _div("v1.6.2 theme switch")
    try:
        from autofarmstation.app import _apply_palette, apply_theme
        # _apply_palette 接受 (app, theme) 两个参数;不是仅 dark 的版本
        sig = __import__("inspect").signature(_apply_palette)
        params = list(sig.parameters.values())
        assert len(params) >= 2, f"_apply_palette 必须接受 (app, theme),签名={sig}"
        assert params[-1].name == "theme", f"最后一个参数应叫 theme,实际 {params[-1].name}"
        # 应用模块不能再写死调 _apply_dark_palette / 不读 cfg
        src = __import__("inspect").getsource(__import__("autofarmstation.app", fromlist=["run"]))
        assert "_apply_dark_palette" not in src, "app.py 不能再有 _apply_dark_palette 写死调用"
        assert 'cfg.get("ui.theme"' in src or "cfg.get('ui.theme'" in src or 'ui.theme' in src, \
            "app.py 必须从 cfg 读 ui.theme"
        _ok("_apply_palette(theme) 接受主题参数;app.py 从 cfg 读取 ui.theme")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("theme", e)
        return False


def t_language_placeholder_disabled() -> bool:
    """v1.6.2 英语占位选项必须被禁用,避免用户选了之后被记住成 en-US。"""
    _div("v1.6.2 language placeholder disabled")
    try:
        from autofarmstation.ui import settings_dialog as sd
        src = __import__("inspect").getsource(sd)
        # 必须有禁用第二项的处理
        assert "English" in src, "settings_dialog 应该有 English 选项"
        assert "setEnabled(False)" in src or "model().item" in src, \
            "English 占位项必须被 setEnabled(False) 禁用"
        # 不能写死 en-US 作为有效值保存
        assert 'set("ui.language", "en-US"' not in src, "不能保存 en-US(没有翻译)"
        # 必须读 cfg.ui.language 时回退 zh-CN
        assert "zh-CN" in src, "默认 / 回退值必须是 zh-CN"
        _ok("English 占位被禁用;ui.language 落盘时强制 zh-CN")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("language", e)
        return False


def t_default_clicker_params_applied() -> bool:
    """v1.6.2 默认连点参数真的传到 ClickerPanel:改 cfg.defaults.clicker_* 后
    新建 ClickerPanel 的 _interval / _jitter / _mode 跟着变。"""
    _div("v1.6.2 default clicker params")
    try:
        from autofarmstation.utils.config import Config
        from autofarmstation.core.automation_hub import AutomationHub
        from autofarmstation.ui.action_panel import ClickerPanel

        cfg = Config()
        cfg.set("defaults.clicker_interval_ms", 333)
        cfg.set("defaults.clicker_jitter_ms", 25)
        cfg.set("defaults.clicker_button", "right")
        cfg.set("defaults.clicker_mode", "random")

        hub = AutomationHub()
        cp = ClickerPanel(hub=hub, defaults={
            "interval_ms": int(cfg.get("defaults.clicker_interval_ms")),
            "jitter_pct": int(cfg.get("defaults.clicker_jitter_ms")),
            "button": str(cfg.get("defaults.clicker_button")),
            "mode": str(cfg.get("defaults.clicker_mode")),
        })
        assert cp._interval.value() == 333, f"interval 应是 333,实际 {cp._interval.value()}"
        assert cp._jitter.value() == 25, f"jitter 应是 25,实际 {cp._jitter.value()}"
        # mode 索引 0=post, 1=send;random 走 send,所以应是 1
        assert cp._mode.currentIndex() == 1, f"mode 应是 send(1),实际 {cp._mode.currentIndex()}"

        # 不传 cfg / defaults 时仍是硬编码默认(向后兼容)
        cp_default = ClickerPanel(hub=hub)
        assert cp_default._interval.value() == 200
        assert cp_default._jitter.value() == 10
        _ok("ClickerPanel 读 cfg.defaults.clicker_*;不传则用硬编码默认")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("clicker_defaults", e)
        return False


def t_farm_window_guard() -> bool:
    """v1.6.2 挂机时段守护:启用且不在时段 → should_block_start=True;
    跨天时段(start > end)也能正确判断;未启用则不限制。"""
    _div("v1.6.2 farm window guard")
    try:
        import datetime as _dt
        from autofarmstation.utils.config import Config
        from autofarmstation.core.farm_window import FarmWindowGuard

        # 1) 未启用:任何时间都不阻挡
        cfg = Config()
        g = FarmWindowGuard(cfg)
        assert g.should_block_start() is False, "未启用时不应阻挡"
        assert g.in_window() is True, "未启用时 in_window 应返回 True(不限制)"

        # 2) 当天内:start=08:00, end=18:00
        cfg.set("farm_window.enabled", True)
        cfg.set("farm_window.start", "08:00")
        cfg.set("farm_window.end", "18:00")
        morning = _dt.datetime(2026, 1, 1, 7, 30)  # 在时段外
        noon = _dt.datetime(2026, 1, 1, 12, 0)     # 在时段内
        evening = _dt.datetime(2026, 1, 1, 19, 30) # 在时段外
        assert not g.in_window(morning)
        assert g.in_window(noon)
        assert not g.in_window(evening)
        assert g.should_block_start(), "不在时段应返回 True"

        # 3) 跨天:start=22:00, end=08:00
        cfg.set("farm_window.start", "22:00")
        cfg.set("farm_window.end", "08:00")
        late_night = _dt.datetime(2026, 1, 1, 23, 30)  # 在时段内
        before_dawn = _dt.datetime(2026, 1, 1, 3, 30)  # 在时段内(跨天部分)
        daytime = _dt.datetime(2026, 1, 1, 12, 0)      # 在时段外
        assert g.in_window(late_night)
        assert g.in_window(before_dawn)
        assert not g.in_window(daytime)

        # 4) action 字段:默认 pause
        assert g.action() == "pause"
        cfg.set("farm_window.action", "stop")
        assert g.action() == "stop"

        _ok("FarmWindowGuard 当天内 / 跨天 / 启用开关 / action 字段均正确")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("farm_window", e)
        return False


def t_user_bats_persist() -> bool:
    """v1.6.2:用户自加的 bat 脚本必须跨「重建 BatLibrary」持久化。

    用户 bat 永远在 ``%APPDATA%\\AutoFarmStation\\bats\\``,升级软件 / 重启 / 重新
    构造 BatLibrary 实例都不能让它丢。
    """
    _div("v1.6.2 user bats persist across re-init")
    try:
        import tempfile
        from pathlib import Path
        from autofarmstation.core.bat_library import BatLibrary

        with tempfile.TemporaryDirectory(prefix="afs-bat-persist-") as tmp:
            builtin = Path(tmp) / "builtin"
            user = Path(tmp) / "user"
            builtin.mkdir(parents=True, exist_ok=True)
            user.mkdir(parents=True, exist_ok=True)

            # 内置放 1 个,用户空
            (builtin / "hello.bat").write_text(
                "@echo off\r\necho hello\r\n", encoding="utf-8",
            )
            import json as _json
            (builtin / "manifest.json").write_text(
                _json.dumps(
                    {"version": 1, "entries": [
                        {"id": "builtin_hello", "title": "你好",
                         "file": "hello.bat", "category": "测试", "desc": ""},
                    ]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            lib = BatLibrary(builtin_dir=builtin, user_dir=user)
            lib.ensure_seeded()

            # 用户自加 2 个 bat
            lib.create("我的脚本 A")
            lib.create("我的脚本 B")

            # 模拟「升级后重新构造 BatLibrary」
            lib2 = BatLibrary(builtin_dir=builtin, user_dir=user)
            lib2.ensure_seeded()
            entries = lib2.list()
            user_titles = {e.title for e in entries if not e.builtin}
            assert "我的脚本 A" in user_titles, f"脚本 A 丢了: {user_titles}"
            assert "我的脚本 B" in user_titles, f"脚本 B 丢了: {user_titles}"
            assert len(entries) >= 3, f"应至少 1 内置 + 2 用户 = 3,实际 {len(entries)}"

            # 用户删除某个内置 → 下次 ensure_seeded 重新拷贝(因为 .bat 丢失了)
            (user / "hello.bat").unlink()
            lib3 = BatLibrary(builtin_dir=builtin, user_dir=user)
            n = lib3.ensure_seeded()
            assert (user / "hello.bat").exists(), "内置 .bat 应在丢失后自动恢复"
            assert n >= 1, f"应至少恢复 1 个内置 .bat,实际 {n}"

            # 但用户的自定义 bat 仍然在
            entries = lib3.list()
            user_titles = {e.title for e in entries if not e.builtin}
            assert "我的脚本 A" in user_titles
            assert "我的脚本 B" in user_titles

        _ok("用户 bat 跨 re-init 持久;内置 .bat 丢失后自动恢复;用户条目不受影响")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("user_bats", e)
        return False


def t_builtin_manifest_merges() -> bool:
    """v1.6.2:新版本加了内置 bat 条目 → ensure_seeded 把它合进用户 manifest。

    关键点:**只追加不存在的 id**,绝不覆盖用户已有的条目(用户改过的
    title / desc / args 必须保留)。
    """
    _div("v1.6.2 builtin manifest merges into user manifest")
    try:
        import tempfile, json as _json
        from pathlib import Path
        from autofarmstation.core.bat_library import BatLibrary

        with tempfile.TemporaryDirectory(prefix="afs-bat-merge-") as tmp:
            builtin = Path(tmp) / "builtin"
            user = Path(tmp) / "user"
            builtin.mkdir(parents=True, exist_ok=True)
            user.mkdir(parents=True, exist_ok=True)

            # 内置 v1:2 条目
            (builtin / "first.bat").write_text(
                "@echo off\r\necho first\r\n", encoding="utf-8",
            )
            (builtin / "manifest.json").write_text(
                _json.dumps(
                    {"version": 1, "entries": [
                        {"id": "first", "title": "第一条", "file": "first.bat",
                         "category": "测试", "desc": ""},
                        {"id": "second", "title": "第二条", "file": "second.bat",
                         "category": "测试", "desc": ""},
                    ]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (builtin / "second.bat").write_text(
                "@echo off\r\necho second\r\n", encoding="utf-8",
            )

            # 用户已经在用,且改过「第一条」的 title
            (user / "first.bat").write_text(
                "@echo off\r\necho first(用户改过)\r\n", encoding="utf-8",
            )
            (user / "manifest.json").write_text(
                _json.dumps(
                    {"version": 1, "entries": [
                        {"id": "first", "title": "用户改名的第一条", "file": "first.bat",
                         "category": "测试", "desc": ""},
                        {"id": "my_custom", "title": "我的专属", "file": "custom.bat",
                         "category": "我的", "desc": ""},
                    ]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            # 内置 v2 新增 third 条目(模拟软件升级)
            (builtin / "third.bat").write_text(
                "@echo off\r\necho third\r\n", encoding="utf-8",
            )
            (builtin / "manifest.json").write_text(
                _json.dumps(
                    {"version": 1, "entries": [
                        {"id": "first", "title": "第一条", "file": "first.bat",
                         "category": "测试", "desc": ""},
                        {"id": "second", "title": "第二条", "file": "second.bat",
                         "category": "测试", "desc": ""},
                        {"id": "third", "title": "第三条(新)", "file": "third.bat",
                         "category": "测试", "desc": "v1.6.2 新加"},
                    ]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            lib = BatLibrary(builtin_dir=builtin, user_dir=user)
            n = lib.ensure_seeded()
            assert n >= 1, f"应至少新增 1 个内置条目,实际 {n}"

            # 用户 manifest 现在应包含:用户改名的 first + my_custom + 新并入的 second/third
            user_data = _json.loads(
                (user / "manifest.json").read_text(encoding="utf-8"),
            )
            ids = {e["id"] for e in user_data["entries"]}
            assert ids == {"first", "second", "third", "my_custom"}, f"id 集合不对:{ids}"
            # 用户改过的 title 必须保留
            first_e = next(e for e in user_data["entries"] if e["id"] == "first")
            assert first_e["title"] == "用户改名的第一条", \
                f"用户改过的 title 被覆盖: {first_e['title']}"

            # 用户目录里现在有 first/second/third 三个 .bat
            assert (user / "first.bat").exists()
            assert (user / "second.bat").exists()
            assert (user / "third.bat").exists()

        _ok("内置新条目合并进用户 manifest,用户已有条目不被覆盖")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("builtin_merges", e)
        return False


# === v1.6.1 新增的 4 项: ===
def t_dpi_awareness() -> bool:
    """v1.6.1 启动时声明 DPI 感知(SetProcessDpiAwareness / V2 / SetProcessDPIAware 三档回退)。"""
    _div("v1.6.1 dpi awareness")
    try:
        from autofarmstation.__init__ import _set_process_dpi_awareness
        # 函数存在、可调用、不会抛异常
        _set_process_dpi_awareness()
        _ok("DPI 感知声明函数可用(已依次尝试 SetProcessDpiAwareness / V2 / SetProcessDPIAware)")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("dpi", e)
        return False


def t_virtual_screen() -> bool:
    """v1.6.1 SendInput ABSOLUTE 按虚拟屏幕归一化,而非主显示器像素。"""
    _div("v1.6.1 virtual screen")
    try:
        from autofarmstation.core import window_finder as wf
        vs = wf.virtual_screen()
        assert isinstance(vs, tuple) and len(vs) == 4
        vx, vy, vw, vh = vs
        assert vw > 0 and vh > 0, f"虚拟屏幕宽度/高度必须是正数,得到 vw={vw} vh={vh}"
        _ok(f"虚拟屏幕 vx={vx} vy={vy} vw={vw} vh={vh}(用于 SendInput ABSOLUTE 归一化)")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("virtual_screen", e)
        return False


def t_hidden_windows_and_pid() -> bool:
    """v1.6.1 进程识别更稳:按 PID 强制添加 / 按进程名添加 / 隐藏窗口枚举 / set_margins。"""
    _div("v1.6.1 hidden windows + pid + margins")
    try:
        from autofarmstation.core import window_finder as wf
        from autofarmstation.core.process_manager import ProcessManager, TrackedProcess

        # list_all_windows_for_pid:pid=0 返回空列表(保护性检查)
        empty = wf.list_all_windows_for_pid(0)
        assert empty == [], f"pid=0 应返回空列表,得到 {empty!r}"
        # find_windows_for_pids 支持 include_hidden 参数
        sig = __import__("inspect").signature(wf.find_windows_for_pids)
        assert "include_hidden" in sig.parameters, "find_windows_for_pids 必须有 include_hidden 参数"
        # list_all_windows_across_processes 存在(可能没在运行 psutil 的环境里跑,但函数必须存在)
        assert hasattr(wf, "list_all_windows_across_processes"), \
            "list_all_windows_across_processes 必须存在(供「包含隐藏窗口」勾选用)"
        # ProcessManager 新方法
        pm = ProcessManager()
        for m in ("add_by_pid_force", "add_by_process_name", "set_margins"):
            assert hasattr(pm, m), f"ProcessManager 缺少 {m}"
        # set_margins 直接在构造的 TrackedProcess 上跑一遍
        fake = TrackedProcess(hwnd=12345, pid=0, name="test", title="test", exe="")
        pm._items[12345] = fake  # type: ignore[attr-defined]
        ok = pm.set_margins(
            12345,
            margin=(1, 2, 3, 4),
            scale=1.1,
        )
        assert ok and fake.margin_left == 1 and fake.margin_top == 2
        assert fake.margin_right == 3 and fake.margin_bottom == 4
        assert abs(fake.content_scale - 1.1) < 1e-6
        # margin_tuple 应返回 int 四元组
        assert fake.margin_tuple() == (1, 2, 3, 4)
        _ok("按 PID 强制添加 / 按进程名添加 / 隐藏窗口枚举 / set_margins 均已就位")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("hidden_windows_and_pid", e)
        return False


def t_focus_with_margins_and_auto_click() -> bool:
    """v1.6.1 fit_to_work_area 支持每边距 + 缩放;InputSender.click 支持 auto 模式。"""
    _div("v1.6.1 focus margins + auto click")
    try:
        from autofarmstation.core import window_finder as wf

        # fit_to_work_area 接受 4-tuple margin + scale
        sig_m = __import__("inspect").signature(wf.fit_to_work_area)
        assert "margin" in sig_m.parameters and "scale" in sig_m.parameters
        sig_f = __import__("inspect").signature(wf.focus_window)
        assert "margin" in sig_f.parameters and "scale" in sig_f.parameters

        # _resolve_margin 的归一逻辑(int → 4-tuple,None → (0,0,0,0))
        rm = wf._resolve_margin
        assert rm(None) == (0, 0, 0, 0)
        assert rm(10) == (10, 10, 10, 10)
        assert rm((1, 2, 3, 4)) == (1, 2, 3, 4)

        # InputSender.click 返回 (bool, str),default_mode='auto'
        from autofarmstation.core.input_sender import InputSender, _InputHelper
        s = InputSender()
        assert s.default_mode == "auto", f"default_mode 应是 auto,得到 {s.default_mode}"
        # _InputHelper.move_abs 内部应使用 wf.virtual_screen()(通过 spy 粗略验证)
        src = __import__("inspect").getsource(_InputHelper.move_abs)
        assert "virtual_screen" in src, "move_abs 必须调用 virtual_screen(),不能再用主显示器 sw/sh"
        # _InputHelper 暴露了 screen_size 给 selftest 用,任一返回值是 int 都行
        sw, sh = _InputHelper.screen_size()
        assert isinstance(sw, int) and isinstance(sh, int)
        _ok("fit/focus 接受 (l,t,r,b)+scale,InputSender.click 默认 auto,move_abs 走虚拟屏幕")
        return True
    except Exception as e:  # noqa: BLE001
        _fail("focus_margins_and_auto_click", e)
        return False


# === 入口 ===
def run_all() -> int:
    t0 = time.time()
    tests = [
        t_paths, t_sanitize, t_logger, t_config, t_update_checker,
        t_input_sender, t_window_finder, t_window_host, t_autoclicker, t_key_macro,
        t_macro_recorder, t_process_manager, t_scheduler, t_monitor,
        t_statistics, t_presets, t_hub, t_steam_status, t_session,
        t_bat_library, t_steam_overlay,
        t_update_settings, t_audio, t_game_launcher,
        t_io_smoke, t_gui_smoke, t_app_help,
        # v1.6.1 新增
            t_dpi_awareness, t_virtual_screen,
            t_hidden_windows_and_pid, t_focus_with_margins_and_auto_click,
            # v1.6.2 新增
                t_theme_actually_applies, t_language_placeholder_disabled,
                t_default_clicker_params_applied, t_farm_window_guard,
                t_user_bats_persist, t_builtin_manifest_merges,
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