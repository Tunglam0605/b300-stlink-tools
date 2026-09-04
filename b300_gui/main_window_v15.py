"""Production MainWindow variant for the unified engineering Debug Studio.

The base MainWindow remains import-compatible for the established regression suite.
The production executable uses MainWindowV15, where LOCAL/GATEWAY/CLIENT are owned by
Debug Studio.  Gateway host preparation remains available as an internal Debug page,
not as a second top-level SSH workflow.
"""

from __future__ import annotations

from .debug_tab_v15 import DebugTabV15
from .main_window import MainWindow


class MainWindowV15(MainWindow):
    """Main B300 window with one source of truth for local/remote Debug roles."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        previous = self.debug_tab
        index = self.tabs.indexOf(previous)
        if index < 0:
            raise RuntimeError("Base Debug tab is missing from MainWindow.")

        try:
            previous.prepare_shutdown()
        except Exception:
            pass
        try:
            previous.log.disconnect()
            previous.operation_state_changed.disconnect()
        except Exception:
            pass

        self.tabs.removeTab(index)
        previous.setParent(None)
        previous.deleteLater()

        self.debug_tab = DebugTabV15(
            self.debug_service,
            self._selected_probe,
            self,
            settings=self.settings,
            probe_count=lambda: len(self._probes),
        )
        self.debug_tab.log.connect(self.append_log)
        self.debug_tab.operation_state_changed.connect(self._hardware_activity_changed)
        self.tabs.insertTab(index, self.debug_tab, "Debug")

        self._configure_unified_remote_debug_ux()

    def _configure_unified_remote_debug_ux(self) -> None:
        """Make SSH/Gateway setup subordinate to Debug Studio in production UX."""
        # Remote transport is infrastructure for Debug Studio, not a peer feature.
        self.nav_gateway_btn.setVisible(False)
        self.nav_debug_btn.setText("📊  Studio Debug · Theo dõi")
        self.nav_debug_btn.setToolTip(
            "LOCAL: ST-Link trực tiếp · GATEWAY: máy cắm ST-Link · CLIENT: Debug từ xa qua SSH"
        )

        # Keep the proven GatewaySetupTab implementation internally, but expose only
        # the Gateway-host branch.  The Client login source of truth is Debug Studio.
        gateway = self.gateway_tab
        gateway.role_stack.setCurrentIndex(0)

        role_button = getattr(gateway, "gateway_role_button", None)
        role_header = role_button.parentWidget() if role_button is not None else None
        if role_header is not None:
            role_header.setVisible(False)

        # Public-key authorization is legacy maintenance only.  v0.15+ Client uses
        # the embedded password RemoteSession owned by DebugWorkstationController.
        authorize_button = getattr(gateway, "authorize_key_button", None)
        authorize_group = authorize_button.parentWidget() if authorize_button is not None else None
        if authorize_group is not None:
            authorize_group.setVisible(False)

    def _update_page_context(self, index: int) -> None:
        super()._update_page_context(index)
        if index == 3 and hasattr(self, "page_title"):
            self.page_title.setText("Gateway · Hạ tầng Debug từ xa")
            self.page_subtitle.setText(
                "Thiết lập máy này làm Gateway SSH/OpenOCD. Client đăng nhập tại Studio Debug."
            )
            self.status_banner.setText("Gateway setup · quay lại Studio Debug sau khi READY")

    def _tab_changed(self, index: int) -> None:
        super()._tab_changed(index)
        if index == 3 and hasattr(self, "nav_debug_btn"):
            # Gateway host setup is an internal page of the visible Debug workflow.
            for button in self.nav_buttons:
                button.setChecked(False)
            self.nav_debug_btn.setChecked(True)
