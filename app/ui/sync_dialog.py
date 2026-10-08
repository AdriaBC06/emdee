# SPDX-License-Identifier: GPL-3.0-or-later
"""Share vaults with paired devices on the local network.

A thin window over :mod:`app.core.sync`: contacts on the left of the user's
attention, the folder being shared, the three operations, and an "Accept
connections" switch that opens the port only while it is on and shows the
one-time code to type on the other device.

Network work runs on a plain Python thread; results come back through Qt
signals (queued onto the GUI thread).  When the listener needs the user to
approve a request, its thread blocks on an event that the GUI thread sets
from a confirmation dialog — with a timeout, so an unanswered prompt is a
refusal.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.sync.channel import format_code, normalize_code
from ..core.sync.errors import AuthError, SecurityError, SyncError
from ..core.sync.session import (
    APPROVAL_TIMEOUT,
    DEFAULT_LISTEN_SECONDS,
    Listener,
    ListenerOutcome,
    Report,
    Request,
    local_addresses,
    pair,
    transfer,
)
from ..core.sync.store import (
    Contact,
    ContactBook,
    Identity,
    load_identity,
    save_device_settings,
    with_settings,
)
from ..sync_cli import parse_address

__all__ = ["SyncDialog"]

_OP_LABELS = {
    "pull": ("Receive", "Receive “{name}”'s vault into this folder"),
    "push": ("Send", "Send this folder into “{name}”'s vault"),
    "sync": ("Sync both ways", "Merge this folder with “{name}”'s vault"),
}


class _Bridge(QObject):
    """Carries results from worker threads to the GUI thread."""

    event = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(object)
    approval = pyqtSignal(object)


class _Approval:
    def __init__(self, request: Request) -> None:
        self.request = request
        self.answer = False
        self.ready = threading.Event()


class _AddressDialog(QDialog):
    """Name + address (+ optional code) form used for pairing and editing."""

    def __init__(self, title: str, parent: QWidget, *, name: str | None = None,
                 address: str = "", ask_code: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        self.name_edit: QLineEdit | None = None
        if name is not None:
            self.name_edit = QLineEdit(name)
            form.addRow("Name", self.name_edit)
        self.address_edit = QLineEdit(address)
        self.address_edit.setPlaceholderText("192.168.1.20  or  192.168.1.20:47231")
        form.addRow("Address", self.address_edit)
        self.code_edit: QLineEdit | None = None
        if ask_code:
            self.code_edit = _code_edit()
            form.addRow("Code", self.code_edit)
            hint = QLabel("On the other device: Share vaults → Accept connections. Type the code it shows.")
            hint.setObjectName("hintLabel")
            hint.setWordWrap(True)
            form.addRow(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


def _code_edit() -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText("1234 5678")
    edit.setMaxLength(11)
    edit.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
    font = QFont(edit.font())
    font.setPointSizeF(font.pointSizeF() * 1.4)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
    edit.setFont(font)
    return edit


class SyncDialog(QDialog):
    """Contacts, the shared folder, the operations and the listener."""

    #: Emitted after an operation changed files in ``folder``.
    vault_changed = pyqtSignal(Path)

    def __init__(self, current_folder: Callable[[], Path | None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Share vaults")
        self.setMinimumSize(620, 560)
        self._current_folder = current_folder
        self._folder: Path | None = current_folder()
        self._identity: Identity = load_identity()
        self._contacts = ContactBook()
        self._listener: Listener | None = None
        self._busy = False
        self._deadline = 0.0
        self._listen_address = ""

        self._bridge = _Bridge(self)
        self._bridge.event.connect(self._log)
        self._bridge.approval.connect(self._ask_approval)
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick)

        self._build()
        self._refresh_contacts()
        self._refresh_identity()
        self._update_buttons()

    # ================================================================== ui
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        me = QHBoxLayout()
        self._me_label = QLabel()
        self._me_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        me.addWidget(self._me_label, 1)
        edit_me = QPushButton("This device…")
        edit_me.clicked.connect(self._edit_identity)
        me.addWidget(edit_me)
        layout.addLayout(me)

        folder = QHBoxLayout()
        self._folder_label = QLabel()
        self._folder_label.setWordWrap(True)
        folder.addWidget(self._folder_label, 1)
        choose = QPushButton("Choose folder…")
        choose.clicked.connect(self._choose_folder)
        folder.addWidget(choose)
        layout.addLayout(folder)

        title = QLabel("Contacts")
        title.setObjectName("emptyTitle")
        layout.addWidget(title)
        self._list = QListWidget()
        self._list.currentItemChanged.connect(lambda *_: self._update_buttons())
        layout.addWidget(self._list, 1)

        manage = QHBoxLayout()
        self._add_button = QPushButton("Add contact…")
        self._add_button.clicked.connect(self._add_contact)
        self._edit_button = QPushButton("Edit…")
        self._edit_button.clicked.connect(self._edit_contact)
        self._remove_button = QPushButton("Remove")
        self._remove_button.clicked.connect(self._remove_contact)
        for button in (self._add_button, self._edit_button, self._remove_button):
            manage.addWidget(button)
        manage.addStretch(1)
        layout.addLayout(manage)

        ops = QHBoxLayout()
        self._op_buttons: dict[str, QPushButton] = {}
        for op, (label, _tip) in _OP_LABELS.items():
            button = QPushButton(label)
            button.clicked.connect(lambda _c=False, o=op: self._start_operation(o))
            ops.addWidget(button)
            self._op_buttons[op] = button
        ops.addStretch(1)
        self._listen_button = QPushButton("Accept connections")
        self._listen_button.setCheckable(True)
        self._listen_button.toggled.connect(self._toggle_listening)
        ops.addWidget(self._listen_button)
        layout.addLayout(ops)

        self._code_panel = QWidget()
        code_layout = QVBoxLayout(self._code_panel)
        code_layout.setContentsMargins(0, 6, 0, 6)
        self._code_label = QLabel()
        self._code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        font = QFont(self._code_label.font())
        font.setPointSizeF(font.pointSizeF() * 2.6)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
        self._code_label.setFont(font)
        self._code_hint = QLabel()
        self._code_hint.setObjectName("hintLabel")
        self._code_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_hint.setWordWrap(True)
        code_layout.addWidget(self._code_label)
        code_layout.addWidget(self._code_hint)
        self._code_panel.hide()
        layout.addWidget(self._code_panel)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(500)
        self._log_view.setFixedHeight(120)
        layout.addWidget(self._log_view)

        note = QLabel(
            "Local network only · end-to-end encrypted · every operation needs a one-time "
            "code and the other side's approval · replaced or deleted files go to .trash/"
        )
        note.setObjectName("hintLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

    # ============================================================ refresh
    def _refresh_identity(self) -> None:
        ident = self._identity
        self._me_label.setText(
            f"<b>{_html(ident.name)}</b> · port {ident.port}<br>"
            f"<span style='font-family:monospace'>{ident.fingerprint}</span>"
        )
        if self._folder is not None:
            self._folder_label.setText(f"Folder: <b>{_html(str(self._folder))}</b>")
        else:
            self._folder_label.setText("Folder: <i>none — open a folder or choose one</i>")

    def _refresh_contacts(self) -> None:
        selected = self._selected()
        self._list.clear()
        for contact in self._contacts.all():
            item = QListWidgetItem(f"{contact.name}    {contact.address}    {contact.fingerprint}")
            item.setData(Qt.ItemDataRole.UserRole, contact.id)
            item.setToolTip(f"Fingerprint {contact.fingerprint}")
            self._list.addItem(item)
            if selected is not None and contact.id == selected.id:
                self._list.setCurrentItem(item)
        if self._list.currentItem() is None and self._list.count():
            self._list.setCurrentRow(0)

    def _selected(self) -> Contact | None:
        item = self._list.currentItem() if hasattr(self, "_list") else None
        if item is None:
            return None
        wanted = item.data(Qt.ItemDataRole.UserRole)
        return next((c for c in self._contacts.all() if c.id == wanted), None)

    def _update_buttons(self) -> None:
        contact = self._selected()
        listening = self._listener is not None
        idle = not self._busy and not listening
        for op, button in self._op_buttons.items():
            button.setEnabled(idle and contact is not None and self._folder is not None)
            if contact is not None:
                button.setToolTip(_OP_LABELS[op][1].format(name=contact.name))
        self._add_button.setEnabled(idle)
        self._edit_button.setEnabled(idle and contact is not None)
        self._remove_button.setEnabled(idle and contact is not None)
        self._listen_button.setEnabled(not self._busy or listening)

    def _log(self, message: str) -> None:
        self._log_view.appendPlainText(f"{time.strftime('%H:%M:%S')}  {message}")

    # ============================================================ actions
    def _edit_identity(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("This device")
        form = QFormLayout(dialog)
        name = QLineEdit(self._identity.name)
        port = QSpinBox()
        port.setRange(1024, 65535)
        port.setValue(self._identity.port)
        fingerprint = QLabel(self._identity.fingerprint)
        fingerprint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Name", name)
        form.addRow("Port", port)
        form.addRow("Fingerprint", fingerprint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._identity = with_settings(self._identity, name=name.text(), port=port.value())
        save_device_settings(self._identity)
        self._refresh_identity()

    def _choose_folder(self) -> None:
        start = str(self._folder or Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Folder to share", start)
        if chosen:
            self._folder = Path(chosen)
            self._refresh_identity()
            self._update_buttons()

    def _add_contact(self) -> None:
        dialog = _AddressDialog("Add contact", self, ask_code=True)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.code_edit is None:
            return
        try:
            host, port = parse_address(dialog.address_edit.text())
            code = normalize_code(dialog.code_edit.text())
        except SyncError as exc:
            self._error(str(exc))
            return
        identity, contacts = self._identity, self._contacts
        self._log(f"Pairing with {host}:{port}…")
        self._run(lambda emit: pair(identity, contacts, host, port, code, on_event=emit),
                  self._paired)

    def _paired(self, contact: Contact) -> None:
        self._refresh_contacts()
        self._log(f"Paired with “{contact.name}” ({contact.fingerprint}).")
        _plain_box(
            self, QMessageBox.Icon.Information, "Contact added",
            f"“{contact.name}” is now a contact.\n\nFingerprint: {contact.fingerprint}\n"
            "It should match the one shown on that device.",
        )

    def _edit_contact(self) -> None:
        contact = self._selected()
        if contact is None:
            return
        dialog = _AddressDialog("Edit contact", self, name=contact.name, address=contact.address)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            host, port = parse_address(dialog.address_edit.text(), contact.port)
            name = dialog.name_edit.text() if dialog.name_edit is not None else None
            self._contacts.update(contact, name=name, host=host, port=port)
        except SyncError as exc:
            self._error(str(exc))
        self._refresh_contacts()

    def _remove_contact(self) -> None:
        contact = self._selected()
        if contact is None:
            return
        answer = _plain_box(
            self, QMessageBox.Icon.Question, "Remove contact",
            f"Remove “{contact.name}”? It will no longer be able to connect to this device.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._contacts.remove(contact)
            self._refresh_contacts()
            self._update_buttons()

    def _start_operation(self, op: str) -> None:
        contact, folder = self._selected(), self._folder
        if contact is None or folder is None:
            return
        label, tip = _OP_LABELS[op]
        text, ok = QInputDialog.getText(
            self, label,
            f"{tip.format(name=contact.name)}:\n{folder}\n\n"
            f"On “{contact.name}”, click Accept connections and type the code it shows.",
            QLineEdit.EchoMode.Normal, "",
        )
        if not ok:
            return
        try:
            code = normalize_code(text)
        except AuthError as exc:
            self._error(str(exc))
            return
        identity = self._identity
        self._log(f"{label}: connecting to “{contact.name}” at {contact.address}…")
        self._run(
            lambda emit: transfer(identity, contact, folder, op, code, on_event=emit),
            lambda report: self._operation_done(folder, report),
        )

    def _operation_done(self, folder: Path, report: Report) -> None:
        self._log(f"Done: {report.summary()}.")
        for rel, reason in report.skipped:
            self._log(f"  skipped {rel}: {reason}")
        self._refresh_contacts()
        self.vault_changed.emit(folder)

    # ========================================================== listening
    def _toggle_listening(self, on: bool) -> None:
        if not on:
            if self._listener is not None:
                self._listener.cancel()
            return
        if self._listener is not None or self._busy:
            return
        self._contacts.reload()
        try:
            listener = Listener(
                self._identity, self._contacts, vault=self._folder,
                approve=self._approve_from_thread, on_event=self._bridge.event.emit,
            )
            port = listener.open()
        except SyncError as exc:
            self._error(str(exc))
            self._listen_button.setChecked(False)
            return
        self._listener = listener
        addresses = local_addresses()
        host = addresses[0] if addresses else "this device's IP"
        self._listen_address = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        self._deadline = time.monotonic() + DEFAULT_LISTEN_SECONDS
        self._code_label.setText(format_code(listener.code))
        self._code_panel.show()
        self._listen_button.setText("Stop")
        self._tick()
        self._countdown.start()
        what = f"the folder {self._folder.name}" if self._folder else "pairing only"
        self._log(f"Accepting one connection on port {port} ({what}).")
        self._run(lambda _emit: listener.serve(), self._listening_done, listener=True)

    def _tick(self) -> None:
        left = max(0, int(self._deadline - time.monotonic()))
        self._code_hint.setText(
            f"On the other device, connect to {self._listen_address} and type this code · "
            f"expires in {left // 60}:{left % 60:02d}"
        )

    def _listening_done(self, outcome: ListenerOutcome) -> None:
        self._log(outcome.message)
        if outcome.report is not None:
            for rel, reason in outcome.report.skipped:
                self._log(f"  skipped {rel}: {reason}")
            if self._folder is not None:
                self.vault_changed.emit(self._folder)
        if outcome.contact is not None:
            self._refresh_contacts()

    def _stop_listening_ui(self) -> None:
        self._listener = None
        self._countdown.stop()
        self._code_panel.hide()
        self._listen_button.blockSignals(True)
        self._listen_button.setChecked(False)
        self._listen_button.blockSignals(False)
        self._listen_button.setText("Accept connections")

    def _approve_from_thread(self, request: Request) -> bool:
        """Called on the listener thread; blocks until the user answers."""
        pending = _Approval(request)
        self._bridge.approval.emit(pending)
        pending.ready.wait(APPROVAL_TIMEOUT - 15)
        return pending.answer

    def _ask_approval(self, pending: _Approval) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Allow this request?")
        # Names come from the network: never let Qt interpret them as HTML.
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(pending.request.description)
        box.setInformativeText("Only allow it if you are expecting it right now.")
        allow = box.addButton("Allow", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Refuse", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(QMessageBox.StandardButton.NoButton)
        # Unanswered prompts expire on the listener side; close the box too.
        QTimer.singleShot(int((APPROVAL_TIMEOUT - 15) * 1000), box.reject)
        box.exec()
        pending.answer = box.clickedButton() is allow
        pending.ready.set()

    # ============================================================ workers
    def _run(self, work: Callable[[Callable[[str], None]], object],
             on_done: Callable[..., None], *, listener: bool = False) -> None:
        self._busy = True
        self._update_buttons()
        bridge = _Bridge(self)
        bridge.event.connect(self._log)
        bridge.done.connect(lambda result: self._finished(on_done, result, listener))
        bridge.failed.connect(lambda exc: self._failed(exc, listener))

        def target() -> None:
            try:
                result = work(bridge.event.emit)
            except BaseException as exc:  # noqa: BLE001 - reported in the window
                bridge.failed.emit(exc)
            else:
                bridge.done.emit(result)

        threading.Thread(target=target, name="emdee-sync", daemon=True).start()

    def _finished(self, on_done: Callable[..., None], result: object, listener: bool) -> None:
        self._busy = False
        if listener:
            self._stop_listening_ui()
        on_done(result)
        self._update_buttons()

    def _failed(self, exc: BaseException, listener: bool) -> None:
        self._busy = False
        if listener:
            self._stop_listening_ui()
        self._update_buttons()
        message = str(exc) if isinstance(exc, SyncError) else f"Unexpected error: {exc!r}"
        self._log(message)
        if isinstance(exc, SecurityError):
            _plain_box(self, QMessageBox.Icon.Critical, "Security warning", message)
        else:
            self._error(message)

    def _error(self, message: str) -> None:
        _plain_box(self, QMessageBox.Icon.Warning, "Share vaults", message)

    # ============================================================== close
    def done(self, result: int) -> None:  # noqa: D401 - Qt override
        if self._listener is not None:
            self._listener.cancel()
        super().done(result)

    def showEvent(self, event) -> None:  # noqa: N802, ANN001 - Qt API
        folder = self._current_folder()
        if folder is not None and not self._busy and self._listener is None:
            self._folder = folder
        self._contacts.reload()
        self._refresh_contacts()
        self._refresh_identity()
        self._update_buttons()
        super().showEvent(event)


def _plain_box(parent: QWidget, icon: QMessageBox.Icon, title: str, text: str,
               buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok) -> int:
    box = QMessageBox(icon, title, text, buttons, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    return box.exec()


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
