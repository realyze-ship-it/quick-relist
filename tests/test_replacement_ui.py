"""Synthetic read-only Qt comparisons; no browser, network or actual records.

Run with unittest or pytest. No pytest dependency is needed for this module:
    python -m unittest discover -s tests -p test_replacement_ui.py -v
Qt tests are explicitly skipped when PySide6 is not installed.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PySide6.QtCore import Qt, QSettings
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QAbstractItemView, QDialog, QPushButton
except ImportError:
    QApplication = None
else:
    from quick_relist import replacement_ui, ui
    from quick_relist.core import Engine, Store
    from quick_relist.drafts import DraftStore
    from test_replacement_review import add_rooms, capture, checkbox, field, page, seal, selection


class ReadOnlyJobs:
    """Only the two permitted job reads are exposed to the dialog."""
    def __init__(self, store):
        self.store = store

    def get_job(self, ident):
        return self.store.get_job(ident)

    def list_jobs(self):
        return self.store.list_jobs()


class ReadOnlyCaptures:
    """No draft save, address save, transaction or Engine entry point."""
    def __init__(self, drafts):
        self.drafts = drafts

    def get_capture(self, ident):
        return self.drafts.get_capture(ident)

    def list_captures(self, ident):
        return self.drafts.list_captures(ident)


@unittest.skipUnless(QApplication is not None, "PySide6 is not installed; Qt UI tests were not run")
class ReplacementUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.store = Store(":memory:")
        self.drafts = DraftStore(":memory:")
        self.addCleanup(self.store.close)
        self.addCleanup(self.drafts.close)
        Engine(self.store).new_batch(["RXCU10001", "RXCU20002", "RXCU30003"])
        self.original_job, self.replacement_job, self.empty_job = self.store.list_jobs()
        self.read_jobs = ReadOnlyJobs(self.store)
        self.read_captures = ReadOnlyCaptures(self.drafts)
        self.original_id = self.drafts.save_capture(self.original_job["id"], capture())
        self.replacement_id = self.drafts.save_capture(self.replacement_job["id"],
            capture(page([field(value="Synthetic replacement")]), replacement=True))

    def dialog(self, original_id=None):
        widget = replacement_ui.ReplacementComparisonDialog(self.original_job,
            self.drafts.get_capture(original_id or self.original_id), self.read_captures, self.read_jobs)
        self.addCleanup(self.dispose, widget)
        return widget

    def dispose(self, widget):
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def choose(self, widget, ident=None, job=None):
        job = job or self.replacement_job
        widget.job_combo.setCurrentIndex(widget.job_combo.findData(job["id"]))
        widget.capture_combo.setCurrentIndex(widget.capture_combo.findData(ident or self.replacement_id))

    def row(self, widget, key, scope=None):
        return next(index for index, row in enumerate(widget.rows)
                    if row["data"].get("key") == key and (scope is None or row["data"].get("scope") == scope))

    def storage_dump(self):
        return tuple("\n".join(storage.conn.iterdump()) for storage in (self.store, self.drafts))

    def test_explicit_replacement_job_and_capture_no_defaults(self):
        widget = self.dialog()
        self.assertIsNone(widget.job_combo.currentData())
        self.assertFalse(widget.capture_combo.isEnabled())
        self.assertFalse(widget.compare_button.isEnabled())
        self.assertEqual(widget.job_combo.findData(self.original_job["id"]), -1)
        widget.job_combo.setCurrentIndex(widget.job_combo.findData(self.replacement_job["id"]))
        self.assertIsNone(widget.capture_combo.currentData())
        self.assertFalse(widget.compare_button.isEnabled())
        self.assertEqual(widget.table.rowCount(), 0)
        widget.capture_combo.setCurrentIndex(widget.capture_combo.findData(self.replacement_id))
        self.assertTrue(widget.compare_button.isEnabled())
        self.assertEqual(widget.table.rowCount(), 0)
        for text in (self.replacement_job["rxcu"], self.replacement_job["id"], self.replacement_id,
                     self.drafts.get_capture(self.replacement_id)["captured_at"]):
            self.assertIn(text, widget.replacement_label.text())
        self.assertIn(self.original_id, widget.original_label.text())

    def test_exact_selected_capture_not_latest_and_pinned_expected_references(self):
        later = self.drafts.save_capture(self.replacement_job["id"],
            capture(page([field(value="Different newer synthetic value")]), replacement=True))
        widget = self.dialog()
        self.choose(widget, self.replacement_id)
        with patch.object(replacement_ui, "compare_replacement_fields",
                          wraps=replacement_ui.compare_replacement_fields) as compare:
            widget._compare()
        args, options = compare.call_args
        self.assertEqual(options["expected_original_rxcu"], "RXCU10001")
        self.assertEqual(options["expected_replacement_rxcu"], "RXCU20002")
        self.assertEqual(options["expected_original_listing_id"], "111111")
        self.assertEqual(args[1], self.drafts.get_capture(self.replacement_id)["capture"])
        self.assertNotEqual(args[1], self.drafts.get_capture(later)["capture"])
        self.assertEqual(widget.table.item(self.row(widget, "street_name"), 3).text(), "Synthetic replacement")

    def test_comparison_is_read_only_and_never_approves_or_mutates_records(self):
        before = self.storage_dump()
        widget = self.dialog()
        self.choose(widget)
        widget._compare()
        self.assertEqual(widget.table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
        self.assertTrue(widget.detail.isReadOnly())
        self.assertEqual(widget.table.columnCount(), 5)
        self.assertFalse(widget.table.isSortingEnabled())
        for key in ("complete", "fidelity_verified", "publish_ready", "live_ready"):
            self.assertIs(widget.result[key], False)
        self.assertIn("never ready to publish", widget.summary.text())
        self.assertIn("No difference is approved", widget.warning.text())
        captions = {button.text().replace("&", "") for button in widget.findChildren(QPushButton)}
        self.assertEqual(captions, {"Compare these saved captures", "Show capture gaps, chronology and limitations", "Close"})
        widget._compare()  # Repeated comparison has no persistent side effect.
        widget.reject()
        self.assertEqual(self.storage_dump(), before)
        self.assertEqual(widget.table.rowCount(), 0)
        self.assertEqual(widget.detail.toPlainText(), "")
        self.assertEqual(widget.original_record, {})
        self.assertEqual(widget.records, {})

    def test_empty_job_and_no_other_job_are_explicit(self):
        widget = self.dialog()
        widget.job_combo.setCurrentIndex(widget.job_combo.findData(self.empty_job["id"]))
        self.assertIn("no saved observations", widget.summary.text())
        self.assertFalse(widget.compare_button.isEnabled())
        with patch.object(self.read_jobs, "list_jobs", return_value=[self.original_job]):
            no_choices = self.dialog()
        self.assertIn("No different saved listing job", no_choices.summary.text())
        self.assertFalse(no_choices.job_combo.isEnabled())

    def test_same_reference_jobs_excluded_even_with_hyphen(self):
        duplicate = {**self.original_job, "id": "synthetic-duplicate", "rxcu": "RXCU-10001"}
        with patch.object(self.read_jobs, "list_jobs", return_value=[duplicate, self.replacement_job]):
            widget = self.dialog()
        self.assertEqual(widget.job_combo.findData(duplicate["id"]), -1)
        self.assertGreater(widget.job_combo.findData(self.replacement_job["id"]), 0)

    def test_same_listing_identity_rejected_even_with_different_reference(self):
        same_id = self.drafts.save_capture(self.replacement_job["id"],
                                           capture(replacement=True, listing_id="000111111"))
        widget = self.dialog()
        self.choose(widget, same_id)
        widget._compare()
        self.assertIsNone(widget.result)
        self.assertEqual(widget.table.rowCount(), 0)
        self.assertIn("two distinct matched listings", widget.summary.text())
        self.assertEqual(widget.detail.toPlainText(), "")

    def test_capture_filed_under_wrong_job_reference_is_rejected(self):
        wrong = self.drafts.save_capture(self.replacement_job["id"], capture())
        widget = self.dialog()
        self.choose(widget, wrong)
        widget._compare()
        self.assertIsNone(widget.result)
        self.assertEqual(widget.table.rowCount(), 0)
        self.assertIn("could not be safely compared", widget.summary.text())

    def test_malformed_and_bad_integrity_replacement_never_displays_values(self):
        corrupt = capture(replacement=True)
        corrupt["pages"][0]["fields"][0]["value"] = "SYNTHETIC_PRIVATE_ERROR"
        for value in ({"complete": False, "pages": None, "private": "SYNTHETIC_PRIVATE_ERROR"}, corrupt):
            with self.subTest(value_type=value.get("schema_version")):
                ident = self.drafts.save_capture(self.replacement_job["id"], value)
                widget = self.dialog()
                self.choose(widget, ident)
                widget._compare()
                self.assertIsNone(widget.result)
                self.assertEqual(widget.table.rowCount(), 0)
                self.assertNotIn("SYNTHETIC_PRIVATE_ERROR", widget.summary.text() + widget.detail.toPlainText())

    def test_invalid_original_blocks_choices_without_fallback(self):
        original = self.drafts.save_capture(self.original_job["id"], {"complete": False, "pages": None})
        widget = self.dialog(original)
        self.assertIsNone(widget.original_inventory)
        self.assertFalse(widget.job_combo.isEnabled())
        self.assertFalse(widget.compare_button.isEnabled())
        self.assertIn("selected original observation could not be checked", widget.original_label.text())

    def test_selection_changes_clear_stale_results_details_and_filters(self):
        other = self.drafts.save_capture(self.replacement_job["id"], capture(replacement=True))
        widget = self.dialog()
        self.choose(widget)
        original_label = widget.original_label.text()
        widget._compare()
        widget.table.selectRow(self.row(widget, "street_name"))
        self.assertIn("Synthetic replacement", widget.detail.toPlainText())
        widget.filter_input.setText("street")
        widget.capture_combo.setCurrentIndex(widget.capture_combo.findData(other))
        self.assertIsNone(widget.result)
        self.assertEqual(widget.table.rowCount(), 0)
        self.assertEqual(widget.detail.toPlainText(), "")
        self.assertEqual(widget.filter_input.text(), "")
        self.assertTrue(widget.compare_button.isEnabled())
        self.assertEqual(widget.original_label.text(), original_label)
        widget._compare()
        widget.job_combo.setCurrentIndex(widget.job_combo.findData(self.empty_job["id"]))
        self.assertIsNone(widget.result)
        self.assertEqual(widget.detail.toPlainText(), "")
        self.assertFalse(widget.compare_button.isEnabled())
        self.assertEqual(widget.original_label.text(), original_label)
        widget.job_combo.setCurrentIndex(0)
        self.assertIsNone(widget.capture_combo.currentData())

    def test_cancel_and_escape_preserve_storage_and_never_start_comparison(self):
        before = self.storage_dump()
        widget = self.dialog()
        self.choose(widget)
        widget.show()
        self.app.processEvents()
        with patch.object(replacement_ui, "compare_replacement_fields") as compare:
            QTest.keyClick(widget, Qt.Key.Key_Escape)
        self.assertFalse(widget.isVisible())
        compare.assert_not_called()
        self.assertEqual(self.storage_dump(), before)
        self.assertEqual(widget.original_record, {})

    def test_changed_or_missing_record_recheck_clears_old_result_generic_error(self):
        widget = self.dialog()
        self.choose(widget)
        widget._compare()
        original_read = self.read_captures.get_capture
        def changed(ident):
            record = original_read(ident)
            if ident == self.replacement_id:
                record["captured_at"] = "2026-01-01T00:00:00+00:00"
            return record
        with patch.object(self.read_captures, "get_capture", side_effect=changed):
            widget._compare()
        self.assertIsNone(widget.result)
        self.assertEqual(widget.table.rowCount(), 0)
        self.assertEqual(widget.detail.toPlainText(), "")
        self.assertFalse(widget.compare_button.isEnabled())
        self.assertIn("could not be checked", widget.summary.text())

    def test_changed_job_identity_cannot_retarget_selected_capture(self):
        widget = self.dialog()
        self.choose(widget)
        original_read = self.read_jobs.get_job
        def changed(ident):
            result = original_read(ident)
            if ident == self.original_job["id"]:
                result["rxcu"] = "RXCU99999"
            return result
        with patch.object(self.read_jobs, "get_job", side_effect=changed):
            widget._compare()
        self.assertIsNone(widget.result)
        self.assertEqual(widget.table.rowCount(), 0)
        self.assertIn("could not be checked", widget.summary.text())

    def test_job_list_capture_list_and_comparison_read_errors_are_generic(self):
        private = "SYNTHETIC_PRIVATE_ERROR"
        with patch.object(self.read_jobs, "list_jobs", side_effect=ValueError(private)):
            no_jobs = self.dialog()
        self.assertFalse(no_jobs.job_combo.isEnabled())
        self.assertIn("choices could not be read", no_jobs.summary.text())
        widget = self.dialog()
        with patch.object(self.read_captures, "list_captures", side_effect=ValueError(private)):
            widget.job_combo.setCurrentIndex(widget.job_combo.findData(self.replacement_job["id"]))
        self.assertFalse(widget.capture_combo.isEnabled())
        self.assertIn("captures could not be read", widget.summary.text())
        another = self.dialog()
        self.choose(another)
        with patch.object(self.read_captures, "get_capture", side_effect=ValueError(private)):
            another._compare()
        self.assertIn("could not be checked", another.summary.text())
        for dialog in (no_jobs, widget, another):
            self.assertNotIn(private, dialog.summary.text() + dialog.detail.toPlainText())
            self.assertEqual(dialog.table.rowCount(), 0)

    def test_wrong_record_job_or_duplicate_choices_fail_closed(self):
        widget = self.dialog()
        bad = self.drafts.get_capture(self.replacement_id)
        bad["job_id"] = self.original_job["id"]
        with patch.object(self.read_captures, "list_captures", return_value=[bad]):
            widget.job_combo.setCurrentIndex(widget.job_combo.findData(self.replacement_job["id"]))
        self.assertFalse(widget.capture_combo.isEnabled())
        self.assertEqual(widget.records, {})
        widget.job_combo.setCurrentIndex(0)
        record = self.drafts.get_capture(self.replacement_id)
        with patch.object(self.read_captures, "list_captures", return_value=[record, record]):
            widget.job_combo.setCurrentIndex(widget.job_combo.findData(self.replacement_job["id"]))
        self.assertFalse(widget.compare_button.isEnabled())
        self.assertEqual(widget.records, {})

    def test_values_blank_false_missing_selection_codes_and_plain_text_provenance(self):
        original = self.drafts.save_capture(self.original_job["id"],
            capture(page([field(value="<b>Synthetic plain text</b>"), field("street_number", ""), checkbox(), selection("007")])))
        replacement = self.drafts.save_capture(self.replacement_job["id"],
            capture(page([field(value="<b>Synthetic plain text</b>"), field("street_number", ""), checkbox(), selection("008")]), replacement=True))
        widget = self.dialog(original)
        self.choose(widget, replacement)
        widget._compare()
        self.assertEqual(widget.table.item(self.row(widget, "street_name"), 2).text(), "<b>Synthetic plain text</b>")
        self.assertNotIn("<b>", widget.table.item(self.row(widget, "street_name"), 2).toolTip())
        self.assertIn("observed empty text", widget.table.item(self.row(widget, "street_number"), 2).text())
        self.assertEqual(widget.table.item(self.row(widget, "poa"), 2).text(), "false")
        self.assertEqual(widget.table.item(self.row(widget, "levy"), 2).text(), "Not observed")
        selection_row = self.row(widget, "property_type_id")
        self.assertIn('code: "007"', widget.table.item(selection_row, 2).text())
        self.assertIn('code: "008"', widget.table.item(selection_row, 3).text())
        self.assertEqual(widget.table.item(selection_row, 4).text(), "changed")
        widget.table.selectRow(self.row(widget, "street_name"))
        self.assertIn("<b>Synthetic plain text</b>", widget.detail.toPlainText())
        self.assertIn("pages/0/fields/0", widget.detail.toPlainText())
        self.assertIn("page_sha256", widget.detail.toPlainText())
        self.assertEqual(widget.original_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(widget.replacement_label.textFormat(), Qt.TextFormat.PlainText)

    def test_filters_clear_hidden_selection_and_missing_rows_stay_available(self):
        widget = self.dialog()
        self.choose(widget)
        widget._compare()
        index = self.row(widget, "street_name")
        missing = self.row(widget, "levy")
        widget.table.selectRow(index)
        self.assertIn("street_name", widget.detail.toPlainText())
        widget.filter_input.setText("levy")
        self.assertTrue(widget.table.isRowHidden(index))
        self.assertFalse(widget.table.isRowHidden(missing))
        self.assertEqual(widget.detail.toPlainText(), "")
        widget.filter_input.clear()
        self.assertFalse(widget.table.isRowHidden(index))
        self.assertFalse(widget.table.isRowHidden(missing))
        widget.gaps_only.setChecked(True)
        self.assertFalse(widget.table.isRowHidden(missing))
        widget.filter_input.setText("no synthetic matching row")
        self.assertTrue(all(widget.table.isRowHidden(i) for i in range(widget.table.rowCount())))
        self.assertEqual(widget.detail.toPlainText(), "")

    def test_room_groups_remain_separate_searchable_and_positional(self):
        groups = [[checkbox("has_carpets", False)], [checkbox("has_carpets", True)]]
        original = self.drafts.save_capture(self.original_job["id"], add_rooms(capture(), groups=groups))
        replacement = self.drafts.save_capture(self.replacement_job["id"],
                                               add_rooms(capture(replacement=True), groups=groups))
        widget = self.dialog(original)
        self.choose(widget, replacement)
        widget._compare()
        first = self.row(widget, "has_carpets", {"kind": "room", "category": "bedroom", "index": 1})
        second = self.row(widget, "has_carpets", {"kind": "room", "category": "bedroom", "index": 2})
        self.assertEqual(widget.table.item(first, 2).text(), "false")
        self.assertEqual(widget.table.item(second, 2).text(), "true")
        self.assertIn("Bedroom 2 (position)", widget.table.item(second, 0).text())
        widget.table.selectRow(first)
        self.assertIn("room_groups/0", widget.detail.toPlainText())
        self.assertIn("room_positional_alignment_unverified", widget.detail.toPlainText())
        group = widget.rows[second]["group"]
        widget.scope_combo.setCurrentIndex(widget.scope_combo.findData(group))
        self.assertTrue(widget.table.isRowHidden(first))
        self.assertFalse(widget.table.isRowHidden(second))
        self.assertEqual(widget.detail.toPlainText(), "")
        widget.table.selectRow(second)
        widget.gaps_only.setChecked(True)
        self.assertTrue(widget.table.isRowHidden(second))
        self.assertEqual(widget.detail.toPlainText(), "")
        self.assertTrue(any(not widget.table.isRowHidden(i) and row["data"]["status"] == "missing"
                            for i, row in enumerate(widget.rows)))

    def test_unknown_coverage_pending_exclusion_chronology_and_gaps_visible(self):
        original = capture(page([field("unknown_synthetic", "Raw unmapped synthetic value")]))
        original["pending_room_observations"] = [{"title": "Set bedrooms options", "value": "SYNTHETIC_PENDING_PRIVATE"}]
        original["status"] = "stopped"
        original["limitations"] = ["room_dialog_close_or_identity_unverified"]
        ident = self.drafts.save_capture(self.original_job["id"], seal(original))
        widget = self.dialog(ident)
        self.choose(widget)
        widget._compare()
        self.assertIn("1 pending room observation(s) excluded", widget.scope_warning.text())
        self.assertIn("interrupted", widget.scope_warning.text())
        self.assertIn("windows overlap", widget.scope_warning.text())
        self.assertIn("do not verify Hub creation/save order", widget.scope_warning.text())
        self.assertIn("capture gap(s)", widget.scope_warning.text())
        self.assertIn("page_not_observed", widget.detail.toPlainText())
        self.assertIn("room_dialog_close_or_identity_unverified", widget.detail.toPlainText())
        self.assertIn('"publish_ready": false', widget.detail.toPlainText())
        unknown = next(i for i, row in enumerate(widget.rows) if row.get("coverage_label") == "unknown_synthetic")
        self.assertIn("Raw unmapped synthetic value", widget.table.item(unknown, 2).text())
        widget.table.selectRow(unknown)
        self.assertIn("control_unmapped", widget.detail.toPlainText())
        self.assertNotIn("SYNTHETIC_PENDING_PRIVATE", json.dumps(widget.result))
        for row in range(widget.table.rowCount()):
            widget.table.selectRow(row)
            self.assertNotIn("SYNTHETIC_PENDING_PRIVATE", widget.detail.toPlainText())
        widget.coverage_button.click()
        self.assertIn("pending_room_observations_excluded", widget.detail.toPlainText())
        self.assertNotIn("SYNTHETIC_PENDING_PRIVATE", widget.detail.toPlainText())

    def test_entry_button_requires_stores_and_matched_source(self):
        record = self.drafts.get_capture(self.original_id)
        for kwargs, expected in (({}, False), ({"store": self.store}, True)):
            widget = ui.CapturedObservationsDialog(self.original_job, [record], self.drafts, **kwargs)
            self.addCleanup(self.dispose, widget)
            self.assertEqual(widget.replacement_button.isEnabled(), expected)
        malformed = self.drafts.save_capture(self.original_job["id"], {"complete": False, "identity": [], "pages": []})
        records = [record, self.drafts.get_capture(malformed)]
        widget = ui.CapturedObservationsDialog(self.original_job, records, self.drafts, store=self.store)
        self.addCleanup(self.dispose, widget)
        widget.capture_combo.setCurrentIndex(1)
        self.assertFalse(widget.replacement_button.isEnabled())
        widget.capture_combo.setCurrentIndex(-1)
        self.assertFalse(widget.replacement_button.isEnabled())
        self.assertEqual(widget.source_text.toPlainText(), "")

    def test_entry_uses_current_original_and_closes_without_saving(self):
        newer = self.drafts.save_capture(self.original_job["id"], capture(page([field(value="Newer source")])))
        records = self.drafts.list_captures(self.original_job["id"])
        widget = ui.CapturedObservationsDialog(self.original_job, records, self.drafts, store=self.store)
        self.addCleanup(self.dispose, widget)
        widget.capture_combo.setCurrentIndex(widget.capture_combo.findData(self.original_id))
        before = self.storage_dump()
        seen = []
        def inspect(dialog):
            seen.append((dialog.original_record["id"], dialog.original_job["id"]))
            return QDialog.DialogCode.Rejected
        with patch.object(replacement_ui.ReplacementComparisonDialog, "exec", inspect):
            widget._review_replacement()
        self.assertEqual(seen, [(self.original_id, self.original_job["id"])])
        self.assertNotEqual(seen[0][0], newer)
        self.assertEqual(before, self.storage_dump())

    def test_entry_construction_error_is_generic(self):
        widget = ui.CapturedObservationsDialog(self.original_job,
            [self.drafts.get_capture(self.original_id)], self.drafts, store=self.store)
        self.addCleanup(self.dispose, widget)
        with patch.object(replacement_ui, "ReplacementComparisonDialog", side_effect=ValueError("SYNTHETIC_PRIVATE_ERROR")):
            widget._review_replacement()
        self.assertIn("could not be opened", widget.saved_hint.text())
        self.assertNotIn("SYNTHETIC_PRIVATE_ERROR", widget.saved_hint.text())

    def test_main_window_passes_local_store_without_changing_launcher_api(self):
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "synthetic-settings.ini"), QSettings.Format.IniFormat)
            with patch.object(ui, "QSettings", return_value=settings):
                window = ui.MainWindow(store=self.store, drafts=self.drafts)
            try:
                window.selected_job = self.original_job
                seen = []
                def inspect(dialog):
                    seen.append((dialog.store, dialog.job["id"], dialog.replacement_button.isEnabled()))
                    return QDialog.DialogCode.Rejected
                with patch.object(ui.CapturedObservationsDialog, "exec", inspect):
                    window._show_saved_captures()
                self.assertEqual(seen, [(self.store, self.original_job["id"], True)])
                self.assertFalse(window.live_ready)
            finally:
                self.dispose(window)


if __name__ == "__main__":
    unittest.main()
