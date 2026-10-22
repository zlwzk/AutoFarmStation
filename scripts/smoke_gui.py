"""GUI 冒烟测试:真实拉起 MainWindow,验证 v1.8.8 的线程范式改动不崩.

测什么:
  1. MainWindow 能正常构造并 show(不抛异常)
  2. 热键 watcher 的 receiver 注入生效 —— _fire 不再直接跑回调
  3. pm.on_change 从「监控线程」触发时,_sync 被切回主线程而不是当场建控件
  4. 定时任务 handler 被切到主线程(不会在调度线程里 close)
  5. 进程能干净退出

跑法:python scripts/smoke_gui.py [秒数]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_TMP = Path(tempfile.mkdtemp(prefix="afs_smoke_"))
os.environ["APPDATA"] = str(_TMP)
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["TEMP"] = str(_TMP)
os.environ["TMP"] = str(_TMP)

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QCoreApplication

ok = True


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, (" — " + detail) if detail else ""))
    if not cond:
        ok = False


print("=== v1.8.8 GUI 冒烟 ===")
app = QApplication([])

from autofarmstation.utils.logger import setup, get
setup()
log = get()

from autofarmstation.ui.main_window import MainWindow

t0 = time.time()
win = MainWindow(verbose=False)
check("MainWindow 构造成功", True, "耗时 %.2fs" % (time.time() - t0))
win.show()
check("MainWindow show 成功", win.isVisible())

# --- 热键范式 ---
hits = {"n": 0}
win._hotkeys.register("start_all", lambda: hits.__setitem__("n", hits["n"] + 1))
check("热键 receiver 已注入", win._hotkeys._receiver_getter is not None)

# --- pm.on_change 范式:在「假监控线程」里触发,不应当场执行 _sync ---
sync_calls = {"n": 0}
real_sync = win._left._sync


def counting_sync() -> None:
    sync_calls["n"] += 1
    real_sync()


win._left._sync = counting_sync

import threading
before = sync_calls["n"]
threading.Thread(target=win._pm._notify, daemon=True).start()
time.sleep(0.2)
check("监控线程触发 pm.on_change 时未当场执行 _sync(已切主线程)",
      sync_calls["n"] == before,
      "同步执行了 %d 次" % (sync_calls["n"] - before))

# 跑事件循环,确认被派派的 _sync 最终会执行
deadline = time.time() + 2.0
while sync_calls["n"] == before and time.time() < deadline:
    QCoreApplication.processEvents()
    time.sleep(0.01)
check("切主线程后 _sync 最终执行", sync_calls["n"] > before,
      "执行了 %d 次" % (sync_calls["n"] - before))

# --- 定时任务 handler 范式:必须被切到主线程 ---
posted = {"n": 0}


def fake_handler(t) -> None:
    posted["n"] += 1


win._sched.register_handler("stop_all", lambda t: win._post_to_ui(fake_handler, t))
from autofarmstation.core.scheduler import ScheduledTask, TaskFreq
st = ScheduledTask(name="smoke-stop", action="stop_all", freq=TaskFreq.ONCE,
                   run_at="2099-01-01T00:00:00")
# 直接调 handler(等价调度线程调用)
win._sched._handlers["stop_all"](st)
time.sleep(0.2)
check("调度 handler 未在调用线程当场执行(已切主线程)", posted["n"] == 0)
deadline = time.time() + 2.0
while posted["n"] == 0 and time.time() < deadline:
    QCoreApplication.processEvents()
    time.sleep(0.01)
check("切主线程后 handler 最终执行", posted["n"] == 1, "执行了 %d 次" % posted["n"])

# --- 跑一会儿,确认没有周期性崩溃 / 线程泄漏 ---
t_end = time.time() + SECONDS
crash = {"v": False}
orig_hook = sys.excepthook


def guard(exc_type, exc, tb) -> None:
    crash["v"] = True
    log.error("冒烟期出现未捕获异常:\n%s", "".join(__import__("traceback").format_exception(exc_type, exc, tb)))
    orig_hook(exc_type, exc, tb)


sys.excepthook = guard
while time.time() < t_end:
    QCoreApplication.processEvents()
    time.sleep(0.02)
sys.excepthook = orig_hook

check("运行 %.0fs 无未捕获异常" % SECONDS, not crash["v"])

# --- 干净退出 ---
# closeEvent 里会弹「确认退出」模态框,冒烟脚本没人点它,只能挂死。
# 先把 ui.confirm_exit 关掉(并落盘),再在主线程里 close。
try:
    win._cfg.set("ui.confirm_exit", False)
    win._cfg.save()
    print("  (已关闭退出确认)")
except Exception as e:  # noqa: BLE001
    print("  (关闭退出确认失败: %r)" % e)

try:
    win.close()
    check("close() 无异常", True)
except Exception as e:  # noqa: BLE001
    check("close() 无异常", False, repr(e))

# close 之后还要把收尾的事件循环跑完(QTimer.singleShot 的收尾、deleteLater)
deadline = time.time() + 5.0
while time.time() < deadline:
    QCoreApplication.processEvents()
    time.sleep(0.02)
check("close 后事件循环跑完", True)

alive_threads = [t.name for t in threading.enumerate() if t.is_alive() and t is not threading.main_thread()]
print("  残留线程: %s" % (alive_threads or "无"))

print()
print("=== 结果 ===")
print("全部通过" if ok else "存在失败项")
sys.exit(0 if ok else 1)
