"""v0.16 Target Awareness integration for the engineering Debug Workstation."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog

from b300_core.debug_types import DwarfTypeService
from b300_core.rtos import FreeRtosInspector
from b300_core.target_awareness import TargetAwarenessFacade

from .debug_intelligence_tabs import (
    FaultAnalysisPane,
    FreeRtosPane,
    PeripheralInspectorPane,
    TargetSummaryPane,
)
from .debug_tab_v152 import DebugTabV152


class DebugTabV160(DebugTabV152):
    """Add read-only Target/SVD/FreeRTOS/Fault intelligence to v0.15.2 lifecycle."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._v160_facade = None
        self._v160_dwarf = None
        self._v160_dwarf_path = None
        self._install_intelligence_tabs()

    def _install_intelligence_tabs(self) -> None:
        tabs = self.workstation.bottom_tabs
        self.target_awareness_pane = TargetSummaryPane(tabs)
        self.peripheral_pane = PeripheralInspectorPane(tabs)
        self.freertos_pane = FreeRtosPane(tabs)
        self.fault_pane = FaultAnalysisPane(tabs)
        tabs.addTab(self.target_awareness_pane, "TARGET")
        tabs.addTab(self.peripheral_pane, "PERIPHERALS")
        tabs.addTab(self.freertos_pane, "FREERTOS")
        tabs.addTab(self.fault_pane, "FAULT")

        self.target_awareness_pane.refresh_requested.connect(self._v160_refresh_target)
        self.peripheral_pane.load_svd_requested.connect(self._v160_choose_svd)
        self.peripheral_pane.inspect_requested.connect(self._v160_inspect_register)
        self.freertos_pane.refresh_requested.connect(self._v160_refresh_freertos)
        self.fault_pane.analyze_requested.connect(self._v160_analyze_fault)

    def _v160_reset_services(self) -> None:
        self._v160_facade = None
        self._v160_dwarf = None
        self._v160_dwarf_path = None

    def _v160_require_facade(self) -> TargetAwarenessFacade:
        controller = self._workstation_controller
        if not controller.interactive_active or controller.workspace is None or controller.memory_backend is None:
            raise RuntimeError("Kết nối Debug tương tác trước khi dùng Target Awareness.")
        if self._v160_facade is None:
            self._v160_facade = TargetAwarenessFacade(controller.workspace, controller.memory_backend)
        return self._v160_facade

    def _v160_symbol_file(self) -> Path:
        text = self.symbol_path.text().strip()
        if not text:
            raise RuntimeError("FreeRTOS/source mapping cần AXF/ELF đã xác minh.")
        path = Path(text).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in {".axf", ".elf"}:
            raise RuntimeError("AXF/ELF hiện tại không hợp lệ.")
        return path

    def _v160_optional_symbol_file(self):
        text = self.symbol_path.text().strip()
        if not text:
            return None
        path = Path(text).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in {".axf", ".elf"}:
            return None
        return path

    def _v160_require_dwarf(self) -> DwarfTypeService:
        path = self._v160_symbol_file()
        if self._v160_dwarf is None or self._v160_dwarf_path != path:
            self._v160_dwarf = DwarfTypeService(path)
            self._v160_dwarf_path = path
        return self._v160_dwarf

    def _v160_refresh_target(self) -> None:
        try:
            values = self._v160_require_facade().target_summary()
        except Exception as error:
            self.target_awareness_pane.set_error(str(error))
            return
        self.target_awareness_pane.set_summary(values)

    def _v160_choose_svd(self) -> None:
        path_text, _selected = QFileDialog.getOpenFileName(
            self,
            "Chọn CMSIS-SVD",
            "",
            "CMSIS-SVD (*.svd *.xml);;All files (*)",
        )
        if not path_text:
            return
        try:
            facade = self._v160_require_facade()
        except Exception as error:
            self.peripheral_pane.set_error(str(error))
            return
        path = Path(path_text)

        def operation(_log, _phase, _cancel):
            return str(path), facade.load_svd(path)

        self._begin_worker(
            operation,
            lambda result: self.peripheral_pane.set_device(result[0], result[1].peripherals),
            "LOAD CMSIS-SVD...",
            failed=lambda failure: self.peripheral_pane.set_error(failure.message),
        )

    def _v160_inspect_register(self, peripheral: str, register: str) -> None:
        try:
            facade = self._v160_require_facade()
        except Exception as error:
            self.peripheral_pane.set_error(str(error))
            return
        self._begin_worker(
            lambda _log, _phase, _cancel: facade.peripherals.inspect_register(peripheral, register),
            self.peripheral_pane.set_snapshot,
            "READ PERIPHERAL...",
            failed=lambda failure: self.peripheral_pane.set_error(failure.message),
        )

    def _v160_refresh_freertos(self) -> None:
        try:
            facade = self._v160_require_facade()
            memory = facade.memory
            symbol_file = self._v160_symbol_file()
        except Exception as error:
            self.freertos_pane.set_error(str(error))
            return

        def operation(_log, _phase, _cancel):
            dwarf = DwarfTypeService(symbol_file)
            snapshot = FreeRtosInspector(memory, dwarf).capture()
            return dwarf, snapshot

        def completed(result) -> None:
            dwarf, snapshot = result
            self._v160_dwarf = dwarf
            self._v160_dwarf_path = symbol_file
            self.freertos_pane.set_snapshot(snapshot)

        self._begin_worker(
            operation,
            completed,
            "FREERTOS SNAPSHOT...",
            failed=lambda failure: self.freertos_pane.set_error(failure.message),
        )

    def _v160_analyze_fault(self) -> None:
        try:
            facade = self._v160_require_facade()
        except Exception as error:
            self.fault_pane.set_error(str(error))
            return
        symbol_file = self._v160_optional_symbol_file()

        def operation(_log, _phase, _cancel):
            analysis = facade.faults.capture()
            region = facade.faults.classify_fault_address(analysis)
            source = None
            dwarf = None
            if analysis.exception_frame is not None and symbol_file is not None:
                try:
                    dwarf = DwarfTypeService(symbol_file)
                    source = dwarf.resolve_address(analysis.exception_frame.pc)
                except Exception:
                    source = None
            return analysis, region, source, dwarf

        def completed(result) -> None:
            analysis, region, source, dwarf = result
            if dwarf is not None and symbol_file is not None:
                self._v160_dwarf = dwarf
                self._v160_dwarf_path = symbol_file
            self.fault_pane.set_analysis(analysis, region=region, source=source)

        self._begin_worker(
            operation,
            completed,
            "FAULT ANALYSIS...",
            failed=lambda failure: self.fault_pane.set_error(failure.message),
        )

    def _v15_local_started(self, result) -> None:
        self._v160_reset_services()
        super()._v15_local_started(result)
        QTimer.singleShot(0, self._v160_refresh_target)

    def _v15_client_started(self, result) -> None:
        self._v160_reset_services()
        super()._v15_client_started(result)
        QTimer.singleShot(0, self._v160_refresh_target)

    def _stopped(self, before) -> None:
        super()._stopped(before)
        self._v160_reset_services()
