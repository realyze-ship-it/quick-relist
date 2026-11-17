from quick_relist.address import propose_address_fields, _ADDRESS_NAMES


def _field(name, value=""):
    return {"name": name, "id": name, "label": "", "type": "text", "value": value, "checked": None}


def _capture(fields):
    return {"complete": False, "pages": [{"page_key": "details", "fields": fields}]}


def test_proposes_all_observed_controls_without_claiming_verified():
    fields = [_field(name, "synthetic") for name in _ADDRESS_NAMES.values()]
    fields += [{**_field(""), "label": "Suburb", "value": "Synthetic"},
               {**_field("address_hidden"), "label": "Show address on web", "type": "checkbox", "checked": True}]
    result = propose_address_fields(_capture(fields))
    assert len(result["fields"]) == 15
    assert result["missing"] == result["ambiguous"] == []
    assert result["complete"] is False
    assert result["fields"]["show_address_on_web"]["field"]["checked"] is True
    assert result["fields"]["country"]["path"] == "pages/0/fields/0"


def test_duplicate_never_guessed_and_missing_remains_explicit():
    result = propose_address_fields(_capture([_field("street_name", "A"), _field("street_name", "B")]))
    assert "street_name" in result["ambiguous"]
    assert "street_name" not in result["fields"]
    assert "suburb" in result["missing"]


def test_latest_details_only_and_detached_copy():
    cap = {"complete": False, "pages": [{"page_key": "details", "fields": [_field("street_name", "old")]},
           {"page_key": "media", "fields": [_field("street_name", "wrong")]},
           {"page_key": "details", "fields": [_field("street_name", "new")]}]}
    result = propose_address_fields(cap)
    assert result["fields"]["street_name"]["path"] == "pages/2/fields/0"
    result["fields"]["street_name"]["field"]["value"] = "edited"
    assert cap["pages"][2]["fields"][0]["value"] == "new"


def test_complete_or_malformed_capture_never_accepted():
    for cap in ({"complete": True}, {}, None):
        assert propose_address_fields(cap)["fields"] == {}
