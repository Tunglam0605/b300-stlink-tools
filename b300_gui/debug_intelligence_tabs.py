"""Compact Target Awareness panes for the engineering Debug Workstation."""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class TargetSummaryPane(QWidget):
    refresh_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("TARGET · STM32 / Cortex-M"))
        top.addStretch(1)
        self.refresh_button = QPushButton("Làm mới")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setPlaceholderText("Kết nối Debug tương tác để xem Target Description.")
        layout.addWidget(self.summary, 1)

    def set_summary(self, values: dict) -> None:
        caps = values.get("capabilities", {})
        enabled = ", ".join(key.upper() for key, value in caps.items() if value) or "—"
        self.summary.setPlainText(
            "Part: {part}\nCore: {core}\nFamily: {family}\nVendor: {vendor}\n"
            "Flash: {flash} KiB\nHW breakpoints: {bp}\nHW watchpoints: {wp}\n"
            "Capabilities: {caps}".format(
                part=values.get("part", "—"), core=values.get("core", "—"),
                family=values.get("family", "—"), vendor=values.get("vendor", "—"),
                flash=int(values.get("flash_bytes", 0) or 0) // 1024,
                bp=values.get("breakpoints", "—"), wp=values.get("watchpoints", "—"), caps=enabled,
            )
        )

    def set_error(self, message: str) -> None:
        self.summary.setPlainText("TARGET ERROR\n" + str(message))


class PeripheralInspectorPane(QWidget):
    load_svd_requested = Signal()
    inspect_requested = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.svd_path = QLineEdit()
        self.svd_path.setReadOnly(True)
        self.svd_path.setPlaceholderText("Chưa nạp CMSIS-SVD")
        self.load_button = QPushButton("Nạp SVD…")
        self.load_button.clicked.connect(self.load_svd_requested.emit)
        top.addWidget(self.svd_path, 1)
        top.addWidget(self.load_button)
        layout.addLayout(top)

        chooser = QHBoxLayout()
        chooser.addWidget(QLabel("Peripheral"))
        self.peripheral_combo = QComboBox()
        chooser.addWidget(self.peripheral_combo, 1)
        chooser.addWidget(QLabel("Register"))
        self.register_combo = QComboBox()
        chooser.addWidget(self.register_combo, 1)
        self.inspect_button = QPushButton("Đọc register")
        chooser.addWidget(self.inspect_button)
        layout.addLayout(chooser)
        self.peripheral_combo.currentTextChanged.connect(self._peripheral_changed)
        self.inspect_button.clicked.connect(self._emit_inspect)

        self.status = QLabel("HALT-only · read-only · lazy register read")
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Field", "Value", "Bit", "Width"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self._register_map = {}

    def set_device(self, path: str, peripherals: Iterable) -> None:
        self.svd_path.setText(path)
        self._register_map = {item.name: tuple(item.registers) for item in peripherals}
        self.peripheral_combo.blockSignals(True)
        self.peripheral_combo.clear()
        self.peripheral_combo.addItems(sorted(self._register_map))
        self.peripheral_combo.blockSignals(False)
        self._peripheral_changed(self.peripheral_combo.currentText())
        self.status.setText("SVD READY · chọn register rồi đọc khi MCU HALT")

    def _peripheral_changed(self, name: str) -> None:
        self.register_combo.clear()
        self.register_combo.addItems([item.name for item in self._register_map.get(name, ())])

    def _emit_inspect(self) -> None:
        peripheral = self.peripheral_combo.currentText().strip()
        register = self.register_combo.currentText().strip()
        if peripheral and register:
            self.inspect_requested.emit(peripheral, register)

    def set_snapshot(self, snapshot) -> None:
        self.status.setText(
            "%s.%s @ 0x%08X = 0x%0*X" % (
                snapshot.peripheral, snapshot.register, snapshot.address,
                max(2, snapshot.size_bits // 4), snapshot.raw_value,
            )
        )
        self.table.setRowCount(len(snapshot.fields))
        for row, field in enumerate(snapshot.fields):
            for col, value in enumerate((field.name, "0x%X" % field.value, field.bit_offset, field.bit_width)):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))

    def set_error(self, message: str) -> None:
        self.status.setText("PERIPHERAL ERROR · " + str(message))


class FreeRtosPane(QWidget):
    refresh_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("FreeRTOS Task Inspector · DWARF-driven"))
        top.addStretch(1)
        self.refresh_button = QPushButton("Đọc task")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)
        self.status = QLabel("HALT-only · không hard-code TCB/List offset")
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Task", "State", "Priority", "Base", "TCB", "SP", "Stack start",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

    @staticmethod
    def _address(value) -> str:
        return "—" if value is None else "0x%08X" % int(value)

    def set_snapshot(self, snapshot) -> None:
        self.table.setRowCount(len(snapshot.tasks))
        for row, task in enumerate(snapshot.tasks):
            values = (
                task.name, task.state, task.priority,
                "—" if task.base_priority is None else task.base_priority,
                self._address(task.tcb_address), self._address(task.stack_pointer),
                self._address(task.stack_start),
            )
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))
        declared = "?" if snapshot.declared_task_count is None else str(snapshot.declared_task_count)
        text = "Tasks %d/%s" % (len(snapshot.tasks), declared)
        if snapshot.limited_reason:
            text += " · PARTIAL · " + snapshot.limited_reason
        else:
            text += " · COMPLETE"
        self.status.setText(text)

    def set_error(self, message: str) -> None:
        self.status.setText("FREERTOS ERROR · " + str(message))


class FaultAnalysisPane(QWidget):
    analyze_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Cortex-M Fault Analyzer"))
        top.addStretch(1)
        self.analyze_button = QPushButton("Phân tích fault")
        self.analyze_button.clicked.connect(self.analyze_requested.emit)
        top.addWidget(self.analyze_button)
        layout.addLayout(top)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("MCU phải HALT. Analyzer chỉ đọc SCB/register/exception frame.")
        layout.addWidget(self.output, 1)

    def set_analysis(self, analysis, *, region=None, source=None) -> None:
        lines = [
            "CFSR=0x%08X  HFSR=0x%08X" % (analysis.cfsr, analysis.hfsr),
            "DFSR=0x%08X  AFSR=0x%08X  SHCSR=0x%08X" % (analysis.dfsr, analysis.afsr, analysis.shcsr),
        ]
        if analysis.fault_address is not None:
            lines.append(
                "Fault address: 0x%08X (%s)%s" % (
                    analysis.fault_address, analysis.fault_address_source or "?",
                    " · " + region if region else "",
                )
            )
        if analysis.flags:
            lines.append("Flags:")
            lines.extend("  - %s/%s: %s" % (item.group, item.name, item.description) for item in analysis.flags)
        else:
            lines.append("Flags: none decoded")
        frame = analysis.exception_frame
        if frame is not None:
            lines.append(
                "Exception frame: %s SP=0x%08X PC=0x%08X LR=0x%08X xPSR=0x%08X" % (
                    "PSP" if frame.used_psp else "MSP", frame.stack_pointer, frame.pc, frame.lr, frame.xpsr,
                )
            )
            if source is not None:
                location = "%s:%s" % (source.file or "?", source.line or "?")
                lines.append("Fault PC source: %s · %s" % (source.function or "?", location))
        self.output.setPlainText("\n".join(lines))

    def set_error(self, message: str) -> None:
        self.output.setPlainText("FAULT ANALYZER ERROR\n" + str(message))
