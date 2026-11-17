"""Stdlib-only contract tests for additive, partial table observations."""
from copy import deepcopy
import json
import unittest

from quick_relist.capture import (
    CaptureReadError, PartialHubCapture, READ_PAGE_SCRIPT, SCHEMA_VERSION,
    verify_capture_integrity,
)

BASE = "https://hub.remax.co.za/app/listings/edit/synthetic-table"


def cell(text, **changes):
    value = {
        "dom_order": 0, "tag": "td", "role": None, "id": None, "text": text,
        "rowspan_attribute": None, "rowspan_property": 1,
        "colspan_attribute": None, "colspan_property": 1,
        "scope_attribute": None, "scope_property": "",
        "headers_attribute": None, "headers_property": "", "header_ids": [],
        "header_refs": [], "actions": [], "chips": [],
    }
    value.update(changes)
    return value


def table():
    rows = [
        {"dom_order": 0, "tag": "tr", "role": None, "row_group_tag": "thead",
         "cells": [cell("Agent", tag="th", id="agent", scope_attribute="col", scope_property="col")]},
        {"dom_order": 1, "tag": "tr", "role": None, "row_group_tag": "tbody",
         "cells": [cell("Synthetic agent", headers_attribute="agent", headers_property="agent",
                        header_ids=["agent"], header_refs=[{"id": "agent", "status": "observed_header",
                        "row_dom_order": 0, "cell_dom_order": 0}])]},
    ]
    return {
        "dom_order": 0, "section": "Synthetic commission", "rows": [["Agent"], ["Synthetic agent"]],
        "structure": {"schema_version": "table-dom/v1", "tag": "table", "role": None,
                      "captions": [], "status": "observed_native", "mapping_verified": False,
                      "complete": False, "rows": rows, "limitations": ["table_semantics_unverified"]},
    }


class Driver:
    current_url = BASE
    current_window_handle = "synthetic"
    window_handles = ["synthetic"]

    def __init__(self, tables):
        self.raw = {"href": BASE, "ready_state": "complete", "tables": tables}
        self.raw.update({key: [] for key in ("fields", "rich_text", "dialogs", "sections", "lists",
                                            "media", "editor_links", "discovery", "limitations")})
        self.calls = 0

    def execute_script(self, script):
        assert script == READ_PAGE_SCRIPT
        self.calls += 1
        return deepcopy(self.raw)


class TableCaptureTests(unittest.TestCase):
    def test_legacy_table_payload_remains_accepted_without_structure(self):
        old = {"dom_order": 0, "section": "Old synthetic table", "rows": [["Header"], ["Value"]]}
        result = PartialHubCapture(Driver([old])).capture_current_page()
        self.assertEqual(result["schema_version"], SCHEMA_VERSION)
        self.assertEqual(result["pages"][0]["tables"], [old])
        self.assertTrue(verify_capture_integrity(result))
        self.assertFalse(result["complete"])
        self.assertFalse(result["live_ready"])

    def test_structure_survives_with_integrity_and_detached_results(self):
        source = table()
        driver = Driver([source])
        capture = PartialHubCapture(driver)
        result = capture.capture_current_page()
        self.assertEqual(result["pages"][0]["tables"], [source])
        self.assertTrue(verify_capture_integrity(result))
        result["pages"][0]["tables"][0]["structure"]["rows"][1]["cells"][0]["rowspan_property"] = 2
        self.assertFalse(verify_capture_integrity(result))
        self.assertEqual(capture.get_result()["pages"][0]["tables"], [source])
        self.assertEqual(driver.calls, 1)

    def test_strings_in_new_records_receive_existing_url_sanitization(self):
        source = table()
        data = source["structure"]["rows"][1]["cells"][0]
        url = "https://example.invalid/view?token=SYNTHETIC_PRIVATE_QUERY#fragment"
        data["text"] = url
        source["rows"][1][0] = url
        data["chips"] = [{"text": url}]
        data["actions"] = [{"tag": "button", "role": None, "label": url,
                            "disabled": False, "aria_disabled": None}]
        result = PartialHubCapture(Driver([source])).capture_current_page()
        self.assertNotIn("SYNTHETIC_PRIVATE_QUERY", json.dumps(result))
        self.assertEqual(result["pages"][0]["tables"][0]["rows"][1], ["https://example.invalid/view"])
        self.assertTrue(verify_capture_integrity(result))

    def test_malformed_structure_rejected_without_retaining_an_observation(self):
        modifications = [
            lambda t: t.update(structure=[]),
            lambda t: t["structure"].update(schema_version="unknown"),
            lambda t: t["structure"].update(status=[]),
            lambda t: t["structure"].update(complete=True),
            lambda t: t["structure"].update(mapping_verified=True),
            lambda t: t["structure"].update(rows={}),
            lambda t: t["structure"]["rows"][0].update(dom_order=True),
            lambda t: t["structure"]["rows"][0].update(row_group_tag=[]),
            lambda t: t["structure"]["rows"][0].update(cells=[42]),
            lambda t: t["structure"]["rows"][0]["cells"][0].update(rowspan_property=-1),
            lambda t: t["structure"]["rows"][0]["cells"][0].update(colspan_property=True),
            lambda t: t["structure"]["rows"][0]["cells"][0].update(actions=[{"label": []}]),
            lambda t: t["structure"]["rows"][0]["cells"][0].update(chips=[{"text": 42}]),
            lambda t: t["structure"]["rows"][1]["cells"][0]["header_refs"][0].update(row_dom_order=999),
            lambda t: t["rows"].append(["SYNTHETIC_PRIVATE_MALFORMED"]),
        ]
        for change in modifications:
            with self.subTest(change=change):
                source = table()
                change(source)
                capture = PartialHubCapture(Driver([source]))
                with self.assertRaises(CaptureReadError) as caught:
                    capture.capture_current_page()
                self.assertNotIn("SYNTHETIC_PRIVATE", str(caught.exception))
                self.assertEqual(capture.get_result()["pages"], [])

    def test_script_remains_read_only_and_uses_no_external_state(self):
        for forbidden in (".click(", ".submit(", ".dispatchEvent(", ".setAttribute(",
                          "localStorage", "sessionStorage", "document.cookie", "fetch(",
                          "XMLHttpRequest", "window.open(", "innerHTML ="):
            self.assertNotIn(forbidden, READ_PAGE_SCRIPT)


if __name__ == "__main__":
    unittest.main()
