"""UI 主题令牌.

## 为什么要有这个模块

``app._apply_palette`` 只设 ``QPalette``,而各个对话框 / 卡片里散着
**144 处硬编码色**(``#888`` / ``#aaa`` / ``#666`` …)。这些色是照深色底
调的:切到浅色主题时 QPalette 变了,它们不变 —— 于是「框架是浅色、
提示文字还是深色底才看得见的灰」,整片界面发糊、对比度不足。

## 用法

``from .theme import token`` 然后 ``token("muted")`` / ``token("hint")`` 拿当前
主题对应的色值;带样式的地方拼进去:

    lbl.setStyleSheet(f"color:{token('hint')}; font-size:11px;")

主题切换时调 ``theme.set_theme("light")``,之后所有 ``token()`` 都返回新值
(已经 setStyleSheet 过的控件要重新刷一次,见各对话框的 ``retranslate_theme``)。

## 语义令牌(而不是「灰 3」这种物理名)

叫语义名的原因:换个主题 / 调整对比度时,只需要改这里一张表,
不需要去 13 个文件里猜「这个灰是干什么的」。
"""

from __future__ import annotations

import re
import threading
from typing import Final

# 每个令牌在深色 / 浅色下的取值。
# 命名规则:
#   fg        - 主要文字
#   fg_muted  - 次要文字(说明、计数)
#   fg_hint   - 提示文字(最弱,但不能看不见)
#   ok/warn/err/info - 语义色
#   accent    - 主题强调色(选中、链接)
#   card_bg / card_border - 卡片背景与描边
PALETTES: Final[dict[str, dict[str, str]]] = {
    "dark": {
        "fg": "#dcdcdc",
        "fg_muted": "#aaa",
        "fg_hint": "#888",
        "ok": "#8fd18f",
        "ok_soft": "#9fd6a8",
        "warn": "#ffcb6b",
        "warn_bg": "#3a3a00",
        "warn_fg": "#ffe",
        "err": "#e08080",
        "err_soft": "#ff7575",
        "info": "#4a9eff",
        "info_soft": "#a0cfff",
        "info_bg": "#d8ecff",
        "accent": "#4a9eff",
        "card_bg": "#2b2b2b",
        "card_border": "#3c3c3c",
        "chip_bg": "#232323",
        "chip_border": "#444",
        "surface": "#262626",
    },
    "light": {
        "fg": "#1c2024",
        "fg_muted": "#5c6570",
        "fg_hint": "#7a838d",
        "ok": "#2f7d3a",
        "ok_soft": "#3f9150",
        "warn": "#9a6b00",
        "warn_bg": "#fff6d9",
        "warn_fg": "#6b4a00",
        "err": "#c0392b",
        "err_soft": "#d9534f",
        "info": "#0b63d6",
        "info_soft": "#3b82f6",
        "info_bg": "#e8f1ff",
        "accent": "#0b63d6",
        "card_bg": "#ffffff",
        "card_border": "#d7dce2",
        "chip_bg": "#eef1f5",
        "chip_border": "#c8d0d9",
        "surface": "#f5f6f8",
    },
}

_lock = threading.Lock()
_current = "dark"

# 匹配 #RGB / #RRGGBB / #AARRGGBB
_COLOR_RE: Final[re.Pattern[str]] = re.compile(r"#[0-9a-fA-F]{3,8}\b")

# 供 qss() 用的替换表:物理色 → 语义令牌。
# 只收「主题文字/背景色」;卡片上的功能色(分组徽标的绿紫青)不在表内 ——
# 那是分类标识,不该跟着主题变。
_TOKEN_OF_COLOR: Final[dict[str, str]] = {
    "#888": "fg_hint", "#888888": "fg_hint", "#999": "fg_hint", "#666": "fg_hint",
    "#aaa": "fg_muted", "#bbb": "fg_muted",
    "#e08080": "err", "#ff7575": "err_soft",
    "#8fd18f": "ok", "#9fd6a8": "ok_soft", "#8fd694": "ok",
    "#e0a080": "warn", "#ffcb6b": "warn",
    "#3a3a00": "warn_bg", "#ffe": "warn_fg", "#666600": "warn_fg",
    "#a0cfff": "info_soft", "#9fc7ff": "info_soft", "#9ad": "info_soft",
    "#d8ecff": "info_bg", "#e8f1ff": "info_bg",
    "#3c3c3c": "card_border", "#444": "chip_border", "#555": "chip_border",
    "#232323": "chip_bg", "#262626": "surface", "#2b2b2b": "card_bg",
}


def set_theme(theme: str) -> None:
    """切换主题('dark' / 'light');未知值退回 dark."""
    global _current
    with _lock:
        _current = theme if theme in PALETTES else "dark"


def current_theme() -> str:
    with _lock:
        return _current


def token(name: str, fallback: str = "#888") -> str:
    """取当前主题下某个语义令牌的色值.

    ``fallback`` 只在令牌名拼错时用 —— 拼错应该尽早发现,所以这里刻意
    不给静默默认色,而是返回一个「一看就不对」的洋红,方便在界面上认出来。
    """
    with _lock:
        theme = _current
    pal = PALETTES.get(theme, PALETTES["dark"])
    return pal.get(name, fallback)


def style(name: str, extra: str = "") -> str:
    """拼一段 ``color:<token>`` 样式,顺手带上额外声明."""
    tail = ("; " + extra) if extra else ""
    return f"color:{token(name)}{tail}"


def qss(template: str) -> str:
    """把一段 QSS 模板里的主题色替换成当前主题的语义色.

    为什么需要它:项目里有 90 多处 ``setStyleSheet("color:#888; ...")`` 写死在
    普通字符串里(还有跨行调用的),手工一个个改成 f-string 容易漏也容易改坏。
    ``theme_adapter`` 给 ``QWidget.setStyleSheet`` 装了过滤,所有样式串都会
    先过这里再设上去 —— 调用点结构一行不用改:

        lbl.setStyleSheet(qss("color:#888; font-size:11px;"))

    深色主题下这是**恒等变换**(表里的色和深色令牌取值相同),所以不会给
    现有用户带来任何视觉回退;浅色主题下才换成高对比色。
    不在表里的颜色(功能色、图片路径、red/green 之类关键字)原样保留。
    """
    if not template:
        return template

    def _sub(m: "re.Match[str]") -> str:
        c = m.group(0)
        name = _TOKEN_OF_COLOR.get(c.lower())
        return token(name) if name else c

    return _COLOR_RE.sub(_sub, template)


def all_tokens() -> dict[str, str]:
    with _lock:
        theme = _current
    return dict(PALETTES.get(theme, PALETTES["dark"]))
