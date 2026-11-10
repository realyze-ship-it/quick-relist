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


@pytest.fixture
def draft_window(window, tmp_path):
    from quick_relist.drafts import DraftStore
    drafts = DraftStore(tmp_path / "synthetic-observations.sqlite3")
    window.drafts = drafts
    window.media_root = tmp_path / "synthetic-media"
    yield window
    drafts.close()


def _synthetic_partial_capture():
    return {
        "capture_kind": "partial_visible_dom", "complete": False, "live_ready": False,
        "listing_id": "SYNTHETIC-UNVERIFIED-ID",
        "identity": {"route_verified": True, "reference_verified": False, "account_verified": False},
        "pages": [{
            "page_key": "details", "page_path": "/synthetic-editor", "complete": False,
            "fields": [
                {"label": "Street name", "name": "street", "id": "synthetic-street", "type": "text", "value": "  SYNTHETIC original street  ", "checked": None},
                {"label": "Show address", "name": "show", "type": "checkbox", "value": "on", "checked": False},
                {"label": "Null field", "name": "null", "type": "text", "value": None},
                {"label": "Unknown structure", "name": "nested", "type": "custom", "value": {"synthetic": [1, True]}},
                {"label": "Suburb", "name": "suburb", "tag": "select", "type": "select-one", "value": "synthetic-1", "selected_options": [{"value": "synthetic-1", "text": "Synthetic original suburb"}]},
                {"label": "Observed number", "name": "number", "value": 1.5},
            ],
            "dialogs": [{"title": "Synthetic address details", "fields": [{"label": "Unit number", "name": "unit", "type": "text", "value": "1"}]}],
        }],
        "coverage": {"missing_pages": ["media", "portals"]},
        "blindspots": [{"code": "source_incomplete", "message": "Synthetic incomplete fixture"}],
    }


def _seed_partial(window):
    _add_batch(window, "RXCU000123")
    capture = _synthetic_partial_capture()
    capture_id = window.drafts.save_capture(window.selected_job["id"], capture)
    window.refresh()
    return capture_id, capture


def test_partial_observation_controls_are_separate_from_live_approval(draft_window):
    window = draft_window
    capture_id, capture = _seed_partial(window)
    assert window.saved_captures_button.isEnabled()
    assert "incomplete" in window.observation_status.text().lower()
    assert "unverified" in window.observation_status.text().lower()
    assert not window.review_button.isEnabled()
    assert not window.capture_button.isEnabled()
    assert not window.advance_button.isEnabled()
    assert window.import_media_button.isEnabled()
    assert not window.capture_page_button.isEnabled()  # No browser is connected.
    assert window.drafts.get_capture(capture_id)["capture"] == capture
    job = window.store.get_job(window.selected_job["id"])
    assert job["snapshot"] is None and job["original_address"] is None
    assert job["approved"] is False and job["state"] == "queued"
    assert window.stat_values["reviewed"].text() == "0"


def test_tentative_editor_uses_exact_captured_controls_and_preserves_all_metadata(application):
    capture = _synthetic_partial_capture()
    untouched = deepcopy(capture)
    dialog = ui.TentativeAddressDialog(capture)
    try:
        assert not dialog.save_button.isEnabled()
        assert dialog.original_address() == {}
        for include in dialog.included.values():
            include.setChecked(True)
        assert dialog.save_button.isEnabled()
        original = dialog.original_address()
        assert original["pages/0/fields/0"] == capture["pages"][0]["fields"][0]
        assert original["pages/0/dialogs/0/fields/0"]["value"] == "1"
        assert dialog.corrected_address() == original
        dialog.fields["pages/0/fields/0"].setPlainText("  SYNTHETIC changed street  ")
        dialog.fields["pages/0/fields/1"].setChecked(True)
        dialog.fields["pages/0/fields/4"].setPlainText("tentative-option-not-resolved")
        edited = dialog.corrected_address()
        assert edited["pages/0/fields/0"]["value"] == "  SYNTHETIC changed street  "
        assert edited["pages/0/fields/1"]["checked"] is True
        assert edited["pages/0/fields/1"]["value"] == "on"
        assert edited["pages/0/fields/2"]["value"] is None
        assert edited["pages/0/fields/3"]["value"] == {"synthetic": [1, True]}
        assert edited["pages/0/fields/4"]["selected_options"] == original["pages/0/fields/4"]["selected_options"]
        assert edited["pages/0/fields/5"]["value"] == 1.5
        assert dialog.fields["pages/0/fields/3"].isReadOnly()
        assert dialog.fields["pages/0/fields/5"].isReadOnly()
        assert capture == untouched
    finally:
        dialog.deleteLater()


def test_tentative_field_filter_does_not_drop_hidden_selected_fields(application):
    dialog = ui.TentativeAddressDialog(_synthetic_partial_capture())
    try:
        path = "pages/0/fields/0"
        dialog.included[path].setChecked(True)
        dialog.filter_input.setText("Unit number")
        assert dialog.field_rows[path].isHidden()
        assert not dialog.field_rows["pages/0/dialogs/0/fields/0"].isHidden()
        assert set(dialog.original_address()) == {path}
        assert dialog.save_button.isEnabled()
        dialog.included[path].setChecked(False)
        assert not dialog.save_button.isEnabled()
    finally:
        dialog.deleteLater()


def test_saved_tentative_revision_reopens_without_changing_original(draft_window, application):
    capture_id, capture = _seed_partial(draft_window)
    dialog = ui.TentativeAddressDialog(capture)
    path = "pages/0/fields/0"
    dialog.included[path].setChecked(True)
    dialog.fields[path].setPlainText("Synthetic tentative street")
    original = dialog.original_address()
    edited = dialog.corrected_address()
    revision_id = draft_window.drafts.save_address(capture_id, original, edited)
    dialog.deleteLater()
    revision = draft_window.drafts.get_address(revision_id)
    reopened = ui.TentativeAddressDialog(capture, revision)
    try:
        assert reopened.included[path].isChecked()
        assert reopened.fields[path].toPlainText() == "Synthetic tentative street"
        assert reopened.original_address() == original
        assert reopened.corrected_address() == edited
        assert revision["approved"] is False
        assert draft_window.drafts.get_capture(capture_id)["capture"] == capture
        assert not draft_window.store.get_job(draft_window.selected_job["id"])["approved"]
    finally:
        reopened.deleteLater()


def test_saved_observations_render_plaintext_and_unknown_media(draft_window, monkeypatch):
    capture_id, capture = _seed_partial(draft_window)
    capture["pages"][0]["fields"][0]["value"] = "<b>SYNTHETIC plain text</b>"
    draft_window.drafts.save_capture(draft_window.selected_job["id"], capture)
    dialog = ui.CapturedObservationsDialog(draft_window.selected_job, draft_window.drafts.list_captures(draft_window.selected_job["id"]), draft_window.drafts)
    try:
        assert dialog.capture_combo.count() == 2
        assert dialog.source_text.isReadOnly()
        assert "<b>SYNTHETIC plain text</b>" in dialog.source_text.toPlainText()
        assert "INCOMPLETE" in dialog.coverage.text()
        assert "media, portals" in dialog.coverage.text()
        assert "not verified" in dialog.saved_hint.text()
        assert dialog.edit_button.isEnabled()
    finally:
        dialog.deleteLater()


def test_cancelled_tentative_dialog_does_not_save_revision(draft_window, monkeypatch):
    capture_id, _ = _seed_partial(draft_window)
    dialog = ui.CapturedObservationsDialog(draft_window.selected_job, draft_window.drafts.list_captures(draft_window.selected_job["id"]), draft_window.drafts)
    monkeypatch.setattr(ui.TentativeAddressDialog, "exec", lambda *_: QtWidgets.QDialog.DialogCode.Rejected)
    dialog._edit_address()
    assert draft_window.drafts.list_addresses(capture_id) == []
    dialog.deleteLater()


def test_tentative_save_adds_revision_but_never_calls_engine_approval(draft_window, monkeypatch):
    capture_id, _ = _seed_partial(draft_window)
    path = "pages/0/fields/0"

    def edit(dialog):
        dialog.included[path].setChecked(True)
        dialog.fields[path].setPlainText("Synthetic tentative revision")
        return QtWidgets.QDialog.DialogCode.Accepted

    def forbidden(*_):
        raise AssertionError("Partial observations must never call engine approval")

    monkeypatch.setattr(ui.TentativeAddressDialog, "exec", edit)
    monkeypatch.setattr(draft_window.engine, "approve_address", forbidden)
    dialog = ui.CapturedObservationsDialog(draft_window.selected_job, draft_window.drafts.list_captures(draft_window.selected_job["id"]), draft_window.drafts)
    dialog._edit_address()
    dialog._edit_address()
    revisions = draft_window.drafts.list_addresses(capture_id)
    assert len(revisions) == 2
    assert all(revision["approved"] is False for revision in revisions)
    assert revisions[0]["original"][path]["value"] == "  SYNTHETIC original street  "
    assert revisions[0]["edited"][path]["value"] == "Synthetic tentative revision"
    assert "approval remains blocked" in dialog.saved_hint.text()
    dialog.deleteLater()


def test_capture_current_page_is_saved_once_and_keeps_live_workflow_blocked(draft_window, application, monkeypatch):
    from quick_relist import capture as capture_module
    window = draft_window
    _add_batch(window, "RXCU000123")
    calls = []
    capture = _synthetic_partial_capture()
    window.hub = SimpleNamespace(driver=object(), select_editor_tab=lambda: calls.append("select"))

    class SyntheticCollector:
        def __init__(self, driver):
            calls.append("new collector")

        def capture_current_page(self):
            calls.append("read")
            return deepcopy(capture)

    monkeypatch.setattr(capture_module, "PartialHubCapture", SyntheticCollector)
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *_: QtWidgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(window, "_show_saved_captures", lambda: calls.append("review"))
    window.refresh()
    journal = window.store.journal(window.selected_job["id"])
    window._capture_current_page()
    window._capture_current_page()
    _finish_worker(application, window)
    assert calls == ["select", "new collector", "read", "review"]
    assert len(window.drafts.list_captures(window.selected_job["id"])) == 1
    assert window.store.journal(window.selected_job["id"]) == journal
    assert not window.review_button.isEnabled() and not window.advance_button.isEnabled()
    window._capture_current_page()
    _finish_worker(application, window)
    assert calls.count("select") == 1 and calls.count("new collector") == 1
    assert calls.count("read") == 2
    assert len(window.drafts.list_captures(window.selected_job["id"])) == 2
    assert window.store.get_job(window.selected_job["id"])["snapshot"] is None


def test_declined_page_capture_has_no_browser_or_storage_action(draft_window, monkeypatch):
    _add_batch(draft_window, "RXCU000123")
    calls = []
    draft_window.hub = SimpleNamespace(driver=object(), select_editor_tab=lambda: calls.append("select"))
    draft_window.refresh()
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *_: QtWidgets.QMessageBox.StandardButton.Cancel)
    draft_window._capture_current_page()
    assert calls == []
    assert not draft_window._is_busy
    assert draft_window.drafts.list_captures(draft_window.selected_job["id"]) == []


def test_capture_failure_is_redacted_and_does_not_claim_saved(draft_window, application, monkeypatch, capfd):
    from quick_relist import capture as capture_module
    _add_batch(draft_window, "RXCU000123")
    secret_fixture = "SYNTHETIC PRIVATE VALUE - NEVER LOG"
    draft_window.hub = SimpleNamespace(driver=object(), select_editor_tab=lambda: None)

    class FailingCollector:
        def __init__(self, driver):
            pass

        def capture_current_page(self):
            raise RuntimeError(secret_fixture)

    monkeypatch.setattr(capture_module, "PartialHubCapture", FailingCollector)
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *_: QtWidgets.QMessageBox.StandardButton.Yes)
    draft_window.refresh()
    draft_window._capture_current_page()
    _finish_worker(application, draft_window)
    assert draft_window.drafts.list_captures(draft_window.selected_job["id"]) == []
    assert secret_fixture not in str(draft_window.test_errors)
    assert secret_fixture not in str(capfd.readouterr())
    assert "stopped" in draft_window.statusBar().currentMessage().lower()
    assert not draft_window.review_button.isEnabled()


def test_local_media_import_is_incomplete_and_never_grants_address_approval(draft_window, application, monkeypatch):
    from quick_relist import media
    _add_batch(draft_window, "RXCU000123")
    calls = []
    manifest = {"complete": False, "media_count": 1, "media_manifest": [{"id": "synthetic", "kind": None, "position": None, "caption": None}], "unresolved": ["Synthetic media identity and completeness are unknown"]}
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *_: ("synthetic.zip", ""))
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *_: QtWidgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(media, "import_media_zip", lambda path, root: (calls.append((path, root)), manifest)[1])
    monkeypatch.setattr(draft_window, "_show_saved_captures", lambda: None)
    draft_window.refresh()
    draft_window._import_media()
    draft_window._import_media()
    _finish_worker(application, draft_window)
    assert len(calls) == 1
    records = draft_window.drafts.list_captures(draft_window.selected_job["id"])
    assert len(records) == 1 and records[0]["capture"] == manifest
    dialog = ui.CapturedObservationsDialog(draft_window.selected_job, records, draft_window.drafts)
    assert not dialog.edit_button.isEnabled()
    assert "completeness unknown" in dialog.capture_combo.currentText()
    assert "Synthetic media identity" in dialog.source_text.toPlainText()
    assert not draft_window.advance_button.isEnabled()
    assert not draft_window.review_button.isEnabled()
    dialog.deleteLater()


def test_declining_media_selection_does_not_import(draft_window, monkeypatch):
    _add_batch(draft_window, "RXCU000123")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *_: ("", ""))
    draft_window._import_media()
    assert not draft_window._is_busy
    assert draft_window.drafts.list_captures(draft_window.selected_job["id"]) == []


def test_capture_confirmation_explicitly_covers_editor_selection_and_unverified_identity(draft_window, monkeypatch):
    _add_batch(draft_window, "RXCU000123")
    seen = []

    def decline(box):
        assert box.defaultButton() == box.button(QtWidgets.QMessageBox.StandardButton.Cancel)
        seen.append(box.informativeText())
        return QtWidgets.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", decline)
    assert draft_window._confirm_local_observation("Read a partial page") is False
    assert "first page capture selects the sole open Hub editor" in seen[0]
    assert "later captures stay pinned" in seen[0]
    assert "not automatically matched" in seen[0]
    assert "without encryption" in seen[0]


def test_real_capture_collector_pins_first_editor_and_rejects_changed_listing(draft_window, application, monkeypatch):
    from quick_relist import capture as capture_module
    _add_batch(draft_window, "RXCU000123")
    calls = []

    class SyntheticDriver:
        current_window_handle = "manage"
        window_handles = ["manage", "editor"]
        current_url = "https://hub.remax.co.za/app/listings"

        def execute_script(self, script):
            assert script == capture_module.READ_PAGE_SCRIPT
            calls.append("read")
            raw = {key: [] for key in capture_module._PAYLOAD_LISTS}
            raw.update({"href": self.current_url, "ready_state": "complete", "unavailable": False})
            raw["fields"] = _synthetic_partial_capture()["pages"][0]["fields"]
            return raw

    driver = SyntheticDriver()

    def select():
        calls.append("select")
        driver.current_window_handle = "editor"
        driver.current_url = "https://hub.remax.co.za/app/listings/edit/synthetic-original"

    draft_window.hub = SimpleNamespace(driver=driver, select_editor_tab=select)
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *_: QtWidgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(draft_window, "_show_saved_captures", lambda: None)
    draft_window.refresh()
    draft_window._capture_current_page()
    _finish_worker(application, draft_window)
    records = draft_window.drafts.list_captures(draft_window.selected_job["id"])
    assert len(records) == 1
    assert capture_module.verify_capture_integrity(records[0]["capture"])
    assert records[0]["capture"]["identity"]["reference_verified"] is False
    driver.current_url = "https://hub.remax.co.za/app/listings/edit/synthetic-different"
    draft_window._capture_current_page()
    _finish_worker(application, draft_window)
    assert calls == ["select", "read"]  # No second selection or read after identity changes.
    assert len(draft_window.drafts.list_captures(draft_window.selected_job["id"])) == 1
    assert "stopped" in draft_window.statusBar().currentMessage().lower()
    assert draft_window.store.get_job(draft_window.selected_job["id"])["snapshot"] is None


def test_ui_saved_revision_error_is_redacted_without_losing_original(draft_window, monkeypatch, capfd):
    capture_id, capture = _seed_partial(draft_window)
    secret_fixture = "SYNTHETIC PRIVATE ADDRESS - NEVER LOG"

    def edit(dialog):
        dialog.included["pages/0/fields/0"].setChecked(True)
        return QtWidgets.QDialog.DialogCode.Accepted

    def fail(*_):
        raise RuntimeError(secret_fixture)

    dialog = ui.CapturedObservationsDialog(draft_window.selected_job, draft_window.drafts.list_captures(draft_window.selected_job["id"]), draft_window.drafts)
    monkeypatch.setattr(ui.TentativeAddressDialog, "exec", edit)
    monkeypatch.setattr(draft_window.drafts, "save_address", fail)
    dialog._edit_address()
    assert "could not be saved" in dialog.saved_hint.text()
    assert secret_fixture not in dialog.saved_hint.text()
    assert secret_fixture not in str(capfd.readouterr())
    assert draft_window.drafts.get_capture(capture_id)["capture"] == capture
    assert draft_window.drafts.list_addresses(capture_id) == []
    dialog.deleteLater()


def test_cancelled_job_retains_observation_review_but_disables_new_capture(draft_window):
    _seed_partial(draft_window)
    job_id = draft_window.selected_job["id"]
    draft_window.hub = SimpleNamespace(driver=object())
    draft_window.engine.cancel_job(job_id)
    draft_window.refresh()
    assert draft_window.saved_captures_button.isEnabled()
    assert not draft_window.capture_page_button.isEnabled()
    assert not draft_window.import_media_button.isEnabled()
    assert not draft_window.advance_button.isEnabled()


def test_new_observation_buttons_fit_small_window(draft_window, application):
    _seed_partial(draft_window)
    draft_window.resize(1060, 720)
    application.processEvents()
    for button in (draft_window.capture_page_button, draft_window.saved_captures_button, draft_window.import_media_button):
        assert button.height() >= 35
        assert button.width() >= button.minimumSizeHint().width()
    scroll = draft_window.stack.widget(0)
    assert scroll.verticalScrollBar().maximum() > 0
    assert not draft_window.grab().isNull()


def test_noncheckbox_boolean_value_is_preserved_readonly(application):
    capture = _synthetic_partial_capture()
    capture["pages"][0]["fields"].append({"label": "Synthetic boolean metadata", "value": True})
    dialog = ui.TentativeAddressDialog(capture)
    try:
        path = "pages/0/fields/6"
        dialog.included[path].setChecked(True)
        assert dialog.fields[path].isReadOnly()
        assert dialog.corrected_address()[path]["value"] is True
    finally:
        dialog.deleteLater()
