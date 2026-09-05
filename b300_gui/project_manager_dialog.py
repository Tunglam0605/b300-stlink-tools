"""Shared Debug Project manager for workspace + ELF/AXF selection."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from b300_core.project_profiles import ProjectProfile, ProjectProfileStore


class ProjectEditDialog(QDialog):
    def __init__(self, profile: Optional[ProjectProfile] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._profile = profile
        self.setWindowTitle("Dự án")
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_input = QLineEdit(profile.name if profile else "")
        self.workspace_input = QLineEdit(str(profile.workspace) if profile else "")
        self.symbols_input = QLineEdit(str(profile.symbols) if profile else "")
        self.hex_input = QLineEdit(str(profile.application_hex) if profile and profile.application_hex else "")
        self.hex_input.setPlaceholderText("File HEX Application (tùy chọn)")
        self.target_family_input = QLineEdit(profile.target_family if profile else "")
        hex_row = QHBoxLayout(); hex_row.addWidget(self.hex_input, 1)
        choose_hex = QPushButton("Chọn file…"); choose_hex.clicked.connect(self._browse_hex); hex_row.addWidget(choose_hex)
        workspace_row = QHBoxLayout(); workspace_row.addWidget(self.workspace_input, 1)
        choose_workspace = QPushButton("Chọn thư mục…"); choose_workspace.clicked.connect(self._browse_workspace); workspace_row.addWidget(choose_workspace)
        symbols_row = QHBoxLayout(); symbols_row.addWidget(self.symbols_input, 1)
        choose_symbols = QPushButton("Chọn file…"); choose_symbols.clicked.connect(self._browse_symbols); symbols_row.addWidget(choose_symbols)
        form.addRow("Tên dự án", self.name_input)
        form.addRow("Thư mục làm việc", workspace_row)
        form.addRow("File ELF / AXF", symbols_row)
        form.addRow("HEX Application", hex_row)
        form.addRow("Dòng vi điều khiển", self.target_family_input)
        root.addLayout(form)
        note = QLabel("Thư mục làm việc, file symbol và HEX Application được dùng chung cho NẠP, GIÁM SÁT và GỠ LỖI VS CODE.")
        note.setWordWrap(True); root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Lưu")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def _browse_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Chọn thư mục workspace", self.workspace_input.text())
        if selected: self.workspace_input.setText(selected)

    def _browse_symbols(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Chọn file ELF / AXF", self.workspace_input.text(), "ELF / AXF (*.elf *.axf)")
        if selected: self.symbols_input.setText(selected)

    def _browse_hex(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Chọn Application HEX", self.workspace_input.text(), "Application HEX (*.hex)")
        if selected: self.hex_input.setText(selected)

    def profile(self) -> ProjectProfile:
        return ProjectProfile.create(
            self.name_input.text().strip(), Path(self.workspace_input.text().strip()), Path(self.symbols_input.text().strip()),
            project_id=(self._profile.project_id if self._profile else None), require_exists=True,
            application_hex=Path(self.hex_input.text().strip()) if self.hex_input.text().strip() else None,
            target_family=self.target_family_input.text().strip(),
        )


class ProjectManagerDialog(QDialog):
    profiles_changed = Signal()

    def __init__(self, store: ProjectProfileStore, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Quản lý dự án")
        self.setObjectName("projectManagerDialog")
        self.resize(820, 430)
        root = QVBoxLayout(self)
        intro = QLabel("Thư mục làm việc, ELF/AXF và HEX Application dùng chung cho các chức năng.")
        intro.setObjectName("pageSubtitle"); root.addWidget(intro)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Tên dự án", "Thư mục làm việc", "File ELF / AXF", "Mặc định"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header=self.table.horizontalHeader(); header.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch); header.setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3,QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self._edit); root.addWidget(self.table,1)
        row=QHBoxLayout(); self.btn_add=QPushButton("Thêm"); self.btn_edit=QPushButton("Sửa")
        self.btn_delete=QPushButton("Xóa"); self.btn_default=QPushButton("Đặt làm mặc định")
        for button in (self.btn_add,self.btn_edit,self.btn_delete,self.btn_default): row.addWidget(button)
        row.addStretch(1); close=QPushButton("Đóng"); close.clicked.connect(self.accept); row.addWidget(close); root.addLayout(row)
        self.btn_add.clicked.connect(self._add); self.btn_edit.clicked.connect(self._edit); self.btn_delete.clicked.connect(self._delete)
        self.btn_default.clicked.connect(self._set_default); self.table.itemSelectionChanged.connect(self._update_buttons)
        self.refresh()

    def _selected(self) -> Optional[ProjectProfile]:
        row=self.table.currentRow()
        if row<0: return None
        item=self.table.item(row,0); return self.store.get(item.data(256)) if item else None

    def refresh(self) -> None:
        projects=self.store.list(); default_id=self.store.default_id(); selected=self._selected(); selected_id=selected.project_id if selected else None
        self.table.setRowCount(len(projects)); select_row=-1
        for row,profile in enumerate(projects):
            name=QTableWidgetItem(profile.name); name.setData(256,profile.project_id); self.table.setItem(row,0,name)
            self.table.setItem(row,1,QTableWidgetItem(str(profile.workspace))); self.table.setItem(row,2,QTableWidgetItem(str(profile.symbols)))
            self.table.setItem(row,3,QTableWidgetItem("Mặc định" if profile.project_id==default_id else ""))
            if profile.project_id==selected_id: select_row=row
        if select_row>=0: self.table.selectRow(select_row)
        elif projects: self.table.selectRow(0)
        self._update_buttons()

    def _update_buttons(self) -> None:
        enabled=self._selected() is not None
        for button in (self.btn_edit,self.btn_delete,self.btn_default): button.setEnabled(enabled)

    def _add(self) -> None:
        dialog=ProjectEditDialog(parent=self)
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        try: self.store.upsert(dialog.profile())
        except Exception as error: QMessageBox.warning(self,"Dự án gỡ lỗi",str(error)); return
        self.profiles_changed.emit(); self.refresh()

    def _edit(self,*_args) -> None:
        profile=self._selected()
        if profile is None: return
        dialog=ProjectEditDialog(profile,self)
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        try: self.store.upsert(dialog.profile())
        except Exception as error: QMessageBox.warning(self,"Dự án gỡ lỗi",str(error)); return
        self.profiles_changed.emit(); self.refresh()

    def _delete(self) -> None:
        profile=self._selected()
        if profile is None: return
        if QMessageBox.question(self,"Xóa dự án","Xóa dự án '%s'?"%profile.name)!=QMessageBox.StandardButton.Yes: return
        self.store.delete(profile.project_id); self.profiles_changed.emit(); self.refresh()

    def _set_default(self) -> None:
        profile=self._selected()
        if profile is None: return
        self.store.set_default(profile.project_id); self.profiles_changed.emit(); self.refresh()


__all__=["ProjectManagerDialog","ProjectEditDialog"]
