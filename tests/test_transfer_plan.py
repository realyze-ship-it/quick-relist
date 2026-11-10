"""Stdlib-only saved-capture to typed-plan tests. No browser or live data."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from quick_relist.capture import _digest
from quick_relist.date_capture import DATE_NAMES
from quick_relist.hub_form_writer import TEXT_FIELDS, COUNT_IDS, COMBO_NAMES, CHECKBOX_NAMES, _validate_value
from quick_relist.source_fields import extract_source_fields
from quick_relist.transfer_plan import build_transfer_plan, _canonical
from segmented_date_fixtures import capture, date_record


def field(name, value="000.00", **kwargs):
    return dict({"name": name, "id": name, "label": name, "tag": "input", "type": "text",
                 "value": value, "disabled": False, "read_only": False}, **kwargs)


def seal(c):
    for p in c["pages"]:
        p.pop("integrity", None)
        p["integrity"] = {"algorithm": "sha256", "sha256": _digest(p)}
    c.pop("integrity", None)
    c["integrity"] = {"algorithm": "sha256", "sha256": _digest(c)}
    return c


def full_capture(replacement=False):
    fs = [field(n) for n in TEXT_FIELDS["details"]]
    fs += [field("", "00", id=n) for n in COUNT_IDS]
    fs += [field(n, "on", type="checkbox", checked=False) for n in CHECKBOX_NAMES]
    fs += [field("sale_state_id", str(i), type="radio", checked=i == 1, label="State " + str(i), id="radio-" + str(i)) for i in range(5)]
    fs += [field(n, "Option", tag="div", type="custom-combobox", observed_business_selection_code=True,
                 selected_options=[{"value": "ZA" if n == "country_id" else "007", "text": "Option"}]) for n in COMBO_NAMES]
    fs += [date_record(i) for i in range(5)]
    c = capture(fs, replacement=replacement)
    for number, page in enumerate(("deal_particulars", "media"), 2):
        p = deepcopy(c["pages"][0])
        p.update(page_key=page, page_path=p["page_path"] + "/" + page, observation_number=number,
                 observed_at=f"2026-01-02T03:04:0{number}+00:00", fields=[field(n) for n in TEXT_FIELDS[page]])
        if page == "media":
            p["fields"][-1] = field("marketing_header", "Line 1\n  Line 2\r\n", tag="textarea", type="textarea", label="Marketing header description")
            p["fields"].append(field("", "  Brochure\n\nEnd  ", tag="textarea", type="textarea", id="outlined-multiline-flexible", label="Brochure Description"))
        c["pages"].append(p)
    return seal(c)


def pins(a, b):
    return dict(expected_source_listing_id="123456", expected_source_rxcu="RXCU12345",
                expected_source_capture_sha256=a["integrity"]["sha256"], expected_target_listing_id="234567",
                expected_target_rxcu="RXCU23456", expected_target_capture_sha256=b["integrity"]["sha256"])


def plan(a=None, b=None, **changes):
    a = full_capture() if a is None else a
    b = full_capture(True) if b is None else b
    return build_transfer_plan(a, b, **(pins(a, b) | changes))


def rows(p):
    return {r["key"]: r for group in p["groups"].values() for r in group}


class TransferPlanTests(unittest.TestCase):
    def test_all_supported_handlers_from_saved_capture_json(self):
        a, b = (json.loads(json.dumps(full_capture(x))) for x in (False, True))
        p = plan(a, b)
        self.assertEqual(p["status"], "partial_read_only")
        rs = rows(p)
        inventory = extract_source_fields(a, expected_listing_id="123456", expected_rxcu="RXCU12345")
        for entry in inventory["entries"]:
            self.assertEqual(rs[entry["key"]]["source"], entry)
        for row in rs.values():
            if row["writer_key"]:
                self.assertIsNotNone(row["candidate"], row["key"])
                self.assertEqual(row["comparison"], "matched")
                _validate_value(row["writer_key"], row["candidate"], row["page"])
                _validate_value(row["writer_key"], row["baseline"], row["page"])
        self.assertEqual({r["writer_kind"] for r in rs.values()}, {None, "text", "textarea", "count", "radio", "checkbox", "combo", "date"})
        self.assertEqual(rs["feature_bedroom"]["writer_key"], "feature-bedroom-input")
        self.assertEqual(rs["feature_bedroom"]["candidate"], "00")
        self.assertEqual(rs["property_type_id"]["candidate"], {"code": "007", "text": "Option"})
        self.assertEqual(rs["selling_price"]["candidate"], "000.00")
        self.assertEqual(rs["brochure_description"]["candidate"], "  Brochure\n\nEnd  ")
        self.assertEqual(rs["marketing_header"]["candidate"], "Line 1\n  Line 2\r\n")

    def test_no_readiness_account_revisions_or_overrides(self):
        p = plan()
        for key in ("complete", "live_ready", "persistence_verified", "account_verified"):
            self.assertIs(p[key], False)
        self.assertIsNone(p["source_revision"])
        self.assertIsNone(p["target_revision"])
        self.assertEqual(p["approved_overrides"], [])
        self.assertIn("creation_required_fields_unqualified", p["blockers"])
        self.assertIn("source_and_target_server_revisions_unknown", p["blockers"])
        with self.assertRaises(TypeError):
            plan(expected_source_revision="invented")

    def test_dates_and_address_raw_candidate_never_semantically_qualified(self):
        rs = rows(plan())
        for key in DATE_NAMES:
            row = rs[key]
            self.assertEqual(row["candidate"]["parts"], row["source"]["date_parts"])
            self.assertEqual(row["candidate"]["raw"], row["source"]["value"])
            self.assertEqual(row["status"], "blocked")
            self.assertTrue(any("semantics" in b for b in row["blockers"]))
        self.assertEqual(rs["address_hidden"]["candidate"], {"checked": False, "control_value": "on"})
        self.assertEqual(rs["address_hidden"]["status"], "blocked")

    def test_changed_baseline_exact_and_no_coercion(self):
        a, b = full_capture(), full_capture(True)
        b["pages"][0]["fields"][0]["value"] = "0"
        seal(b)
        row = rows(plan(a, b))["land_area"]
        self.assertEqual(row["candidate"], "000.00")
        self.assertEqual(row["baseline"], "0")
        self.assertEqual(row["comparison"], "changed")
        self.assertNotEqual(_canonical(False), _canonical(0))
        self.assertNotEqual(_canonical(True), _canonical(1))
        self.assertNotEqual(_canonical("00"), _canonical(0))
        for bad in (0, False, 1.0, None):
            b["pages"][0]["fields"][0]["value"] = bad
            row = rows(plan(a, seal(b)))["land_area"]
            self.assertIsNone(row["baseline"])
            self.assertEqual(row["status"], "blocked")

    def test_boolean_checkbox_is_not_integer(self):
        for bad in (0, 1, "false"):
            b = full_capture(True)
            next(f for f in b["pages"][0]["fields"] if f["name"] == "poa")["checked"] = bad
            self.assertIsNone(rows(plan(b=seal(b)))["poa"]["baseline"])

    def test_missing_duplicate_and_type_mismatch(self):
        b = full_capture(True)
        b["pages"][0]["fields"].pop(0)
        self.assertIn("target_value_unresolved", rows(plan(b=seal(b)))["land_area"]["blockers"])
        b = full_capture(True)
        b["pages"][0]["fields"].append(deepcopy(b["pages"][0]["fields"][0]))
        row = rows(plan(b=seal(b)))["land_area"]
        self.assertEqual(row["target"]["status"], "ambiguous")
        self.assertIsNone(row["baseline"])
        b = full_capture(True)
        b["pages"][0]["fields"][0]["type"] = "number"
        self.assertIn("source_target_control_types_differ", rows(plan(b=seal(b)))["land_area"]["blockers"])

    def test_selections_require_code_and_label_and_target_options(self):
        for change in ({"value": "008"}, {"text": "Other"}):
            b = full_capture(True)
            f = next(f for f in b["pages"][0]["fields"] if f["name"] == "property_type_id")
            f["selected_options"][0].update(change)
            f["value"] = f["selected_options"][0]["text"]
            r = rows(plan(b=seal(b)))["property_type_id"]
            self.assertIn("selection_code_label_conflict", r["blockers"])
            self.assertIn("target_option_availability_unverified", r["blockers"])
        for alteration in ("duplicate", "provenance", "value"):
            b = full_capture(True)
            f = next(f for f in b["pages"][0]["fields"] if f["name"] == "property_type_id")
            if alteration == "duplicate":
                f["selected_options"] *= 2
            elif alteration == "provenance":
                f["observed_business_selection_code"] = False
            else:
                f["value"] = "wrong"
            self.assertIsNone(rows(plan(b=seal(b)))["property_type_id"]["baseline"])

    def test_radio_options_and_checkbox_control_value_differ(self):
        b = full_capture(True)
        next(f for f in b["pages"][0]["fields"] if f["name"] == "sale_state_id")["label"] = "Other option"
        next(f for f in b["pages"][0]["fields"] if f["name"] == "poa")["value"] = "off"
        rs = rows(plan(b=seal(b)))
        self.assertIn("radio_options_differ", rs["sale_state_id"]["blockers"])
        self.assertIn("checkbox_control_values_differ", rs["poa"]["blockers"])

    def test_unknown_nested_and_unqualified_fields_remain_visible(self):
        a = full_capture()
        a["pages"][0]["fields"].extend([field("unknown", "retain"), field("", "Place", label="Suburb")])
        a["pages"][0]["dialogs"] = [{"fields": [field("description", "room")], "rich_text": [],
            "room_groups": [{"title": "Bedroom", "index": 1, "fields": [field("has_blinds", "yes")], "rich_text": []}]}]
        a["pages"][2]["rich_text"] = [{"label": "rdw-editor", "text": "Rich", "sanitized_html": "<b>Rich</b>"}]
        p = plan(seal(a))
        self.assertEqual(p["inventories"]["source"]["unknowns"][0]["field"]["value"], "retain")
        self.assertEqual(p["inventories"]["source"]["related_records"][0]["record"]["room_groups"][0]["fields"][0]["value"], "yes")
        for key in ("suburb", "rich_text_description"):
            self.assertIsNone(rows(p)[key]["writer_key"])
            self.assertIn("writer_handler_unqualified", rows(p)[key]["blockers"])

    def test_sanitized_truncated_limitations_block(self):
        for page_limit in (True, False):
            a = full_capture()
            owner = a["pages"][0] if page_limit else a
            owner["limitations"] = ["url_credentials_and_queries_omitted", "text_truncated"]
            r = rows(plan(seal(a)))["street_name"]
            self.assertEqual(r["status"], "blocked")
            self.assertTrue(any("limitations" in b for b in r["blockers"]))

    def test_rejected_inputs_and_stale_substitution_have_no_candidates(self):
        a, b = full_capture(), full_capture(True)
        kw = pins(a, b)
        for bad in (None, [], {}, {"complete": True}):
            r = build_transfer_plan(bad, b, **kw)
            self.assertEqual(r["status"], "rejected")
            self.assertFalse(any(r["groups"].values()))
        self.assertEqual(build_transfer_plan(b, a, **kw)["status"], "rejected")
        a["pages"][0]["fields"][0]["value"] = "stale alternate"
        self.assertEqual(build_transfer_plan(seal(a), b, **kw)["issues"][0]["code"], "capture_pin_mismatch")
        for changes in ({"expected_source_rxcu": None}, {"expected_source_capture_sha256": ""},
                        {"expected_source_listing_id": 123456}, {"expected_target_listing_id": "123456"},
                        {"expected_target_rxcu": "RXCU-12345"}, {"expected_source_rxcu": "RXCU99999"}):
            self.assertEqual(plan(**changes)["status"], "rejected")

    def test_wrong_page_identity_and_malformed_date_rejected(self):
        a = full_capture()
        a["pages"][0]["matched_source"]["listing_id"] = "234567"
        self.assertEqual(plan(seal(a))["status"], "rejected")
        a = full_capture()
        f = next(f for f in a["pages"][0]["fields"] if f["name"] == DATE_NAMES[0])
        f["date_picker"]["segments"][0]["dom_order"] = False
        self.assertIsNone(rows(plan(seal(a)))[DATE_NAMES[0]]["candidate"])

    def test_conflicting_identifiers_block_both_sides(self):
        for side in ("source", "target"):
            for name, identifier in (("street_name", "selling_price"), ("unrelated", "street_name")):
                a, b = full_capture(), full_capture(True)
                c = a if side == "source" else b
                f = next(f for f in c["pages"][0]["fields"] if f["name"] == "street_name")
                f.update(name=name, id=identifier)
                seal(c)
                r = rows(plan(a, b))["street_name"]
                self.assertEqual(r["status"], "blocked")
                self.assertIn(side + "_writer_control_identifier_conflict", r["blockers"])
                if identifier == "selling_price":
                    self.assertEqual(rows(plan(a, b))["selling_price"]["status"], "blocked")

    def test_correct_text_name_allows_dynamic_id(self):
        a, b = full_capture(), full_capture(True)
        for c in (a, b):
            next(f for f in c["pages"][0]["fields"] if f["name"] == "street_name")["id"] = ":dynamic-r19:"
            seal(c)
        self.assertEqual(rows(plan(a, b))["street_name"]["status"], "candidate_only")

    def test_marketing_header_requires_exact_name_and_id(self):
        for side in ("source", "target"):
            for change in ({"id": ":dynamic-r19:"}, {"name": ""}, {"name": "unrelated"}):
                a, b = full_capture(), full_capture(True)
                c = a if side == "source" else b
                f = next(f for f in c["pages"][2]["fields"] if f["name"] == "marketing_header")
                f.update(change)
                r = rows(plan(seal(a), seal(b)))["marketing_header"]
                self.assertEqual(r["status"], "blocked")
                if "id" in change:
                    self.assertIn(side + "_writer_control_identifier_conflict", r["blockers"])
                else:
                    self.assertIsNone(r["candidate" if side == "source" else "baseline"])

    def test_empty_count_name_does_not_prove_absent_attribute(self):
        r = rows(plan())["feature_bedroom"]
        self.assertEqual(r["candidate"], "00")
        self.assertEqual(r["status"], "blocked")
        for side in ("source", "target"):
            self.assertIn(side + "_count_name_attribute_absence_unverified", r["blockers"])

    def test_source_readability_is_separate_from_target_editability(self):
        a, b = full_capture(), full_capture(True)
        a["pages"][0]["fields"][0].update(disabled=True, read_only=True)
        r = rows(plan(seal(a), b))["land_area"]
        self.assertEqual(r["status"], "candidate_only")
        self.assertEqual(r["candidate"], "000.00")
        self.assertIn({"side": "source", "code": "source_control_editability_unverified"}, r["issues"])
        b["pages"][0]["fields"][0]["disabled"] = True
        r = rows(plan(a, seal(b)))["land_area"]
        self.assertEqual(r["comparison"], "matched")
        self.assertIn("target_control_editability_unverified", r["blockers"])

    def test_size_limits_and_non_json_inputs_fail_closed(self):
        for constant in ("MAX_CAPTURE_BYTES", "MAX_PLAN_BYTES"):
            with patch("quick_relist.transfer_plan." + constant, 1):
                p = plan()
                self.assertEqual(p["status"], "rejected")
                self.assertFalse(any(p["groups"].values()))
                self.assertEqual(p["inventories"], {})
        a, b = full_capture(), full_capture(True)
        kw = pins(a, b)
        a["unknown"] = float("nan")
        self.assertEqual(build_transfer_plan(a, b, **kw)["issues"], ["capture_malformed"])

    def test_detached_result_does_not_mutate_inputs_or_other_rows(self):
        a, b = full_capture(), full_capture(True)
        before = deepcopy((a, b))
        p = plan(a, b)
        self.assertEqual((a, b), before)
        self.assertEqual(json.loads(json.dumps(p)), p)
        rows(p)["street_name"]["source"]["observations"][0]["field"]["value"] = "changed output"
        self.assertEqual((a, b), before)
        self.assertEqual(rows(plan(a, b))["street_name"]["candidate"], "000.00")


if __name__ == "__main__":
    unittest.main()
