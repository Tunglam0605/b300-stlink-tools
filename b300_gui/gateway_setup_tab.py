"""Gateway host preparation UI for OpenSSH-based remote debug."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from b300_core.gateway_readiness import inspect_gateway_readiness
from b300_core.gateway_network import GatewayEndpointProbe, probe_gateway_ssh_endpoint
from b300_core.gateway_setup import (
    DEFAULT_SSH_PORT, GatewayHostReport, GatewayPrepareResult, build_gateway_prepare_plan,
    client_connection_text, inspect_gateway_host, prepare_gateway_host,
)
from b300_core.ssh_identity import (
    AuthorizedKeyResult, SshClientPrepareResult, SshClientPrerequisiteReport, SshIdentityReport,
    ensure_ssh_identity, inspect_ssh_client_prerequisites, inspect_ssh_identity,
    install_gateway_public_key, prepare_ssh_client_prerequisites, validate_public_key,
)
from b300_core.ssh_host_trust import (
    GatewayHostKey, HostTrustResult, local_gateway_host_key, scan_gateway_host_key,
    trust_gateway_host_key,
)
from b300_core.remote_profile import RemoteGatewayProfile, load_remote_profile, save_remote_profile
from b300_core.remote_connectivity import RemoteConnectivityResult, check_remote_connectivity
from .workers import FunctionWorker
from .collapsible_card import CollapsibleCard


_ACTION_TEXT = {
    "install_openssh_server": "Cài OpenSSH Server",
    "enable_ssh_startup": "Bật SSH tự khởi động cùng hệ điều hành",
    "start_ssh_service": "Khởi động dịch vụ SSH",
    "set_active_network_private": "Đổi mạng Windows đang dùng từ Public sang Private (chỉ profile đang hoạt động)",
    "manual_fix_network_profile": "DỪNG: không xác định duy nhất mạng Windows đang hoạt động",
    "allow_ssh_firewall": "Cho phép duy nhất SSH TCP/22 qua host firewall",
    "manual_fix_debug_exposure": "DỪNG: debug port 3333/4444/6666 đang bị expose ra ngoài loopback",
}


class GatewaySetupTab(QWidget):
    log = Signal(str)
    operation_state_changed = Signal()

    def __init__(
            self, parent=None, *,
            inspector: Callable[..., GatewayHostReport] = inspect_gateway_host,
            preparer: Callable[..., GatewayPrepareResult] = prepare_gateway_host,
            full_inspector: Callable[..., object] = inspect_gateway_readiness,
            identity_inspector: Callable[..., SshIdentityReport] = inspect_ssh_identity,
            identity_ensurer: Callable[..., SshIdentityReport] = ensure_ssh_identity,
            client_prereq_inspector: Callable[..., SshClientPrerequisiteReport] = inspect_ssh_client_prerequisites,
            client_prereq_preparer: Callable[..., SshClientPrepareResult] = prepare_ssh_client_prerequisites,
            key_authorizer: Callable[..., AuthorizedKeyResult] = install_gateway_public_key,
            host_key_reader: Callable[..., GatewayHostKey] = local_gateway_host_key,
            host_key_scanner: Callable[..., GatewayHostKey] = scan_gateway_host_key,
            host_truster: Callable[..., HostTrustResult] = trust_gateway_host_key,
            endpoint_prober: Callable[..., GatewayEndpointProbe] = probe_gateway_ssh_endpoint,
            profile_loader: Callable[..., Optional[RemoteGatewayProfile]] = load_remote_profile,
            profile_saver: Callable[..., object] = save_remote_profile,
            connectivity_checker: Callable[..., RemoteConnectivityResult] = check_remote_connectivity,
            auto_refresh: bool = True,
    ) -> None:
        super().__init__(parent)
        self.inspector = inspector
        self.preparer = preparer
        self.full_inspector = full_inspector
        self.identity_inspector = identity_inspector
        self.identity_ensurer = identity_ensurer
        self.client_prereq_inspector = client_prereq_inspector
        self.client_prereq_preparer = client_prereq_preparer
        self.key_authorizer = key_authorizer
        self.host_key_reader = host_key_reader
        self.host_key_scanner = host_key_scanner
        self.host_truster = host_truster
        self.endpoint_prober = endpoint_prober
        self.profile_loader = profile_loader
        self.profile_saver = profile_saver
        self.connectivity_checker = connectivity_checker
        self._remote_profile: Optional[RemoteGatewayProfile] = None
        self._client_network_problem: Optional[GatewayEndpointProbe] = None
        self._connectivity_ready = False
        self._identity: Optional[SshIdentityReport] = None
        self._local_host_key: Optional[GatewayHostKey] = None
        self._gateway_ssh_port = DEFAULT_SSH_PORT
        self._pending_host_key_port: Optional[int] = None
        self._worker: Optional[FunctionWorker] = None
        self._retired_workers = []
        self._report: Optional[GatewayHostReport] = None
        self._build_ui()
        self._render_identity(self.identity_inspector())
        self._load_saved_profile()
        self._update_next_action()
        if auto_refresh:
            self.refresh_host()

    @property
    def has_active_operation(self) -> bool:
        return self._worker is not None

    def request_shutdown(self) -> None:
        """Request cooperative cancellation so the owning window can close later."""
        if self._worker is not None:
            self._worker.cancel()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        header = QFrame()
        header.setObjectName("gatewayHero")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 10, 12, 10)
        header_layout.setSpacing(6)
        role_intro = QLabel("Chọn vai trò của máy này")
        role_intro.setObjectName("roleSectionTitle")
        header_layout.addWidget(role_intro)
        subtitle = QLabel("Chọn đúng vai trò, sau đó làm theo bước được đề xuất.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        header_layout.addWidget(subtitle)

        role_row = QHBoxLayout()
        role_row.setSpacing(8)
        role_label = QLabel("Vai trò")
        role_label.setObjectName("rolePrompt")
        role_row.addWidget(role_label)
        self.gateway_role_button = QPushButton("🖥  Gateway · ST-Link")
        self.gateway_role_button.setObjectName("roleToggle")
        self.gateway_role_button.setCheckable(True)
        self.client_role_button = QPushButton("💻  Client · Từ xa")
        self.client_role_button.setObjectName("roleToggle")
        self.client_role_button.setCheckable(True)
        self.role_group = QButtonGroup(self)
        self.role_group.setExclusive(True)
        self.role_group.addButton(self.gateway_role_button, 0)
        self.role_group.addButton(self.client_role_button, 1)
        self.gateway_role_button.setChecked(True)
        self.gateway_role_button.setMinimumHeight(34)
        self.client_role_button.setMinimumHeight(34)
        self.gateway_role_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.client_role_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        role_row.addWidget(self.gateway_role_button, 1)
        role_row.addWidget(self.client_role_button, 1)
        header_layout.addLayout(role_row)
        root.addWidget(header)

        self.next_action = QLabel("Bước tiếp theo: kiểm tra trạng thái máy Gateway.")
        self.next_action.setObjectName("nextActionBanner")
        self.next_action.setWordWrap(True)
        root.addWidget(self.next_action)

        self.role_stack = QStackedWidget()
        self.role_stack.setObjectName("gatewayRoleStack")
        self.role_stack.addWidget(self._build_gateway_page())
        self.role_stack.addWidget(self._build_client_page())
        root.addWidget(self.role_stack, 1)
        self.gateway_role_button.clicked.connect(lambda: self._select_role(0))
        self.client_role_button.clicked.connect(lambda: self._select_role(1))

        self.progress = QProgressBar()
        self.progress.setObjectName("gatewayProgress")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("Idle")
        self.progress.setVisible(False)
        root.addWidget(self.progress)

    def _step_header(self, number: int, title: str, description: str) -> QWidget:
        frame = QFrame()
        frame.setObjectName("workflowStepHeader")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        badge = QLabel(str(number))
        badge.setObjectName("workflowStepBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(24, 24)
        text = QLabel("<b>%s</b><br><span style='color:#64748B'>%s</span>" % (title, description))
        text.setWordWrap(True)
        layout.addWidget(badge)
        layout.addWidget(text, 1)
        return frame

    def _scroll_page(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("workflowScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _build_gateway_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 6)
        layout.setSpacing(8)

        layout.addWidget(self._step_header(
            1, "Chuẩn bị Gateway",
            "Kiểm OpenSSH Server, SSH service, firewall TCP/22 và chắc chắn debug ports không lộ ra LAN.",
        ))
        host_group = QGroupBox("Gateway readiness")
        host_layout = QVBoxLayout(host_group)
        self.status = QLabel("Chưa kiểm tra Gateway host")
        self.status.setObjectName("gatewaySetupStatus")
        self.status.setWordWrap(True)
        host_layout.addWidget(self.status)
        self.gateway_check_details = CollapsibleCard(
            "Chi tiết kiểm tra",
            "OpenSSH, firewall và trạng thái cổng",
            expanded=False,
        )
        self.check_table = QTableWidget(0, 3)
        self.check_table.setObjectName("gatewaySetupCheckTable")
        self.check_table.setHorizontalHeaderLabels(["Kiểm tra", "Trạng thái", "Chi tiết"])
        self.check_table.verticalHeader().setVisible(False)
        self.check_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.check_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self.check_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.resizeSection(0, 190)
        header.resizeSection(1, 105)
        self.check_table.setMinimumHeight(132)
        self.check_table.setMaximumHeight(180)
        self.gateway_check_details.content_layout.addWidget(self.check_table)
        host_actions = QHBoxLayout()
        self.refresh_button = QPushButton("Kiểm tra lại")
        self.refresh_button.setObjectName("gatewayRefreshButton")
        self.refresh_button.clicked.connect(self.refresh_host)
        self.prepare_button = QPushButton("Chuẩn bị Gateway")
        self.prepare_button.setObjectName("gatewayPrepareButton")
        self.prepare_button.clicked.connect(self.prepare_host)
        self.selftest_button = QPushButton("Gateway Self-Test")
        self.selftest_button.setObjectName("gatewaySelfTestButton")
        self.selftest_button.clicked.connect(self.run_selftest)
        for button in (self.refresh_button, self.prepare_button, self.selftest_button):
            button.setMinimumHeight(34)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            host_actions.addWidget(button, 1)
        host_layout.addLayout(host_actions)
        host_layout.addWidget(self.gateway_check_details)
        layout.addWidget(host_group)

        layout.addWidget(self._step_header(
            2, "Gửi thông tin kết nối cho Client",
            "Copy địa chỉ kết nối và fingerprint trực tiếp từ Gateway này sang laptop Client.",
        ))
        config_group = QGroupBox("Kết nối Client")
        config_layout = QVBoxLayout(config_group)
        self.copy_button = QPushButton("Sao chép cấu hình cho Client")
        self.copy_button.setObjectName("gatewayCopyClientButton")
        self.copy_button.clicked.connect(self.copy_client_configuration)
        self.copy_button.setEnabled(False)
        self.copy_button.setMinimumHeight(34)
        config_layout.addWidget(self.copy_button)

        self.gateway_connection_details = CollapsibleCard(
            "Chi tiết kết nối",
            "IP/hostname, SSH port và fingerprint",
            expanded=False,
        )
        details_layout = self.gateway_connection_details.content_layout
        self.client_config = QPlainTextEdit()
        self.client_config.setObjectName("gatewayClientConfiguration")
        self.client_config.setReadOnly(True)
        self.client_config.setMinimumHeight(72)
        self.client_config.setMaximumHeight(96)
        self.client_config.setPlaceholderText("Kiểm tra Gateway để lấy thông tin kết nối.")
        details_layout.addWidget(self.client_config)
        self.host_key_status = QLabel("Fingerprint: chưa đọc")
        self.host_key_status.setObjectName("gatewayHostKeyStatus")
        self.host_key_status.setProperty("state", "idle")
        self.host_key_status.setWordWrap(True)
        details_layout.addWidget(self.host_key_status)
        config_actions = QHBoxLayout()
        self.show_host_key_button = QPushButton("Đọc fingerprint")
        self.show_host_key_button.setObjectName("gatewayShowHostKeyButton")
        self.show_host_key_button.clicked.connect(self.show_local_host_key)
        self.copy_host_fingerprint_button = QPushButton("Copy fingerprint")
        self.copy_host_fingerprint_button.setObjectName("gatewayCopyHostFingerprintButton")
        self.copy_host_fingerprint_button.clicked.connect(self.copy_host_fingerprint)
        self.copy_host_fingerprint_button.setEnabled(False)
        for button in (self.show_host_key_button, self.copy_host_fingerprint_button):
            button.setMinimumHeight(34)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            config_actions.addWidget(button, 1)
        details_layout.addLayout(config_actions)
        config_layout.addWidget(self.gateway_connection_details)
        layout.addWidget(config_group)

        layout.addWidget(self._step_header(
            3, "Cho phép Client đăng nhập",
            "Paste public key từ Client. Private key tuyệt đối không được đưa sang Gateway.",
        ))
        authorize_group = QGroupBox("Authorize Client public key")
        authorize_layout = QHBoxLayout(authorize_group)
        authorize_note = QLabel(
            "Sau khi Client tạo B300 key, copy <b>public key</b> sang đây để cho phép SSH không mật khẩu."
        )
        authorize_note.setWordWrap(True)
        self.authorize_key_button = QPushButton("Authorize Public Key…")
        self.authorize_key_button.setObjectName("gatewayAuthorizeKeyButton")
        self.authorize_key_button.clicked.connect(self.authorize_client_key)
        self.authorize_key_button.setMinimumHeight(34)
        authorize_layout.addWidget(authorize_note, 1)
        authorize_layout.addWidget(self.authorize_key_button)
        layout.addWidget(authorize_group)

        self.gateway_safety_details = CollapsibleCard(
            "An toàn & kỹ thuật",
            "Các giới hạn mà tool luôn giữ",
            expanded=False,
        )
        safety = QLabel(
            "Không sửa sshd_config, không đổi password, không tắt firewall và không tạo rule cho "
            "TCP 3333/4444/6666. Nếu SSH đã READY, Prepare là no-op."
        )
        safety.setObjectName("safetyNote")
        safety.setWordWrap(True)
        self.gateway_safety_details.content_layout.addWidget(safety)
        layout.addWidget(self.gateway_safety_details)
        layout.addStretch(1)
        return self._scroll_page(content)

    def _build_client_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 6)
        layout.setSpacing(8)

        layout.addWidget(self._step_header(
            1, "Chuẩn bị khóa Client",
            "Tool tạo/reuse một B300 ed25519 key riêng. Chỉ public key được copy sang Gateway.",
        ))
        identity_group = QGroupBox("B300 Client SSH identity")
        identity_layout = QVBoxLayout(identity_group)
        self.identity_status = QLabel("B300 Client key: not checked")
        self.identity_status.setObjectName("gatewayIdentityStatus")
        self.identity_status.setWordWrap(True)
        identity_layout.addWidget(self.identity_status)
        identity_actions = QHBoxLayout()
        self.identity_prepare_button = QPushButton("Tạo / kiểm tra Key")
        self.identity_prepare_button.setObjectName("gatewayIdentityPrepareButton")
        self.identity_prepare_button.clicked.connect(self.prepare_client_identity)
        self.identity_copy_button = QPushButton("Copy Public Key")
        self.identity_copy_button.setObjectName("gatewayIdentityCopyButton")
        self.identity_copy_button.clicked.connect(self.copy_public_key)
        for button in (self.identity_prepare_button, self.identity_copy_button):
            button.setMinimumHeight(34)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            identity_actions.addWidget(button, 1)
        identity_layout.addLayout(identity_actions)
        layout.addWidget(identity_group)

        layout.addWidget(self._step_header(
            2, "Xác minh Gateway và lưu profile",
            "Nhập fingerprint nhìn trực tiếp trên máy Gateway. Tool scan rồi chỉ trust nếu khớp chính xác.",
        ))
        trust_group = QGroupBox("Remote Gateway")
        trust_layout = QVBoxLayout(trust_group)
        endpoint_grid = QGridLayout()
        endpoint_grid.setHorizontalSpacing(8)
        endpoint_grid.setVerticalSpacing(6)
        host_label = QLabel("Host / IP")
        host_label.setObjectName("formLabel")
        user_label = QLabel("User")
        user_label.setObjectName("formLabel")
        port_label = QLabel("Port")
        port_label.setObjectName("formLabel")
        endpoint_grid.addWidget(host_label, 0, 0)
        endpoint_grid.addWidget(user_label, 0, 1)
        endpoint_grid.addWidget(port_label, 0, 2)
        self.trust_host = QLineEdit()
        self.trust_host.setObjectName("gatewayTrustHost")
        self.trust_host.setPlaceholderText("192.168.1.95")
        self.trust_host.setMinimumHeight(32)
        endpoint_grid.addWidget(self.trust_host, 1, 0)
        self.trust_user = QLineEdit()
        self.trust_user.setObjectName("gatewayTrustUser")
        self.trust_user.setPlaceholderText("automation")
        self.trust_user.setMinimumHeight(32)
        endpoint_grid.addWidget(self.trust_user, 1, 1)
        self.trust_port = QSpinBox()
        self.trust_port.setObjectName("gatewayTrustPort")
        self.trust_port.setRange(1, 65535)
        self.trust_port.setValue(22)
        self.trust_port.setMinimumWidth(92)
        self.trust_port.setMinimumHeight(32)
        endpoint_grid.addWidget(self.trust_port, 1, 2)
        endpoint_grid.setColumnStretch(0, 3)
        endpoint_grid.setColumnStretch(1, 2)
        endpoint_grid.setColumnStretch(2, 0)
        trust_layout.addLayout(endpoint_grid)

        fingerprint_label = QLabel("Host fingerprint · SHA256")
        fingerprint_label.setObjectName("formLabel")
        trust_layout.addWidget(fingerprint_label)
        fingerprint_row = QHBoxLayout()
        fingerprint_row.setSpacing(8)
        self.trust_fingerprint = QLineEdit()
        self.trust_fingerprint.setObjectName("gatewayTrustFingerprint")
        self.trust_fingerprint.setPlaceholderText("SHA256:... lấy trực tiếp từ Gateway")
        self.trust_fingerprint.setMinimumHeight(32)
        fingerprint_row.addWidget(self.trust_fingerprint, 1)
        self.trust_host_button = QPushButton("Verify & Lưu")
        self.trust_host_button.setObjectName("gatewayTrustHostButton")
        self.trust_host_button.setMinimumHeight(34)
        self.trust_host_button.clicked.connect(self.trust_remote_host)
        fingerprint_row.addWidget(self.trust_host_button)
        trust_layout.addLayout(fingerprint_row)
        self.client_profile_status = QLabel("Profile: chưa cấu hình")
        self.client_profile_status.setObjectName("clientProfileStatus")
        self.client_profile_status.setWordWrap(True)
        trust_layout.addWidget(self.client_profile_status)
        layout.addWidget(trust_group)

        layout.addWidget(self._step_header(
            3, "Kiểm tra kết nối thật",
            "Sau khi Gateway đã authorize public key, chạy kiểm tra SSH strict trước khi Debug/Live Monitor.",
        ))
        connection_group = QGroupBox("SSH connection check")
        connection_layout = QHBoxLayout(connection_group)
        self.client_connection_status = QLabel("Chưa kiểm tra kết nối SSH")
        self.client_connection_status.setObjectName("clientConnectionStatus")
        self.client_connection_status.setWordWrap(True)
        self.client_connect_button = QPushButton("Kiểm tra SSH")
        self.client_connect_button.setMinimumHeight(34)
        self.client_connect_button.setObjectName("gatewayClientConnectButton")
        self.client_connect_button.clicked.connect(self.check_client_connection)
        connection_layout.addWidget(self.client_connection_status, 1)
        connection_layout.addWidget(self.client_connect_button)
        layout.addWidget(connection_group)

        self.client_help_details = CollapsibleCard(
            "Chi tiết",
            "Cách profile được dùng cho Debug",
            expanded=False,
        )
        client_note = QLabel(
            "Sau khi kết nối PASS, Theo dõi / Debug tự dùng Gateway đã lưu; không cần nhập lại host/user."
        )
        client_note.setObjectName("infoNote")
        client_note.setWordWrap(True)
        self.client_help_details.content_layout.addWidget(client_note)
        layout.addWidget(self.client_help_details)
        layout.addStretch(1)
        return self._scroll_page(content)

    def _select_role(self, index: int) -> None:
        self.role_stack.setCurrentIndex(index)
        self.gateway_role_button.setChecked(index == 0)
        self.client_role_button.setChecked(index == 1)
        self._update_next_action()

    def _load_saved_profile(self) -> None:
        try:
            profile = self.profile_loader()
        except Exception as error:
            self._remote_profile = None
            self.client_profile_status.setText("Profile lỗi/không đọc được · %s" % error)
            self.client_profile_status.setProperty("state", "error")
            return
        self._remote_profile = profile
        if profile is None:
            self.client_profile_status.setText("Profile: chưa cấu hình")
            self.client_profile_status.setProperty("state", "idle")
            return
        self.trust_host.setText(profile.host)
        self.trust_user.setText(profile.user)
        self.trust_port.setValue(profile.port)
        self.client_profile_status.setText(
            "Saved profile READY · %s@%s:%d · không chứa password/private key" %
            (profile.user, profile.host, profile.port)
        )
        self.client_profile_status.setProperty("state", "ready")
        self.client_profile_status.style().unpolish(self.client_profile_status)
        self.client_profile_status.style().polish(self.client_profile_status)

    def _update_next_action(self) -> None:
        if not hasattr(self, "next_action"):
            return
        if self.role_stack.currentIndex() == 0:
            if self._report is None:
                text = "Bước tiếp theo · Gateway: bấm “Kiểm tra lại” để đọc trạng thái SSH và debug-port safety."
                state = "info"
            elif not self._report.ready:
                text = "Bước tiếp theo · Gateway: bấm “Chuẩn bị máy Gateway”, xem plan và xác nhận thay đổi hệ điều hành nếu cần."
                state = "warning"
            elif self._local_host_key is None:
                text = "Bước tiếp theo · Gateway READY: đọc/copy fingerprint rồi gửi cùng IP/username cho máy Client."
                state = "success"
            else:
                text = "Bước tiếp theo · Gateway: chờ Client gửi public key, sau đó chọn “Authorize Client Public Key…”."
                state = "success"
        else:
            if self._identity is None or not self._identity.ready:
                text = "Bước tiếp theo · Client: tạo/reuse B300 Client Key. Private key luôn ở lại máy này."
                state = "info"
            elif self._client_network_problem is not None:
                text = (
                    "Client chưa tới được Gateway %s. Trên Gateway bấm “Chuẩn bị Gateway”; "
                    "nếu vẫn lỗi, kiểm tra hai máy không ở mạng Guest/AP isolation."
                ) % self._client_network_problem.endpoint
                state = "warning"
            elif self._remote_profile is None:
                text = "Bước tiếp theo · Client: nhập Host/User/Fingerprint từ Gateway rồi “Verify Gateway & Lưu Profile”."
                state = "info"
            elif not self._connectivity_ready:
                text = "Bước tiếp theo · Client: copy Public Key sang Gateway để authorize, rồi bấm “Kiểm tra SSH Connection”."
                state = "warning"
            else:
                text = "Client READY ✓ · Có thể mở Theo dõi / Debug; endpoint sẽ được lấy tự động từ saved profile."
                state = "success"
        self.next_action.setText(text)
        self.next_action.setProperty("state", state)
        self.next_action.style().unpolish(self.next_action)
        self.next_action.style().polish(self.next_action)

    def _trust_and_save_profile(
            self, host: str, user: str, port: int, expected_fingerprint: str,
    ):
        profile = RemoteGatewayProfile(host, user, port).validate()
        trust_result = self._scan_verify_trust_host(profile.host, profile.port, expected_fingerprint)
        path = self.profile_saver(profile)
        return trust_result, profile, path

    def check_client_connection(self) -> None:
        if self._remote_profile is None:
            QMessageBox.warning(
                self, "Chưa có Gateway profile",
                "Hãy hoàn tất bước Verify Gateway & Lưu Profile trước khi kiểm tra kết nối SSH.",
            )
            return
        self._connectivity_ready = False
        self._start(
            lambda: self.connectivity_checker(self._remote_profile),
            self._client_connection_checked,
            "Checking managed SSH connection…",
        )

    def _client_connection_checked(self, result: RemoteConnectivityResult) -> None:
        self._connectivity_ready = bool(result.ready)
        if result.ready:
            self.client_connection_status.setText("SSH READY ✓ · %s" % result.gateway)
            self.client_connection_status.setProperty("state", "ready")
            self.log.emit("Gateway Client connectivity PASS: %s" % result.gateway)
        else:
            self.client_connection_status.setText(
                "SSH chưa sẵn sàng · %s · %s" % (result.reason_code, result.message)
            )
            self.client_connection_status.setProperty("state", "error")
            self.log.emit("Gateway Client connectivity BLOCKED: %s" % result.message)
        self.client_connection_status.style().unpolish(self.client_connection_status)
        self.client_connection_status.style().polish(self.client_connection_status)
        self._update_next_action()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.progress.setVisible(bool(busy))
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(1 if self._report and self._report.ready else 0)
        self.progress.setFormat(text or ("READY" if self._report and self._report.ready else "Idle"))
        for button in (
            self.refresh_button, self.prepare_button, self.selftest_button,
            self.identity_prepare_button, self.authorize_key_button,
            self.show_host_key_button, self.trust_host_button, self.client_connect_button,
            self.gateway_role_button, self.client_role_button,
        ):
            button.setEnabled(not busy)
        self.copy_button.setEnabled(not busy and self._report is not None)
        self.identity_copy_button.setEnabled(not busy and self._identity is not None and self._identity.ready)
        self.copy_host_fingerprint_button.setEnabled(
            not busy and self._local_host_key is not None and
            self.host_key_status.property("state") == "ready"
        )
        self.operation_state_changed.emit()

    def _start(self, operation, completed, busy_text: str, failed=None) -> None:
        if self._worker is not None:
            return
        self._set_busy(True, busy_text)
        worker = FunctionWorker(lambda _log, _phase, _cancel: operation(), self)
        worker.completed.connect(completed)
        worker.failed.connect(failed or self._failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _worker_finished(self) -> None:
        worker = self.sender()
        if worker is not None:
            worker.wait()
        if worker is self._worker:
            self._worker = None
        if worker is not None:
            # Match DebugTab lifecycle: keep finished QThread wrappers alive for the
            # owning tab lifetime instead of mixing deleteLater with parent teardown.
            self._retired_workers.append(worker)
        self._set_busy(False)
        self._pending_host_key_port = None
        self._update_next_action()

    def _failed(self, failure) -> None:
        message = getattr(failure, "message", str(failure))
        if self.role_stack.currentIndex() == 0:
            self.status.setText("Gateway operation FAILED · %s" % message)
            self.status.setProperty("state", "error")
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
        else:
            if message.startswith("SSH_TCP_UNREACHABLE:"):
                problem = self._client_network_problem
                endpoint = problem.endpoint if problem is not None else "Gateway SSH"
                display = (
                    "Không thể tới %s. Trên Gateway bấm “Chuẩn bị Gateway”; "
                    "nếu Gateway đã READY, kiểm tra firewall Wi-Fi hoặc Guest/AP isolation."
                ) % endpoint
            else:
                display = "Client operation FAILED · %s" % message
            self.client_connection_status.setText(display)
            self.client_connection_status.setProperty("state", "error")
            self.client_connection_status.style().unpolish(self.client_connection_status)
            self.client_connection_status.style().polish(self.client_connection_status)
        self.log.emit("Gateway Setup failed: %s" % message)
        self._update_next_action()

    def refresh_host(self) -> None:
        port = self._gateway_ssh_port
        self._start(lambda: self.inspector(ssh_port=port), self._host_refreshed, "Checking host…")

    def _host_refreshed(self, report: GatewayHostReport) -> None:
        self._render(report)
        self.log.emit("Gateway host inspection: %s" % report.conclusion)

    def _render(self, report: GatewayHostReport) -> None:
        previous_port = self._gateway_ssh_port
        self._gateway_ssh_port = int(report.ssh_port)
        if previous_port != self._gateway_ssh_port and self._local_host_key is not None:
            self._local_host_key = None
            self._set_host_key_state("idle", "Fingerprint: chưa đọc")
            self.copy_host_fingerprint_button.setEnabled(False)
        self._report = report
        if report.ready:
            self.status.setText("GATEWAY SSH READY ✓ · %s · TCP/%d" % (report.platform.upper(), report.ssh_port))
            self.status.setProperty("state", "ready")
        else:
            self.status.setText("GATEWAY SETUP REQUIRED · %s" % report.platform.upper())
            self.status.setProperty("state", "warning")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.check_table.setRowCount(len(report.checks))
        for row, check in enumerate(report.checks):
            self.check_table.setItem(row, 0, QTableWidgetItem(check.name))
            self.check_table.setItem(row, 1, QTableWidgetItem(check.status))
            self.check_table.setItem(row, 2, QTableWidgetItem(check.message))
        self.check_table.resizeColumnsToContents()
        self.client_config.setPlainText(client_connection_text(report))
        self.copy_button.setEnabled(True)
        try:
            plan = build_gateway_prepare_plan(report)
        except ValueError:
            self.prepare_button.setEnabled(False)
        else:
            self.prepare_button.setText("Gateway READY · Kiểm tra lại" if not plan.changes_required else "Chuẩn bị Gateway")
        self._update_next_action()

    def prepare_host(self) -> None:
        if self._report is None:
            self.refresh_host()
            return
        try:
            plan = build_gateway_prepare_plan(self._report)
        except ValueError as error:
            QMessageBox.critical(self, "Gateway Setup blocked", str(error))
            return
        if "manual_fix_debug_exposure" in plan.actions:
            QMessageBox.critical(
                self, "Unsafe debug exposure",
                "TCP 3333/4444/6666 đang có listener ngoài loopback. Tool sẽ không tự sửa hoặc tiếp tục. "
                "Hãy đóng/reconfigure process đó trước."
            )
            return
        if "manual_fix_network_profile" in plan.actions:
            QMessageBox.critical(
                self, "Network profile needs attention",
                "B300 thấy nhiều mạng/VPN đang hoạt động hoặc không xác định được mạng LAN cần dùng. "
                "Tool sẽ không tự đổi profile, không mở firewall và không yêu cầu UAC. "
                "Hãy ngắt mạng/VPN phụ hoặc đặt đúng mạng LAN thành Private, rồi bấm Kiểm tra lại.",
            )
            return
        if not plan.changes_required:
            self.refresh_host()
            return
        details = "\n".join("• %s" % _ACTION_TEXT.get(action, action) for action in plan.actions)
        answer = QMessageBox.question(
            self, "Prepare B300 Gateway",
            "Tool sẽ thực hiện các thay đổi hệ điều hành sau:\n\n%s\n\n"
            "Windows sẽ hiện UAC; Ubuntu sẽ yêu cầu quyền administrator.\n"
            "Tool không sửa sshd_config/password và không mở 3333/4444/6666.\n\nTiếp tục?" % details,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        port = self._gateway_ssh_port
        self._start(lambda: self.preparer(ssh_port=port), self._prepare_finished, "Preparing Gateway…")

    def _prepare_finished(self, result: GatewayPrepareResult) -> None:
        self._render(result.after)
        if result.succeeded:
            self.log.emit("Gateway Prepare PASS; changed=%s" % result.changed)
            QMessageBox.information(self, "Gateway Ready", "OpenSSH Gateway đã sẵn sàng. Có thể dùng máy khác kết nối bằng GUI Client qua SSH.")
        else:
            self.log.emit("Gateway Prepare BLOCKED")
            QMessageBox.warning(self, "Gateway not ready", "Gateway vẫn chưa đạt readiness. Xem bảng check và log để xử lý nguyên nhân.")

    def _render_identity(self, report: SshIdentityReport) -> None:
        self._identity = report
        if report.ready:
            self.identity_status.setText(
                "Client Key READY ✓ · %s · private key chỉ nằm trên máy này" % report.fingerprint
            )
            self.identity_copy_button.setEnabled(True)
            self.identity_prepare_button.setText("Key READY · Kiểm tra lại")
        else:
            self.identity_status.setText(
                "Client Key chưa sẵn sàng · tạo/reuse trước khi thiết lập kết nối SSH"
            )
            self.identity_copy_button.setEnabled(False)
            self.identity_prepare_button.setText("Tạo / kiểm tra Key")
        self._update_next_action()

    def prepare_client_identity(self) -> None:
        try:
            prereq = self.client_prereq_inspector()
        except Exception as error:
            QMessageBox.critical(self, "OpenSSH Client check failed", str(error))
            return
        if prereq.ready:
            self._start(self.identity_ensurer, self._identity_prepared, "Preparing Client SSH key…")
            return
        answer = QMessageBox.question(
            self, "Prepare OpenSSH Client",
            "OpenSSH Client/ssh-keygen is not ready on this PC. Install the missing OS component now?\n\n"
            "Windows uses UAC. Ubuntu uses root/pkexec. No firewall rule is added for Client setup.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(self._prepare_client_identity_with_prerequisites, self._identity_prepared, "Preparing OpenSSH Client + key…")

    def _prepare_client_identity_with_prerequisites(self) -> SshIdentityReport:
        prepared = self.client_prereq_preparer()
        if not prepared.succeeded or not prepared.after.ready:
            raise RuntimeError("OpenSSH Client setup did not reach READY state.")
        return self.identity_ensurer()

    def _identity_prepared(self, report: SshIdentityReport) -> None:
        self._connectivity_ready = False
        self._render_identity(report)
        self.log.emit("B300 Client SSH key READY; fingerprint=%s; private key content not exported." % report.fingerprint)
        QMessageBox.information(
            self, "Client SSH Key Ready",
            "B300 ed25519 Client key is ready. Copy ONLY the public key and authorize it on the Gateway."
        )

    def copy_public_key(self) -> None:
        if self._identity is None or not self._identity.ready or not self._identity.public_key_text:
            return
        QApplication.clipboard().setText(self._identity.public_key_text)
        self.log.emit("B300 Client public key copied; private key remains local and was not read/exported.")

    def authorize_client_key(self) -> None:
        value, accepted = QInputDialog.getMultiLineText(
            self, "Authorize Client Public Key",
            "Paste ONE ssh-ed25519 public key from the Client. Never paste a private key:", ""
        )
        if not accepted:
            return
        try:
            public_key = validate_public_key(value)
        except ValueError as error:
            QMessageBox.critical(self, "Invalid public key", str(error))
            return
        answer = QMessageBox.question(
            self, "Authorize Client Key",
            "Append this validated public key to the correct authorized_keys target?\n\n"
            "Only public-key material is written. Existing matching keys are left unchanged.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(
            lambda: self.key_authorizer(public_key), self._key_authorized,
            "Authorizing Client public key…",
        )

    def _key_authorized(self, result: AuthorizedKeyResult) -> None:
        self.log.emit(
            "Gateway Client public key installed for sshd; fingerprint=%s changed=%s target=%s" %
            (result.fingerprint, result.changed, result.target)
        )
        QMessageBox.information(
            self, "Client Key Authorized",
            "Public key installed for sshd%s.\nFingerprint: %s\nTarget: %s\n\n"
            "Run the Client SSH connection check to confirm login." %
            (" and Windows permissions repaired" if result.changed else "", result.fingerprint, result.target),
        )

    def show_local_host_key(self) -> None:
        if self._worker is not None:
            return
        port = self._gateway_ssh_port
        self._pending_host_key_port = port
        self._local_host_key = None
        self._set_host_key_state("busy", "Đang đọc fingerprint...")
        self.copy_host_fingerprint_button.setEnabled(False)
        self._start(
            lambda: self.host_key_reader(port=port), self._local_host_key_loaded,
            "Reading Gateway host fingerprint…", failed=self._local_host_key_failed,
        )

    def _set_host_key_state(self, state: str, text: str) -> None:
        self.host_key_status.setText(text)
        self.host_key_status.setProperty("state", state)
        self.host_key_status.style().unpolish(self.host_key_status)
        self.host_key_status.style().polish(self.host_key_status)

    def _local_host_key_loaded(self, host_key: GatewayHostKey) -> None:
        expected_port = self._pending_host_key_port
        if (
                expected_port is None or host_key.port != expected_port or
                self._gateway_ssh_port != expected_port
        ):
            self._local_host_key_failed(RuntimeError(
                "Gateway SSH port changed while reading the host fingerprint."
            ))
            return
        self._local_host_key = host_key
        self._set_host_key_state(
            "ready", "SSH host key\nED25519\n%s" % host_key.fingerprint,
        )
        self.log.emit("Gateway SSH host fingerprint loaded: %s; private host key was not read/exported." % host_key.fingerprint)
        self._update_next_action()

    def _local_host_key_failed(self, failure) -> None:
        self._local_host_key = None
        message = getattr(failure, "message", str(failure))
        port = self._pending_host_key_port or self._gateway_ssh_port
        security_markers = ("multiple different", "malformed", "untrusted", "conflict", "ambigu")
        if any(marker in message.lower() for marker in security_markers):
            summary = "Dữ liệu public host key từ localhost:%d không hợp lệ hoặc mâu thuẫn." % port
        elif "port changed" in message.lower():
            summary = "Cấu hình SSH port đã thay đổi; hãy đọc lại fingerprint."
        else:
            summary = "SSH Server chưa phản hồi trên localhost:%d hoặc không tìm thấy public host key." % port
        self._set_host_key_state("error", "Không đọc được fingerprint\n%s" % summary)
        self.copy_host_fingerprint_button.setEnabled(False)
        self.log.emit("Gateway SSH host fingerprint read failed: %s" % message)
        self._update_next_action()

    def copy_host_fingerprint(self) -> None:
        if self._local_host_key is None or self.host_key_status.property("state") != "ready":
            return
        QApplication.clipboard().setText(self._local_host_key.fingerprint)
        self.log.emit("Gateway SSH host-key fingerprint copied.")

    def trust_remote_host(self) -> None:
        host = self.trust_host.text().strip()
        user = self.trust_user.text().strip()
        fingerprint = self.trust_fingerprint.text().strip()
        port = self.trust_port.value()
        if not host or not user or not fingerprint:
            QMessageBox.warning(
                self, "Thiếu thông tin Gateway",
                "Nhập Host/IP, SSH username và fingerprint SHA256 hiển thị trực tiếp trên máy Gateway.",
            )
            return
        self._start(
            lambda: self._trust_and_save_profile(host, user, port, fingerprint),
            self._remote_host_trusted, "Verifying Gateway + saving profile…",
        )

    def _scan_verify_trust_host(self, host: str, port: int, expected_fingerprint: str) -> HostTrustResult:
        probe = self.endpoint_prober(host, port)
        if not probe.ready:
            self._client_network_problem = probe
            raise RuntimeError("%s: %s" % (probe.reason_code, probe.message))
        self._client_network_problem = None
        scanned = self.host_key_scanner(host, port)
        if scanned.fingerprint != expected_fingerprint:
            raise RuntimeError(
                "HOST_KEY_FINGERPRINT_MISMATCH: expected %s but scanned %s. No trust record was written." %
                (expected_fingerprint, scanned.fingerprint)
            )
        return self.host_truster(scanned)

    def _remote_host_trusted(self, payload) -> None:
        result, profile, profile_path = payload
        self._remote_profile = profile
        self._connectivity_ready = False
        self.client_profile_status.setText(
            "Profile READY ✓ · %s@%s:%d · strict host trust verified" %
            (profile.user, profile.host, profile.port)
        )
        self.client_profile_status.setProperty("state", "ready")
        self.client_profile_status.style().unpolish(self.client_profile_status)
        self.client_profile_status.style().polish(self.client_profile_status)
        self.log.emit(
            "Remote Gateway verified + profile saved; fingerprint=%s changed=%s profile=%s" %
            (result.fingerprint, result.changed, profile_path)
        )
        self._update_next_action()
        QMessageBox.information(
            self, "Gateway đã xác minh",
            "Strict host trust + saved profile đã sẵn sàng.\n\n"
            "Bước tiếp theo: authorize Public Key trên máy Gateway, sau đó chạy Kiểm tra SSH Connection.",
        )

    def run_selftest(self) -> None:
        port = self._gateway_ssh_port

        def operation():
            host = self.inspector(ssh_port=port)
            full = self.full_inspector(ssh_port=port, gdb_port=3333, tcl_port=6666)
            return host, full
        self._start(operation, self._selftest_finished, "Running self-test…")

    def _selftest_finished(self, result) -> None:
        host, full = result
        self._render(host)
        summary = "Host SSH=%s | Gateway doctor=%s" % (host.conclusion, full.conclusion)
        self.log.emit("Gateway Self-Test: %s" % summary)
        if host.ready and full.ready:
            QMessageBox.information(self, "Gateway Self-Test PASS", summary + "\nOpenOCD/GDB/TCL remain loopback-only; remote access uses SSH.")
        else:
            QMessageBox.warning(self, "Gateway Self-Test BLOCKED", summary + "\nReview failed checks before remote Client testing.")

    def copy_client_configuration(self) -> None:
        if self._report is None:
            return
        QApplication.clipboard().setText(client_connection_text(self._report))
        self.log.emit("Gateway Client configuration copied to clipboard.")
