"""Workspace views for B300 ST-Link desktop workstation."""

from .operator_view import OperatorView
from .rnd_flash_view import RndFlashView
from .memory_view import MemoryView
from .debug_studio_view import DebugStudioView
from .gateway_view import GatewayView
from .program_view import ProgramView
from .monitor_view import MonitorView
from .debug_vscode_view import DebugVsCodeView
from .device_view import DeviceView
from .settings_view import SettingsView

__all__ = [
    "OperatorView",
    "RndFlashView",
    "MemoryView",
    "DebugStudioView",
    "GatewayView",
    "ProgramView",
    "MonitorView",
    "DebugVsCodeView",
    "DeviceView",
    "SettingsView",
]
