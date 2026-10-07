"""Compact SSH account editor; persistence contains no credentials."""
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout,
)
from b300_core.gateway_profiles import GatewayProfile


class PulseLoginDialog(QDialog):
    def __init__(self, profiles=(), selected_id=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Đăng nhập SSH')
        self.resize(440, 300)
        self._profiles = tuple(profiles)
        self._profile = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)
        form = QFormLayout()
        self.accounts = QComboBox()
        for item in self._profiles:
            self.accounts.addItem(item.name, item.profile_id)
        self.accounts.addItem('Máy mới…', None)
        self.host = QLineEdit()
        self.host.setPlaceholderText('IP hoặc tên máy')
        self.user = QLineEdit()
        self.user.setPlaceholderText('Tài khoản SSH')
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText('Để trống nếu dùng khóa SSH')
        for text, widget in (('Máy', self.accounts), ('Địa chỉ', self.host),
                             ('Tài khoản', self.user), ('Cổng', self.port),
                             ('Mật khẩu', self.password_input)):
            form.addRow(text, widget)
        root.addLayout(form)
        self.error = QLabel('')
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton('Hủy')
        cancel.clicked.connect(self.reject)
        submit = QPushButton('Kết nối')
        submit.setDefault(True)
        submit.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(submit)
        root.addLayout(row)
        self.accounts.currentIndexChanged.connect(self._select_account)
        index = self.accounts.findData(selected_id)
        self.accounts.setCurrentIndex(max(0, index))
        self._select_account()

    def _select_account(self, *_):
        selected = self.accounts.currentData()
        self._profile = next((item for item in self._profiles if item.profile_id == selected), None)
        profile = self._profile
        self.host.setText(profile.endpoint.host if profile else '')
        self.user.setText(profile.endpoint.user if profile else '')
        self.port.setValue(profile.endpoint.port if profile else 22)
        self.password_input.clear()

    def profile(self):
        old = self._profile
        # Reuse custom CLI settings only for the same saved endpoint.
        same = bool(old and self.host.text().strip() == old.endpoint.host
                    and self.user.text().strip() == old.endpoint.user
                    and self.port.value() == old.endpoint.port)
        return GatewayProfile.create(
            old.name if same else self.host.text().strip(),
            self.host.text().strip(), self.user.text().strip(), self.port.value(),
            profile_id=old.profile_id if same else None,
            cli_path=old.endpoint.cli_path if same else None,
        )

    def accept(self):
        try:
            self.profile()
        except ValueError as error:
            self.error.setText(str(error))
            return
        super().accept()

    def done(self, result):
        if result != QDialog.DialogCode.Accepted:
            self.password_input.clear()
        super().done(result)
