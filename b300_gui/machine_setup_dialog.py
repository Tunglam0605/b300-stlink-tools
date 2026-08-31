"""Fresh-machine setup dialog for B300 ST-Link Tools."""
from __future__ import annotations

import platform
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from b300_core.machine_setup import (
    DriverPackageRequired, MachineSetupReport, STLINK_OFFICIAL_URL,
    inspect_machine_setup, install_linux_udev, install_openssh_client,
    install_windows_stlink_driver,
)
from .workers import FunctionWorker


_STATE_LABELS = {
    "ready": "✓ Sẵn sàng",
    "missing": "Còn thiếu",
    "optional": "Tùy chọn",
}


class MachineSetupDialog(QDialog):
    """One place to prepare a new Windows/Linux workstation."""

    openocd_setup_requested = Signal()
    setup_changed = Signal()

    def __init__(self, openocd_checker: Callable[[], bool], parent=None) -> None:
        super().__init__(parent)
        self._openocd_checker = openocd_checker
        self._report: Optional[MachineSetupReport] = None
        self._driver_package: Optional[Path] = None
        self._worker: Optional[FunctionWorker] = None
        self._checks: dict[str, QCheckBox] = {}
        self.setWindowTitle("Thiết lập máy mới")
        self.setMinimumSize(720, 560)
        self.resize(780, 620)
        self._build_ui()
        self.refresh_status()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("Thiết lập máy mới")
        title.setObjectName("pageContextTitle")
        root.addWidget(title)
        subtitle = QLabel(
            "B300 kiểm tra những thành phần cần thiết và chỉ cài phần còn thiếu. "
            "Máy đã sẵn sàng sẽ không bị thay đổi."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("pageContextSubtitle")
        root.addWidget(subtitle)

        self.summary = QLabel("Đang kiểm tra…")
        self.summary.setObjectName("nextActionBanner")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        scroll.setWidget(self.list_host)
        root.addWidget(scroll, 1)

        package_row = QHBoxLayout()
        self.driver_package_label = QLabel("Gói ST-Link driver: tự động / chưa chọn")
        self.driver_package_label.setWordWrap(True)
        package_row.addWidget(self.driver_package_label, 1)
        self.select_driver_button = QPushButton("Chọn STSW-LINK009…")
        self.select_driver_button.clicked.connect(self.select_driver_package)
        package_row.addWidget(self.select_driver_button)
        self.official_driver_button = QPushButton("Trang ST chính thức")
        self.official_driver_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(STLINK_OFFICIAL_URL))
        )
        package_row.addWidget(self.official_driver_button)
        self.driver_package_widget = QWidget()
        self.driver_package_widget.setLayout(package_row)
        root.addWidget(self.driver_package_widget)

        self.operation_status = QLabel("")
        self.operation_status.setWordWrap(True)
        root.addWidget(self.operation_status)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Kiểm tra lại")
        self.refresh_button.clicked.connect(self.refresh_status)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        self.install_selected_button = QPushButton("Cài các mục đã chọn")
        self.install_selected_button.clicked.connect(self.install_selected)
        buttons.addWidget(self.install_selected_button)
        self.install_all_button = QPushButton("Cài tất cả phần còn thiếu")
        self.install_all_button.setObjectName("primaryButton")
        self.install_all_button.clicked.connect(self.install_all_missing)
        buttons.addWidget(self.install_all_button)
        close_button = QPushButton("Đóng")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

    def _clear_rows(self) -> None:
        self._checks.clear()
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _row(self, component) -> QWidget:
        row = QFrame()
        row.setObjectName("setupComponentRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        check = QCheckBox()
        check.setEnabled(component.installable and component.state != "ready")
        check.setChecked(component.required and component.state != "ready")
        check.setAccessibleName("Chọn %s" % component.title)
        self._checks[component.component_id] = check
        layout.addWidget(check)
        text = QVBoxLayout()
        title = QLabel(component.title)
        title.setObjectName("setupComponentTitle")
        detail = QLabel(component.detail)
        detail.setWordWrap(True)
        detail.setObjectName("pageContextSubtitle")
        text.addWidget(title)
        text.addWidget(detail)
        layout.addLayout(text, 1)
        state = QLabel(_STATE_LABELS.get(component.state, component.state))
        state.setObjectName("setupStateBadge")
        state.setProperty("state", component.state)
        layout.addWidget(state)
        return row

    def refresh_status(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._set_busy(True, "Đang kiểm tra máy…")

        def operation(_log, _phase, _cancel):
            return inspect_machine_setup(openocd_ready=bool(self._openocd_checker()))

        self._worker = FunctionWorker(operation, self)
        self._worker.completed.connect(self._status_ready)
        self._worker.failed.connect(self._status_failed)
        self._worker.finished.connect(lambda: self._set_busy(False, ""))
        self._worker.start()

    def _status_ready(self, report: MachineSetupReport) -> None:
        self._report = report
        self._clear_rows()
        for component in report.components:
            self.list_layout.insertWidget(self.list_layout.count() - 1, self._row(component))
        if report.required_ready:
            self.summary.setText("✓ Máy đã sẵn sàng cho B300 ST-Link Tools.")
            self.summary.setProperty("state", "ready")
        else:
            missing = ", ".join(item.title for item in report.missing_required)
            self.summary.setText("Còn thiếu: %s. Có thể bấm Cài tất cả phần còn thiếu." % missing)
            self.summary.setProperty("state", "warning")
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)
        self.driver_package_widget.setVisible(report.platform == "windows")
        self.install_all_button.setEnabled(not report.required_ready)

    def _status_failed(self, failure) -> None:
        self.operation_status.setText("Không kiểm tra được máy: %s" % getattr(failure, "message", failure))

    def _set_busy(self, busy: bool, text: str) -> None:
        for button in (
            self.refresh_button, self.select_driver_button, self.official_driver_button,
            self.install_selected_button,
        ):
            button.setEnabled(not busy)
        required_missing = self._report is None or not self._report.required_ready
        self.install_all_button.setEnabled((not busy) and required_missing)
        if text:
            self.operation_status.setText(text)

    def select_driver_package(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, "Chọn STSW-LINK009 chính thức", "", "ST Driver ZIP (*.zip);;Tất cả file (*)"
        )
        if not path:
            directory = QFileDialog.getExistingDirectory(
                self, "Hoặc chọn thư mục STSW-LINK009 đã giải nén"
            )
            path = directory
        if not path:
            return
        self._driver_package = Path(path)
        self.driver_package_label.setText("Gói ST-Link driver: %s" % self._driver_package.name)

    def install_all_missing(self) -> None:
        if self._report is None:
            return
        for component in self._report.components:
            check = self._checks.get(component.component_id)
            if check is not None:
                check.setChecked(component.required and component.state != "ready" and component.installable)
        self.install_selected()

    def install_selected(self) -> None:
        if self._report is None:
            return
        selected = [
            component for component in self._report.components
            if self._checks.get(component.component_id) is not None
            and self._checks[component.component_id].isChecked()
            and component.installable
        ]
        if not selected:
            self.operation_status.setText("Không có thành phần nào được chọn để cài.")
            return
        if QMessageBox.question(
            self, "Xác nhận thiết lập máy",
            "Chỉ những thành phần đang thiếu và được chọn mới được cài. Windows có thể hiện UAC. Tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True, "Đang cài thành phần còn thiếu…")

        def operation(log, _phase, _cancel):
            messages = []
            for component in selected:
                log("SETUP %s" % component.component_id)
                if component.component_id == "stlink_driver":
                    result = install_windows_stlink_driver(self._driver_package)
                elif component.component_id == "linux_udev":
                    result = install_linux_udev()
                elif component.component_id == "openssh_client":
                    result = install_openssh_client(system_name=platform.system())
                elif component.component_id == "openocd":
                    return ("openocd", tuple(messages))
                else:
                    continue
                messages.append(result.message)
            return ("done", tuple(messages))

        self._worker = FunctionWorker(operation, self)
        self._worker.completed.connect(self._install_finished)
        self._worker.failed.connect(self._install_failed)
        self._worker.finished.connect(self._install_worker_finished)
        self._worker.start()

    def _install_worker_finished(self) -> None:
        self._set_busy(False, "")
        QTimer.singleShot(0, self.refresh_status)

    def _install_finished(self, result) -> None:
        marker, messages = result
        if messages:
            self.operation_status.setText("\n".join(messages))
        if marker == "openocd":
            self.openocd_setup_requested.emit()
        self.setup_changed.emit()

    def _install_failed(self, failure) -> None:
        message = getattr(failure, "message", str(failure))
        if "STSW-LINK009" in message:
            self.operation_status.setText(
                "Cần gói STSW-LINK009 chính thức. Bấm Trang ST chính thức, tải gói theo điều khoản ST, "
                "sau đó chọn ZIP/thư mục và bấm cài lại."
            )
        else:
            self.operation_status.setText("Thiết lập chưa hoàn tất: %s" % message)
        self.setup_changed.emit()
