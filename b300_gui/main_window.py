"""Main B300 provisioning window."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QDialog,
)

from b300_core.models import FlashPlan, ProbeRef
from b300_core.models import TargetInfo
from b300_core.debug_service import DebugService
from b300_core.offline_setup import (
    current_platform_name,
    find_offline_bundle,
    install_offline_bundle,
)
from b300_core import __version__ as CORE_VERSION
from b300_core.policy import SECTORS
from b300_core.probe import list_probes
from b300_core.factory_resource import load_trusted_bootloader
from b300_core.service import B300Service, FactoryResult, FlashResult
from b300_core.updater import UpdateCheckResult, should_auto_check
from b300_core.update_install import launch_install_plan, prepare_install
from b300_core.update_platform import detect_update_platform
from b300_core.build_info import build_commit
from b300_core.release_notes import current_release_notes
from b300_core.versioning import SemVer

from .viewmodels import FlashViewState, confirmation_text
from .workers import FunctionWorker
from .memory_tab import MemoryTab
from .debug_tab import DebugTab
from .branding import asset_path
from .about_dialog import AboutDialog
from .confirm_dialog import ConfirmFlashDialog
from .toast import ToastNotification
from .log_highlighter import format_log_html
from .operation_state import OperationState
from .update_dialog import UpdateDialog
from .update_worker import UpdateCheckWorker, UpdateDownloadWorker
from .whats_new_dialog import WhatsNewDialog
from . import __version__


from .styles import APP_STYLE


class MainWindow(QMainWindow):
    def __init__(self, service: Optional[B300Service] = None,
                 probe_loader: Callable = list_probes,
                 setup_bundle_provider: Optional[Callable[[], Optional[Path]]] = None,
                 setup_installer: Callable[[Path], Path] = install_offline_bundle,
                 update_client=None, automatic_updates: bool = True,
                 update_installer=None, settings=None,
                 debug_service: Optional[DebugService] = None) -> None:
        super().__init__()
        self.service = service or B300Service()
        self.debug_service = debug_service or DebugService(
            session_manager=getattr(self.service, "session_manager", None)
        )
        self.probe_loader = probe_loader
        self.setup_bundle_provider = setup_bundle_provider or self._select_offline_bundle
        self.setup_installer = setup_installer
        self.update_client = update_client
        self.update_installer = update_installer or launch_install_plan
        self.settings = settings or QSettings("TungLamAutomation", "B300-STLink")
        self.image_info = None
        self.flash_plan: Optional[FlashPlan] = None
        self.target_info: Optional[TargetInfo] = None
        self.target_ready = False
        self.openocd_ready = False
        self.busy = False
        self._probe_selection_required = False
        self._probes = ()
        self._threads = []
        self._cancellable_worker = None
        self._update_workers = []
        self.update_dialog = None
        self.about_dialog = None
        self.whats_new_dialog = None
        self._update_result = None
        self._downloaded_update = None
        self._automatic_updates = bool(automatic_updates)
        self._update_poll_timer = QTimer(self)
        self._update_poll_timer.setInterval(15 * 60 * 1000)
        self._update_poll_timer.timeout.connect(self._automatic_update_tick)

        self.setWindowTitle("B300 ST-Link Provisioning")
        self.setWindowIcon(QIcon(str(asset_path("b300-stlink-icon.png"))))
        # Fit laptop/high-DPI desktops. 1366x768 at 125-150% scaling can have
        # less than 650 logical pixels of usable height. Never force the window
        # beyond the current screen work area; scrollable workflows handle the rest.
        self.setMinimumSize(760, 460)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = max(760, min(1120, int(available.width() * 0.94)))
            height = max(460, min(780, int(available.height() * 0.92)))
            self.resize(width, height)
        else:
            self.resize(1120, 780)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self._build_menu()
        self.append_log(
            "B300 ST-Link GUI v%s · Core v%s · OpenOCD profile 0.12.0-7" %
            (__version__, CORE_VERSION)
        )
        self.refresh_probes()
        self._restore_last_image()
        if self._automatic_updates:
            QTimer.singleShot(0, self._show_whats_new_if_needed)
            if self._automatic_updates_enabled():
                self._update_poll_timer.start()
                QTimer.singleShot(2000, self._automatic_update_tick)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        brand_row = QHBoxLayout()
        self.brand_logo = QLabel()
        self.brand_logo.setObjectName("brandLogo")
        self.brand_logo.setAccessibleName("B300 ST-Link Tools")
        self.brand_logo.setToolTip("Nhấn vào logo để mở menu Trợ giúp & Giới thiệu")
        self.brand_logo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.brand_logo.setPixmap(
            QPixmap(str(asset_path("b300-stlink-wordmark.png"))).scaledToHeight(
                63, Qt.TransformationMode.SmoothTransformation
            )
        )
        self.brand_logo.mousePressEvent = self._brand_logo_clicked
        brand_row.addWidget(self.brand_logo)
        brand_row.addStretch(1)

        brand_info = QVBoxLayout()
        brand_info.setSpacing(4)
        brand_info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        top_meta_row = QHBoxLayout()
        top_meta_row.setSpacing(8)
        top_meta_row.addStretch(1)
        eyebrow = QLabel("INDUSTRIAL PROVISIONING SYSTEM · STM32F407")
        eyebrow.setObjectName("eyebrowLabel")
        top_meta_row.addWidget(eyebrow)

        channel = getattr(self.update_client, "channel", "stable")
        channel_name = getattr(channel, "value", str(channel)).capitalize()
        self.update_channel_label = QLabel("v%s · %s" % (__version__, channel_name))
        self.update_channel_label.setObjectName("updateChannelLabel")
        self.update_channel_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_channel_label.setToolTip("Nhấn để kiểm tra cập nhật phiên bản mới")
        self.update_channel_label.mousePressEvent = lambda _event: self.check_for_updates(manual=True)
        top_meta_row.addWidget(self.update_channel_label)
        brand_info.addLayout(top_meta_row)

        subtitle = QLabel("Nạp Application STM32F407 an toàn · Giữ nguyên Bootloader và đường OTA")
        subtitle.setObjectName("subtitleLabel")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignRight)
        brand_info.addWidget(subtitle)
        brand_row.addLayout(brand_info)
        root.addLayout(brand_row)

        self.status_banner = QLabel("Sẵn sàng kiểm tra ST-Link")
        self.status_banner.setObjectName("statusBanner")
        self.status_banner.setAccessibleName("Trạng thái phiên nạp")
        self.status_banner.setVisible(False)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.status_banner, 1)
        self.setup_button = QPushButton("Thiết lập môi trường")
        self.setup_button.setObjectName("setupButton")
        self.setup_button.setAccessibleName("Thiết lập OpenOCD offline")
        self.setup_button.setAccessibleDescription(
            "Cài OpenOCD từ gói B300 offline đã kiểm tra checksum; không cần Internet."
        )
        self.setup_button.setToolTip(
            "Cài OpenOCD 0.12.0-7 từ ZIP/tar.gz offline đầy đủ"
        )
        self.setup_button.clicked.connect(self.setup_environment)
        self.setup_button.setVisible(False)
        status_row.addWidget(self.setup_button)
        root.addLayout(status_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_flash_tab(), "Nạp firmware")
        self.memory_tab = MemoryTab(
            self.service, self._selected_probe, log_sink=self.append_log
        )
        self.memory_tab.operation_state_changed.connect(self._hardware_activity_changed)
        self.tabs.addTab(self.memory_tab, "Memory / Metadata")
        self.debug_tab = DebugTab(
            self.debug_service, self._selected_probe, self,
            settings=self.settings, probe_count=lambda: len(self._probes),
        )
        self.debug_tab.log.connect(self.append_log)
        self.debug_tab.operation_state_changed.connect(self._hardware_activity_changed)
        self.tabs.addTab(self.debug_tab, "Debug")
        self.tabs.currentChanged.connect(self._tab_changed)
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        self.help_menu = self.menuBar().addMenu("Trợ giúp")
        self.about_action = self.help_menu.addAction("Giới thiệu")
        self.about_action.triggered.connect(self.show_about)
        self.help_menu.addSeparator()
        self.check_updates_action = self.help_menu.addAction("Kiểm tra cập nhật")
        self.check_updates_action.triggered.connect(
            lambda: self.check_for_updates(manual=True)
        )
        self.release_notes_action = self.help_menu.addAction("Ghi chú phiên bản")
        self.release_notes_action.triggered.connect(self.show_release_notes)
        self.menuBar().setVisible(False)

    def _brand_logo_clicked(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = self.brand_logo.mapToGlobal(self.brand_logo.rect().bottomLeft())
            self.help_menu.exec(pos)

    def show_about(self) -> None:
        if self.about_dialog is None:
            self.about_dialog = AboutDialog(
                __version__, CORE_VERSION, build_commit(), self
            )
            self.about_dialog.check_updates_requested.connect(
                lambda: self.check_for_updates(manual=True)
            )
        self.about_dialog.show()
        self.about_dialog.raise_()

    def _show_whats_new_if_needed(self) -> None:
        seen_text = self.settings.value("updates/last_seen_version")
        self.settings.setValue("updates/last_seen_version", __version__)
        if seen_text is None:
            return
        try:
            should_show = SemVer.parse(str(seen_text)) < SemVer.parse(__version__)
        except ValueError:
            return
        if not should_show or self.whats_new_dialog is not None:
            return
        try:
            notes = current_release_notes(__version__)
        except (OSError, ValueError) as error:
            self.append_log("What's New unavailable: %s" % error)
            return
        self.whats_new_dialog = WhatsNewDialog(__version__, notes, self)
        self.whats_new_dialog.show()

    def show_release_notes(self) -> None:
        if self._update_result is not None:
            QDesktopServices.openUrl(QUrl(self._update_result.release.release_page))
        else:
            QDesktopServices.openUrl(QUrl(
                "https://github.com/Tunglam0605/b300-stlink-tools/releases/latest"
            ))

    @staticmethod
    def _utc_now_text() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _automatic_updates_enabled(self) -> bool:
        return bool(
            self.update_client is not None and
            self.settings.value("updates/automatic", True, type=bool)
        )

    def _automatic_update_tick(self) -> None:
        """Background update discovery without blocking or spamming the release endpoint."""
        if not self._automatic_updates_enabled():
            self._update_poll_timer.stop()
            return
        if any(isinstance(worker, UpdateCheckWorker) for worker in self._update_workers):
            return
        if should_auto_check(
            self.settings.value("updates/last_check_utc"),
            interval=timedelta(hours=1),
        ):
            self.check_for_updates(manual=False)

    def check_for_updates(self, manual: bool = False) -> None:
        if self.update_client is None:
            if manual:
                QMessageBox.warning(
                    self, "Không thể kiểm tra cập nhật",
                    "Bản chạy này không có cấu hình updater cho nền tảng hiện tại.",
                )
            return
        if any(isinstance(worker, UpdateCheckWorker) for worker in self._update_workers):
            return
        worker = UpdateCheckWorker(self.update_client, __version__, self)
        worker.completed.connect(
            lambda result, selected=manual: self._update_check_finished(result, selected)
        )
        worker.failed.connect(
            lambda error, selected=manual: self._update_check_failed(error, selected)
        )
        worker.finished.connect(self._update_worker_finished)
        self._update_workers.append(worker)
        self.check_updates_action.setEnabled(False)
        channel = getattr(self.update_client, "channel", "stable")
        channel_name = getattr(channel, "value", str(channel)).capitalize()
        self.update_channel_label.setText("v%s · %s · Đang kiểm tra…" % (__version__, channel_name))
        worker.start()

    def _update_check_finished(
            self, result: UpdateCheckResult, manual: bool = False) -> None:
        self.settings.setValue("updates/last_check_utc", self._utc_now_text())
        self._update_result = result
        channel = getattr(self.update_client, "channel", "stable")
        channel_name = getattr(channel, "value", str(channel)).capitalize()
        status = "Có bản mới" if result.available else "Đã mới nhất"
        if result.available:
            self.update_channel_label.setText(
                "⬆ v%s có sẵn · đang dùng v%s" % (result.release.version, __version__)
            )
            self.update_channel_label.setToolTip(
                "Có bản cập nhật mới. Nhấn để xem hoặc kiểm tra lại."
            )
        else:
            self.update_channel_label.setText("v%s · %s · %s" % (__version__, channel_name, status))
            self.update_channel_label.setToolTip("Đang dùng phiên bản mới nhất. Nhấn để kiểm tra lại.")
        if not result.available or result.asset is None:
            if manual:
                QMessageBox.information(
                    self, "Đã cập nhật",
                    "Bạn đang dùng phiên bản mới nhất (%s)." % __version__,
                )
            return
        if self.update_dialog is not None:
            self.update_dialog.close()
        self.update_dialog = UpdateDialog(__version__, result.release, result.asset, self)
        self.update_dialog.download_requested.connect(self._start_update_download)
        self.update_dialog.install_requested.connect(self._install_downloaded_update)
        self.update_dialog.release_requested.connect(
            lambda url: QDesktopServices.openUrl(QUrl(url))
        )
        self.update_dialog.show()
        self._refresh_update_install_state()

    def _update_check_failed(self, error, manual: bool = False) -> None:
        self.settings.setValue("updates/last_check_utc", self._utc_now_text())
        self.append_log("Update check failed: %s" % error)
        channel = getattr(self.update_client, "channel", "stable")
        channel_name = getattr(channel, "value", str(channel)).capitalize()
        self.update_channel_label.setText("v%s · %s · Lỗi kiểm tra" % (__version__, channel_name))
        if manual:
            QMessageBox.warning(self, "Không thể kiểm tra cập nhật", str(error))

    def _update_worker_finished(self) -> None:
        worker = self.sender()
        if worker in self._update_workers:
            self._update_workers.remove(worker)
        self.check_updates_action.setEnabled(True)
        worker.deleteLater()

    def _update_cache_dir(self) -> Path:
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            return base / "B300-STLink" / "updates"
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        return base / "b300-stlink" / "updates"

    def _start_update_download(self) -> None:
        if (
            self.update_client is None or self._update_result is None or
            self._update_result.asset is None or self.update_dialog is None
        ):
            return
        if any(isinstance(worker, UpdateDownloadWorker) for worker in self._update_workers):
            return
        self.update_dialog.set_downloading()
        worker = UpdateDownloadWorker(
            self.update_client, self._update_result.asset, self._update_cache_dir(), self
        )
        worker.progress.connect(self.update_dialog.set_download_progress)
        worker.completed.connect(self._update_download_finished)
        worker.failed.connect(self._update_download_failed)
        worker.finished.connect(self._update_worker_finished)
        self._update_workers.append(worker)
        worker.start()

    def _update_download_finished(self, package: Path) -> None:
        self._downloaded_update = Path(package)
        if self.update_dialog is not None:
            self.update_dialog.set_ready(self._downloaded_update)
        self._refresh_update_install_state()

    def _update_download_failed(self, error) -> None:
        self.append_log("Update download failed: %s" % error)
        if self.update_dialog is not None:
            self.update_dialog.action_button.setText("Thử tải lại")
            self.update_dialog.action_button.setEnabled(True)
        QMessageBox.warning(self, "Tải cập nhật thất bại", str(error))

    def _operation_state(self) -> OperationState:
        memory_tab = getattr(self, "memory_tab", None)
        debug_tab = getattr(self, "debug_tab", None)
        return OperationState(
            main_hardware_busy=self.busy or bool(self._threads),
            memory_hardware_busy=bool(
                memory_tab is not None and memory_tab.has_active_operation
            ),
            debug_hardware_busy=bool(
                debug_tab is not None and debug_tab.has_active_operation
            ),
        )

    def _hardware_activity_changed(self, _busy: bool = False) -> None:
        """Reflect shared ST-Link ownership immediately across every GUI surface."""
        self._update_controls()

    def _refresh_update_install_state(self) -> None:
        if self.update_dialog is None or self.update_dialog.ready_package is None:
            return
        busy = self._operation_state().is_hardware_busy
        self.update_dialog.set_install_allowed(
            not busy,
            "Bản cập nhật đã tải xong; chờ thao tác phần cứng hiện tại hoàn tất.",
        )

    def _install_downloaded_update(self) -> None:
        if self._downloaded_update is None or self._operation_state().is_hardware_busy:
            self._refresh_update_install_state()
            return
        try:
            platform_name = detect_update_platform(Path(sys.executable))
            plan = prepare_install(self._downloaded_update, platform_name)
        except (RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "Không thể cài cập nhật", str(error))
            return
        if not plan.managed:
            QMessageBox.warning(
                self, "Không thể cài cập nhật",
                "Nền tảng hiện tại không hỗ trợ cài đặt cập nhật tự động.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Cài đặt và khởi động lại",
            "Gói cập nhật đã vượt qua kiểm tra chữ ký và SHA-256.\n\n"
            "B300 ST-Link Tools sẽ tự động đóng, cài đặt bản mới và khởi động lại. "
            "Trên Ubuntu có thể xuất hiện hộp thoại xác thực quyền quản trị.\n\n"
            "Tiếp tục cập nhật?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.update_installer(plan)
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "Không thể mở trình cài đặt", str(error))
            return
        if self.update_dialog is not None:
            self.update_dialog.close()
        # The detached Linux helper waits for this process to disappear before
        # replacing the AppImage or invoking apt through Polkit. Explicitly quit
        # the Qt event loop after the close event is accepted so the old GUI
        # cannot remain as a stale/frozen process during an update.
        self.close()
        app = QGuiApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def _tab_changed(self, index: int) -> None:
        if self.busy or bool(self._threads):
            return
        if index == 0:
            if self.image_info is not None:
                self._set_status("Firmware hợp lệ; sẵn sàng dry-run hoặc nạp", "normal")
            elif self.openocd_ready:
                self._set_status("Sẵn sàng kiểm tra ST-Link", "normal")
            else:
                self._set_status("OpenOCD chưa sẵn sàng; hãy thiết lập môi trường offline", "error")
        elif index == 1:
            self._set_status(
                "Khảo sát bộ nhớ Flash (Read-Only) · Sector & OTA Metadata",
                "normal",
            )
        elif index == 2:
            self._set_status(
                "DEBUG MODE · Điều khiển OpenOCD / GDB; không ghi nạp firmware",
                "normal",
            )

    def _build_flash_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("flashTabPage")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        self.flash_scroll = QScrollArea()
        self.flash_scroll.setObjectName("flashScrollArea")
        self.flash_scroll.setWidgetResizable(True)
        self.flash_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.flash_scroll_content = QWidget()
        self.flash_scroll_content.setObjectName("flashScrollContent")
        content_layout = QVBoxLayout(self.flash_scroll_content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(8)
        self.flash_scroll.setWidget(self.flash_scroll_content)
        page_layout.addWidget(self.flash_scroll)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("flashResponsiveSplitter")
        self.flash_splitter = splitter
        left = QWidget()
        left_layout = QVBoxLayout(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)

        device_group = QGroupBox("1. ST-Link probe")
        device_row = QGridLayout(device_group)
        device_row.setHorizontalSpacing(8)
        device_row.setVerticalSpacing(6)
        self.probe_combo = QComboBox()
        self.probe_combo.setObjectName("probeSelector")
        self.probe_combo.setAccessibleName("Chọn ST-Link probe")
        self.probe_combo.currentIndexChanged.connect(self._probe_changed)
        self.refresh_button = QPushButton("Làm mới")
        self.refresh_button.clicked.connect(self.refresh_probes)
        self.inspect_target_button = QPushButton("Kiểm tra target")
        self.inspect_target_button.clicked.connect(self.inspect_target)
        device_row.addWidget(self.probe_combo, 0, 0)
        device_row.addWidget(self.refresh_button, 0, 1)
        device_row.addWidget(self.inspect_target_button, 0, 2)
        device_row.setColumnStretch(0, 1)
        self.target_summary = QLabel("Chưa kiểm tra chip/điện áp/flash/WRP")
        self.target_summary.setObjectName("targetSummaryBox")
        self.target_summary.setWordWrap(True)
        device_row.addWidget(self.target_summary, 1, 0, 1, 3)
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
        self.image_summary.setObjectName("imageSummaryBox")
        self.image_summary.setWordWrap(True)
        self.image_summary.setStyleSheet("color: #64748B;")
        firmware_layout.addWidget(self.image_summary, 1, 0, 1, 2)
        left_layout.addWidget(firmware_group)

        plan_group = QGroupBox("3. Flash plan cố định")
        plan_layout = QVBoxLayout(plan_group)
        plan_layout.setContentsMargins(8, 6, 8, 6)
        plan_layout.setSpacing(4)

        self.flash_plan_summary = QLabel(
            "<b>Bảo vệ:</b> Sector 0–2 (Bootloader) &nbsp;|&nbsp; "
            "<b>Xóa:</b> Sector 3–7 &nbsp;|&nbsp; "
            "<b>Nạp:</b> Application tại <code>0x08010000</code>"
        )
        self.flash_plan_summary.setObjectName("flashPlanSummaryCard")
        self.flash_plan_summary.setTextFormat(Qt.TextFormat.RichText)
        self.flash_plan_summary.setWordWrap(True)
        plan_layout.addWidget(self.flash_plan_summary)

        self.flash_plan_label = QLabel(
            "Erase Sector 3–7 → Program/Verify Application → STLM VERIFIED → "
            "Bootloader CONFIRMED → Post-verify"
        )
        self.flash_plan_label.setObjectName("flashPlanBadge")
        self.flash_plan_label.setStyleSheet(
            "background-color: #F8FAFC; color: #475569; border: 1px solid #E2E8F0; "
            "border-radius: 6px; padding: 3px 8px; font-weight: 600;"
        )
        self.flash_plan_label.setWordWrap(True)
        plan_layout.addWidget(self.flash_plan_label)

        self.plan_table = QTableWidget(5, 3)
        self.plan_table.setHorizontalHeaderLabels(["Sector", "Vai trò", "Thao tác"])
        self.plan_table.verticalHeader().setVisible(False)
        self.plan_table.verticalHeader().setDefaultSectionSize(24)
        self.plan_table.setMinimumHeight(160)
        self.plan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.plan_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for row, sector in enumerate(SECTORS[3:]):
            action = ("Erase + ghi STLM sau App verify"
                      if sector.index == 3 else "Erase + Program Application")
            self.plan_table.setItem(row, 0, QTableWidgetItem(str(sector.index)))
            self.plan_table.setItem(row, 1, QTableWidgetItem(sector.role))
            self.plan_table.setItem(row, 2, QTableWidgetItem(action))
        header = self.plan_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        plan_layout.addWidget(self.plan_table, 1)
        left_layout.addWidget(plan_group, 1)

        actions = QGridLayout()
        actions.setHorizontalSpacing(10)
        actions.setVerticalSpacing(8)
        self.dry_run_button = QPushButton("Kiểm tra dry-run")
        self.dry_run_button.clicked.connect(self.show_dry_run)
        self.cancel_button = QPushButton("Hủy thao tác an toàn")
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.cancel_button.setEnabled(False)
        actions.addWidget(self.dry_run_button, 0, 0)
        actions.addWidget(self.cancel_button, 0, 1)

        self.factory_provision_button = QPushButton("Nạp Bootloader")
        self.factory_provision_button.setObjectName("factoryProvisionButton")
        self.factory_provision_button.setToolTip(
            "One-click Factory: tự động nạp Bootloader tin cậy (Sector 0–2), "
            "kiểm tra chip/WRP và khôi phục bảo vệ an toàn."
        )
        self.factory_provision_button.clicked.connect(self.start_factory_provision)

        self.flash_button = QPushButton("Nạp Application")
        self.flash_button.setObjectName("flashButton")
        self.flash_button.clicked.connect(self.confirm_flash)

        actions.addWidget(self.factory_provision_button, 1, 0)
        actions.addWidget(self.flash_button, 1, 1)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        left_layout.addLayout(actions)

        log_group = QGroupBox("Log thời gian thực")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
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

        # Factory Aliases for unified single-tab workflow
        try:
            self.factory_trusted = load_trusted_bootloader()
            image = self.factory_trusted.image
            self.factory_artifact_label = QLabel(
                "Phiên bản: FW %s · Mục tiêu: B300_F407ZE · Toàn vẹn: ĐÃ XÁC THỰC ✓\n"
                "Vùng nhớ: 0x%08X..0x%08X (Sector 0–2)\n"
                "SHA-256: %s\n"
                "Source commit: %s" % (
                    self.factory_trusted.firmware_version, image.start_address,
                    image.end_address, image.sha256, self.factory_trusted.source_commit,
                )
            )
        except Exception as error:
            self.factory_trusted = None
            self.factory_artifact_label = QLabel("Bootloader tin cậy không khả dụng: %s" % error)

        self.factory_probe_combo = self.probe_combo
        self.factory_log_view = self.log_view
        self.factory_progress = self.progress

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        content_layout.addWidget(splitter)
        self.flash_scroll_content.setMinimumHeight(650)
        self._update_controls()
        QTimer.singleShot(0, self._update_flash_layout)
        return page

    def _update_flash_layout(self) -> None:
        """Stack Flash controls/log on narrow viewports and preserve vertical space."""
        if not hasattr(self, "flash_splitter") or not hasattr(self, "flash_scroll"):
            return
        width = self.flash_scroll.viewport().width()
        narrow = width > 0 and width < 1050
        orientation = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        if self.flash_splitter.orientation() != orientation:
            self.flash_splitter.setOrientation(orientation)
        if narrow:
            self.flash_splitter.setStretchFactor(0, 3)
            self.flash_splitter.setStretchFactor(1, 2)
            self.flash_scroll_content.setMinimumHeight(1050)
        else:
            self.flash_splitter.setStretchFactor(0, 3)
            self.flash_splitter.setStretchFactor(1, 2)
            self.flash_scroll_content.setMinimumHeight(650)

    def show_factory_dry_run(self) -> None:
        if self.factory_trusted is None or self.busy:
            return
        try:
            probe = self._selected_factory_probe()
            preview = self.service.factory_preview(self.factory_trusted.image, probe)
            transactions = (
                ("WRP OFF (conditional)", self.service.factory_protect_command(probe, False)),
                ("Erase S0-S2 + Program/Verify", self.service.factory_flash_command(preview)),
                ("WRP ON (mandatory restore)", self.service.factory_protect_command(probe, True)),
                ("Reset after verify/protect", self.service.reset_command(probe)),
            )
        except Exception as error:
            self.append_factory_log("Factory dry-run failed: %s" % error)
            self._set_status("Factory dry-run khong hop le", "error")
            return
        self.append_factory_log("FACTORY DRY-RUN (khong ghi phan cung)")
        for label, command in transactions:
            self.append_factory_log("%s: %s" % (label, subprocess.list2cmdline(command)))
        self._set_status("Factory dry-run hợp lệ; chưa có thay đổi phần cứng", "normal")

    def start_factory_provision(self) -> None:
        """One-click Factory entry point; all destructive safety checks stay automatic."""
        if self.factory_trusted is None or not self.openocd_ready or self.busy:
            return
        try:
            probe = self._selected_factory_probe()
        except ValueError as error:
            self._set_status(str(error), "error")
            return

        self.busy = True
        self.factory_progress.setRange(0, 100)
        self.factory_progress.setValue(0)
        self.factory_progress.setFormat("0% · Đang kiểm tra target / WRP")
        self.factory_log_view.clear()
        self.append_factory_log("FACTORY ONE-CLICK: bắt đầu preflight read-only")
        self._set_status(
            "Factory preflight đang kiểm tra F407 / 512 KiB / RDP / WRP; chưa ghi flash",
            "busy",
        )
        self._update_controls()
        self._start_worker(
            lambda log, phase, cancel: self.service.inspect_target(
                probe, event_sink=log, cancel_event=cancel
            ),
            lambda info, selected_probe=probe: self._factory_preflight_finished(
                selected_probe, info
            ),
            cancellable=False,
            on_failed=self._factory_operation_failed,
            log_handler=self.append_factory_log,
        )

    def _factory_preflight_finished(self, probe: ProbeRef, info: TargetInfo) -> None:
        """Approve the inspected target, then immediately start the trusted Factory flow."""
        self.apply_target_info(info)
        try:
            if not info.protection_reported:
                raise ValueError(
                    "OpenOCD did not report sector write-protection; Factory is blocked."
                )
            plan = self.service.factory_plan(self.factory_trusted.image, probe, info)
        except Exception as error:
            self.busy = False
            self.factory_progress.setRange(0, 1)
            self.factory_progress.setValue(0)
            self.factory_progress.setFormat("Factory bị chặn ở preflight")
            self.append_factory_log("PRE-FLIGHT BLOCKED: %s" % error)
            self._set_status("Factory preflight bị chặn: %s" % error, "error")
            self._update_controls()
            return

        self.busy = True
        self.factory_progress.setRange(0, 100)
        self.factory_progress.setValue(10)
        self.factory_progress.setFormat("10% · Preflight OK · bắt đầu Factory")
        self.append_factory_log(
            "PRE-FLIGHT OK: STM32F407 / 512 KiB / RDP Level 0 / WRP reported"
        )
        self._set_status(
            "Factory provisioning đang chạy; không rút ST-Link hoặc làm mất nguồn",
            "busy",
        )
        self._update_controls()
        self._start_worker(
            lambda log, phase, cancel: self.service.provision_bootloader(
                plan, event_sink=log, phase_sink=phase
            ),
            self._factory_finished,
            cancellable=False,
            on_failed=self._factory_operation_failed,
            phase_handler=self._factory_phase_changed,
            log_handler=self.append_factory_log,
        )

    def _factory_finished(self, result: FactoryResult) -> None:
        self.busy = False
        self.factory_progress.setRange(0, 1)
        self.factory_progress.setValue(1 if result.succeeded else 0)
        if result.succeeded:
            self.factory_progress.setFormat("Factory OK")
            self._set_status(
                "Bootloader verified; WRP Sector 0-2 đã được khôi phục và xác minh",
                "success",
            )
            if result.final_target is not None:
                self.apply_target_info(result.final_target)
        else:
            self.factory_progress.setFormat("Factory FAILED")
            self._set_status(
                "Factory phase %s · %s · Tiếp theo: %s" % (
                    result.failure_phase or "unknown", result.reason, result.next_action
                ),
                "error",
            )
        self._update_controls()

    def _restore_last_image(self) -> None:
        last_path = self.settings.value("lastImage", "")
        if last_path and Path(str(last_path)).is_file():
            self.load_image_path(Path(str(last_path)), quiet=True)

    def refresh_probes(self) -> None:
        current = self.probe_combo.currentData() if self.probe_combo.count() else None
        factory_current = (
            self.factory_probe_combo.currentData()
            if hasattr(self, "factory_probe_combo") and self.factory_probe_combo.count()
            else None
        )
        try:
            probes = tuple(self.probe_loader())
            available, executable = self.service.doctor()
        except Exception as error:
            probes = ()
            available, executable = False, ""
            self.append_log("Probe check failed: %s" % error)

        self._probes = tuple(probes)
        if hasattr(self, "debug_tab"):
            self.debug_tab.refresh_environment()

        self.probe_combo.blockSignals(True)
        self.probe_combo.clear()
        self._probe_selection_required = len(probes) > 1
        if self._probe_selection_required:
            self.probe_combo.addItem("Chọn ST-Link theo serial...", None)
        else:
            self.probe_combo.addItem("Tự động chọn (ST-Link duy nhất)", None)
        for probe in probes:
            self.probe_combo.addItem("%s | %s" % (probe.name, probe.serial), probe.serial)
        restore_index = self.probe_combo.findData(current)
        self.probe_combo.setCurrentIndex(max(0, restore_index))
        self.probe_combo.blockSignals(False)

        if hasattr(self, "factory_probe_combo"):
            self.factory_probe_combo.blockSignals(True)
            self.factory_probe_combo.clear()
            if len(probes) > 1:
                self.factory_probe_combo.addItem("Chọn đúng ST-Link cho Factory...", None)
            else:
                self.factory_probe_combo.addItem("Tự động chọn ST-Link", None)
            for probe in probes:
                self.factory_probe_combo.addItem(
                    "%s | %s" % (probe.name, probe.serial), probe.serial
                )
            restore_factory = self.factory_probe_combo.findData(factory_current)
            if factory_current is None and len(probes) == 1:
                restore_factory = 1
            elif restore_factory < 0 and current is not None:
                restore_factory = self.factory_probe_combo.findData(current)
            self.factory_probe_combo.setCurrentIndex(max(0, restore_factory))
            self.factory_probe_combo.blockSignals(False)

        self.openocd_ready = available
        self.target_ready = False
        self.target_info = None
        if hasattr(self, "factory_target_summary"):
            self.factory_target_summary.setText(
                "Sẵn sàng one-click Factory; target/WRP sẽ được tự kiểm tra trước khi ghi"
            )
        if available:
            detail = "%d probe(s) found" % len(probes) if probes else "OpenOCD ready"
            self._set_status("%s | %s" % (detail, executable), "normal")
        else:
            self._set_status(
                "OpenOCD not found; use offline environment setup",
                "error",
            )
        self._rebuild_plan()

    def _select_offline_bundle(self) -> Optional[Path]:
        search_root = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                       else Path.cwd())
        try:
            discovered = find_offline_bundle(search_root, current_platform_name())
        except RuntimeError as error:
            QMessageBox.critical(self, "Nền tảng chưa được hỗ trợ", str(error))
            return None
        if discovered is not None:
            return discovered
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn gói B300 ST-Link offline đầy đủ",
            str(search_root),
            "B300 offline bundle (*.zip *.tar.gz)",
        )
        return Path(selected) if selected else None

    def setup_environment(self) -> None:
        if self.busy:
            return
        try:
            bundle = self.setup_bundle_provider()
        except Exception as error:
            QMessageBox.critical(self, "Không thể chọn gói offline", str(error))
            return
        if bundle is None:
            return
        answer = QMessageBox.question(
            self,
            "Thiết lập OpenOCD offline",
            "Gói nguồn: %s\n\n"
            "Cài OpenOCD xPack 0.12.0-7 từ gói này?\n"
            "Tool sẽ kiểm tra SHA-256 tin cậy của archive và toàn bộ runtime.\n"
            "Thao tác không dùng Internet và không kết nối STM32/ST-Link." % bundle,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.busy = True
        self.setup_button.setText("Đang thiết lập…")
        self.progress.setRange(0, 0)
        self.progress.setFormat("Đang kiểm tra gói offline")
        self._set_status("Đang thiết lập OpenOCD offline; không cần Internet", "busy")
        self._update_controls()
        self._start_worker(
            lambda log, phase, cancel: self.setup_installer(Path(bundle)),
            self._offline_setup_finished,
            on_failed=self._offline_setup_failed,
        )

    def _offline_setup_finished(self, executable: Path) -> None:
        self.service.executable = str(executable)
        self.busy = False
        self.setup_button.setText("Thiết lập môi trường")
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setFormat("Thiết lập hoàn tất")
        self.append_log("Offline OpenOCD installed and verified: %s" % executable)
        available, resolved = self.service.doctor()
        self.openocd_ready = available
        self.target_ready = False
        self.target_info = None
        if available:
            self._set_status(
                "OpenOCD sẵn sàng · chưa quét ST-Link · %s" % resolved,
                "normal",
            )
        else:
            self._set_status("Không thể xác nhận OpenOCD sau thiết lập", "error")
        self._rebuild_plan()

    def _offline_setup_failed(self, failure) -> None:
        self.busy = False
        self.setup_button.setText("Thiết lập môi trường")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Setup lỗi")
        message = getattr(failure, "message", str(failure))
        self.append_log(getattr(failure, "traceback", str(failure)))
        self._set_status(
            "Thiết lập offline thất bại · %s · Chọn đúng bundle cho máy này" % message,
            "error",
        )
        self._update_controls()

    def _probe_changed(self) -> None:
        selected = self.probe_combo.currentData()
        if hasattr(self, "factory_probe_combo"):
            self.factory_probe_combo.blockSignals(True)
            index = self.factory_probe_combo.findData(selected)
            self.factory_probe_combo.setCurrentIndex(max(0, index))
            self.factory_probe_combo.blockSignals(False)
        self.target_ready = False
        self.target_info = None
        self.target_summary.setText("Probe changed; inspect target/WRP again")
        if hasattr(self, "factory_target_summary"):
            self.factory_target_summary.setText("Probe changed; inspect target/WRP again")
        self._rebuild_plan()

    def _factory_probe_changed(self) -> None:
        selected = self.factory_probe_combo.currentData()
        if selected is not None:
            self.probe_combo.blockSignals(True)
            index = self.probe_combo.findData(selected)
            if index >= 0:
                self.probe_combo.setCurrentIndex(index)
            self.probe_combo.blockSignals(False)
        self.target_ready = False
        self.target_info = None
        self.target_summary.setText("Factory probe changed; target sẽ được tự kiểm tra khi nạp")
        self.factory_target_summary.setText("Probe đã thay đổi; one-click Factory sẽ tự kiểm tra target/WRP")
        self._rebuild_plan()

    def _selected_factory_probe(self) -> ProbeRef:
        serial = self.factory_probe_combo.currentData()
        if serial is not None:
            return ProbeRef(serial)
        probes = self._probes
        if len(probes) > 1:
            raise ValueError("Có nhiều ST-Link; hãy chọn đúng serial trước khi nạp Bootloader.")
        if len(probes) == 1:
            return ProbeRef(probes[0].serial or None)
        # OpenOCD auto-select is intentionally allowed when USB discovery exposes no serial
        # (common with some ST-Link V2/clone devices). Target/RDP/WRP checks still fail closed.
        return ProbeRef(None)

    def inspect_factory_target(self) -> None:
        if not self.openocd_ready or self.busy:
            return
        try:
            probe = self._selected_factory_probe()
        except ValueError as error:
            self._set_status(str(error), "error")
            return
        self.busy = True
        self._set_status("Reading Factory target/WRP/RDP via selected ST-Link serial", "busy")
        self._update_controls()
        self._start_worker(
            lambda log, phase, cancel: self.service.inspect_target(
                probe, event_sink=log, cancel_event=cancel
            ),
            self.apply_target_info,
            cancellable=True,
        )

    def _selected_probe(self) -> ProbeRef:
        if self._probe_selection_required and self.probe_combo.currentData() is None:
            raise ValueError("Multiple ST-Link probes detected; select one serial explicitly.")
        return ProbeRef(self.probe_combo.currentData())

    def inspect_target(self) -> None:
        if not self.openocd_ready or self.busy:
            return
        probe = self._selected_probe()
        self.busy = True
        self._set_status("Đang đọc chip/điện áp/flash/WRP qua SWD…", "busy")
        self._update_controls()
        self._start_worker(
            lambda log, phase, cancel: self.service.inspect_target(
                probe, event_sink=log, cancel_event=cancel
            ),
            self.apply_target_info,
            cancellable=True,
        )

    def apply_target_info(self, info: TargetInfo) -> None:
        self.busy = False
        is_f407 = (info.device_id & 0xFFF) == 0x413 and info.flash_kib == 512
        wrp_bootloader_ok = (
            info.protection_reported and
            all(sector in info.protected_sectors for sector in (0, 1, 2))
        )
        normal_ready = is_f407 and wrp_bootloader_ok and not info.readout_protected
        self.target_ready = normal_ready
        # Keep valid F407 target information for the separate Factory workflow even
        # when S0-S2 WRP is currently off. RDP/security still blocks destructive plans.
        self.target_info = info if is_f407 else None
        rdp_text = "ENABLED (blocked)" if info.readout_protected else "Level 0 / not reported as secured"
        summary = (
            "Device ID: 0x%08X | Flash: %d KiB | Voltage: %.3f V\n"
            "WRP: %s\nRDP/Security: %s" % (
                info.device_id, info.flash_kib, info.target_voltage,
                info.protection_summary, rdp_text,
            )
        )
        self.target_summary.setText(summary)
        if hasattr(self, "factory_target_summary"):
            self.factory_target_summary.setText(summary)

        if not is_f407:
            self._set_status("Target is not the B300 STM32F407ZE 512 KiB configuration", "error")
        elif info.readout_protected:
            self._set_status("RDP/security is enabled; B300 Tools will not modify RDP", "error")
        elif not info.protection_reported:
            self._set_status("OpenOCD did not report WRP; destructive provisioning is blocked", "error")
        elif wrp_bootloader_ok:
            self._set_status("B300 target valid; Bootloader S0-S2 WRP protected", "success")
        else:
            self._set_status(
                "B300 target valid but Bootloader WRP is incomplete; normal Application flash blocked",
                "error",
            )
        self._rebuild_plan()

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
            self.image_summary.setStyleSheet("color: #DC2626; font-weight: 600;")
            self._set_status("Firmware không hợp lệ", "error")
            if not quiet:
                self.append_log("Image validation failed: %s" % error)
            self._update_controls()
            return False
        self.file_path.setText(str(self.image_info.path))
        self.settings.setValue("lastImage", str(self.image_info.path))
        self.image_summary.setStyleSheet("color: #059669; font-weight: 600;")
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
        if (self.image_info is None or self.target_info is None or
                (self._probe_selection_required and self.probe_combo.currentData() is None)):
            self.flash_plan = None
        else:
            try:
                self.flash_plan = self.service.plan(
                    self.image_info, self._selected_probe(), self.target_info
                )
            except Exception as error:
                self.flash_plan = None
                self.append_log("Flash plan failed: %s" % error)
        self._update_controls()

    def _update_controls(self) -> None:
        operation = self._operation_state()
        main_locked = self.busy or operation.main_blocked_by_other
        flash_state = FlashViewState(
            self.target_ready, self.flash_plan is not None, main_locked
        )
        self.flash_button.setEnabled(flash_state.can_flash)
        # Dry-run and image selection are offline-only and may remain usable while Debug owns ST-Link.
        self.dry_run_button.setEnabled(self.flash_plan is not None and not self.busy)
        self.choose_button.setEnabled(not self.busy)
        self.refresh_button.setEnabled(not main_locked)
        probe_selected = not self._probe_selection_required or \
            self.probe_combo.currentData() is not None
        self.inspect_target_button.setEnabled(
            self.openocd_ready and probe_selected and not main_locked
        )
        self.probe_combo.setEnabled(not main_locked)
        self.setup_button.setVisible(not self.openocd_ready)
        self.setup_button.setEnabled(not operation.is_hardware_busy)
        if hasattr(self, "factory_provision_button"):
            probes = self._probes
            factory_probe_ok = (
                len(probes) <= 1 or self.factory_probe_combo.currentData() is not None
            )
            self.factory_provision_button.setEnabled(
                self.factory_trusted is not None and self.openocd_ready and
                factory_probe_ok and not main_locked
            )
            self.factory_probe_combo.setEnabled(not main_locked)
        if hasattr(self, "memory_tab"):
            self.memory_tab.set_external_blocked(operation.memory_blocked_by_other)
        if hasattr(self, "debug_tab"):
            self.debug_tab.set_external_blocked(operation.debug_blocked_by_other)
        self._refresh_update_install_state()

    def show_dry_run(self) -> None:
        if self.flash_plan is None:
            return
        self.append_log("DRY-RUN (không ghi phần cứng)")
        transactions = (
            ("Program/Verify", self.service.flash_command(self.flash_plan)),
            ("Reset (chỉ sau verify thành công)",
             self.service.reset_command(self.flash_plan.probe)),
        )
        for label, command in transactions:
            self.append_log("%s: %s" % (label, subprocess.list2cmdline(command)))
        self._set_status("Dry-run hợp lệ; kiểm tra probe/file trước khi nạp", "normal")

    def confirm_flash(self) -> None:
        if self.flash_plan is None or self.busy:
            return
        dialog = ConfirmFlashDialog(self.flash_plan, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._start_flash()

    def _start_flash(self) -> None:
        assert self.flash_plan is not None
        plan = self.flash_plan
        self.busy = True
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("0% · Chuẩn bị")
        self._set_status("Đang ghi Sector 3–7; không rút ST-Link hoặc mất nguồn", "busy")
        self._update_controls()
        self._start_worker(
            lambda log, phase, cancel: self.service.flash(
                plan,
                event_sink=log,
                phase_sink=phase,
                cancel_event=cancel,
            ),
            self._flash_finished,
            cancellable=False,
        )

    def _start_worker(self, operation, on_finished, cancellable: bool = False,
                      on_failed=None, phase_handler=None, log_handler=None) -> None:
        worker = FunctionWorker(operation, self)
        worker.log.connect(log_handler or self.append_log)
        worker.phase.connect(phase_handler or self._flash_phase_changed)
        worker.completed.connect(on_finished)
        worker.failed.connect(on_failed or self._operation_failed)
        worker.finished.connect(self._worker_finished)
        self._threads.append(worker)
        if cancellable:
            self._cancellable_worker = worker
            self.cancel_button.setEnabled(True)
        worker.start()

    def _worker_finished(self) -> None:
        worker = self.sender()
        if worker in self._threads:
            self._threads.remove(worker)
        if worker is self._cancellable_worker:
            self._cancellable_worker = None
            self.cancel_button.setEnabled(False)
        worker.deleteLater()
        # completed/failed callbacks run before QThread.finished, so their first
        # _update_controls() still sees this worker in self._threads and keeps
        # Memory/Metadata/Debug externally blocked. Refresh again only after the
        # worker has actually left the ownership list. Without this, ST-Link is
        # physically free but the GUI remains latched busy until another UI event
        # (or an application restart) happens.
        self._update_controls()

    def cancel_operation(self) -> None:
        if self._cancellable_worker is None:
            return
        self._cancellable_worker.cancel()
        self.cancel_button.setEnabled(False)
        self._set_status("Đang hủy thao tác read-only an toàn…", "busy")

    def _factory_phase_changed(self, event) -> None:
        self.factory_progress.setRange(0, 100)
        self.factory_progress.setValue(event.progress)
        self.factory_progress.setFormat("%d%% - %s" % (event.progress, event.message))
        if event.phase not in {"succeeded", "failed"}:
            self._set_status(
                "%s - do not disconnect ST-Link or power" % event.message,
                "busy",
            )

    def _factory_operation_failed(self, failure) -> None:
        self.busy = False
        self.factory_progress.setRange(0, 1)
        self.factory_progress.setValue(0)
        self.factory_progress.setFormat("Factory error")
        phase = getattr(failure, "phase", "factory")
        message = getattr(failure, "message", str(failure))
        next_action = getattr(
            failure, "next_action",
            "Verify Sector 0-2 WRP before any further flash operation.",
        )
        self.append_factory_log(getattr(failure, "traceback", str(failure)))
        self._set_status(
            "Factory phase %s - %s - Next: %s" % (phase, message, next_action),
            "error",
        )
        self._update_controls()

    def _flash_phase_changed(self, event) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(event.progress)
        self.progress.setFormat("%d%% · %s" % (event.progress, event.message))
        if event.phase not in {"succeeded", "failed"}:
            self._set_status(
                "%s · không rút ST-Link hoặc mất nguồn" % event.message,
                "busy",
            )

    def _flash_finished(self, result: FlashResult) -> None:
        self.busy = False
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if result.succeeded else 0)
        if result.succeeded:
            verification = result.boot_verification
            confirmed = result.confirmed_metadata
            self.progress.setFormat("Hoàn tất · STLM CONFIRMED")
            self._set_status(
                "Nạp thành công · STLM CONFIRMED seq=%s · Application PC=%s · BKP1R=0" % (
                    confirmed.sequence if confirmed is not None else "N/A",
                    "0x%08X" % verification.pc if verification and verification.pc else "N/A",
                ),
                "success",
            )
            self.memory_tab.invalidate_metadata_view(
                "Application provisioning vừa erase/program S3–S7."
            )
        elif result.status == "programmed_boot_failed":
            self.progress.setFormat("Boot verify lỗi")
            self._set_status(
                "Phase %s · %s · Tiếp theo: %s" % (
                    result.failure_phase,
                    result.reason,
                    result.next_action,
                ),
                "error",
            )
        else:
            self.progress.setFormat("Flash lỗi")
            self._set_status(
                "Phase %s · %s · Tiếp theo: %s" % (
                    result.failure_phase or "unknown",
                    result.reason or "OpenOCD transaction failed",
                    result.next_action or "Xem log; không tự retry.",
                ),
                "error",
            )
        self._update_controls()

    def _operation_failed(self, failure) -> None:
        self.busy = False
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Lỗi")
        phase = getattr(failure, "phase", "operation")
        message = getattr(failure, "message", str(failure))
        next_action = getattr(
            failure, "next_action", "Xem log; không tự retry."
        )
        detail = getattr(failure, "traceback", str(failure))
        self.append_log(detail)
        self._set_status(
            "Phase %s · %s · Tiếp theo: %s" % (phase, message, next_action),
            "error",
        )
        self._update_controls()

    def closeEvent(self, event) -> None:
        if self.busy or self._threads or self.memory_tab.has_active_operation or self.debug_tab.has_active_operation:
            event.ignore()
            self._set_status(
                "Thao tác đang chạy; hãy chờ hoàn tất hoặc hủy khi nút Hủy được bật.",
                "error",
            )
            self.append_log("Close blocked: an ST-Link operation is still active.")
            return
        self._update_poll_timer.stop()
        if not self.debug_tab.prepare_shutdown():
            event.ignore()
            self._set_status("Debug worker chưa dừng sạch; thử đóng lại sau vài giây.", "error")
            return
        event.accept()

    def append_factory_log(self, line: str) -> None:
        self.append_log(line)

    def append_log(self, line: str) -> None:
        html_line = format_log_html(str(line))
        self.log_view.appendHtml(html_line)
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfLine)
        self.log_view.setTextCursor(cursor)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )
        self.log_view.horizontalScrollBar().setValue(0)

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
        self._show_toast(text, state)

    def _show_toast(self, text: str, state: str) -> None:
        if not text or not self.isVisible():
            return
        if hasattr(self, "_current_toast") and self._current_toast is not None:
            try:
                self._current_toast.dismiss()
            except Exception:
                pass
        self._current_toast = ToastNotification(text, state, self)
        self._current_toast.show_toast(self)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_flash_layout()
        if (
            hasattr(self, "_current_toast") and
            self._current_toast is not None and
            self._current_toast.isVisible()
        ):
            self._current_toast._reposition(self)
