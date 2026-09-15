"""core 包 - 业务核心."""

from .window_finder import WindowInfo, list_visible_windows, find_windows_for_pids, get_window_info
from .input_sender import InputSender, InputEventType, MouseButton
from .autoclicker import AutoClicker, AutoClickerConfig, ClickPoint
from .key_macro import KeyMacro, KeyMacroConfig, KeyStep
from .macro_recorder import MacroRecorder, MacroPlayer, MacroEvent, MacroEventType, MacroScript
from .preview_capture import PreviewCapture
from .process_manager import ProcessManager, TrackedProcess
from .scheduler import Scheduler, ScheduledTask, TaskFreq
from .monitor import ProcessMonitor, MonitorEvent, MonitorStatus
from .automation_hub import AutomationHub, BundleResult
from .steam_status import (
    SteamStatusController,
    SUPPORTED_STATES,
    STATE_ONLINE,
    STATE_AWAY,
    STATE_INVISIBLE,
    state_label,
    read_current_state,
    is_steam_running,
)
from .statistics import Statistics
from .preset_library import (
    PresetLibrary, Preset,
    autoclicker_dict_to_points, key_macro_dict_to_steps,
)
from .session import Session
from .bat_library import BatLibrary, BatEntry, BatArg
from .steam_overlay import (
    describe as steam_overlay_describe,
    set_overlay as steam_overlay_set,
    restore_backup as steam_overlay_restore,
)

__all__ = [
    "WindowInfo",
    "list_visible_windows",
    "find_windows_for_pids",
    "get_window_info",
    "InputSender",
    "InputEventType",
    "MouseButton",
    "AutoClicker",
    "AutoClickerConfig",
    "ClickPoint",
    "KeyMacro",
    "KeyMacroConfig",
    "KeyStep",
    "MacroRecorder",
    "MacroPlayer",
    "MacroEvent",
    "MacroEventType",
    "MacroScript",
    "PreviewCapture",
    "ProcessManager",
    "TrackedProcess",
    "Scheduler",
    "ScheduledTask",
    "TaskFreq",
    "ProcessMonitor",
    "MonitorEvent",
    "MonitorStatus",
    "AutomationHub",
    "BundleResult",
    "SteamStatusController",
    "SUPPORTED_STATES",
    "STATE_ONLINE",
    "STATE_AWAY",
    "STATE_INVISIBLE",
    "state_label",
    "read_current_state",
    "is_steam_running",
    "Statistics",
    "PresetLibrary",
    "Preset",
    "autoclicker_dict_to_points",
    "key_macro_dict_to_steps",
    "Session",
    "BatLibrary", "BatEntry", "BatArg",
    "steam_overlay_describe", "steam_overlay_set", "steam_overlay_restore",
]