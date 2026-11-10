"""Synthetic offline inventories only. No browser, account, or listing data."""
from copy import deepcopy
import hashlib
import json

import pytest

from quick_relist.capture import SCHEMA_VERSION as CAPTURE_SCHEMA, verify_capture_integrity
from quick_relist.source_fields import extract_source_fields


def seal(capture):
    def receipt(value):
        value.pop("integrity", None)
        value["integrity"] = {"algorithm": "sha256", "sha256": hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       allow_nan=False).encode()).hexdigest()}
    for page in capture["pages"]:
        receipt(page)
    receipt(capture)
    return capture


def field(name="street_name", value="Synthetic street", **updates):
    result = {"name": name, "id": name, "label": name, "tag": "input", "type": "text",
              "value": value, "dom_order": 0, "checked": None, "selected_options": None,
              "disabled": False, "read_only": False, "section": "Synthetic section"}
    result.update(updates)
    return result


def select(name="property_type_id", code="007", text="Synthetic property type", **updates):
    return field(name, text, tag="div", type="custom-combobox",
                 selected_options=[{"value": code, "text": text}],
                 observed_business_selection_code=True, **updates)


def page(key="details", fields=None, number=1, **updates):
    result = {"page_key": key, "page_path": "/app/listings/edit/123456" + ("/" + key if key != "details" else ""),
              "observation_number": number, "observed_at": f"2026-01-02T03:04:{number:02}+00:00",
              "complete": False, "fields": [field()] if fields is None else fields,
              "rich_text": [], "dialogs": [], "sections": [], "lists": [], "tables": [], "media": [],
              "editor_links": [], "discovery": [], "limitations": [],
              "matched_source": {"listing_id": "123456", "rxcu": "RXCU12345",
                                 "reference_checked_before_and_after": True, "pinned_tab_checked": True,
                                 "dom_stability_seconds": 1.0, "hydration_complete_verified": False}}
    result.update(updates)
    return result


def capture(*pages):
    return seal({"schema_version": CAPTURE_SCHEMA, "capture_kind": "matched_partial_visible_dom",
                 "complete": False, "live_ready": False, "listing_id": "123456", "status": "partial",
                 "identity": {"route_verified": True, "reference_verified": True,
                              "account_verified": False, "page_binding_verified": False},
                 "matched_source": {"listing_id": "123456", "rxcu": "RXCU12345", "row_link_binding_used": True,
                                    "all_accepted_observations_guarded": True, "historical_observations_only": True,
                                    "account_verified": False, "fidelity_verified": False, "nested_dialogs_verified": False},
                 "limitations": [], "pages": list(pages)})


def entries(result):
    return {entry["key"]: entry for entry in result["entries"]}


def test_exact_native_fields_dates_prices_and_counts_are_lossless():
    names = ["land_area", "floor_area", "street_number", "street_name", "street_type", "estate_name",
             "lightstone_erf_number", "lightstone_sectional_scheme_unit_number", "scheme_number", "scheme_name",
             "lightstone_id_reg_number", "door_number", "latitude", "longitude", "selling_price", "rates", "levy",
             "special_levies", "mandate_signed_date", "mandate_expiry", "date_listed", "expiry_date", "on_market_since"]
    controls = [field(name, " 0001.00 ", extra_observed_metadata={"synthetic": [False, None, ""]}) for name in names]
    features = ("bedroom", "kitchen", "bathroom", "garage", "parking", "lounge", "dining")
    controls += [field("", "00", id=f"feature-{name}-input", type="number") for name in features]
    original = capture(page(fields=controls))
    result = extract_source_fields(original, expected_listing_id="123456", expected_rxcu="RXCU-12345")
    assert result["status"] == "partial"
    mapped = entries(result)
    assert all(mapped[name]["status"] == "observed" and mapped[name]["value"] == " 0001.00 " for name in names)
    assert all(mapped["feature_" + name]["value"] == "00" for name in features)
    for index, control in enumerate(controls):
        key = control["name"] or "feature_" + features[index - len(names)]
        observation = mapped[key]["observations"][0]
        assert observation["field"] == control
        assert observation["path"] == f"pages/0/fields/{index}"
        assert observation["page_sha256"] == original["pages"][0]["integrity"]["sha256"]
        assert observation["observed_at"] == original["pages"][0]["observed_at"]
    assert result["unknowns"] == []
    assert result["complete"] is result["live_ready"] is False
    assert json.loads(json.dumps(result)) == result


def test_all_exact_business_selectors_preserve_display_code_and_originals():
    names = ["property_type_id", "mandate_type_id", "office_id", "listing_type_id", "country_id", "land_area_units",
             "floor_area_units", "apartment_descriptive_property_type", "deed_office_id"]
    original = capture(page(fields=[select(name) for name in names]))
    result = entries(extract_source_fields(original))
    for name in names:
        assert result[name]["status"] == "observed"
        assert result[name]["value"] == "Synthetic property type"
        assert result[name]["selected_code"] == "007"
        assert result[name]["display_value"] == "Synthetic property type"
        assert result[name]["selected_options"] == [{"value": "007", "text": "Synthetic property type"}]
        assert result[name]["issues"] == ["option_resolution_unverified"]


def test_native_select_value_is_code_not_display_and_blank_is_not_missing():
    native = field("country_id", "", tag="select", type="select-one",
                   selected_options=[{"value": "", "text": ""}])
    row = entries(extract_source_fields(capture(page(fields=[native]))))["country_id"]
    assert row["status"] == "observed"
    assert row["value"] == row["selected_code"] == row["display_value"] == ""


@pytest.mark.parametrize("checked", [False, True])
def test_checkboxes_preserve_false_and_never_invert_address_hidden(checked):
    names = ["poa", "featured", "address_hidden", "transfer_cost"]
    controls = [field(name, "on", type="checkbox", checked=checked, label="Show address on web") for name in names]
    result = entries(extract_source_fields(capture(page(fields=controls))))
    for name in names:
        assert result[name]["value"] is checked
        assert result[name]["control_value"] == "on"
        assert result[name]["observations"][0]["field"]["checked"] is checked
    assert result["address_hidden"]["status"] == "unresolved"
    assert "address_visibility_polarity_unverified" in result["address_hidden"]["issues"]
    assert "show_address_on_web" not in result


def radios(*checked):
    return [field("sale_state_id", f"0{index}", id=f"radio-{index}", label=f"Synthetic state {index}",
                  type="radio", checked=value) for index, value in enumerate(checked)]


def test_radio_group_has_one_value_all_originals_and_selected_path():
    controls = radios(False, True, False)
    row = entries(extract_source_fields(capture(page(fields=controls))))["sale_state_id"]
    assert row["status"] == "observed"
    assert row["value"] == "01"
    assert row["display_value"] == "Synthetic state 1"
    assert row["selected_path"] == "pages/0/fields/1"
    assert [obs["field"] for obs in row["observations"]] == controls


@pytest.mark.parametrize("checked,status,issue", [((False, False), "unresolved", "radio_selection_missing"),
                                                ((True, True), "ambiguous", "radio_group_ambiguous")])
def test_radio_selection_never_guessed(checked, status, issue):
    row = entries(extract_source_fields(capture(page(fields=radios(*checked)))))["sale_state_id"]
    assert row["status"] == status and row["issues"] == [issue]
    assert "value" not in row


def test_duplicate_radio_code_is_ambiguous_even_if_only_one_checked():
    controls = radios(False, True)
    controls[1]["value"] = controls[0]["value"]
    assert entries(extract_source_fields(capture(page(fields=controls))))["sale_state_id"]["status"] == "ambiguous"


def test_suburb_label_retains_text_without_inventing_entity_id():
    control = field("", " Synthetic suburb ", id=":dynamic-r17:", label="Suburb *")
    row = entries(extract_source_fields(capture(page(fields=[control]))))["suburb"]
    assert row["status"] == "unresolved"
    assert row["value"] == " Synthetic suburb "
    assert row["issues"] == ["suburb_selected_entity_id_unresolved"]
    assert row["observations"][0]["field"] == control
    assert "selected_code" not in row


def test_deal_media_brochure_and_richtext_map_only_exact_observed_controls():
    deal = [field(name, "000.0") for name in ("total_commission_value", "total_commission_percentage")]
    names = ["virtualtour_url", "videotour_url", "p24_youtube_video_id", "matterport_video_id", "eyespy360_url", "marketing_header"]
    media = [field(name, "Synthetic media text") for name in names]
    brochure = field("", "Synthetic\n\nbrochure", id="outlined-multiline-flexible", label="Brochure Description",
                     tag="textarea", type="textarea")
    other = field("", "Other text", id="outlined-multiline-flexible", label="Other description", tag="textarea", type="textarea")
    rich = {"label": "rdw-editor", "text": "Synthetic rich text", "sanitized_html": "<p><b>Synthetic</b> rich text</p>",
            "format_fidelity_verified": False, "dom_order": 0}
    result = extract_source_fields(capture(page("deal_particulars", deal),
                                           page("media", media + [brochure, other], number=2, rich_text=[rich])))
    mapped = entries(result)
    assert all(mapped[name]["value"] == "Synthetic media text" for name in names)
    assert mapped["total_commission_value"]["value"] == "000.0"
    assert mapped["brochure_description"]["status"] == "observed"
    assert mapped["brochure_description"]["value"] == "Synthetic\n\nbrochure"
    assert mapped["rich_text_description"]["status"] == "unresolved"
    assert mapped["rich_text_description"]["sanitized_html"] == rich["sanitized_html"]
    assert mapped["rich_text_description"]["observations"][0]["path"] == "pages/1/rich_text/0"
    assert result["unknowns"][0]["field"] == other


def test_missing_richtext_label_and_brochure_id_only_never_match():
    brochure = field("", "Synthetic", id="outlined-multiline-flexible", label="", tag="textarea", type="textarea")
    rich = {"text": "Synthetic", "sanitized_html": "<p>Synthetic</p>"}
    result = extract_source_fields(capture(page("media", [brochure], rich_text=[rich])))
    assert entries(result)["brochure_description"]["status"] == "missing"
    assert entries(result)["rich_text_description"]["status"] == "missing"
    assert len(result["unknowns"]) == 2


def test_latest_whole_page_wins_no_older_value_or_missing_field_backfill():
    original = capture(page(fields=[field(value="Old"), field("selling_price", "000")]),
                       page("media", [field("marketing_header", "Old header")], number=2),
                       page(fields=[field(value="New")], number=3))
    result = extract_source_fields(original)
    mapped = entries(result)
    assert mapped["street_name"]["value"] == "New"
    assert mapped["street_name"]["observations"][0]["path"] == "pages/2/fields/0"
    assert mapped["selling_price"]["status"] == "missing"
    assert "value" not in mapped["selling_price"]
    assert result["superseded_pages"][0]["path"] == "pages/0"
    assert result["current_values_verified"] is False
    assert {gap["code"] for gap in result["gaps"]} >= {"historical_values_not_rechecked", "cross_page_revision_unverified"}


def test_ambiguous_duplicate_preserves_all_candidates_and_no_value():
    result = extract_source_fields(capture(page(fields=[field(value="First"), field(value="Second")])))
    row = entries(result)["street_name"]
    assert row["status"] == "ambiguous" and "value" not in row
    assert [obs["field"]["value"] for obs in row["observations"]] == ["First", "Second"]


def test_name_id_conflict_does_not_reuse_control_as_two_values():
    result = entries(extract_source_fields(capture(page(fields=[field("street_name", id="street_number")]))))
    for key in ("street_name", "street_number"):
        assert result[key]["status"] == "ambiguous"
        assert result[key]["issues"] == ["conflicting_control_identifiers"]
        assert "value" not in result[key]


def test_unknown_wrong_page_and_dialog_controls_all_retained_not_silently_mapped():
    unknown = field("unknown_new_field", "Unknown synthetic value")
    wrong_page = field("street_name", "Wrong tab")
    dialog = {"title": "Synthetic room", "fields": [field("street_name", "Dialog value")],
              "rich_text": [{"label": "rdw-editor", "text": "Synthetic", "sanitized_html": "<p>Synthetic</p>"}]}
    result = extract_source_fields(capture(page(fields=[unknown], dialogs=[dialog]),
                                           page("portals", [wrong_page], number=2)))
    assert entries(result)["street_name"]["status"] == "missing"
    assert {x["path"] for x in result["unknowns"]} == {
        "pages/0/fields/0", "pages/0/dialogs/0/fields/0", "pages/0/dialogs/0/rich_text/0", "pages/1/fields/0"}
    assert all(x["reason"] == "dialog_control_unmapped" for x in result["unknowns"] if "/dialogs/" in x["path"])


def test_capture_limits_and_page_provenance_never_imply_lossless_browser_values():
    source = capture(page(limitations=["dom_text_truncated", "url_credentials_and_queries_omitted"]))
    source["limitations"] = ["dom_text_truncated", "url_credentials_and_queries_omitted"]
    result = extract_source_fields(seal(source))
    assert result["capture_limitations"] == source["limitations"]
    assert result["selected_pages"][0]["limitations"] == source["pages"][0]["limitations"]
    assert any(x.get("limitation") == "dom_text_truncated" for x in result["gaps"])
    assert result["complete"] is False


def test_result_is_detached_and_input_never_modified():
    source = capture(page(fields=[select(), field("unknown")]))
    before = deepcopy(source)
    result = extract_source_fields(source)
    mapped = entries(result)
    mapped["property_type_id"]["selected_options"][0]["value"] = "Changed"
    mapped["property_type_id"]["observations"][0]["field"]["selected_options"][0]["value"] = "Changed"
    result["unknowns"][0]["field"]["value"] = "Changed"
    result["selected_pages"][0]["limitations"].append("Changed")
    assert source == before and verify_capture_integrity(source)


def assert_rejected(result, issue):
    assert result["status"] == "rejected" and result["source"] is None
    assert result["issues"] == [issue]
    assert result["unknowns"] == result["selected_pages"] == []
    assert all(row["status"] == "unresolved" and row["observations"] == [] and "value" not in row for row in result["entries"])
    assert "PRIVATE SYNTHETIC MARKER" not in json.dumps(result)
    assert result["complete"] is result["live_ready"] is False


@pytest.mark.parametrize("value", [None, [], {}, "PRIVATE SYNTHETIC MARKER", {"complete": False},
                                   {"value": float("nan")}, {"value": object()}])
def test_malformed_inputs_return_safe_static_rejections(value):
    result = extract_source_fields(value)
    assert result["status"] == "rejected"
    assert "PRIVATE SYNTHETIC MARKER" not in json.dumps(result)


def test_integrity_checked_before_reading_any_values():
    source = capture(page())
    source["pages"][0]["fields"][0]["value"] = "PRIVATE SYNTHETIC MARKER"
    assert_rejected(extract_source_fields(source), "capture_integrity_invalid")


@pytest.mark.parametrize("change,issue", [
    (lambda c: c.update(capture_kind="partial_visible_dom"), "capture_not_matched_partial"),
    (lambda c: c.update(live_ready=True), "capture_not_matched_partial"),
    (lambda c: c.update(matched_source=None), "source_identity_missing"),
    (lambda c: c["matched_source"].update(listing_id="999999"), "source_identity_conflict"),
    (lambda c: c["identity"].update(reference_verified=False), "capture_guard_missing"),
    (lambda c: c["matched_source"].update(all_accepted_observations_guarded=False), "capture_guard_missing"),
    (lambda c: c["pages"][0].update(page_path="/app/listings/edit/999999"), "page_identity_conflict"),
    (lambda c: c["pages"][0].update(page_path="/app/listings/edit/123456?secret=PRIVATE SYNTHETIC MARKER"), "page_route_invalid"),
    (lambda c: c["pages"][0].update(page_key="media"), "page_identity_conflict"),
    (lambda c: c["pages"][0].update(page_key="unknown"), "page_metadata_invalid"),
    (lambda c: c["pages"][0].pop("matched_source"), "unguarded_history_rejected"),
    (lambda c: c["pages"][0]["matched_source"].update(listing_id="999999"), "page_identity_conflict"),
    (lambda c: c["pages"][0]["matched_source"].update(rxcu="RXCU99999"), "page_identity_conflict"),
    (lambda c: c["pages"][0]["matched_source"].update(pinned_tab_checked=False), "page_guard_missing"),
    (lambda c: c["pages"][0]["matched_source"].update(reference_checked_before_and_after=1), "page_guard_missing"),
    (lambda c: c["pages"][0]["matched_source"].update(dom_stability_seconds=True), "page_guard_missing"),
    (lambda c: c["pages"][0]["matched_source"].update(dom_stability_seconds=0), "page_guard_missing"),
    (lambda c: c["pages"][0].update(observation_number=True), "observation_order_invalid"),
    (lambda c: c["pages"][0].update(observed_at="not a time"), "observation_order_invalid"),
    (lambda c: c["pages"][0].update(observed_at="2026-01-02T03:04:01"), "observation_order_invalid"),
    (lambda c: c["pages"][0].update(fields=["PRIVATE SYNTHETIC MARKER"]), "page_payload_malformed"),
    (lambda c: c["pages"][0].update(dialogs=[{"fields": [], "rich_text": None}]), "page_payload_malformed"),
    (lambda c: c["pages"][0].update(limitations=[{}]), "page_payload_malformed"),
    (lambda c: c.update(limitations=[{}]), "capture_limitations_malformed"),
])
def test_recomputed_digest_does_not_hide_identity_guard_or_structure_conflicts(change, issue):
    source = capture(page(fields=[field(value="PRIVATE SYNTHETIC MARKER")]))
    change(source)
    assert_rejected(extract_source_fields(seal(source)), issue)


@pytest.mark.parametrize("expected,issue", [({"expected_listing_id": "999999"}, "expected_listing_mismatch"),
                                           ({"expected_listing_id": 123456}, "expected_listing_mismatch"),
                                           ({"expected_rxcu": "RXCU99999"}, "expected_reference_mismatch"),
                                           ({"expected_rxcu": {}}, "expected_reference_mismatch")])
def test_job_identity_expectations_are_checked(expected, issue):
    assert_rejected(extract_source_fields(capture(page()), **expected), issue)


@pytest.mark.parametrize("change", [lambda p: p.update(observation_number=1),
                                   lambda p: p.update(observation_number=0),
                                   lambda p: p.update(observed_at="2020-01-01T00:00:00+00:00")])
def test_duplicate_out_of_order_or_time_regressing_history_rejected(change):
    newer = page(number=2)
    change(newer)
    assert_rejected(extract_source_fields(capture(page(), newer)), "observation_order_invalid")


def test_newer_unguarded_history_never_falls_back_to_stale_value():
    newer = page(number=2)
    newer.pop("matched_source")
    assert_rejected(extract_source_fields(capture(page(), newer)), "unguarded_history_rejected")


def test_other_listing_in_superseded_history_is_not_hidden_by_latest_page():
    old = page()
    old["matched_source"]["listing_id"] = "999999"
    assert_rejected(extract_source_fields(capture(old, page(number=2))), "page_identity_conflict")


def test_empty_guarded_capture_not_a_complete_or_usable_inventory():
    assert_rejected(extract_source_fields(capture()), "guarded_observations_missing")


@pytest.mark.parametrize("updates,issue", [({"value": None}, "control_shape_invalid"),
                                          ({"type": {}}, "control_shape_invalid"),
                                          ({"tag": None}, "control_shape_invalid"),
                                          ({"type": "password"}, "control_type_mismatch"),
                                          ({"type": "checkbox", "checked": False}, "control_type_mismatch")])
def test_malformed_mapped_control_retained_but_no_chosen_value(updates, issue):
    control = field(**updates)
    result = extract_source_fields(capture(page(fields=[control])))
    row = entries(result)["street_name"]
    assert row["status"] == "unresolved" and row["issues"] == [issue]
    assert row["observations"][0]["field"] == control
    assert "value" not in row


@pytest.mark.parametrize("options,status,issue", [(None, "unresolved", "selected_options_malformed"),
    ([{"value": 1, "text": "Synthetic"}], "unresolved", "selected_options_malformed"),
    ([], "unresolved", "selected_option_unresolved"),
    ([{"value": "a", "text": "A"}, {"value": "b", "text": "B"}], "ambiguous", "multiple_selected_options")])
def test_selector_missing_malformed_or_ambiguous_codes_preserved_without_guess(options, status, issue):
    control = select()
    control["selected_options"] = options
    row = entries(extract_source_fields(capture(page(fields=[control]))))["property_type_id"]
    assert row["status"] == status and row["issues"] == [issue]
    assert row["selected_options"] == options
    assert "selected_code" not in row


@pytest.mark.parametrize("change,issue", [(lambda f: f.update(value="Different display"), "selection_value_conflict"),
                                         (lambda f: f.pop("observed_business_selection_code"), "selection_code_provenance_missing")])
def test_selector_contradiction_or_missing_provenance_not_promoted(change, issue):
    control = select()
    change(control)
    row = entries(extract_source_fields(capture(page(fields=[control]))))["property_type_id"]
    assert row["status"] == "unresolved" and row["issues"] == [issue]
    assert "selected_code" not in row


def test_complete_mapping_never_claims_required_fields_branch_or_live_readiness():
    result = extract_source_fields(capture(page(), page("deal_particulars", [], number=2),
                                           page("media", [], number=3), page("portals", [], number=4)))
    assert result["complete"] is result["live_ready"] is result["required_fields_verified"] is result["listing_branch_verified"] is False
    assert not any(gap["code"] == "page_not_observed" for gap in result["gaps"])
    assert any(gap["code"] == "control_not_observed" for gap in result["gaps"])


def test_room_groups_keep_scope_originals_and_duplicate_ids_in_related_record():
    groups = [{"title": f"Bedroom {index + 1}", "index": index + 1,
               "fields": [field("description", f"Synthetic bedroom {index + 1}")],
               "rich_text": [], "mapping_verified": False} for index in range(2)]
    dialog = {"title": "Set bedrooms options", "fields": [group["fields"][0] for group in groups],
              "rich_text": [], "room_groups": groups}
    source = capture(page(dialogs=[dialog]))
    result = extract_source_fields(source)
    related = result["related_records"][0]
    assert related["path"] == "pages/0/dialogs/0" and related["status"] == "unresolved"
    assert related["record"] == dialog
    assert len(result["unknowns"]) == 2
    assert result["unknowns"][0]["path"] != result["unknowns"][1]["path"]
    related["record"]["room_groups"][0]["fields"][0]["value"] = "Changed"
    assert source["pages"][0]["dialogs"][0] == dialog


def test_pending_room_observations_counted_as_gap_but_values_never_mapped():
    source = capture(page())
    source["pending_room_observations"] = [{"fields": [field("selling_price", "PRIVATE SYNTHETIC MARKER")],
                                            "source_guard_after_close": False}]
    result = extract_source_fields(seal(source))
    assert result["status"] == "partial"
    assert result["pending_room_observation_count"] == 1
    assert {"code": "pending_room_observations_excluded", "count": 1} in result["gaps"]
    assert entries(result)["selling_price"]["status"] == "missing"
    assert "PRIVATE SYNTHETIC MARKER" not in json.dumps(result)
    assert result["related_records"] == []


@pytest.mark.parametrize("pending", [{}, None, ["PRIVATE SYNTHETIC MARKER"]])
def test_malformed_pending_observation_container_rejected(pending):
    source = capture(page())
    source["pending_room_observations"] = pending
    assert_rejected(extract_source_fields(seal(source)), "pending_room_observations_malformed")


@pytest.mark.parametrize("groups", [None, [None], [{"title": "Synthetic", "index": True, "fields": [], "rich_text": []}],
                                    [{"title": "Synthetic", "index": 0, "fields": None, "rich_text": []}]])
def test_malformed_room_group_container_rejected(groups):
    dialog = {"fields": [], "rich_text": [], "room_groups": groups}
    assert_rejected(extract_source_fields(capture(page(dialogs=[dialog]))), "page_payload_malformed")


def guarded_dialog():
    return {"title": "Set bedrooms options", "fields": [field("description", "Synthetic bedroom")], "rich_text": [],
            "source": {"rxcu": "RXCU12345", "listing_id": "123456", "page_key": "details",
                       "opener": "Set bedrooms options", "title": "Set bedrooms options", "feature_count": "1",
                       "reference_checked_before_and_after": True, "pinned_tab_checked": True,
                       "main_fields_unchanged_verified": True, "close_verified": True,
                       "complete": False, "server_writes_verified": False}}


def test_guarded_dialog_source_and_originals_retained():
    dialog = guarded_dialog()
    result = extract_source_fields(capture(page(dialogs=[dialog])))
    assert result["status"] == "partial"
    assert result["related_records"][0]["record"] == dialog


@pytest.mark.parametrize("change,issue", [
    ({"listing_id": "999999"}, "dialog_identity_conflict"),
    ({"rxcu": "RXCU99999"}, "dialog_identity_conflict"),
    ({"page_key": "media"}, "dialog_identity_conflict"),
    ({"close_verified": False}, "dialog_guard_missing"),
    ({"main_fields_unchanged_verified": False}, "dialog_guard_missing"),
    ({"reference_checked_before_and_after": False}, "dialog_guard_missing"),
])
def test_dialog_with_identity_or_post_close_contradiction_is_not_promoted(change, issue):
    dialog = guarded_dialog()
    dialog["source"].update(change)
    assert_rejected(extract_source_fields(capture(page(dialogs=[dialog]))), issue)


@pytest.mark.parametrize("status", ["stopped", "cancelled"])
def test_interrupted_capture_retains_guarded_progress_and_visible_gap(status):
    source = capture(page())
    source["status"] = status
    result = extract_source_fields(seal(source))
    assert result["status"] == "partial" and result["capture_status"] == status
    assert entries(result)["street_name"]["status"] == "observed"
    assert {"code": "capture_interrupted"} in result["gaps"]


def test_dialog_main_fields_hash_must_pair_with_accepted_page():
    from quick_relist.capture import _digest
    dialog = guarded_dialog()
    main = page(dialogs=[dialog])
    dialog['source']['main_fields_sha256'] = _digest({key: main[key] for key in ('fields', 'rich_text')})
    assert extract_source_fields(capture(main))['status'] == 'partial'
    main['fields'][0]['value'] = 'Changed after room capture'
    assert_rejected(extract_source_fields(capture(main)), 'dialog_main_fields_conflict')
