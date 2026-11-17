"""Detached, read-only bridge from two pinned captures to form-copy candidates.

No request, browser, persistence, approved override, or execution is produced.
Pins detect accidental substitution, not authenticity or live freshness. Both
listing IDs and RXCU references must be distinct and supplied explicitly. All
values/provenance may be private: keep the result local and do not log it.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any

from .date_capture import DATE_NAMES, DATE_UNRESOLVED_ISSUES
from .hub_form_writer import TEXT_FIELDS, _kind, _validate_value, TransferRejected
from .source_fields import extract_source_fields

SCHEMA_VERSION = "hub-transfer-plan/v1"
MAX_CAPTURE_BYTES = 4_000_000
MAX_PLAN_BYTES = 32_000_000
_GLOBAL_BLOCKERS = [
    "historical_values_not_rechecked", "account_ownership_unverified",
    "source_and_target_server_revisions_unknown", "replacement_relationship_unverified",
    "source_completeness_unverified", "creation_required_fields_unqualified",
    "live_control_structure_unqualified", "persistence_unverified",
]


def _canonical(value):
    # Python considers True == 1; JSON types must remain distinct.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reference(value):
    return value.replace("-", "") if isinstance(value, str) and re.fullmatch(r"RXCU-?[0-9]{3,20}", value) else None


def _typed(entry):
    """Return only exact handler-shaped observations, never inferred values."""
    allowed = ({"address_visibility_polarity_unverified"} if entry["key"] == "address_hidden"
               else set(DATE_UNRESOLVED_ISSUES) if entry["key"] in DATE_NAMES else None)
    if entry["status"] != "observed" and not (
            entry["status"] == "unresolved" and allowed is not None and set(entry["issues"]) == allowed):
        return None
    if "value" not in entry:
        return None
    if entry["kind"] == "selection":
        return {"code": entry["selected_code"], "text": entry["display_value"]}
    if entry["kind"] == "checkbox":
        return {"checked": entry["value"], "control_value": entry["control_value"]}
    if entry["kind"] == "radio_group":
        return [{"code": o["field"]["value"], "checked": o["field"]["checked"]} for o in entry["observations"]]
    if entry["kind"] == "date":
        return {"raw": entry["value"], "parts": entry["date_parts"]} if "date_parts" in entry else None
    return entry["value"]


def _shape_issues(entry, writer_kind, writer_key, *, target):
    fields = [o["field"] for o in entry["observations"]]
    issues = []
    for f in fields:
        if target and (f.get("disabled") is not False or f.get("read_only") is not False):
            issues.append("control_editability_unverified")
        tag, typ = f.get("tag"), f.get("type")
        name, identifier = f.get("name"), f.get("id")
        if writer_kind in ("text", "count") or writer_key == "marketing_header":
            if (isinstance(name, str) and name and name != writer_key
                    or name == writer_key and identifier in TEXT_FIELDS[entry["page"]] and identifier != writer_key):
                issues.append("writer_control_identifier_conflict")
        if writer_key == "marketing_header" and (name != writer_key or identifier != writer_key):
            issues.append("writer_control_identifier_conflict")
        if writer_kind == "count" and not name:
            # Capture records the name property; '' cannot distinguish an absent
            # attribute from name="". The writer requires actual absence.
            issues.append("count_name_attribute_absence_unverified")
        if writer_kind == "combo" and (tag, typ) != ("div", "custom-combobox"):
            issues.append("writer_control_shape_unsupported")
        if writer_kind == "count" and ((tag, typ) != ("input", "text") or f.get("name")):
            issues.append("writer_control_shape_unsupported")
        if writer_kind == "text" and not (tag == "textarea" or tag == "input" and typ in ("text", "number", "url", "tel")):
            issues.append("writer_control_shape_unsupported")
        if writer_kind == "textarea":
            expected_id = "marketing_header" if entry["key"] == "marketing_header" else "outlined-multiline-flexible"
            expected_label = "Marketing header description" if entry["key"] == "marketing_header" else "Brochure Description"
            if (tag, typ, f.get("id"), f.get("label")) != ("textarea", "textarea", expected_id, expected_label):
                issues.append("writer_control_shape_unsupported")
    if writer_kind == "radio" and len(fields) != 5:
        issues.append("writer_radio_members_unsupported")
    return sorted(set(issues))


def _row(source, target):
    key = source["key"]
    writer_key = "feature-" + key[len("feature_"):] + "-input" if key.startswith("feature_") else key
    row = {"key": key, "page": source["page"], "writer_key": None, "writer_kind": None,
           "status": "blocked", "candidate": None, "baseline": None, "comparison": None,
           "source": source, "target": target, "issues": [], "blockers": [],
           "live_ready": False, "persistence_verified": False}
    for side, entry in (("source", source), ("target", target)):
        row["issues"].extend({"side": side, "code": code} for code in entry["issues"])
    try:
        kind = _kind(writer_key, source["page"])
    except TransferRejected:
        row["blockers"].append("writer_handler_unqualified")
        return row
    row.update(writer_key=writer_key, writer_kind=kind)
    for side, entry, output in (("source", source, "candidate"), ("target", target, "baseline")):
        row["blockers"].extend(side + "_" + issue for issue in
                               _shape_issues(entry, kind, writer_key, target=side == "target"))
        value = _typed(entry)
        if value is None:
            row["blockers"].append(side + "_value_unresolved")
            continue
        row[output] = value
        if side == "source" and any(o["field"].get("disabled") is not False or
                                    o["field"].get("read_only") is not False for o in entry["observations"]):
            row["issues"].append({"side": side, "code": "source_control_editability_unverified"})
        try:
            _validate_value(writer_key, value, source["page"])
        except TransferRejected:
            row["blockers"].append(side + "_writer_value_unsupported")
        row["blockers"].extend(side + "_" + code for code in entry["issues"])
        if any(o["limitations"] for o in entry["observations"]):
            row["blockers"].append(side + "_observation_limitations")
    if row["candidate"] is not None and row["baseline"] is not None:
        a, b = row["candidate"], row["baseline"]
        row["comparison"] = "matched" if _canonical(a) == _canonical(b) else "changed"
        shapes = [[(o["field"].get("tag"), o["field"].get("type")) for o in e["observations"]] for e in (source, target)]
        if shapes[0] != shapes[1]:
            row["blockers"].append("source_target_control_types_differ")
        if kind == "checkbox" and a["control_value"] != b["control_value"]:
            row["blockers"].append("checkbox_control_values_differ")
        if kind == "radio":
            signatures = [[(o["field"]["value"], o["field"].get("label")) for o in e["observations"]] for e in (source, target)]
            if signatures[0] != signatures[1]:
                row["blockers"].append("radio_options_differ")
        if kind == "combo":
            # Captures contain selected options, not an exhaustive target menu.
            row["blockers"].append("target_option_availability_unverified")
            if (a["code"] == b["code"]) != (a["text"] == b["text"]):
                row["blockers"].append("selection_code_label_conflict")
    row["blockers"] = sorted(set(row["blockers"]))
    if not row["blockers"]:
        row["status"] = "candidate_only"
    return row


def build_transfer_plan(source_capture: Any, target_capture: Any, *,
                        expected_source_listing_id: str, expected_source_rxcu: str,
                        expected_source_capture_sha256: str, expected_target_listing_id: str,
                        expected_target_rxcu: str, expected_target_capture_sha256: str) -> dict:
    """Map saved captures using mandatory identity and exact capture pins.

    A candidate is an exact historical value, not an approved change. Baselines
    are historical target observations, not revision checks. No caller-provided
    revision/account fields are accepted. ``candidate_only`` means that a typed
    value pair mapped; global blockers still apply. Missing and unsupported
    inventory rows are retained alongside unknown controls and nested records.
    """
    result = {"schema_version": SCHEMA_VERSION, "status": "rejected", "complete": False,
              "live_ready": False, "persistence_verified": False, "account_verified": False,
              "source_revision": None, "target_revision": None, "source": None, "target": None,
              "groups": {page: [] for page in ("details", "deal_particulars", "media")},
              "inventories": {}, "unresolved_coverage": [], "issues": [],
              "blockers": list(_GLOBAL_BLOCKERS), "approved_overrides": [],
              "warning": "Read-only historical candidates, not a complete copy or executable request. Checksums are not authentication."}
    identities = [(expected_source_listing_id, expected_source_rxcu, expected_source_capture_sha256),
                  (expected_target_listing_id, expected_target_rxcu, expected_target_capture_sha256)]
    if any(not isinstance(i, str) or not re.fullmatch(r"[0-9]{1,128}", i)
           or _reference(r) is None or not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h)
           for i, r, h in identities):
        result["issues"].append("explicit_capture_pins_required")
        return result
    if identities[0][0] == identities[1][0] or _reference(identities[0][1]) == _reference(identities[1][1]):
        result["issues"].append("distinct_source_and_target_required")
        return result
    # Limit detached input and output work independently of collector limits.
    try:
        if any(len(_canonical(c).encode("utf-8")) > MAX_CAPTURE_BYTES for c in (source_capture, target_capture)):
            result["issues"].append("capture_size_limit")
            return result
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        result["issues"].append("capture_malformed")
        return result
    inventories = []
    for side, capture, (listing_id, rxcu, digest) in zip(("source", "target"), (source_capture, target_capture), identities):
        try:
            inventory = extract_source_fields(capture, expected_listing_id=listing_id, expected_rxcu=rxcu)
        except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
            result["issues"].append({"side": side, "code": "capture_malformed"})
            return result
        if inventory["status"] == "rejected":
            result["issues"].append({"side": side, "code": "capture_rejected", "details": inventory["issues"]})
        elif inventory["source"]["capture_sha256"] != digest:
            result["issues"].append({"side": side, "code": "capture_pin_mismatch"})
        inventories.append(inventory)
    if result["issues"]:
        return result  # Do not expose candidate data from a rejected pair.
    result["status"] = "partial_read_only"
    for side, inventory in zip(("source", "target"), inventories):
        result[side] = inventory["source"]
        result["inventories"][side] = inventory
        for name in ("unknowns", "related_records", "gaps", "superseded_pages"):
            result["unresolved_coverage"].extend({"side": side, "kind": name, "observation": item} for item in inventory[name])
        if inventory["capture_status"] != "partial":
            result["blockers"].append(side + "_capture_interrupted")
        if inventory["capture_limitations"]:
            result["blockers"].append(side + "_capture_limitations")
    target_entries = {e["key"]: e for e in inventories[1]["entries"]}
    for entry in inventories[0]["entries"]:
        row = _row(entry, target_entries[entry["key"]])
        for side, inventory in zip(("source", "target"), inventories):
            if inventory["capture_limitations"]:
                row["blockers"].append(side + "_capture_limitations")
                row["status"] = "blocked"
        result["groups"][entry["page"]].append(row)
    if len(_canonical(result).encode("utf-8")) > MAX_PLAN_BYTES:
        result.update(status="rejected", source=None, target=None, inventories={},
                      groups={page: [] for page in result["groups"]}, unresolved_coverage=[],
                      issues=["plan_size_limit"])
    return deepcopy(result)
