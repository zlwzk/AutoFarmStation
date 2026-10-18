"""AutoFarmStation - 多开挂机大师.

v1.6.1 起:在导入 Qt 之前就把进程声明为「Per-Monitor DPI Aware」(Win10+),
这是修「聚焦只铺一半 / SendInput 坐标错位」的根因;老 Win 系统 / 非 DWM 环境
依次回退 SetProcessDpiAwarenessContext V2 / SetProcessDPIAware,全部失败就
按系统默认(普通 Win7 兼容)。

v1.8.0 起:稳定性加固 —— 全局异常兜底(sys / 线程 excepthook,未捕获异常写日志并拦截,
不再静默崩溃);截图回调带接收者上下文 + 渲染节流(卡片销毁竞态与事件队列洪泛都挡掉);
连点器新增「位置抖动(±px)」与「运行时长限制(分钟)」;游戏窗口重开后按 exe 路径自动
重绑配置并恢复运行;修复分类条 QToolButton 构造错误导致分组栏不刷新的问题。
v1.7.0 起:新增**分屏分类** —— 把窗口归入「需点击」「纯挂时长」等分组,顶部分类条
一键过滤 + 整组启动/停止;连点器修复「发送方式选择不生效」「最小化就停点」,
post 点击 / 键盘消息补发兼容性事件,修「打开日志目录」崩溃与急停永久杀调度器。
v1.6.5 起:Bat 脚本库对话框提供两种方式快速添加 .bat / .cmd 脚本:
① **拖进来** —— 把文件管理器里的脚本拖到窗口任一处(标题 / 树 / 右侧详情都能放);
② **点选进来** —— 点顶部「📁 从文件选择 .bat / .cmd…」按钮,弹出 Windows 文件选择框,可多选。
两种方式共用同一份逻辑:自动识别 GBK / UTF-8 / UTF-16 编码、同名自动加 _N 后缀、绝不覆盖。
v1.6.4 起:Bat 脚本库对话框启用拖放,可直接把文件管理器里的 .bat / .cmd 拖进窗口
快速添加为脚本;自动识别 GBK / UTF-8 / UTF-16 编码;同名自动加 _N 后缀,不覆盖。
v1.6.3 起:设置 → 更新与日志里新增「立即更新」按钮 —— 点一下自动下载最新版 exe、
校验大小、替换当前进程、自动启动新版本,全程不再弹浏览器。源码运行时引导用户
到 release 页面手动下载。打包后 exe 与「立即检查 → 前往下载」入口并存,各取所需。
v1.6.2 起:修了一批「设置里改了但实际不生效」的项目(主题切换、英文语言占位、
默认连点参数、启动时最小化、挂机时段守护、Steam 叠加层自动应用)。
所有用户数据严格保存在 `%APPDATA%\\AutoFarmStation\\`(Windows 自带的用户级目录),
不同 Windows 账户互不干扰,升级默认是平滑迁移。
"""

from __future__ import annotations

import ctypes
import os

__version__ = "1.8.0"
__app_name__ = "AutoFarmStation"
__app_name_cn__ = "多开挂机大师"
__author_handle__ = "zlwzk"


# === DPI 感知声明(必须在导入 PySide6 / 创建任何 QApplication 之前调用) ===
def _set_process_dpi_awareness() -> None:
    """声明进程 DPI 感知等级:

    - 优先 SetProcessDpiAwareness(2) = PROCESS_PER_MONITOR_DPI_AWARE(Win8.1+)
    - 回退 SetProcessDpiAwarenessContext(-4) = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2(Win10 1703+)
    - 再回退 SetProcessDPIAware()(Win Vista+;只声明「系统 DPI」感知,不是 Per-Monitor)
    - 失败 / 非 Windows / 非 DWM 环境静默忽略(不影响运行)。
    """
    if os.name != "nt":
        return
    # 1. Win 8.1+: SetProcessDpiAwareness(level)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    # 2. Win 10 1703+: SetProcessDpiAwarenessContext(handle)
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        return
    except Exception:
        pass
    # 3. Win Vista+: SetProcessDPIAware()
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return
    except Exception:
        return


_set_process_dpi_awareness()


# === 项目简介(同时用于 GitHub repo 描述、README 顶部、Release 标题) ===
PROJECT_TAGLINE = (
    "一款专为挂机游戏设计的 Windows 多开自动化工作站。"
    "窗口嵌入、实时预览、连点器、键盘宏、宏录制、多窗口同步执行、"
    "自动唤醒游戏、定时任务、音量控制、Steam 状态联动,一站搞定。"
)
PROJECT_DESCRIPTION = (
    "AutoFarmStation(多开挂机大师)是一款面向 Steam 挂机游戏 / 放置类游戏 / 放置网页游戏的"
    "Windows 多开自动化工具。\n"
    "\n"
    "• **窗口嵌入**:把游戏窗口收进软件里,桌面上不再散落一堆窗口,一屏掌控所有挂机农场\n"
    "• 左侧自适应预览网格实时显示每个窗口画面(GDI 抓帧,被遮挡也能截到);\n"
    "  点击聚焦即按显示器工作区铺满,完整显示整个游戏界面\n"
    "• 卡片上可**单独停止/恢复进程**;恢复时游戏没开就**自动唤醒**(Steam 游戏先启动 Steam)\n"
    "• 关闭软件可**同步结束被追踪的游戏进程**;启动恢复会话时自动唤醒没开的游戏\n"
    "• **音量控制**:整体音量只作用于已加入的窗口(不会连带静音 QQ / 浏览器),可静音、可还原快照\n"
    "• **定时任务**:一次性 / 间隔 / 每天 / 每周(可多选) 四种频率 + 延时执行,十种动作,持久化保存\n"
    "• **挂机时段**:只在指定时间段挂机,时段外自动暂停 / 停止,回到时段给出提示\n"
    "• **资源监控**:卡片显示 CPU / 内存占用,超阈值提醒一次\n"
    "• **日志查看器**:界面内过滤 / 复制 / 导出日志,不用翻目录\n"
    "• **配置备份**:设置 / 窗口 / 预设 / 宏 / 任务打包导出,导入前自动备份\n"
    "• 内置连点器、键盘宏、宏录制/回放\n"
    "• **多窗口同步 / 单窗口独立**:勾选任意几个窗口,一键让它们同步执行同一套连贯操作;\n"
    "  每个窗口又各自保存一份独立配置,可以分开执行互不相同的操作\n"
    "• **分屏分类**:把窗口归入「需点击 / 纯挂时长」等分组,分类条一键过滤 + 整组启动/停止,\n"
    "  挂机农场再多也一目了然\n"
    "• **Steam 状态联动**:开始挂机自动把 Steam 状态切到「在线 / 离开 / 隐身」,\n"
    "  挂机停完自动还原(本地实现,不碰账号密码)\n"
    "• 自带 Steam 挂机游戏预设(Melvor Idle、Universal Paperclips、Clicker Heroes、"
    "Idle Slayer、Mr. Mine、NGU IDLE、Cell to Singularity 等),一键应用\n"
    "• 卡片尺寸可由用户在设置里自由调节(适配小屏多开 / 大屏看清画面)\n"
    "• 支持任意自定义进程(不仅限 Steam),所有路径/用户名/游戏目录自动脱敏\n"
    "• 进程监控、定时调度、运行时长统计,7×24 托管你的挂机农场\n"
    "\n"
    "完全本地运行,无需联网,无广告无追踪。"
)


# === Release 公告(也作为 GitHub Release 正文) ===
RELEASE_NOTES = """\
# AutoFarmStation v1.8.0 · 稳定加固 + 点击器升级 + 窗口重开自动接回

这一版的核心是**稳**:把「用一阵就莫名崩掉」的几条路径全部堵住,
同时把点击器和多窗口并行能力再升一档。

---

## 一、防崩溃加固(重点)

| # | 问题 | 修复 |
|---|------|------|
| 1 | **打包成 exe 后,界面回调里任何一个没接住的异常都会让程序直接消失**(stderr 没人看) | 新增全局兜底:`sys.excepthook` + 工作线程 `threading.excepthook` 全部接管 —— 异常写进日志并在状态栏提示,程序继续运行 |
| 2 | **分类条(分屏分类)不刷新 / 启动时报错** | v1.7.0 的分类条按钮用错了 `QToolButton` 构造方式,每次刷新都抛异常 —— 已修,分组过滤 / 整组启停恢复正常 |
| 3 | **截图回调与卡片销毁竞态**:窗口关闭瞬间,还在路上的截图回调可能在已销毁的控件上渲染 | 回调带接收者上下文,卡片销毁后未执行的渲染自动丢弃;再加渲染节流,多开高帧率时不再刷爆事件队列 |
| 4 | 截图 / 连点 / 宏等工作线程异常只打印线程名 | 统一写日志(含完整堆栈),崩溃排查有据可查 |

## 二、点击器升级

| 新能力 | 说明 |
|--------|------|
| **位置抖动(±N px)** | 每次点击在点位周围随机偏移,更像真人操作,也能规避「完全同坐标连点」的检测;0 = 关闭 |
| **运行时长限制(N 分钟)** | 挂够时长自动停止,不用盯表;0 = 不限时 |
| 失败统计保留 | 状态栏继续显示「连点 mm:ss (次数 / 失败 N)」 |

## 三、多窗口并行:三种用法都说清楚了

1. **单窗口独立**:右侧面板配置只对选中的窗口生效,单独启停;
2. **勾选同步启动**:勾几个窗口一键同时启动 —— **每个窗口用自己已保存的配置,可以互不相同**(不同逻辑并行跑);想让大家跑同一套,先点「应用当前配置」;
3. **分屏分类整组启停**:右键卡片标题分组后,分类条「▶ 启动本组 / ■ 停止本组」批量操作。

界面 tooltip 与空态提示已全部改写成上面这套说法,不再歧义。

## 四、窗口重开自动接回

游戏自己崩溃重启 / 闪退后重新打开,窗口句柄变了但 exe 没变 ——
现在会**自动把配置 / 别名 / 分组搬到新窗口上并恢复运行**,不用手动重新添加。
(仅对之前有保存配置的窗口生效,30 秒扫描冷却,不会误接管。)

---

## 兼容与升级

- 配置 / 会话 / 预设 / 宏 / 定时任务全部向后兼容;新增字段带默认值,老配置直接用;
- 分组、点位比例坐标等 v1.7.0 数据不受影响。

---

## 全局热键

| 快捷键 | 动作 |
|--------|------|
| F9 | 启动全部(有勾选时只启动勾选的窗口) |
| F10 | 停止全部 |
| Ctrl + Alt + P | 一键急停(定时任务 / 监控保持可用) |
| Ctrl + T | 定时任务 |
| Ctrl + B | Bat 脚本库 |
| Ctrl + , | 设置 |

---

## 自检

`python scripts/selftest.py` **49 项全过**(v1.7.0 是 47 项,新增:连点新字段序列化、崩溃兜底)。

---

## 文件说明

- `AutoFarmStation.exe` — 主程序,单文件 exe,Windows 10/11 64 位

---

## 历史版本(已发布)

<details>
<summary><b>v1.7.0 · 分屏分类 + 连点器兼容性修复</b>(点击展开)</summary>

- 分屏分类:窗口归入「需点击 / 纯挂时长 / 自定义」分组,分类条过滤 + 整组启停;
- 连点器修复「发送方式选择不生效」「最小化就停点」;post 点击补 WM_MOUSEMOVE,
  键盘消息补扫描码 lParam;新增 auto 发送方式与失败统计;
- 修「打开日志目录」崩溃、急停永久杀调度器、挂机时段判定可测性。

</details>

<details>
<summary><b>v1.6.5 · Bat 脚本库:拖拽 + 点选双入口</b>(点击展开)</summary>

- 「📁 从文件选择 .bat / .cmd…」按钮 + 拖拽导入双保险;
- 拖拽三层 acceptDrops + 视觉反馈;共享 `_import_paths()`;
- 用户目录旧 manifest / 旧 .bat 全部不动。

</details>

<details>
<summary><b>v1.6.2 · 修 6 项「设置了但不生效」</b>(点击展开)</summary>

主题切换、语言占位禁用、默认连点参数、启动最小化、挂机时段守护、Steam 叠加层自动应用;
bat 库 seed 改为「只追加不覆盖」。

</details>

<details>
<summary><b>v1.6.1 · 进程识别 / 聚焦 / 边距缩放 / 连点 auto</b>(点击展开)</summary>

包含隐藏窗口、按 PID / 进程名添加;Per-Monitor DPI Aware;每窗口独立边距 + 缩放;
连点默认 auto(后台投递优先,失败转真实输入)。

</details>
"""