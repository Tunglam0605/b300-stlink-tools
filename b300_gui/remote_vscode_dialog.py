"""Small GUI for exporting a safe VSCode remote-debug kit."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

from b300_core.models import ProbeRef
from b300_core.remote_vscode import RemoteVsCodeProfile, workspace_executable


class RemoteVsCodeDialog(QDialog):
    def __init__(self, selected_probe: Callable[[], ProbeRef], parent=None) -> None:
        super().__init__(parent)
        self.selected_probe = selected_probe
        self.setWindowTitle("B300 · VSCode Remote Debug")
        self.setMinimumWidth(720)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Gateway vẫn giữ OpenOCD ở 127.0.0.1. Máy VSCode kết nối qua SSH tunnel; "
            "không mở trực tiếp GDB/TCL ra LAN hoặc Internet."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("Ví dụ: 192.168.1.109 hoặc b300-gateway.local")
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("Ví dụ: automation")
        self.ssh_port = QSpinBox()
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(22)
        self.local_gdb_port = QSpinBox()
        self.local_gdb_port.setRange(1, 65535)
        self.local_gdb_port.setValue(3333)
        self.program_edit = QLineEdit("Objects/F407/Main_V2_F407.axf")
        self.program_edit.setToolTip("Đường dẫn AXF/ELF tương đối bên trong workspace được mở trên máy VSCode.")
        self.gdb_edit = QLineEdit("arm-none-eabi-gdb")
        self.gdb_edit.setToolTip("GDB phải có trên máy chạy VSCode; gateway không cần GDB.")
        form.addRow("Gateway host:", self.host_edit)
        form.addRow("SSH user:", self.user_edit)
        form.addRow("SSH port:", self.ssh_port)
        form.addRow("Local GDB port:", self.local_gdb_port)
        form.addRow("AXF/ELF trong workspace:", self.program_edit)
        form.addRow("GDB trên máy VSCode:", self.gdb_edit)
        root.addLayout(form)

        self.preview = QLabel("Nhập gateway host/user để xem lệnh tunnel.")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(self.preview.textInteractionFlags())
        root.addWidget(self.preview)

        buttons = QHBoxLayout()
        self.export_button = QPushButton("Xuất VSCode Remote Kit...")
        self.close_button = QPushButton("Đóng")
        self.export_button.clicked.connect(self.export_kit)
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.export_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        root.addLayout(buttons)

        for widget in (self.host_edit, self.user_edit, self.program_edit, self.gdb_edit):
            widget.textChanged.connect(self.refresh_preview)
        self.ssh_port.valueChanged.connect(self.refresh_preview)
        self.local_gdb_port.valueChanged.connect(self.refresh_preview)

    def build_profile(self) -> RemoteVsCodeProfile:
        probe_serial: Optional[str] = None
        try:
            probe = self.selected_probe()
            probe_serial = getattr(probe, "serial", None) if probe is not None else None
        except Exception:
            probe_serial = None
        return RemoteVsCodeProfile(
            ssh_host=self.host_edit.text().strip(),
            ssh_user=self.user_edit.text().strip(),
            ssh_port=self.ssh_port.value(),
            local_gdb_port=self.local_gdb_port.value(),
            remote_gdb_port=3333,
            executable=workspace_executable(self.program_edit.text().strip()),
            gdb_path=self.gdb_edit.text().strip(),
            probe_serial=probe_serial,
        )

    def refresh_preview(self) -> None:
        try:
            profile = self.build_profile()
            profile.validate()
        except Exception as error:
            self.preview.setText("Chưa đủ cấu hình: %s" % error)
            return
        self.preview.setText(
            "Gateway: %s\nSSH tunnel: %s" %
            (profile.gateway_command(), profile.tunnel_command())
        )

    def export_kit(self) -> None:
        try:
            profile = self.build_profile()
            profile.validate()
        except Exception as error:
            QMessageBox.warning(self, "Cấu hình Remote Debug chưa hợp lệ", str(error))
            return
        directory = QFileDialog.getExistingDirectory(
            self, "Chọn workspace VSCode để xuất Remote Debug Kit", ""
        )
        if not directory:
            return
        try:
            outputs = profile.write_kit(Path(directory), force=False)
        except FileExistsError as error:
            QMessageBox.warning(
                self,
                "Không ghi đè cấu hình hiện có",
                "%s\n\nTool fail-closed để không phá launch.json hiện tại. "
                "Hãy backup/review file cũ trước khi thay thế." % error,
            )
            return
        except Exception as error:
            QMessageBox.warning(self, "Không thể xuất Remote Debug Kit", str(error))
            return
        QMessageBox.information(
            self,
            "Đã tạo VSCode Remote Debug Kit",
            "Đã tạo %d file trong:\n%s\n\nMở B300-REMOTE-DEBUG.md để làm theo checklist." %
            (len(outputs), Path(directory).resolve()),
        )
