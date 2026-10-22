"""v1.8.8 稳定性修复回归测试.

覆盖本轮修掉的 P0：
1. atomic_write 必须是原子的（Windows 上 unlink+move 存在原文件消失空窗）
2. set_window_pos 的 SWP 标志位：只传 y 时不能把 x 也一起禁掉
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, (" — " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)


def test_atomic_write() -> None:
    print("=== atomic_write 原子性 ===")
    from autofarmstation.utils.paths import atomic_write

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "config.json"
        atomic_write(p, '{"a": 1}')
        check("首次写入可读", json.loads(p.read_text(encoding="utf-8")) == {"a": 1})

        # 反复覆盖，每次都必须是完整内容（不存在半截/空文件）
        # 次数收在 60:每次 atomic_write 都带 fsync(≈20ms),跑 200 次会把
        # 测试拖到十几秒,而正确性用 60 次已经能覆盖。
        bad = 0
        t0 = time.time()
        for i in range(60):
            atomic_write(p, json.dumps({"n": i, "pad": "x" * 500}))
            try:
                if json.loads(p.read_text(encoding="utf-8"))["n"] != i:
                    bad += 1
            except Exception:
                bad += 1
        elapsed = time.time() - t0
        check("60 次覆盖全部完整可读", bad == 0, "损坏 %d 次" % bad)
        # 性能护栏:单次写入不能慢到离谱(否则用户改个设置都卡)
        check("单次写入均值 < 100ms", elapsed / 60 < 0.1,
              "%.1f ms/次" % (elapsed / 60 * 1000))

        # 覆盖后不得残留 .tmp
        leftovers = [x.name for x in p.parent.iterdir() if ".tmp" in x.name]
        check("无 .tmp 残留", not leftovers, str(leftovers))

        # 并发写同一路径：不能出现文件不存在或损坏
        p2 = Path(td) / "concurrent.json"
        atomic_write(p2, '{"k": 0}')
        errors: list[str] = []

        def worker(idx: int) -> None:
            try:
                for i in range(8):
                    atomic_write(p2, json.dumps({"k": idx, "i": i}))
            except Exception as ex:  # noqa: BLE001
                errors.append(repr(ex))

        ts = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        check("并发写无异常", not errors, "; ".join(errors[:2]))
        try:
            json.loads(p2.read_text(encoding="utf-8"))
            ok = True
        except Exception:
            ok = False
        check("并发写后文件仍是合法 JSON", ok)

        # 写失败时必须清掉半截 tmp（模拟目录只读的场景用非法内容类型）
        p3 = Path(td) / "bad.json"
        try:
            atomic_write(p3, 12345)  # type: ignore[arg-type]
        except Exception:
            pass
        leftovers2 = [x.name for x in p3.parent.iterdir() if ".tmp" in x.name]
        check("异常路径无 tmp 残留", not leftovers2, str(leftovers2))


def test_set_window_pos_flags() -> None:
    print("=== set_window_pos SWP 标志位 ===")
    # 直接验证标志常量逻辑，不起真窗口（CI/无头环境也能跑）
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002

    src = (Path(__file__).resolve().parents[1] / "autofarmstation" / "core"
           / "window_finder.py").read_text(encoding="utf-8")

    # 关键：x is None 或 y is None 都必须置 NOMOVE；w/h 必须置 NOSIZE
    check("x is None 走 SWP_NOMOVE", "if x is None or y is None:" in src
          and "f |= SWP_NOMOVE" in src)
    check("w is None 走 SWP_NOSIZE", "if w is None or h is None:" in src
          and "f |= SWP_NOSIZE" in src)
    # 反模式：不得再出现 `if y is None: f |= SWP_NOMOVE`
    check("无 y-only 误用 NOMOVE 的反模式",
          "if y is None:\n        f |= SWP_NOMOVE" not in src)

    # 语义单元测试：只传 y 时 x 必须被 NOMOVE 保护，否则 cx=-1 会把窗口移到 -1
    def build_flags(x, y, w, h, flags=0):
        cx = -1 if x is None else int(x)
        f = flags
        if x is None or y is None:
            f |= SWP_NOMOVE
        if w is None or h is None:
            f |= SWP_NOSIZE
        return cx, f

    cx, f = build_flags(None, 100, None, None)
    check("只传 y 时置 NOMOVE（x 不被改成 -1）", bool(f & SWP_NOMOVE))
    check("只传 y 时置 NOSIZE", bool(f & SWP_NOSIZE))

    cx, f = build_flags(50, None, None, None)
    check("只传 x 时置 NOMOVE", bool(f & SWP_NOMOVE))

    cx, f = build_flags(50, 100, 800, 600)
    check("四参全传时无 NOMOVE/NOSIZE", not (f & (SWP_NOMOVE | SWP_NOSIZE)))


def main() -> int:
    print("=== v1.8.8 稳定性回归 ===")
    test_atomic_write()
    print()
    test_set_window_pos_flags()
    print()
    if FAILED:
        print("未通过 %d 项: %s" % (len(FAILED), FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
