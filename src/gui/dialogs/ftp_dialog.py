"""FTP/SFTP connection manager dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from src.database.db import FtpSessionRow
from src.settings.config import ConfigManager


class FtpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("FTP/SFTP Connection Manager")
        self.resize(650, 400)
        self._cfg = ConfigManager.get_instance()
        self._build_ui()
        self._load_sessions()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)

        # Session list
        list_layout = QVBoxLayout()
        self._session_list = QListWidget()
        self._session_list.currentRowChanged.connect(self._on_session_selected)
        list_layout.addWidget(QLabel("Sessions:"))
        list_layout.addWidget(self._session_list, stretch=1)
        btn_new = QPushButton("New")
        btn_new.clicked.connect(self._new_session)
        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._delete_session)
        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_new)
        btn_row.addWidget(btn_del)
        list_layout.addLayout(btn_row)
        layout.addLayout(list_layout)

        # Session details form
        form_group = QGroupBox("Connection Details")
        form = QFormLayout(form_group)

        self._name_edit = QLineEdit()
        self._protocol_combo = QComboBox()
        self._protocol_combo.addItems(["FTP", "FTPS", "SFTP"])
        self._host_edit = QLineEdit()
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(21)
        self._protocol_combo.currentTextChanged.connect(self._on_protocol_changed)
        self._user_edit = QLineEdit()
        self._pass_edit = QLineEdit()
        self._pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._path_edit = QLineEdit("/")

        form.addRow("Name:", self._name_edit)
        form.addRow("Protocol:", self._protocol_combo)
        form.addRow("Host:", self._host_edit)
        form.addRow("Port:", self._port_spin)
        form.addRow("Username:", self._user_edit)
        form.addRow("Password:", self._pass_edit)
        form.addRow("Remote path:", self._path_edit)

        btn_save = QPushButton("Save Session")
        btn_save.clicked.connect(self._save_session)
        btn_connect = QPushButton("Connect")
        btn_connect.clicked.connect(self._connect)

        action_row = QHBoxLayout()
        action_row.addStretch()
        action_row.addWidget(btn_save)
        action_row.addWidget(btn_connect)

        form_layout = QVBoxLayout()
        form_layout.addWidget(form_group)
        form_layout.addLayout(action_row)
        form_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        form_layout.addWidget(btn_close)

        layout.addLayout(form_layout, stretch=1)

    def _load_sessions(self) -> None:
        self._session_list.clear()
        for s in self._cfg._db.get_ftp_sessions():
            item = QListWidgetItem(f"{s.protocol} – {s.name}")
            item.setData(256, s)
            self._session_list.addItem(item)

    def _on_session_selected(self, row: int) -> None:
        item = self._session_list.item(row)
        if not item:
            return
        s: FtpSessionRow = item.data(256)
        self._name_edit.setText(s.name)
        self._protocol_combo.setCurrentText(s.protocol)
        self._host_edit.setText(s.host)
        self._port_spin.setValue(s.port)
        self._user_edit.setText(s.username)
        self._pass_edit.setText(s.password)
        self._path_edit.setText(s.remote_path)

    def _on_protocol_changed(self, proto: str) -> None:
        self._port_spin.setValue({"FTP": 21, "FTPS": 990, "SFTP": 22}.get(proto, 21))

    def _new_session(self) -> None:
        self._name_edit.clear()
        self._host_edit.clear()
        self._user_edit.clear()
        self._pass_edit.clear()
        self._path_edit.setText("/")

    def _save_session(self) -> None:
        s = FtpSessionRow(
            id=0,
            name=self._name_edit.text().strip(),
            protocol=self._protocol_combo.currentText(),
            host=self._host_edit.text().strip(),
            port=self._port_spin.value(),
            username=self._user_edit.text(),
            password=self._pass_edit.text(),
            remote_path=self._path_edit.text(),
            passive=True,
        )
        if not s.name or not s.host:
            QMessageBox.warning(self, "Validation", "Name and host are required.")
            return
        self._cfg._db.save_ftp_session(s)
        self._load_sessions()

    def _delete_session(self) -> None:
        item = self._session_list.currentItem()
        if not item:
            return
        s: FtpSessionRow = item.data(256)
        self._cfg._db.delete_ftp_session(s.id)
        self._load_sessions()

    def _connect(self) -> None:
        QMessageBox.information(
            self,
            "Connect",
            f"Connecting to {self._host_edit.text()}…\n"
            "(FTP panel integration coming soon)",
        )
