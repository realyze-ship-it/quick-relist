"""Local-only comparison of explicitly selected saved observations.

This dialog accepts no Engine, Hub session, browser or write callback. Its only
storage calls are list_jobs/get_job and list_captures/get_capture. A comparison
is transient and can never approve a difference or advance a relisting job.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from html import escape
import json
import re
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QHeaderView, QLineEdit, QPlainTextEdit, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .replacement_review import WARNING, compare_replacement_fields
from .source_fields import extract_source_fields
from .ui import STYLE, _button, _label


def _reference(value):
    if not isinstance(value, str) or not re.fullmatch(r"RXCU-?[0-9]{3,20}", value):
        raise ValueError("Invalid local reference")
    return value.replace("-", "")


def _job_identity(job):
    if not isinstance(job, dict) or not isinstance(job.get("id"), str) or not job["id"]:
        raise ValueError("Invalid local job")
    return job["id"], _reference(job.get("rxcu")), job.get("source_id")


def _check_record(record, job_id):
    if (not isinstance(record, dict) or record.get("job_id") != job_id
            or any(not isinstance(record.get(key), str) or not record[key]
                   for key in ("id", "captured_at", "sha256"))
            or not isinstance(record.get("capture"), dict)):
        raise ValueError("Invalid local observation")
    saved = datetime.fromisoformat(record["captured_at"])
    if saved.tzinfo is None or saved.utcoffset() is None:
        raise ValueError("Invalid local observation timestamp")


def _same_record(first, second):
    # JSON equality, unlike Python dict equality, distinguishes False and 0.
    options = {"sort_keys": True, "ensure_ascii": False, "allow_nan": False}
    return json.dumps(first, **options) == json.dumps(second, **options)


def _identity_text(role, job, record, listing_id=None):
    lines = [f"{role}: {job['rxcu']} · job {job['id']}",
             f"Saved locally: {record['captured_at']} · capture {record['id']}"]
    if listing_id is not None:
        lines.append(f"Captured listing ID: {listing_id}")
    return "\n".join(lines)


def _value_text(entry):
    if "value" not in entry:
        return {"missing": "Not observed", "ambiguous": "Ambiguous; see details"}.get(
            entry.get("status"), "Unresolved; see details")
    value = entry.get("display_value", entry["value"])
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if text == "":
        text = '"" (observed empty text)'
    if "selected_code" in entry:
        text += " [code: " + json.dumps(entry["selected_code"], ensure_ascii=False) + "]"
    return text


def _room_scope(scope):
    return f"Room · {scope['category'].capitalize()} {scope['index']} (position)"


class ReplacementComparisonDialog(QDialog):
    """Pin one original record; require an explicit different job and capture."""

    def __init__(self, original_job: dict[str, Any], original_record: dict[str, Any],
                 drafts: Any, store: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.original_job = deepcopy(original_job)
        self.original_record = deepcopy(original_record)
        self.drafts, self.store = drafts, store
        self.jobs = {}
        self.records = {}
        self.original_inventory = None
        self.result = None
        self.rows = []
        self.setWindowTitle("Compare saved replacement · review only")
        self.setStyleSheet(STYLE)
        self.resize(1240, 700)
        self.setMinimumSize(860, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(23, 22, 23, 20)
        root.addWidget(_label("Compare saved replacement", "pageTitle"))
        self.warning = _label(WARNING, "noticeBody", True)
        root.addWidget(self.warning)
        body = QSplitter(Qt.Orientation.Horizontal)
        choices_scroll = QScrollArea()
        choices_scroll.setWidgetResizable(True)
        choices_scroll.setMinimumWidth(260)
        choices_widget = QWidget()
        choices = QVBoxLayout(choices_widget)
        choices.setContentsMargins(0, 0, 12, 0)
        choices_scroll.setWidget(choices_widget)
        review_widget = QWidget()
        review = QVBoxLayout(review_widget)
        review.setContentsMargins(0, 0, 0, 0)
        body.addWidget(choices_scroll)
        body.addWidget(review_widget)
        body.setChildrenCollapsible(False)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([310, 850])
        root.addWidget(body, 1)
        self.original_label = _label("Checking the selected original observation…", "smallTitle", True)
        choices.addWidget(self.original_label)
        self.job_combo = QComboBox()
        self.job_combo.setAccessibleName("Choose a different saved listing job as replacement")
        self.job_combo.addItem("Choose a replacement listing / job…", None)
        self.job_combo.setEnabled(False)
        self.job_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.job_combo.setMinimumContentsLength(15)
        choices.addWidget(self.job_combo)
        self.capture_combo = QComboBox()
        self.capture_combo.setAccessibleName("Choose the exact replacement capture")
        self.capture_combo.addItem("Choose a replacement capture…", None)
        self.capture_combo.setEnabled(False)
        self.capture_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.capture_combo.setMinimumContentsLength(15)
        choices.addWidget(self.capture_combo)
        self.replacement_label = _label("No replacement capture selected.", "muted", True)
        choices.addWidget(self.replacement_label)
        self.compare_button = _button("Compare these saved captures", self._compare)
        self.compare_button.setEnabled(False)
        self.compare_button.setAutoDefault(False)
        choices.addWidget(self.compare_button)
        self.summary = _label("Choose a replacement job and capture explicitly. The original above stays fixed.", "noticeBody", True)
        choices.addWidget(self.summary)
        self.scope_warning = _label("Only local historical observations are compared. Room alignment is positional, not a verified room identity. Saved time is not Hub creation or publication time.", "muted", True)
        choices.addWidget(self.scope_warning)
        choices.addStretch(1)
        filters = QHBoxLayout()
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Find a scope, field, value or status…")
        self.filter_input.setAccessibleName("Filter saved replacement comparison")
        filters.addWidget(self.filter_input, 1)
        self.scope_combo = QComboBox()
        self.scope_combo.setAccessibleName("Filter comparison by listing or individual room")
        self.scope_combo.addItem("All scopes", None)
        filters.addWidget(self.scope_combo)
        self.gaps_only = QCheckBox("Hide matched fields")
        filters.addWidget(self.gaps_only)
        review.addLayout(filters)
        self.coverage_button = _button("Show capture gaps, chronology and limitations", self._show_coverage)
        self.coverage_button.setEnabled(False)
        self.coverage_button.setAutoDefault(False)
        review.addWidget(self.coverage_button)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Scope", "Field", "Original", "Replacement", "Status"])
        self.table.setAccessibleName("Read-only original and replacement comparison")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(False)  # Keep every room's fields together.
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column in (2, 3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 155)
        self.table.setColumnWidth(1, 170)
        self.table.setColumnWidth(4, 105)
        review.addWidget(self.table, 2)
        review.addWidget(_label("Read-only provenance and review details", "smallTitle"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName("Comparison provenance and limitations, plain text only")
        review.addWidget(self.detail, 1)
        root.addWidget(_label("Private local data; not encrypted by this app. Nothing here changes a saved record, approves a difference or enables publication.", "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.job_combo.currentIndexChanged.connect(self._job_changed)
        self.capture_combo.currentIndexChanged.connect(self._capture_changed)
        self.table.itemSelectionChanged.connect(self._show_row)
        self.filter_input.textChanged.connect(self._filter_rows)
        self.scope_combo.currentIndexChanged.connect(self._filter_rows)
        self.gaps_only.toggled.connect(self._filter_rows)
        self._load_original_and_jobs()

    def _read_unchanged_record(self, job, selected):
        if _job_identity(self.store.get_job(job["id"])) != _job_identity(job):
            raise ValueError("Local job identity changed")
        record = self.drafts.get_capture(selected["id"])
        _check_record(record, job["id"])
        if not _same_record(record, selected):
            raise ValueError("Local observation changed")
        return record

    def _load_original_and_jobs(self):
        try:
            _job_identity(self.original_job)
            _check_record(self.original_record, self.original_job["id"])
            record = self._read_unchanged_record(self.original_job, self.original_record)
            inventory = extract_source_fields(record["capture"], expected_rxcu=self.original_job["rxcu"],
                                              expected_listing_id=self.original_job.get("source_id"))
            if inventory["status"] != "partial":
                raise ValueError("Original observation rejected")
            self.original_inventory = inventory
            self.original_label.setText(_identity_text("Fixed original", self.original_job, record,
                                                       inventory["source"]["listing_id"]))
        except Exception:
            self.original_label.setText("The selected original observation could not be checked.")
            self.summary.setText("Comparison is blocked. Close this review and reopen the exact saved matched observation. Saved records are unchanged; no Hub action was taken.")
            return
        try:
            jobs = self.store.list_jobs()
            if not isinstance(jobs, list):
                raise ValueError("Invalid job list")
            choices = {}
            for job in jobs:
                ident, reference, _ = _job_identity(job)
                if ident == self.original_job["id"] or reference == _reference(self.original_job["rxcu"]):
                    continue
                if ident in choices:
                    raise ValueError("Ambiguous local job")
                choices[ident] = deepcopy(job)
            self.jobs = choices
            for ident, job in choices.items():
                self.job_combo.addItem(f"{job['rxcu']} · job {ident}", ident)
            self.job_combo.setEnabled(bool(choices))
            if not choices:
                self.summary.setText("No different saved listing job is available. Save a matched observation under another listing job before comparing. No replacement was selected.")
        except Exception:
            self.summary.setText("Saved listing choices could not be read. Close this review and check local storage. No replacement was selected and no Hub action was taken.")

    def _clear_result(self):
        self.result = None
        self.rows = []
        self.table.clearSelection()
        self.table.setRowCount(0)
        self.detail.clear()
        self.coverage_button.setEnabled(False)
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem("All scopes", None)
        self.scope_combo.blockSignals(False)
        self.filter_input.clear()
        self.gaps_only.setChecked(False)
        self.scope_warning.setText("Only local historical observations are compared. Room alignment is positional, not a verified room identity. Saved time is not Hub creation or publication time.")

    def _job_changed(self, *_args):
        self._clear_result()
        self.records = {}
        self.capture_combo.blockSignals(True)
        self.capture_combo.clear()
        self.capture_combo.addItem("Choose a replacement capture…", None)
        self.capture_combo.blockSignals(False)
        self.capture_combo.setEnabled(False)
        self.compare_button.setEnabled(False)
        self.replacement_label.setText("No replacement capture selected.")
        self.summary.setText("Choose a replacement job and capture explicitly. The original above stays fixed.")
        job = self.jobs.get(self.job_combo.currentData())
        if job is None:
            return
        try:
            if _job_identity(self.store.get_job(job["id"])) != _job_identity(job):
                raise ValueError("Local job identity changed")
            records = self.drafts.list_captures(job["id"])
            if not isinstance(records, list):
                raise ValueError("Invalid observation list")
            choices = {}
            for record in records:
                _check_record(record, job["id"])
                if record["id"] in choices:
                    raise ValueError("Ambiguous observation identity")
                choices[record["id"]] = deepcopy(record)
            self.records = choices
            for ident, record in choices.items():
                self.capture_combo.addItem(f"Saved locally {record['captured_at']} · capture {ident}", ident)
            self.capture_combo.setEnabled(bool(choices))
            self.summary.setText("Choose the exact saved replacement capture; none is selected automatically." if choices
                                 else "This job has no saved observations. Choose another job or close the review. No capture was substituted.")
        except Exception:
            self.summary.setText("This job's saved captures could not be read. No comparison was made. Saved records are unchanged; no Hub action was taken.")

    def _capture_changed(self, *_args):
        self._clear_result()
        self.compare_button.setEnabled(False)
        self.replacement_label.setText("No replacement capture selected.")
        self.summary.setText("Choose the exact saved replacement capture; none is selected automatically.")
        job = self.jobs.get(self.job_combo.currentData())
        record = self.records.get(self.capture_combo.currentData())
        if job is None or record is None:
            return
        self.replacement_label.setText(_identity_text("Selected replacement", job, record))
        self.summary.setText("Both saved records are selected. Compare them to check observed values and remaining gaps.")
        self.compare_button.setEnabled(self.original_inventory is not None)

    def _compare(self):
        if not self.compare_button.isEnabled():
            return
        self._clear_result()
        job = self.jobs.get(self.job_combo.currentData())
        selected = self.records.get(self.capture_combo.currentData())
        try:
            if job is None or selected is None:
                raise ValueError("No explicit replacement selected")
            original = self._read_unchanged_record(self.original_job, self.original_record)
            replacement = self._read_unchanged_record(job, selected)
            result = compare_replacement_fields(original["capture"], replacement["capture"],
                expected_original_rxcu=self.original_job["rxcu"], expected_replacement_rxcu=job["rxcu"],
                expected_original_listing_id=self.original_inventory["source"]["listing_id"],
                expected_replacement_listing_id=job.get("source_id"))
            if result["status"] != "partial":
                self.summary.setText("These captures could not be safely compared as two distinct matched listings. Check the exact selected records. No values were compared, no difference was approved and no Hub action was taken.")
                return
            self.result = result
            self.replacement_label.setText(_identity_text("Selected replacement", job, replacement,
                                                          result["replacement"]["source"]["listing_id"]))
            self._render_result()
        except Exception:
            # Never expose exception text, which may contain private values.
            self._clear_result()
            self.compare_button.setEnabled(False)
            self.summary.setText("The selected local records could not be checked. Close this review and reopen the saved observations. No saved record was changed and no Hub action was taken.")

    def _render_result(self):
        result = self.result
        self.rows = []
        for row in result["entries"]:
            self.rows.append({"scope": "Listing · " + row["page"], "group": "Listing fields", "data": row})
        for group in result["room_groups"]:
            name = _room_scope(group["scope"])
            provenance = {key: value for key, value in group.items() if key != "entries"}
            for row in group["entries"]:
                self.rows.append({"scope": name, "group": name, "data": row, "room_group": provenance})
        for coverage in result["unresolved_coverage"]:
            scope = coverage.get("scope") or {}
            name = _room_scope(scope) if scope.get("kind") == "room" and "index" in scope else "Unresolved coverage"
            observation = coverage["observation"]
            field = observation.get("field", {})
            label = field.get("label") or field.get("name") or field.get("id") or coverage["kind"]
            value = "Excluded; see gaps" if coverage["kind"] == "pending_dialogs" else "Unmapped; see details"
            if field:
                raw = field.get("checked") if field.get("type") in ("checkbox", "radio") else field.get("value", field.get("text"))
                value = json.dumps(raw, ensure_ascii=False)
            self.rows.append({"scope": name + " · " + coverage["side"], "group": "Unresolved coverage",
                              "data": coverage, "coverage_label": label, "coverage_value": value})
        groups = list(dict.fromkeys(row["group"] for row in self.rows))
        for group in groups:
            self.scope_combo.addItem(group, group)
        self.table.setRowCount(len(self.rows))
        for index, wrapped in enumerate(self.rows):
            row = wrapped["data"]
            if "coverage_label" in wrapped:
                value = wrapped["coverage_value"]
                original = value if row["side"] == "original" else "Not compared"
                replacement = value if row["side"] == "replacement" else "Not compared"
                label = wrapped["coverage_label"]
            else:
                original, replacement = (_value_text(row[side]) for side in ("original", "replacement"))
                label = row["label"]
            values = (wrapped["scope"], label, original, replacement, row["status"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                # Qt tooltips can interpret rich text. Escape captured values,
                # including markup and image paths, before using a safe wrapper.
                item.setToolTip("<pre>" + escape(str(value)) + "</pre>")
                self.table.setItem(index, column, item)
        summary = result["summary"]
        counts = "; ".join(f"{summary[key]} {key}" for key in ("matched", "changed", "missing", "ambiguous", "unresolved"))
        self.summary.setText(f"Observed fields: {counts}. {summary['unresolved_coverage']} additional unresolved coverage item(s). Matched means equal partial observations only, never ready to publish.")
        a, b = result["original"], result["replacement"]
        pending = a["pending_room_observation_count"] + b["pending_room_observation_count"]
        interrupted = any(side["capture_status"] in ("stopped", "cancelled") for side in (a, b))
        chronology = {"replacement_observed_after_original": "Replacement observations follow the original observation window.",
                      "replacement_observed_before_original": "Replacement observations predate the original observation window.",
                      "observation_windows_overlap": "The two observation windows overlap."}[result["chronology"]["status"]]
        self.scope_warning.setText(f"Original: {len(a['gaps'])} capture gap(s). Replacement: {len(b['gaps'])} capture gap(s). "
            f"{pending} pending room observation(s) excluded. " + ("At least one capture was interrupted. " if interrupted else "")
            + chronology + " Capture times do not verify Hub creation/save order. Room groups align by category and position only. Open gaps for missing pages, superseded observations and all coverage limitations.")
        self.coverage_button.setEnabled(True)
        self._show_coverage()

    def _show_coverage(self):
        if self.result is None:
            return
        self.table.clearSelection()
        details = {key: self.result[key] for key in (
            "status", "complete", "fidelity_verified", "publish_ready", "live_ready", "original", "replacement",
            "chronology", "limitations", "issues", "summary", "warning")}
        details["local_records"] = {
            "original": {key: self.original_record[key] for key in ("id", "job_id", "captured_at", "sha256")},
            "replacement": {key: self.records[self.capture_combo.currentData()][key]
                            for key in ("id", "job_id", "captured_at", "sha256")},
        }
        self.detail.setPlainText(json.dumps(details, ensure_ascii=False, indent=2))

    def _show_row(self):
        index = self.table.currentRow()
        if (self.table.selectedItems() and 0 <= index < len(self.rows)
                and not self.table.isRowHidden(index)):
            self.detail.setPlainText(json.dumps(self.rows[index], ensure_ascii=False, indent=2))
        else:
            self.detail.clear()

    def _filter_rows(self, *_args):
        query = self.filter_input.text().strip().casefold()
        group = self.scope_combo.currentData()
        for index, row in enumerate(self.rows):
            text = " ".join(self.table.item(index, col).text() for col in range(5)).casefold()
            hidden = (bool(query and query not in text) or (group is not None and row["group"] != group)
                      or (self.gaps_only.isChecked() and row["data"]["status"] == "matched"))
            self.table.setRowHidden(index, hidden)
        self._show_row()

    def clear_private_data(self):
        self._clear_result()
        self.original_inventory = None
        self.original_record = {}
        self.original_job = {}
        self.jobs.clear()
        self.records.clear()
        for combo in (self.job_combo, self.capture_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)
        self.original_label.clear()
        self.replacement_label.clear()
        self.compare_button.setEnabled(False)

    def done(self, result):
        self.clear_private_data()
        super().done(result)
