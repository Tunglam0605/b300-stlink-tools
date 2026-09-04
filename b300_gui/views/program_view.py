"""Operator-oriented Program Page for B300 ST-Link Tools (v0.18).

Provides safe firmware provisioning for Application and Bootloader with progressive
disclosure, clear status indicators, and remote programming foundation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from b300_core.hex_image import inspect_image
from b300_core.models import ImageInfo, ProbeInfo, TargetInfo
from b300_gui.collapsible_card import CollapsibleCard
from b300_gui.widgets.memory_map_widget import MemoryMapWidget
from b300_gui.widgets.pass_fail_banner import PassFailBanner
from b300_gui.widgets.pipeline_stepper import PipelineStepper


class ProgramView(QWidget):
    """Clean, operator-oriented ST-Link programming view."""

    flash_application_requested = Signal(Path, bool)  # (path, is_dry_run)
    flash_bootloader_requested = Signal(bool)          # (confirmed,)
    remote_flash_requested = Signal(str, Path)         # (gateway_host, file_path)
    file_selected = Signal(Path)
    probe_refresh_requested = Signal()
    target_inspect_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("programViewContainer")
        self._selected_file: Optional[Path] = None
        self._current_image: Optional[ImageInfo] = None
        self._probes: List[ProbeInfo] = []
        self._target_info: Optional[TargetInfo] = None
        self._busy = False

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container.setObjectName("programContent")
        self.container_layout = QVBoxLayout(container)
        self.container_layout.setContentsMargins(16, 12, 16, 14)
        self.container_layout.setSpacing(10)

        self._build_ui()
        scroll.setWidget(container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _build_ui(self) -> None:
        # Top Result / Status Banner
        self.banner = PassFailBanner(self)
        self.banner.hide()
        self.container_layout.addWidget(self.banner)

        # 1. DEVICE CARD
        self._build_device_card()

        # 2. FIRMWARE CARD (Application & Bootloader)
        self._build_firmware_card()

        # 3. PIPELINE STEPPER
        self.stepper = PipelineStepper(self)
        self.container_layout.addWidget(self.stepper)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.hide()
        self.container_layout.addWidget(self.progress_bar)

        # 4. ADVANCED SECTION (Collapsed by default)
        self._build_advanced_section()

    def _build_device_card(self) -> None:
        card = QFrame()
        card.setObjectName("cardSurface")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        title = QLabel("DEVICE · MẠCH NẠP & TARGET MCU")
        title.setObjectName("eyebrowLabel")
        header_row.addWidget(title)
        header_row.addStretch(1)

        self.btn_refresh_probe = QPushButton("🔄 Quét lại ST-Link")
        self.btn_refresh_probe.setObjectName("ghostButton")
        self.btn_refresh_probe.setToolTip("Quét lại danh sách ST-Link USB")
        self.btn_refresh_probe.clicked.connect(self.probe_refresh_requested.emit)
        header_row.addWidget(self.btn_refresh_probe)

        self.btn_inspect_target = QPushButton("🔍 Kiểm tra Target")
        self.btn_inspect_target.setObjectName("ghostButton")
        self.btn_inspect_target.setToolTip("Đọc Target ID, điện áp, Flash size và Option Bytes")
        self.btn_inspect_target.clicked.connect(self.target_inspect_requested.emit)
        header_row.addWidget(self.btn_inspect_target)
        card_layout.addLayout(header_row)

        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(14)
        info_grid.setVerticalSpacing(4)

        # Probe Info
        lbl_probe_title = QLabel("Probe:")
        lbl_probe_title.setStyleSheet("font-weight: 600; color: #94A3B8;")
        self.lbl_probe = QLabel("ST-Link V2 · Đang quét...")
        self.lbl_probe.setStyleSheet("font-weight: 700; color: #F8FAFC;")
        info_grid.addWidget(lbl_probe_title, 0, 0)
        info_grid.addWidget(self.lbl_probe, 0, 1)

        # Target Info
        lbl_target_title = QLabel("Target:")
        lbl_target_title.setStyleSheet("font-weight: 600; color: #94A3B8;")
        self.lbl_target = QLabel("STM32F407ZET6")
        self.lbl_target.setStyleSheet("font-weight: 700; color: #F8FAFC;")
        info_grid.addWidget(lbl_target_title, 0, 2)
        info_grid.addWidget(self.lbl_target, 0, 3)

        # Status
        lbl_status_title = QLabel("Status:")
        lbl_status_title.setStyleSheet("font-weight: 600; color: #94A3B8;")
        self.lbl_status = QLabel("● Chờ kết nối ST-Link")
        self.lbl_status.setStyleSheet("font-weight: 700; color: #EAB308;")
        info_grid.addWidget(lbl_status_title, 0, 4)
        info_grid.addWidget(self.lbl_status, 0, 5)

        info_grid.setColumnStretch(1, 1)
        info_grid.setColumnStretch(3, 1)
        info_grid.setColumnStretch(5, 1)
        card_layout.addLayout(info_grid)

        self.container_layout.addWidget(card)

    def _build_firmware_card(self) -> None:
        card = QFrame()
        card.setObjectName("cardSurface")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)

        # Mode Selection Row
        mode_row = QHBoxLayout()
        mode_title = QLabel("FIRMWARE PROGRAMMING")
        mode_title.setObjectName("eyebrowLabel")
        mode_row.addWidget(mode_title)
        mode_row.addStretch(1)

        self.mode_group = QButtonGroup(self)
        self.radio_local = QRadioButton("Local ST-Link (USB)")
        self.radio_local.setChecked(True)
        self.radio_local.setToolTip("Nạp trực tiếp qua mạch ST-Link cắm trên máy này")
        self.mode_group.addButton(self.radio_local, 0)
        mode_row.addWidget(self.radio_local)

        self.radio_remote = QRadioButton("Remote Gateway")
        self.radio_remote.setToolTip("Nạp firmware từ xa qua B300 Gateway (SSH)")
        self.mode_group.addButton(self.radio_remote, 1)
        mode_row.addWidget(self.radio_remote)

        self.mode_group.idToggled.connect(self._on_programming_mode_toggled)
        card_layout.addLayout(mode_row)

        # ----------------------------------------------------
        # LOCAL MODE SUB-PANEL
        # ----------------------------------------------------
        self.local_panel = QWidget()
        local_layout = QVBoxLayout(self.local_panel)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.setSpacing(10)

        # Application Section
        app_box = QFrame()
        app_box.setObjectName("nestedCard")
        app_box_layout = QVBoxLayout(app_box)
        app_box_layout.setContentsMargins(12, 10, 12, 10)
        app_box_layout.setSpacing(6)

        app_title_row = QHBoxLayout()
        app_lbl = QLabel("Application Firmware")
        app_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #38BDF8;")
        app_title_row.addWidget(app_lbl)

        app_hint = QLabel("Sector 4–7 (0x08010000..0x0807FFFF) · Tự động ghi 44B STLM metadata")
        app_hint.setStyleSheet("font-size: 11px; color: #64748B;")
        app_title_row.addWidget(app_hint)
        app_title_row.addStretch(1)
        app_box_layout.addLayout(app_title_row)

        file_row = QHBoxLayout()
        file_row.setSpacing(6)
        self.app_file_edit = QLineEdit()
        self.app_file_edit.setPlaceholderText("Chọn file Application (.hex, .bin, .elf)...")
        self.app_file_edit.setReadOnly(True)
        file_row.addWidget(self.app_file_edit, 1)

        self.btn_browse_app = QPushButton("📁 Chọn file…")
        self.btn_browse_app.clicked.connect(self._browse_app_file)
        file_row.addWidget(self.btn_browse_app)
        app_box_layout.addLayout(file_row)

        self.app_meta_label = QLabel("Chưa chọn file firmware application")
        self.app_meta_label.setStyleSheet("font-size: 11px; color: #64748B; font-family: monospace;")
        app_box_layout.addWidget(self.app_meta_label)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        self.cb_dry_run = QCheckBox("Dry-run (chỉ kiểm tra hợp lệ, không xóa flash)")
        self.cb_dry_run.setToolTip("Chạy kiểm tra transaction và contract trước khi ghi flash thật")
        action_row.addWidget(self.cb_dry_run)
        action_row.addStretch(1)

        self.btn_flash_app = QPushButton("⚡ NẠP APPLICATION")
        self.btn_flash_app.setObjectName("primaryActionButton")
        self.btn_flash_app.setStyleSheet(
            "QPushButton { background: #0284C7; color: white; font-weight: 800; "
            "font-size: 13px; padding: 8px 18px; border-radius: 4px; }"
            "QPushButton:hover { background: #0369A1; }"
            "QPushButton:disabled { background: #334155; color: #64748B; }"
        )
        self.btn_flash_app.clicked.connect(self._on_flash_app_clicked)
        action_row.addWidget(self.btn_flash_app)
        app_box_layout.addLayout(action_row)

        local_layout.addWidget(app_box)

        # Bootloader Section
        boot_box = QFrame()
        boot_box.setObjectName("nestedCard")
        boot_box_layout = QVBoxLayout(boot_box)
        boot_box_layout.setContentsMargins(12, 10, 12, 10)
        boot_box_layout.setSpacing(6)

        boot_title_row = QHBoxLayout()
        boot_lbl = QLabel("Bootloader B300")
        boot_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F59E0B;")
        boot_title_row.addWidget(boot_lbl)

        boot_hint = QLabel("Sector 0–2 (0x08000000..0x0800BFFF) · Bản chuẩn bundle v0.6.5")
        boot_hint.setStyleSheet("font-size: 11px; color: #64748B;")
        boot_title_row.addWidget(boot_hint)
        boot_title_row.addStretch(1)
        boot_box_layout.addLayout(boot_title_row)

        boot_action_row = QHBoxLayout()
        self.boot_info_label = QLabel("Bundled Bootloader: B300-Bootloader-v0.6.5 (WRP Sector 0-2)")
        self.boot_info_label.setStyleSheet("font-size: 11px; color: #94A3B8;")
        boot_action_row.addWidget(self.boot_info_label, 1)

        self.btn_flash_bootloader = QPushButton("🛡 NẠP BOOTLOADER")
        self.btn_flash_bootloader.setObjectName("warningActionButton")
        self.btn_flash_bootloader.setToolTip(
            "Chỉ dùng khi chuẩn bị main mới hoặc bảo trì bootloader được ủy quyền. "
            "Yêu cầu xác nhận an toàn trước khi nạp."
        )
        self.btn_flash_bootloader.setStyleSheet(
            "QPushButton { background: #78350F; color: #FDE68A; font-weight: 700; "
            "font-size: 11px; padding: 6px 14px; border: 1px solid #B45309; border-radius: 4px; }"
            "QPushButton:hover { background: #92400E; color: white; }"
            "QPushButton:disabled { background: #1E293B; color: #475569; border-color: #334155; }"
        )
        self.btn_flash_bootloader.clicked.connect(self._on_flash_bootloader_clicked)
        boot_action_row.addWidget(self.btn_flash_bootloader)
        boot_box_layout.addLayout(boot_action_row)

        local_layout.addWidget(boot_box)
        card_layout.addWidget(self.local_panel)

        # ----------------------------------------------------
        # REMOTE GATEWAY MODE SUB-PANEL (Foundation for v0.18 backend)
        # ----------------------------------------------------
        self.remote_panel = QWidget()
        remote_layout = QVBoxLayout(self.remote_panel)
        remote_layout.setContentsMargins(0, 0, 0, 0)
        remote_layout.setSpacing(10)
        self.remote_panel.hide()

        remote_box = QFrame()
        remote_box.setObjectName("nestedCard")
        remote_box_layout = QVBoxLayout(remote_box)
        remote_box_layout.setContentsMargins(12, 10, 12, 10)
        remote_box_layout.setSpacing(8)

        rem_title_row = QHBoxLayout()
        rem_lbl = QLabel("Remote Programming Gateway")
        rem_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #A855F7;")
        rem_title_row.addWidget(rem_lbl)
        rem_note = QLabel("Gửi firmware qua B300 Gateway SSH tunnel để nạp từ xa")
        rem_note.setStyleSheet("font-size: 11px; color: #64748B;")
        rem_title_row.addWidget(rem_note)
        rem_title_row.addStretch(1)
        remote_box_layout.addLayout(rem_title_row)

        gateway_form = QGridLayout()
        gateway_form.setHorizontalSpacing(8)
        gateway_form.setVerticalSpacing(6)

        lbl_gw = QLabel("Gateway:")
        lbl_gw.setStyleSheet("font-weight: 600; color: #94A3B8;")
        self.remote_gw_edit = QLineEdit()
        self.remote_gw_edit.setPlaceholderText("192.168.1.109:22 hoặc b300-gateway.local")
        gateway_form.addWidget(lbl_gw, 0, 0)
        gateway_form.addWidget(self.remote_gw_edit, 0, 1)

        lbl_fw = QLabel("Firmware:")
        lbl_fw.setStyleSheet("font-weight: 600; color: #94A3B8;")
        self.remote_fw_edit = QLineEdit()
        self.remote_fw_edit.setPlaceholderText("application.hex...")
        self.remote_fw_edit.setReadOnly(True)
        btn_browse_remote = QPushButton("📁 Chọn HEX…")
        btn_browse_remote.clicked.connect(self._browse_remote_file)
        fw_row = QHBoxLayout()
        fw_row.addWidget(self.remote_fw_edit, 1)
        fw_row.addWidget(btn_browse_remote)
        gateway_form.addWidget(lbl_fw, 1, 0)
        gateway_form.addLayout(fw_row, 1, 1)

        remote_box_layout.addLayout(gateway_form)

        # Pipeline Stepper Indicator for Remote Flash
        pipeline_box = QFrame()
        pipeline_layout = QHBoxLayout(pipeline_box)
        pipeline_layout.setContentsMargins(4, 4, 4, 4)
        pipeline_layout.setSpacing(4)
        pipeline_steps = ["Upload", "Validate", "Gateway", "ST-Link", "Verify"]
        self.pipeline_labels = []
        for i, step in enumerate(pipeline_steps):
            lbl = QLabel(f"[{i+1}] {step}")
            lbl.setStyleSheet("font-size: 10px; color: #64748B; font-weight: 600; padding: 2px 6px;")
            pipeline_layout.addWidget(lbl)
            self.pipeline_labels.append(lbl)
            if i < len(pipeline_steps) - 1:
                arr = QLabel("→")
                arr.setStyleSheet("color: #475569; font-size: 11px;")
                pipeline_layout.addWidget(arr)
        pipeline_layout.addStretch(1)
        remote_box_layout.addWidget(pipeline_box)

        # Primary Remote CTA
        rem_action_row = QHBoxLayout()
        rem_action_row.addStretch(1)
        self.btn_remote_flash = QPushButton("🚀 GỬI & NẠP APPLICATION TỪ XA")
        self.btn_remote_flash.setStyleSheet(
            "QPushButton { background: #7C3AED; color: white; font-weight: 800; "
            "font-size: 12px; padding: 8px 18px; border-radius: 4px; }"
            "QPushButton:hover { background: #6D28D9; }"
            "QPushButton:disabled { background: #334155; color: #64748B; }"
        )
        self.btn_remote_flash.clicked.connect(self._on_remote_flash_clicked)
        rem_action_row.addWidget(self.btn_remote_flash)
        remote_box_layout.addLayout(rem_action_row)

        remote_layout.addWidget(remote_box)
        card_layout.addWidget(self.remote_panel)

        self.container_layout.addWidget(card)

    def _build_advanced_section(self) -> None:
        self.adv_card = CollapsibleCard(
            "Nâng cao / Diagnostics",
            "Verify, Metadata, Memory, Option Bytes, Log chi tiết",
            expanded=False,
            parent=self,
        )
        adv_layout = self.adv_card.content_layout

        # 1. Visual Memory Map Bar
        self.memory_map = MemoryMapWidget(self)
        adv_layout.addWidget(self.memory_map)

        # 2. Metadata & Option Bytes Information
        adv_info_row = QHBoxLayout()
        self.lbl_metadata_info = QLabel("Metadata: 0x0800C000 · 44-byte STLM record (Chưa đọc)")
        self.lbl_metadata_info.setStyleSheet("font-size: 11px; color: #94A3B8; font-family: monospace;")
        adv_info_row.addWidget(self.lbl_metadata_info)
        adv_info_row.addStretch(1)

        self.lbl_option_bytes = QLabel("WRP S0-S2: Protected · RDP: Level 0")
        self.lbl_option_bytes.setStyleSheet("font-size: 11px; color: #38BDF8; font-family: monospace;")
        adv_info_row.addWidget(self.lbl_option_bytes)
        adv_layout.addLayout(adv_info_row)

        # 3. Privileged Remote Bootloader Flash Section
        remote_boot_box = QFrame()
        remote_boot_box.setStyleSheet(
            "background: rgba(185, 28, 28, 0.08); border: 1px solid #7F1D1D; border-radius: 4px;"
        )
        remote_boot_layout = QHBoxLayout(remote_boot_box)
        remote_boot_layout.setContentsMargins(10, 8, 10, 8)
        remote_boot_layout.setSpacing(8)

        warn_icon = QLabel("⚠️")
        warn_icon.setStyleSheet("font-size: 14px;")
        remote_boot_layout.addWidget(warn_icon)

        remote_boot_text = QLabel(
            "FLASH BOOTLOADER REMOTELY: Quy trình đặc quyền cao. "
            "Chỉ chạy khi có xác thực bảo mật và target được bảo vệ. Không bao giờ mass erase."
        )
        remote_boot_text.setStyleSheet("font-size: 11px; color: #FCA5A5;")
        remote_boot_text.setWordWrap(True)
        remote_boot_layout.addWidget(remote_boot_text, 1)

        self.btn_remote_bootloader = QPushButton("Nạp Bootloader từ xa…")
        self.btn_remote_bootloader.setStyleSheet(
            "QPushButton { background: #991B1B; color: white; font-weight: 700; "
            "font-size: 10px; padding: 5px 10px; border-radius: 3px; }"
            "QPushButton:disabled { background: #374151; color: #9CA3AF; }"
        )
        self.btn_remote_bootloader.setToolTip("Yêu cầu quyền quản trị và xác nhận hai bước")
        self.btn_remote_bootloader.clicked.connect(self._on_privileged_remote_bootloader_clicked)
        remote_boot_layout.addWidget(self.btn_remote_bootloader)
        adv_layout.addWidget(remote_boot_box)

        # 4. Diagnostics Log Terminal
        log_header = QLabel("LOG NHẬT KÝ THAO TÁC FLASH")
        log_header.setStyleSheet("font-size: 10px; font-weight: 700; color: #64748B; letter-spacing: 0.5px;")
        adv_layout.addWidget(log_header)

        self.log_terminal = QPlainTextEdit()
        self.log_terminal.setReadOnly(True)
        self.log_terminal.setMaximumHeight(140)
        self.log_terminal.setStyleSheet(
            "background: #090D16; color: #CBD5E1; font-family: monospace; "
            "font-size: 11px; border: 1px solid #1E293B; border-radius: 4px;"
        )
        adv_layout.addWidget(self.log_terminal)

        self.container_layout.addWidget(self.adv_card)

    def _on_programming_mode_toggled(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        is_local = (button_id == 0)
        self.local_panel.setVisible(is_local)
        self.remote_panel.setVisible(not is_local)

    def _browse_app_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file firmware Application",
            str(Path.home()),
            "Firmware Files (*.hex *.bin *.elf);;Intel HEX (*.hex);;Binary (*.bin);;ELF (*.elf);;All (*.*)",
        )
        if path:
            self.set_file_path(Path(path))

    def _browse_remote_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file firmware Application từ xa",
            str(Path.home()),
            "Intel HEX (*.hex);;All (*.*)",
        )
        if path:
            self.remote_fw_edit.setText(path)

    def set_file_path(self, path: Path) -> None:
        self._selected_file = path
        self.app_file_edit.setText(str(path))
        self.remote_fw_edit.setText(str(path))
        try:
            image = inspect_image(path)
            self._current_image = image
            self.app_meta_label.setText(
                f"File: {path.name} | Dung lượng: {image.byte_count} B | "
                f"CRC: 0x{image.crc32:08X} | Entry: 0x{image.entry_point:08X}"
            )
            if hasattr(self.memory_map, "set_image"):
                self.memory_map.set_image(image)
            self.file_selected.emit(path)
        except Exception as exc:
            self.app_meta_label.setText(f"Lỗi đọc file: {exc}")

    def set_probes(self, probes: Sequence[ProbeInfo]) -> None:
        self._probes = list(probes)
        if not self._probes:
            self.lbl_probe.setText("Không tìm thấy ST-Link")
            self.lbl_status.setText("○ Ngắt kết nối")
            self.lbl_status.setStyleSheet("font-weight: 700; color: #EF4444;")
        else:
            p = self._probes[0]
            name = getattr(p, "description", None) or getattr(p, "serial", None) or "ST-Link V2"
            self.lbl_probe.setText(f"{name} ({len(self._probes)} probe)")
            self.lbl_status.setText("● Sẵn sàng")
            self.lbl_status.setStyleSheet("font-weight: 700; color: #10B981;")

    def set_target_info(self, info: Optional[TargetInfo]) -> None:
        self._target_info = info
        if info is None:
            self.lbl_target.setText("STM32F407ZET6")
            self.lbl_option_bytes.setText("Chưa kiểm tra Target")
        else:
            self.lbl_target.setText(f"STM32F407 · {info.flash_kib} KB Flash · {info.target_voltage:.2f}V")
            self.lbl_option_bytes.setText(
                f"WRP: {info.protection_summary} | Target match: {'Đạt' if (info.device_id & 0xFFF) == 0x413 else 'Sai target'}"
            )

    def append_log(self, text: str) -> None:
        self.log_terminal.appendPlainText(text)

    def _on_flash_app_clicked(self) -> None:
        if self._selected_file is None:
            QMessageBox.warning(self, "Chưa chọn firmware", "Vui lòng chọn file firmware Application trước khi nạp.")
            return
        is_dry_run = self.cb_dry_run.isChecked()
        self.flash_application_requested.emit(self._selected_file, is_dry_run)

    def _on_flash_bootloader_clicked(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Xác nhận nạp Bootloader",
            "CẢNH BÁO: Thao tác này sẽ ghi đè Bootloader tại Sector 0–2 (0x08000000..0x0800BFFF).\n"
            "Chỉ thực hiện cho bo mạch mới hoặc bảo trì đặc quyền.\n\n"
            "Bạn có chắc chắn muốn nạp Bootloader không?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.flash_bootloader_requested.emit(True)

    def _on_remote_flash_clicked(self) -> None:
        gw = self.remote_gw_edit.text().strip()
        fw = self.remote_fw_edit.text().strip()
        if not gw or not fw:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập Gateway và chọn file firmware Application.")
            return
        # TODO(v0.18-backend): Connect to feature/v0.18-debug-gateway-core Remote Flash protocol
        # GatewayController.start_remote_flash(gateway=gw, file_path=Path(fw))
        self.append_log(f"Chuẩn bị pipeline Remote Flash tới Gateway {gw} cho file {fw}...")
        self.remote_flash_requested.emit(gw, Path(fw))

    def _on_privileged_remote_bootloader_clicked(self) -> None:
        QMessageBox.warning(
            self,
            "Đặc quyền bị hạn chế",
            "Remote Bootloader Flash là tính năng đặc quyền an toàn nghiêm ngặt.\n"
            "Vui lòng thực hiện trực tiếp qua cổng USB hoặc xác thực console an toàn.\n"
            "B300 từ chối mass erase qua đường truyền từ xa.",
        )


__all__ = ["ProgramView"]
