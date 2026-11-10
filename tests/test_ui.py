"""Headless UI regression checks using synthetic local data only.

No test opens a browser, contacts Hub, or uses actual account/listing data.
The module skips cleanly when the optional Qt runtime is not installed.
Run with: python -m pytest tests/test_ui.py -q
"""
from __future__ import annotations

from copy import deepcopy
import os
import time
from types import SimpleNamespace

import pytest

# Set before importing Qt or creating QApplication, including on a desktop.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from quick_relist.core import Engine, REQUIRED_ADAPTER_QUALIFICATIONS, Store  # noqa: E402
from quick_relist import ui  # noqa: E402


@pytest.fixture(scope="session")
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def window(application, tmp_path, monkeypatch):
    """Use temporary settings/database and suppress only expected modal alerts."""
    settings_path = str(tmp_path / "synthetic-settings.ini")
    monkeypatch.setattr(ui, "QSettings", lambda *_: QSettings(settings_path, QSettings.Format.IniFormat))
    store = Store(tmp_path / "synthetic-ui.sqlite3")
    engine = Engine(store)
    widget = ui.MainWindow(engine, store)
    widget.test_errors = []
    monkeypatch.setattr(widget, "_show_error", lambda title, message: widget.test_errors.append((title, message)))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok)
    widget.show()
    application.processEvents()
    yield widget
    _finish_worker(application, widget)
    widget.rxcu_input.clear()
    widget.close()
    widget.deleteLater()
    application.processEvents()
    store.close()


def _finish_worker(application, window, seconds=5):
    deadline = time.monotonic() + seconds
    while window._is_busy and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    assert not window._is_busy, "The synthetic worker did not finish in time"
    application.processEvents()


def _add_batch(window, references="RXCU000001\nRXCU000002"):
    window.rxcu_input.setPlainText(references)
    window._create_batch()
    return window.batch_id


def _synthetic_review_job():
    return {
        "rxcu": "SYNTHETIC TEST ONLY",
        "source_id": "synthetic-source",
        "snapshot": {"complete": True},
        "original_address": {
            "street_number": 1,
            "street_name": "Synthetic original street",
            "unit_number": None,
            "latitude": -1.25,
            "show_address_on_web": False,
            "unknown_structure": {"preserved": ["synthetic", 1]},
        },
    }


def test_empty_workspace_has_no_live_actions(window):
    assert window.jobs == []
    assert window.batches == []
    assert window.queue_stack.currentIndex() == 0
    assert not window.create_button.isEnabled()
    assert not window.capture_button.isEnabled()
    assert not window.review_button.isEnabled()
    assert not window.advance_button.isEnabled()
    assert not window.audit_button.isEnabled()
    assert not window.cancel_job_button.isEnabled()
    assert not window.search_hub_button.isEnabled()
    assert "not connected" in window.connection_pill.text().lower()
    assert "not available" in window.notice_title.text().lower()


def test_create_batch_persists_only_local_references(window):
    batch_id = _add_batch(window)
    assert batch_id
    assert len(window.store.list_batches()) == 1
    assert [job["rxcu"] for job in window.store.list_jobs(batch_id)] == ["RXCU000001", "RXCU000002"]
    for job in window.jobs:
        assert job["state"] == "queued"
        assert job["snapshot"] is None
        assert not job["approved"]
    assert not window.rxcu_input.toPlainText()
    assert not window.create_button.isEnabled()
    assert window.audit_button.isEnabled()
    assert window.cancel_job_button.isEnabled()
    assert not window.capture_button.isEnabled()
    assert not window.review_button.isEnabled()
    assert not window.advance_button.isEnabled()
    assert window.stat_values["queued"].text() == "2"
    assert window.stat_values["reviewed"].text() == "0"
    assert window.stat_values["live"].text() == "0"


def test_reference_normalization_and_duplicate_removal(window):
    _add_batch(window, "rxcu000001, RXCU000001;\nRXCU000002")
    assert [job["rxcu"] for job in window.jobs] == ["RXCU000001", "RXCU000002"]
    assert len(window.store.list_batches()) == 1


def test_invalid_reference_does_not_save_a_batch(window):
    window.rxcu_input.setPlainText("NOT-AN-RXCU")
    window._create_batch()
    assert window.test_errors
    assert window.store.list_batches() == []
    assert window.store.list_jobs() == []
    assert window.rxcu_input.toPlainText() == "NOT-AN-RXCU"


def test_repeated_create_after_input_cleared_is_a_noop(window):
    first = _add_batch(window)
    window._create_batch()
    assert window.batch_id == first
    assert len(window.store.list_batches()) == 1


def test_saved_batches_search_and_open(window, application):
    first = _add_batch(window, "RXCU000001")
    _add_batch(window, "RXCU000002")
    window._show_page(1)
    window.saved_search.setText("not-a-match")
    assert all(window.saved_table.isRowHidden(row) for row in range(2))
    assert "No batches match" in window.saved_empty.text()
    window.saved_search.setText("rxcu000001")
    visible_rows = [row for row in range(2) if not window.saved_table.isRowHidden(row)]
    assert len(visible_rows) == 1
    window.saved_table.selectRow(visible_rows[0])
    application.processEvents()
    assert window.open_batch_button.isEnabled()
    window._open_batch()
    assert window.stack.currentIndex() == 0
    assert window.batch_id == first
    assert [job["rxcu"] for job in window.jobs] == ["RXCU000001"]


def test_reopened_store_retains_saved_batch(window):
    batch_id = _add_batch(window)
    second_connection = Store(window.store.path)
    try:
        assert second_connection.list_batches()[0]["id"] == batch_id
        assert len(second_connection.list_jobs(batch_id)) == 2
    finally:
        second_connection.close()


def test_worker_success_callback_and_duplicate_call_guard(window, application):
    calls = []
    results = []

    def operation():
        calls.append("first")
        return {"complete": False, "synthetic": True}

    window._run_operation("Synthetic worker", operation, "Synthetic operation complete", results.append)
    window._run_operation("Must not run", lambda: calls.append("second"), "Wrong result")
    assert window._is_busy
    assert not window.stack.isEnabled()
    _finish_worker(application, window)
    assert calls == ["first"]
    assert results == [{"complete": False, "synthetic": True}]
    assert window.stack.isEnabled()
    assert window.progress.isHidden()
    assert window.statusBar().currentMessage() == "Synthetic operation complete"


def test_worker_failure_restores_controls_without_success(window, application):
    def fail():
        raise RuntimeError("Synthetic test failure")

    results = []
    window._run_operation("Synthetic failure", fail, "Must not appear", results.append)
    _finish_worker(application, window)
    assert results == []
    assert window._operation_error == "Synthetic test failure"
    assert window.test_errors[-1][0] == "Operation did not complete"
    assert window.stack.isEnabled()
    assert window.progress.isHidden()
    assert "stopped" in window.statusBar().currentMessage().lower()


class SyntheticReadOnlyHub:
    """In-memory fixture: deliberately has no network or browser implementation."""

    def __init__(self):
        self.calls = []

    def open(self):
        self.calls.append("open")

    def search_reference(self, reference):
        self.calls.append(("search", reference))
        return {"searched": True, "warning": "Synthetic fixture; no match verified"}

    def inspect_current_editor_page(self):
        self.calls.append("inspect")
        return {"fields": [], "complete": False, "warning": "Synthetic partial fixture"}


def test_readonly_browser_wiring_never_approves_a_snapshot(window, application, monkeypatch):
    hub = SyntheticReadOnlyHub()
    window.hub = hub
    inspections = []
    monkeypatch.setattr(window, "_show_partial_inspection", inspections.append)
    window._open_readonly_hub()
    window._open_readonly_hub()
    _finish_worker(application, window)
    assert hub.calls == ["open"]
    _add_batch(window, "RXCU000123")
    assert window.search_hub_button.isEnabled()
    window._search_selected_reference()
    _finish_worker(application, window)
    assert hub.calls[-1] == ("search", "RXCU000123")
    window._inspect_readonly_hub()
    _finish_worker(application, window)
    assert inspections[0]["complete"] is False
    job = window.store.list_jobs()[0]
    assert job["snapshot"] is None
    assert job["original_address"] is None
    assert not job["approved"]
    assert job["state"] == "queued"
    assert not window.review_button.isEnabled()
    assert not window.advance_button.isEnabled()


def test_partial_inspection_is_rendered_as_plain_text(window, monkeypatch):
    captured = []
    synthetic_html = "<b>SYNTHETIC FIELD, NOT RENDERED HTML</b>"

    def inspect_dialog(dialog):
        editor = dialog.findChild(QtWidgets.QPlainTextEdit)
        assert editor is not None
        assert editor.isReadOnly()
        captured.append(editor.toPlainText())
        return QtWidgets.QDialog.DialogCode.Rejected

    monkeypatch.setattr(QtWidgets.QDialog, "exec", inspect_dialog)
    window._show_partial_inspection({"complete": False, "fields": [{"value": synthetic_html}]})
    assert synthetic_html in captured[0]
    assert window.store.list_jobs() == []


@pytest.mark.parametrize("snapshot", [None, {}, {"complete": False}, {"complete": "true"}, {"complete": 1}])
def test_address_review_rejects_incomplete_or_nonboolean_capture(application, snapshot):
    job = _synthetic_review_job()
    job["snapshot"] = snapshot
    with pytest.raises(ValueError, match="complete, verified source capture"):
        ui.AddressReviewDialog(job)


@pytest.mark.parametrize("address", [None, {}, "Synthetic unstructured address"])
def test_address_review_requires_captured_address(application, address):
    job = _synthetic_review_job()
    job["original_address"] = address
    with pytest.raises(ValueError, match="complete, verified source capture"):
        ui.AddressReviewDialog(job)


def test_address_review_preserves_source_types_and_unknown_structures(application):
    job = _synthetic_review_job()
    untouched = deepcopy(job)
    dialog = ui.AddressReviewDialog(job)
    try:
        assert dialog.corrected_address() == job["original_address"]
        dialog.fields["street_name"].setText("Synthetic corrected street")
        corrected = dialog.corrected_address()
        assert corrected["street_name"] == "Synthetic corrected street"
        assert corrected["street_number"] == 1
        assert isinstance(corrected["street_number"], int)
        assert corrected["latitude"] == -1.25
        assert corrected["unit_number"] is None
        assert corrected["show_address_on_web"] is False
        assert corrected["unknown_structure"] == {"preserved": ["synthetic", 1]}
        assert job == untouched
    finally:
        dialog.close()
        dialog.deleteLater()


def test_address_review_requires_mandate_expired_and_explicit_confirmation(application):
    dialog = ui.AddressReviewDialog(_synthetic_review_job())
    try:
        assert dialog.reason.count() == 2  # Placeholder and the sole allowed reason.
        assert dialog.reason.itemData(1) == "mandate_expired"
        assert "Listing has been cancelled" in dialog.reason.itemText(1)
        assert "Mandate expired" in dialog.reason.itemText(1)
        assert dialog.reason.findData("cancelled") == -1
        assert not dialog.save_button.isEnabled()
        dialog.reason.setCurrentIndex(1)
        assert not dialog.save_button.isEnabled()
        dialog.confirmed.setChecked(True)
        assert dialog.save_button.isEnabled()
        dialog.reason.setCurrentIndex(0)
        assert not dialog.save_button.isEnabled()
        assert not dialog.confirmed.isChecked()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_editing_address_clears_previous_review_confirmation(application):
    dialog = ui.AddressReviewDialog(_synthetic_review_job())
    try:
        dialog.show()
        dialog.reason.setCurrentIndex(1)
        dialog.confirmed.setChecked(True)
        field = dialog.fields["street_name"]
        field.setFocus()
        field.selectAll()
        QTest.keyClicks(field, "Synthetic replacement")
        assert not dialog.confirmed.isChecked()
        assert not dialog.save_button.isEnabled()
        dialog.confirmed.setChecked(True)
        dialog.fields["show_address_on_web"].setChecked(True)
        assert not dialog.confirmed.isChecked()
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("flag", [False, "true", 1, None])
def test_live_ready_requires_exact_true(window, flag):
    window.engine = SimpleNamespace(adapter=SimpleNamespace(is_live_ready=flag))
    assert not window.live_ready


def test_live_ready_requires_every_connector_qualification(window):
    adapter = SimpleNamespace(is_live_ready=True)
    window.engine = SimpleNamespace(adapter=adapter)
    assert not window.live_ready
    adapter.qualification = {key: True for key in REQUIRED_ADAPTER_QUALIFICATIONS}
    assert window.live_ready
    for key in REQUIRED_ADAPTER_QUALIFICATIONS:
        adapter.qualification[key] = False
        assert not window.live_ready
        adapter.qualification[key] = "true"
        assert not window.live_ready
        adapter.qualification[key] = True


def test_unqualified_connector_does_not_show_verified_status(window):
    _add_batch(window, "RXCU000001")
    window.engine.adapter = SimpleNamespace(is_live_ready=True, qualification={})
    window.refresh()
    assert "not connected" in window.connection_pill.text().lower()
    assert window.notice.isVisible()
    assert not window.capture_button.isEnabled()
    assert not window.advance_button.isEnabled()
    assert window.cancel_job_button.isEnabled()


def test_small_window_scrolls_instead_of_clipping_action_buttons(window, application):
    window.resize(1060, 720)
    application.processEvents()
    scroll = window.stack.widget(0)
    assert isinstance(scroll, QtWidgets.QScrollArea)
    assert scroll.verticalScrollBar().maximum() > 0
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    application.processEvents()
    for button in (window.search_hub_button, window.capture_button, window.review_button, window.advance_button, window.cancel_job_button, window.audit_button):
        assert button.height() >= 35
        assert button.width() >= button.minimumSizeHint().width()
    assert not window.grab().isNull()


def test_invalid_or_credential_bearing_hub_urls_are_rejected(window):
    for url in ("http://example.invalid", "javascript:alert(1)", "https://user:password@example.invalid", "not a URL"):
        window.hub_url.setText(url)
        assert window._validated_hub_url() is None
    assert len(window.test_errors) == 4


def test_signin_tokens_are_not_saved_in_settings(window):
    window.hub_url.setText("https://example.invalid/login?token=synthetic-not-real")
    window._save_url()
    assert window._settings.value("hub_url") is None
    assert "clean sign-in URL" in window.test_errors[-1][0]
    window.hub_url.setText("https://example.invalid/login#synthetic")
    window._save_url()
    assert window._settings.value("hub_url") is None


def test_clean_signin_url_can_be_saved_without_opening_browser(window):
    window.hub_url.setText("https://example.invalid/login")
    window._save_url()
    assert window._settings.value("hub_url") == "https://example.invalid/login"
    assert not window.live_ready


def test_declining_cancel_keeps_local_job_unchanged(window, monkeypatch):
    _add_batch(window, "RXCU000001")
    job_id = window.selected_job["id"]
    history = window.store.journal(job_id)
    dialogs = []

    def decline(dialog):
        dialogs.append((dialog.text(), dialog.informativeText()))
        assert dialog.defaultButton() == dialog.button(QtWidgets.QMessageBox.StandardButton.Cancel)
        return QtWidgets.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", decline)
    window._cancel_job()
    assert not window._is_busy
    assert window.store.get_job(job_id)["state"] == "queued"
    assert window.store.journal(job_id) == history
    assert len(dialogs) == 1
    assert "RXCU000001" in dialogs[0][0]
    assert "history will remain" in dialogs[0][1]
    assert "No Hub listing" in dialogs[0][1]


def test_confirmed_cancel_uses_core_and_preserves_record(window, application, monkeypatch):
    batch_id = _add_batch(window, "RXCU000001")
    job_id = window.selected_job["id"]
    previous_history_count = len(window.store.journal(job_id))
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *_: QtWidgets.QMessageBox.StandardButton.Yes)
    window._cancel_job()
    window._cancel_job()  # A repeated click while work is pending cannot repeat it.
    _finish_worker(application, window)
    job = window.store.get_job(job_id)
    assert job["state"] == "cancelled"
    assert job["rxcu"] == "RXCU000001"
    assert job["batch_id"] == batch_id
    assert len(window.store.journal(job_id)) > previous_history_count
    assert window.selected_job["state"] == "cancelled"
    assert window.detail_state.text() == "Cancelled locally"
    assert not window.cancel_job_button.isEnabled()
    assert not window.capture_button.isEnabled()
    assert not window.review_button.isEnabled()
    assert not window.advance_button.isEnabled()
    assert window.audit_button.isEnabled()
    assert window.saved_table.item(0, 3).text() == "Cancelled locally"
    assert "history is preserved" in window.statusBar().currentMessage()


def test_cancel_core_failure_does_not_claim_success(window, application, monkeypatch):
    _add_batch(window, "RXCU000001")
    job_id = window.selected_job["id"]
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *_: QtWidgets.QMessageBox.StandardButton.Yes)

    def deny(_job_id):
        raise RuntimeError("Synthetic cancellation denied by authoritative core")

    monkeypatch.setattr(window.engine, "cancel_job", deny)
    window._cancel_job()
    _finish_worker(application, window)
    assert window.store.get_job(job_id)["state"] == "queued"
    assert window.test_errors[-1][0] == "Operation did not complete"
    assert "cancelled" not in window.statusBar().currentMessage().lower()


@pytest.mark.parametrize("state", ["queued", "awaiting_address", "ready", "blocked"])
def test_cancel_available_only_for_unstarted_states(window, state):
    _add_batch(window, "RXCU000001")
    job_id = window.selected_job["id"]
    # Synthetic local fixture state only; no adapter or external operation exists.
    with window.store._tx() as db:
        window.store._update(db, job_id, state=state)
    window.refresh()
    assert window.cancel_job_button.isEnabled()


@pytest.mark.parametrize("state", ["portals_removed", "archived", "created", "fidelity_verified", "publication_pending", "live", "needs_reconciliation", "cancelled"])
def test_cancel_disabled_for_started_or_terminal_states(window, state):
    _add_batch(window, "RXCU000001")
    job_id = window.selected_job["id"]
    with window.store._tx() as db:
        window.store._update(db, job_id, state=state)
    window.refresh()
    assert not window.cancel_job_button.isEnabled()


@pytest.mark.parametrize("action", ["remove_portals", "archive", "create", "publish"])
def test_cancel_disabled_after_any_destructive_intention(window, monkeypatch, action):
    _add_batch(window, "RXCU000001")
    monkeypatch.setattr(window.store, "journal", lambda *_: [{"action": action, "phase": "intended"}])
    window._select_job()
    assert window.selected_job["state"] == "queued"
    assert not window.cancel_job_button.isEnabled()


def test_cancel_disabled_when_history_cannot_be_checked(window, monkeypatch):
    _add_batch(window, "RXCU000001")

    def unavailable(_job_id):
        raise RuntimeError("Synthetic history unavailable")

    monkeypatch.setattr(window.store, "journal", unavailable)
    window._select_job()
    assert not window.cancel_job_button.isEnabled()


def test_blocked_job_without_write_can_be_cancelled(window, application, monkeypatch):
    _add_batch(window, "RXCU000001")
    job_id = window.selected_job["id"]
    with window.store._tx() as db:
        window.store._update(db, job_id, state="blocked", error="Synthetic pre-write verification block")
    window.refresh()
    assert window.cancel_job_button.isEnabled()
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *_: QtWidgets.QMessageBox.StandardButton.Yes)
    window._cancel_job()
    _finish_worker(application, window)
    assert window.store.get_job(job_id)["state"] == "cancelled"
    assert window.selected_job["state"] == "cancelled"
    assert window.audit_button.isEnabled()


@pytest.mark.parametrize("action", ["remove_portals", "archive", "create", "publish"])
def test_blocked_job_with_write_history_cannot_be_cancelled(window, monkeypatch, action):
    _add_batch(window, "RXCU000001")
    job_id = window.selected_job["id"]
    with window.store._tx() as db:
        window.store._update(db, job_id, state="blocked", error="Synthetic blocked state")
    monkeypatch.setattr(window.store, "journal", lambda *_: [{"action": action, "phase": "intended"}])
    window.refresh()
    assert window.selected_job["state"] == "blocked"
    assert not window.cancel_job_button.isEnabled()
    window._cancel_job()
    assert not window._is_busy
    assert window.store.get_job(job_id)["state"] == "blocked"
