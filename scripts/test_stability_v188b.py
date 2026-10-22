"""v1.8.8 稳定性回归(第二批):调度器与监控线程的异常自愈.

修掉的问题:
1. scheduler._loop 里 save() 无保护 —— 落盘失败会让调度线程死掉,
   所有定时任务永久失效且界面无感知。
2. monitor._try_restart 无限重启 —— 游戏反复崩溃会打成重启风暴打满 CPU。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, (" — " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)


def test_scheduler_survives_save_failure() -> None:
    print("=== 调度器:落盘失败不能杀死调度线程 ===")
    from autofarmstation.core.scheduler import Scheduler, ScheduledTask, TaskFreq
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        sched = Scheduler(path=Path(td) / "schedules.json", autosave=True)

        # save() 内部只吞 OSError;这里换成抛 ValueError(模拟 JSON 序列化失败
        # 之类的非 OSError),验证调度循环不会因此死掉
        def boom() -> None:
            raise ValueError("模拟非 OSError 写入失败")

        sched.save = boom  # type: ignore[method-assign]

        fired: list[str] = []

        def handler(t: ScheduledTask) -> None:
            fired.append(t.name)

        sched.register_handler("start_all", handler)

        # 一个 1 秒后就到点的间隔任务
        t = ScheduledTask(name="每1秒", action="start_all", freq=TaskFreq.INTERVAL,
                          interval_sec=1)
        sched.add(t, persist=False)
        sched.start()
        try:
            time.sleep(3.5)
        finally:
            alive = sched.is_alive() if hasattr(sched, "is_alive") else None
            sched.stop()

        check("落盘持续失败时任务仍在执行", len(fired) >= 2,
              "3.5 秒内只触发了 %d 次" % len(fired))
        if alive is not None:
            check("调度线程在 save 抛异常后仍存活", alive)


def test_restart_storm_guard() -> None:
    print("=== 监控:重启风暴限流 ===")
    from autofarmstation.core.monitor import ProcessMonitor, MonitorEvent, MonitorStatus
    from autofarmstation.core.process_manager import ProcessManager

    pm = ProcessManager()
    mon = ProcessMonitor(pm, interval_sec=0.2, auto_restart=True,
                         restart_exe="nonexistent_zzz.exe")

    spawns: list[list[str]] = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            spawns.append(list(cmd))

    import subprocess
    real = subprocess.Popen
    subprocess.Popen = FakePopen  # type: ignore[misc]
    try:
        # 连续点 10 次火:按 min_gap=10s,最多只会有 1 次真正启动
        for _ in range(10):
            mon._try_restart()
        first = len(spawns)
        check("10 秒冷却内连续触发只启动 1 次", first <= 1, "实际启动 %d 次" % first)

        # 把时间推过去,允许再启动;但窗口内(5 分钟)最多 3 次
        # 注意:上一段已经成功启动过 1 次并记入 history,这里必须清零重算,
        # 否则会把上一段的 1 次算进来,得到 4 次而误判为超限。
        mon._last_restart_ts = 0.0
        mon._restart_history = []
        spawns.clear()          # 断言基准也要清零,只数这一轮
        for _ in range(10):
            mon._last_restart_ts = 0.0
            mon._try_restart()
        check("5 分钟窗口内最多重启 3 次", len(spawns) <= 3, "本轮启动 %d 次" % len(spawns))
    finally:
        subprocess.Popen = real  # type: ignore[misc]

    # 风暴提示
    mon._restart_history = [time.time()] * 5
    mon._storm_notice_ts = 0.0
    msgs: list[str] = []
    mon.on_event(lambda ev: msgs.append(ev.message))
    mon._last_restart_ts = 0.0
    mon._try_restart()
    check("触发风暴上限时给出用户提示", any("自动重启已暂停" in m for m in msgs), str(msgs[:1]))

    # 带空格路径的参数不能被切碎
    mon2 = ProcessMonitor(pm, auto_restart=True,
                          restart_exe="C:/Program Files/Game/game.exe",
                          restart_args='"-windowed" "D:/My Games/save"')
    got: list[list[str]] = []

    class FakePopen2:
        def __init__(self, cmd, **kw):
            got.append(list(cmd))

    subprocess.Popen = FakePopen2  # type: ignore[misc]
    try:
        mon2._try_restart()
    finally:
        subprocess.Popen = real  # type: ignore[misc]
    if got:
        cmd = got[0]
        check("带空格路径参数完整保留", "D:/My Games/save" in cmd, str(cmd))
        check("exe 路径完整", cmd[0] == "C:/Program Files/Game/game.exe", str(cmd))
    else:
        check("带空格路径参数完整保留", False, "没有启动")


def main() -> int:
    print("=== v1.8.8 稳定性回归(第二批)===")
    test_scheduler_survives_save_failure()
    print()
    test_restart_storm_guard()
    print()
    if FAILED:
        print("未通过 %d 项: %s" % (len(FAILED), FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
