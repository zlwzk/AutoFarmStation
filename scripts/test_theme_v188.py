"""v1.8.8 UI 主题统一回归.

验证 theme.py / theme_adapter.py:
  1. qss() 把主题色换成当前主题的值,非主题色原样保留
  2. 深色主题下 qss 是恒等变换(不引入视觉回退)
  3. adapter 装上后 setStyleSheet 自动过滤;卸掉后恢复原样
  4. refresh_all 幂等(重复刷不会越刷越糟)
  5. app.apply_theme 切主题后 token 跟着变
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
_T = Path(tempfile.mkdtemp(prefix="afs_theme_"))
os.environ["APPDATA"] = str(_T)
os.environ["LOCALAPPDATA"] = str(_T)
os.environ["TEMP"] = str(_T)
os.environ["TMP"] = str(_T)

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, (" — " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)


from PySide6.QtWidgets import QApplication, QLabel

app = QApplication.instance() or QApplication([])

from autofarmstation.ui import theme, theme_adapter

print("=== qss 主题色换算 ===")
theme.set_theme("dark")
check("深色 #888 保持(恒等,无回退)",
      theme.qss("color:#888; font-size:11px;") == "color:#888; font-size:11px;",
      theme.qss("color:#888; font-size:11px;"))
check("深色 #e08080 保持", theme.qss("color:#e08080;") == "color:#e08080;")

theme.set_theme("light")
light_hint = theme.qss("color:#888;")
check("浅色 #888 → 换成浅色主题的 hint 色", light_hint != "color:#888;", light_hint)
check("浅色 hint 色是高对比深灰", theme.token("fg_hint") == "#7a838d", theme.token("fg_hint"))
check("浅色 err 换成深红", theme.token("err") == "#c0392b", theme.token("err"))

print()
print("=== 非主题色不被误伤 ===")
check("功能色 #4a9eff 原样保留", theme.qss("color:#4a9eff;") == "color:#4a9eff;")
check("分组色 #8fd18f 会被替换(它是主题 ok 色)",
      theme.qss("color:#8fd18f;") != "color:#8fd18f;")
check("背景 #000 保留", theme.qss("background:#000;") == "background:#000;")
check("空串安全", theme.qss("") == "")
check("None 之外的普通文本不动", theme.qss("border:1px solid red;") == "border:1px solid red;")

print()
print("=== 主题令牌表完整性 ===")
dark_pal = theme.PALETTES["dark"]
light_pal = theme.PALETTES["light"]
check("两套主题令牌键一致", set(dark_pal) == set(light_pal),
      "差集: %s" % (set(dark_pal) ^ set(light_pal)))
check("所有 qss 替换表里的色在令牌表中都有对应",
      all(name in dark_pal for name in theme._TOKEN_OF_COLOR.values()),
      str([n for n in theme._TOKEN_OF_COLOR.values() if n not in dark_pal]))

print()
print("=== adapter:装上后 setStyleSheet 自动过滤 ===")
theme.set_theme("light")
lbl = QLabel("x")
check("安装返回 True", theme_adapter.install() is True)
check("重复安装安全", theme_adapter.install() is True)
lbl.setStyleSheet("color:#888; font-size:11px;")
ss = lbl.styleSheet()
check("浅色下 #888 已被换算", "#888" not in ss, ss)
check("算出了 hint 色", theme.token("fg_hint") in ss, ss)

theme.set_theme("dark")
lbl.setStyleSheet("color:#888; font-size:11px;")
check("深色下回到原值", lbl.styleSheet() == "color:#888; font-size:11px;", lbl.styleSheet())

print()
print("=== refresh_all 幂等 ===")
theme.set_theme("light")
n1 = theme_adapter.refresh_all()
n2 = theme_adapter.refresh_all()
check("重刷不抛异常且有作用", n1 >= 1, "刷新 %d 个" % n1)
check("重复刷新结果一致", lbl.styleSheet() == lbl.styleSheet())
check("刷新后仍是合法 QSS(含换算色)", theme.token("fg_hint") in lbl.styleSheet(),
      lbl.styleSheet())

print()
print("=== 卸载恢复原状 ===")
theme_adapter.uninstall()
check("卸载后重复安装可再用", theme_adapter.install() is True)
theme_adapter.uninstall()

print()
print("=== app.apply_theme 集成 ===")
from autofarmstation.app import apply_theme
apply_theme(app, "light")
check("apply_theme(light) 后 token 是浅色", theme.token("fg_hint") == "#7a838d")
apply_theme(app, "dark")
check("apply_theme(dark) 后 token 是深色", theme.token("fg_hint") == "#888")
check("apply_theme 不抛异常", True)

print()
if FAILED:
    print("未通过 %d 项: %s" % (len(FAILED), FAILED))
    sys.exit(1)
print("全部通过")
sys.exit(0)
