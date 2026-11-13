"""Independently authored Qt interface for the Quick Relist local workspace.

The GUI deliberately cannot create a source snapshot or claim a Hub action
succeeded. Those operations belong to the supplied Engine and its verified
adapter. ``launch(engine, store)`` is the application entry point; constructing
``MainWindow`` without a backend is supported for reviewing the interface.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .core import REQUIRED_ADAPTER_QUALIFICATIONS

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, QUrl, Signal, Slot, QMimeData
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


STYLE = """
QWidget { font-family: 'Segoe UI', 'DejaVu Sans', sans-serif; font-size: 13px;
          color: #23364c; }
QMainWindow, QDialog { background: #f2f5f9; }
QWidget#shell, QWidget#workspace { background: #f2f5f9; }
QFrame#sidebar { background: #11263e; border: none; }
QLabel#brand { color: #ffffff; font-size: 18px; font-weight: 700; }
QLabel#brandSub { color: #92a8c1; font-size: 11px; }
QLabel#brandMark { color: #ffffff; background: #2776ed; border-radius: 12px;
                  font-size: 24px; font-weight: 700; }
QLabel#navHeading { color: #7e96b1; font-size: 10px; font-weight: 700; }
QPushButton#navButton { background: transparent; color: #b7c6d7;
                       border: none; border-radius: 8px; text-align: left;
                       padding: 13px 14px; font-weight: 600; }
QPushButton#navButton:hover { background: #1b3654; color: white; }
QPushButton#navButton:checked { background: #234d7c; color: white; }
QFrame#sidebarLine { background: #29405b; max-height: 1px; border: none; }
QLabel#sidebarFoot { color: #b3c3d5; font-size: 12px; }
QLabel#sidebarMuted { color: #8199b4; font-size: 11px; }
QLabel#pageTitle { font-size: 28px; font-weight: 700; color: #152b43; }
QLabel#subtitle { color: #6f7f91; font-size: 13px; }
QLabel#sectionTitle { font-size: 16px; font-weight: 700; color: #1b314a; }
QLabel#smallTitle { font-size: 12px; font-weight: 700; color: #1e3855; }
QLabel#muted { color: #748295; font-size: 12px; }
QLabel#eyebrow { font-size: 10px; color: #71829a; font-weight: 700; }
QLabel#stat { font-size: 25px; font-weight: 700; color: #203a57; }
QFrame#card { background: #ffffff; border: 1px solid #e0e7f0;
              border-radius: 12px; }
QFrame#notice { background: #fff8e9; border: 1px solid #eddbab;
                border-radius: 9px; }
QFrame#blueNotice { background: #edf4ff; border: 1px solid #d8e6ff;
                    border-radius: 9px; }
QLabel#noticeTitle { color: #75541b; font-weight: 700; font-size: 12px; }
QLabel#noticeBody { color: #806539; font-size: 12px; }
QLabel#pill { border-radius: 11px; padding: 4px 10px; font-size: 11px;
              font-weight: 600; background: #e9eff8; color: #516a89; }
QPushButton { padding: 9px 14px; background: #ffffff; color: #355473;
              border: 1px solid #d5dfeb; border-radius: 7px; font-weight: 600; }
QPushButton:hover { background: #edf4ff; border-color: #afc7e9; }
QPushButton:pressed { background: #e2edfb; }
QPushButton:focus { border: 2px solid #719dec; }
QPushButton:disabled { color: #97a6b6; border-color: #e2e8ef; background: #f2f5f8; }
QPushButton#primary { background: #256fe7; color: white; border: 1px solid #256fe7; }
QPushButton#primary:hover { background: #185ece; }
QPushButton#primary:pressed { background: #154fac; }
QPushButton#primary:disabled { color: #8799b0; background: #e5ecf6; border-color: #dfe7f1; }
QPushButton#linkButton { color: #256fe7; background: transparent; border: none; padding: 5px; }
QPushButton#danger { color: #9f4b37; border-color: #e0c4bc; }
QPushButton#danger:disabled { color: #97a6b6; border-color: #e2e8ef; background: #f2f5f8; }
QLineEdit, QPlainTextEdit, QComboBox { border: 1px solid #d9e2ed;
        border-radius: 7px; background: #ffffff; color: #23364c; padding: 9px; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #6d9cef; }
QLineEdit:disabled, QPlainTextEdit:disabled { background: #f3f6fa; color: #93a0af; }
QPlainTextEdit#sourceText { background: #f7f9fc; color: #5f728a; }
QComboBox::drop-down { border: none; width: 25px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #dce9fc;
                           selection-color: #203a57; padding: 6px; }
QTableWidget { background: #ffffff; border: none; gridline-color: #edf1f6;
               selection-background-color: #edf4ff; selection-color: #213b5b;
               alternate-background-color: #fbfcfe; outline: none; }
QTableWidget::item { padding: 10px 9px; border-bottom: 1px solid #edf1f6; }
QTableWidget::item:selected { background: #eaf2ff; color: #16488b; }
QHeaderView::section { background: #f7f9fc; color: #74859b; border: none;
                      border-bottom: 1px solid #e4eaf2; padding: 11px 9px;
                      font-size: 10px; font-weight: 700; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #f1f4f8; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #cbd6e4; border-radius: 4px; min-height: 26px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #f1f4f8; height: 9px; margin: 0; }
QScrollBar::handle:horizontal { background: #cbd6e4; border-radius: 4px; min-width: 26px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QSplitter::handle { background: transparent; width: 12px; }
QProgressBar { border: none; background: #e6eef9; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background: #307cea; border-radius: 2px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QStatusBar { background: #ffffff; color: #738297; border-top: 1px solid #e1e8f0; }
QToolTip { background: #193452; color: white; border: none; padding: 7px; }
"""

STATE_LABELS = {
    "queued": "Awaiting capture",
    "awaiting_address": "Address review",
    "ready": "Ready for next step",
    "portals_removed": "Removed from portals",
    "archived": "Original archived",
    "created": "New listing created",
    "fidelity_verified": "Copy verified",
    "publication_pending": "Publication pending",
    "live": "Confirmed live",
    "needs_reconciliation": "Check outcome first",
    "blocked": "Blocked",
    "cancelled": "Cancelled locally",
}

ADDRESS_LABELS = {
    "unit": "Unit / apartment",
    "unit_number": "Unit number",
    "complex": "Complex / estate",
    "complex_name": "Complex / estate",
    "street_number": "Street number",
    "street_name": "Street name",
    "street_type": "Street type",
    "estate_name": "Estate name",
    "erf_number": "Erf number",
    "ss_unit_number": "SS unit number",
    "ss_number": "SS number",
    "ss_name": "SS name",
    "id_registration_number": "ID / registration number",
    "door_number": "Door number",
    "street_address": "Street address",
    "address_line_1": "Address line 1",
    "address_line_2": "Address line 2",
    "suburb": "Suburb",
    "city": "City / town",
    "province": "Province / region",
    "postal_code": "Postal code",
    "country": "Country",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "show_address_on_web": "Show address on the web",
    "show_on_web": "Show address on the web",
}


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def _button(text: str, callback: Callable, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    widget.setMinimumHeight(35)
    if primary:
        widget.setObjectName("primary")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


def _card() -> tuple[QFrame, QVBoxLayout]:
    widget = QFrame()
    widget.setObjectName("card")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(20, 19, 20, 19)
    layout.setSpacing(13)
    return widget, layout


def _timestamp(value: Any) -> str:
    if not value:
        return "Not recorded"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone()
        return parsed.strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return str(value)


def _address_summary(address: Any) -> str:
    if not isinstance(address, dict) or not address:
        return "No address captured"
    preferred = ("unit_number", "street_number", "street_name", "street_address", "suburb", "city")
    parts = [str(address[key]).strip() for key in preferred if address.get(key)]
    if not parts:
        parts = [str(value) for value in address.values() if isinstance(value, str) and value.strip()][:4]
    return ", ".join(parts) if parts else "Captured address available for review"


def _state_label(state: Any) -> str:
    return STATE_LABELS.get(str(state), str(state or "Unknown").replace("_", " ").capitalize())


def _collected_job_view(drafts, job):
    """Show collected data without changing the engine's write approval state."""
    if drafts is None:
        return None
    from .collection import latest_collection
    from .address import propose_address_fields
    record = latest_collection(drafts, job['id'])
    if not record:
        return None
    capture = record['capture']
    source = drafts.get_capture(capture['source_capture_id'])['capture']
    revisions = drafts.list_addresses(capture['source_capture_id'])
    edited = revisions[0]['edited'] if revisions else {}
    address = {}
    for key, proposed in propose_address_fields(source)['fields'].items():
        field = edited.get(proposed['path'], proposed['field'])
        address['unit_number' if key == 'ss_unit_number' else key] = field.get('value')
    return {'address': address, 'reviewed': bool(revisions),
            'state': 'Address review saved' if revisions else 'Ready for address review',
            'media_count': capture['media_count']}


def _run_status(runs, job):
    from .relist_runner import display_status
    record = runs.get(job['id']) if runs is not None else None
    return display_status(record)


def _relist_state_label(status):
    if status.get('state') == 'manual_completed':
        return 'Completed in Hub · ' + status['target_reference']
    if status.get('state') == 'manual_publication_pending':
        return 'Hub recovery · waiting for portals'
    if 'create' in status.get('unresolved_actions', []):
        return 'Stopped · replacement Save needs checking'
    if status.get('state')=='published':
        return 'Relisted · '+status['target_reference']
    labels={'portals_removed':'Original removed from portals','archived':'Original archived; copy pending',
            'created':'Replacement saved; checking content','verified':'Replacement checked; publishing',
            'publication_pending':'Waiting for portals','needs_review':'Stopped · check saved progress'}
    return labels.get(status.get('state'),'Check saved progress')


class _Worker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, operation: Callable[[], Any]):
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self.operation())
        except Exception as exc:  # The error must be visible, never a fake success.
            self.failed.emit(str(exc) or type(exc).__name__)
        finally:
            self.finished.emit()


class AddressReviewDialog(QDialog):
    """Review only real captured fields; preserve unknown and nested field values."""

    def __init__(self, job: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        snapshot = job.get("snapshot")
        original = job.get("original_address")
        if not isinstance(snapshot, dict) or snapshot.get("complete") is not True or not isinstance(original, dict) or not original:
            raise ValueError("A complete, verified source capture is required before address review.")
        self.job = deepcopy(job)
        self.original = deepcopy(original)
        self.address = deepcopy(job.get("corrected_address") or original)
        self.fields: dict[str, QWidget] = {}
        self.setWindowTitle(f"Review address · {job.get('rxcu', 'Quick Relist')}")
        self.setMinimumSize(620, 600)
        self.resize(740, 780)
        root = QVBoxLayout(self)
        root.setContentsMargins(25, 23, 25, 20)
        root.setSpacing(15)
        root.addWidget(_label("Review the address", "pageTitle"))
        root.addWidget(_label(f"{job.get('rxcu', '')} · Confirm these details before any live action.", "subtitle", True))

        source_card, source_layout = _card()
        source_layout.addWidget(_label("CAPTURED ORIGINAL", "eyebrow"))
        source_layout.addWidget(_label(_address_summary(original), "smallTitle", True))
        source_layout.addWidget(_label(f"Source listing: {job.get('source_id') or 'Recorded in source capture'}", "muted", True))
        source_text = QPlainTextEdit(json.dumps(original, ensure_ascii=False, indent=2))
        source_text.setObjectName("sourceText")
        source_text.setReadOnly(True)
        source_text.setMaximumHeight(105)
        source_text.setAccessibleName("Captured original address, read only")
        source_layout.addWidget(source_text)
        root.addWidget(source_card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        editor = QWidget()
        form = QFormLayout(editor)
        form.setContentsMargins(1, 4, 10, 4)
        form.setSpacing(11)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignVCenter)
        for key, value in self.address.items():
            title = ADDRESS_LABELS.get(key, str(key).replace("_", " ").capitalize())
            if isinstance(value, bool):
                field = QCheckBox("Yes")
                field.setChecked(value)
            elif isinstance(value, (dict, list)):
                field = QLineEdit(json.dumps(value, ensure_ascii=False))
                field.setReadOnly(True)
                field.setToolTip("Structured source value is preserved unchanged.")
            else:
                field = QLineEdit("" if value is None else str(value))
                field.setPlaceholderText("Not provided in the source")
            field.setAccessibleName(title)
            field.setObjectName(f"address_{key}")
            self.fields[key] = field
            form.addRow(_label(title), field)
        scroll.setWidget(editor)
        root.addWidget(scroll, 1)

        root.addWidget(_label("Cancellation and archive reason", "smallTitle"))
        self.reason = QComboBox()
        self.reason.setAccessibleName("Archive reason")
        self.reason.addItem("Select an archive reason…", "")
        self.reason.addItem("Listing has been cancelled · Mandate expired", "mandate_expired")
        index = self.reason.findData(job.get("archive_reason"))
        if index >= 0:
            self.reason.setCurrentIndex(index)
        root.addWidget(self.reason)
        self.confirmed = QCheckBox("I have checked the address and archive reason.")
        self.confirmed.setAccessibleName("Confirm address and archive reason reviewed")
        root.addWidget(self.confirmed)
        for widget in self.fields.values():
            if isinstance(widget, QLineEdit) and not widget.isReadOnly():
                widget.textEdited.connect(lambda _text: self.confirmed.setChecked(False))
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(lambda _checked: self.confirmed.setChecked(False))
        self.reason.currentIndexChanged.connect(lambda _index: self.confirmed.setChecked(False))
        root.addWidget(_label("Saving this review records your choices locally. It does not remove, archive, create, or publish a listing.", "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.addButton("Save address review", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setObjectName("primary")
        self.save_button.setEnabled(False)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        self.confirmed.toggled.connect(self._validate)
        self.reason.currentIndexChanged.connect(self._validate)
        root.addWidget(buttons)

    @Slot()
    def _validate(self) -> None:
        self.save_button.setEnabled(self.confirmed.isChecked() and bool(self.reason.currentData()))

    def corrected_address(self) -> dict[str, Any]:
        corrected = deepcopy(self.address)
        for key, widget in self.fields.items():
            original_value = self.address[key]
            if isinstance(widget, QCheckBox):
                corrected[key] = widget.isChecked()
            elif isinstance(widget, QLineEdit) and not widget.isReadOnly():
                text = widget.text().strip()
                # Retain original types if a field was not changed.
                if text == ("" if original_value is None else str(original_value)):
                    corrected[key] = original_value
                elif isinstance(original_value, (int, float)) and not isinstance(original_value, bool):
                    try:
                        corrected[key] = type(original_value)(text)
                    except ValueError:
                        corrected[key] = text
                else:
                    corrected[key] = text
        return corrected


def _capture_fields(capture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index observed controls without claiming a semantic address mapping."""
    result = {}
    pages = capture.get("pages", [])
    if not isinstance(pages, list):
        return result
    for page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        groups = [(f"pages/{page_index}/fields", page.get("fields", []))]
        dialogs = page.get("dialogs", [])
        if isinstance(dialogs, list):
            for dialog_index, dialog in enumerate(dialogs):
                if isinstance(dialog, dict):
                    groups.append((f"pages/{page_index}/dialogs/{dialog_index}/fields", dialog.get("fields", [])))
        for prefix, fields in groups:
            if not isinstance(fields, list):
                continue
            for field_index, field in enumerate(fields):
                if isinstance(field, dict) and ("value" in field or (field.get("type") in ("checkbox", "radio") and isinstance(field.get("checked"), bool))):
                    result[f"{prefix}/{field_index}"] = deepcopy(field)
    return result


def _capture_title(capture: dict[str, Any]) -> str:
    if "media_manifest" in capture:
        return "Local media ZIP · completeness unknown"
    pages = capture.get("pages", [])
    if not isinstance(pages, list):
        return "Partial observation"
    names = [str(page.get("page_key") or "editor") for page in pages if isinstance(page, dict)]
    return ", ".join(names) or "Partial observation"


class TentativeAddressDialog(QDialog):
    """Local field selection and edits, never an Engine address approval."""

    def __init__(self, capture: dict[str, Any], revision: dict[str, Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        if not isinstance(capture, dict) or capture.get("complete") is not False:
            raise ValueError("An explicitly incomplete observation is required.")
        self.original = _capture_fields(capture)
        if not self.original:
            raise ValueError("This observation contains no editable field observations.")
        self.fields: dict[str, QWidget] = {}
        self.included: dict[str, QCheckBox] = {}
        self.edit_values: dict[str, Any] = {}
        self.value_keys: dict[str, str] = {}
        previous_original = (revision or {}).get("original", {})
        previous_edited = (revision or {}).get("edited", {})
        from .address import propose_address_fields
        proposal = propose_address_fields(capture)
        suggested_paths = {item["path"] for item in proposal["fields"].values()}
        self.setWindowTitle("Tentative address · not approved")
        self.setStyleSheet(STYLE)
        self.resize(800, 760)
        self.setMinimumSize(620, 540)
        root = QVBoxLayout(self)
        root.setContentsMargins(23, 22, 23, 20)
        root.addWidget(_label("Prepare a tentative address", "pageTitle"))
        root.addWidget(_label("Suggested address controls are preselected where uniquely observed. Review every field and correct any selection. The listing, account and address mapping remain unverified.", "noticeBody", True))
        if proposal["missing"] or proposal["ambiguous"]:
            root.addWidget(_label(f"Address mapping needs review: {len(proposal['missing'])} fields not observed and {len(proposal['ambiguous'])} ambiguous. No missing values were invented.", "muted", True))
        root.addWidget(_label("These edits cannot approve the address or enable relisting. Typed option values are tentative and still need valid Hub option resolution. The incomplete source and every saved revision remain unchanged.", "muted", True))
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Find a captured field by label or name…")
        self.filter_input.setAccessibleName("Filter observed address candidates")
        root.addWidget(self.filter_input)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        editor = QWidget()
        rows = QVBoxLayout(editor)
        self.field_rows: dict[str, QWidget] = {}
        self.field_search: dict[str, str] = {}
        ordered_paths = [path for path in self.original if path in suggested_paths] + [path for path in self.original if path not in suggested_paths]
        for path in ordered_paths:
            original = self.original[path]
            title = str(original.get("label") or original.get("name") or original.get("id") or "Unlabelled observed control")
            card, box = _card()
            box.setContentsMargins(14, 12, 14, 12)
            box.setSpacing(6)
            include = QCheckBox(title)
            # A revision is only reusable when it belongs to these exact originals.
            matches = path in previous_original and previous_original[path] == original
            included = matches if revision is not None else path in suggested_paths
            include.setChecked(included)
            include.setAccessibleName(f"Include observed field: {title}")
            self.included[path] = include
            box.addWidget(include)
            box.addWidget(_label(path, "muted"))
            value_key = "checked" if original.get("type") in ("checkbox", "radio") and isinstance(original.get("checked"), bool) else "value"
            self.value_keys[path] = value_key
            initial = previous_edited.get(path, original) if matches else original
            if not isinstance(initial, dict):
                initial = original
            value = deepcopy(initial.get(value_key, original.get(value_key)))
            self.edit_values[path] = value
            source = QPlainTextEdit(json.dumps(original.get(value_key), ensure_ascii=False, indent=2))
            source.setReadOnly(True)
            source.setMaximumHeight(62)
            source.setObjectName("sourceText")
            source.setAccessibleName(f"Observed original {title}, read only")
            box.addWidget(_label("OBSERVED ORIGINAL", "eyebrow"))
            box.addWidget(source)
            if isinstance(value, bool) and value_key == "checked":
                field = QCheckBox("Yes")
                field.setChecked(value)
            elif isinstance(value, (dict, list, int, float)):
                field = QLineEdit(json.dumps(value, ensure_ascii=False))
                field.setReadOnly(True)
                field.setToolTip("Structured and numeric observed values are preserved unchanged.")
            else:
                field = QPlainTextEdit("" if value is None else str(value))
                field.setMaximumHeight(80)
            field.setAccessibleName(f"Tentative edited {title}")
            field.setEnabled(included)
            self.fields[path] = field
            box.addWidget(_label("TENTATIVE COPY", "eyebrow"))
            box.addWidget(field)
            include.toggled.connect(field.setEnabled)
            include.toggled.connect(self._validate)
            self.field_rows[path] = card
            self.field_search[path] = f"{title} {original.get('name', '')} {original.get('id', '')}".lower()
            rows.addWidget(card)
        rows.addStretch()
        scroll.setWidget(editor)
        root.addWidget(scroll, 1)
        self.filter_input.textChanged.connect(self._filter_fields)
        self.selection_hint = _label("Select at least one observed address field.", "muted", True)
        root.addWidget(self.selection_hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.addButton("Save tentative copy locally", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setObjectName("primary")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
        self._validate()

    def _filter_fields(self, query: str) -> None:
        for path, row in self.field_rows.items():
            row.setVisible(query.strip().lower() in self.field_search[path])

    def _validate(self, *_args) -> None:
        if not hasattr(self, "save_button"):
            return
        count = sum(widget.isChecked() for widget in self.included.values())
        self.save_button.setEnabled(count > 0)
        self.selection_hint.setText(f"{count} field(s) selected. No address approval will be granted.")

    def original_address(self) -> dict[str, Any]:
        return {path: deepcopy(self.original[path]) for path, include in self.included.items() if include.isChecked()}

    def corrected_address(self) -> dict[str, Any]:
        corrected = self.original_address()
        for path, original in corrected.items():
            widget = self.fields[path]
            key = self.value_keys[path]
            previous = self.edit_values[path]
            value = deepcopy(previous)
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QPlainTextEdit):
                text = widget.toPlainText()
                # Exact untouched values retain nulls, whitespace and numeric types.
                if text != ("" if previous is None else str(previous)):
                    value = text
            original[key] = value
        return corrected


class SourceFieldReviewDialog(QDialog):
    """Read-only structured copy inventory; never a relist approval."""

    def __init__(self, inventory: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.inventory = deepcopy(inventory)
        self.setWindowTitle("Copied listing fields · review required")
        self.setStyleSheet(STYLE)
        self.resize(1020, 760)
        self.setMinimumSize(740, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(23, 22, 23, 20)
        root.addWidget(_label("Review copied listing fields", "pageTitle"))
        self.summary = _label("", "noticeBody", True)
        root.addWidget(self.summary)
        self.scope_warning = _label("", "noticeBody", True)
        root.addWidget(self.scope_warning)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Find a field, page, value or status…")
        self.filter_input.setAccessibleName("Filter copied listing fields")
        root.addWidget(self.filter_input)
        self.gaps_only = QCheckBox("Show only fields needing review")
        root.addWidget(self.gaps_only)
        self.capture_gaps_button = _button("Show capture gaps and interruptions", self._show_capture_gaps)
        root.addWidget(self.capture_gaps_button)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Field", "Page", "Observed value", "Status"])
        self.table.setAccessibleName("Read-only source field inventory")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 245)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(3, 115)
        root.addWidget(self.table, 2)
        root.addWidget(_label("Original control and capture location", "smallTitle"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName("Original field provenance, plain text only")
        root.addWidget(self.detail, 1)
        root.addWidget(_label("This is a historical local copy, not a verified backup. Missing fields may not apply to this listing. Suburb selection, nested records, formatting, account ownership and complete source coverage still need verification. Nothing here edits Hub or enables relisting.", "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.rows = []
        if inventory.get("status") == "partial":
            self.rows = deepcopy(inventory.get("entries", []))
            for related in inventory.get("related_records", []):
                record = related.get("record", {})
                for group_index, group in enumerate(record.get("room_groups", [])):
                    fields = group.get("fields", [])
                    checked = sum(field.get("type") == "checkbox" and field.get("checked") is True for field in fields)
                    self.rows.append({"key": f"{related.get('path', '')}/room_groups/{group_index}",
                        "label": str(group.get("title") or "Room") + " options", "page": related.get("page_key", "details"),
                        "status": "unresolved", "kind": "room_group",
                        "display_value": f"{len(fields)} captured controls; {checked} checked options",
                        "observations": [{"path": f"{related.get('path', '')}/room_groups/{group_index}",
                            "field": deepcopy(group), "dialog_source": deepcopy(record.get("source", {}))}],
                        "issues": deepcopy(related.get("issues", []))})
            for item in inventory.get("unknowns", []):
                field = item.get("field", {})
                self.rows.append({"key": item.get("path", ""),
                    "label": field.get("label") or field.get("name") or field.get("id") or "Unmapped control",
                    "page": item.get("page_key", ""), "status": "unmapped",
                    "observations": [deepcopy(item)], "issues": [item.get("reason", "unmapped_control")]})
            observed = sum(row.get("status") == "observed" for row in self.rows)
            self.summary.setText(f"{observed} uniquely observed field(s); {len(self.rows) - observed} items missing, ambiguous, unresolved or unmapped. Select a row to inspect its original control and exact source location. Values are never invented or taken from older pages to fill a gap.")
            warnings = []
            if inventory.get('capture_status') in ('stopped', 'cancelled'):
                warnings.append('Capture was interrupted before completion.')
            pending = inventory.get('pending_room_observation_count', 0)
            if type(pending) is int and pending > 0:
                warnings.append(f'{pending} uncertain room observation(s) are excluded from this inventory; inspect the original record and current browser.')
            if inventory.get('capture_limitations'):
                warnings.append('The collector recorded limitations that may affect copied values.')
            if inventory.get('gaps'):
                warnings.append('Open capture gaps to review the remaining checks, including missing pages and unverified records.')
            self.scope_warning.setText(' '.join(warnings))
        else:
            self.summary.setText("This observation could not be safely mapped to the selected listing. The original record remains unchanged. Capture the exact matched editor again before using a copied field inventory.")
            self.detail.setPlainText(json.dumps({"issues": inventory.get("issues", [])}, ensure_ascii=False, indent=2))
        self.scope_warning.setVisible(bool(self.scope_warning.text()))
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            value = row.get("display_value", row.get("value"))
            if value is None and row.get("status") == "unmapped":
                original = row["observations"][0].get("field", {})
                value = original.get("checked") if original.get("type") in ("checkbox", "radio") else original.get("value", original.get("text"))
            rendered = "" if value is None else value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            values = (row.get("label") or row.get("key", ""), row.get("page", ""), rendered, row.get("status", ""))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(index, column, item)
        self.table.itemSelectionChanged.connect(self._show_field)
        self.filter_input.textChanged.connect(self._filter_fields)
        self.gaps_only.toggled.connect(self._filter_fields)
        if self.rows:
            self.table.selectRow(0)

    def _show_capture_gaps(self):
        self.table.clearSelection()
        self.detail.setPlainText(json.dumps({key: self.inventory.get(key) for key in
            ('capture_status', 'pending_room_observation_count', 'gaps', 'capture_limitations', 'issues', 'warning')},
            ensure_ascii=False, indent=2))

    def _show_field(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.rows) and not self.table.isRowHidden(row):
            self.detail.setPlainText(json.dumps(self.rows[row], ensure_ascii=False, indent=2))
        else:
            self.detail.clear()

    def _filter_fields(self, *_args):
        query = self.filter_input.text().casefold().strip()
        for index, row in enumerate(self.rows):
            text = " ".join(self.table.item(index, column).text() for column in range(4)).casefold()
            hidden = bool(query and query not in text) or (self.gaps_only.isChecked() and row.get("status") == "observed")
            self.table.setRowHidden(index, hidden)
        self._show_field()


class CapturedObservationsDialog(QDialog):
    """Read private immutable observations and save separate tentative revisions."""

    def __init__(self, job: dict[str, Any], records: list[dict[str, Any]], drafts: Any, parent: QWidget | None = None, *, store: Any = None):
        super().__init__(parent)
        self.job = deepcopy(job)
        self.records = deepcopy(records)
        self.drafts = drafts
        self.store = store
        self.setWindowTitle("Saved observations · incomplete")
        self.setStyleSheet(STYLE)
        self.resize(860, 730)
        root = QVBoxLayout(self)
        root.setContentsMargins(23, 22, 23, 20)
        root.addWidget(_label("Saved partial observations", "pageTitle"))
        root.addWidget(_label(f"Filed locally under {job.get('rxcu', '')}. Each record shows its own source checks. Account ownership and complete source fidelity remain unverified. Nothing here approves or runs a relist.", "noticeBody", True))
        self.capture_combo = QComboBox()
        self.capture_combo.setAccessibleName("Choose an immutable partial observation")
        for record in self.records:
            self.capture_combo.addItem(f"{_timestamp(record.get('captured_at'))} · {_capture_title(record['capture'])}", record["id"])
        root.addWidget(self.capture_combo)
        self.coverage = _label("", "muted", True)
        root.addWidget(self.coverage)
        self.source_text = QPlainTextEdit()
        self.source_text.setReadOnly(True)
        self.source_text.setAccessibleName("Saved original observation, plain text only")
        root.addWidget(self.source_text, 1)
        self.source_fields_button = _button("Review copied listing fields", self._review_source_fields)
        root.addWidget(self.source_fields_button)
        self.replacement_button = _button("Compare with a saved replacement", self._review_replacement)
        root.addWidget(self.replacement_button)
        self.revision_combo = QComboBox()
        self.revision_combo.setAccessibleName("Choose a tentative address revision")
        root.addWidget(self.revision_combo)
        self.edit_button = _button("Prepare tentative address", self._edit_address)
        root.addWidget(self.edit_button)
        self.saved_hint = _label("Private local data. This app does not encrypt it. Media classification, order, captions and backup completeness are not verified.", "muted", True)
        root.addWidget(self.saved_hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.capture_combo.currentIndexChanged.connect(self._show_capture)
        self._show_capture()

    def _show_capture(self, *_args) -> None:
        index = self.capture_combo.currentIndex()
        if not 0 <= index < len(self.records):
            self.edit_button.setEnabled(False)
            self.source_fields_button.setEnabled(False)
            self.replacement_button.setEnabled(False)
            self.source_text.clear()
            return
        record = self.records[index]
        capture = record["capture"]
        self.source_text.setPlainText(json.dumps(capture, ensure_ascii=False, indent=2))
        pages = capture.get("pages", [])
        coverage = capture.get("coverage", {})
        missing = coverage.get("missing_pages") if isinstance(coverage, dict) else None
        page_note = f"{len(pages)} observed page(s). " if isinstance(pages, list) and pages else ""
        missing_note = f"Missing pages: {', '.join(str(item) for item in missing)}. " if isinstance(missing, list) and missing else ""
        identity = capture.get("identity")
        matched = isinstance(identity, dict) and identity.get("reference_verified") is True
        self.source_fields_button.setEnabled(matched and bool(pages))
        self.replacement_button.setEnabled(matched and bool(pages)
            and capture.get("capture_kind") == "matched_partial_visible_dom"
            and self.store is not None and self.drafts is not None)
        source_note = "RXCU headings matched for each accepted page. " if matched else "RXCU association unverified. "
        stopped_note = str(capture.get("stop_reason") or "")
        self.coverage.setText(f"INCOMPLETE · {page_note}{missing_note}{source_note}A full source backup and media completeness are not verified. {stopped_note}")
        self.revision_combo.clear()
        self.revision_combo.addItem("New tentative copy from this original", None)
        try:
            revisions = self.drafts.list_addresses(record["id"])
        except Exception:
            self.saved_hint.setText("Saved revisions could not be read. Editing is blocked until the local record can be checked.")
            self.edit_button.setEnabled(False)
            return
        for revision in revisions:
            self.revision_combo.addItem(f"Tentative revision · {_timestamp(revision.get('saved_at'))}", revision)
        if revisions:
            self.revision_combo.setCurrentIndex(1)
        self.edit_button.setEnabled(bool(_capture_fields(capture)))

    def _review_source_fields(self):
        index = self.capture_combo.currentIndex()
        if not self.source_fields_button.isEnabled() or not 0 <= index < len(self.records):
            return
        dialog = None
        try:
            from .source_fields import extract_source_fields
            inventory = extract_source_fields(self.records[index]["capture"], expected_rxcu=self.job.get("rxcu"))
            dialog = SourceFieldReviewDialog(inventory, self)
            dialog.exec()
        except Exception:
            self.saved_hint.setText("The copied field inventory could not be prepared. The original observation is unchanged and no Hub action was taken.")
        finally:
            if dialog is not None:
                dialog.detail.clear()
                dialog.table.clearContents()
                dialog.deleteLater()

    def _review_replacement(self):
        index = self.capture_combo.currentIndex()
        if not self.replacement_button.isEnabled() or not 0 <= index < len(self.records):
            return
        dialog = None
        try:
            from .replacement_ui import ReplacementComparisonDialog
            dialog = ReplacementComparisonDialog(self.job, self.records[index], self.drafts, self.store, self)
            dialog.exec()
        except Exception:
            self.saved_hint.setText("The saved replacement comparison could not be opened. Saved observations are unchanged; no difference was approved and no Hub action was taken.")
        finally:
            if dialog is not None:
                dialog.clear_private_data()
                dialog.deleteLater()

    def _edit_address(self) -> None:
        index = self.capture_combo.currentIndex()
        if not self.edit_button.isEnabled() or not 0 <= index < len(self.records):
            return
        record = self.records[index]
        dialog = None
        try:
            dialog = TentativeAddressDialog(record["capture"], self.revision_combo.currentData(), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            original, edited = dialog.original_address(), dialog.corrected_address()
            self.drafts.save_address(record["id"], original, edited)
        except Exception:
            # Exceptions from storage must never leak private field data into logs.
            self.saved_hint.setText("The tentative revision could not be saved. The observed original is unchanged; no approval was granted.")
            return
        finally:
            if dialog is not None:
                dialog.deleteLater()
        self._show_capture()
        self.saved_hint.setText("Tentative revision saved locally. Original and earlier revisions preserved. Address approval remains blocked.")


class DownloadReviewDialog(QDialog):
    """Review local files without opening, importing or approving their contents."""
    def __init__(self, review: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review previous photo-pack attempt")
        self.resize(760, 570)
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Files are preserved, not verified", "sectionTitle"))
        layout.addWidget(_label("A timeout does not mean the download failed. Check the files and the browser's download history before requesting another pack. Resetting only lifts the local pause; it never retries, imports or assigns a file.", "muted", True))
        folder = QLineEdit(review["directory"])
        folder.setReadOnly(True)
        folder.setAccessibleName("Private download folder")
        layout.addWidget(folder)
        open_folder = _button("Open download folder", lambda: self._open_folder(review["directory"]))
        layout.addWidget(open_folder)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["File", "Bytes", "Observed state"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().hide()
        for row, entry in enumerate(review["files"]):
            table.insertRow(row)
            labels = {"partial": "Incomplete download", "zip_candidate": "ZIP, contents unverified", "other": "Unrecognized file"}
            for col, text in enumerate((entry["name"], str(entry["size"]), labels[entry["kind"]])):
                table.setItem(row, col, QTableWidgetItem(text))
        layout.addWidget(table, 1)
        if not review["files"]:
            layout.addWidget(_label("No files were found. Check for a pending download in any older browser before resetting.", "noticeBody", True))
        elif any(item["kind"] == "partial" for item in review["files"]):
            layout.addWidget(_label("Incomplete files will be kept as they are. Do not import them as a completed photo pack.", "noticeBody", True))
        self.closed_check = QCheckBox("I closed every Quick Relist browser, including earlier sessions.")
        self.reviewed_check = QCheckBox("I reviewed the files and pending downloads. Keep all files and allow a new request.")
        layout.addWidget(self.closed_check)
        layout.addWidget(self.reviewed_check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        self.reset_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.reset_button.setText("Keep files and reset pause")
        self.reset_button.setEnabled(False)
        self.reset_button.setAutoDefault(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        self.closed_check.toggled.connect(self._update_ready)
        self.reviewed_check.toggled.connect(self._update_ready)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_ready(self):
        self.reset_button.setEnabled(self.closed_check.isChecked() and self.reviewed_check.isChecked())

    def accept(self):
        if self.closed_check.isChecked() and self.reviewed_check.isChecked():
            super().accept()

    def _open_folder(self, directory):
        # The backend validated this generated local directory, never a URL
        # or executable name supplied by a ZIP. Opening it does not import it.
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(directory)):
            QMessageBox.information(self, "Folder unavailable", "Copy the folder path above into File Explorer to review the saved files.")


class MainWindow(QMainWindow):
    collection_progress = Signal(str)
    clipboard_requested = Signal(object)
    """Desktop shell over the safety-enforcing Engine/Store API."""

    def __init__(self, engine: Any = None, store: Any = None, hub: Any = None, drafts: Any = None, media_root: Any = None, runs: Any = None):
        super().__init__()
        self.engine = engine
        self.store = store if store is not None else getattr(engine, "store", None)
        self.hub = hub
        self.drafts = drafts
        self.runs = runs
        self.media_root = Path(media_root) if media_root is not None else None
        self._capture_sessions: dict[str, tuple[Any, Any]] = {}
        self._editor_open_job_id: str | None = None
        self.batch_id: str | None = None
        self.jobs: list[dict[str, Any]] = []
        self.batches: list[dict[str, Any]] = []
        self.selected_job: dict[str, Any] | None = None
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._operation_error: str | None = None
        self._operation_success: str = ""
        self._operation_result: Any = None
        self._operation_callback: Callable[[Any], None] | None = None
        self._is_busy = False
        self._cancel_operation_callback = None
        self._settings = QSettings("QuickRelist", "QuickRelist")
        self.setWindowTitle("Quick Relist")
        self.setMinimumSize(1060, 720)
        self.resize(1360, 900)
        self.setStyleSheet(STYLE)
        self._build_shell()
        self.clipboard_requested.connect(self._set_description_clipboard)
        self.refresh()

    def _fit_detail_actions(self):
        # Native Windows metrics may change after the first polish/show or DPI
        # transition. Recompute instead of freezing a pre-show width estimate.
        if not hasattr(self, "details_card"):
            return
        buttons = self.details_card.findChildren(QPushButton)
        if not buttons:
            return
        for button in buttons:
            button.ensurePolished()
        margins = self.details_card.layout().contentsMargins()
        needed = max(button.minimumSizeHint().width() for button in buttons) + margins.left() + margins.right() + 2
        if self.details_card.minimumWidth() != max(310, needed):
            self.details_card.setMinimumWidth(max(310, needed))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_detail_actions)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_detail_actions)

    @property
    def live_ready(self) -> bool:
        adapter = getattr(self.engine, "adapter", None)
        if getattr(adapter, "is_live_ready", False) is not True:
            return False
        qualification = getattr(adapter, "qualification", None)
        return isinstance(qualification, dict) and all(qualification.get(key) is True for key in REQUIRED_ADAPTER_QUALIFICATIONS)

    def _build_shell(self) -> None:
        shell = QWidget()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        horizontal = QHBoxLayout(shell)
        horizontal.setContentsMargins(0, 0, 0, 0)
        horizontal.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(216)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(19, 27, 19, 23)
        side.setSpacing(8)
        brand = QHBoxLayout()
        mark = _label("Q", "brandMark")
        mark.setFixedSize(43, 43)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.addWidget(mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        brand_text.addWidget(_label("Quick Relist", "brand"))
        brand_text.addWidget(_label("DESKTOP WORKSPACE", "brandSub"))
        brand.addLayout(brand_text)
        side.addLayout(brand)
        side.addSpacing(31)
        side.addWidget(_label("WORKSPACE", "navHeading"))
        side.addSpacing(5)
        self.nav_buttons: list[QPushButton] = []
        for index, text in enumerate(("Batch workspace", "Saved batches", "Connection && safety")):
            button = _button(text, lambda checked=False, i=index: self._show_page(i))
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setMinimumHeight(44)
            self.nav_buttons.append(button)
            side.addWidget(button)
        side.addStretch()
        side.addWidget(_label("A deliberate workflow", "sidebarFoot"))
        foot = _label("Capture. Review. Verify.\nOne confirmed step at a time.", "sidebarMuted", True)
        foot.setContentsMargins(0, 6, 0, 16)
        side.addWidget(foot)
        divider = QFrame()
        divider.setObjectName("sidebarLine")
        divider.setFixedHeight(1)
        side.addWidget(divider)
        side.addSpacing(12)
        side.addWidget(_label("Local workspace", "sidebarFoot"))
        side.addWidget(_label("Saved on this computer", "sidebarMuted"))
        horizontal.addWidget(sidebar)

        main = QWidget()
        main.setObjectName("workspace")
        outer = QVBoxLayout(main)
        outer.setContentsMargins(28, 25, 28, 19)
        outer.setSpacing(17)
        header = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setSpacing(5)
        self.page_title = _label("Batch workspace", "pageTitle")
        self.page_subtitle = _label("Prepare listings with a clear record of every step.", "subtitle", True)
        header_text.addWidget(self.page_title)
        header_text.addWidget(self.page_subtitle)
        header.addLayout(header_text, 1)
        self.connection_pill = _label("Hub not connected", "pill")
        self.connection_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.connection_pill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        header.addWidget(self.connection_pill)
        outer.addLayout(header)

        self.notice = QFrame()
        self.notice.setObjectName("notice")
        notice_layout = QHBoxLayout(self.notice)
        notice_layout.setContentsMargins(15, 12, 13, 12)
        notice_text = QVBoxLayout()
        notice_text.setSpacing(3)
        self.notice_title = _label("Live relisting is not available in this build", "noticeTitle")
        self.notice_body = _label("Save references and partial observations locally. Complete verified capture, portal removal, archiving and publishing require a verified integration.", "noticeBody", True)
        notice_text.addWidget(self.notice_title)
        notice_text.addWidget(self.notice_body)
        notice_layout.addLayout(notice_text, 1)
        guide = _button("View setup", lambda: self._show_page(2))
        guide.setObjectName("linkButton")
        notice_layout.addWidget(guide)
        outer.addWidget(self.notice)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_workspace())
        self.stack.addWidget(self._build_saved())
        self.stack.addWidget(self._build_connection())
        outer.addWidget(self.stack, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        outer.addWidget(self.progress)
        self.stop_operation_button = _button("Stop processing", self._stop_operation)
        self.stop_operation_button.hide()
        outer.addWidget(self.stop_operation_button)
        horizontal.addWidget(main, 1)
        self.statusBar().showMessage("Local workspace ready. No Hub actions have been performed.")
        self._show_page(0)

    def _build_workspace(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_values: dict[str, QLabel] = {}
        for key, title, note in (
            ("queued", "IN THIS BATCH", "Listing references"),
            ("reviewed", "ADDRESS REVIEWED", "Confirmed locally"),
            ("live", "CONFIRMED LIVE", "Verified by Hub"),
        ):
            card, box = _card()
            box.setContentsMargins(17, 12, 17, 12)
            box.setSpacing(4)
            box.addWidget(_label(title, "eyebrow"))
            number = _label("0", "stat")
            self.stat_values[key] = number
            box.addWidget(number)
            box.addWidget(_label(note, "muted"))
            stats.addWidget(card, 1)
        layout.addLayout(stats)

        compose, compose_layout = _card()
        compose_layout.setContentsMargins(19, 15, 19, 16)
        compose_layout.setSpacing(9)
        heading = QHBoxLayout()
        heading.addWidget(_label("Start a new batch", "sectionTitle"))
        heading.addStretch()
        heading.addWidget(_label("1. Add references    2. Capture    3. Review", "muted"))
        compose_layout.addLayout(heading)
        compose_layout.addWidget(_label("Paste RXCU references, then start. Chrome opens automatically; finish your login and OTP and the app collects the listings for your address review.", "muted", True))
        input_row = QHBoxLayout()
        self.rxcu_input = QPlainTextEdit()
        self.rxcu_input.setPlaceholderText("RXCU123456\nRXCU123457")
        self.rxcu_input.setAccessibleName("RXCU references, one per line or comma separated")
        self.rxcu_input.setFixedHeight(77)
        self.rxcu_input.textChanged.connect(self._update_input)
        input_row.addWidget(self.rxcu_input, 1)
        input_actions = QVBoxLayout()
        input_actions.setSpacing(7)
        self.create_button = _button("Start batch", self._start_batch, True)
        self.create_button.setMinimumWidth(168)
        self.create_button.setEnabled(False)
        input_actions.addWidget(self.create_button)
        self.input_hint = _label("No references entered", "muted")
        self.input_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        input_actions.addWidget(self.input_hint)
        input_actions.addStretch()
        input_row.addLayout(input_actions)
        compose_layout.addLayout(input_row)
        layout.addWidget(compose)

        collect_actions = QHBoxLayout()
        self.collect_batch_button = _button('Resume collection', self._collect_batch)
        collect_actions.addWidget(self.collect_batch_button)
        self.relist_batch_button = _button('Relist reviewed batch', self._relist_reviewed_batch, True)
        self.relist_batch_button.setToolTip('Remove and archive each original, then create, check and publish its reviewed replacement.')
        collect_actions.addWidget(self.relist_batch_button)
        collect_actions.addStretch()
        layout.addLayout(collect_actions)
        self.collection_progress.connect(self.statusBar().showMessage)
        self.operation_message = _label('Start a batch to open Hub. After sign-in, collection continues automatically.', 'noticeBody', True)
        self.collection_progress.connect(self.operation_message.setText)
        layout.addWidget(self.operation_message)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setMinimumHeight(415)
        queue_card, queue_layout = _card()
        queue_card.setMinimumWidth(390)
        queue_layout.setContentsMargins(0, 16, 0, 8)
        queue_heading = QHBoxLayout()
        queue_heading.setContentsMargins(18, 0, 18, 0)
        queue_text = QVBoxLayout()
        queue_text.setSpacing(4)
        queue_text.addWidget(_label("Listing queue", "sectionTitle"))
        self.queue_subtitle = _label("Create a batch to begin", "muted")
        queue_text.addWidget(self.queue_subtitle)
        queue_heading.addLayout(queue_text, 1)
        refresh = _button("Refresh", self.refresh)
        refresh.setAccessibleName("Refresh local batch data")
        queue_heading.addWidget(refresh)
        queue_layout.addLayout(queue_heading)
        self.queue_stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(25, 20, 25, 20)
        empty_layout.addStretch()
        empty_icon = _label("+", "brandMark")
        empty_icon.setFixedSize(44, 44)
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon, 0, Qt.AlignmentFlag.AlignCenter)
        empty_title = _label("Your next batch starts here", "sectionTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_copy = _label("Add RXCU references above, or reopen a saved batch.\nNo listing data is loaded until Hub capture succeeds.", "muted", True)
        empty_copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_copy)
        empty_layout.addStretch()
        self.queue_stack.addWidget(empty)
        self.queue_table = self._make_table(["RXCU REFERENCE", "ADDRESS", "STATE"])
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.itemSelectionChanged.connect(self._select_job)
        self.queue_table.itemDoubleClicked.connect(lambda _: self._review_address())
        self.queue_stack.addWidget(self.queue_table)
        queue_layout.addWidget(self.queue_stack, 1)
        queue_layout.addWidget(_label("  A saved queue is not a completed relist.", "muted"))
        splitter.addWidget(queue_card)

        self.details_card, details = _card()
        self.details_card.setMinimumWidth(310)
        details.setSpacing(10)
        details.addWidget(_label("SELECTED LISTING", "eyebrow"))
        self.detail_title = _label("Nothing selected", "sectionTitle")
        details.addWidget(self.detail_title)
        self.detail_state = _label("Choose a row to review its next step.", "muted", True)
        details.addWidget(self.detail_state)
        self.detail_address = _label("Original address appears only after a complete Hub capture.", "muted", True)
        details.addWidget(self.detail_address)
        self.detail_error = _label("", "noticeBody", True)
        self.detail_error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_error.hide()
        details.addWidget(self.detail_error)
        details.addSpacing(2)
        self.verify_captured_address_button = _button('Verify address', self._verify_captured_address, True)
        details.addWidget(self.verify_captured_address_button)
        self.check_publication_button = _button('Check publication', self._check_publication)
        self.check_publication_button.hide()
        details.addWidget(self.check_publication_button)
        self.record_recovery_button = _button('Record work completed in Hub', self._record_hub_recovery)
        self.record_recovery_button.hide()
        details.addWidget(self.record_recovery_button)
        self.check_existing_copy_button = _button('Check existing replacement', self._check_existing_copy)
        self.check_existing_copy_button.hide()
        details.addWidget(self.check_existing_copy_button)
        self.collection_summary = _label('Collect this batch to load its details and original photos.', 'muted', True)
        details.addWidget(self.collection_summary)
        self.diagnostics_toggle = QCheckBox('Show collection tools')
        details.addWidget(self.diagnostics_toggle)
        self.diagnostics_panel = QWidget()
        outer_details = details
        details = QVBoxLayout(self.diagnostics_panel)
        details.setContentsMargins(0, 0, 0, 0)
        outer_details.addWidget(self.diagnostics_panel)
        self.diagnostics_panel.hide()
        self.diagnostics_toggle.toggled.connect(self.diagnostics_panel.setVisible)
        self.search_hub_button = _button("Find RXCU in Hub (read only)", self._search_selected_reference)
        self.search_hub_button.setToolTip("Search Manage Listings in the dedicated Hub browser. Review results and filters manually.")
        details.addWidget(self.search_hub_button)
        self.open_editor_button = _button("Open exact matching editor", self._open_selected_editor)
        self.open_editor_button.setToolTip("Open the unique exact RXCU result's Edit Listing link and verify the editor headings. No listing is changed.")
        details.addWidget(self.open_editor_button)
        self.recheck_editor_button = _button("Recheck opened editor (read only)", self._recheck_selected_editor)
        self.recheck_editor_button.setToolTip("Read the existing tab from this job's unresolved opening. Never click its link again or open another browser.")
        details.addWidget(self.recheck_editor_button)
        self.editor_open_guidance = _label("", "muted", True)
        details.addWidget(self.editor_open_guidance)
        self.capture_all_pages_button = _button("Capture matched editor tabs", self._capture_all_pages)
        self.capture_all_pages_button.setToolTip("Read each observed editor tab with RXCU and tab checks. Saves incomplete private source data; never changes Hub.")
        details.addWidget(self.capture_all_pages_button)
        self.capture_page_button = _button("Save current page (incomplete)", self._capture_current_page)
        self.capture_page_button.setToolTip("Read visible controls without changing Hub. Saves private observations under the selected local job; the RXCU association is not verified.")
        details.addWidget(self.capture_page_button)
        self.saved_captures_button = _button("Review partial observations", self._show_saved_captures)
        details.addWidget(self.saved_captures_button)
        self.download_media_button = _button("Download photo pack", self._download_media)
        self.download_media_button.setToolTip("Download the verified editor's photo pack into a private local folder. Contents and completeness remain unverified.")
        details.addWidget(self.download_media_button)
        self.review_download_button = _button("Review previous download", self._review_download)
        details.addWidget(self.review_download_button)
        self.import_media_button = _button("Import local media ZIP", self._import_media)
        self.import_media_button.setToolTip("Preserve original downloaded ZIP and image bytes locally. This does not verify media identity, order, captions or completeness.")
        details.addWidget(self.import_media_button)
        self.observation_status = _label("No partial observations saved.", "muted", True)
        details.addWidget(self.observation_status)
        self.capture_button = _button("1  Capture original listing", self._capture)
        self.capture_button.setToolTip("Reads the original listing through a verified Hub adapter. Unavailable in the unconnected build.")
        details.addWidget(self.capture_button)
        self.review_button = _button("2  Review address", self._review_address)
        self.review_button.setToolTip("Available only after a complete source capture.")
        details.addWidget(self.review_button)
        self.advance_button = _button("3  Run next verified step", self._advance, True)
        details.addWidget(self.advance_button)
        self.reconcile_button = _button("Check uncertain outcome", self._reconcile)
        self.reconcile_button.hide()
        details.addWidget(self.reconcile_button)
        details = outer_details
        self.cancel_job_button = _button("Cancel unstarted job", self._cancel_job)
        self.cancel_job_button.setObjectName("danger")
        self.cancel_job_button.setToolTip("Cancel this local queue job before any destructive operation. Its saved reference and activity history remain.")
        details.addWidget(self.cancel_job_button)
        details.addStretch(1)
        self.audit_button = _button("View activity record", self._show_journal)
        self.audit_button.setObjectName("linkButton")
        details.addWidget(self.audit_button)
        self.details_card.ensurePolished()
        for button in self.details_card.findChildren(QPushButton):
            button.ensurePolished()
        margins = details.contentsMargins()
        action_width = max(button.minimumSizeHint().width() for button in self.details_card.findChildren(QPushButton))
        self.details_card.setMinimumWidth(max(310, action_width + margins.left() + margins.right() + 2))
        splitter.addWidget(self.details_card)
        splitter.setSizes([660, self.details_card.minimumWidth()])
        layout.addWidget(splitter, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _build_saved(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)
        card, box = _card()
        bar = QHBoxLayout()
        bar.addWidget(_label("Your saved batches", "sectionTitle"), 1)
        self.saved_search = QLineEdit()
        self.saved_search.setPlaceholderText("Search a batch or RXCU…")
        self.saved_search.setClearButtonEnabled(True)
        self.saved_search.setMinimumWidth(230)
        self.saved_search.setAccessibleName("Search saved batches")
        self.saved_search.textChanged.connect(self._filter_saved)
        bar.addWidget(self.saved_search)
        bar.addWidget(_button("Refresh", self.refresh))
        box.addLayout(bar)
        box.addWidget(_label("Batches and their activity records are stored on this computer. Open one to continue safely from its recorded state.", "muted", True))
        self.saved_table = self._make_table(["BATCH", "CREATED", "LISTINGS", "STATUS"])
        self.saved_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index in (1, 2, 3):
            self.saved_table.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        self.saved_table.itemDoubleClicked.connect(lambda _: self._open_batch())
        self.saved_table.itemSelectionChanged.connect(self._saved_selection_changed)
        box.addWidget(self.saved_table, 1)
        self.saved_empty = _label("No saved batches yet. Create your first batch in the workspace.", "muted", True)
        self.saved_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.saved_empty)
        footer = QHBoxLayout()
        footer.addWidget(_label("Double-click a batch to open it.", "muted"), 1)
        self.open_batch_button = _button("Open selected batch", self._open_batch, True)
        self.open_batch_button.setEnabled(False)
        footer.addWidget(self.open_batch_button)
        box.addLayout(footer)
        layout.addWidget(card, 1)
        privacy, privacy_box = _card()
        privacy_box.addWidget(_label("Keep your local records private", "smallTitle"))
        privacy_box.addWidget(_label("Source captures may contain property, contact, and address details. Use a trusted Windows account and protect this computer. Login passwords are not collected by this app.", "muted", True))
        layout.addWidget(privacy)
        return page

    def _build_connection(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 5, 0)
        layout.setSpacing(15)
        browser_card, browser_layout = _card()
        browser_layout.addWidget(_label("Read-only Hub inspection", "sectionTitle"))
        browser_layout.addWidget(_label("Open a dedicated Chrome window, sign in yourself, then navigate to one listing editor. Inspection reads the current editor without saving or changing it.", "muted", True))
        browser_actions = QHBoxLayout()
        self.open_browser_button = _button("Open Hub for sign-in", self._open_readonly_hub, True)
        self.open_browser_button.setEnabled(self.hub is not None)
        self.inspect_browser_button = _button("Inspect current page", self._inspect_readonly_hub)
        self.inspect_browser_button.setEnabled(self.hub is not None)
        browser_actions.addWidget(self.open_browser_button)
        browser_actions.addWidget(self.inspect_browser_button)
        browser_actions.addStretch()
        browser_layout.addLayout(browser_actions)
        self.review_download_session_button = _button("Review previous photo-pack attempt", self._review_download)
        browser_layout.addWidget(self.review_download_session_button)
        browser_layout.addWidget(_label("This preview is partial, unverified and shown in memory only. To preserve a page locally, select a queued reference in Batch workspace and use Save current page. Partial observations and tentative edits never approve an address or enable live changes.", "noticeBody", True))
        layout.addWidget(browser_card)
        connection, connection_layout = _card()
        connection_layout.addWidget(_label("Sign in to Hub yourself", "sectionTitle"))
        connection_layout.addWidget(_label("Use your normal Hub website in your browser. Quick Relist never asks for your password, one-time code, or recovery details.", "muted", True))
        connection_layout.addWidget(_label("Your Hub sign-in URL", "smallTitle"))
        self.hub_url = QLineEdit(str(self._settings.value("hub_url", "")))
        self.hub_url.setPlaceholderText("Paste your official https:// Hub sign-in address")
        self.hub_url.setAccessibleName("Official Hub sign-in HTTPS URL")
        connection_layout.addWidget(self.hub_url)
        url_actions = QHBoxLayout()
        url_actions.addWidget(_button("Open sign-in page", self._open_hub, True))
        url_actions.addWidget(_button("Save URL locally", self._save_url))
        url_actions.addStretch()
        connection_layout.addLayout(url_actions)
        connection_layout.addWidget(_label("Opening your browser does not connect this app to that browser session or enable live relisting.", "noticeBody", True))
        layout.addWidget(connection)

        checklist, checklist_layout = _card()
        checklist_layout.addWidget(_label("How live relisting is checked", "sectionTitle"))
        for number, title, description in (
            ("01", "The correct Hub listing", "Quick Relist checks the listing reference and saved details before making changes. Keep the dedicated Hub browser open while the app is working."),
            ("02", "Complete source capture", "The original address, listing fields, media, and supporting information must be recorded before changes. Missing source data blocks the workflow."),
            ("03", "Your address review", "Review each prefilled address before starting the batch. Sales use Listing has been cancelled and Mandate expired; rentals use Listing has been withdrawn. Saving an address review alone makes no Hub changes."),
            ("04", "One verified operation at a time", "Removal, archiving, copying, and publication are separate recorded steps. An uncertain result must be checked before a retry to avoid duplicate listings."),
        ):
            row = QHBoxLayout()
            tag = _label(number, "pill")
            tag.setFixedWidth(39)
            tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(tag, 0, Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(4)
            text.addWidget(_label(title, "smallTitle"))
            text.addWidget(_label(description, "muted", True))
            row.addLayout(text, 1)
            checklist_layout.addLayout(row)
        layout.addWidget(checklist)
        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(51)
        table.setWordWrap(False)
        table.setMinimumHeight(100)
        return table

    @Slot(int)
    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        titles = ("Batch workspace", "Saved batches", "Connection & safety")
        subtitles = (
            "Prepare listings with a clear record of every step.",
            "Pick up where you left off, without losing the activity record.",
            "Manual sign-in. Verified actions. No silent assumptions.",
        )
        self.page_title.setText(titles[index])
        self.page_subtitle.setText(subtitles[index])
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

    @staticmethod
    def _tokens(text: str) -> list[str]:
        from .core import normalize_rxcu, ValidationError
        tokens = []
        for item in re.split(r"[\s,;]+", text):
            if not item.strip():
                continue
            try:
                tokens.append(normalize_rxcu(item))
            except ValidationError:
                tokens.append(item.strip().upper())  # Engine gives the validation message.
        return tokens

    @Slot()
    def _update_input(self) -> None:
        tokens = self._tokens(self.rxcu_input.toPlainText())
        unique = list(dict.fromkeys(tokens))
        duplicates = len(tokens) - len(unique)
        hint = f"{len(unique)} reference{'s' if len(unique) != 1 else ''}"
        if duplicates:
            hint += f" · {duplicates} duplicate{'s' if duplicates != 1 else ''}"
        self.input_hint.setText(hint if tokens else "No references entered")
        self.create_button.setEnabled(bool(tokens) and bool(self.engine) and not self._is_busy)

    @Slot()
    def _start_batch(self) -> None:
        previous = self.batch_id
        self._create_batch()
        if self.batch_id != previous:
            self._collect_batch()

    @Slot()
    def _create_batch(self) -> None:
        if not self.engine or self._is_busy:
            return
        tokens = self._tokens(self.rxcu_input.toPlainText())
        unique = list(dict.fromkeys(tokens))
        if not unique:
            return
        if len(unique) != len(tokens):
            QMessageBox.information(self, "Duplicate references", "Repeated references will be included once in this batch.")
        try:
            batch_id = self.engine.new_batch(unique)
        except Exception as exc:
            self._show_error("Batch not created", str(exc))
            return
        self.batch_id = str(batch_id)
        self.rxcu_input.clear()
        self.refresh()
        self._show_page(0)
        self.statusBar().showMessage(f"Saved {len(unique)} reference{'s' if len(unique) != 1 else ''} locally. No Hub listing was changed.")

    @Slot()
    def refresh(self) -> None:
        if self._is_busy:
            return
        selected_id = self.selected_job.get("id") if self.selected_job else None
        try:
            self.batches = list(self.store.list_batches()) if self.store else []
            if self.batch_id is None and self.batches:
                self.batch_id = str(self.batches[-1]["id"])
            self.jobs = list(self.store.list_jobs(self.batch_id)) if self.store and self.batch_id else []
        except Exception as exc:
            self._show_error("Could not read local workspace", str(exc))
            return
        self.connection_pill.setText("Verified adapter available" if self.live_ready else ("Hub browser open" if getattr(self.hub, 'driver', None) is not None else "Hub not connected"))
        self.notice.setVisible(not self.live_ready)
        if not self.engine or not self.store:
            self.notice_title.setText("Local backend is not available")
            self.notice_body.setText("This interface is open for review. Start Quick Relist with its Engine and local Store to save batches. No Hub actions are enabled.")
        elif self.runs is not None:
            self.notice_title.setText('Review addresses before relisting')
            self.notice_body.setText('Collect the listings and photos, save each address review, then relist the batch. Each original is removed and archived before its replacement is saved and published.')
        self.stat_values["queued"].setText(str(len(self.jobs)))
        self.collect_batch_button.setEnabled(any(job.get('state') == 'queued' for job in self.jobs) and self.hub is not None and self.drafts is not None and self.media_root is not None and not self._is_busy)
        collected_views = {job['id']: _collected_job_view(self.drafts, job) for job in self.jobs}
        self.stat_values["reviewed"].setText(str(sum(bool(job.get("approved")) or bool((collected_views[job['id']] or {}).get('reviewed')) for job in self.jobs)))
        run_statuses = {job['id']: _run_status(self.runs, job) for job in self.jobs}
        pending_jobs = [j for j in self.jobs if j.get('state')!='cancelled'
                        and (run_statuses[j['id']] or {}).get('state') not in ('published','manual_completed')]
        self.relist_batch_button.setEnabled(bool(pending_jobs) and self.runs is not None
            and getattr(self.hub,'driver',None) is not None and not self._is_busy
            and all(bool((collected_views[j['id']] or {}).get('reviewed')) for j in pending_jobs))
        self.stat_values["live"].setText(str(sum(job.get("state") == "live" or (run_statuses[job['id']] or {}).get('state') in ('published','manual_completed') for job in self.jobs)))
        batch = next((item for item in self.batches if str(item.get("id")) == self.batch_id), None)
        if batch:
            self.queue_subtitle.setText(f"Saved {_timestamp(batch.get('created_at'))} · {len(self.jobs)} listing{'s' if len(self.jobs) != 1 else ''}")
        else:
            self.queue_subtitle.setText("Create a batch to begin")
        self.queue_table.blockSignals(True)
        self.queue_table.setRowCount(len(self.jobs))
        selected_row = 0
        for row, job in enumerate(self.jobs):
            if job.get("id") == selected_id:
                selected_row = row
            address = job.get("corrected_address") or job.get("original_address")
            collected = collected_views[job['id']] if job.get('state') == 'queued' else None
            run = run_statuses[job['id']]
            state_text = _relist_state_label(run) if run else (collected['state'] if collected else _state_label(job.get("state")))
            values = (str(job.get("rxcu", "")), _address_summary(collected['address'] if collected else address), state_text)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.ItemDataRole.UserRole, job.get("id"))
                if column == 0:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if column == 2 and job.get("state") in ("needs_reconciliation", "blocked"):
                    item.setForeground(QColor("#ad6236"))
                elif column == 2 and job.get("state") == "live":
                    item.setForeground(QColor("#26764d"))
                self.queue_table.setItem(row, column, item)
        self.queue_table.blockSignals(False)
        self.queue_stack.setCurrentIndex(1 if self.jobs else 0)
        if self.jobs:
            self.queue_table.selectRow(selected_row)
        else:
            self.selected_job = None
        self._select_job()
        self._refresh_saved()
        self._update_input()

    def _refresh_saved(self) -> None:
        self.saved_table.setRowCount(len(self.batches))
        for row, batch in enumerate(self.batches):
            batch_id = str(batch.get("id", ""))
            batch_jobs = self.store.list_jobs(batch_id) if self.store else []
            live = sum(job.get("state") == "live" for job in batch_jobs)
            cancelled = sum(job.get("state") == "cancelled" for job in batch_jobs)
            needs_check = sum(job.get("state") in ("needs_reconciliation", "blocked") for job in batch_jobs)
            status = "Needs attention" if needs_check else ("Cancelled locally" if batch_jobs and cancelled == len(batch_jobs) else ("Confirmed live" if batch_jobs and live == len(batch_jobs) else "Local queue saved"))
            title = str(batch.get("name") or f"Batch {batch_id[:8].upper()}")
            values = (title, _timestamp(batch.get("created_at")), str(len(batch_jobs)), status)
            search_data = " ".join([title, batch_id] + [str(job.get("rxcu", "")) for job in batch_jobs]).lower()
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, batch_id)
                item.setData(Qt.ItemDataRole.UserRole + 1, search_data)
                item.setToolTip(value)
                self.saved_table.setItem(row, column, item)
        self.saved_empty.setVisible(not bool(self.batches))
        self._filter_saved()
        self._saved_selection_changed()

    @Slot()
    def _filter_saved(self) -> None:
        query = self.saved_search.text().strip().lower()
        visible = 0
        for row in range(self.saved_table.rowCount()):
            item = self.saved_table.item(row, 0)
            match = bool(item) and query in str(item.data(Qt.ItemDataRole.UserRole + 1))
            self.saved_table.setRowHidden(row, not match)
            visible += bool(match)
        if self.batches:
            self.saved_empty.setText("No batches match your search.")
            self.saved_empty.setVisible(not visible)
        else:
            self.saved_empty.setText("No saved batches yet. Create your first batch in the workspace.")
            self.saved_empty.show()
        self._saved_selection_changed()

    @Slot()
    def _saved_selection_changed(self) -> None:
        row = self.saved_table.currentRow()
        self.open_batch_button.setEnabled(row >= 0 and not self.saved_table.isRowHidden(row) and not self._is_busy)

    @Slot()
    def _open_batch(self) -> None:
        row = self.saved_table.currentRow()
        item = self.saved_table.item(row, 0)
        if not item or self.saved_table.isRowHidden(row) or self._is_busy:
            return
        self.batch_id = str(item.data(Qt.ItemDataRole.UserRole))
        self.selected_job = None
        self.refresh()
        self._show_page(0)

    @Slot()
    def _select_job(self) -> None:
        row = self.queue_table.currentRow()
        self.selected_job = self.jobs[row] if 0 <= row < len(self.jobs) else None
        job = self.selected_job
        self.verify_captured_address_button.setEnabled(False)
        self.check_publication_button.hide()
        self.record_recovery_button.hide()
        self.check_existing_copy_button.hide()
        self.collection_summary.setText('Collect this batch to load its details and original photos.')
        if job and self.drafts:
            from .collection import latest_collection
            collection = latest_collection(self.drafts, job['id'])
            if collection:
                summary = collection['capture']
                self.verify_captured_address_button.setEnabled(not self._is_busy)
                reviews = self.drafts.list_addresses(summary['source_capture_id'])
                self.collection_summary.setText(f"{summary['media_count']} original photos checked. " + ('Address review saved.' if reviews else 'Address ready for your review.'))
        self.capture_button.setEnabled(False)
        self.search_hub_button.setEnabled(False)
        self.open_editor_button.setEnabled(False)
        self.recheck_editor_button.setEnabled(False)
        self.editor_open_guidance.setText("")
        self.editor_open_guidance.hide()
        self.review_button.setEnabled(False)
        self.capture_page_button.setEnabled(False)
        self.capture_all_pages_button.setEnabled(False)
        self.saved_captures_button.setEnabled(False)
        self.import_media_button.setEnabled(False)
        self.download_media_button.setEnabled(False)
        self.observation_status.setText("No partial observations saved.")
        self.advance_button.setEnabled(False)
        self.reconcile_button.setEnabled(False)
        self.reconcile_button.hide()
        self.cancel_job_button.setEnabled(False)
        self.audit_button.setEnabled(bool(job) and not self._is_busy)
        self.detail_error.hide()
        guard = self.media_root.parent / "downloads" / "download-review-required.json" if self.media_root is not None else None
        try:
            pending = guard is not None and (guard.exists() or guard.is_symlink())
        except OSError:
            pending = True
        for button in (self.review_download_button, self.review_download_session_button):
            button.setEnabled(pending and not self._is_busy)
        if not job:
            self.detail_title.setText("Nothing selected")
            self.detail_state.setText("Choose a row to review its next step.")
            self.detail_address.setText("Original address appears only after a complete Hub capture.")
            return
        state = str(job.get("state", "queued"))
        opening = self._editor_open_state(str(job.get("rxcu")))
        self.search_hub_button.setEnabled(self.hub is not None and opening == "none" and not self._is_busy)
        self.open_editor_button.setEnabled(self.hub is not None and opening == "none" and not self._is_busy)
        self.recheck_editor_button.setEnabled(opening == "pending" and not self._is_busy)
        guidance = {
            "pending": "The last editor opening is still unverified. Leave its tabs and results unchanged, then choose Recheck opened editor (read only). It reads the existing tab without clicking the link again. Partial page observations remain incomplete and unverified.",
            "other": "Another local job has an unresolved editor opening. Select its original job to recheck the existing tab. Do not open another editor or change the results.",
            "blocked": "The opened editor cannot be safely matched in this session. Close every dedicated Quick Relist browser and restart Quick Relist. No force or reset can approve this opening. Saved partial observations remain incomplete and unverified.",
        }.get(opening, "")
        self.editor_open_guidance.setText(guidance)
        self.editor_open_guidance.setVisible(bool(guidance))
        unstarted = state in ("queued", "awaiting_address", "ready", "blocked")
        self.capture_page_button.setEnabled(self.hub is not None and self.drafts is not None and unstarted and not self._is_busy)
        self.import_media_button.setEnabled(self.drafts is not None and self.media_root is not None and unstarted and not self._is_busy)
        bindings = getattr(self.hub, "editor_bindings", {}) if self.hub is not None else {}
        self.capture_all_pages_button.setEnabled(opening == "none" and bool(bindings.get(str(job.get("rxcu")))) and self.drafts is not None and unstarted and not self._is_busy)
        self.download_media_button.setEnabled(opening == "none" and bool(bindings.get(str(job.get("rxcu")))) and self.drafts is not None and self.media_root is not None and unstarted and not self._is_busy and not pending)
        self.download_media_button.setToolTip("Automatic downloads are paused after an attempt. Use Review previous download to preserve files and reset the pause after checking the browser." if pending else "Request a photo pack from the matched editor. Its listing association and completeness remain unverified.")
        if self.drafts is not None:
            try:
                observations = self.drafts.list_captures(job["id"])
                self.saved_captures_button.setEnabled(bool(observations) and not self._is_busy)
                if observations:
                    self.observation_status.setText(f"{len(observations)} incomplete observation(s). RXCU association and media completeness unverified.")
            except Exception:
                self.observation_status.setText("Local observations could not be read. No capture approval is available.")
        self.detail_title.setText(str(job.get("rxcu", "Listing")))
        self.detail_state.setText(_state_label(state))
        self.detail_address.setText(_address_summary(job.get("corrected_address") or job.get("original_address")))
        collected_view = _collected_job_view(self.drafts, job) if state == 'queued' else None
        if collected_view:
            self.detail_state.setText(collected_view['state'])
            self.detail_address.setText(_address_summary(collected_view['address']))
        run_status = _run_status(self.runs, job)
        if run_status:
            self.detail_state.setText(_relist_state_label(run_status))
            if run_status.get('state') == 'needs_review':
                self.collection_summary.setText('This run stopped. The activity record shows earlier observations, not the current Hub state. Check the original and any existing replacement before continuing. Starting another batch will not bypass this record.')
                self.record_recovery_button.setVisible(True)
                self.record_recovery_button.setEnabled(not self._is_busy)
                from .creation_recovery import can_check_creation
                if can_check_creation(self.runs.get(job['id'])):
                    self.check_existing_copy_button.setVisible(True)
                    self.check_existing_copy_button.setEnabled(not self._is_busy and self.hub is not None)
            if run_status.get('state') in ('publication_pending','manual_publication_pending'):
                self.detail_state.setText('Waiting for portals · ' + run_status['target_reference'])
                names={'website':'RE/MAX','p24':'Property24','pp':'Private Property','global':'RE/MAX Global',
                       'iol':'IOL Property','immoafrica':'ImmoAfrica','flow':'Flow Living'}
                portals=run_status.get('publication',{}).get('portals',{})
                confirmed=[names.get(k,k) for k,v in portals.items() if v.get('state')=='published']
                waiting=[names.get(k,k) for k,v in portals.items() if v.get('state')!='published']
                self.collection_summary.setText('Confirmed: '+(', '.join(confirmed) or 'none yet')+
                    '. Waiting: '+(', '.join(waiting) or 'none')+'. Original archived; replacement checked.')
                self.check_publication_button.setVisible(True)
                self.check_publication_button.setEnabled(not self._is_busy and self.hub is not None)
                if run_status.get('mode') == 'manual_recovery':
                    self.collection_summary.setText('Recovery recorded from your checks in Hub. Confirmed: '+(', '.join(confirmed) or 'none yet')+'. Waiting: '+(', '.join(waiting) or 'none')+'. Earlier attempts remain in the activity record.')
            if run_status.get('state') == 'manual_completed':
                self.collection_summary.setText('Replacement completed directly in Hub and recorded locally. Earlier attempts are retained and will not be repeated.')
        if run_status and run_status.get('state') == 'published':
            self.detail_state.setText('Relisted · ' + run_status['target_reference'])
            self.collection_summary.setText('Replacement published. Original archived. Saved source details and photos are retained locally.')
            self.verify_captured_address_button.setEnabled(False)
        if job.get("error"):
            self.detail_error.setText(str(job["error"]))
            self.detail_error.show()
        elif not self.live_ready and state == "queued" and not collected_view:
            self.detail_error.setText("A complete, verified Hub capture is unavailable. Partial observations and tentative edits cannot approve a relist.")
            self.detail_error.show()
        snapshot = job.get("snapshot")
        captured = bool(isinstance(snapshot, dict) and snapshot.get("complete") is True and isinstance(job.get("original_address"), dict) and job.get("original_address"))
        self.capture_button.setEnabled(bool(self.engine) and self.live_ready and state == "queued" and not self._is_busy)
        self.review_button.setEnabled(bool(self.engine) and captured and state in ("awaiting_address", "ready") and not self._is_busy)
        self.advance_button.setEnabled(bool(self.engine) and self.live_ready and bool(job.get("approved")) and state in ("ready", "portals_removed", "archived", "created", "fidelity_verified", "publication_pending") and not self._is_busy)
        self.reconcile_button.setVisible(state == "needs_reconciliation")
        self.reconcile_button.setEnabled(bool(self.engine) and self.live_ready and state == "needs_reconciliation" and not self._is_busy)
        if self.engine and self.store and state in ("queued", "awaiting_address", "ready", "blocked") and not self._is_busy:
            try:
                history = self.store.journal(job["id"])
                destructive_started = any(entry.get("action") in ("remove_portals", "archive", "create", "publish") for entry in history)
                self.cancel_job_button.setEnabled(not destructive_started)
            except Exception:
                # Do not offer cancellation when the operation history cannot be checked.
                self.cancel_job_button.setEnabled(False)
        self.advance_button.setToolTip("A verified live Hub adapter and an approved address are required.")
        if self.runs is not None and job and self.runs.get(job['id']):
            self.cancel_job_button.setEnabled(False)
            self.verify_captured_address_button.setEnabled(False)

    @Slot(object)
    def _set_description_clipboard(self, request):
        try:
            mime = QMimeData()
            if sys.platform == 'win32':
                from .html_clipboard import windows_html
                mime.setData('application/x-qt-windows-mime;value="HTML Format"', windows_html(request['html']))
            else:
                mime.setHtml(request['html'])
            if request['text'] is not None:
                mime.setText(request['text'])
            QApplication.clipboard().setMimeData(mime)
            request['ok'] = True
        finally:
            request['done'].set()

    def _description_clipboard(self, html, text):
        request = {'html':html,'text':text,'done':threading.Event(),'ok':False}
        self.clipboard_requested.emit(request)
        if not request['done'].wait(15) or not request['ok']:
            raise RuntimeError('The formatted description could not be prepared for Hub.')

    @Slot()
    def _record_hub_recovery(self):
        if self._is_busy or not self.selected_job or self.runs is None or self.drafts is None:
            return
        from .collection import latest_collection
        from .manual_recovery import recovery_dialog
        from .portal_ui import active_destinations
        job = self.selected_job
        if (_run_status(self.runs, job) or {}).get('state') != 'needs_review':
            return
        saved = latest_collection(self.drafts, job['id'])
        if not saved:
            return
        try:
            destinations = active_destinations(saved['capture']['portal_observation'])
            dialog, values = recovery_dialog(job['rxcu'], destinations, self)
            dialog.setStyleSheet(STYLE)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self.runs.record_manual_recovery(job['id'], source_capture_id=saved['capture']['source_capture_id'], **values())
            self.refresh()
            self.statusBar().showMessage('Hub recovery recorded locally. No listing was changed.')
        except ValueError as exc:
            self._show_error('Check the recovery details', str(exc))
        except Exception as exc:
            self._show_error('Recovery was not recorded', str(exc))

    @Slot()
    def _check_existing_copy(self):
        if self._is_busy or not self.selected_job or self.runs is None or self.drafts is None or self.hub is None:
            return
        from .creation_recovery import can_check_creation, check_existing_replacement, recovery_dialog
        from .live_relist_ui import HubRelistUI
        from .hub_login import wait_for_hub_login
        job = deepcopy(self.selected_job)
        run = self.runs.get(job['id'])
        if not can_check_creation(run):
            return
        dialog, values = recovery_dialog(run['binding'], self)
        dialog.setStyleSheet(STYLE)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        reference, listing_id = values()
        stop = threading.Event()
        def check():
            if self.hub.driver is None:
                self._safe_readonly_call(self.hub.open, opening=True)
            wait_for_hub_login(self.hub.driver, cancelled=stop.is_set, progress=self.collection_progress.emit)
            provider = HubRelistUI(self.hub, self.media_root, clipboard=self._description_clipboard, cancelled=stop.is_set)
            return check_existing_replacement(self.drafts, self.runs, provider, job, reference, listing_id, cancelled=stop.is_set)
        self._run_operation('Checking the original and the existing replacement…', check,
                            'Existing copy verified. Choose Relist reviewed batch to continue that copy.', cancel=stop.set)

    @Slot()
    def _check_publication(self):
        if self._is_busy or not self.selected_job or self.runs is None or self.hub is None:
            return
        job=deepcopy(self.selected_job)
        status=_run_status(self.runs,job)
        if not status or status.get('state') not in ('publication_pending','manual_publication_pending'):
            return
        from .live_relist_ui import HubRelistUI
        from .hub_login import wait_for_hub_login
        stop = threading.Event()
        def check():
            if self.hub.driver is None:
                self._safe_readonly_call(self.hub.open, opening=True)
            wait_for_hub_login(self.hub.driver, cancelled=stop.is_set, progress=self.collection_progress.emit)
            provider=HubRelistUI(self.hub,self.media_root,clipboard=self._description_clipboard,cancelled=stop.is_set)
            outcome=provider.recheck_publication(status)
            if status.get('mode') == 'manual_recovery':
                return self.runs.check_manual_publication(job['id'], status, outcome)
            status.update(state='published' if outcome['complete'] else 'publication_pending',publication=outcome)
            self.runs.note(self.runs.get(job['id'])['id'],'status',status)
            return status
        self._run_operation('Checking the existing publication requests…',check,'Publication status refreshed. No new request was sent.',cancel=stop.set)

    @Slot()
    def _relist_reviewed_batch(self):
        if self._is_busy or self.runs is None or getattr(self.hub,'driver',None) is None:
            return
        jobs = deepcopy(self.jobs)
        from .collection import latest_collection
        from .relist_plan import build_relist_plan
        from .relist_runner import RelistRunner, removal_can_be_checked, copy_can_be_checked
        from .live_relist_ui import HubRelistUI
        from .form_copy import CopyStopped
        from .run_store import RunNeedsReview
        def preflight():
            prepared=[]
            for job in jobs:
                if job.get('state')=='cancelled' or (_run_status(self.runs,job) or {}).get('state') in ('published','manual_completed'):
                    continue
                previous = self.runs.get(job['id'])
                if previous and not (removal_can_be_checked(previous) or copy_can_be_checked(previous)):
                    raise RunNeedsReview('A previous run needs an outcome check. Its original will not be processed again.')
                saved=latest_collection(self.drafts,job['id'])
                if not saved:
                    raise CopyStopped('Collect every listing first.')
                self.runs.assert_source_available(job['id'], saved['capture']['rxcu'], saved['capture']['listing_id'])
                reviews=self.drafts.list_addresses(saved['capture']['source_capture_id'])
                if not reviews:
                    raise CopyStopped('Save every address review first.')
                build_relist_plan(saved['capture'],reviews[0])
                prepared.append(job)
            return prepared
        def confirm(prepared):
            if not prepared:
                return
            from .brochure_review import source_brochure, latest_review, save_review
            for job in prepared:
                collection = latest_collection(self.drafts, job['id'])['capture']
                original = source_brochure(collection)
                if len(original) <= 250 or latest_review(self.drafts, job['id'], collection):
                    continue
                dialog = QDialog(self)
                dialog.setWindowTitle('Review brochure summary · ' + job['rxcu'])
                dialog.resize(640, 370)
                layout = QVBoxLayout(dialog)
                layout.addWidget(_label('Hub allows 250 characters in the brochure summary. Shorten this text while preserving its facts. The full listing description stays unchanged.', 'noticeBody', True))
                edit = QPlainTextEdit(original)
                edit.setAccessibleName('Reviewed brochure summary')
                layout.addWidget(edit)
                count = QLabel()
                layout.addWidget(count)
                buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
                buttons.button(QDialogButtonBox.StandardButton.Save).setText('Save summary review')
                layout.addWidget(buttons)
                def changed():
                    value = edit.toPlainText()
                    count.setText(str(len(value)) + ' / 250 characters')
                    buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(value.strip()) and len(value) <= 250)
                edit.textChanged.connect(changed)
                changed()
                buttons.accepted.connect(dialog.accept)
                buttons.rejected.connect(dialog.reject)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    dialog.deleteLater()
                    return
                save_review(self.drafts, job['id'], collection, edit.toPlainText())
                dialog.deleteLater()
            box=QMessageBox(self)
            box.setWindowTitle('Relist the reviewed batch')
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText('\n'.join(j['rxcu']+' · '+_address_summary(_collected_job_view(self.drafts,j)['address']) for j in prepared))
            from .relist_dates import relist_today, six_months_after
            day = relist_today()
            box.setInformativeText('For each listing: remove the original from its portals, archive sales as cancelled / mandate expired and rentals as withdrawn, create the replacement at the reviewed address, check its details and photos, then publish it to the same portals.\n\n'
                'New runs use the relist day for Date listed and On market since, with expiry six calendar months later. '
                'Today: '+day.strftime('%d %b %Y')+' → '+six_months_after(day).strftime('%d %b %Y')+'. '
                'Sale mandate dates follow those dates. Rental occupation dates stay unchanged. '
                'An interrupted run keeps its recorded dates and completed steps are checked and skipped.')
            box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
            box.button(QMessageBox.StandardButton.Yes).setText('Relist this batch')
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if box.exec()!=QMessageBox.StandardButton.Yes:
                return
            stop=threading.Event()
            provider=HubRelistUI(self.hub,self.media_root,clipboard=self._description_clipboard,
                cancelled=stop.is_set,progress=self.collection_progress.emit)
            runner=RelistRunner(self.drafts,self.runs,provider,cancelled=stop.is_set,progress=self.collection_progress.emit)
            def run():
                try:
                    return runner.batch(prepared)
                except (CopyStopped,RunNeedsReview):
                    raise
                except Exception:
                    raise RuntimeError('Relisting stopped. Check the activity record and the existing Hub listing before continuing. No uncertain action was retried.') from None
            self._run_operation('Relisting one listing at a time…',run,'Batch processing finished. Check each listing’s recorded result.',cancel=stop.set)
        self._run_operation('Checking the reviewed batch…',preflight,'The reviewed batch is ready.',confirm)

    def _confirm_local_observation(self, purpose: str) -> bool:
        if not self.selected_job:
            return False
        confirmation = QMessageBox(self)
        confirmation.setIcon(QMessageBox.Icon.Question)
        confirmation.setTextFormat(Qt.TextFormat.PlainText)
        confirmation.setWindowTitle("Save incomplete private data locally?")
        confirmation.setText(f"{purpose} under {self.selected_job.get('rxcu', 'this local job')}?")
        confirmation.setInformativeText("Check that you selected the intended local job. The editor or ZIP is not automatically matched to this RXCU or account. Private property and contact details may be saved on this computer without encryption. The first page capture selects the sole open Hub editor; later captures stay pinned to that editor and listing.\n\nNothing will be approved, uploaded or changed in Hub. A complete source backup and original-media coverage remain unverified.")
        confirmation.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        confirmation.button(QMessageBox.StandardButton.Yes).setText("Save incomplete data locally")
        confirmation.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return confirmation.exec() == QMessageBox.StandardButton.Yes

    @Slot()
    def _collect_batch(self):
        if self._is_busy or not self.hub or not self.drafts or not self.media_root:
            return
        if not any(job.get('state') == 'queued' for job in self.jobs):
            return
        # Developer launches can refresh read-only collectors between tests
        # without discarding the operator's manual sign-in. Packaged builds do
        # not set this flag and always use the installed, tested modules.
        import os
        if os.environ.get('QUICK_RELIST_DEVELOPMENT') == '1':
            import importlib
            for name in ('business_fields', 'date_capture', 'capture', 'general_features', 'room_capture', 'source_fields', 'form_copy', 'portal_ui', 'media_captions', 'original_media', 'matched_capture', 'collection'):
                importlib.reload(importlib.import_module('quick_relist.' + name))
        from .collection import collect_batch, CollectionStopped
        from .form_copy import CopyStopped
        from .capture import CaptureError
        from .original_media import OriginalMediaError
        from .downloads import DownloadReviewError
        from .hub_login import LoginStopped, wait_for_hub_login
        stop = threading.Event()
        jobs = deepcopy(self.jobs)
        def collect():
            if stop.is_set():
                raise LoginStopped('Collection stopped. Your saved batch is ready to resume.')
            if self.hub.driver is None:
                self.collection_progress.emit('Opening the RE/MAX Chrome window…')
                self._safe_readonly_call(self.hub.open, opening=True)
            try:
                wait_for_hub_login(self.hub.driver, cancelled=stop.is_set,
                                   progress=self.collection_progress.emit)
                return collect_batch(self.hub, self.drafts, jobs, self.media_root,
                                     cancelled=stop.is_set, progress=self.collection_progress.emit)
            except (CollectionStopped, CopyStopped, CaptureError, OriginalMediaError, DownloadReviewError, LoginStopped):
                raise
            except Exception:
                raise RuntimeError('Collection stopped. Saved information and photos have been kept. Check Hub and the collection tools before continuing; no listing was changed.') from None
        self._run_operation('Opening Hub and waiting for your sign-in…', collect,
                            'Collection finished. Select a listing and verify its address.',
                            lambda _: self._verify_captured_address(), cancel=stop.set)

    @Slot()
    def _verify_captured_address(self):
        if self._is_busy or not self.selected_job or not self.drafts:
            return
        if self.runs is not None and self.runs.get(self.selected_job['id']):
            return  # Resume collection must respect the same lock as the button.
        from .collection import latest_collection
        from .address_review_ui import VerifyAddressDialog
        collection = latest_collection(self.drafts, self.selected_job['id'])
        if not collection:
            return
        capture_id = collection['capture']['source_capture_id']
        source = self.drafts.get_capture(capture_id)['capture']
        revisions = self.drafts.list_addresses(capture_id)
        dialog = VerifyAddressDialog(self.selected_job['rxcu'], source, revisions[0] if revisions else None, self)
        dialog.setStyleSheet(STYLE)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.drafts.save_address(capture_id, dialog.original, dialog.edited_address())
            self.refresh()
            self.statusBar().showMessage('Address review saved. Hub has not been changed.')

    @Slot()
    def _capture_all_pages(self) -> None:
        if (not self.selected_job or self.hub is None or self.drafts is None
                or self._is_busy or not self.capture_all_pages_button.isEnabled()):
            return
        job_id, reference = self.selected_job["id"], str(self.selected_job["rxcu"])
        confirmation = QMessageBox(self)
        confirmation.setTextFormat(Qt.TextFormat.PlainText)
        confirmation.setWindowTitle("Capture matched editor tabs locally?")
        confirmation.setText(f"Read the observed editor tabs for {reference} and save the visible source details on this computer?")
        confirmation.setInformativeText("The exact RXCU headings and browser tab will be checked before and after accepted pages. Existing bedroom, bathroom, kitchen, parking and lounge options with supported positive counts will be opened and closed with Escape, without changing their controls. Other nested records remain unverified. The browser stays on its last inspected tab. Finish or discard any unsaved edits yourself first, and do not use the browser during capture.\n\nPrivate property and contact details are stored locally without encryption. Account ownership, originals and complete field coverage remain unverified. No Hub fields will be changed or saved. You may stop after the current browser read.")
        confirmation.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        confirmation.button(QMessageBox.StandardButton.Yes).setText("Capture locally")
        confirmation.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirmation.exec() != QMessageBox.StandardButton.Yes:
            return
        cancel_event = threading.Event()

        def capture_and_save():
            from .matched_capture import capture_matched_listing
            try:
                capture = capture_matched_listing(self.hub, reference, cancelled=cancel_event.is_set, include_room_options=True)
                record_id = self.drafts.save_capture(job_id, capture)
                return {"record_id": record_id, "page_count": len(capture.get("pages", [])),
                        "status": capture.get("status"), "stop_reason": capture.get("stop_reason")}
            except Exception:
                raise RuntimeError("The matched source capture could not be recorded. No listing was changed. Check the matched editor and private local storage before starting again.") from None

        def show_result(result):
            message = f"Saved {result['page_count']} guarded editor page(s) locally. Source backup remains incomplete."
            if result["status"] in ("stopped", "cancelled"):
                message += " " + str(result.get("stop_reason") or "Capture stopped before all pages were read.")
            self.statusBar().showMessage(message)
            self._show_saved_captures()

        self._run_operation("Reading matched editor tabs without changing Hub…", capture_and_save,
                            "Matched source inspection finished.", show_result, cancel=cancel_event.set)

    @Slot()
    def _capture_current_page(self) -> None:
        if not self.selected_job or self.hub is None or self.drafts is None or self._is_busy or not self.capture_page_button.isEnabled():
            return
        if not self._confirm_local_observation("Save a partial page observation from the Hub editor"):
            return
        job_id = self.selected_job["id"]
        reference = str(self.selected_job["rxcu"])

        def capture_and_save():
            try:
                from .capture import PartialHubCapture
                session = self._capture_sessions.get(job_id)
                if session is None or session[0] is not self.hub.driver:
                    # The operator explicitly approved selecting the sole editor.
                    # Never switch tabs after the capture collector has been pinned.
                    self.hub.select_editor_tab()
                    collector = PartialHubCapture(self.hub.driver)
                else:
                    collector = session[1]
                binding = getattr(self.hub, "editor_bindings", {}).get(reference)
                if binding:
                    if self.hub.driver.current_window_handle != binding["handle"]:
                        raise ValueError("The verified editor tab changed")
                    self.hub.verify_editor_reference(reference, binding["listing_id"])
                capture = collector.capture_current_page(binding["listing_id"]) if binding else collector.capture_current_page()
                if binding:
                    self.hub.verify_editor_reference(reference, binding["listing_id"])
                self._capture_sessions[job_id] = (self.hub.driver, collector)
                return self.drafts.save_capture(job_id, capture)
            except Exception:
                # Selenium/storage messages may contain values or private URLs.
                raise RuntimeError("The current page could not be saved as a partial observation. Check that exactly one Hub editor is open and local storage is available. Existing collections stay pinned to their original tab and listing; reopen the app to start a new collection without removing saved records. No listing was changed or approved.") from None

        self._run_operation("Reading the current page without changing Hub…", capture_and_save, "Incomplete page observation saved locally. Listing association and source completeness remain unverified.", lambda _result: self._show_saved_captures())

    @Slot()
    def _show_saved_captures(self) -> None:
        if not self.selected_job or self.drafts is None or self._is_busy:
            return
        try:
            records = self.drafts.list_captures(self.selected_job["id"])
            if not records:
                return
            dialog = CapturedObservationsDialog(self.selected_job, records, self.drafts, self, store=self.store)
        except Exception:
            self._show_error("Local observations unavailable", "The saved observations could not be read. No address approval or Hub action is available from these records.")
            return
        dialog.exec()
        dialog.source_text.clear()
        dialog.deleteLater()
        self.refresh()

    @Slot()
    def _download_media(self) -> None:
        if not self.selected_job or self.hub is None or self.drafts is None or self.media_root is None or self._is_busy or not self.download_media_button.isEnabled():
            return
        reference = str(self.selected_job["rxcu"])
        job_id = self.selected_job["id"]
        binding = getattr(self.hub, "editor_bindings", {}).get(reference)
        if not binding:
            return
        confirmation = QMessageBox(self)
        confirmation.setTextFormat(Qt.TextFormat.PlainText)
        confirmation.setIcon(QMessageBox.Icon.Question)
        confirmation.setWindowTitle("Download photo pack?")
        confirmation.setText(f"Download the photo pack for {reference} to this computer?")
        confirmation.setInformativeText("The editor headings will be checked again. If browser events match a completed ZIP to this editor frame and RXCU filename, its bytes will be imported into this local job. Otherwise the file remains unassigned for manual review. Do not use the browser or start other downloads during this request. Further requests pause until you review the attempt. Files are not encrypted by this app. No Hub listing will be changed; media completeness, original resolution and account ownership remain unverified.")
        confirmation.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        confirmation.button(QMessageBox.StandardButton.Yes).setText("Download locally")
        confirmation.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirmation.exec() != QMessageBox.StandardButton.Yes:
            return

        def request_download():
            from .hub_readonly import DownloadOutcomeUnknown, PreviousDownloadExists
            try:
                result = self.hub.download_photo_pack(reference, binding["listing_id"], self.media_root.parent / "downloads")
                evidence = result.get("download_evidence", {})
                matched = (result.get("attribution_verified") is True and isinstance(evidence, dict)
                           and evidence.get("method") == "browser_frame_guid_and_reference"
                           and evidence.get("rxcu") == reference and evidence.get("listing_id") == binding["listing_id"]
                           and evidence.get("completion_observed") is True)
                if matched:
                    from .media import import_media_zip
                    imported = import_media_zip(result["candidate_path"], self.media_root)
                    if (imported["archive"]["sha256"] != evidence.get("archive_sha256")
                            or imported["archive"]["size"] != evidence.get("archive_size")):
                        raise ValueError("The imported archive differs from the browser-matched bytes")
                    imported["download_evidence"] = deepcopy(evidence)
                    result["capture_id"] = self.drafts.save_capture(job_id, imported)
                    result["media_count"] = imported["media_count"]
                return result
            except PreviousDownloadExists as exc:
                raise RuntimeError(f"A previous download needs review before another attempt. Check this local folder: {exc.directory}") from None
            except DownloadOutcomeUnknown as exc:
                raise RuntimeError(f"The download outcome is uncertain. Check this local folder before retrying: {exc.directory}") from None
            except Exception:
                raise RuntimeError("The photo-pack download could not be confirmed. Check the editor and browser before retrying. No listing was changed.") from None

        def show_candidate(result):
            if result.get("capture_id"):
                self.statusBar().showMessage(f"Stored {result['media_count']} image file(s) from the browser-matched ZIP. Complete source, original resolution, classification and order remain unverified.")
                self._show_saved_captures()
                return
            box = QMessageBox(self)
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setWindowTitle("ZIP requires manual review")
            box.setText("A ZIP appeared, but its listing association is not verified.")
            box.setInformativeText(f"Inspect the file before using Import local media ZIP. Use Review previous download after checking the files and browser to reset the pause.\n\nLocal folder: {result['directory']}")
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.exec()

        self._run_operation("Requesting the photo pack without changing Hub…", request_download,
                            "Photo-pack request finished; review the recorded evidence and remaining media checks.", show_candidate)

    @Slot()
    def _review_download(self) -> None:
        if self.media_root is None or self._is_busy:
            return
        root = self.media_root.parent / "downloads"
        marker = root / "download-review-required.json"
        if not (marker.exists() or marker.is_symlink()):
            self.refresh()
            return
        if self.hub is not None and getattr(self.hub, "driver", None) is not None:
            confirmation = QMessageBox(self)
            confirmation.setTextFormat(Qt.TextFormat.PlainText)
            confirmation.setWindowTitle("Close the Hub browser for review?")
            confirmation.setText("First check the browser's download history and finish or cancel any pending download.")
            confirmation.setInformativeText("Continue closes this app's dedicated Hub browser and ends its sign-in session. Unsaved browser edits may be lost. All downloaded files stay on this computer. The download pause stays until you finish the next review.")
            confirmation.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
            confirmation.button(QMessageBox.StandardButton.Yes).setText("Close browser and review")
            confirmation.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if confirmation.exec() != QMessageBox.StandardButton.Yes:
                return

        def close_and_inspect():
            from .downloads import inspect_download
            if self.hub is not None and getattr(self.hub, "driver", None) is not None:
                try:
                    self.hub.close()
                except Exception:
                    raise RuntimeError("The Hub browser could not be confirmed closed. The download pause was kept. Close every Quick Relist browser manually, restart Quick Relist, then review the previous download.") from None
            return inspect_download(root)

        self._run_operation("Reviewing the previous local download…", close_and_inspect,
                            "Previous download files inspected. No listing was changed.", self._show_download_review)

    def _show_download_review(self, review):
        dialog = DownloadReviewDialog(review, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        confirmed = dialog.closed_check.isChecked() and dialog.reviewed_check.isChecked()
        dialog.deleteLater()
        if not accepted or not confirmed:
            self.statusBar().showMessage("Download review cancelled. The pause and all files are preserved.")
            return
        root = self.media_root.parent / "downloads"

        def finish():
            from .downloads import finish_download_review
            if self.hub is not None and getattr(self.hub, "driver", None) is not None:
                raise RuntimeError("A Hub browser is still connected. Close it before resetting the download pause.")
            return finish_download_review(root, review["review_token"], browsers_closed=True, files_reviewed=True)

        self._run_operation("Recording the download review…", finish,
                            "Download pause reset; all files were kept. No download was retried. Reopen Hub and select the listing again for a new request, or import a reviewed ZIP manually.")

    @Slot()
    def _import_media(self) -> None:
        if not self.selected_job or self.drafts is None or self.media_root is None or self._is_busy or not self.import_media_button.isEnabled():
            return
        path, _selected_filter = QFileDialog.getOpenFileName(self, "Choose a downloaded original-media ZIP", "", "ZIP archives (*.zip);;All files (*)")
        if not path or not self._confirm_local_observation("Preserve the selected media ZIP and image files"):
            return
        job_id = self.selected_job["id"]

        def import_and_save():
            try:
                from .media import import_media_zip
                result = import_media_zip(path, self.media_root)
                return self.drafts.save_capture(job_id, result)
            except Exception:
                raise RuntimeError("The media import could not be recorded. No listing was changed or approved. If files were imported before recording stopped, they remain in the local media folder; check that folder before retrying.") from None

        self._run_operation("Checking and preserving local media bytes…", import_and_save, "Local media import recorded. Image bytes are preserved; listing association, classification, order, captions and completeness remain unverified.", lambda _result: self._show_saved_captures())

    @Slot()
    def _capture(self) -> None:
        if not self.selected_job or not self.engine or not self.live_ready or self._is_busy:
            return
        job_id = self.selected_job["id"]
        self._run_operation("Capturing the original listing…", lambda: self.engine.snapshot(job_id), "Source capture completed. Review the recorded address before proceeding.")

    @Slot()
    def _review_address(self) -> None:
        if not self.selected_job or not self.engine or self._is_busy or not self.review_button.isEnabled():
            return
        try:
            dialog = AddressReviewDialog(self.selected_job, self)
        except ValueError as exc:
            self._show_error("Source capture required", str(exc))
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        job_id = self.selected_job["id"]
        address = dialog.corrected_address()
        reason = str(dialog.reason.currentData())
        self._run_operation("Saving your address review…", lambda: self.engine.approve_address(job_id, address, reason), "Address review saved locally. No Hub listing was changed.")

    @Slot()
    def _advance(self) -> None:
        if not self.selected_job or not self.engine or not self.live_ready or self._is_busy or not self.advance_button.isEnabled():
            return
        job = self.selected_job
        descriptions = {
            "ready": "Remove the original listing from its publishing portals. This affects the listing's public visibility.",
            "portals_removed": "Set the original to Listing has been cancelled with the Mandate expired reason, then archive it.",
            "archived": "Create a new Hub listing from the captured source with the reviewed address.",
            "created": "Verify that the new listing preserves the captured source content and media.",
            "fidelity_verified": "Publish the verified new listing to the configured portals.",
            "publication_pending": "Check whether Hub confirms publication. Do not create another listing.",
        }
        description = descriptions.get(str(job.get("state")), "Perform the next verified workflow operation.")
        reason = str(job.get("archive_reason") or "Not recorded").replace("_", " ")
        confirmation = QMessageBox(self)
        confirmation.setIcon(QMessageBox.Icon.Warning)
        confirmation.setWindowTitle("Confirm the next Hub step")
        confirmation.setText(f"{job.get('rxcu', '')}: {description}")
        confirmation.setInformativeText(f"Address: {_address_summary(job.get('corrected_address'))}\nArchive reason: {reason}\n\nOnly this next step will run. The result must be verified before continuing.")
        confirmation.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        confirmation.button(QMessageBox.StandardButton.Yes).setText("Confirm this step")
        confirmation.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirmation.exec() != QMessageBox.StandardButton.Yes:
            return
        job_id = job["id"]
        self._run_operation("Running one verified Hub step…", lambda: self.engine.advance(job_id), "Step finished. Check the recorded state before continuing.")

    @Slot()
    def _reconcile(self) -> None:
        if not self.selected_job or not self.engine or not self.live_ready or self._is_busy or not self.reconcile_button.isEnabled():
            return
        job_id = self.selected_job["id"]
        self._run_operation("Checking the previous outcome…", lambda: self.engine.reconcile(job_id), "Outcome check finished. Review the recorded state before any further action.")

    @Slot()
    def _cancel_job(self) -> None:
        if not self.selected_job or not self.engine or self._is_busy or not self.cancel_job_button.isEnabled():
            return
        job_id = self.selected_job["id"]
        reference = str(self.selected_job.get("rxcu", "this listing"))
        confirmation = QMessageBox(self)
        confirmation.setIcon(QMessageBox.Icon.Question)
        confirmation.setWindowTitle("Cancel this local job?")
        confirmation.setTextFormat(Qt.TextFormat.PlainText)
        confirmation.setText(f"Cancel the unstarted local job for {reference}?")
        confirmation.setInformativeText("The saved reference and activity history will remain. No Hub listing will be cancelled, archived, removed, or changed.\n\nCancellation is allowed only before any destructive Hub operation has started.")
        confirmation.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        confirmation.button(QMessageBox.StandardButton.Cancel).setText("Keep job")
        confirmation.button(QMessageBox.StandardButton.Yes).setText("Cancel local job")
        confirmation.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirmation.exec() != QMessageBox.StandardButton.Yes:
            return
        self._run_operation("Cancelling the unstarted local job…", lambda: self.engine.cancel_job(job_id), "Local job cancelled. Its history is preserved; no Hub listing was changed.")

    def _run_operation(self, title: str, operation: Callable[[], Any], success: str, callback: Callable[[Any], None] | None = None, *, cancel: Callable[[], None] | None = None) -> None:
        if self._is_busy:
            return
        self._is_busy = True
        self._operation_error = None
        self._operation_success = success
        self._operation_result = None
        self._operation_callback = callback
        self._cancel_operation_callback = cancel
        self.stop_operation_button.setVisible(cancel is not None)
        self.stop_operation_button.setEnabled(cancel is not None)
        self.stack.setEnabled(False)
        self.progress.show()
        self.statusBar().showMessage(title)
        self.operation_message.setText(title)
        self._thread = QThread(self)
        self._worker = _Worker(operation)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._operation_succeeded)
        self._worker.failed.connect(self._operation_failed)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._operation_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    @Slot()
    def _stop_operation(self):
        if self._is_busy and self._cancel_operation_callback is not None:
            self._cancel_operation_callback()
            self.stop_operation_button.setEnabled(False)
            self.statusBar().showMessage("Stopping between browser actions. Saved information and completed steps will be kept.")

    @Slot(object)
    def _operation_succeeded(self, result: Any) -> None:
        self._operation_result = result

    @Slot(str)
    def _operation_failed(self, error: str) -> None:
        self._operation_error = error

    @Slot()
    def _operation_finished(self) -> None:
        # QThread.finished can reach the GUI while deferred QObject deletion
        # and thread-local cleanup are still running. Keep both Python
        # wrappers alive until that cleanup is complete; otherwise releasing
        # the worker here can race its C++ destructor on the worker thread.
        if self._thread is not None:
            self._thread.wait()
        self._thread = None
        self._worker = None
        self._is_busy = False
        self.progress.hide()
        self.stop_operation_button.hide()
        self._cancel_operation_callback = None
        self.stack.setEnabled(True)
        self.refresh()
        callback, result = self._operation_callback, self._operation_result
        self._operation_result = None
        self._operation_callback = None
        if self._operation_error:
            self.statusBar().showMessage("Operation stopped. Review the recorded outcome before trying again.")
            self.operation_message.setText(self._operation_error)
            self._show_error("Operation did not complete", self._operation_error)
        else:
            self.statusBar().showMessage(self._operation_success)
            self.operation_message.setText(self._operation_success)
            if callback:
                callback(result)

    def _safe_readonly_call(self, operation: Callable[[], Any], *, opening=False) -> Any:
        try:
            return operation()
        except Exception as exc:
            from .diagnostics import record_error
            reference = None
            if self.media_root is not None:
                reference = record_error(self.media_root.parent / 'diagnostics',
                                         'open_browser' if opening else 'readonly_hub', exc)
            # Browser exceptions can contain private URLs or page contents.
            message = ("The dedicated Chrome window could not be opened. Close and reopen Quick Relist before trying Open Hub again. No listing was changed."
                       if opening else "The read-only Hub step could not be verified. Check the browser, filters and selected listing before continuing. No listing was changed.")
            if reference:
                message += " Private diagnostic: " + reference[:8] + "."
            raise RuntimeError(message) from None

    @Slot()
    def _open_readonly_hub(self) -> None:
        if self.hub is None or self._is_busy:
            return
        self._run_operation("Opening the Hub browser…", lambda: self._safe_readonly_call(self.hub.open, opening=True), "Hub browser opened for inspection. For a saved batch, choose Resume collection; it waits for sign-in automatically.")

    @Slot()
    def _inspect_readonly_hub(self) -> None:
        if self.hub is None or self._is_busy:
            return
        self._run_operation("Reading the current Hub editor…", lambda: self._safe_readonly_call(self.hub.inspect_current_editor_page), "Read-only inspection finished. The result is partial and unverified; no listing was changed.", self._show_partial_inspection)

    @Slot()
    def _open_selected_editor(self) -> None:
        if self.hub is None or not self.selected_job or self._is_busy or not self.open_editor_button.isEnabled():
            return
        reference = str(self.selected_job["rxcu"])
        self._editor_open_job_id = str(self.selected_job["id"])
        self._run_operation("Opening the exact matching editor…", lambda: self._safe_readonly_call(lambda: self.hub.open_listing_editor(reference)),
                            "Matching row, editor link and headings verified. Account and complete source remain unverified.")

    def _editor_open_state(self, reference: str) -> str:
        status = getattr(self.hub, "editor_open_state", None)
        if status is None:
            return "none"
        try:
            result = status(reference)  # In-memory only; never read a browser on the UI thread.
            if result == "pending" and self._editor_open_job_id != str((self.selected_job or {}).get("id")):
                return "other"
            return result if result in ("none", "pending", "other", "blocked") else "blocked"
        except Exception:
            return "blocked"

    @Slot()
    def _recheck_selected_editor(self) -> None:
        if (self.hub is None or not self.selected_job or self._is_busy
                or not self.recheck_editor_button.isEnabled()):
            return
        reference = str(self.selected_job["rxcu"])
        if self._editor_open_state(reference) != "pending":
            self._select_job()
            return

        def finished(result):
            if isinstance(result, dict) and result.get("editor_pending") is True:
                self.statusBar().showMessage("The existing editor is still unverified. Leave its tabs and results unchanged, then recheck when it has loaded. No link was clicked again.")

        self._run_operation("Rechecking the existing editor without navigating…",
                            lambda: self._safe_readonly_call(lambda: self.hub.recheck_opened_editor(reference)),
                            "Existing editor verified against the original row and link. Account and complete source remain unverified.", finished)

    @Slot()
    def _search_selected_reference(self) -> None:
        if self.hub is None or not self.selected_job or self._is_busy or not self.search_hub_button.isEnabled():
            return
        reference = str(self.selected_job["rxcu"])
        self._run_operation("Searching Manage Listings in the Hub browser…", lambda: self._safe_readonly_call(lambda: self.hub.search_reference(reference)), "Hub search submitted. Review matches and active filters in the browser; a matching listing has not been verified.")

    def _show_partial_inspection(self, result: Any) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Partial Hub inspection · read only")
        dialog.resize(800, 620)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(23, 22, 23, 20)
        layout.addWidget(_label("Partial, unverified inspection", "pageTitle"))
        layout.addWidget(_label("These are the visible fields from the current editor, not a complete source snapshot. They cannot be used to approve or run a relist. No data is saved by this view.", "noticeBody", True))
        text = QPlainTextEdit(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        text.setReadOnly(True)
        text.setAccessibleName("Partial inspection result, plain text only")
        layout.addWidget(text, 1)
        layout.addWidget(_label("The result may contain private property or contact information. Close this window when you are finished reviewing it.", "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        text.clear()
        dialog.deleteLater()

    @Slot()
    def _show_journal(self) -> None:
        if not self.selected_job or not self.store or self._is_busy:
            return
        try:
            entries = {'preparation':self.store.journal(self.selected_job["id"]),
                       'relist':self.runs.get(self.selected_job['id']) if self.runs is not None else None}
        except Exception as exc:
            self._show_error("Could not read the activity record", str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Activity record · {self.selected_job.get('rxcu', '')}")
        dialog.resize(770, 580)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(23, 23, 23, 20)
        layout.addWidget(_label("Recorded activity", "pageTitle"))
        layout.addWidget(_label("Local evidence of attempted operations and their reported outcomes. A queued or attempted operation is not a success.", "muted", True))
        text = QPlainTextEdit(json.dumps(entries, indent=2, ensure_ascii=False, default=str) if entries else "No operations have been recorded for this reference yet.")
        text.setReadOnly(True)
        text.setAccessibleName("Read-only listing activity record")
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _validated_hub_url(self) -> QUrl | None:
        text = self.hub_url.text().strip()
        url = QUrl(text)
        if not url.isValid() or url.scheme().lower() != "https" or not url.host() or url.userName() or url.password():
            self._show_error("Use your official HTTPS address", "Enter the official Hub sign-in URL beginning with https://. Do not include a username or password in the address.")
            return None
        return url

    @Slot()
    def _save_url(self) -> None:
        url = self._validated_hub_url()
        if url is None:
            return
        # Persist a public landing URL only, never a login token or query string.
        if url.hasQuery() or url.hasFragment():
            self._show_error("Use a clean sign-in URL", "Remove any query string or fragment before saving. Those parts can contain private sign-in tokens.")
            return
        self._settings.setValue("hub_url", url.toString())
        self.statusBar().showMessage("Hub website address saved locally. The app is still not connected to your browser session.")

    @Slot()
    def _open_hub(self) -> None:
        url = self._validated_hub_url()
        if url is None:
            return
        if not QDesktopServices.openUrl(url):
            self._show_error("Could not open your browser", "Open your official Hub sign-in page in your usual browser. This app has not connected to that session.")
            return
        self.statusBar().showMessage("Sign-in page opened in your browser. Browser sign-in does not enable the missing Hub integration.")

    def _show_error(self, title: str, message: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(message or "The operation could not be completed.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def closeEvent(self, event: Any) -> None:
        if self._is_busy:
            QMessageBox.information(self, "Operation in progress", "Wait for the current operation to finish before closing Quick Relist. Interrupting a live step could leave its outcome uncertain.")
            event.ignore()
            return
        if self.rxcu_input.toPlainText().strip():
            result = QMessageBox.question(self, "Unsaved references", "The references in the new-batch box have not been saved. Close without saving them?", QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if result != QMessageBox.StandardButton.Discard:
                event.ignore()
                return
        event.accept()


def launch(engine: Any = None, store: Any = None, hub: Any = None, drafts: Any = None, media_root: Any = None, runs: Any = None) -> int:
    """Run the desktop app. The caller owns the injected Engine and Store."""
    application = QApplication.instance()
    owns_application = application is None
    if application is None:
        application = QApplication(sys.argv)
    application.setApplicationName("Quick Relist")
    application.setOrganizationName("QuickRelist")
    application.setFont(QFont("Segoe UI", 10))
    window = MainWindow(engine=engine, store=store, hub=hub, drafts=drafts, media_root=media_root, runs=runs)
    window.show()
    # Keep the window alive when embedded in an existing QApplication.
    application._quick_relist_window = window  # type: ignore[attr-defined]
    return application.exec() if owns_application else 0


__all__ = ["AddressReviewDialog", "TentativeAddressDialog", "CapturedObservationsDialog", "DownloadReviewDialog", "MainWindow", "launch"]
