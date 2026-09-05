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
        self.setWindowTitle("Debug Project")
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_input = QLineEdit(profile.name if profile else "")
        self.workspace_input = QLineEdit(str(profile.workspace) if profile else "")
        self.symbols_input = QLineEdit(str(profile.symbols) if profile else "")
        workspace_row = QHBoxLayout(); workspace_row.addWidget(self.workspace_input, 1)
        choose_workspace = QPushButton("Browse…"); choose_workspace.clicked.connect(self._browse_workspace); workspace_row.addWidget(choose_workspace)
        symbols_row = QHBoxLayout(); symbols_row.addWidget(self.symbols_input, 1)
        choose_symbols = QPushButton("Browse…"); choose_symbols.clicked.connect(self._browse_symbols); symbols_row.addWidget(choose_symbols)
        form.addRow("Name", self.name_input)
        form.addRow("Workspace", workspace_row)
        form.addRow("ELF / AXF", symbols_row)
        root.addLayout(form)
        note = QLabel("Project này được dùng chung bởi Monitor và Debug Local/Client.")
        note.setWordWrap(True); root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def _browse_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select workspace", self.workspace_input.text())
        if selected: self.workspace_input.setText(selected)

    def _browse_symbols(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Select ELF / AXF", self.workspace_input.text(), "ELF / AXF (*.elf *.axf)")
        if selected: self.symbols_input.setText(selected)

    def profile(self) -> ProjectProfile:
        return ProjectProfile.create(
            self.name_input.text().strip(), Path(self.workspace_input.text().strip()), Path(self.symbols_input.text().strip()),
            project_id=(self._profile.project_id if self._profile else None), require_exists=True,
        )


class ProjectManagerDialog(QDialog):
    profiles_changed = Signal()

    def __init__(self, store: ProjectProfileStore, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Project Manager")
        self.setObjectName("projectManagerDialog")
        self.resize(820, 430)
        root = QVBoxLayout(self)
        intro = QLabel("Saved Debug Projects · một workspace + ELF/AXF dùng chung cho MONITOR và DEBUG.")
        intro.setObjectName("pageSubtitle"); root.addWidget(intro)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Name", "Workspace", "ELF / AXF", "Default"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header=self.table.horizontalHeader(); header.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch); header.setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3,QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self._edit); root.addWidget(self.table,1)
        row=QHBoxLayout(); self.btn_add=QPushButton("Add"); self.btn_edit=QPushButton("Edit")
        self.btn_delete=QPushButton("Delete"); self.btn_default=QPushButton("Set default")
        for button in (self.btn_add,self.btn_edit,self.btn_delete,self.btn_default): row.addWidget(button)
        row.addStretch(1); close=QPushButton("Close"); close.clicked.connect(self.accept); row.addWidget(close); root.addLayout(row)
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
            self.table.setItem(row,3,QTableWidgetItem("Default" if profile.project_id==default_id else ""))
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
        except Exception as error: QMessageBox.warning(self,"Debug Project",str(error)); return
        self.profiles_changed.emit(); self.refresh()

    def _edit(self,*_args) -> None:
        profile=self._selected()
        if profile is None: return
        dialog=ProjectEditDialog(profile,self)
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        try: self.store.upsert(dialog.profile())
        except Exception as error: QMessageBox.warning(self,"Debug Project",str(error)); return
        self.profiles_changed.emit(); self.refresh()

    def _delete(self) -> None:
        profile=self._selected()
        if profile is None: return
        if QMessageBox.question(self,"Delete Project","Xóa project '%s'?"%profile.name)!=QMessageBox.StandardButton.Yes: return
        self.store.delete(profile.project_id); self.profiles_changed.emit(); self.refresh()

    def _set_default(self) -> None:
        profile=self._selected()
        if profile is None: return
        self.store.set_default(profile.project_id); self.profiles_changed.emit(); self.refresh()


__all__=["ProjectManagerDialog","ProjectEditDialog"]
