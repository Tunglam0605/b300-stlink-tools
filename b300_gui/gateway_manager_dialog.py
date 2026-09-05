"""Shared saved-Gateway manager used by all remote B300 workflows."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from b300_core.gateway_profiles import GatewayProfile, GatewayProfileStore
from b300_core.gateway_sessions import GatewaySessionManager
from .gateway_login_dialog import GatewayLoginDialog


class GatewayEditDialog(QDialog):
    def __init__(self, profile: Optional[GatewayProfile] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._profile = profile
        self.setWindowTitle("Gateway Profile")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_input = QLineEdit(profile.name if profile else "")
        self.host_input = QLineEdit(profile.endpoint.host if profile else "")
        self.user_input = QLineEdit(profile.endpoint.user if profile else "")
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(profile.endpoint.port if profile else 22)
        form.addRow("Name", self.name_input)
        form.addRow("Host / IP", self.host_input)
        form.addRow("SSH user", self.user_input)
        form.addRow("SSH port", self.port_input)
        layout.addLayout(form)
        note = QLabel("Chỉ lưu Name/Host/User/Port. Password không được lưu trong profile.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def profile(self) -> GatewayProfile:
        return GatewayProfile.create(
            self.name_input.text().strip(), self.host_input.text().strip(), self.user_input.text().strip(),
            self.port_input.value(), profile_id=(self._profile.profile_id if self._profile else None),
        )


class GatewayManagerDialog(QDialog):
    profiles_changed = Signal()

    def __init__(self, store: GatewayProfileStore, sessions: GatewaySessionManager,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.sessions = sessions
        self.setWindowTitle("Gateway Manager")
        self.setObjectName("gatewayManagerDialog")
        self.resize(760, 430)
        root = QVBoxLayout(self)
        intro = QLabel("Saved Gateways · endpoint dùng chung cho DEBUG, MONITOR và các remote workflow.")
        intro.setObjectName("pageSubtitle")
        root.addWidget(intro)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Name", "Endpoint", "Session", "Default"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self._connect_selected)
        root.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.btn_add = QPushButton("Add")
        self.btn_edit = QPushButton("Edit")
        self.btn_delete = QPushButton("Delete")
        self.btn_default = QPushButton("Set default")
        self.btn_connect = QPushButton("Connect")
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_connect.setObjectName("primaryActionButton")
        for button in (self.btn_add, self.btn_edit, self.btn_delete, self.btn_default): actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self.btn_disconnect)
        actions.addWidget(self.btn_connect)
        root.addLayout(actions)
        close_row = QHBoxLayout(); close_row.addStretch(1)
        close = QPushButton("Close"); close.clicked.connect(self.accept); close_row.addWidget(close)
        root.addLayout(close_row)
        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_delete.clicked.connect(self._delete)
        self.btn_default.clicked.connect(self._set_default)
        self.btn_connect.clicked.connect(self._connect_selected)
        self.btn_disconnect.clicked.connect(self._disconnect_selected)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.refresh()

    def _selected(self) -> Optional[GatewayProfile]:
        row = self.table.currentRow()
        if row < 0: return None
        item = self.table.item(row, 0)
        return self.store.get(item.data(256)) if item is not None else None

    def refresh(self) -> None:
        profiles = self.store.list()
        default_id = self.store.default_id()
        selected_id = self._selected().profile_id if self._selected() else None
        self.table.setRowCount(len(profiles))
        select_row = -1
        for row, profile in enumerate(profiles):
            name = QTableWidgetItem(profile.name); name.setData(256, profile.profile_id)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(profile.display_endpoint))
            self.table.setItem(row, 2, QTableWidgetItem("CONNECTED" if self.sessions.connected(profile.endpoint) else "Disconnected"))
            self.table.setItem(row, 3, QTableWidgetItem("Default" if profile.profile_id == default_id else ""))
            if selected_id == profile.profile_id: select_row = row
        if select_row >= 0: self.table.selectRow(select_row)
        elif profiles: self.table.selectRow(0)
        self._update_buttons()

    def _update_buttons(self) -> None:
        profile = self._selected()
        enabled = profile is not None
        for button in (self.btn_edit, self.btn_delete, self.btn_default, self.btn_connect, self.btn_disconnect):
            button.setEnabled(enabled)
        if profile is not None:
            connected = self.sessions.connected(profile.endpoint)
            self.btn_connect.setEnabled(not connected)
            self.btn_disconnect.setEnabled(connected)

    def _add(self) -> None:
        dialog = GatewayEditDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try: self.store.upsert(dialog.profile())
        except Exception as error:
            QMessageBox.warning(self, "Gateway profile", str(error)); return
        self.profiles_changed.emit(); self.refresh()

    def _edit(self) -> None:
        profile = self._selected()
        if profile is None: return
        if self.sessions.connected(profile.endpoint):
            QMessageBox.warning(self, "Gateway profile", "Disconnect Gateway trước khi sửa endpoint.")
            return
        dialog = GatewayEditDialog(profile, self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try: self.store.upsert(dialog.profile())
        except Exception as error:
            QMessageBox.warning(self, "Gateway profile", str(error)); return
        self.profiles_changed.emit(); self.refresh()

    def _delete(self) -> None:
        profile = self._selected()
        if profile is None: return
        if self.sessions.connected(profile.endpoint):
            QMessageBox.warning(self, "Gateway profile", "Disconnect Gateway trước khi xóa profile.")
            return
        if QMessageBox.question(self, "Delete Gateway", "Xóa profile '%s'?" % profile.name) != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(profile.profile_id); self.profiles_changed.emit(); self.refresh()

    def _set_default(self) -> None:
        profile = self._selected()
        if profile is None: return
        self.store.set_default(profile.profile_id); self.profiles_changed.emit(); self.refresh()

    def _connect_selected(self, *_args) -> None:
        profile = self._selected()
        if profile is None: return
        try:
            if not self.sessions.connected(profile.endpoint):
                if self.sessions.has_cached_password(profile.endpoint):
                    self.sessions.connect(profile.endpoint)
                else:
                    login = GatewayLoginDialog(profile, self)
                    if login.exec() != QDialog.DialogCode.Accepted: return
                    secret = login.password()
                    try:
                        self.sessions.connect(profile.endpoint, secret)
                    finally:
                        login.password_input.clear()
            self.store.set_default(profile.profile_id)
        except Exception as error:
            QMessageBox.warning(self, "SSH connection", str(error)); return
        self.profiles_changed.emit(); self.refresh()

    def _disconnect_selected(self) -> None:
        profile = self._selected()
        if profile is None: return
        self.sessions.disconnect(profile.endpoint)
        self.profiles_changed.emit(); self.refresh()


__all__ = ["GatewayManagerDialog", "GatewayEditDialog"]
