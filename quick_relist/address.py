"""Tentative address extraction from incomplete, visible Hub observations.

Field selectors are based on observed Hub labels/names, not a verified API.
This mapping never approves an address or enables a listing operation.
"""
from __future__ import annotations
from copy import deepcopy

# Registered property identifiers stay exactly as captured during relisting.
REGISTERED_ADDRESS_KEYS = frozenset(('erf_number', 'ss_number'))

# Semantic key -> exact observed DOM name or id. Suburb and the visibility
# checkbox are matched by their observed accessible labels instead.
_ADDRESS_NAMES = {
    "country": "country_id", "street_number": "street_number",
    "street_name": "street_name", "street_type": "street_type",
    "estate_name": "estate_name", "erf_number": "lightstone_erf_number",
    "ss_unit_number": "lightstone_sectional_scheme_unit_number",
    "ss_number": "scheme_number", "ss_name": "scheme_name",
    "id_registration_number": "lightstone_id_reg_number",
    "door_number": "door_number", "latitude": "latitude", "longitude": "longitude",
}


def propose_address_fields(capture):
    """Suggest address controls from the latest particulars observation.

    Only exact uniquely matched controls are returned. Missing or repeated
    controls remain unresolved. Original field objects and their capture paths
    are preserved for user review; no identity or validation claim is made.
    """
    empty = {"fields": {}, "missing": sorted([*_ADDRESS_NAMES, "suburb", "show_address_on_web"]),
             "ambiguous": [], "complete": False,
             "warning": "Tentative mapping only. Verify the listing, address, suburb selection and every field in Hub."}
    if not isinstance(capture, dict) or capture.get("complete") is not False:
        return empty
    pages = capture.get("pages")
    if not isinstance(pages, list):
        return empty
    candidates = [(i, page) for i, page in enumerate(pages) if isinstance(page, dict) and page.get("page_key") == "details" and isinstance(page.get("fields"), list)]
    if not candidates:
        return empty
    page_index, page = candidates[-1]
    found, missing, ambiguous = {}, [], []
    def collect(predicate):
        return [(i, field) for i, field in enumerate(page["fields"]) if isinstance(field, dict) and predicate(field)]
    for key, name in _ADDRESS_NAMES.items():
        matches = collect(lambda f: f.get("name") == name or f.get("id") == name)
        if len(matches) == 1:
            index, field = matches[0]
            found[key] = {"path": f"pages/{page_index}/fields/{index}", "field": deepcopy(field)}
        elif matches:
            ambiguous.append(key)
        else:
            missing.append(key)
    for key, label, required_type in (("suburb", "suburb", None), ("show_address_on_web", "show address on web", "checkbox")):
        matches = collect(lambda f: str(f.get("label") or "").strip().casefold().rstrip(" *:") == label
                          and (required_type is None or str(f.get("type") or "").lower() == required_type))
        if len(matches) == 1:
            index, field = matches[0]
            found[key] = {"path": f"pages/{page_index}/fields/{index}", "field": deepcopy(field)}
        elif matches:
            ambiguous.append(key)
        else:
            missing.append(key)
    return {"fields": found, "missing": sorted(missing), "ambiguous": sorted(ambiguous), "complete": False,
            "warning": "Tentative mapping only. Verify the listing, address, suburb selection and every field in Hub."}
