"""开发探针:读取当前登录 Steam 账号在公开资料页上显示的在线状态.

原理:Steam 客户端把当前登录账号的 32 位 accountid 写在
    HKCU\\Software\\Valve\\Steam\\ActiveProcess\\ActiveUser
steamid64 = accountid + 76561197960265728,再取公开资料 XML 的 onlineState
(in-game / online / offline)。隐身时对外就是 offline。

用法:

    python scripts/probe_steam_state.py
"""

from __future__ import annotations

import re
import sys
import urllib.request
import winreg

BASE = 76561197960265728


def active_account_id() -> int:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as k:
        val, _ = winreg.QueryValueEx(k, "ActiveUser")
        return int(val)


def main() -> int:
    aid = active_account_id()
    sid = aid + BASE
    url = f"https://steamcommunity.com/profiles/{sid}?xml=1"
    print(f"accountid={aid} steamid64={sid}")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print("fetch failed:", e)
        return 1
    for tag in ("steamID", "onlineState", "stateMessage", "privacyState", "isVacBanned"):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", body, re.S)
        if m:
            print(f"  {tag} = {m.group(1).strip()[:120]}")
    if "<onlineState>" not in body:
        print("  (XML 里没有 onlineState,资料可能不公开)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
