"""Dependency-free tests of production UI decision/dispatch methods.

These compile the named methods from ui.py and supply inert widget doubles.
They do not import Qt, test layout or thread execution. Real Qt integration
coverage is in test_ui.py and requires the optional PySide6/pytest packages.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class Widget:
    def __init__(self):
        self.enabled, self.visible, self.text, self.tip = False, False, "", ""
    def setEnabled(self, value): self.enabled = bool(value)
    def isEnabled(self): return self.enabled
    def setVisible(self, value): self.visible = bool(value)
    def hide(self): self.visible = False
    def show(self): self.visible = True
    def setText(self, value): self.text = value
    def setToolTip(self, value): self.tip = value
    def showMessage(self, value): self.text = value


def production_methods():
    tree = ast.parse((Path(__file__).parents[1] / "quick_relist" / "ui.py").read_text())
    window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    names = {"_select_job", "_editor_open_state", "_open_selected_editor", "_recheck_selected_editor", "_search_selected_reference", "_safe_readonly_call"}
    methods = [node for node in window.body if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in methods:
        node.decorator_list = []
    extracted = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *methods], type_ignores=[])
    namespace = {"__name__": "quick_relist.ui", "__package__": "quick_relist",
                 "_state_label": str, "_address_summary": lambda _: "Synthetic address placeholder",
                 "_collected_job_view": lambda *_: None, "_run_status": lambda *_: None,
                 "_relist_state_label": str}
    exec(compile(ast.fix_missing_locations(extracted), "<production UI methods>", "exec"), namespace)
    return {name: namespace[name] for name in names}


class UI:
    def __init__(self, status="none"):
        self.status = status
        self.calls, self.operations = [], []
        self._is_busy, self.live_ready = False, False
        self.jobs = [{"id": "synthetic-job", "rxcu": "RXCU12345", "state": "queued"}]
        self._editor_open_job_id = "synthetic-job"
        self.row = 0
        self.queue_table = SimpleNamespace(currentRow=lambda: self.row)
        self.media_root = self.engine = self.store = self.runs = None
        self.drafts = SimpleNamespace(list_captures=lambda _: [])
        self.hub = SimpleNamespace(editor_open_state=lambda ref: self.status,
                                   editor_bindings={},
                                   recheck_opened_editor=lambda ref: self.calls.append(ref) or {"editor_pending": True},
                                   open_listing_editor=lambda ref: self.calls.append(("open", ref)),
                                   search_reference=lambda ref: self.calls.append(("search", ref)))
        for name in ("capture_button", "search_hub_button", "open_editor_button", "recheck_editor_button",
                     "editor_open_guidance", "review_button", "capture_page_button", "capture_all_pages_button",
                     "saved_captures_button", "import_media_button", "download_media_button", "observation_status",
                     "advance_button", "reconcile_button", "cancel_job_button", "audit_button", "detail_error",
                     "review_download_button", "review_download_session_button", "detail_title", "detail_state",
                     "detail_address", "status_bar", "verify_captured_address_button",
                     "check_publication_button", "collection_summary"):
            setattr(self, name, Widget())
        self._select_job()

    def _run_operation(self, *args, **kwargs):
        self.operations.append((args, kwargs))
        self._is_busy = True

    def statusBar(self): return self.status_bar


for name, method in production_methods().items():
    setattr(UI, name, staticmethod(method) if name == "_safe_readonly_call" else method)


class EditorRecheckUITests(unittest.TestCase):
    def test_button_enabled_only_for_selected_valid_pending_intent(self):
        for status in ("none", "pending", "other", "blocked", "unexpected"):
            with self.subTest(status=status):
                ui = UI(status)
                self.assertEqual(ui.recheck_editor_button.enabled, status == "pending")
                self.assertEqual(ui.open_editor_button.enabled, status == "none")
                self.assertEqual(ui.search_hub_button.enabled, status == "none")
                self.assertEqual(ui.editor_open_guidance.visible, status != "none")
                self.assertFalse(ui.capture_button.enabled or ui.review_button.enabled or ui.advance_button.enabled or ui.reconcile_button.enabled)
                self.assertTrue(ui.capture_page_button.enabled)  # Explicit unverified mode is preserved.

    def test_no_selected_job_and_busy_state_disable_recheck(self):
        ui = UI("pending")
        ui.row = -1
        ui._select_job()
        self.assertFalse(ui.recheck_editor_button.enabled)
        self.assertFalse(ui.editor_open_guidance.visible)
        ui.row, ui._is_busy = 0, True
        ui._select_job()
        self.assertFalse(ui.recheck_editor_button.enabled)

    def test_pending_guidance_explains_no_second_click_and_partial_limits(self):
        ui = UI("pending")
        self.assertIn("without clicking the link again", ui.editor_open_guidance.text)
        self.assertIn("incomplete and unverified", ui.editor_open_guidance.text)
        ui.status = "other"
        ui._select_job()
        self.assertIn("original job", ui.editor_open_guidance.text)
        ui.status = "blocked"
        ui._select_job()
        self.assertIn("restart Quick Relist", ui.editor_open_guidance.text)
        self.assertIn("No force or reset", ui.editor_open_guidance.text)

    def test_pending_intent_blocks_old_matched_binding_and_download(self):
        ui = UI("pending")
        ui.hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "old"}
        ui._select_job()
        self.assertFalse(ui.capture_all_pages_button.enabled)
        self.assertFalse(ui.download_media_button.enabled)
        ui._open_selected_editor()
        ui._search_selected_reference()
        self.assertEqual(ui.operations, [])
        self.assertEqual(ui.calls, [])

    def test_recheck_dispatches_through_existing_background_path_once(self):
        ui = UI("pending")
        ui._recheck_selected_editor()
        ui._recheck_selected_editor()
        self.assertEqual(ui.calls, [])  # Scheduling does not run browser reads on the UI thread.
        self.assertEqual(len(ui.operations), 1)
        args, kwargs = ui.operations[0]
        self.assertEqual(kwargs, {})
        result = args[1]()
        self.assertEqual(ui.calls, ["RXCU12345"])
        args[3](result)
        self.assertIn("still unverified", ui.status_bar.text)
        self.assertIn("No link was clicked again", ui.status_bar.text)

    def test_success_removes_recheck_and_enables_matched_capture_only(self):
        ui = UI("pending")
        def recheck(reference):
            ui.status = "none"
            ui.hub.editor_bindings[reference] = {"listing_id": "123", "handle": "target"}
            return {"row_link_verified": True, "complete": False}
        ui.hub.recheck_opened_editor = recheck
        ui._recheck_selected_editor()
        args, _ = ui.operations[0]
        result = args[1]()
        ui._is_busy = False
        ui._select_job()
        args[3](result)
        self.assertFalse(ui.recheck_editor_button.enabled or ui.editor_open_guidance.visible)
        self.assertTrue(ui.capture_all_pages_button.enabled)
        self.assertFalse(ui.capture_button.enabled or ui.advance_button.enabled)
        self.assertIn("Account and complete source remain unverified", args[2])

    def test_blocked_recheck_is_sanitized_and_guidance_changes_after_refresh(self):
        ui = UI("pending")
        def fail(_):
            ui.status = "blocked"
            raise RuntimeError("SYNTHETIC_PRIVATE_BROWSER_CONTENT")
        ui.hub.recheck_opened_editor = fail
        ui._recheck_selected_editor()
        with self.assertRaises(RuntimeError) as caught:
            ui.operations[0][0][1]()
        self.assertNotIn("SYNTHETIC_PRIVATE", str(caught.exception))
        ui._is_busy = False
        ui._select_job()
        self.assertFalse(ui.recheck_editor_button.enabled)
        self.assertIn("restart", ui.editor_open_guidance.text)

    def test_selection_or_intent_change_before_dispatch_cannot_recheck_other_job(self):
        ui = UI("pending")
        ui.status = "other"
        ui._recheck_selected_editor()
        self.assertEqual(ui.operations, [])
        self.assertFalse(ui.recheck_editor_button.enabled)
        self.assertIn("original job", ui.editor_open_guidance.text)

    def test_another_job_with_the_same_reference_cannot_consume_the_opening(self):
        ui = UI("pending")
        ui.jobs.append({"id": "other-job", "rxcu": "RXCU12345", "state": "queued"})
        ui.row = 1
        ui._select_job()
        self.assertFalse(ui.recheck_editor_button.enabled)
        self.assertIn("original job", ui.editor_open_guidance.text)
        ui._recheck_selected_editor()
        self.assertEqual(ui.operations, [])
        ui.row = 0
        ui._select_job()
        self.assertTrue(ui.recheck_editor_button.enabled)


if __name__ == "__main__":
    unittest.main()
