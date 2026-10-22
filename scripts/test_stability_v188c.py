"""v1.8.8 稳定性回归(第三批):音量 COM / Steam 乱序 / 过期一次性任务.

修掉的问题:
1. audio._ensure_com 用进程级 flag —— COM 初始化是每线程的,
   调度线程调音量会静默失效(定时调音量没反应)。
2. steam_status 把「已等于挂机态」的当前状态当成挂机前状态存下来,
   还原时反而设成挂机态,用户原状态永久丢失。
3. scheduler 对「时间已过的一次性任务」永远不执行,且每秒写盘。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, (" — " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)


def test_audio_com_per_thread() -> None:
    print("=== 音量:COM 初始化必须按线程 ===")
    from autofarmstation.core import audio

    check("_ensure_com 存在", hasattr(audio, "_ensure_com"))
    # 每线程独立:主线程初始化后,子线程必须还没初始化
    audio._ensure_com()
    main_ready = getattr(audio._com_local, "ready", False)
    other = {"ready": None}

    def worker() -> None:
        other["ready"] = getattr(audio._com_local, "ready", False)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    check("主线程初始化后其它线程仍未初始化(threading.local 隔离)",
          other["ready"] in (None, False),
          "子线程读到 ready=%r" % other["ready"])
    if main_ready:
        print("  (主线程 COM 初始化成功)")


def test_steam_no_overwrite_with_farm_state() -> None:
    print("=== Steam:不要把挂机态当成挂机前状态 ===")
    from autofarmstation.core import steam_status as ss

    # 造一个最小可用的 controller
    class FakeCfg:
        def get(self, key, default=None):
            return {
                "steam.enabled": True,
                "steam.farm_state": "away",
                "steam.restore_previous": True,
                "steam.verify": False,
            }.get(key, default)

    ctrl = ss.SteamStatusController(FakeCfg())
    check("farm_state 读出为 away", ctrl.farm_state == "away", ctrl.farm_state)

    # 当前状态已经是 away(= farm_state)时进入挂机:
    # 不应把 away 记成 _previous_state,也不应触发切换
    ctrl.current_state = lambda: "away"  # type: ignore[method-assign]
    ctrl._notify = lambda msg, once_key=None: None  # type: ignore[method-assign]
    ctrl._worker_async = lambda state, note: (_ for _ in ()).throw(  # type: ignore[misc]
        AssertionError("不该发起切换"))
    with ctrl._lock:
        ctrl._enter_locked()
    check("当前已是挂机态时不覆盖 _previous_state",
          ctrl._previous_state is None, "被记成 %r" % ctrl._previous_state)
    check("当前已是挂机态时不置 _applied", ctrl._applied is False)

    # generation token 存在且单调
    check("有 _generation 字段", hasattr(ctrl, "_generation"))
    g0 = ctrl._generation
    ctrl._generation += 1
    check("generation 可单调递增", ctrl._generation == g0 + 1)


def test_overdue_once_task_runs() -> None:
    print("=== 定时任务:时间已过的一次性任务必须补跑 ===")
    import tempfile
    from autofarmstation.core.scheduler import Scheduler, ScheduledTask, TaskFreq

    with tempfile.TemporaryDirectory() as td:
        sched = Scheduler(path=Path(td) / "schedules.json", autosave=False)

        fired: list[str] = []
        sched.register_handler("stop_all", lambda t: fired.append(t.name))

        # 造一个「1 小时前就该跑」的一次性任务
        past = ScheduledTask(
            name="过期任务", action="stop_all", freq=TaskFreq.ONCE,
            run_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600)),
        )
        sched.add(past, persist=False)
        t = sched.get("过期任务")
        t.enabled = True
        t.next_run = 0.0  # 模拟刚加载、还没算过 next_run

        nxt = sched.compute_next(t)
        check("过期的一次性任务 compute_next 返回 0", nxt == 0.0, "返回 %r" % nxt)

        sched.start()
        try:
            deadline = time.time() + 4.0
            while not fired and time.time() < deadline:
                time.sleep(0.1)
        finally:
            sched.stop()
        check("过期的一次性任务被执行", fired == ["过期任务"], "fired=%s" % fired)
        check("执行后被禁用", t.enabled is False)


def main() -> int:
    print("=== v1.8.8 稳定性回归(第三批)===")
    test_audio_com_per_thread()
    print()
    test_steam_no_overwrite_with_farm_state()
    print()
    test_overdue_once_task_runs()
    print()
    if FAILED:
        print("未通过 %d 项: %s" % (len(FAILED), FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
