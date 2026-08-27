"""Main B300 provisioning window."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSettings, QThread, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from b300_core.models import FlashPlan, ProbeRef
from b300_core.policy import SECTORS
from b300_core.probe import list_probes
from b300_core.service import B300Service, FlashResult

from .viewmodels import FlashViewState, confirmation_text
from .workers import FunctionWorker
from .memory_tab import MemoryTab


APP_STYLE = """
QMainWindow, QWidget { background: #F8FAFC; color: #1E293B; font-size: 13px; }
QGroupBox { background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 8px;
            margin-top: 14px; padding: 14px 12px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QLineEdit, QComboBox, QPlainTextEdit, QTableWidget { background: #FFFFFF;
            border: 1px solid #94A3B8; border-radius: 5px; padding: 6px; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTableWidget:focus {
            border: 2px solid #2563EB; }
QPushButton { min-height: 34px; padding: 2px 14px; border-radius: 6px;
              border: 1px solid #64748B; background: #FFFFFF; font-weight: 600; }
QPushButton:hover { background: #EFF6FF; border-color: #2563EB; }
QPushButton:focus { border: 2px solid #2563EB; }
QPushButton:disabled { color: #94A3B8; background: #E2E8F0; border-color: #CBD5E1; }
QPushButton#flashButton { background: #C2410C; color: #FFFFFF; border-color: #9A3412; }
QPushButton#flashButton:hover { background: #9A3412; }
QLabel#statusBanner { border-radius: 6px; padding: 9px 12px; background: #E2E8F0;
                      color: #334155; font-weight: 600; }
QLabel#statusBanner[state="success"] { background: #DCFCE7; color: #166534; }
QLabel#statusBanner[state="error"] { background: #FEE2E2; color: #991B1B; }
QLabel#statusBanner[state="busy"] { background: #FFEDD5; color: #9A3412; }
QTabWidget::pane { border: 1px solid #CBD5E1; background: #F8FAFC; }
QTabBar::tab { min-width: 120px; padding: 9px 14px; }
QTabBar::tab:selected { background: #FFFFFF; border-bottom: 3px solid #2563EB; }
"""


class MainWindow(QMainWindow):
    def __init__(self, service: Optional[B300Service] = None,
                 probe_loader: Callable = list_probes) -> None:
        super().__init__()
        self.service = service or B300Service()
        self.probe_loader = probe_loader
        self.settings = QSettings("TungLamAutomation", "B300-STLink")
        self.image_info = None
        self.flash_plan: Optional[FlashPlan] = None
        self.target_ready = False
        self.busy = False
        self._threads = []

        self.setWindowTitle("B300 ST-Link Provisioning")
        self.setMinimumSize(900, 650)
        self.resize(1120, 780)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self.refresh_probes()
        self._restore_last_image()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("B300 ST-Link Provisioning")
        title.setFont(QFont(title.font().family(), 18, QFont.Weight.DemiBold))
        subtitle = QLabel("Nạp Application STM32F407 an toàn · giữ nguyên Bootloader và đường OTA")
        subtitle.setStyleSheet("color: #475569;")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.status_banner = QLabel("Sẵn sàng kiểm tra ST-Link")
        self.status_banner.setObjectName("statusBanner")
        self.status_banner.setAccessibleName("Trạng thái phiên nạp")
        root.addWidget(self.status_banner)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_flash_tab(), "Nạp firmware")
        self.memory_tab = MemoryTab(self.service, self._selected_probe)
        self.tabs.addTab(self.memory_tab, "Memory / Metadata")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def _build_flash_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(12, 12, 12, 12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)

        device_group = QGroupBox("1. ST-Link probe")
        device_row = QHBoxLayout(device_group)
        self.probe_combo = QComboBox()
        self.probe_combo.setObjectName("probeSelector")
        self.probe_combo.setAccessibleName("Chọn ST-Link probe")
        self.probe_combo.currentIndexChanged.connect(self._probe_changed)
        self.refresh_button = QPushButton("Làm mới")
        self.refresh_button.clicked.connect(self.refresh_probes)
        device_row.addWidget(self.probe_combo, 1)
        device_row.addWidget(self.refresh_button)
        left_layout.addWidget(device_group)

        firmware_group = QGroupBox("2. Application HEX")
        firmware_layout = QGridLayout(firmware_group)
        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        self.file_path.setAccessibleName("Đường dẫn Application HEX")
        self.choose_button = QPushButton("Chọn file…")
        self.choose_button.clicked.connect(self.choose_file)
        firmware_layout.addWidget(self.file_path, 0, 0)
        firmware_layout.addWidget(self.choose_button, 0, 1)
        self.image_summary = QLabel("Chưa chọn firmware")
        self.image_summary.setWordWrap(True)
        self.image_summary.setStyleSheet("color: #475569;")
        firmware_layout.addWidget(self.image_summary, 1, 0, 1, 2)
        left_layout.addWidget(firmware_group)

        plan_group = QGroupBox("3. Flash plan cố định")
        plan_layout = QVBoxLayout(plan_group)
        self.flash_plan_label = QLabel(
            "Erase Sector 3–7 → Program/Verify Application → Provision marker → Reset"
        )
        self.flash_plan_label.setWordWrap(True)
        plan_layout.addWidget(self.flash_plan_label)
        self.plan_table = QTableWidget(5, 3)
        self.plan_table.setHorizontalHeaderLabels(["Sector", "Vai trò", "Thao tác"])
        self.plan_table.verticalHeader().setVisible(False)
        self.plan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.plan_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for row, sector in enumerate(SECTORS[3:]):
            action = "Erase metadata" if sector.index == 3 else "Erase + Program"
            self.plan_table.setItem(row, 0, QTableWidgetItem(str(sector.index)))
            self.plan_table.setItem(row, 1, QTableWidgetItem(sector.role))
            self.plan_table.setItem(row, 2, QTableWidgetItem(action))
        self.plan_table.resizeColumnsToContents()
        plan_layout.addWidget(self.plan_table)
        left_layout.addWidget(plan_group, 1)

        actions = QHBoxLayout()
        self.dry_run_button = QPushButton("Kiểm tra dry-run")
        self.dry_run_button.clicked.connect(self.show_dry_run)
        self.flash_button = QPushButton("Nạp Application")
        self.flash_button.setObjectName("flashButton")
        self.flash_button.clicked.connect(self.confirm_flash)
        actions.addWidget(self.dry_run_button)
        actions.addStretch(1)
        actions.addWidget(self.flash_button)
        left_layout.addLayout(actions)

        log_group = QGroupBox("Log thời gian thực")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setAccessibleName("Log OpenOCD")
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        log_layout.addWidget(self.log_view, 1)
        log_actions = QHBoxLayout()
        self.clear_log_button = QPushButton("Xóa hiển thị")
        self.clear_log_button.clicked.connect(self.log_view.clear)
        self.export_log_button = QPushButton("Xuất log…")
        self.export_log_button.clicked.connect(self.export_log)
        log_actions.addWidget(self.clear_log_button)
        log_actions.addWidget(self.export_log_button)
        log_actions.addStretch(1)
        log_layout.addLayout(log_actions)
        right_layout.addWidget(log_group, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("Chưa chạy")
        right_layout.addWidget(self.progress)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        page_layout.addWidget(splitter)
        self._update_controls()
        return page

    def _restore_last_image(self) -> None:
        last_path = self.settings.value("lastImage", "")
        if last_path and Path(str(last_path)).is_file():
            self.load_image_path(Path(str(last_path)), quiet=True)

    def refresh_probes(self) -> None:
        current = self.probe_combo.currentData() if self.probe_combo.count() else None
        self.probe_combo.blockSignals(True)
        self.probe_combo.clear()
        self.probe_combo.addItem("Auto-select (single ST-Link)", None)
        try:
            probes = self.probe_loader()
            available, executable = self.service.doctor()
        except Exception as error:
            probes = ()
            available, executable = False, ""
            self.append_log("Probe check failed: %s" % error)
        for probe in probes:
            self.probe_combo.addItem("%s · %s" % (probe.name, probe.serial), probe.serial)
        restore_index = self.probe_combo.findData(current)
        self.probe_combo.setCurrentIndex(max(0, restore_index))
        self.probe_combo.blockSignals(False)
        self.target_ready = available
        if available:
            detail = "%d probe tìm thấy" % len(probes) if probes else "OpenOCD sẵn sàng"
            self._set_status("%s · %s" % (detail, executable), "normal")
        else:
            self._set_status("Không tìm thấy OpenOCD; chạy setup/doctor trước", "error")
        self._rebuild_plan()

    def _probe_changed(self) -> None:
        self._rebuild_plan()

    def _selected_probe(self) -> ProbeRef:
        return ProbeRef(self.probe_combo.currentData())

    def choose_file(self) -> None:
        initial = str(Path(self.file_path.text()).parent) if self.file_path.text() else ""
        selected, _ = QFileDialog.getOpenFileName(
            self, "Chọn Application HEX", initial, "Intel HEX (*.hex *.ihx)"
        )
        if selected:
            self.load_image_path(Path(selected))

    def load_image_path(self, path: Path, quiet: bool = False) -> bool:
        try:
            self.image_info = self.service.inspect_image(Path(path))
        except Exception as error:
            self.image_info = None
            self.flash_plan = None
            self.file_path.setText(str(path))
            self.image_summary.setText("Không hợp lệ: %s" % error)
            self.image_summary.setStyleSheet("color: #991B1B; font-weight: 600;")
            self._set_status("Firmware không hợp lệ", "error")
            if not quiet:
                self.append_log("Image validation failed: %s" % error)
            self._update_controls()
            return False
        self.file_path.setText(str(self.image_info.path))
        self.settings.setValue("lastImage", str(self.image_info.path))
        self.image_summary.setStyleSheet("color: #166534; font-weight: 600;")
        self.image_summary.setText(
            "%s bytes · 0x%08X..0x%08X\nSHA-256: %s" % (
                self.image_info.size,
                self.image_info.start_address,
                self.image_info.end_address,
                self.image_info.sha256,
            )
        )
        self._rebuild_plan()
        self._set_status("Firmware hợp lệ; sẵn sàng dry-run", "normal")
        return True

    def _rebuild_plan(self) -> None:
        if self.image_info is None:
            self.flash_plan = None
        else:
            try:
                self.flash_plan = self.service.plan(self.image_info, self._selected_probe())
            except Exception as error:
                self.flash_plan = None
                self.append_log("Flash plan failed: %s" % error)
        self._update_controls()

    def _update_controls(self) -> None:
        state = FlashViewState(self.target_ready, self.flash_plan is not None, self.busy)
        self.flash_button.setEnabled(state.can_flash)
        self.dry_run_button.setEnabled(self.flash_plan is not None and not self.busy)
        self.choose_button.setEnabled(not self.busy)
        self.refresh_button.setEnabled(not self.busy)
        self.probe_combo.setEnabled(not self.busy)

    def show_dry_run(self) -> None:
        if self.flash_plan is None:
            return
        command = self.service.flash_command(self.flash_plan)
        self.append_log("DRY-RUN (không ghi phần cứng)")
        self.append_log(subprocess.list2cmdline(command))
        self._set_status("Dry-run hợp lệ; kiểm tra probe/file trước khi nạp", "normal")

    def confirm_flash(self) -> None:
        if self.flash_plan is None or self.busy:
            return
        answer = QMessageBox.question(
            self,
            "Xác nhận nạp Application",
            confirmation_text(self.flash_plan),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_flash()

    def _start_flash(self) -> None:
        assert self.flash_plan is not None
        plan = self.flash_plan
        self.busy = True
        self.progress.setRange(0, 0)
        self.progress.setFormat("Đang nạp và verify…")
        self._set_status("Đang ghi Sector 3–7; không rút ST-Link hoặc mất nguồn", "busy")
        self._update_controls()
        self._start_worker(
            lambda emit: self.service.flash(plan, event_sink=emit),
            self._flash_finished,
        )

    def _start_worker(self, operation, on_finished) -> None:
        thread = QThread(self)
        worker = FunctionWorker(operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.append_log)
        worker.finished.connect(on_finished)
        worker.finished.connect(thread.quit)
        worker.failed.connect(self._operation_failed)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._threads.remove(thread)
                                if thread in self._threads else None)
        self._threads.append(thread)
        thread.start()

    def _flash_finished(self, result: FlashResult) -> None:
        self.busy = False
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if result.succeeded else 0)
        if result.succeeded:
            verification = result.boot_verification
            self.progress.setFormat("Hoàn tất")
            self._set_status(
                "Nạp thành công · Application PC=%s · BKP đã clear" %
                ("0x%08X" % verification.pc if verification and verification.pc else "N/A"),
                "success",
            )
        elif result.status == "programmed_boot_failed":
            self.progress.setFormat("Boot verify lỗi")
            reason = result.boot_verification.reason if result.boot_verification else "Unknown"
            self._set_status("Đã program nhưng Boot verification thất bại: %s" % reason, "error")
        else:
            self.progress.setFormat("Flash lỗi")
            self._set_status("Nạp/verify thất bại; đã dừng và không tự retry", "error")
        self._update_controls()

    def _operation_failed(self, message: str) -> None:
        self.busy = False
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Lỗi")
        self.append_log(message)
        self._set_status("Thao tác thất bại; xem log và không retry mù", "error")
        self._update_controls()

    def append_log(self, line: str) -> None:
        self.log_view.appendPlainText(str(line))

    def export_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Xuất log", "b300-stlink.log", "Log (*.log *.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.log_view.toPlainText(), encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "Không thể xuất log", str(error))

    def _set_status(self, text: str, state: str) -> None:
        self.status_banner.setText(text)
        self.status_banner.setProperty("state", state)
        self.status_banner.style().unpolish(self.status_banner)
        self.status_banner.style().polish(self.status_banner)
