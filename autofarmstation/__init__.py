"""AutoFarmStation - 多开挂机大师.

v1.6.1 起:在导入 Qt 之前就把进程声明为「Per-Monitor DPI Aware」(Win10+),
这是修「聚焦只铺一半 / SendInput 坐标错位」的根因;老 Win 系统 / 非 DWM 环境
依次回退 SetProcessDpiAwarenessContext V2 / SetProcessDPIAware,全部失败就
按系统默认(普通 Win7 兼容)。

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

__version__ = "1.6.3"
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
# AutoFarmStation v1.6.3 · 设置里直接「一键更新」,不弹浏览器不拷链接

v1.6.1 解决了「点击、聚焦、缩放、识别」四件大事;但社区反馈:设置面板里
**6 项勾上 / 改完根本没生效** —— 这一版按根因逐条修。同时把**所有用户数据
严格留在本机**这件事在公告里再明确写一次,方便你 / 同事 / 老板看清楚。

---

## 这次修了哪些「假设置」

| # | 设置项 | 之前 | 现在 |
|---|--------|------|------|
| 1 | 主题(深 / 浅色) | 写死深色,选了没用 | 立刻应用调色板(部分旧 widget 仍要重启才能 100% 跟随,会提示) |
| 2 | 语言(English) | 选项存在但点了无翻译 | 占位项**直接禁用**,鼠标移上去会注明「规划中」,避免误选 |
| 3 | **默认连点参数** | 改完新建连点器还是 200ms / 10% | 新建连点器**真的**读 `defaults.clicker_*`(间隔、抖动、按键、方式) |
| 4 | **启动时最小化** | 复选框从不生效 | 启动时**真的**最小化到托盘,焦点不会被打扰 |
| 5 | **挂机时段** | 改完从不限制 | 时段外**拒绝启动**(明确文案),并**自动停掉所有正在跑的**;回到时段给出提示(action=pause 记住之前的窗口) |
| 6 | Steam 叠加层「禁用」 | 复选框切换不自动写 steam.cfg | 设置面板**确认**时如果勾选了,自动写一次 steam.cfg + 备份原文件,不再要求手动点「立即写入」 |
| 7 | **Bat 脚本库每次更新都丢** | 内置脚本升级时直接覆盖用户目录,用户改过的副本 / 自己加的脚本会被覆盖 | `ensure_seeded` 改为「**只追加不覆盖**」:内置 .bat 缺失时自动恢复,但绝不覆盖用户在用户目录里**已有的** .bat 与 manifest 条目;新版本新增的内置条目会**合并**进用户的 manifest,用户改过的 title / desc / args 全部保留 |

修法的核心:

- `app._apply_palette(theme)` 由原来的「永远 dark」改为读 `cfg.get("ui.theme")`;
  设置面板点「确认」就立刻调用,无需重启就能看到主面板变浅。
- `ActionPanel` / `ClickerPanel` 在构造时接收 `cfg`,把 `defaults.clicker_*` 读进来;
  没有传 cfg 的旧代码路径**保持硬编码 200ms / 10%**,行为与 v1.6.1 完全一致。
- `MainWindow` 启动后注册一个 `showEvent`,在第一次显示时按 `ui.start_minimized`
  设置 `Qt.WindowState.WindowMinimized`,再也不忽略这个开关。
- 新增 `core/farm_window.py::FarmWindowGuard` —— 时段外 `should_block_start()` 返回 True,
  `ClickerPanel._on_start` 在启动前调一个外部注入的回调(由主窗口提供),
  阻断并弹出明确提示;`MainWindow` 还启了一个 30 秒的 QTimer,
  在**进入 / 离开**时段时分别停 / 提示。
- `SettingsDialog._on_accept` 在 cfg 落盘后,顺手把「主题」和「Steam 叠加层」
  也立刻生效。
- `core/bat_library.py::ensure_seeded` 重写:内置 .bat 文件**只在缺失时**复制;
  用户 manifest.json **只在「新增 id」**时追加,用户改过的条目永远保留。
  启动日志会打印 `%APPDATA%\\AutoFarmStation\\bats`,让用户**看见**脚本保存在哪儿,
  再开弹也塞了一行提示。

---

## 数据本地 + 用户隔离 + 升级不丢

强调一遍:

- **所有数据严格存在 `%APPDATA%\\AutoFarmStation\\`**(Windows 自带的当前用户级目录),
  与你的 Windows 账户绑定;
- **不同 Windows 账户之间互不可见** —— 公司电脑别人登录,看不到你的窗口 / 预设 / 宏 / 任务;
- 升级版本不会清空配置;新字段全部带默认值(老 `config.json` 直接能用);
  `TrackedProcess` 在 v1.6.1 增的边距 / 缩放在 v1.6.2 同样保留,完全向前兼容;
- **绝对不上传**:窗口标题、进程路径、用户名、机器名、AppData、TEMP 路径
  都不出现在仓库 commit、日志、issue、反馈文本里。

---

## 顺带

- 全局热键 / 嵌入 / 多开联动 / Steam 状态联动 / 资源监控 / 备份导入导出
  行为**未变**(v1.6.1 已经全部正常工作);
- 自检 **37 项** 全过(v1.6.1 是 31 项,新增 6 项:主题切换、English 占位禁用、
  默认连点参数生效、挂机时段守护、用户 bat 跨 re-init 持久、内置 manifest 合并进用户)。

---

## 兼容与升级

- 配置文件向后兼容:`farm_window.enabled` / `start` / `end` / `action`
  之前就存在,只是 v1.6.2 才真的去读;老值直接生效;
- 老「整体音量」配置不动;会话快照、预设、宏、定时任务格式未变。

---

## 全局热键

| 快捷键 | 动作 |
|--------|------|
| F9 | 启动全部(有勾选时只启动勾选的窗口) |
| F10 | 停止全部 |
| Ctrl + Alt + P | 一键急停 |
| Ctrl + T | 定时任务 |
| Ctrl + B | Bat 脚本库 |
| Ctrl + , | 设置 |

---

## 完整功能

- **窗口嵌入**:把游戏窗口收进软件内,一键嵌入/弹出,退出自动还原。
- **多窗口预览**:左侧自适应网格(1/2/3/4/6 格子),GDI 实时抓帧,点击聚焦(铺满工作区),双击最大化。
- **每窗口独立边距 + 画面缩放**:按窗口设置聚焦时的上 / 下 / 左 / 右像素边距,以及画面缩放比例
  (边框全屏游戏会跟着等比缩放)。
- **多窗口同步 / 单窗口独立**:勾选多个窗口一键同步执行同一套操作;每个窗口又可各自独立配置。
  - **连点器**:多点击点位轮询、随机抖动、左右中/双击支持、可视化选点、比例坐标抗缩放。
    - **点击方式 = 自动**(默认):先后台投递、失败自动改真实输入;可锁定成「只投递」/「只真实输入」。
  - **键盘宏**:按键序列回放、持续按键、单键循环,任意组合,按窗口尺寸等比映射。
  - **宏录制/回放**:录制鼠标 + 键盘轨迹,保存为可分享的 JSON,任意窗口回放。
- **进程管理**:卡片停止/恢复进程,恢复时自动唤醒游戏(Steam 游戏先启动 Steam),退出软件可同步关闭游戏。
- **进程识别更稳**:
  - 「选择窗口」可显示隐藏 / 无标题的窗口;
  - 工具菜单「按 PID 添加」/「按进程名添加」,连 PID 直接补齐遗漏的进程。
- **音量控制**:整体音量只作用于已加入窗口(不动系统总音量),单窗口会话音量,可静音、可还原快照。
- **定时任务**:一次性 / 间隔 / 每天 / 每周(可多选) 四种频率 + 延时执行,十种动作,持久化保存。
- **挂机时段**:只在指定时间段挂机,时段外自动暂停 / 停止,回到时段给出提示。
- **资源监控**:卡片显示 CPU / 内存占用,超阈值提醒一次。
- **日志查看器**:界面内过滤 / 复制 / 导出日志。
- **配置备份**:设置 / 窗口 / 预设 / 宏 / 任务打包导出,导入前自动备份。
- **Steam 状态联动**:挂机自动切状态、停完自动还原(在线 / 离开 / 隐身)。
- **Steam 叠加层控制**:一键关闭 Shift+Tab,写 steam.cfg + 备份还原。
- **Bat 脚本库**:内置 + 用户脚本两路合并,运行不弹黑窗,支持参数。
- **窗口控制**:置顶、透明度调节,按进程独立配置。
- **进程监控**:进程崩溃检测 + 运行状态实时展示。
- **预设库**:内置 10 个 Steam 挂机游戏预设(Melvor Idle、Universal Paperclips、Clicker Heroes、
  Idle Slayer、Mr. Mine、NGU IDLE、Cell to Singularity、AdVenture Capitalist、Egg, Inc.、通用挂机游戏)。
- **统计**:每进程运行时长、点击/按键次数、累计天数。
- **设置**:界面、预览、默认连点参数、游戏进程、挂机时段、资源监控、音量、Steam、更新与日志。
- **DPI 感知**:启动时声明 Per-Monitor DPI Aware,聚焦 / SendInput / 嵌入都与系统 DPI 缩放吻合。

---

## 隐私

- 100% 本地运行,所有数据保存在 `%APPDATA%\\AutoFarmStation\\`。
- 窗口嵌入只调用 Windows 自身的 `SetWindowPos`/`SetParent` 窗口接口,不注入、不读写目标进程内存。
- 音量控制只调用 Windows Core Audio 接口,不录屏、不采集音频数据。
- 自动唤醒只调用 `subprocess` / `steam://` 协议,不注入游戏、不改游戏文件。
- Steam 状态联动只触发 Steam 自己的 `steam://` 协议、只读本地 `localconfig.vdf`:
  **不联网、不读账号密码、不碰 Steam 令牌文件**。
- 日志/反馈上传前自动脱敏:Windows 用户名、游戏安装目录、AppData、TEMP、机器名一律替换为占位符。
- UI 不显示 Windows 用户名,所有路径用 `%APPDATA%\\...` / `%USERPROFILE%\\...` 占位符展示。
- 不上传任何数据,更新检查只读取 GitHub Releases。

---

## 自检

`python -m scripts.selftest` **37 项全过**(v1.6.1 是 31 项)。

---

## 反馈

应用内「帮助 → 反馈建议」一键前往 GitHub Issues(已脱敏)。
仓库:[github.com/zlwzk/AutoFarmStation](https://github.com/zlwzk/AutoFarmStation)

---

## 文件说明

- `AutoFarmStation.exe` — 主程序,单文件 exe,Windows 10/11 64 位

---

## 历史版本(已发布)

<details>
<summary><b>v1.6.1 · 修四件用着用着就难受的老毛病</b>(点击展开)</summary>

按用户反馈集中修四个老毛病:

1. **进程识别不到** — 新增「包含隐藏窗口」复选 / 「按 PID 添加」/「按进程名添加」;
   新增 `list_all_windows_for_pid` + `list_all_windows_across_processes`,
   `find_windows_for_pids` 支持 `include_hidden`。
2. **聚焦之后画面显示不完全** — 启动时声明 Per-Monitor DPI Aware(Win10+),
   `focus_window` 改用 `MonitorFromWindow + GetMonitorInfoW` 取窗口所在显示器
   实际工作区;`fit_to_work_area` 用 `SetWindowPos + SWP_FRAMECHANGED`。
3. **自定义缩放每条边** — 新增「边距 / 缩放」对话框(4 边像素 + 50%~150%
   画面缩放),每窗口独立保存;`focus_window` 支持 `(l,t,r,b)` margin + scale。
4. **连点不能用** — `InputSender` 默认 `mode=auto`(先后台投递 PostMessage,失败
   自动改用真实输入 SendInput),SendInput ABSOLUTE 改用整个虚拟屏幕归一化
   而非主显示器像素;状态栏在回退时明确提示原因。auto-clicker 兼容。

附带:`TrackedProcess` 新增 `margin_left/top/right/bottom` + `content_scale`,配置文件
向后兼容;`__init__.py` 启动时依次尝试 `SetProcessDpiAwareness(2)` → V2 → `SetProcessDPIAware`。

</details>
"""