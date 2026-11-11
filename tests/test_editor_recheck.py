"""Offline opening/recheck regressions; runnable without optional packages.

Run: python -m unittest discover -s tests -p 'test_editor_recheck*.py' -v
The Selenium import shim supplies selector constants only. No browser, Hub,
network, storage, JavaScript execution or real account data is used here.
"""
from copy import deepcopy
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from quick_relist import hub_readonly as module


REF = "RXCU12345"
URL = module.MANAGE_URL + "/edit/123"
MATCH = {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
LOADING = {"outer_count": 0, "inner_count": 0, "outer_matches": False, "inner_matches": False}


class Element:
    def __init__(self, text="", children=None, action=None, href=None):
        self.text, self.children, self.action, self.href = text, children or {}, action, href
        self.clicks = 0

    def is_displayed(self): return True
    def is_enabled(self): return True
    def find_elements(self, _by, selector): return self.children.get(selector, [])
    def get_attribute(self, key):
        return {"href": self.href, "aria-controls": "synthetic-menu", "aria-expanded": "true", "id": "synthetic-menu"}.get(key)

    def click(self):
        self.clicks += 1
        if self.action:
            self.action()


class Driver:
    """Only explicitly permitted DOM/tab operations exist in this fake."""
    def __init__(self):
        self.urls = {"source": module.MANAGE_URL}
        self.current_window_handle = "source"
        self.window_handles = ["source"]
        self.switch_to = self
        self.root = object()
        self.menu_open = False
        self.source_valid = True
        self.identity = deepcopy(LOADING)
        self.click_mode = "open"
        self.quit_fails = False
        self.quit_calls = 0
        self.after_identity = None
        self.after_source = None
        self.scripts = []
        self.switches = []
        self.paragraph = Element("Ref: RXCU-12345")
        self.combo = Element(action=lambda: setattr(self, "menu_open", True))
        self.row = Element(children={"p": [self.paragraph], '[role="combobox"]': [self.combo]})
        self.link = Element("Edit Listing", href=URL, action=self.click_link)
        self.archive = Element("Archive Listing", action=lambda: self._forbidden())
        self.menu = Element(children={'[role="option"]': [Element(children={"a": [self.link, self.archive]})]})

    @property
    def current_url(self): return self.urls[self.current_window_handle]

    def window(self, handle):
        if handle not in self.window_handles:
            raise RuntimeError("SYNTHETIC_PRIVATE_MISSING_TAB")
        self.current_window_handle = handle
        self.switches.append(handle)

    def add_target(self, handle="target", url=URL):
        self.window_handles.append(handle)
        self.urls[handle] = url

    def click_link(self):
        if self.click_mode in ("open", "open_raise"):
            self.add_target()
        if self.click_mode in ("raise", "open_raise"):
            raise RuntimeError("SYNTHETIC_PRIVATE_CLICK_ERROR")
        self.menu_open = False  # Recheck must not require the old menu.

    def find_elements(self, _by, selector):
        if selector == '[role="row"]': return [self.row]
        if selector == '[role="listbox"]': return [self.menu] if self.menu_open else []
        raise AssertionError("Unexpected synthetic selector")

    def execute_script(self, script, *args):
        self.scripts.append(script)
        if script == "return document.documentElement;": return self.root
        if script == module.ROW_BINDING_SCRIPT:
            return args == (self.row, self.combo, self.menu, self.link, REF)
        if script == module.SOURCE_CONTEXT_SCRIPT:
            valid = self.source_valid and args == (self.root, self.row, REF) and self.paragraph.text == "Ref: RXCU-12345"
            if self.after_source: self.after_source()
            return valid
        if script == module.IDENTITY_SCRIPT:
            assert args == (REF,)
            result = deepcopy(self.identity)
            if self.after_identity: self.after_identity()
            return result
        raise AssertionError("Unexpected browser script")

    def quit(self):
        self.quit_calls += 1
        if self.quit_fails: raise RuntimeError("SYNTHETIC_PRIVATE_QUIT_ERROR")

    def _forbidden(self, *args, **kwargs): raise AssertionError("No navigation, network or mutation allowed")
    get = refresh = back = forward = execute_async_script = execute_cdp_cmd = _forbidden


def selector_modules():
    names = ("selenium", "selenium.webdriver", "selenium.webdriver.common", "selenium.webdriver.common.by")
    result = {name: ModuleType(name) for name in names}
    result[names[-1]].By = SimpleNamespace(CSS_SELECTOR="css selector", TAG_NAME="tag name", XPATH="xpath")
    return result


class EditorRecheckTests(unittest.TestCase):
    def setUp(self):
        self.imports = patch.dict(sys.modules, selector_modules())
        self.imports.start()
        self.addCleanup(self.imports.stop)
        self.driver = Driver()
        self.hub = module.ReadOnlyHub(self.driver)

    def timeout(self):
        with self.assertRaisesRegex(RuntimeError, "still unverified"):
            self.hub.open_listing_editor("rxcu-12345", timeout=.05, poll_interval=.01)
        self.assertEqual(self.hub.editor_open_state(REF), "pending")
        self.assertEqual(self.driver.link.clicks, 1)
        self.assertEqual(self.hub.editor_bindings, {})

    def assert_blocked(self):
        with self.assertRaises(RuntimeError) as caught:
            self.hub.recheck_opened_editor(REF)
        self.assertNotIn("SYNTHETIC_PRIVATE", str(caught.exception))
        self.assertEqual(self.hub.editor_open_state(REF), "blocked")
        self.assertEqual(self.hub.editor_bindings, {})
        self.driver.identity = deepcopy(MATCH)
        with self.assertRaises(RuntimeError): self.hub.recheck_opened_editor(REF)
        with self.assertRaises(RuntimeError): self.hub.open_listing_editor(REF)
        self.assertEqual(self.driver.link.clicks, 1)
        self.assertEqual(self.driver.archive.clicks, 0)

    def test_normal_success_preserves_existing_result_contract(self):
        self.driver.identity = deepcopy(MATCH)
        result = self.hub.open_listing_editor(REF, timeout=.05, poll_interval=.01)
        self.assertTrue(result["editor_reference_matches"] and result["row_link_verified"] and result["route_matches"])
        self.assertFalse(result["complete"] or result["account_verified"])
        self.assertEqual(self.hub.editor_bindings[REF], {"listing_id": "123", "handle": "target"})
        self.assertEqual(self.hub.editor_open_state(REF), "none")
        self.assertEqual(self.driver.combo.clicks, 1)
        self.assertEqual(self.driver.link.clicks, 1)
        self.assertEqual(self.driver.archive.clicks, 0)

    def test_timeout_then_successful_recheck_clicks_original_link_only_once(self):
        self.timeout()
        self.assertFalse(self.driver.menu_open)
        intent = self.hub._pending_editor
        self.assertEqual(intent.target_handle, "target")
        self.driver.identity = deepcopy(MATCH)
        result = self.hub.recheck_opened_editor("rxcu-12345")
        self.assertTrue(result["row_link_verified"])
        self.assertEqual(self.driver.current_window_handle, "target")
        self.assertEqual(self.driver.link.clicks, 1)
        self.assertEqual(self.driver.combo.clicks, 1)
        self.assertIsNone(self.hub._pending_editor)

    def test_repeated_missing_headings_stay_pending_without_navigation(self):
        self.timeout()
        intent = self.hub._pending_editor
        for identity in (LOADING, {**MATCH, "inner_count": 0, "inner_matches": False}, LOADING):
            self.driver.identity = deepcopy(identity)
            self.assertEqual(self.hub.recheck_opened_editor(REF), {"editor_pending": True, "complete": False})
            self.assertIs(self.hub._pending_editor, intent)
            self.assertEqual(self.hub.editor_open_state(REF), "pending")
        self.assertEqual(self.driver.link.clicks, 1)
        self.assertEqual(self.driver.combo.clicks, 1)

    def test_headings_disappearing_at_final_read_stay_pending_then_can_match(self):
        self.timeout()
        intent = self.hub._pending_editor
        self.driver.identity = deepcopy(MATCH)
        self.driver.after_identity = lambda: setattr(self.driver, "identity", deepcopy(LOADING))
        self.assertTrue(self.hub.recheck_opened_editor(REF)["editor_pending"])
        self.assertEqual(self.hub.editor_open_state(REF), "pending")
        self.assertIs(self.hub._pending_editor, intent)
        self.assertEqual(self.hub.editor_bindings, {})
        self.driver.after_identity = None
        self.driver.identity = deepcopy(MATCH)
        self.assertTrue(self.hub.recheck_opened_editor(REF)["row_link_verified"])
        self.assertEqual(self.driver.link.clicks, 1)

    def test_wrong_or_duplicate_headings_at_final_read_are_sticky(self):
        for conflict in ({**MATCH, "outer_matches": False}, {**MATCH, "inner_count": 2}):
            with self.subTest(conflict=conflict):
                self.driver = Driver()
                self.hub = module.ReadOnlyHub(self.driver)
                self.timeout()
                self.driver.identity = deepcopy(MATCH)
                self.driver.after_identity = lambda: setattr(self.driver, "identity", deepcopy(conflict))
                self.assert_blocked()

    def test_no_target_yet_can_be_rechecked_without_a_second_click(self):
        self.driver.click_mode = "none"
        self.timeout()
        self.assertIsNone(self.hub._pending_editor.target_handle)
        self.assertTrue(self.hub.recheck_opened_editor(REF)["editor_pending"])
        self.driver.add_target()
        self.driver.identity = deepcopy(MATCH)
        self.assertTrue(self.hub.recheck_opened_editor(REF)["row_link_verified"])
        self.assertEqual(self.driver.link.clicks, 1)

    def test_click_uncertainty_with_and_without_an_immediate_tab(self):
        for mode in ("raise", "open_raise"):
            with self.subTest(mode=mode):
                self.driver, self.hub = Driver(), None
                self.hub = module.ReadOnlyHub(self.driver)
                self.driver.click_mode = mode
                with self.assertRaisesRegex(RuntimeError, "uncertain") as caught:
                    self.hub.open_listing_editor(REF, timeout=.05, poll_interval=.01)
                self.assertNotIn("SYNTHETIC_PRIVATE", str(caught.exception))
                self.assertEqual(self.hub.editor_open_state(REF), "pending")
                if mode == "raise": self.driver.add_target()
                self.driver.identity = deepcopy(MATCH)
                self.assertTrue(self.hub.recheck_opened_editor(REF)["row_link_verified"])
                self.assertEqual(self.driver.link.clicks, 1)

    def test_intent_exists_before_even_an_uncertain_click(self):
        def action():
            self.assertEqual(self.hub.editor_open_state(REF), "pending")
            self.assertIs(self.hub._pending_editor.driver, self.driver)
            self.assertEqual(self.hub._pending_editor.source_handles, frozenset({"source"}))
            raise RuntimeError("SYNTHETIC_PRIVATE_CLICK_ERROR")
        self.driver.link.action = action
        with self.assertRaisesRegex(RuntimeError, "uncertain"): self.hub.open_listing_editor(REF)
        self.assertEqual(self.driver.link.clicks, 1)

    def test_blank_target_is_pinned_and_can_load_the_exact_route(self):
        self.driver.link.action = lambda: self.driver.add_target(url="about:blank")
        self.timeout()
        self.assertEqual(self.hub._pending_editor.target_handle, "target")
        self.assertTrue(self.hub.recheck_opened_editor(REF)["editor_pending"])
        self.driver.urls["target"] = URL
        self.driver.identity = deepcopy(MATCH)
        self.assertTrue(self.hub.recheck_opened_editor(REF)["row_link_verified"])

    def test_visible_conflict_on_blank_target_is_not_a_loading_exception(self):
        self.driver.link.action = lambda: self.driver.add_target(url="about:blank")
        self.timeout()
        self.driver.identity = {**MATCH, "outer_matches": False}
        self.assert_blocked()
        self.driver.urls["target"] = URL
        self.assert_blocked()

    def test_wrong_duplicate_and_malformed_identity_is_sticky(self):
        cases = [{**MATCH, "outer_matches": False}, {**MATCH, "inner_matches": False},
                 {**MATCH, "outer_count": 2}, {**MATCH, "inner_count": 2},
                 {**MATCH, "inner_count": 0}, {**MATCH, "outer_count": True}, {}, None]
        for identity in cases:
            with self.subTest(identity=identity):
                self.driver = Driver()
                self.hub = module.ReadOnlyHub(self.driver)
                self.timeout()
                self.driver.identity = identity
                self.assert_blocked()

    def test_initial_open_never_waits_out_a_visible_conflict(self):
        self.driver.identity = {**MATCH, "outer_matches": False}
        with self.assertRaisesRegex(RuntimeError, "cannot be safely matched"):
            self.hub.open_listing_editor(REF, timeout=.05, poll_interval=.01)
        self.assert_blocked()

    def test_wrong_or_changed_routes_are_sticky(self):
        for url in (URL + "/media", module.MANAGE_URL + "/edit/456", URL + "?x=1", "https://other.invalid/", "about:blank"):
            with self.subTest(url=url):
                self.driver = Driver()
                self.hub = module.ReadOnlyHub(self.driver)
                self.timeout()
                self.driver.urls["target"] = url
                self.assert_blocked()
                self.driver.urls["target"] = URL
                self.assert_blocked()

    def test_multiple_new_tabs_are_never_guessed(self):
        self.timeout()
        self.driver.add_target("extra")
        self.assert_blocked()
        self.driver.window_handles.remove("extra")
        self.assert_blocked()

    def test_pinned_target_cannot_be_replaced_with_same_url_and_headings(self):
        self.timeout()
        self.driver.window_handles.remove("target")
        self.driver.add_target("replacement")
        self.driver.current_window_handle = "replacement"
        self.driver.identity = deepcopy(MATCH)
        self.assert_blocked()

    def test_closing_pinned_target_is_not_a_return_to_no_target_pending(self):
        self.timeout()
        self.driver.window_handles.remove("target")
        self.driver.current_window_handle = "source"
        self.assert_blocked()

    def test_missing_original_window_blocks_even_if_editor_matches(self):
        self.timeout()
        self.driver.window_handles.remove("source")
        self.driver.identity = deepcopy(MATCH)
        self.assert_blocked()

    def test_other_original_window_disappearing_is_also_a_conflict(self):
        self.driver.window_handles.append("unrelated")
        self.driver.urls["unrelated"] = "https://example.invalid/"
        self.timeout()
        self.driver.window_handles.remove("unrelated")
        self.assert_blocked()

    def test_changed_driver_or_missing_session_never_recovers_by_putting_it_back(self):
        for replacement in (Driver(), None):
            with self.subTest(replacement=bool(replacement)):
                self.driver = Driver()
                self.hub = module.ReadOnlyHub(self.driver)
                self.timeout()
                self.hub.driver = replacement
                self.assertEqual(self.hub.editor_open_state(REF), "blocked")
                self.hub.driver = self.driver
                self.assert_blocked()

    def test_other_selected_reference_does_not_consume_or_use_intent(self):
        self.timeout()
        before = len(self.driver.scripts)
        self.assertEqual(self.hub.editor_open_state("RXCU99999"), "other")
        with self.assertRaises(RuntimeError): self.hub.recheck_opened_editor("RXCU99999")
        self.assertEqual(len(self.driver.scripts), before)
        self.assertEqual(self.hub.editor_open_state(REF), "pending")
        self.driver.identity = deepcopy(MATCH)
        self.assertTrue(self.hub.recheck_opened_editor(REF)["row_link_verified"])

    def test_source_route_document_row_and_visible_reference_changes_block(self):
        mutations = [lambda d: d.urls.update(source=module.MANAGE_URL + "?search=changed"),
                     lambda d: setattr(d, "root", object()),
                     lambda d: setattr(d, "row", Element()),
                     lambda d: setattr(d, "source_valid", False),
                     lambda d: setattr(d.paragraph, "text", "Ref: RXCU-99999")]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                self.driver = Driver()
                self.hub = module.ReadOnlyHub(self.driver)
                self.timeout()
                mutate(self.driver)
                self.assert_blocked()

    def test_same_tab_navigation_never_becomes_additional_tab_evidence(self):
        self.driver.link.action = lambda: self.driver.urls.update(source=URL)
        with self.assertRaisesRegex(RuntimeError, "cannot be safely matched"):
            self.hub.open_listing_editor(REF, timeout=.05, poll_interval=.01)
        self.assert_blocked()

    def test_changes_during_identity_read_are_rejected(self):
        mutations = [lambda d: d.urls.update(target=URL + "/media"),
                     lambda d: setattr(d, "current_window_handle", "source"),
                     lambda d: d.add_target("extra"),
                     lambda d: setattr(d, "source_valid", False)]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                self.driver = Driver()
                self.hub = module.ReadOnlyHub(self.driver)
                self.timeout()
                self.driver.identity = deepcopy(MATCH)
                self.driver.after_identity = lambda: mutate(self.driver)
                self.assert_blocked()

    def test_browser_read_failure_is_sanitized_and_blocks_unsafe_recovery(self):
        self.timeout()
        def failure(): raise RuntimeError("SYNTHETIC_PRIVATE_SOURCE_CONTENT")
        self.driver.after_source = failure
        self.assert_blocked()

    def test_another_open_or_search_is_blocked_before_any_ui_interaction(self):
        self.timeout()
        before = (len(self.driver.scripts), self.driver.combo.clicks)
        for reference in (REF, "RXCU99999"):
            with self.assertRaises(RuntimeError): self.hub.open_listing_editor(reference)
            with self.assertRaises(RuntimeError): self.hub.search_reference(reference)
        self.assertEqual(before, (len(self.driver.scripts), self.driver.combo.clicks))
        self.assertEqual(self.driver.link.clicks, 1)

    def test_previous_same_reference_binding_is_not_used_after_uncertain_open(self):
        self.hub.editor_bindings[REF] = {"listing_id": "old", "handle": "old"}
        self.timeout()
        self.assertNotIn(REF, self.hub.editor_bindings)

    def test_unsuccessful_close_preserves_intent_and_successful_close_clears(self):
        self.timeout()
        intent = self.hub._pending_editor
        self.driver.quit_fails = True
        with self.assertRaisesRegex(RuntimeError, "could not be confirmed closed"): self.hub.close()
        self.assertIs(self.hub._pending_editor, intent)
        self.assertIs(self.hub.driver, self.driver)
        self.driver.quit_fails = False
        self.hub.close()
        self.assertIsNone(self.hub._pending_editor)
        self.assertIsNone(self.hub.driver)
        self.assertEqual(self.hub.editor_open_state(REF), "none")
        self.assertEqual(self.driver.quit_calls, 2)

    def test_closing_replaced_driver_cannot_clear_original_intent(self):
        self.timeout()
        intent = self.hub._pending_editor
        replacement = Driver()
        self.hub.driver = replacement
        with self.assertRaisesRegex(RuntimeError, "original Hub browser"): self.hub.close()
        self.assertIs(self.hub._pending_editor, intent)
        self.assertEqual(replacement.quit_calls, 0)
        self.assertEqual(self.hub.editor_open_state(REF), "blocked")

    def test_no_intent_never_adopts_a_manually_opened_editor(self):
        self.driver.add_target()
        self.driver.identity = deepcopy(MATCH)
        with self.assertRaises(RuntimeError): self.hub.recheck_opened_editor(REF)
        self.assertEqual(self.driver.scripts, [])
        self.assertEqual(self.driver.link.clicks, 0)
        self.assertEqual(self.hub.editor_bindings, {})

    def test_second_recheck_after_success_does_not_rebind_or_read(self):
        self.timeout()
        self.driver.identity = deepcopy(MATCH)
        self.hub.recheck_opened_editor(REF)
        before = len(self.driver.scripts)
        with self.assertRaises(RuntimeError): self.hub.recheck_opened_editor(REF)
        self.assertEqual(len(self.driver.scripts), before)


if __name__ == "__main__":
    unittest.main()
