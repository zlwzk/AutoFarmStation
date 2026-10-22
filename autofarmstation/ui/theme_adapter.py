"""UI 主题统一(运行时装裁 + 静态收尾两条腿走路).

## 为什么这么做

前两次想用脚本把 70 处 ``setStyleSheet("color:#888;...")`` 逐个包成
``qss(...)`` 都失败了 —— 项目里有跨行调用的 ``setStyleSheet(...)``,
正则在单行视角下会把括号配错,直接生成 SyntaxError。

所以改成两条腿:

1. **运行时兜底(本模块)**:装上之后,所有 ``setStyleSheet`` / ``setStyleSheet``
   的字符串参数都先过一遍 ``theme.qss()`` —— 主题色自动换成当前主题的值。
   调用点一行不用改,跨行调用也不关心里面怎么断。
2. **静态收尾**:把最高频的十几个位置手工改成 f-string + ``token()``
   (见 ``docs`` 里的清单),让代码可读、也让 IDE 能跳转。

## 装法

``MainWindow.__init__`` 里调 ``install()`` 一次即可;主题切换时
``apply_theme`` 会同步 ``theme.set_theme``,之后所有新设置的样式都跟着变。

注意:已经 setStyleSheet 过的控件,主题切换后要重新刷一次才会变
(``refresh_widget_theme`` 提供这个能力)。
"""

from __future__ import annotations

import weakref
from typing import Any

from . import theme as _theme

_installed = False
_patched: list[tuple[Any, str, Any]] = []

# 登记「设过样式」的控件,主题切换时重刷它们。
# 用 WeakSet:控件销毁后自动脱落,不会拖住已经关闭的对话框。
_registry: "weakref.WeakSet[Any]" = weakref.WeakSet()


def _wrap(original):
    """把一个 setStyleSheet 方法包成「先过 qss 再调用」."""

    def wrapper(self, style: str, *args, **kwargs):
        try:
            if isinstance(style, str) and style:
                style = _theme.qss(style)
                # 登记:主题切换时要重刷它
                _registry.add(self)
        except Exception:  # noqa: BLE001
            pass  # 主题模块出问题也不能让界面设不了样式
        return original(self, style, *args, **kwargs)

    return wrapper


def install() -> bool:
    """给 QWidget.setStyleSheet 装上主题过滤(只装一次)."""
    global _installed
    if _installed:
        return True
    try:
        from PySide6.QtWidgets import QWidget
    except Exception:  # noqa: BLE001
        return False

    orig = QWidget.setStyleSheet
    if getattr(orig, "_afs_themed", False):
        _installed = True
        return True
    wrapped = _wrap(orig)
    wrapped._afs_themed = True  # type: ignore[attr-defined]
    QWidget.setStyleSheet = wrapped  # type: ignore[method-assign]
    _patched.append((QWidget, "setStyleSheet", orig))
    _installed = True
    return True


def uninstall() -> None:
    """卸掉过滤(测试用)."""
    global _installed
    for cls, name, orig in _patched:
        try:
            setattr(cls, name, orig)
        except Exception:  # noqa: BLE001
            pass
    _patched.clear()
    _installed = False


# === 主题切换后刷新已设过样式的控件 ===

def refresh_all(root: Any = None) -> int:
    """重刷所有登记过的控件的样式.

    做法:把它们当前的 stylesheet 取出来、重新过一遍 qss 再设回去。
    因为 qss 是幂等的(不在表里的色原样返回),重复刷不会越刷越糟。
    """
    n = 0
    for w in list(_registry):
        try:
            cur = w.styleSheet()
            if cur:
                w.setStyleSheet(_theme.qss(cur))
                n += 1
        except Exception:  # noqa: BLE001
            continue
    return n
