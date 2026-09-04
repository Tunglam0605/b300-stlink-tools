"""Production MainWindow for B300 ST-Link Tools v0.18 Simplified UX.

Architecture:
  B300 = ST-Link Programmer + Safety Engine + Gateway + VS Code Debug Bridge.

Main Navigation:
  1. PROGRAM  (Operator-oriented Application & Bootloader provisioning)
  2. MONITOR  (Zero-halt realtime DWT / variable observation)
  3. DEBUG    (VS Code Bridge: LOCAL, GATEWAY, CLIENT)
  4. DEVICE   (Target MCU & ST-Link Probe health)
  5. SETTINGS (Machine setup, theme, updater, diagnostics)

The base MainWindow and MainWindowV15 remain import-compatible for regression tests.
The production executable uses MainWindowV18.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from b300_core.models import ImageInfo, ProbeInfo, TargetInfo
from .main_window import MainWindow
from .views.debug_vscode_view import DebugVsCodeView
from .views.device_view import DeviceView
from .views.monitor_view import MonitorView
from .views.program_view import ProgramView
from .views.settings_view import SettingsView


class MainWindowV18(MainWindow):
    """v0.18 Simplified B300 Desktop Window."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._configure_v18_navigation()
        self._configure_v18_views()
        self.show_page("program")

    def _configure_v18_navigation(self) -> None:
        """Replace cluttered tabs and sidebar buttons with the clean 5-page navigation."""
        # Hide legacy sidebar buttons
        for btn_name in ("nav_flash_btn", "nav_memory_btn", "nav_debug_btn", "nav_gateway_btn"):
            btn = getattr(self, btn_name, None)
            if btn is not None:
                btn.setVisible(False)

        # Reconfigure sidebar layout with clean 5 navigation buttons
        sidebar_layout = self.sidebar.layout()
        if sidebar_layout is None:
            return

        self.v18_nav_buttons = []

        # Find position to insert navigation buttons (right after nav title)
        insert_idx = 3

        btn_specs = [
            ("program", "⚡  PROGRAM", "Nạp firmware Application & Bootloader an toàn"),
            ("monitor", "📈  MONITOR", "Theo dõi biến realtime Zero-Halt (không dừng MCU)"),
            ("debug", "🐞  DEBUG", "Debug với VS Code + Cortex-Debug (LOCAL / GATEWAY / CLIENT)"),
            ("device", "🔍  DEVICE", "Thông số phần cứng Target MCU & mạch ST-Link"),
            ("settings", "⚙️  SETTINGS", "Cài đặt driver, OpenOCD, giao diện và cập nhật"),
        ]

        self.nav_program_btn = None
        self.nav_monitor_btn = None
        self.nav_debug_btn_v18 = None
        self.nav_device_btn = None
        self.nav_settings_btn = None

        button_attrs = [
            "nav_program_btn",
            "nav_monitor_btn",
            "nav_debug_btn_v18",
            "nav_device_btn",
            "nav_settings_btn",
        ]

        for i, (page_key, title, tooltip) in enumerate(btn_specs):
            btn = QPushButton(title)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda _checked=False, pk=page_key: self.show_page(pk))
            sidebar_layout.insertWidget(insert_idx + i, btn)
            self.v18_nav_buttons.append(btn)
            setattr(self, button_attrs[i], btn)

        # Keep legacy nav_buttons list updated so theme / helper methods don't crash
        self.nav_buttons = self.v18_nav_buttons

    def _configure_v18_views(self) -> None:
        """Instantiate the 5 simplified views and mount them into the primary stack."""
        # Hide the legacy tabs widget from display
        self.tabs.setVisible(False)

        # Create the new v0.18 central stack
        self.v18_stack = QStackedWidget()
        self.v18_stack.setObjectName("v18MainStack")

        # 1. PROGRAM VIEW
        self.program_view = ProgramView(self)
        self.program_view.flash_application_requested.connect(self._on_v18_flash_application)
        self.program_view.flash_bootloader_requested.connect(self._on_v18_flash_bootloader)
        self.program_view.file_selected.connect(self._on_v18_file_selected)
        self.program_view.probe_refresh_requested.connect(self.refresh_probes)
        self.program_view.target_inspect_requested.connect(self.inspect_target)
        self.v18_stack.addWidget(self.program_view)  # Index 0: PROGRAM

        # 2. MONITOR VIEW (Zero-Halt Live Observation)
        self.monitor_view = MonitorView(self)
        self.v18_stack.addWidget(self.monitor_view)  # Index 1: MONITOR

        # 3. DEBUG VIEW (VS Code Bridge)
        self.debug_vscode_view = DebugVsCodeView(self)
        self.debug_vscode_view.open_local_vscode_requested.connect(self._on_v18_open_local_vscode)
        self.debug_vscode_view.open_remote_vscode_requested.connect(self._on_v18_open_remote_vscode)
        self.debug_vscode_view.start_gateway_requested.connect(self._on_v18_start_gateway)
        self.debug_vscode_view.stop_gateway_requested.connect(self._on_v18_stop_gateway)
        self.debug_vscode_view.legacy_ide_requested.connect(self._on_v18_open_legacy_ide)
        self.v18_stack.addWidget(self.debug_vscode_view)  # Index 2: DEBUG

        # 4. DEVICE VIEW
        self.device_view = DeviceView(self)
        self.device_view.refresh_requested.connect(self.refresh_probes)
        self.device_view.doctor_requested.connect(self.inspect_target)
        self.v18_stack.addWidget(self.device_view)  # Index 3: DEVICE

        # 5. SETTINGS VIEW
        self.settings_view = SettingsView(self)
        self.settings_view.machine_setup_requested.connect(self.show_machine_setup)
        self.settings_view.toggle_theme_requested.connect(self._on_toggle_theme)
        self.settings_view.check_updates_requested.connect(lambda: self.check_for_updates(manual=True))
        self.settings_view.export_support_bundle_requested.connect(self.export_support_bundle)
        self.settings_view.about_requested.connect(self.show_about)
        self.settings_view.release_notes_requested.connect(self.show_release_notes)
        self.v18_stack.addWidget(self.settings_view)  # Index 4: SETTINGS

        # Replace tabs in layout
        content_area = self.tabs.parentWidget()
        if content_area is not None and content_area.layout() is not None:
            content_area.layout().addWidget(self.v18_stack)

        # Initial hardware sync
        self.program_view.set_probes(self._probes)
        self.debug_vscode_view.set_probes(self._probes)
        self.device_view.set_probes(self._probes)
        if self.target_info:
            self.program_view.set_target_info(self.target_info)
            self.debug_vscode_view.set_target_info(self.target_info)
            self.device_view.set_target_info(self.target_info)

    def show_page(self, page_name: str) -> None:
        """Switch active page cleanly without resetting services or halting target."""
        page_map = {
            "program": (0, self.nav_program_btn, "PROGRAM · Nạp firmware",
                        "Nạp Application hoặc Bootloader an toàn; Sector 0–2 được bảo vệ."),
            "monitor": (1, self.nav_monitor_btn, "MONITOR · Theo dõi realtime",
                        "Zero-halt observation: quan sát biến và DWT timeline mà không dừng MCU."),
            "debug": (2, self.nav_debug_btn_v18, "DEBUG · VS Code Bridge",
                      "Tích hợp VS Code + Cortex-Debug cho LOCAL, GATEWAY và CLIENT debug."),
            "device": (3, self.nav_device_btn, "DEVICE · Thông số phần cứng",
                      "Kiểm tra Target STM32F407, điện áp VDD, Option Bytes và WRP Bootloader."),
            "settings": (4, self.nav_settings_btn, "SETTINGS · Thiết lập môi trường",
                         "Cài đặt ST-Link driver, OpenOCD runtime, cấu hình giao diện và cập nhật."),
        }

        entry = page_map.get(page_name.lower())
        if entry is None:
            return

        index, active_btn, title, subtitle = entry

        # Update sidebar button states
        for btn in self.v18_nav_buttons:
            btn.setChecked(btn is active_btn)

        # Switch stack
        self.v18_stack.setCurrentIndex(index)

        # Update page title & subtitle
        if hasattr(self, "page_title"):
            self.page_title.setText(title)
        if hasattr(self, "page_subtitle"):
            self.page_subtitle.setText(subtitle)

    # ------------------------------------------------------------------
    # Hardware & State Synchronization
    # ------------------------------------------------------------------
    def refresh_probes(self) -> None:
        super().refresh_probes()
        if hasattr(self, "program_view"):
            self.program_view.set_probes(self._probes)
        if hasattr(self, "debug_vscode_view"):
            self.debug_vscode_view.set_probes(self._probes)
        if hasattr(self, "device_view"):
            self.device_view.set_probes(self._probes)

    def apply_target_info(self, info: TargetInfo) -> None:
        super().apply_target_info(info)
        if hasattr(self, "program_view"):
            self.program_view.set_target_info(info)
        if hasattr(self, "debug_vscode_view"):
            self.debug_vscode_view.set_target_info(info)
        if hasattr(self, "device_view"):
            self.device_view.set_target_info(info)

    def append_log(self, text: str) -> None:
        super().append_log(text)
        if hasattr(self, "program_view") and hasattr(self.program_view, "append_log"):
            self.program_view.append_log(text)

    # ------------------------------------------------------------------
    # View Event Handlers
    # ------------------------------------------------------------------
    def _on_v18_file_selected(self, path: Path) -> None:
        self.load_image_path(path, quiet=True)

    def _on_v18_flash_application(self, path: Path, is_dry_run: bool) -> None:
        if self.image_info is None or Path(self.image_info.path) != path:
            if not self.load_image_path(path):
                return
        if not self.target_ready or self.flash_plan is None:
            self._set_status(
                "Chưa thể nạp: hãy kiểm tra đúng Target B300, WRP Bootloader và flash plan.",
                "error",
            )
            if hasattr(self, "program_view"):
                self.program_view.banner.show_fail(
                    "CHƯA SẴN SÀNG NẠP FIRMWARE",
                    "Target hoặc flash plan chưa đạt điều kiện an toàn.",
                    next_action="Bấm 'Kiểm tra Target' để xác minh WRP Bootloader trước khi nạp.",
                )
            return
        if is_dry_run:
            self.show_dry_run()
        else:
            self.confirm_flash()

    def _on_v18_flash_bootloader(self, confirmed: bool) -> None:
        if not confirmed:
            return
        self.confirm_factory_provision()

    def _on_v18_open_local_vscode(self, workspace: Path, elf: Path) -> None:
        self.append_log(f"Đã mở VS Code tại workspace: {workspace} (ELF: {elf})")

    def _on_v18_open_remote_vscode(self, profile: dict) -> None:
        self.append_log(f"Đã tạo cấu hình Remote VS Code cho Gateway {profile.get('host')}")

    def _on_v18_start_gateway(self) -> None:
        self.append_log("Yêu cầu khởi động Gateway OpenOCD loopback...")
        # TODO(v0.18-backend): GatewayController.start_gateway(...)

    def _on_v18_stop_gateway(self) -> None:
        self.append_log("Yêu cầu dừng Gateway OpenOCD...")
        # TODO(v0.18-backend): GatewayController.stop_gateway(...)

    def _on_v18_open_legacy_ide(self) -> None:
        """Allow explicit developer access to the legacy internal debug workbench."""
        # Unhide legacy tabs and switch to Debug tab for diagnostic inspection
        self.tabs.setCurrentIndex(2)
        self.tabs.setVisible(True)
        self.v18_stack.setVisible(False)
        self.append_log("Chuyển sang Internal Debug Workbench (chế độ chẩn đoán kỹ thuật).")


__all__ = ["MainWindowV18"]
