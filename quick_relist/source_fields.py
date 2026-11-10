"""Read-only field inventory from one saved, guarded partial capture.

This is an observed DOM mapping, not the Hub creation schema. An ``observed``
entry means that one control (or radio group) was mapped without ambiguity. It
does not establish completeness, current server values, required fields, option
validity, account ownership, or readiness to recreate a listing. No browser or
network access, persistence, field coercion, logging, or listing writes occur.

Call ``extract_source_fields(capture, expected_listing_id=..., expected_rxcu=...)``
when reviewing a job. The optional expectations bind the inventory to that job.
Every original selected control is retained, including unknown properties, at
its original capture path. Values remain exactly as captured, which may already
be sanitized or truncated by the collector. A checksum detects accidental edits
only; it is not authentication. Never treat this inventory as a verified backup.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import math
import re
from typing import Any

from .capture import EDITOR_PAGES, CaptureSafetyError, _digest, parse_editor_url, verify_capture_integrity
from .date_capture import (DATE_UNRESOLVED_ISSUES, date_observed_parts,
                           is_segmented_date_record, segmented_date_record_valid)

SCHEMA_VERSION = "hub-source-fields/v1"
WARNING = "Historical partial field inventory only. Verify every value in Hub; not a complete source backup or safe for relisting."

# Exact observed names/IDs, not inferred sale/rental or account-specific rules.
_SELECTS = (
    ("property_type_id", "Property type"), ("mandate_type_id", "Mandate type"),
    ("office_id", "Office"), ("listing_type_id", "Listing type"),
    ("country_id", "Country"), ("land_area_units", "Land area units"),
    ("floor_area_units", "Floor area units"),
    ("apartment_descriptive_property_type", "Apartment descriptive property type"),
    ("deed_office_id", "Deed office"),
)
_DETAILS = (
    ("land_area", "Land area"), ("floor_area", "Floor area"),
    ("street_number", "Street number"), ("street_name", "Street name"),
    ("street_type", "Street type"), ("estate_name", "Estate name"),
    ("lightstone_erf_number", "Erf number"),
    ("lightstone_sectional_scheme_unit_number", "Sectional scheme unit number"),
    ("scheme_number", "Scheme number"), ("scheme_name", "Scheme name"),
    ("lightstone_id_reg_number", "ID / registration number"),
    ("door_number", "Door number"), ("latitude", "Latitude"), ("longitude", "Longitude"),
    ("selling_price", "Selling price"), ("rates", "Rates"), ("levy", "Levy"),
    ("special_levies", "Special levies"),
)
_DATES = (
    ("mandate_signed_date", "Mandate signed date"), ("mandate_expiry", "Mandate expiry"),
    ("date_listed", "Date listed"), ("expiry_date", "Expiry date"),
    ("on_market_since", "On market since"),
)
_CHECKBOXES = (
    ("poa", "Price on application"), ("featured", "Featured"),
    # The label's apparent polarity must never invert this raw control state.
    ("address_hidden", "Address visibility (raw address_hidden)"),
    ("transfer_cost", "Transfer cost"),
)
_FEATURES = ("bedroom", "kitchen", "bathroom", "garage", "parking", "lounge", "dining")
_MEDIA = (
    ("virtualtour_url", "Virtual tour URL"), ("videotour_url", "Video tour URL"),
    ("p24_youtube_video_id", "P24 YouTube video ID"),
    ("matterport_video_id", "Matterport video ID"),
    ("eyespy360_url", "EyeSpy360 URL"), ("marketing_header", "Marketing header"),
)


def _specs():
    result = []
    def add(pairs, page, kind, selector="name_or_id"):
        for key, label in pairs:
            result.append({"key": key, "label": label, "page": page, "kind": kind,
                           "selector": selector, "identifier": key})
    add(_SELECTS, "details", "selection", "name")
    add(_DETAILS, "details", "text")
    add(_DATES, "details", "date", "name")
    add(_CHECKBOXES, "details", "checkbox", "name")
    add((("sale_state_id", "Sale state"),), "details", "radio_group", "name")
    add((("suburb", "Suburb"),), "details", "suburb", "label")
    for name in _FEATURES:
        result.append({"key": "feature_" + name, "label": name.capitalize() + " count",
                       "page": "details", "kind": "text", "selector": "id",
                       "identifier": "feature-" + name + "-input"})
    add((("total_commission_value", "Total commission value"),
         ("total_commission_percentage", "Total commission percentage")),
        "deal_particulars", "text", "name")
    add(_MEDIA, "media", "text", "name")
    add((("brochure_description", "Brochure Description"),), "media", "brochure", "label")
    add((("rich_text_description", "Rich text description"),), "media", "rich_text", "label")
    return result


def _blank_result():
    return {
        "schema_version": SCHEMA_VERSION, "status": "rejected", "complete": False,
        "live_ready": False, "source": None, "selected_pages": [], "superseded_pages": [],
        "entries": [{k: s[k] for k in ("key", "label", "page", "kind")} |
                    {"status": "unresolved", "observations": [], "issues": ["capture_not_accepted"]}
                    for s in _specs()],
        "unknowns": [], "related_records": [], "pending_room_observation_count": 0,
        "gaps": [], "issues": [], "warning": WARNING,
        "historical_observations_only": True, "current_values_verified": False,
        "required_fields_verified": False, "listing_branch_verified": False,
    }


def _issue(result, code):
    result["issues"].append(code)
    result["gaps"].append({"code": code})
    return result


def _reference(value):
    # Unlike normalize_rxcu, do not stringify a malformed object.
    if isinstance(value, str) and re.fullmatch(r"RXCU-?[0-9]{3,20}", value):
        return value.replace("-", "")
    return None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _page_meta(page, index):
    return {"path": f"pages/{index}", "page_key": page["page_key"],
            "observation_number": page["observation_number"], "observed_at": page["observed_at"],
            "page_sha256": page["integrity"]["sha256"], "limitations": deepcopy(page["limitations"])}


def _validate(capture, expected_listing_id, expected_rxcu):
    """Return a static error code. Never place data values in diagnostics."""
    try:
        encoded = json.dumps(capture, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return "capture_malformed"
    if len(encoded) > 16_000_000:
        return "capture_too_large"
    if not verify_capture_integrity(capture):
        return "capture_integrity_invalid"
    if capture.get("live_ready") is not False or capture.get("capture_kind") != "matched_partial_visible_dom":
        return "capture_not_matched_partial"
    if capture.get("status") not in ("partial", "stopped", "cancelled"):
        return "capture_status_invalid"
    source = capture.get("matched_source")
    if not isinstance(source, dict):
        return "source_identity_missing"
    listing_id = capture.get("listing_id")
    if not isinstance(listing_id, str) or not re.fullmatch(r"[0-9]{1,128}", listing_id):
        return "source_identity_missing"
    reference = _reference(source.get("rxcu"))
    if reference is None or source.get("listing_id") != listing_id:
        return "source_identity_conflict"
    if expected_listing_id is not None and (not isinstance(expected_listing_id, str) or expected_listing_id != listing_id):
        return "expected_listing_mismatch"
    if expected_rxcu is not None and (_reference(expected_rxcu) is None or _reference(expected_rxcu) != reference):
        return "expected_reference_mismatch"
    pages = capture["pages"]
    if not pages:
        return "guarded_observations_missing"
    identity = capture.get("identity")
    if (not isinstance(identity, dict) or identity.get("route_verified") is not True
            or identity.get("reference_verified") is not True
            or source.get("row_link_binding_used") is not True
            or source.get("all_accepted_observations_guarded") is not True):
        return "capture_guard_missing"
    last_number, last_time = 0, None
    for page in pages:
        if page.get("page_key") not in EDITOR_PAGES or not isinstance(page.get("page_path"), str):
            return "page_metadata_invalid"
        try:
            route = parse_editor_url("https://hub.remax.co.za" + page["page_path"])
        except CaptureSafetyError:
            return "page_route_invalid"
        if route["listing_id"] != listing_id or route["page_key"] != page["page_key"]:
            return "page_identity_conflict"
        guarded = page.get("matched_source")
        if not isinstance(guarded, dict):
            return "unguarded_history_rejected"
        if guarded.get("listing_id") != listing_id or _reference(guarded.get("rxcu")) != reference:
            return "page_identity_conflict"
        if (guarded.get("reference_checked_before_and_after") is not True
                or guarded.get("pinned_tab_checked") is not True):
            return "page_guard_missing"
        stable = guarded.get("dom_stability_seconds")
        if type(stable) not in (int, float) or not math.isfinite(stable) or stable <= 0:
            return "page_guard_missing"
        number, observed = page.get("observation_number"), _timestamp(page.get("observed_at"))
        if type(number) is not int or number <= last_number or observed is None:
            return "observation_order_invalid"
        if last_time is not None and observed < last_time:
            return "observation_order_invalid"
        last_number, last_time = number, observed
        if any(not isinstance(page.get(key), list) for key in ("fields", "rich_text", "dialogs", "limitations")):
            return "page_payload_malformed"
        if any(not isinstance(value, str) for value in page["limitations"]):
            return "page_payload_malformed"
        if any(not isinstance(value, dict) for key in ("fields", "rich_text", "dialogs") for value in page[key]):
            return "page_payload_malformed"
        for dialog in page["dialogs"]:
            if any(not isinstance(dialog.get(key), list) or any(not isinstance(x, dict) for x in dialog[key])
                   for key in ("fields", "rich_text")):
                return "page_payload_malformed"
            if "source" in dialog:
                dialog_source = dialog["source"]
                if (not isinstance(dialog_source, dict) or dialog_source.get("listing_id") != listing_id
                        or _reference(dialog_source.get("rxcu")) != reference
                        or dialog_source.get("page_key") != page["page_key"]):
                    return "dialog_identity_conflict"
                if any(dialog_source.get(key) is not True for key in (
                        "reference_checked_before_and_after", "pinned_tab_checked",
                        "main_fields_unchanged_verified", "close_verified")):
                    return "dialog_guard_missing"
                if ('main_fields_sha256' in dialog_source and dialog_source['main_fields_sha256'] !=
                        _digest({key: page[key] for key in ('fields', 'rich_text')})):
                    return "dialog_main_fields_conflict"
            if "room_groups" in dialog:
                groups = dialog["room_groups"]
                if not isinstance(groups, list) or any(not isinstance(g, dict) for g in groups):
                    return "page_payload_malformed"
                for group in groups:
                    if (not isinstance(group.get("title"), str)
                            or type(group.get("index")) is not int or group["index"] <= 0
                            or any(not isinstance(group.get(key), list)
                                   or any(not isinstance(x, dict) for x in group[key])
                                   for key in ("fields", "rich_text"))):
                        return "page_payload_malformed"
    if not isinstance(capture.get("limitations"), list) or any(not isinstance(x, str) for x in capture["limitations"]):
        return "capture_limitations_malformed"
    if "pending_room_observations" in capture:
        pending = capture["pending_room_observations"]
        if not isinstance(pending, list) or any(not isinstance(item, dict) for item in pending):
            return "pending_room_observations_malformed"
    return None


def _label(field):
    label = field.get("label")
    return " ".join(label.split()).rstrip(" *:").casefold() if isinstance(label, str) else ""


def _matches(spec, field, rich):
    if (spec["kind"] == "rich_text") != rich:
        return False
    selector, identifier = spec["selector"], spec["identifier"]
    if selector == "name_or_id":
        return field.get("name") == identifier or field.get("id") == identifier
    if selector in ("name", "id"):
        return field.get(selector) == identifier
    return _label(field) == {"suburb": "suburb", "brochure_description": "brochure description",
                             "rich_text_description": "rdw-editor"}[spec["key"]]


def _control_issues(field, kind):
    if is_segmented_date_record(field):
        return [] if kind == "date" and segmented_date_record_valid(field) else ["segmented_date_shape_invalid"]
    if kind == "rich_text":
        return [] if all(isinstance(field.get(k), str) for k in ("text", "sanitized_html")) else ["control_shape_invalid"]
    if (not isinstance(field.get("value"), str) or not isinstance(field.get("type"), str)
            or not isinstance(field.get("tag"), str)
            or any(k in field and not isinstance(field[k], str) for k in ("name", "id", "label"))):
        return ["control_shape_invalid"]
    tag, control_type = field["tag"].lower(), field["type"].lower()
    if kind == "checkbox":
        valid = tag == "input" and control_type == "checkbox" and type(field.get("checked")) is bool
    elif kind == "radio_group":
        valid = tag == "input" and control_type == "radio" and type(field.get("checked")) is bool
    elif kind == "selection":
        valid = (tag == "div" and control_type == "custom-combobox") or (tag == "select" and control_type == "select-one")
    elif kind == "brochure":
        valid = tag == "textarea" and control_type == "textarea"
    elif kind == "suburb":
        valid = tag == "input" and control_type == "text"
    else:
        valid = ((tag == "input" and control_type in ("text", "number", "date", "datetime-local", "url", "tel"))
                 or (tag == "textarea" and control_type == "textarea" and kind == "text"))
    return [] if valid else ["control_type_mismatch"]


def _resolve(entry, observations):
    entry["observations"] = deepcopy(observations)
    if not observations:
        entry.update(status="missing", issues=["control_not_observed"])
        return
    kind = entry["kind"]
    if len(observations) > 1 and kind != "radio_group":
        entry.update(status="ambiguous", issues=["multiple_matching_controls"])
        return
    issues = sorted({issue for obs in observations for issue in _control_issues(obs["field"], kind)})
    if issues:
        entry.update(status="unresolved", issues=issues)
        return
    field = observations[0]["field"]
    entry.update(status="observed", issues=[])
    if kind == "radio_group":
        checked = [obs for obs in observations if obs["field"]["checked"]]
        values = [obs["field"]["value"] for obs in observations]
        if len(values) != len(set(values)) or len(checked) > 1:
            entry.update(status="ambiguous", issues=["radio_group_ambiguous"])
        elif not checked:
            entry.update(status="unresolved", issues=["radio_selection_missing"])
        else:
            selected = checked[0]["field"]
            entry.update(value=selected["value"], display_value=selected.get("label", ""),
                         selected_path=checked[0]["path"])
        return
    if kind == "rich_text":
        entry.update(value=field["text"], display_value=field["text"],
                     sanitized_html=field["sanitized_html"], status="unresolved",
                     issues=["rich_text_format_fidelity_unverified"])
        return
    if kind == "checkbox":
        entry["value"] = field["checked"]
        entry["control_value"] = field["value"]
        if entry["key"] == "address_hidden":
            entry["issues"] = ["address_visibility_polarity_unverified"]
            entry["status"] = "unresolved"
        return
    entry.update(value=field["value"], display_value=field["value"])
    if kind == "date" and is_segmented_date_record(field):
        data = field["date_picker"]
        entry.update(status="unresolved", issues=list(DATE_UNRESOLVED_ISSUES),
                     date_parts=date_observed_parts(field),
                     date_semantics_verified=False, date_business_validation_verified=False,
                     date_requiredness={"verified": False, "label_text": data["label"]["text_content"],
                         "input_required_attribute": data["input"]["required_attribute"],
                         "input_required_property": data["input"]["required_property"],
                         "input_aria_required": data["input"]["aria_required"],
                         "group_aria_required": data["group"]["aria_required"]})
    if kind == "suburb":
        entry.update(status="unresolved", issues=["suburb_selected_entity_id_unresolved"])
    if kind == "selection":
        options = field.get("selected_options")
        entry["selected_options"] = deepcopy(options)
        if not isinstance(options, list) or any(not isinstance(o, dict) or not isinstance(o.get("value"), str)
                                               or not isinstance(o.get("text"), str) for o in options):
            entry.update(status="unresolved", issues=["selected_options_malformed"])
        elif len(options) > 1:
            entry.update(status="ambiguous", issues=["multiple_selected_options"])
        elif not options:
            entry.update(status="unresolved", issues=["selected_option_unresolved"])
        else:
            option = options[0]
            custom = field["type"].lower() == "custom-combobox"
            if field["value"] != option["text" if custom else "value"]:
                entry.update(status="unresolved", issues=["selection_value_conflict"])
            elif custom and field.get("observed_business_selection_code") is not True:
                entry.update(status="unresolved", issues=["selection_code_provenance_missing"])
            else:
                entry.update(selected_code=option["value"], display_value=option["text"])
                entry["issues"] = ["option_resolution_unverified"]
        if entry["status"] in ("ambiguous", "unresolved"):
            # The raw value and options remain in observations, but a disputed
            # selection must not be mistaken for a chosen copy value.
            entry.pop("value", None)
            entry.pop("display_value", None)


def extract_source_fields(capture: Any, *, expected_listing_id: str | None = None,
                          expected_rxcu: str | None = None) -> dict:
    """Return a detached JSON-compatible field-by-field copy/review inventory.

    ``entries`` has stable keys, labels, page and kind, a status, issues, and
    ``observations`` containing the full original ``field`` plus its capture
    path, page hash, number and timestamp. A chosen ``value`` is present only
    where the control value can be read unambiguously. Combo values stay as
    captured; ``selected_code`` and ``selected_options`` retain code/display
    pairs. Suburb text is never promoted to a selected entity ID. Checkbox
    values are their raw checked booleans, including ``address_hidden``.

    Only one capture is accepted. Mixed/unguarded/contradictory histories are
    rejected, even with recomputed checksums. The highest observation number
    per page is used after validating chronological order; missing fields are
    never filled from older pages. ``unknowns`` includes every unmapped control
    from selected pages and open dialogs. Earlier observations remain in the
    input capture and are explicitly listed under ``superseded_pages``.

    Validation failures return ``status='rejected'`` with static issue codes,
    no source values, and unresolved entries. Rejection does not throw private
    payloads into exception messages. All results remain incomplete/non-live.
    """
    result = _blank_result()
    error = _validate(capture, expected_listing_id, expected_rxcu)
    if error:
        return _issue(result, error)
    result["status"] = "partial"
    result["capture_status"] = capture["status"]
    if capture["status"] != "partial":
        result["gaps"].append({"code": "capture_interrupted"})
    result["source"] = {"listing_id": capture["listing_id"], "rxcu": capture["matched_source"]["rxcu"],
                        "capture_sha256": capture["integrity"]["sha256"]}
    result["pending_room_observation_count"] = len(capture.get("pending_room_observations", []))
    if result["pending_room_observation_count"]:
        # Interrupted or unverified close observations are not part of the
        # guarded source. Report the gap without copying or mapping their data.
        result["gaps"].append({"code": "pending_room_observations_excluded",
                               "count": result["pending_room_observation_count"]})
    latest = {}
    for index, page in enumerate(capture["pages"]):
        key = page["page_key"]
        if key in latest:
            old_index, old_page = latest[key]
            result["superseded_pages"].append(_page_meta(old_page, old_index))
        latest[key] = index, page
    specs = _specs()
    pools = {}
    for page_key in EDITOR_PAGES:
        if page_key not in latest:
            result["gaps"].append({"code": "page_not_observed", "page": page_key})
            continue
        index, page = latest[page_key]
        meta = _page_meta(page, index)
        result["selected_pages"].append(meta)
        controls = []
        def collect(items, suffix, rich=False, dialog=False):
            for control_index, field in enumerate(items):
                obs = {**deepcopy(meta), "path": f"pages/{index}/{suffix}/{control_index}", "field": deepcopy(field)}
                controls.append((obs, rich, dialog))
        collect(page["fields"], "fields")
        collect(page["rich_text"], "rich_text", rich=True)
        for dialog_index, dialog in enumerate(page["dialogs"]):
            result["related_records"].append({**deepcopy(meta),
                "path": f"pages/{index}/dialogs/{dialog_index}", "kind": "dialog",
                "status": "unresolved", "record": deepcopy(dialog),
                "issues": ["dialog_record_mapping_unverified"]})
            collect(dialog["fields"], f"dialogs/{dialog_index}/fields", dialog=True)
            collect(dialog["rich_text"], f"dialogs/{dialog_index}/rich_text", rich=True, dialog=True)
        pools[page_key] = controls
    used = set()
    for spec, entry in zip(specs, result["entries"]):
        matches = [obs for obs, rich, dialog in pools.get(spec["page"], [])
                   if not dialog and _matches(spec, obs["field"], rich)]
        _resolve(entry, matches)
        for obs in matches:
            used.add(obs["path"])
        # A control cannot stand for two source fields even if its name and ID
        # individually match known identifiers. Preserve both candidate rows.
        if any(sum(_matches(s, obs["field"], spec["kind"] == "rich_text")
                   for s in specs if s["page"] == spec["page"]) > 1 for obs in matches):
            entry.update(status="ambiguous", issues=["conflicting_control_identifiers"])
            for key in ("value", "display_value", "selected_code", "selected_path", "control_value", "date_parts", "date_requiredness"):
                entry.pop(key, None)
        for code in entry["issues"]:
            result["gaps"].append({"code": code, "field_key": entry["key"], "page": entry["page"]})
    for controls in pools.values():
        for obs, rich, dialog in controls:
            if obs["path"] not in used:
                result["unknowns"].append({**obs, "reason": "dialog_control_unmapped" if dialog else "control_unmapped",
                                           "kind": "rich_text" if rich else "control"})
    for code in ("historical_values_not_rechecked", "cross_page_revision_unverified",
                 "required_fields_and_listing_branch_unverified", "account_ownership_unverified",
                 "nested_dialogs_and_related_records_unverified", "media_bytes_and_order_unverified",
                 "portal_choices_and_statuses_unverified"):
        result["gaps"].append({"code": code})
    if result["unknowns"]:
        result["gaps"].append({"code": "unmapped_controls_present"})
    result["capture_limitations"] = deepcopy(capture["limitations"])
    for code in result["capture_limitations"]:
        result["gaps"].append({"code": "capture_limitation", "limitation": code})
    return result
