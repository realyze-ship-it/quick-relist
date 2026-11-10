"""Synthetic offline fixtures only. No live property/date data."""
from copy import deepcopy
from html import escape

from quick_relist.capture import SCHEMA_VERSION, _digest
from quick_relist.date_capture import DATE_NAMES, DATE_LABELS, DATE_SCHEMA, DATE_TYPE

RAW_DATES = ("2040-01-02", " 2041/03/04 ", "05.06.2042", "2043-07-08T09:10:11+02:00", "\u200e2044-09-10")


def picker_html(index=0, raw=None, *, name=None):
    name = DATE_NAMES[index] if name is None else name
    raw = RAW_DATES[index] if raw is None else raw
    label = DATE_LABELS[index] + ("*" if index in (2, 3) else "")
    parts = []
    for position, (part, value, text) in enumerate((("Year", "2099", "2099"), ("Month", "11", "November"), ("Day", "19", "19th"))):
        value_text = '' if position == 0 else f' aria-valuetext="{text}"'
        parts.append(f'''<span class="MuiPickersSectionList-section"><span class="MuiPickersSectionList-sectionContent MuiPickersInputBase-sectionContent" role="spinbutton" contenteditable="true" aria-label="{part}" aria-valuenow="{value}"{value_text} aria-readonly="false" aria-disabled="false" tabindex="{0 if position == 0 else -1}">{text}</span></span>''')
    return f'''<div class="MuiFormControl-root MuiPickersTextField-root" data-fixture="date-{index}">
<label id="fixture-label-{index}" for="fixture-input-{index}">{label}</label>
<div class="MuiPickersInputBase-root MuiPickersOutlinedInput-root" role="group" aria-labelledby="fixture-label-{index}">
<div class="MuiPickersSectionList-root MuiPickersInputBase-sectionsContainer">{''.join(parts)}</div>
<button type="button" aria-label="Choose date">Calendar (synthetic)</button>
<input id="fixture-input-{index}" name="{escape(name, quote=True)}" aria-hidden="true" tabindex="-1" class="MuiPickersInputBase-input MuiPickersOutlinedInput-input" value="{escape(raw, quote=True)}">
</div></div>'''


def fixture_html():
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic segmented date capture fixture</title>
<style>body{font:16px system-ui} .MuiPickersTextField-root{margin:16px;padding:12px;border:1px solid #bbb}
.MuiPickersInputBase-input{position:absolute;opacity:0;pointer-events:none;width:1px;height:1px}
.MuiPickersSectionList-section{display:inline-block;margin-right:12px}</style>
<main><h1>Synthetic date observations</h1><p>Offline fixture only. Raw inputs deliberately differ from segments. No calendar logic or network.</p>''' + ''.join(picker_html(i) for i in range(5)) + '''
<label for="ordinary-date">Ordinary native date</label><input id="ordinary-date" type="date" value="2045-12-31">
<div contenteditable="true" aria-label="Unrelated editable">Retain this observation</div></main></html>'''


def date_record(index=0, raw=None):
    name = DATE_NAMES[index]
    label = DATE_LABELS[index] + ("*" if index in (2, 3) else "")
    raw = RAW_DATES[index] if raw is None else raw
    parts = []
    for position, (part, value, text) in enumerate((("Year", "2099", "2099"), ("Month", "11", "November"), ("Day", "19", "19th"))):
        parts.append({"dom_order": position, "tag": "span", "role": "spinbutton",
            "classes": ["MuiPickersSectionList-sectionContent", "MuiPickersInputBase-sectionContent"],
            "parent_tag": "span", "parent_classes": ["MuiPickersSectionList-section"],
            "contenteditable": "true", "aria_label": part, "aria_labelledby": None, "aria_hidden": None, "style": None,
            "aria_readonly": "false", "aria_disabled": "false", "tabindex": "0" if position == 0 else "-1",
            "aria_valuenow": value, "aria_valuetext": None if position == 0 else text, "text": text,
            "visible": True, "sensitive_context": False, "child_element_count": 0})
    visible = {"visible": True, "sensitive_context": False}
    return {"dom_order": index, "tag": "input", "type": DATE_TYPE, "id": f"fixture-input-{index}", "name": name,
        "label": label, "section": "", "value": raw, "checked": None, "disabled": False, "read_only": False,
        "selected_options": None, "option_count": None,
        "date_picker": {"schema_version": DATE_SCHEMA, "status": "observed_supported", "semantics_verified": False,
            "business_validation_verified": False, "requiredness_verified": False,
            "input": {"tag": "input", "id": f"fixture-input-{index}", "name": name, "type_attribute": None,
                "type_property": "text", "aria_hidden": "true", "tabindex": "-1",
                "classes": ["MuiPickersInputBase-input", "MuiPickersOutlinedInput-input"], "value": raw,
                "disabled": False, "read_only": False, "required_attribute": None, "required_property": False,
                "aria_required": None, "hidden_attribute": None, "sensitive_context": False},
            "label": {"tag": "label", "id": f"fixture-label-{index}", "for": f"fixture-input-{index}",
                "text": label, "text_content": label, **visible},
            "group": {"tag": "div", "role": "group", "classes": ["MuiPickersInputBase-root", "MuiPickersOutlinedInput-root"],
                "aria_labelledby": f"fixture-label-{index}", "aria_describedby": None, "aria_required": None, **visible},
            "wrapper": {"tag": "div", "classes": ["MuiFormControl-root", "MuiPickersTextField-root"], **visible},
            "container": {"tag": "div", "classes": ["MuiPickersSectionList-root", "MuiPickersInputBase-sectionsContainer"], **visible},
            "segments": parts,
            "binding": {"document_named_input_count": 1, "document_input_id_count": 1, "document_label_id_count": 1,
                "document_labels_for_input_count": 1, "wrapper_input_count": 1, "wrapper_label_count": 1,
                "wrapper_group_count": 1, "wrapper_spinbutton_count": 3, "wrapper_contenteditable_count": 3,
                "wrapper_button_count": 1, "group_section_list_count": 1, "input_parent_is_group": True,
                "group_parent_is_wrapper": True, "label_in_wrapper": True, "sections_in_group": True,
                "calendar_in_group": True, "capture_root_contains_wrapper": True}}}


def capture(fields, *, replacement=False, rich_text=None):
    listing_id, rxcu = ("234567", "RXCU23456") if replacement else ("123456", "RXCU12345")
    page = {"page_key": "details", "page_path": "/app/listings/edit/" + listing_id, "observation_number": 1,
        "observed_at": "2026-01-02T03:04:01+00:00", "complete": False, "fields": deepcopy(fields),
        "rich_text": deepcopy(rich_text or []), "dialogs": [], "sections": [], "lists": [], "tables": [], "media": [],
        "editor_links": [], "discovery": [], "limitations": [],
        "matched_source": {"listing_id": listing_id, "rxcu": rxcu, "reference_checked_before_and_after": True,
            "pinned_tab_checked": True, "dom_stability_seconds": 1.0, "hydration_complete_verified": False}}
    page["integrity"] = {"algorithm": "sha256", "sha256": _digest(page)}
    result = {"schema_version": SCHEMA_VERSION, "capture_kind": "matched_partial_visible_dom", "complete": False,
        "live_ready": False, "listing_id": listing_id, "status": "partial", "pages": [page], "limitations": [],
        "identity": {"route_verified": True, "reference_verified": True, "account_verified": False, "page_binding_verified": False},
        "matched_source": {"listing_id": listing_id, "rxcu": rxcu, "row_link_binding_used": True,
            "all_accepted_observations_guarded": True, "historical_observations_only": True, "account_verified": False,
            "fidelity_verified": False, "nested_dialogs_verified": False}}
    result["integrity"] = {"algorithm": "sha256", "sha256": _digest(result)}
    return result
