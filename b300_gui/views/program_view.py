"""Project-driven Application programming with existing guarded action signals."""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional, Sequence
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget, QBoxLayout
from b300_core.hex_image import inspect_image
from b300_core.models import ImageInfo, ProbeInfo, TargetInfo
from b300_core.policy import validate_target_for_provisioning, validate_bootloader_write_protection, SUPPORTED_FLASH_KIB
from ..collapsible_card import CollapsibleCard
from ..widgets.pass_fail_banner import PassFailBanner
from ..widgets.pipeline_stepper import PipelineStepper
from ..widgets.flash_plan_bar import FlashPlanBar
from ..widgets.engineering import SectionCard, ActivityLogPanel


class ProgramView(QWidget):
    flash_application_requested = Signal(Path, bool)
    flash_bootloader_requested = Signal(bool)
    file_selected = Signal(Path)
    file_invalidated = Signal()
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
        outer = QWidget()
        self.container_layout = QVBoxLayout(outer)
        self.container_layout.setContentsMargins(16, 16, 16, 16)
        self.container_layout.setSpacing(12)
        self.banner = PassFailBanner(self)
        self.banner.layout().setContentsMargins(12, 5, 12, 5)
        self.banner.title_label.setObjectName("fieldLabel")
        self.banner.setMaximumHeight(58)
        self.container_layout.addWidget(self.banner)
        self.top_card_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.top_card_layout.setSpacing(12)
        self.top_card_layout.addWidget(self._build_firmware_card(), 1)
        self.top_card_layout.addWidget(self._build_preflight_card(), 1)
        self.container_layout.addLayout(self.top_card_layout)
        self.flash_plan_bar = FlashPlanBar(self)
        self.flash_plan_bar.view_details_requested.connect(self._toggle_advanced_card)
        self.memory_map = self.flash_plan_bar
        self.container_layout.addWidget(self.flash_plan_bar)
        execution = SectionCard("4. Thực hiện nạp", "Tự kiểm tra trước khi xác nhận", self, icon="program")
        execution.body.setContentsMargins(14, 8, 14, 8)
        execution.body.setSpacing(6)
        buttons = QHBoxLayout()
        self.btn_flash_app = self._button("NẠP ỨNG DỤNG", self._on_flash_app_clicked)
        self.btn_flash_app.setObjectName("primaryActionButton")
        self.btn_flash_app.setEnabled(False)
        self.btn_dry_run_action = self._button("CHẠY THỬ", self._on_dry_run_clicked)
        self.btn_dry_run_action.setEnabled(False)
        self.btn_toggle_adv = self._button("Chi tiết / Chế độ nhà máy", self._toggle_advanced_card)
        for button in (self.btn_flash_app, self.btn_dry_run_action, self.btn_toggle_adv):
            buttons.addWidget(button)
        self.btn_flash_app.setMinimumWidth(190)
        for button in (self.btn_flash_app, self.btn_dry_run_action, self.btn_toggle_adv):
            button.setMinimumHeight(40)
        execution.header_layout.addLayout(buttons, 2)
        self.stepper = PipelineStepper(self)
        step_copy = (
            ("ST-Link", "Kết nối đầu dò"),
            ("HEX", "Vùng nhớ hợp lệ"),
            ("Xóa", "Giữ Bootloader"),
            ("Ghi ứng dụng", "Nạp và xác minh"),
            ("Siêu dữ liệu", "STLM đã xác minh"),
            ("Khởi động", "Xác nhận khởi động"),
        )
        for step, (caption, description) in zip(self.stepper._step_widgets, step_copy):
            step.title_text = caption
            step.subtitle_text = description
            step.title_label.setText(caption)
            step.sub_label.setText(description)
            step.sub_label.hide()
            step.setToolTip("%s · %s" % (caption, description))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        execution.body.addWidget(self.progress_bar)
        self._build_advanced_section()
        self.adv_card.content_layout.insertWidget(0, self.stepper)
        self.activity_log = ActivityLogPanel(parent=self)
        self.activity_log.setMaximumHeight(132)
        self.log_terminal = self.activity_log.terminal
        self.btn_clear_log = self.activity_log.clear_button
        self.btn_save_log = self.activity_log.save_button
        self.combo_filter = self.activity_log.filter_combo
        self.container_layout.addWidget(self.activity_log)
        self.container_layout.addStretch(1)
        scroll.setWidget(outer)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(16, 0, 16, 12)
        footer.addWidget(execution)
        root.addLayout(footer)

    @staticmethod
    def _button(text, callback):
        button = QPushButton(text)
        button.setMinimumHeight(32)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _label(text="", name="monoText"):
        label = QLabel(text)
        label.setObjectName(name)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _badge_state(label, state):
        label.setObjectName({"failed": "statusPillDanger", "passed": "statusPillSuccess"}.get(state, "statusPillNeutral"))
        label.style().unpolish(label)
        label.style().polish(label)

    def _build_firmware_card(self):
        card = SectionCard("1. Tệp nạp / Ứng dụng", "HEX ứng dụng từ dự án đang chọn.", self, icon="file")
        card.body.setSpacing(7)
        row = QHBoxLayout()
        self.app_file_edit = self._label("Dự án chưa có HEX ứng dụng")
        self.app_file_edit.setMinimumHeight(34)
        self.app_file_edit.setObjectName("engineeringPathField")
        row.addWidget(self.app_file_edit, 1)
        self.btn_browse_app = self._button("Chọn HEX khác…", self._browse_app_file)
        self.btn_browse_app.setObjectName("ghostButton")
        row.addWidget(self.btn_browse_app)
        card.body.addLayout(row)
        self.badge_file_valid = self._label("Chưa chọn HEX", "mutedLabel")
        self._badge_state(self.badge_file_valid, "unknown")
        card.header_layout.addWidget(self.badge_file_valid)
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, (title, attr) in enumerate((("Kích thước", "meta_size"), ("Dải địa chỉ", "meta_span"), ("CRC32", "meta_crc"), ("SHA256", "meta_sha"))):
            label = self._label("—")
            setattr(self, attr, label)
            grid.addWidget(self._label(title, "mutedLabel"), 0, index)
            grid.addWidget(label, 1, index)
        card.body.addLayout(grid)
        self.app_meta_label = self._label("Chưa chọn HEX ứng dụng", "mutedLabel")
        self.app_meta_label.setParent(self)
        self.app_meta_label.hide()
        card.body.addStretch(1)
        return card

    def _build_preflight_card(self):
        card = SectionCard("2. MCU & Kiểm tra an toàn", "B300 STM32F407 · %d KiB" % SUPPORTED_FLASH_KIB, self, icon="shield")
        card.body.setSpacing(5)
        self.badge_preflight = self._label("Chưa kiểm tra", "mutedLabel")
        self._badge_state(self.badge_preflight, "unknown")
        self.lbl_probe = self._label("Chưa quét ST-Link", "mutedLabel")
        self.lbl_status = self._label("Chờ kết nối", "mutedLabel")
        self.lbl_target = self._label("Chưa đọc MCU")
        self.lbl_target_actual = self.lbl_target
        card.header_layout.addWidget(self.badge_preflight)
        self.lbl_probe.setParent(self)
        self.lbl_status.setParent(self)
        self.lbl_probe.hide()
        self.lbl_status.hide()
        card.body.addWidget(self.lbl_target)
        grid = QGridLayout()
        for index, (title, attr) in enumerate((("Dung lượng bộ nhớ", "lbl_target_flash"), ("WRP S0–S2", "lbl_target_wrp"), ("RDP", "lbl_target_rdp"), ("Siêu dữ liệu OTA", "lbl_target_meta"))):
            label = self._label("Chưa kiểm tra")
            setattr(self, attr, label)
            grid.addWidget(self._label(title, "mutedLabel"), index, 0)
            grid.addWidget(label, index, 1)
        card.body.addLayout(grid)
        card.body.addStretch(1)
        # Explicit diagnostics remain secondary; the primary flash path auto-inspects.
        self.btn_refresh_probe = self._button("Quét ST-Link", self.probe_refresh_requested.emit)
        self.btn_inspect_target = self._button("Kiểm tra MCU", self.target_inspect_requested.emit)
        self.btn_refresh_probe.setParent(self)
        self.btn_inspect_target.setParent(self)
        self.btn_refresh_probe.hide()
        self.btn_inspect_target.hide()
        return card

    def _build_advanced_section(self):
        self.adv_card = CollapsibleCard("Chi tiết & Chế độ nhà máy", "Nạp Bootloader có kiểm soát", self, expanded=False)
        self.adv_card.hide()
        self.adv_card.expanded_changed.connect(self.adv_card.setVisible)
        self.lbl_option_bytes = self._label("WRP S0–S2 / RDP · chưa kiểm tra MCU", "mutedLabel")
        self.adv_card.content_layout.addWidget(self.lbl_option_bytes)
        self.bootloader_card = SectionCard("Bootloader B300", parent=self)
        self.boot_info_label = self._label("Dùng Bootloader chuẩn nhúng sẵn; quy trình yêu cầu xác nhận chế độ nhà máy riêng.", "mutedLabel")
        self.bootloader_card.body.addWidget(self.boot_info_label)
        self.btn_flash_bootloader = self._button("NẠP BOOTLOADER", self._on_flash_bootloader_clicked)
        self.bootloader_card.body.addWidget(self.btn_flash_bootloader)
        self.adv_card.content_layout.addWidget(self.bootloader_card)
        self.container_layout.addWidget(self.adv_card)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.top_card_layout.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 1000 else QBoxLayout.Direction.LeftToRight)

    def _toggle_advanced_card(self) -> None:
        self.adv_card.set_expanded(not self.adv_card.is_expanded())
        self.adv_card.setVisible(self.adv_card.is_expanded())

    def _clear_log(self) -> None:
        self.activity_log.clear()

    def _save_log(self) -> None:
        self.activity_log.save()

    def _on_dry_run_clicked(self) -> None:
        if self._selected_file is None:
            return
        self.flash_application_requested.emit(self._selected_file, True)

    def _on_flash_app_clicked(self) -> None:
        if self._selected_file is None:
            return
        self.flash_application_requested.emit(self._selected_file, False)

    def _on_flash_bootloader_clicked(self) -> None:
        self.flash_bootloader_requested.emit(True)

    def _browse_app_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Chọn Intel HEX ứng dụng", str(Path.home()),
            "Intel HEX (*.hex);;Tất cả tệp (*)",
        )
        if selected:
            self.set_file_path(Path(selected))

    def clear_project_file(self):
        self.btn_flash_app.setEnabled(False)
        self.btn_dry_run_action.setEnabled(False)
        self._selected_file = None
        self._current_image = None
        self.app_file_edit.setText("Dự án chưa có HEX ứng dụng")
        self.app_meta_label.setText("Dự án chưa có HEX ứng dụng; chọn trong CÀI ĐẶT hoặc dùng Chọn HEX khác.")
        self.badge_file_valid.setText("Chưa chọn HEX")
        self._badge_state(self.badge_file_valid, "unknown")
        for label in (self.meta_size, self.meta_span, self.meta_crc, self.meta_sha):
            label.setText("—")
        self.flash_plan_bar.reset_app_span()
        self.file_invalidated.emit()

    def set_file_path(self, path: Path) -> None:
        selected = Path(path).expanduser()
        self._selected_file = None
        self._current_image = None
        self.btn_flash_app.setEnabled(False)
        self.app_file_edit.setText(str(selected))
        self.file_invalidated.emit()
        self.btn_dry_run_action.setEnabled(False)
        for label in (self.meta_size, self.meta_span, self.meta_crc, self.meta_sha):
            label.setText("—")
        self.flash_plan_bar.reset_app_span()
        if selected.suffix.lower() != ".hex":
            self.app_meta_label.setText(
                "Không hỗ trợ định dạng này trong đường nạp an toàn hiện tại. Hãy chọn Intel HEX (.hex)."
            )
            self.badge_file_valid.setText("✕ Định dạng không hỗ trợ")
            self._badge_state(self.badge_file_valid, "failed")
            self.meta_size.setText("—")
            self.meta_span.setText("—")
            self.meta_crc.setText("—")
            self.meta_sha.setText("—")
            return
        try:
            image = inspect_image(selected)
        except Exception as error:
            self.app_meta_label.setText("HEX không hợp lệ / không an toàn: %s" % error)
            self.badge_file_valid.setText("✕ HEX không hợp lệ")
            self._badge_state(self.badge_file_valid, "failed")
            return
        self._selected_file = selected
        self._current_image = image
        crc = "0x%08X" % image.flash_crc32 if image.flash_crc32 is not None else "không có"
        reset = "0x%08X" % image.reset_vector if image.reset_vector is not None else "không có"
        self.app_meta_label.setText(
            "%s · %d B · 0x%08X..0x%08X · CRC %s · Điểm khởi động lại %s · SHA256 %s…"
            % (
                selected.name, image.size, image.start_address, image.end_address,
                crc, reset, image.sha256[:12],
            )
        )
        self.badge_file_valid.setText("✓ Tệp hợp lệ")
        self._badge_state(self.badge_file_valid, "passed")

        self.meta_size.setText("%.1f KB (%d B)" % (image.size / 1024.0, image.size))
        self.meta_span.setText("0x%08X - 0x%08X" % (image.start_address, image.end_address))
        self.meta_crc.setText(crc)
        self.meta_sha.setText("%s…" % image.sha256[:16])

        self.flash_plan_bar.update_app_span(image.start_address, image.end_address, image.size)
        if hasattr(self.memory_map, "set_image"):
            self.memory_map.set_image(image)
        self.btn_flash_app.setEnabled(not self._busy)
        self.btn_dry_run_action.setEnabled(not self._busy)
        self.file_selected.emit(selected)

    def set_probes(self, probes: Sequence[ProbeInfo], selected_serial: Optional[str] = None) -> None:
        self._probes = list(probes)
        if not self._probes:
            self.lbl_probe.setText("Không tìm thấy ST-Link")
            self.lbl_status.setText("○ MẤT KẾT NỐI")
            return
        if len(self._probes) > 1 and selected_serial is None:
            self.lbl_probe.setText("Chọn ST-Link theo số sê-ri ở thanh trên")
            self.lbl_status.setText("Chưa chọn đầu dò")
            return
        probe = next((item for item in self._probes if item.serial == selected_serial), self._probes[0])
        serial = probe.serial or "tự chọn"
        self.lbl_probe.setText("%s · %s%s" % (
            probe.name, serial, " · +%d" % (len(self._probes) - 1) if len(self._probes) > 1 else ""
        ))
        self.lbl_status.setText("● ĐẦU DÒ SẴN SÀNG")

    def set_target_info(self, info: Optional[TargetInfo]) -> None:
        self._target_info = info
        if info is None:
            self.lbl_target.setText("Chưa đọc MCU")
            self.lbl_option_bytes.setText("WRP S0–S2 / RDP · chưa kiểm tra MCU")
            self.badge_preflight.setText("● Chưa kiểm tra")
            self._badge_state(self.badge_preflight, "unknown")
            self.badge_preflight.setToolTip("")
            self.lbl_target_actual.setText("Chưa đọc MCU")
            self.lbl_target_flash.setText("Chưa kiểm tra")
            self.lbl_target_wrp.setText("Chưa kiểm tra")
            self.lbl_target_rdp.setText("Chưa kiểm tra")
            return
        self.lbl_target.setText(
            "%s · %d KiB bộ nhớ · %.2f V" % (
                "STM32F407" if info.device_id & 0xFFF == 0x413 else "STM32 ID 0x%03X" % (info.device_id & 0xFFF),
                info.flash_kib, info.target_voltage,
            )
        )
        protected = {0, 1, 2}.issubset(set(info.protected_sectors)) if info.protection_reported else False
        wrp_summary = "đã bảo vệ S0–S2" if protected else "chưa bảo vệ đủ S0–S2" if info.protection_reported else "chưa kiểm tra"
        rdp_summary = "đã khóa đọc" if info.readout_protected else "Mức 0"
        self.lbl_option_bytes.setText("WRP: %s · RDP: %s" % (wrp_summary, rdp_summary))
        try:
            validate_target_for_provisioning(info)
            validate_bootloader_write_protection(info)
        except ValueError as error:
            self.badge_preflight.setText("⚠ Không đạt")
            self._badge_state(self.badge_preflight, "failed")
            self.badge_preflight.setToolTip(str(error))
        else:
            self.badge_preflight.setText("✓ MCU đã kiểm tra")
            self._badge_state(self.badge_preflight, "passed")
            self.badge_preflight.setToolTip("Mỗi lần nạp vẫn kiểm tra lại MCU và tệp HEX.")
        self.lbl_target_flash.setText("%d KiB" % info.flash_kib)
        wrp_ok = protected
        self.lbl_target_wrp.setText("Chưa kiểm tra" if not info.protection_reported else
                                    "Đã bảo vệ S0–S2" if wrp_ok else "Chưa bảo vệ đủ S0–S2")
        self.lbl_target_rdp.setText("Mức 0 (không bảo vệ)" if not info.readout_protected else "Đã khóa RDP")

    def append_log(self, text: str) -> None:
        self.activity_log.append(str(text))

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.btn_browse_app.setEnabled(not busy)
        self.btn_flash_app.setEnabled(not busy and self._selected_file is not None)
        self.btn_dry_run_action.setEnabled(not busy and self._selected_file is not None)
        self.btn_flash_bootloader.setEnabled(not busy)
        self.btn_refresh_probe.setEnabled(not busy)
        self.btn_inspect_target.setEnabled(not busy)


__all__ = ["ProgramView"]
