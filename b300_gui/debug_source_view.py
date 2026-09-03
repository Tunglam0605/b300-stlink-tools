"""Source code view with line numbers, execution markers, and disassembly fallback."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Set

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class LineNumberArea(QWidget):
    """Gutter widget displaying line numbers and debug execution/breakpoint glyphs."""

    def __init__(self, editor: "SourceCodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.line_number_area_paint_event(event)


class SourceCodeEditor(QPlainTextEdit):
    """Monospace code editor with line numbers, execution marker, and breakpoint dots."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugSourceEditor")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

        self.line_number_area = LineNumberArea(self)
        self.current_line: Optional[int] = None  # 1-indexed
        self.breakpoints: Set[int] = set()       # 1-indexed set of line numbers
        self.frame_line: Optional[int] = None    # 1-indexed selected stack frame line

        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        self.update_line_number_area_width(0)

    def line_number_area_width(self) -> int:
        digits = max(1, len(str(max(1, self.blockCount()))))
        space = 36 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def update_line_number_area_width(self, _) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def set_execution_location(self, line: Optional[int]) -> None:
        self.current_line = line
        self.highlight_current_line()
        self.line_number_area.update()
        if line is not None and line > 0:
            block = self.document().findBlockByLineNumber(line - 1)
            if block.isValid():
                cursor = QTextCursor(block)
                self.setTextCursor(cursor)
                self.centerCursor()

    def set_frame_location(self, line: Optional[int]) -> None:
        self.frame_line = line
        self.highlight_current_line()
        self.line_number_area.update()

    def set_breakpoints(self, lines: Set[int]) -> None:
        self.breakpoints = set(lines)
        self.line_number_area.update()

    def highlight_current_line(self) -> None:
        extra_selections = []
        if self.current_line is not None and self.current_line > 0:
            block = self.document().findBlockByLineNumber(self.current_line - 1)
            if block.isValid():
                selection = QPlainTextEdit.ExtraSelection()
                # Emerald/teal tint for active execution line
                line_color = QColor("#064E3B") if self.palette().window().color().lightness() < 128 else QColor("#D1FAE5")
                selection.format.setBackground(line_color)
                selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                selection.cursor = QTextCursor(block)
                selection.cursor.clearSelection()
                extra_selections.append(selection)

        if self.frame_line is not None and self.frame_line > 0 and self.frame_line != self.current_line:
            block = self.document().findBlockByLineNumber(self.frame_line - 1)
            if block.isValid():
                selection = QPlainTextEdit.ExtraSelection()
                # Amber tint for stack frame line
                line_color = QColor("#451A03") if self.palette().window().color().lightness() < 128 else QColor("#FEF3C7")
                selection.format.setBackground(line_color)
                selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                selection.cursor = QTextCursor(block)
                selection.cursor.clearSelection()
                extra_selections.append(selection)

        self.setExtraSelections(extra_selections)

    def line_number_area_paint_event(self, event) -> None:
        painter = QPainter(self.line_number_area)
        is_dark = self.palette().window().color().lightness() < 128
        gutter_bg = QColor("#0D1420") if is_dark else QColor("#F1F5F9")
        gutter_text = QColor("#64748B") if is_dark else QColor("#94A3B8")
        painter.fillRect(event.rect(), gutter_bg)

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_num = block_number + 1

                # Draw breakpoint dot
                if line_num in self.breakpoints:
                    painter.setBrush(QColor("#EF4444"))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(6, top + 3, 10, 10)

                # Draw execution arrow
                if line_num == self.current_line:
                    painter.setPen(QColor("#10B981"))
                    painter.drawText(18, top, self.line_number_area_width() - 20, self.fontMetrics().height(),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "➔")

                painter.setPen(gutter_text)
                painter.drawText(
                    0, top, self.line_number_area_width() - 8, self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(line_num)
                )

            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1


class DebugSourceView(QWidget):
    """Central source pane supporting file viewing with line highlights and disassembly fallback."""

    location_changed = Signal(str, int)  # file, line

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugSourceView")
        self._current_file: Optional[str] = None
        self._current_line: Optional[int] = None
        self._current_address: Optional[str] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # File navigation / breadcrumb header
        self.header = QFrame(self)
        self.header.setObjectName("debugSourceHeader")
        self.header.setStyleSheet("background: #131A2A; border-bottom: 1px solid #2A3A52; padding: 4px 8px;")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_layout.setSpacing(8)

        self.file_label = QLabel("Chưa có source file")
        self.file_label.setObjectName("debugSourceFileLabel")
        self.file_label.setStyleSheet("font-size: 11px; font-weight: 700; color: #F1F5F9; font-family: monospace;")
        header_layout.addWidget(self.file_label)

        self.loc_badge = QLabel("")
        self.loc_badge.setObjectName("debugSourceLocationBadge")
        self.loc_badge.setStyleSheet("font-size: 11px; color: #10B981; font-family: monospace;")
        header_layout.addWidget(self.loc_badge)
        header_layout.addStretch(1)

        layout.addWidget(self.header)

        # Stacked view: Page 0 = Source Code Editor, Page 1 = Disassembly / Fallback
        self.stack = QStackedWidget(self)

        self.editor = SourceCodeEditor(self.stack)
        self.stack.addWidget(self.editor)

        self.disasm_view = QPlainTextEdit(self.stack)
        self.disasm_view.setObjectName("debugDisassemblyFallback")
        self.disasm_view.setReadOnly(True)
        disasm_font = QFont("Consolas", 10)
        disasm_font.setStyleHint(QFont.StyleHint.Monospace)
        self.disasm_view.setFont(disasm_font)
        self.disasm_view.setPlaceholderText("Không tìm thấy source file. Disassembly fallback tại PC...")
        self.stack.addWidget(self.disasm_view)

        layout.addWidget(self.stack, 1)

    def show_location(
        self,
        file_path: Optional[str] = None,
        line: Optional[int] = None,
        address: Optional[str] = None,
        function: Optional[str] = None,
    ) -> None:
        self._current_file = file_path
        self._current_line = line
        self._current_address = address

        loc_text = ""
        if address:
            loc_text += f"{address} "
        if function:
            loc_text += f"[{function}] "
        if line:
            loc_text += f":{line}"
        self.loc_badge.setText(loc_text.strip())

        # If file_path is provided and exists on disk, show in editor
        if file_path and os.path.isfile(file_path):
            try:
                content = Path(file_path).read_text(encoding="utf-8", errors="replace")
                self.file_label.setText(Path(file_path).name)
                self.file_label.setToolTip(file_path)
                self.editor.setPlainText(content)
                self.editor.set_execution_location(line)
                self.stack.setCurrentWidget(self.editor)
                return
            except Exception:
                pass

        # Fallback to disassembly / placeholder pane
        display_name = Path(file_path).name if file_path else "Target PC"
        self.file_label.setText(display_name)
        if file_path:
            self.file_label.setToolTip(f"Source not found: {file_path}")

        disasm_lines = [
            f"; Target PC Location: {address or '0x08010000'}",
            f"; Function: {function or '—'}",
            f"; Source file not available on local host: {file_path or '—'}",
            f"; Line: {line or '—'}",
            "",
            f"  {address or '0x08010000'}:   4803        ldr     r0, [pc, #12]",
            f"  {(hex(int(address, 16) + 2) if address and address.startswith('0x') else '0x08010002')}:   4770        bx      lr",
        ]
        self.disasm_view.setPlainText("\n".join(disasm_lines))
        self.stack.setCurrentWidget(self.disasm_view)

    def set_breakpoints(self, lines: Set[int]) -> None:
        self.editor.set_breakpoints(lines)
