"""Operator-oriented PROGRAM page for B300 v0.18.

The page deliberately exposes the proven local Application/Bootloader paths and
keeps remote programming visibly marked as a foundation until an authenticated
end-to-end transfer protocol is available.  Application selection uses the same
strict Intel-HEX parser as the backend so unsupported BIN/ELF files can never
look ready in the GUI.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
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
    flash_application_requested = Signal(Path, bool)
    flash_bootloader_requested = Signal(bool)
    remote_flash_requested = Signal(str, Path)  # reserved for future transport
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
        content = QWidget()
        self.container_layout = QVBoxLayout(content)
        self.container_layout.setContentsMargins(16, 12, 16, 14)
        self.container_layout.setSpacing(10)
        self._build_ui()
        scroll.setWidget(content)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _build_ui(self) -> None:
        self.banner = PassFailBanner(self)
        self.banner.hide()
        self.container_layout.addWidget(self.banner)
        self._build_device_card()
        self._build_firmware_card()
        self.stepper = PipelineStepper(self)
        self.container_layout.addWidget(self.stepper)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.hide()
        self.container_layout.addWidget(self.progress_bar)
        self._build_advanced_section()

    def _build_device_card(self) -> None:
        card = QFrame()
        card.setObjectName("cardSurface")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel("DEVICE · ST-LINK & TARGET MCU")
        title.setObjectName("eyebrowLabel")
        header.addWidget(title)
        header.addStretch(1)
        self.btn_refresh_probe = QPushButton("↻ Quét ST-Link")
        self.btn_refresh_probe.clicked.connect(self.probe_refresh_requested.emit)
        self.btn_inspect_target = QPushButton("🔍 Kiểm tra Target")
        self.btn_inspect_target.clicked.connect(self.target_inspect_requested.emit)
        header.addWidget(self.btn_refresh_probe)
        header.addWidget(self.btn_inspect_target)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.addWidget(QLabel("Probe"), 0, 0)
        self.lbl_probe = QLabel("ST-Link · đang quét…")
        grid.addWidget(self.lbl_probe, 0, 1)
        grid.addWidget(QLabel("Target"), 0, 2)
        self.lbl_target = QLabel("STM32F407ZET6")
        grid.addWidget(self.lbl_target, 0, 3)
        grid.addWidget(QLabel("Status"), 0, 4)
        self.lbl_status = QLabel("● Chờ kết nối")
        grid.addWidget(self.lbl_status, 0, 5)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)
        self.container_layout.addWidget(card)

    def _build_firmware_card(self) -> None:
        card = QFrame()
        card.setObjectName("cardSurface")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        modes = QHBoxLayout()
        title = QLabel("FIRMWARE PROGRAMMING")
        title.setObjectName("eyebrowLabel")
        modes.addWidget(title)
        modes.addStretch(1)
        self.mode_group = QButtonGroup(self)
        self.radio_local = QRadioButton("Local ST-Link (USB)")
        self.radio_remote = QRadioButton("Remote Gateway · Foundation")
        self.radio_local.setChecked(True)
        self.mode_group.addButton(self.radio_local, 0)
        self.mode_group.addButton(self.radio_remote, 1)
        modes.addWidget(self.radio_local)
        modes.addWidget(self.radio_remote)
        self.mode_group.idToggled.connect(self._on_programming_mode_toggled)
        layout.addLayout(modes)

        self.local_panel = QWidget()
        local = QVBoxLayout(self.local_panel)
        local.setContentsMargins(0, 0, 0, 0)
        local.setSpacing(8)

        app = QFrame()
        app.setObjectName("nestedCard")
        app_layout = QVBoxLayout(app)
        app_layout.setContentsMargins(12, 10, 12, 10)
        app_header = QHBoxLayout()
        app_header.addWidget(QLabel("Application Firmware"))
        hint = QLabel("Sector 4–7 · start 0x08010000 · Intel HEX an toàn")
        hint.setObjectName("mutedLabel")
        app_header.addWidget(hint)
        app_header.addStretch(1)
        app_layout.addLayout(app_header)
        file_row = QHBoxLayout()
        self.app_file_edit = QLineEdit()
        self.app_file_edit.setReadOnly(True)
        self.app_file_edit.setPlaceholderText("Chọn Application Intel HEX (.hex)")
        self.btn_browse_app = QPushButton("📁 Chọn HEX…")
        self.btn_browse_app.clicked.connect(self._browse_app_file)
        file_row.addWidget(self.app_file_edit, 1)
        file_row.addWidget(self.btn_browse_app)
        app_layout.addLayout(file_row)
        self.app_meta_label = QLabel("Chưa chọn Application HEX")
        self.app_meta_label.setWordWrap(True)
        self.app_meta_label.setObjectName("mutedLabel")
        app_layout.addWidget(self.app_meta_label)
        action = QHBoxLayout()
        self.cb_dry_run = QCheckBox("Dry-run · kiểm tra, không ghi Flash")
        action.addWidget(self.cb_dry_run)
        action.addStretch(1)
        self.btn_flash_app = QPushButton("⚡ NẠP APPLICATION")
        self.btn_flash_app.setObjectName("primaryActionButton")
        self.btn_flash_app.setEnabled(False)
        self.btn_flash_app.clicked.connect(self._on_flash_app_clicked)
        action.addWidget(self.btn_flash_app)
        app_layout.addLayout(action)
        local.addWidget(app)

        boot = QFrame()
        boot.setObjectName("nestedCard")
        boot_layout = QHBoxLayout(boot)
        boot_text = QVBoxLayout()
        boot_text.addWidget(QLabel("Bootloader B300 · Factory / Maintenance"))
        self.boot_info_label = QLabel(
            "Dùng trusted bundled Bootloader; đường dẫn này có xác nhận đặc quyền và không mass erase."
        )
        self.boot_info_label.setWordWrap(True)
        self.boot_info_label.setObjectName("mutedLabel")
        boot_text.addWidget(self.boot_info_label)
        boot_layout.addLayout(boot_text, 1)
        self.btn_flash_bootloader = QPushButton("🛡 NẠP BOOTLOADER")
        self.btn_flash_bootloader.clicked.connect(self._on_flash_bootloader_clicked)
        boot_layout.addWidget(self.btn_flash_bootloader)
        local.addWidget(boot)
        layout.addWidget(self.local_panel)

        self.remote_panel = QWidget()
        remote = QVBoxLayout(self.remote_panel)
        remote.setContentsMargins(0, 0, 0, 0)
        remote_box = QFrame()
        remote_box.setObjectName("nestedCard")
        remote_layout = QVBoxLayout(remote_box)
        remote_title = QLabel("Remote Application Programming · SAFETY FOUNDATION")
        remote_layout.addWidget(remote_title)
        remote_note = QLabel(
            "Backend đã có manifest/SHA-256/safety validation, nhưng Client→Gateway file transfer và "
            "network execution chưa được bật trong v0.18 integration. Nút nạp được khóa để tránh "
            "tạo cảm giác thao tác đã thực thi khi transport chưa hoàn chỉnh."
        )
        remote_note.setWordWrap(True)
        remote_layout.addWidget(remote_note)
        form = QGridLayout()
        self.remote_gw_edit = QLineEdit()
        self.remote_gw_edit.setPlaceholderText("Gateway host (dùng ở bản transport sau)")
        self.remote_fw_edit = QLineEdit()
        self.remote_fw_edit.setReadOnly(True)
        browse = QPushButton("📁 Chọn HEX…")
        browse.clicked.connect(self._browse_remote_file)
        form.addWidget(QLabel("Gateway"), 0, 0)
        form.addWidget(self.remote_gw_edit, 0, 1, 1, 2)
        form.addWidget(QLabel("Firmware"), 1, 0)
        form.addWidget(self.remote_fw_edit, 1, 1)
        form.addWidget(browse, 1, 2)
        remote_layout.addLayout(form)
        pipeline = QHBoxLayout()
        self.pipeline_labels = []
        for index, step in enumerate(("Upload", "Validate", "Gateway", "ST-Link", "Verify")):
            label = QLabel("[%d] %s" % (index + 1, step))
            self.pipeline_labels.append(label)
            pipeline.addWidget(label)
            if index < 4:
                pipeline.addWidget(QLabel("→"))
        pipeline.addStretch(1)
        remote_layout.addLayout(pipeline)
        remote_actions = QHBoxLayout()
        remote_actions.addStretch(1)
        self.btn_remote_flash = QPushButton("🔒 REMOTE FLASH · CHƯA BẬT")
        self.btn_remote_flash.setEnabled(False)
        self.btn_remote_flash.setToolTip("Chờ authenticated Client→Gateway transfer protocol hoàn thiện.")
        remote_actions.addWidget(self.btn_remote_flash)
        remote_layout.addLayout(remote_actions)
        remote.addWidget(remote_box)
        self.remote_panel.hide()
        layout.addWidget(self.remote_panel)
        self.container_layout.addWidget(card)

    def _build_advanced_section(self) -> None:
        self.adv_card = CollapsibleCard(
            "Nâng cao / Diagnostics",
            "Memory map, metadata, Option Bytes và log chi tiết",
            expanded=False,
            parent=self,
        )
        advanced = self.adv_card.content_layout
        self.memory_map = MemoryMapWidget(self)
        advanced.addWidget(self.memory_map)
        info = QHBoxLayout()
        self.lbl_metadata_info = QLabel("Metadata · 0x0800C000 · chưa đọc")
        self.lbl_option_bytes = QLabel("WRP S0–S2 / RDP · chưa kiểm tra Target")
        info.addWidget(self.lbl_metadata_info, 1)
        info.addWidget(self.lbl_option_bytes, 1)
        advanced.addLayout(info)

        privileged = QFrame()
        privileged.setObjectName("nestedCard")
        privileged_layout = QHBoxLayout(privileged)
        text = QLabel(
            "Remote Bootloader programming không được mở ở v0.18. Local trusted factory provisioning "
            "vẫn là đường duy nhất được hỗ trợ."
        )
        text.setWordWrap(True)
        privileged_layout.addWidget(text, 1)
        self.btn_remote_bootloader = QPushButton("🔒 Remote Bootloader")
        self.btn_remote_bootloader.setEnabled(False)
        privileged_layout.addWidget(self.btn_remote_bootloader)
        advanced.addWidget(privileged)

        self.log_terminal = QPlainTextEdit()
        self.log_terminal.setReadOnly(True)
        self.log_terminal.setMaximumHeight(140)
        advanced.addWidget(self.log_terminal)
        self.container_layout.addWidget(self.adv_card)

    def _on_programming_mode_toggled(self, button_id: int, checked: bool) -> None:
        if checked:
            local = button_id == 0
            self.local_panel.setVisible(local)
            self.remote_panel.setVisible(not local)

    def _browse_app_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Chọn Application Intel HEX", str(Path.home()),
            "Intel HEX (*.hex);;All files (*)",
        )
        if selected:
            self.set_file_path(Path(selected))

    def _browse_remote_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Chọn Application Intel HEX", str(Path.home()), "Intel HEX (*.hex)"
        )
        if selected:
            self.remote_fw_edit.setText(selected)

    def set_file_path(self, path: Path) -> None:
        selected = Path(path).expanduser()
        self._selected_file = None
        self._current_image = None
        self.btn_flash_app.setEnabled(False)
        self.app_file_edit.setText(str(selected))
        self.remote_fw_edit.setText(str(selected))
        if selected.suffix.lower() != ".hex":
            self.app_meta_label.setText(
                "Không hỗ trợ định dạng này trong đường nạp an toàn hiện tại. Hãy chọn Intel HEX (.hex)."
            )
            return
        try:
            image = inspect_image(selected)
        except Exception as error:
            self.app_meta_label.setText("HEX không hợp lệ / không an toàn: %s" % error)
            return
        self._selected_file = selected
        self._current_image = image
        crc = "0x%08X" % image.flash_crc32 if image.flash_crc32 is not None else "n/a"
        reset = "0x%08X" % image.reset_vector if image.reset_vector is not None else "n/a"
        self.app_meta_label.setText(
            "%s · %d B · 0x%08X..0x%08X · CRC %s · Reset %s · SHA256 %s…"
            % (
                selected.name, image.size, image.start_address, image.end_address,
                crc, reset, image.sha256[:12],
            )
        )
        if hasattr(self.memory_map, "set_image"):
            self.memory_map.set_image(image)
        self.btn_flash_app.setEnabled(True)
        self.file_selected.emit(selected)

    def set_probes(self, probes: Sequence[ProbeInfo]) -> None:
        self._probes = list(probes)
        if not self._probes:
            self.lbl_probe.setText("Không tìm thấy ST-Link")
            self.lbl_status.setText("○ DISCONNECTED")
            return
        probe = self._probes[0]
        serial = probe.serial or "auto-select"
        self.lbl_probe.setText("%s · %s%s" % (
            probe.name, serial, " · +%d" % (len(self._probes) - 1) if len(self._probes) > 1 else ""
        ))
        self.lbl_status.setText("● PROBE READY")

    def set_target_info(self, info: Optional[TargetInfo]) -> None:
        self._target_info = info
        if info is None:
            self.lbl_target.setText("STM32F407ZET6")
            self.lbl_option_bytes.setText("WRP S0–S2 / RDP · chưa kiểm tra Target")
            return
        self.lbl_target.setText(
            "STM32F407 · %d KB Flash · %.2fV" % (info.flash_kib, info.target_voltage)
        )
        self.lbl_option_bytes.setText(
            "WRP %s · RDP %s" %
            (info.protection_summary, "protected" if info.readout_protected else "Level 0")
        )

    def append_log(self, text: str) -> None:
        self.log_terminal.appendPlainText(str(text))

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.btn_browse_app.setEnabled(not busy)
        self.btn_flash_app.setEnabled(not busy and self._selected_file is not None)
        self.btn_flash_bootloader.setEnabled(not busy)
        self.btn_refresh_probe.setEnabled(not busy)
        self.btn_inspect_target.setEnabled(not busy)

    def _on_flash_app_clicked(self) -> None:
        if self._selected_file is None:
            QMessageBox.warning(self, "Firmware chưa hợp lệ", "Hãy chọn Application Intel HEX hợp lệ trước.")
            return
        self.flash_application_requested.emit(self._selected_file, self.cb_dry_run.isChecked())

    def _on_flash_bootloader_clicked(self) -> None:
        answer = QMessageBox.question(
            self,
            "Xác nhận Factory Bootloader Provisioning",
            "Thao tác đặc quyền này ghi trusted Bootloader vào Sector 0–2.\n"
            "Chỉ dùng cho bo mới hoặc bảo trì được ủy quyền. B300 không mass erase.\n\nTiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.flash_bootloader_requested.emit(True)


__all__ = ["ProgramView"]
