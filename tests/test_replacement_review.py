"""Synthetic local comparison tests. Run with unittest; no Qt/browser required."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from quick_relist.capture import SCHEMA_VERSION as CAPTURE_SCHEMA, _digest, verify_capture_integrity
from quick_relist.replacement_review import compare_replacement_fields


def seal(capture):
    def receipt(value):
        value.pop("integrity", None)
        value["integrity"] = {"algorithm": "sha256", "sha256": hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False).encode()).hexdigest()}
    for page in capture["pages"]:
        receipt(page)
    receipt(capture)
    return capture


def field(name="street_name", value="Synthetic street", **updates):
    result = {"name": name, "id": name, "label": name, "tag": "input", "type": "text",
              "value": value, "checked": None, "selected_options": None, "dom_order": 0,
              "disabled": False, "read_only": False, "section": "Synthetic section"}
    result.update(updates)
    return result


def checkbox(name="poa", checked=False, **updates):
    return field(name, "on", type="checkbox", checked=checked, **updates)


def selection(code="007", text="Synthetic label"):
    return field("property_type_id", text, tag="div", type="custom-combobox",
                 selected_options=[{"value": code, "text": text}], observed_business_selection_code=True)


def page(fields=None, key="details", number=1, **updates):
    result = {"page_key": key, "observation_number": number, "complete": False,
              "observed_at": f"2026-01-02T03:04:{number:02}+00:00",
              "fields": [field()] if fields is None else fields, "rich_text": [], "dialogs": [],
              "limitations": [], "sections": [], "lists": [], "tables": [], "media": [],
              "editor_links": [], "discovery": []}
    result.update(updates)
    return result


def capture(*pages, replacement=False, listing_id=None, rxcu=None):
    listing_id = listing_id or ("222222" if replacement else "111111")
    rxcu = rxcu or ("RXCU20002" if replacement else "RXCU10001")
    pages = deepcopy(list(pages) or [page()])
    for sample in pages:
        suffix = "" if sample["page_key"] == "details" else "/" + sample["page_key"]
        sample["page_path"] = f"/app/listings/edit/{listing_id}" + suffix
        sample["matched_source"] = {"listing_id": listing_id, "rxcu": rxcu,
            "reference_checked_before_and_after": True, "pinned_tab_checked": True, "dom_stability_seconds": 1.0}
    return seal({"schema_version": CAPTURE_SCHEMA, "capture_kind": "matched_partial_visible_dom",
        "complete": False, "live_ready": False, "listing_id": listing_id, "status": "partial",
        "identity": {"route_verified": True, "reference_verified": True, "account_verified": False},
        "matched_source": {"listing_id": listing_id, "rxcu": rxcu, "row_link_binding_used": True,
            "all_accepted_observations_guarded": True, "fidelity_verified": False, "account_verified": False},
        "limitations": [], "pages": pages})


def review(original=None, replacement=None, **updates):
    options = {"expected_original_rxcu": "RXCU10001", "expected_replacement_rxcu": "RXCU20002"}
    options.update(updates)
    return compare_replacement_fields(original if original is not None else capture(),
        replacement if replacement is not None else capture(replacement=True), **options)


def rows(result):
    return {entry["key"]: entry for entry in result["entries"]}


_ROOM_LABELS = {"bedroom": ("Set bedrooms options", "Bedroom"),
                "bathroom": ("Set bathrooms options", "Bathroom"),
                "kitchen": ("Set kitchens options", "Kitchen"),
                "parking": ("Set parkings options", "Parking"),
                "lounge": ("Set lounges options", "Lounge")}


def add_rooms(saved, category="bedroom", groups=None):
    title, noun = _ROOM_LABELS[category]
    groups = groups or [[checkbox("has_carpets"), field("description", "Synthetic room",
                                                    tag="textarea", type="textarea")]]
    sample = saved["pages"][0]
    sample["fields"].append(field("", str(len(groups)), id=f"feature-{category}-input"))
    sample["dialogs"].append({"title": title, "fields": [], "rich_text": [],
        "room_groups": [{"index": index, "title": f"{noun} {index}", "fields": deepcopy(controls),
                         "rich_text": [], "mapping_verified": False} for index, controls in enumerate(groups, 1)],
        "source": {"listing_id": saved["listing_id"], "rxcu": saved["matched_source"]["rxcu"],
                   "page_key": "details", "opener": title, "title": title, "feature_count": len(groups),
                   "reference_checked_before_and_after": True, "pinned_tab_checked": True,
                   "main_fields_unchanged_verified": True, "close_verified": True,
                   "observed_at": "2026-01-02T03:04:10+00:00", "dom_stability_seconds": 0.5,
                   "limitations": [], "complete": False, "server_writes_verified": False}})
    # All synthetic dialogs bind to the same final main fields, as real capture
    # requires. Adding a category must not invalidate the earlier fixture.
    for dialog in sample["dialogs"]:
        dialog["source"]["main_fields_sha256"] = _digest({key: sample[key] for key in ("fields", "rich_text")})
    return seal(saved)


def room_rows(result):
    return {(group["scope"]["category"], group["scope"]["index"], entry["key"]): entry
            for group in result["room_groups"] for entry in group["entries"]}


class ReplacementReviewTests(unittest.TestCase):
    def assert_safe_rejection(self, result, code, side=None, detail=None):
        self.assertEqual(result["status"], "rejected")
        self.assertIsNone(result["original"])
        self.assertIsNone(result["replacement"])
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["room_groups"], [])
        self.assertEqual(result["unresolved_coverage"], [])
        self.assertEqual(result["issues"][0]["code"], code)
        if side:
            self.assertEqual(result["issues"][0]["side"], side)
        if detail:
            self.assertIn(detail, result["issues"][0]["details"])
        self.assertNotIn("PRIVATE SYNTHETIC MARKER", json.dumps(result))
        self.assert_flags(result)

    def assert_flags(self, result):
        for key in ("complete", "fidelity_verified", "publish_ready", "live_ready"):
            self.assertIs(result[key], False)

    def test_explicit_expected_references_required_and_no_private_error(self):
        for invalid in (None, "PRIVATE SYNTHETIC MARKER", 123, {}, [], object()):
            with self.subTest(kind=type(invalid).__name__):
                self.assert_safe_rejection(review(expected_original_rxcu=invalid), "expected_references_required")

    def test_reference_expectations_match_both_sides(self):
        for side in ("original", "replacement"):
            with self.subTest(side=side):
                self.assert_safe_rejection(review(**{f"expected_{side}_rxcu": "RXCU99999"}),
                                           "capture_rejected", side, "expected_reference_mismatch")
        self.assertEqual(review(expected_original_rxcu="RXCU-10001")["status"], "partial")

    def test_expected_id_pins_and_malformed_id(self):
        self.assertEqual(review(expected_original_listing_id="111111", expected_replacement_listing_id="222222")["status"], "partial")
        self.assert_safe_rejection(review(expected_original_listing_id="333333"),
                                   "capture_rejected", "original", "expected_listing_mismatch")
        self.assert_safe_rejection(review(expected_replacement_listing_id="PRIVATE SYNTHETIC MARKER"),
                                   "expected_listing_id_invalid", "replacement")

    def test_same_reference_rejected_even_with_two_capture_objects(self):
        self.assert_safe_rejection(review(expected_replacement_rxcu="RXCU-10001"),
                                   "distinct_listing_references_required")

    def test_same_listing_id_rejected_even_with_different_references(self):
        for listing_id in ("111111", "000111111"):
            with self.subTest(listing_id=listing_id):
                self.assert_safe_rejection(review(replacement=capture(replacement=True, listing_id=listing_id)),
                                           "distinct_listing_ids_required")

    def test_two_snapshots_of_original_are_not_a_replacement(self):
        original = capture()
        self.assert_safe_rejection(review(original, deepcopy(original)), "capture_rejected",
                                   "replacement", "expected_reference_mismatch")

    def test_integrity_failure_rejects_entire_pair_without_values(self):
        for side in ("original", "replacement"):
            sample = capture(replacement=side == "replacement")
            sample["pages"][0]["fields"][0]["value"] = "PRIVATE SYNTHETIC MARKER"
            self.assert_safe_rejection(review(**{side: sample}), "capture_rejected", side, "capture_integrity_invalid")

    def test_malformed_capture_returns_static_diagnostics(self):
        for malformed in ([], "PRIVATE SYNTHETIC MARKER", {"pages": None}, {"v": object()}, {"v": float("nan")}):
            with self.subTest(kind=type(malformed).__name__):
                self.assert_safe_rejection(review(original=malformed), "capture_rejected", "original")

    def test_guard_or_identity_failure_not_fixed_by_resealing(self):
        sample = capture()
        sample["pages"][0]["matched_source"]["rxcu"] = "RXCU33333"
        self.assert_safe_rejection(review(seal(sample)), "capture_rejected", "original", "page_identity_conflict")
        sample = capture()
        sample["identity"]["reference_verified"] = False
        self.assert_safe_rejection(review(seal(sample)), "capture_rejected", "original", "capture_guard_missing")

    def test_equal_observations_never_imply_copy_or_publication(self):
        result = review()
        self.assertEqual(rows(result)["street_name"]["status"], "matched")
        self.assertEqual(result["status"], "partial")
        self.assert_flags(result)
        for code in ("equal_observations_not_complete_copy", "incomplete_source_and_replacement",
                     "account_ownership_unverified", "cross_page_revision_unverified", "no_differences_approved"):
            self.assertIn(code, result["limitations"])

    def test_exact_prices_dates_and_strings_not_normalized_or_approved(self):
        for key in ("selling_price", "mandate_signed_date", "street_name"):
            with self.subTest(key=key):
                result = review(capture(page([field(key, " 0001.00 ")])),
                                capture(page([field(key, "1.0")]), replacement=True))
                row = rows(result)[key]
                self.assertEqual(row["status"], "changed")
                self.assertEqual(row["original"]["value"], " 0001.00 ")
                self.assertEqual(row["replacement"]["value"], "1.0")
                self.assertNotIn("approved", row)

    def test_blank_and_false_not_missing(self):
        controls = [field(value=""), checkbox(checked=False)]
        result = review(capture(page(controls)), capture(page(controls), replacement=True))
        for key in ("street_name", "poa"):
            row = rows(result)[key]
            self.assertEqual(row["status"], "matched")
            self.assertEqual(row["missing_in"], [])
        self.assertIs(rows(result)["poa"]["original"]["value"], False)

    def test_missing_sides_visible_and_no_backfill(self):
        sample = capture(page([field("selling_price", "old")]), page([], number=2))
        result = review(sample, capture(page([field("selling_price", "new")]), replacement=True))
        row = rows(result)["selling_price"]
        self.assertEqual(row["status"], "missing")
        self.assertEqual(row["missing_in"], ["original"])
        self.assertNotIn("value", row["original"])
        self.assertEqual(result["original"]["superseded_pages"][0]["path"], "pages/0")
        self.assertEqual(rows(result)["levy"]["missing_in"], ["original", "replacement"])

    def test_selection_code_change_despite_same_label(self):
        result = review(capture(page([selection("007")])), capture(page([selection("008")]), replacement=True))
        row = rows(result)["property_type_id"]
        self.assertEqual(row["status"], "changed")
        self.assertEqual(row["original"]["display_value"], row["replacement"]["display_value"])
        self.assertEqual(row["original"]["selected_code"], "007")
        self.assertEqual(row["replacement"]["selected_code"], "008")

    def test_selection_label_change_and_invalid_pair_not_guessed(self):
        result = review(capture(page([selection()])), capture(page([selection(text="Changed label")]), replacement=True))
        self.assertEqual(rows(result)["property_type_id"]["status"], "changed")
        malformed = selection()
        malformed["value"] = "Conflicting value"
        result = review(capture(page([malformed])), capture(page([selection()]), replacement=True))
        self.assertEqual(rows(result)["property_type_id"]["status"], "unresolved")
        self.assertIsNone(rows(result)["property_type_id"]["observed_comparison"])

    def test_checkbox_raw_polarity_and_control_values(self):
        controls = [checkbox(), checkbox("address_hidden", False, label="Show address on web")]
        changed = [checkbox(checked=True), checkbox("address_hidden", True, label="Show address on web")]
        result = review(capture(page(controls)), capture(page(changed), replacement=True))
        self.assertEqual(rows(result)["poa"]["status"], "changed")
        row = rows(result)["address_hidden"]
        self.assertEqual(row["status"], "unresolved")
        self.assertEqual(row["observed_comparison"], "changed")
        self.assertIs(row["original"]["value"], False)
        self.assertIs(row["replacement"]["value"], True)
        self.assertEqual(row["original"]["control_value"], "on")

    def test_selected_radio_values_and_ambiguity(self):
        radios = [field("sale_state_id", "01", type="radio", checked=True),
                  field("sale_state_id", "02", type="radio", checked=False)]
        changed = deepcopy(radios)
        changed[0]["checked"], changed[1]["checked"] = False, True
        result = review(capture(page(radios)), capture(page(changed), replacement=True))
        row = rows(result)["sale_state_id"]
        self.assertEqual(row["status"], "changed")
        self.assertEqual(row["original"]["value"], "01")
        self.assertEqual(row["replacement"]["value"], "02")
        self.assertEqual(len(row["original"]["observations"]), 2)
        changed[0]["checked"] = True
        row = rows(review(capture(page(radios)), capture(page(changed), replacement=True)))["sale_state_id"]
        self.assertEqual(row["status"], "ambiguous")
        self.assertIsNone(row["observed_comparison"])

    def test_duplicate_and_conflicting_controls_remain_ambiguous(self):
        for controls in ([field(), field()], [field(id="street_number")]):
            with self.subTest(controls=controls):
                row = rows(review(capture(page(controls))))["street_name"]
                self.assertEqual(row["status"], "ambiguous")
                self.assertIsNone(row["observed_comparison"])
                self.assertEqual(len(row["original"]["observations"]), len(controls))

    def test_suburb_and_rich_description_raw_difference_remains_unresolved(self):
        original_rich = {"label": "rdw-editor", "text": "Synthetic text", "sanitized_html": "<p>Synthetic text</p>"}
        replacement_rich = {**original_rich, "sanitized_html": "<p><b>Synthetic text</b></p>"}
        brochure = field("", "One\n\nTwo", label="Brochure Description", tag="textarea", type="textarea")
        original = capture(page([field("", "Synthetic suburb", label="Suburb")]),
                           page([brochure], key="media", number=2, rich_text=[original_rich]))
        replacement = capture(page([field("", "Different suburb", label="Suburb")]),
                              page([brochure], key="media", number=2, rich_text=[replacement_rich]), replacement=True)
        result = review(original, replacement)
        for key in ("suburb", "rich_text_description"):
            self.assertEqual(rows(result)[key]["status"], "unresolved")
            self.assertEqual(rows(result)[key]["observed_comparison"], "changed")
        self.assertEqual(rows(result)["brochure_description"]["status"], "matched")
        self.assertEqual(rows(result)["brochure_description"]["original"]["value"], "One\n\nTwo")

    def test_unknown_controls_are_coverage_not_guessed_matches(self):
        unknown = field("new_unknown_field", "Same synthetic value")
        result = review(capture(page([unknown])), capture(page([unknown]), replacement=True))
        self.assertNotIn("new_unknown_field", rows(result))
        self.assertEqual(len(result["unresolved_coverage"]), 2)
        self.assertTrue(all(item["status"] == "unresolved" for item in result["unresolved_coverage"]))
        self.assertEqual(result["unresolved_coverage"][0]["observation"]["field"], unknown)

    def test_original_control_and_page_provenance_are_preserved(self):
        control = field(extra_metadata={"nested": [False, None, ""]})
        original = capture(page([control], limitations=["dom_text_truncated"]))
        original["limitations"] = ["url_credentials_and_queries_omitted"]
        result = review(seal(original))
        obs = rows(result)["street_name"]["original"]["observations"][0]
        self.assertEqual(obs["field"], control)
        self.assertEqual(obs["path"], "pages/0/fields/0")
        self.assertEqual(obs["page_sha256"], original["pages"][0]["integrity"]["sha256"])
        self.assertEqual(result["original"]["selected_pages"][0]["limitations"], ["dom_text_truncated"])
        self.assertEqual(result["original"]["capture_limitations"], ["url_credentials_and_queries_omitted"])

    def test_chronology_visible_but_not_server_proof(self):
        for timestamp, outcome in (("2026-01-02T03:04:11+00:00", "replacement_observed_after_original"),
                                   ("2026-01-01T03:04:01+00:00", "replacement_observed_before_original"),
                                   ("2026-01-02T03:04:01+00:00", "observation_windows_overlap")):
            result = review(replacement=capture(page(observed_at=timestamp), replacement=True))
            self.assertEqual(result["chronology"]["status"], outcome)
            self.assertIs(result["chronology"]["server_chronology_verified"], False)
        sample = capture(page(number=2), page(number=1))
        self.assert_safe_rejection(review(sample), "capture_rejected", "original", "observation_order_invalid")

    def test_chronology_includes_later_qualified_dialog_observation(self):
        original = add_rooms(capture())
        replacement = capture(page(observed_at="2026-01-02T03:04:05+00:00"), replacement=True)
        self.assertEqual(review(original, replacement)["chronology"]["status"], "observation_windows_overlap")

    def test_rooms_do_not_merge_repeated_ids_or_names(self):
        one = [checkbox("has_carpets", False), field("description", "First\nroom", tag="textarea", type="textarea")]
        two = [checkbox("has_carpets", True), field("description", "Second room", tag="textarea", type="textarea")]
        changed_two = deepcopy(two)
        changed_two[0]["checked"] = False
        original = add_rooms(capture(), groups=[one, two])
        replacement = add_rooms(capture(replacement=True), groups=[one, changed_two])
        result = review(original, replacement)
        mapped = room_rows(result)
        self.assertEqual(mapped[("bedroom", 1, "has_carpets")]["status"], "matched")
        self.assertIs(mapped[("bedroom", 1, "has_carpets")]["original"]["value"], False)
        self.assertEqual(mapped[("bedroom", 2, "has_carpets")]["status"], "changed")
        self.assertEqual(mapped[("bedroom", 1, "description")]["original"]["value"], "First\nroom")
        obs = mapped[("bedroom", 2, "has_carpets")]["original"]["observations"][0]
        self.assertEqual(obs["path"], "pages/0/dialogs/0/room_groups/1/fields/0")
        self.assertEqual(obs["scope"], {"kind": "room", "category": "bedroom", "index": 2})
        self.assertEqual(obs["room_source"], original["pages"][0]["dialogs"][0]["source"])

    def test_room_categories_and_two_open_plan_names_are_distinct(self):
        original, replacement = capture(), capture(replacement=True)
        for category in ("bedroom", "bathroom", "kitchen", "parking", "lounge"):
            controls = [checkbox("has_open_plan", False), checkbox("has_carpets", False)]
            if category == "kitchen":
                controls.append(checkbox("is_open_plan", True))
            add_rooms(original, category, [controls])
            add_rooms(replacement, category, [deepcopy(controls)])
        replacement["pages"][0]["dialogs"][2]["room_groups"][0]["fields"][2]["checked"] = False
        result = review(original, seal(replacement))
        mapped = room_rows(result)
        self.assertEqual(len(result["room_groups"]), 5)
        self.assertEqual(mapped[("kitchen", 1, "is_open_plan")]["status"], "changed")
        for category in _ROOM_LABELS:
            self.assertEqual(mapped[(category, 1, "has_open_plan")]["status"], "matched")

    def test_category_specific_schema_is_supported_but_not_guessed_elsewhere(self):
        examples = {"bedroom": "has_walk_in_closet", "bathroom": "is_main_en_suite",
                    "kitchen": "has_stove_oven_and_hob", "parking": "is_shade_net_covered_parking",
                    "lounge": "has_underfloor_heating"}
        for category, key in examples.items():
            with self.subTest(category=category):
                original = add_rooms(capture(), category, [[checkbox(key)]])
                replacement = add_rooms(capture(replacement=True), category, [[checkbox(key, True)]])
                self.assertEqual(room_rows(review(original, replacement))[(category, 1, key)]["status"], "changed")
        result = review(add_rooms(capture(), "bedroom", [[checkbox("is_open_plan")]]),
                        add_rooms(capture(replacement=True), "bedroom", [[checkbox("is_open_plan")]]))
        self.assertNotIn(("bedroom", 1, "is_open_plan"), room_rows(result))
        self.assertEqual(len(result["unresolved_coverage"]), 2)

    def test_duplicate_controls_inside_one_room_ambiguous_only_there(self):
        original = add_rooms(capture(), groups=[[checkbox("has_carpets"), checkbox("has_carpets")],
                                                [checkbox("has_carpets")]])
        replacement = add_rooms(capture(replacement=True), groups=[[checkbox("has_carpets")], [checkbox("has_carpets")]])
        result = room_rows(review(original, replacement))
        self.assertEqual(result[("bedroom", 1, "has_carpets")]["status"], "ambiguous")
        self.assertEqual(result[("bedroom", 2, "has_carpets")]["status"], "matched")

    def test_room_missing_group_and_control_are_explicit(self):
        original = add_rooms(capture(), groups=[[checkbox("has_carpets")], [checkbox("has_carpets")]])
        replacement = add_rooms(capture(replacement=True), groups=[[field("description", "Only description", tag="textarea", type="textarea")]])
        result = review(original, replacement)
        mapped = room_rows(result)
        self.assertEqual(mapped[("bedroom", 1, "has_carpets")]["missing_in"], ["replacement"])
        self.assertEqual(mapped[("bedroom", 1, "description")]["missing_in"], ["original"])
        self.assertEqual(mapped[("bedroom", 2, "has_carpets")]["status"], "missing")

    def test_room_unknown_control_and_rich_text_retained_unresolved(self):
        original = add_rooms(capture(), groups=[[checkbox("has_carpets"), checkbox("unknown_option")]])
        group = original["pages"][0]["dialogs"][0]["room_groups"][0]
        group["rich_text"] = [{"text": "Unmapped synthetic room prose"}]
        result = review(seal(original), add_rooms(capture(replacement=True)))
        coverage = result["unresolved_coverage"]
        self.assertEqual({item["kind"] for item in coverage}, {"room_control", "room_rich_text"})
        self.assertEqual(room_rows(result)[("bedroom", 1, "has_carpets")]["status"], "matched")

    def test_flat_room_duplicates_retained_separately_and_extra_unknown_kept(self):
        controls = [checkbox("has_carpets"), field("description", "Same synthetic room", tag="textarea", type="textarea")]
        original = add_rooms(capture(), groups=[controls, deepcopy(controls)])
        dialog = original["pages"][0]["dialogs"][0]
        dialog["fields"] = [deepcopy(control) for group in dialog["room_groups"] for control in group["fields"]]
        for index, control in enumerate(dialog["fields"]):
            control["dom_order"] = index
        extra = field("outside_room_unknown", "Extra synthetic value")
        dialog["fields"].append(extra)
        result = review(seal(original), add_rooms(capture(replacement=True), groups=[controls, deepcopy(controls)]))
        represented = result["original"]["represented_dialog_observations"]
        self.assertEqual(len(represented), 4)
        self.assertTrue(all(item["room_identity_assigned"] is False for item in represented))
        self.assertEqual(len(represented[0]["group_paths"]), 2)
        self.assertEqual({item["observation"]["path"] for item in represented},
                         {f"pages/0/dialogs/0/fields/{index}" for index in range(4)})
        self.assertEqual(result["summary"]["unresolved_coverage"], 1)
        self.assertEqual(result["unresolved_coverage"][0]["observation"]["field"], extra)
        compared = [row for row in room_rows(result).values() if row["observed_comparison"] is not None]
        self.assertEqual(len(compared), 4)
        self.assertTrue(all(row["status"] == "matched" for row in compared))

    def test_extra_identical_flat_room_control_never_arbitrarily_paired(self):
        original = add_rooms(capture(), groups=[[checkbox("has_carpets")]])
        dialog = original["pages"][0]["dialogs"][0]
        dialog["fields"] = [checkbox("has_carpets"), checkbox("has_carpets")]
        result = review(seal(original), add_rooms(capture(replacement=True)))
        self.assertEqual(result["original"]["represented_dialog_observations"], [])
        self.assertEqual(len(result["unresolved_coverage"]), 2)
        self.assertTrue(all(item["issues"] == ["flat_dialog_group_coverage_ambiguous"]
                            for item in result["unresolved_coverage"]))

    def test_unknown_group_control_not_double_counted_as_flat_unknown(self):
        original = add_rooms(capture(), groups=[[checkbox("unknown_option")]])
        original["pages"][0]["dialogs"][0]["fields"] = [checkbox("unknown_option")]
        result = review(seal(original))
        self.assertEqual(result["summary"]["unresolved_coverage"], 1)
        self.assertEqual(len(result["original"]["represented_dialog_observations"]), 1)
        self.assertEqual(result["unresolved_coverage"][0]["issues"], ["room_control_unmapped"])

    def test_known_room_schema_absent_both_sides_stays_visible_without_defaults(self):
        result = review(add_rooms(capture()), add_rooms(capture(replacement=True)))
        row = room_rows(result)[("bedroom", 1, "has_walk_in_closet")]
        self.assertEqual(row["status"], "missing")
        self.assertEqual(row["missing_in"], ["original", "replacement"])
        self.assertNotIn("value", row["original"])
        self.assertNotIn("value", row["replacement"])
        self.assertIsNone(row["observed_comparison"])
        self.assertIn("required_fields_and_listing_branch_unverified", result["limitations"])
        self.assertEqual(len(result["room_groups"][0]["entries"]), 30)

    def test_unverified_dialog_cannot_be_compared(self):
        original = add_rooms(capture())
        original["pages"][0]["dialogs"][0].pop("source")
        result = review(seal(original), add_rooms(capture(replacement=True)))
        self.assertTrue(all(row["status"] == "unresolved" for row in room_rows(result).values()))
        self.assertIn("room_dialog_guard_missing", result["unresolved_coverage"][0]["issues"])

    def test_false_close_guard_rejects_without_private_payload(self):
        original = add_rooms(capture())
        dialog = original["pages"][0]["dialogs"][0]
        dialog["source"]["close_verified"] = False
        dialog["room_groups"][0]["fields"][1]["value"] = "PRIVATE SYNTHETIC MARKER"
        self.assert_safe_rejection(review(seal(original)), "capture_rejected", "original", "dialog_guard_missing")

    def test_rooms_require_main_hash_count_structure_and_time(self):
        changes = (
            (lambda d: d["source"].pop("main_fields_sha256"), "room_main_fields_binding_missing"),
            (lambda d: d["source"].update(feature_count=2), "room_group_structure_unresolved"),
            (lambda d: d["room_groups"][0].update(index=2), "room_group_structure_unresolved"),
            (lambda d: d["source"].update(observed_at="2026-01-01T00:00:00+00:00"), "room_observation_order_invalid"),
            (lambda d: d["source"].update(dom_stability_seconds=0), "room_dialog_guard_missing"),
        )
        for change, issue in changes:
            with self.subTest(issue=issue):
                original = add_rooms(capture())
                change(original["pages"][0]["dialogs"][0])
                result = review(seal(original), add_rooms(capture(replacement=True)))
                self.assertEqual(result["status"], "partial")
                self.assertIn(issue, result["unresolved_coverage"][0]["issues"])
                self.assertTrue(all(row["status"] == "unresolved" for row in room_rows(result).values()))

    def test_room_main_count_binding_mismatch(self):
        original = add_rooms(capture())
        sample = original["pages"][0]
        sample["fields"][-1]["value"] = "2"
        sample["dialogs"][0]["source"]["main_fields_sha256"] = _digest({k: sample[k] for k in ("fields", "rich_text")})
        result = review(seal(original), add_rooms(capture(replacement=True)))
        self.assertIn("room_count_binding_unresolved", result["unresolved_coverage"][0]["issues"])

    def test_duplicate_room_dialogs_are_ambiguous(self):
        original = add_rooms(capture())
        original["pages"][0]["dialogs"].append(deepcopy(original["pages"][0]["dialogs"][0]))
        result = review(seal(original), add_rooms(capture(replacement=True)))
        self.assertTrue(all(row["status"] == "ambiguous" for row in room_rows(result).values()))
        self.assertEqual(len(result["unresolved_coverage"]), 2)

    def test_pending_dialog_contents_excluded_and_count_visible(self):
        original = capture()
        original["pending_room_observations"] = [{"title": "Set bedrooms options", "value": "PRIVATE SYNTHETIC MARKER"}]
        original["status"] = "stopped"
        result = review(seal(original), add_rooms(capture(replacement=True)))
        self.assertNotIn("PRIVATE SYNTHETIC MARKER", json.dumps(result))
        self.assertEqual(result["original"]["pending_room_observation_count"], 1)
        self.assertIn({"code": "capture_interrupted"}, result["original"]["gaps"])
        self.assertIn("pending_room_observations_excluded", result["unresolved_coverage"][-1]["issues"])
        self.assertTrue(all(row["status"] == "missing" for row in room_rows(result).values()))

    def test_accepted_and_pending_duplicate_rejected(self):
        original = add_rooms(capture())
        original["pending_room_observations"] = deepcopy(original["pages"][0]["dialogs"])
        self.assert_safe_rejection(review(seal(original)), "pending_room_observation_conflict", "original")

    def test_result_detached_json_serializable_and_no_input_mutation(self):
        original = add_rooms(capture(page([selection(), field("unknown", "Synthetic unknown")])))
        replacement = add_rooms(capture(page([selection()]), replacement=True))
        before = deepcopy((original, replacement))
        with patch("builtins.open", side_effect=AssertionError("No file access permitted")):
            result = review(original, replacement)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        self.assertEqual((original, replacement), before)
        rows(result)["property_type_id"]["original"]["observations"][0]["field"]["selected_options"][0]["value"] = "Changed"
        result["room_groups"][0]["original"]["record"]["fields"][0]["checked"] = True
        result["original"]["selected_pages"][0]["limitations"].append("Changed")
        self.assertEqual((original, replacement), before)
        self.assertTrue(verify_capture_integrity(original))
        self.assertTrue(verify_capture_integrity(replacement))


if __name__ == "__main__":
    unittest.main()
