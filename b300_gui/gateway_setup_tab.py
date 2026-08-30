"""Gateway host preparation UI for OpenSSH-based remote debug."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from b300_core.gateway_readiness import inspect_gateway_readiness
from b300_core.gateway_setup import (
    GatewayHostReport, GatewayPrepareResult, build_gateway_prepare_plan,
    client_connection_text, inspect_gateway_host, prepare_gateway_host,
)
from b300_core.ssh_identity import (
    AuthorizedKeyResult, SshClientPrepareResult, SshClientPrerequisiteReport, SshIdentityReport,
    ensure_ssh_identity, inspect_ssh_client_prerequisites, inspect_ssh_identity,
    install_gateway_public_key, prepare_ssh_client_prerequisites, validate_public_key,
)
from .workers import FunctionWorker


_ACTION_TEXT = {
    "install_openssh_server": "Cài OpenSSH Server",
    "enable_ssh_startup": "Bật SSH tự khởi động cùng hệ điều hành",
    "start_ssh_service": "Khởi động dịch vụ SSH",
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
        self._identity: Optional[SshIdentityReport] = None
        self._worker: Optional[FunctionWorker] = None
        self._retired_workers = []
        self._report: Optional[GatewayHostReport] = None
        self._build_ui()
        self._render_identity(self.identity_inspector())
        if auto_refresh:
            self.refresh_host()

    @property
    def has_active_operation(self) -> bool:
        return self._worker is not None

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        title = QLabel("Remote Debug Gateway Setup")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #0F172A;")
        root.addWidget(title)
        subtitle = QLabel(
            "Biến máy này thành B300 Gateway hoàn chỉnh. Tool chỉ quản lý OpenSSH/SSH TCP 22; "
            "OpenOCD 3333/4444/6666 luôn phải giữ loopback-only."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #475569;")
        root.addWidget(subtitle)

        self.status = QLabel("Đang kiểm tra Gateway host…")
        self.status.setObjectName("gatewaySetupStatus")
        self.status.setStyleSheet("padding: 8px; font-weight: 700; background: #F1F5F9; border-radius: 6px;")
        root.addWidget(self.status)

        host_group = QGroupBox("Host readiness")
        host_layout = QVBoxLayout(host_group)
        self.check_table = QTableWidget(0, 3)
        self.check_table.setObjectName("gatewaySetupCheckTable")
        self.check_table.setHorizontalHeaderLabels(["Check", "State", "Detail"])
        self.check_table.verticalHeader().setVisible(False)
        self.check_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.check_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.check_table.horizontalHeader().setStretchLastSection(True)
        host_layout.addWidget(self.check_table)
        root.addWidget(host_group, 1)

        config_group = QGroupBox("Client connection")
        config_layout = QVBoxLayout(config_group)
        self.client_config = QPlainTextEdit()
        self.client_config.setObjectName("gatewayClientConfiguration")
        self.client_config.setReadOnly(True)
        self.client_config.setMaximumHeight(125)
        config_layout.addWidget(self.client_config)
        root.addWidget(config_group)

        identity_group = QGroupBox("SSH key bootstrap")
        identity_layout = QVBoxLayout(identity_group)
        self.identity_status = QLabel("B300 Client key: not checked")
        self.identity_status.setObjectName("gatewayIdentityStatus")
        identity_layout.addWidget(self.identity_status)
        identity_actions = QHBoxLayout()
        self.identity_prepare_button = QPushButton("Generate / Reuse Client Key")
        self.identity_prepare_button.setObjectName("gatewayIdentityPrepareButton")
        self.identity_prepare_button.clicked.connect(self.prepare_client_identity)
        self.identity_copy_button = QPushButton("Copy Public Key")
        self.identity_copy_button.setObjectName("gatewayIdentityCopyButton")
        self.identity_copy_button.clicked.connect(self.copy_public_key)
        self.authorize_key_button = QPushButton("Authorize Client Public Key")
        self.authorize_key_button.setObjectName("gatewayAuthorizeKeyButton")
        self.authorize_key_button.clicked.connect(self.authorize_client_key)
        identity_actions.addWidget(self.identity_prepare_button)
        identity_actions.addWidget(self.identity_copy_button)
        identity_actions.addWidget(self.authorize_key_button)
        identity_actions.addStretch(1)
        identity_layout.addLayout(identity_actions)
        key_note = QLabel(
            "Client creates/reuses one B300 ed25519 key. Only the public key is copied to the Gateway; "
            "the private key never leaves the Client or enters logs."
        )
        key_note.setWordWrap(True)
        key_note.setStyleSheet("color: #64748B; font-size: 11px;")
        identity_layout.addWidget(key_note)
        root.addWidget(identity_group)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("Idle")
        root.addWidget(self.progress)

        actions = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("gatewayRefreshButton")
        self.refresh_button.clicked.connect(self.refresh_host)
        self.prepare_button = QPushButton("Prepare This PC as Gateway")
        self.prepare_button.setObjectName("gatewayPrepareButton")
        self.prepare_button.clicked.connect(self.prepare_host)
        self.selftest_button = QPushButton("Run Gateway Self-Test")
        self.selftest_button.setObjectName("gatewaySelfTestButton")
        self.selftest_button.clicked.connect(self.run_selftest)
        self.copy_button = QPushButton("Copy Client Configuration")
        self.copy_button.setObjectName("gatewayCopyClientButton")
        self.copy_button.clicked.connect(self.copy_client_configuration)
        for button in (self.refresh_button, self.prepare_button, self.selftest_button, self.copy_button):
            actions.addWidget(button)
        actions.addStretch(1)
        root.addLayout(actions)

        safety = QLabel(
            "Safety: Gateway Setup không sửa sshd_config, không đổi password, không tắt firewall và "
            "không tạo rule cho TCP 3333/4444/6666. Nếu SSH đã READY, Prepare là no-op."
        )
        safety.setWordWrap(True)
        safety.setStyleSheet("color: #64748B; font-size: 11px;")
        root.addWidget(safety)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(1 if self._report and self._report.ready else 0)
        self.progress.setFormat(text or ("READY" if self._report and self._report.ready else "Idle"))
        for button in (
            self.refresh_button, self.prepare_button, self.selftest_button,
            self.identity_prepare_button, self.authorize_key_button,
        ):
            button.setEnabled(not busy)
        self.copy_button.setEnabled(not busy and self._report is not None)
        self.identity_copy_button.setEnabled(not busy and self._identity is not None and self._identity.ready)
        self.operation_state_changed.emit()

    def _start(self, operation, completed, busy_text: str) -> None:
        if self._worker is not None:
            return
        self._set_busy(True, busy_text)
        worker = FunctionWorker(lambda _log, _phase, _cancel: operation(), self)
        worker.completed.connect(completed)
        worker.failed.connect(self._failed)
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

    def _failed(self, failure) -> None:
        message = getattr(failure, "message", str(failure))
        self.status.setText("Gateway operation FAILED: %s" % message)
        self.status.setStyleSheet("padding: 8px; font-weight: 700; background: #FEE2E2; color: #991B1B; border-radius: 6px;")
        self.log.emit("Gateway Setup failed: %s" % message)

    def refresh_host(self) -> None:
        self._start(lambda: self.inspector(ssh_port=22), self._host_refreshed, "Checking host…")

    def _host_refreshed(self, report: GatewayHostReport) -> None:
        self._render(report)
        self.log.emit("Gateway host inspection: %s" % report.conclusion)

    def _render(self, report: GatewayHostReport) -> None:
        self._report = report
        if report.ready:
            self.status.setText("GATEWAY SSH READY ✓ · %s · TCP/%d" % (report.platform.upper(), report.ssh_port))
            self.status.setStyleSheet("padding: 8px; font-weight: 700; background: #DCFCE7; color: #166534; border-radius: 6px;")
        else:
            self.status.setText("GATEWAY SETUP REQUIRED · %s" % report.platform.upper())
            self.status.setStyleSheet("padding: 8px; font-weight: 700; background: #FEF3C7; color: #92400E; border-radius: 6px;")
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
            self.prepare_button.setText("Gateway Ready · Verify" if not plan.changes_required else "Prepare This PC as Gateway")

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
        self._start(lambda: self.preparer(ssh_port=22), self._prepare_finished, "Preparing Gateway…")

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
                "B300 Client key READY · %s · private key stays local" % report.fingerprint
            )
            self.identity_copy_button.setEnabled(True)
            self.identity_prepare_button.setText("Client Key Ready · Verify")
        else:
            self.identity_status.setText(
                "B300 Client key NOT READY · Generate/Re-use before passwordless Client connection"
            )
            self.identity_copy_button.setEnabled(False)
            self.identity_prepare_button.setText("Generate / Reuse Client Key")

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
            "Gateway Client public key authorized; fingerprint=%s changed=%s target=%s" %
            (result.fingerprint, result.changed, result.target)
        )
        QMessageBox.information(
            self, "Client Key Authorized",
            "Public key authorized%s.\nFingerprint: %s\nTarget: %s" %
            ("" if result.changed else " (already present)", result.fingerprint, result.target),
        )

    def run_selftest(self) -> None:
        def operation():
            host = self.inspector(ssh_port=22)
            full = self.full_inspector(ssh_port=22, gdb_port=3333, tcl_port=6666)
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
