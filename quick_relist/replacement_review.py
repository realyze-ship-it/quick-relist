"""Local, read-only comparison of two explicitly identified partial captures.

This does not establish a complete copy or a relationship between the listings.
It neither imports nor calls Engine, a browser, a live adapter, or persistence.
All output can contain private business data; keep it local and do not log it.
Integrity receipts detect accidental corruption, not authenticity.

``compare_replacement_fields`` returns detached JSON-compatible data. Listing
``entries`` and the entries inside ``room_groups`` have five possible statuses:
matched, changed, missing, ambiguous, unresolved. Each retains its original and
replacement inventory rows, including exact controls and capture provenance.
``missing_in`` identifies the absent side(s); a blank string or False is not
missing. For safely readable but semantically unresolved values (suburb text,
raw address visibility, sanitized rich text), ``observed_comparison`` exposes
matched/changed without upgrading the row's unresolved status.

Room alignment is positional within one observed category, never across rooms.
Only the small observed control vocabulary below is compared. Unknown controls,
unqualified dialogs and other coverage stay unresolved. Pending dialog contents
are excluded entirely. Equal observations are not evidence of a complete copy.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
import re
from typing import Any

from .capture import _digest
from .source_fields import extract_source_fields

SCHEMA_VERSION = "hub-replacement-review/v1"
WARNING = ("Historical partial observations only. Equal fields do not prove a complete copy. "
           "No difference is approved; this review cannot authorize relisting or publication.")
_STATUSES = ("matched", "changed", "missing", "ambiguous", "unresolved")
# Same five observed categories as room_capture.ROOM_OPTIONS. These are data
# labels, not mutation selectors; do not infer new categories from page content.
_ROOMS = {
    "bedroom": ("Set bedrooms options", "Bedroom"),
    "bathroom": ("Set bathrooms options", "Bathroom"),
    "kitchen": ("Set kitchens options", "Kitchen"),
    "parking": ("Set parkings options", "Parking"),
    "lounge": ("Set lounges options", "Lounge"),
}
# Sanitized observed schema, not a complete or authoritative Hub schema. Keep
# category-specific names scoped, especially kitchen is_open_plan versus the
# common has_open_plan. Unknown or misplaced names are not inferred matches.
_COMMON_ROOM_FIELDS = (
    "has_air_conditioner", "has_atrium", "has_balcony", "has_blinds", "has_curtain_rails",
    "has_fan", "has_fireplace", "has_internet_port", "has_linen_room", "has_open_plan",
    "has_patio", "has_recreation_room", "has_strong_room", "has_telephone_port",
    "has_tv_port", "has_veranda", "has_carpets", "has_laminated_floors",
    "has_parquet_floors", "has_tiled_floors", "has_underfloor_heating",
    "has_vinyl_floors", "has_wooden_floors",
)
_CATEGORY_ROOM_FIELDS = {
    "bedroom": ("has_built_in_cupboards", "has_double_bed", "has_king_bed",
                "has_queen_bed", "has_single_bed", "has_walk_in_closet"),
    "bathroom": ("has_basin", "has_bath", "has_bath_toilet_basin", "has_bidet",
                 "has_common_toilet", "is_domestic_bathroom", "has_double_basin",
                 "is_en_suite", "is_full", "is_guest_toilet", "has_jacuzzi_bath",
                 "is_main_en_suite", "is_separate_toilet", "has_shower",
                 "has_shower_toilet_basin", "has_toilet", "has_urinal"),
    "kitchen": ("has_breakfast_nook", "has_center_island", "is_country_style",
                "has_dishwasher_connection", "has_extractor_fan", "has_eye_level_oven",
                "has_fridge", "has_garbage_disposal", "has_gas_hob", "has_gas_oven",
                "has_granite_tops", "has_grill", "has_hob", "has_icemaker", "has_laundry",
                "is_open_plan", "has_pantry", "has_piped_gas", "has_pizza_oven", "has_stove",
                "has_stove_oven_and_hob", "has_tumble_dryer", "has_under_counter_oven",
                "has_washing_machine_connection"),
    "parking": ("is_carport_parking", "is_double_parking", "is_on_street_parking",
                "is_secure_parking", "is_shade_net_covered_parking", "is_single_parking",
                "is_tandem_parking", "is_triple_parking", "is_underground_parking",
                "is_visitors_parking"),
    "lounge": (),
}
_ROOM_FIELDS = {name: "checkbox" for name in _COMMON_ROOM_FIELDS +
                tuple(name for names in _CATEGORY_ROOM_FIELDS.values() for name in names)}
_ROOM_FIELDS["description"] = "description"
_RAW_UNRESOLVED = {
    "address_hidden": {"address_visibility_polarity_unverified"},
    "suburb": {"suburb_selected_entity_id_unresolved"},
    "rich_text_description": {"rich_text_format_fidelity_unverified"},
}
_VALUE_KEYS = ("value", "display_value", "selected_code", "selected_options",
               "control_value", "sanitized_html")
_LIMITATIONS = (
    "incomplete_source_and_replacement", "equal_observations_not_complete_copy",
    "replacement_relationship_unverified", "historical_values_not_rechecked",
    "account_ownership_unverified", "cross_page_revision_unverified",
    "capture_timestamps_not_server_chronology", "required_fields_and_listing_branch_unverified",
    "formatting_fidelity_unverified", "media_bytes_order_captions_cover_and_completeness_unverified",
    "portal_choices_and_statuses_unverified", "nested_dialogs_and_related_records_unverified",
    "room_schema_and_positional_alignment_unverified", "no_differences_approved",
)


def _reference(value):
    return value.replace("-", "") if isinstance(value, str) and re.fullmatch(r"RXCU-?[0-9]{3,20}", value) else None


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _blank():
    return {"schema_version": SCHEMA_VERSION, "status": "rejected", "complete": False,
            "fidelity_verified": False, "publish_ready": False, "live_ready": False,
            "original": None, "replacement": None, "entries": [], "room_groups": [],
            "unresolved_coverage": [], "issues": [], "chronology": None,
            "limitations": list(_LIMITATIONS), "warning": WARNING,
            "summary": {**{status: 0 for status in _STATUSES}, "unresolved_coverage": 0}}


def _rejected(code, side=None, details=()):
    result = _blank()
    result["issues"].append({"code": code, **({"side": side} if side else {}),
                             "details": list(details)})
    return result


def _can_compare(entry):
    if entry["status"] == "observed":
        return "value" in entry
    allowed = _RAW_UNRESOLVED.get(entry["key"])
    return (entry["status"] == "unresolved" and allowed is not None
            and set(entry["issues"]) == allowed and "value" in entry)


def _compare(original, replacement, scope):
    """Compare only explicitly extracted values, never control metadata or paths."""
    states = (original["status"], replacement["status"])
    row = {key: original[key] for key in ("key", "label", "page", "kind")}
    row.update(scope=deepcopy(scope), original=deepcopy(original), replacement=deepcopy(replacement),
               status="unresolved", observed_comparison=None, missing_in=[], issues=[])
    row["missing_in"] = [side for side, state in zip(("original", "replacement"), states) if state == "missing"]
    for side, entry in (("original", original), ("replacement", replacement)):
        row["issues"].extend({"side": side, "code": issue} for issue in entry["issues"])
    if "ambiguous" in states:
        row["status"] = "ambiguous"
    elif "unresolved" in states:
        row["status"] = "unresolved"
    elif "missing" in states:
        row["status"] = "missing"
    if _can_compare(original) and _can_compare(replacement):
        # No float/date coercion, trimming, case-folding, checkbox inversion,
        # label-only selection match, or inferred default is permissible here.
        values = [{key: entry[key] for key in _VALUE_KEYS if key in entry}
                  for entry in (original, replacement)]
        outcome = "matched" if values[0] == values[1] else "changed"
        row["observed_comparison"] = outcome
        if states == ("observed", "observed"):
            row["status"] = outcome
    return row


def _empty_room_entry(key, status="missing", issues=None):
    return {"key": key, "label": key, "page": "details", "kind": _ROOM_FIELDS[key],
            "status": status, "observations": [],
            "issues": list(issues or ["room_control_not_observed"])}


def _room_keys(category):
    return _COMMON_ROOM_FIELDS + _CATEGORY_ROOM_FIELDS[category] + ("description",)


def _coverage(result, side, kind, issue, observation, scope=None, status="unresolved"):
    result["unresolved_coverage"].append({"side": side, "kind": kind, "status": status,
        "scope": deepcopy(scope), "issues": [issue], "observation": deepcopy(observation)})


def _room_entries(record, group, group_index, side, category, result):
    scope = {"kind": "room", "category": category, "index": group["index"]}
    meta = {key: deepcopy(record[key]) for key in
            ("page_key", "observation_number", "observed_at", "page_sha256", "limitations")}
    path = record["path"] + f"/room_groups/{group_index}"
    candidates = {}
    for index, field in enumerate(group["fields"]):
        obs = {**deepcopy(meta), "path": f"{path}/fields/{index}", "field": deepcopy(field),
               "room_source": deepcopy(record["record"]["source"]), "scope": deepcopy(scope)}
        keys = [key for key in _room_keys(category) if field.get("name") == key or field.get("id") == key]
        if not keys:
            _coverage(result, side, "room_control", "room_control_unmapped", obs, scope)
        for key in keys:
            candidates.setdefault(key, []).append((obs, len(keys) > 1))
    for index, field in enumerate(group["rich_text"]):
        _coverage(result, side, "room_rich_text", "room_rich_text_unmapped",
                  {**deepcopy(meta), "path": f"{path}/rich_text/{index}", "field": field}, scope)
    entries = {}
    for key, matches in candidates.items():
        entry = _empty_room_entry(key)
        entry["observations"] = [deepcopy(obs) for obs, _ in matches]
        entries[key] = entry
        if len(matches) > 1 or any(conflict for _, conflict in matches):
            entry.update(status="ambiguous", issues=["room_control_ambiguous"])
            continue
        field = matches[0][0]["field"]
        valid = (isinstance(field.get("value"), str)
                 and all(isinstance(field.get(k, ""), str) for k in ("name", "id", "label")))
        if _ROOM_FIELDS[key] == "checkbox":
            valid = valid and field.get("tag") == "input" and field.get("type") == "checkbox" and type(field.get("checked")) is bool
        else:
            valid = valid and field.get("tag") == "textarea" and field.get("type") == "textarea"
        if not valid:
            entry.update(status="unresolved", issues=["room_control_shape_invalid"])
            continue
        entry.update(status="observed", issues=[])
        if _ROOM_FIELDS[key] == "checkbox":
            entry.update(value=field["checked"], control_value=field["value"])
        else:
            entry.update(value=field["value"], display_value=field["value"])
    return {"scope": scope, "path": path, "title": group["title"], "entries": entries,
            "source": deepcopy(record["record"]["source"]), "provenance": meta,
            "record": deepcopy(group)}


def _room_guard(record, category, inventory, capture):
    dialog, (title, noun) = record["record"], _ROOMS[category]
    source, groups = dialog.get("source"), dialog.get("room_groups")
    if not isinstance(source, dict):
        return "room_dialog_guard_missing"
    count, stable = source.get("feature_count"), source.get("dom_stability_seconds")
    if (record["page_key"] != "details" or source.get("page_key") != "details"
            or source.get("opener") != title or source.get("title") != title
            or any(source.get(key) is not True for key in
                   ("reference_checked_before_and_after", "pinned_tab_checked",
                    "main_fields_unchanged_verified", "close_verified"))
            or type(stable) not in (int, float) or not math.isfinite(stable) or stable <= 0
            or _time(source.get("observed_at")) is None):
        return "room_dialog_guard_missing"
    page = capture["pages"][int(record["path"].split("/")[1])]
    if source.get("main_fields_sha256") != _digest({key: page[key] for key in ("fields", "rich_text")}):
        return "room_main_fields_binding_missing"
    if _time(source["observed_at"]) < _time(page["observed_at"]):
        return "room_observation_order_invalid"
    limitations = source.get("limitations")
    if (not isinstance(limitations, list) or any(not isinstance(code, str) for code in limitations)
            or "room_group_structure_unresolved" in limitations + record["limitations"]):
        return "room_group_structure_unresolved"
    if (type(count) is not int or count <= 0 or not isinstance(groups, list) or len(groups) != count
            or any(group["index"] != index or group["title"] != f"{noun} {index}"
                   for index, group in enumerate(groups, 1))):
        return "room_group_structure_unresolved"
    count_entry = next(e for e in inventory["entries"] if e["key"] == "feature_" + category)
    raw_count = count_entry.get("value")
    if (count_entry["status"] != "observed" or not isinstance(raw_count, str)
            or not re.fullmatch(r"[0-9]{1,15}", raw_count) or int(raw_count) != count):
        return "room_count_binding_unresolved"
    return None


def _rooms(capture, inventory, side, result):
    """Return individually guarded groups and category-level uncertainty."""
    groups, blocked, categories = {}, {}, {}
    for record in inventory["related_records"]:
        title = record["record"].get("title")
        category = next((key for key, (caption, _) in _ROOMS.items() if title == caption), None)
        if category is None:
            _coverage(result, side, "dialog", "dialog_category_unmapped", record)
        else:
            categories.setdefault(category, []).append(record)
    for category, records in categories.items():
        if len(records) != 1:
            blocked[category] = ("ambiguous", ["multiple_room_dialogs"])
            for record in records:
                _coverage(result, side, "dialog", "multiple_room_dialogs", record,
                          {"kind": "room", "category": category}, "ambiguous")
            continue
        record = records[0]
        issue = _room_guard(record, category, inventory, capture)
        if issue:
            blocked[category] = ("unresolved", [issue])
            _coverage(result, side, "dialog", issue, record, {"kind": "room", "category": category})
            continue
        for index, group in enumerate(record["record"]["room_groups"]):
            groups[(category, group["index"])] = _room_entries(record, group, index, side, category, result)
    return groups, blocked


def _side_meta(inventory):
    return {key: deepcopy(inventory[key]) for key in
            ("source", "capture_status", "selected_pages", "superseded_pages", "capture_limitations",
             "gaps", "pending_room_observation_count")} | {"represented_dialog_observations": []}


def _unknown_coverage(inventory, groups, side, result):
    """Account for duplicate flat/group reads, not cross-room control identity.

    fieldsFor/richFor restart dom_order in each scope. Compare every other raw
    property and require equal multiplicity in that *same qualified dialog*.
    Keep all original flat records. Multiple identical grouped controls are
    listed as a set of paths; none is assigned to any particular flat control.
    An extra identical flat control makes the entire bucket unresolved rather
    than arbitrarily choosing which observation is the extra one.
    """
    grouped, flat = {}, {}
    def signature(field):
        return _digest({key: value for key, value in field.items() if key != "dom_order"})
    for group in groups.values():
        dialog_path = group["path"].split("/room_groups/", 1)[0]
        for collection, kind in (("fields", "control"), ("rich_text", "rich_text")):
            for index, field in enumerate(group["record"][collection]):
                key = (dialog_path, kind, signature(field))
                grouped.setdefault(key, []).append(f"{group['path']}/{collection}/{index}")
    for unknown in inventory["unknowns"]:
        path = unknown["path"].rsplit("/", 2)[0]
        key = (path, unknown["kind"], signature(unknown["field"]))
        flat.setdefault(key, []).append(unknown)
    for key, observations in flat.items():
        represented = grouped.get(key, [])
        if represented and len(represented) == len(observations):
            result[side]["represented_dialog_observations"].extend({
                "reason": "flat_dialog_observation_duplicated_in_groups",
                "group_paths": list(represented), "room_identity_assigned": False,
                "observation": deepcopy(observation)} for observation in observations)
        else:
            for observation in observations:
                issue = "flat_dialog_group_coverage_ambiguous" if represented else observation["reason"]
                _coverage(result, side, observation["kind"], issue, observation)


def compare_replacement_fields(original_capture: Any, replacement_capture: Any, *,
                               expected_original_rxcu: str, expected_replacement_rxcu: str,
                               expected_original_listing_id: str | None = None,
                               expected_replacement_listing_id: str | None = None) -> dict:
    """Compare two local captures, requiring distinct expected and matched RXCUs.

    Optional listing IDs further pin the expected jobs. Both captured numeric
    IDs must be distinct even when RXCUs differ. Rejection returns only static
    diagnostic codes and no capture values. All flags remain false, including
    when every comparable observed value matches. No difference is approved.

    ``original``/``replacement`` contain source identity, selected/superseded
    page metadata, limitations, gaps and excluded pending-dialog counts.
    They also retain ``represented_dialog_observations``: duplicate flat reads
    with equal raw-data multiplicity in qualified groups, not room identities.
    ``summary`` counts listing and room *field* statuses plus coverage records.
    ``room_groups`` keeps category/index scope and original group provenance.
    Room matching is a comparison of positional observations, not entity IDs.
    """
    refs = (_reference(expected_original_rxcu), _reference(expected_replacement_rxcu))
    if any(ref is None for ref in refs):
        return _rejected("expected_references_required")
    if refs[0] == refs[1]:
        return _rejected("distinct_listing_references_required")
    inventories = []
    for side, capture, rxcu, listing_id in (
            ("original", original_capture, expected_original_rxcu, expected_original_listing_id),
            ("replacement", replacement_capture, expected_replacement_rxcu, expected_replacement_listing_id)):
        if listing_id is not None and (not isinstance(listing_id, str) or not re.fullmatch(r"[0-9]{1,128}", listing_id)):
            return _rejected("expected_listing_id_invalid", side)
        try:
            inventory = extract_source_fields(capture, expected_rxcu=rxcu, expected_listing_id=listing_id)
        except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError):
            # Defensive boundary for malformed shapes. Never echo an exception.
            return _rejected("capture_validation_failed", side)
        if inventory["status"] == "rejected":
            return _rejected("capture_rejected", side, inventory["issues"])
        pending = capture.get("pending_room_observations", [])
        if any(dialog in pending for page in capture["pages"] for dialog in page["dialogs"]):
            return _rejected("pending_room_observation_conflict", side)
        inventories.append(inventory)
    original, replacement = inventories
    if int(original["source"]["listing_id"]) == int(replacement["source"]["listing_id"]):
        return _rejected("distinct_listing_ids_required")
    result = _blank()
    result.update(status="partial", original=_side_meta(original), replacement=_side_meta(replacement))
    result["entries"] = [_compare(a, b, {"kind": "listing"})
                         for a, b in zip(original["entries"], replacement["entries"])]
    rooms = []
    for side, capture, inventory in (("original", original_capture, original),
                                     ("replacement", replacement_capture, replacement)):
        room_data = _rooms(capture, inventory, side, result)
        rooms.append(room_data)
        _unknown_coverage(inventory, room_data[0], side, result)
        if inventory["pending_room_observation_count"]:
            _coverage(result, side, "pending_dialogs", "pending_room_observations_excluded",
                      {"count": inventory["pending_room_observation_count"]})
    (original_rooms, original_blocked), (replacement_rooms, replacement_blocked) = rooms
    for category, index in sorted(original_rooms.keys() | replacement_rooms.keys()):
        a, b = original_rooms.get((category, index)), replacement_rooms.get((category, index))
        scope = {"kind": "room", "category": category, "index": index}
        rows = []
        for key in _room_keys(category):
            # Known schema coverage remains visible even when neither capture
            # observed a control. Missing does not mean required or applicable.
            a_entry = a["entries"].get(key) if a else None
            b_entry = b["entries"].get(key) if b else None
            a_entry = a_entry or _empty_room_entry(key, *original_blocked.get(category, ("missing", None)))
            b_entry = b_entry or _empty_room_entry(key, *replacement_blocked.get(category, ("missing", None)))
            rows.append(_compare(a_entry, b_entry, scope))
        result["room_groups"].append({"scope": scope, "original": deepcopy(a), "replacement": deepcopy(b),
            "entries": rows, "issues": ["room_positional_alignment_unverified"],
            "missing_in": [side for side, group in (("original", a), ("replacement", b)) if group is None]})
    # Local capture times describe observation order only, not save/create order.
    windows = []
    for capture, groups in ((original_capture, original_rooms), (replacement_capture, replacement_rooms)):
        times = [_time(page["observed_at"]) for page in capture["pages"]]
        # A qualified dialog is read after its parent page, sometimes after
        # that capture's final page timestamp. Do not shorten its time window.
        times.extend(_time(group["source"]["observed_at"]) for group in groups.values())
        windows.append((min(times), max(times)))
    chronology = "replacement_observed_after_original"
    if windows[1][1] < windows[0][0]:
        chronology = "replacement_observed_before_original"
    elif windows[1][0] <= windows[0][1]:
        chronology = "observation_windows_overlap"
    result["chronology"] = {"status": chronology, "server_chronology_verified": False}
    for row in result["entries"] + [row for group in result["room_groups"] for row in group["entries"]]:
        result["summary"][row["status"]] += 1
    result["summary"]["unresolved_coverage"] = len(result["unresolved_coverage"])
    return result
