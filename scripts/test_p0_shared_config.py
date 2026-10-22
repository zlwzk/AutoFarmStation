"""实测 P0-1：同一个 config 对象塞给多窗口会发生什么。

审计声称：
  main_window.py:1357-1362 循环 set_clicker_config(h, ccfg) 用同一个对象
  → _clicker_cfg 的 N 个 entry 指向同一对象，hwnd 被改成最后一个
  → 所有窗口的点全部投递到最后一个窗口

这条如果属实，是「连点全打到一个窗口」的静默数据损坏 —— 价值极高，必须实测。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autofarmstation.core.autoclicker import AutoClickerConfig, ClickPoint
from autofarmstation.core.automation_hub import AutomationHub

FAILED = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, (" — " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)


print("=== 场景 1：循环 set_clicker_config 同一个对象(复现 main_window.py:1360-1362)===")
hub = AutomationHub()

# 右侧面板 build_config() 造出来的「一份」配置
shared = AutoClickerConfig(hwnd=0, points=[ClickPoint(100, 200)], interval_ms=150)
targets = [1001, 1002, 1003]

for h in targets:
    hub.set_clicker_config(h, shared)

# 逐个窗口读回：对象是否互相独立？hwnd 是否正确？
for h in targets:
    cfg = hub.clicker_config(h)
    print("  hwnd=%d -> cfg.hwnd=%d  points=%s  (对象id=%d)"
          % (h, cfg.hwnd, [(p.x, p.y) for p in cfg.points], id(cfg)))

ids = {id(hub.clicker_config(h)) for h in targets}
check("三个窗口持有各自独立的配置对象", len(ids) == 3,
      "实际只有 %d 个不同对象" % len(ids))

hwnds = [hub.clicker_config(h).hwnd for h in targets]
check("每个窗口的 hwnd 是自己的", hwnds == targets, "实际=%s" % hwnds)

print()
print("=== 场景 2：改一个窗口的配置会不会波及其它窗口 ===")
hub2 = AutomationHub()
shared2 = AutoClickerConfig(hwnd=0, points=[ClickPoint(10, 20)], interval_ms=100)
for h in [2001, 2002]:
    hub2.set_clicker_config(h, shared2)

# 用户给窗口 2001 改点位
c1 = hub2.clicker_config(2001)
c1.points.append(ClickPoint(999, 999))
c1.interval_ms = 500

c2 = hub2.clicker_config(2002)
print("  改完 2001 后，2002 的点位=%s 间隔=%d" % ([(p.x, p.y) for p in c2.points], c2.interval_ms))
check("改 2001 不影响 2002 的点位", len(c2.points) == 1, "2002 点位被污染成 %d 个" % len(c2.points))
check("改 2001 不影响 2002 的间隔", c2.interval_ms == 100, "2002 间隔变成 %d" % c2.interval_ms)

print()
print("=== 场景 3：broadcast 路径(审计说这条是对的) ===")
hub3 = AutomationHub()
base = AutoClickerConfig(hwnd=0, points=[ClickPoint(5, 5)], interval_ms=100)
hub3.broadcast_clicker_config([3001, 3002, 3003], base)
ids3 = {id(hub3.clicker_config(h)) for h in [3001, 3002, 3003]}
check("broadcast 给每个窗口独立对象", len(ids3) == 3, "实际 %d 个" % len(ids3))
check("broadcast 不改动传入的 base", base.hwnd == 0, "base.hwnd 被改成 %d" % base.hwnd)

print()
if FAILED:
    print("P0-1 实锤：未通过 %d 项 %s" % (len(FAILED), FAILED))
    print("结论：set_clicker_config / set_key_macro_config 必须 deepcopy，")
    print("      否则「启动全部」会把所有窗口的点打到最后一个窗口上。")
    sys.exit(1)
print("未复现（set_*_config 已经是安全的）")
sys.exit(0)
