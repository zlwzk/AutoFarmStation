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
from .statistics import Statistics
from .preset_library import (
    PresetLibrary, Preset,
    autoclicker_dict_to_points, key_macro_dict_to_steps,
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
    "Statistics",
    "PresetLibrary",
    "Preset",
    "autoclicker_dict_to_points",
    "key_macro_dict_to_steps",
]