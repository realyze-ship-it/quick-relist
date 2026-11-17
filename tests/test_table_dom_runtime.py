"""Actual extractor tests in jsdom; all HTML is synthetic and offline.

The imported runner blocks side effects/storage/network and checks unchanged DOM
and control state. jsdom simulates visibility, not real Chrome layout. The plain
fixture is also suitable for a later offline Windows Chrome inspection.
"""
from pathlib import Path
import json

from test_dom_runtime import extract  # Shared optional fixture; no app dependency.
from quick_relist.capture import _validate_observation

FIXTURE = Path(__file__).parent / "fixtures" / "native_tables.html"


def assert_compatible(result):
    _validate_observation(result, result["href"])
    for table in result["tables"]:
        structure = table["structure"]
        assert table["rows"] == [[cell["text"] for cell in row["cells"]] for row in structure["rows"]]
        assert structure["mapping_verified"] is False
        assert structure["complete"] is False


def test_irregular_native_portal_rows_never_expand_or_assign_chips(extract):
    result = extract(FIXTURE.read_text())
    assert_compatible(result)
    table = result["tables"][0]
    structure = table["structure"]
    assert structure["status"] == "observed_native"
    assert structure["tag"] == "table"
    assert [len(row) for row in table["rows"]] == [8, 8, 5, 1, 8]
    assert all(cell["scope_attribute"] == "col" for cell in structure["rows"][0]["cells"])
    publication = structure["rows"][3]["cells"]
    assert len(publication) == 1
    assert publication[0]["chips"] == [{"text": "Synthetic chip text"}]
    assert publication[0]["actions"] == []
    assert publication[0]["header_ids"] == []
    assert publication[0]["header_refs"] == []
    assert publication[0]["rowspan_attribute"] is None
    assert publication[0]["colspan_attribute"] is None
    assert publication[0]["colspan_property"] == 1
    action_row = structure["rows"][4]["cells"]
    assert [action["label"] for action in action_row[0]["actions"]] == ["Publish to all portals", "Remove from all portals"]
    assert all(cell["chips"] == [] for cell in action_row)
    assert "table_row_cell_counts_vary" in structure["limitations"]
    assert "grid_positions_not_inferred" in structure["limitations"]
    assert "portal_assignments" not in json.dumps(structure)


def test_native_span_header_and_commission_observations_are_raw_not_a_grid(extract):
    result = extract(FIXTURE.read_text())
    assert_compatible(result)
    commission = result["tables"][1]["structure"]
    first = commission["rows"][1]["cells"][0]
    assert first["headers_attribute"] == first["headers_property"] == "agent"
    assert first["header_refs"] == [{"id": "agent", "status": "observed_header", "row_dom_order": 0, "cell_dom_order": 0}]
    assert commission["rows"][1]["cells"][2]["actions"][0]["disabled"] is True
    structure = result["tables"][2]["structure"]
    header = structure["rows"][0]["cells"][0]
    assert header["tag"] == "th"
    assert header["scope_attribute"] == header["scope_property"] == "colgroup"
    assert header["colspan_attribute"] == "2"
    assert header["colspan_property"] == 2
    row_header = structure["rows"][1]["cells"][0]
    assert row_header["rowspan_attribute"] == "0"
    assert row_header["rowspan_property"] == 0
    invalid = structure["rows"][2]["cells"][1]
    assert invalid["rowspan_attribute"] == "-2"
    assert invalid["rowspan_property"] == 1
    assert invalid["colspan_attribute"] == "bogus"
    assert invalid["colspan_property"] == 1
    refs = structure["rows"][1]["cells"][1]["header_refs"]
    assert refs == [{"id": "group", "status": "observed_header", "row_dom_order": 0, "cell_dom_order": 0},
                    {"id": "row-label", "status": "observed_header", "row_dom_order": 1, "cell_dom_order": 0}]
    unresolved = structure["rows"][2]["cells"][0]["header_refs"]
    assert [ref["id"] for ref in unresolved] == ["missing-header", "nested-header", "hidden-header"]
    assert all(ref["status"] == "unresolved" and ref["row_dom_order"] is None for ref in unresolved)
    assert "table_spans_present" in structure["limitations"]
    assert "table_span_attribute_invalid" in structure["limitations"]


def test_nested_tables_do_not_duplicate_rows_cells_text_actions_or_chips(extract):
    result = extract(FIXTURE.read_text())
    assert_compatible(result)
    assert len(result["tables"]) == 4
    outer = result["tables"][2]
    nested = result["tables"][3]
    assert [len(row) for row in outer["rows"]] == [2, 2, 2]
    assert outer["rows"][1][1] == "Outer cell text"
    outer_cell = outer["structure"]["rows"][1]["cells"][1]
    assert outer_cell["actions"] == []
    assert outer_cell["chips"] == []
    assert "Nested cell text" not in json.dumps(outer)
    assert "Nested action" not in json.dumps(outer)
    assert "Nested chip" not in json.dumps(outer)
    assert [len(row) for row in nested["rows"]] == [1, 1]
    assert nested["structure"]["captions"] == ["Nested synthetic table"]
    assert nested["structure"]["rows"][1]["cells"][0]["actions"][0]["label"] == "Nested action"
    assert nested["structure"]["rows"][1]["cells"][0]["chips"] == [{"text": "Nested chip"}]
    assert "nested_table_content_excluded" in outer["structure"]["limitations"]


def test_hidden_sensitive_controls_and_queries_stay_out_of_every_observation(extract):
    result = extract(FIXTURE.read_text())
    assert_compatible(result)
    assert "SYNTHETIC_DO_NOT_CAPTURE" not in json.dumps(result)
    outer = result["tables"][2]["structure"]
    assert len(outer["rows"]) == 3
    assert "table_rows_omitted" in outer["limitations"]
    assert outer["rows"][2]["cells"][0]["actions"][0]["label"] == "Open https://example.invalid/path"


def test_non_native_grid_is_explicitly_unsupported_but_legacy_rows_survive(extract):
    result = extract('''<main><div role="grid"><div role="row"><div role="rowheader">Synthetic row</div>
      <div role="gridcell">Synthetic value <span role="gridcell">Unsupported nested cell</span></div></div></div></main>''')
    assert_compatible(result)
    table = result["tables"][0]
    assert table["rows"] == [["Synthetic row", "Synthetic value"]]
    structure = table["structure"]
    assert structure["status"] == "unsupported_non_native"
    assert structure["rows"][0]["cells"][0]["role"] == "rowheader"
    assert structure["rows"][0]["cells"][1]["rowspan_property"] is None
    assert "non_native_table_structure_unsupported" in structure["limitations"]
    assert "nested_cell_structure_unsupported" in structure["limitations"]


def test_hidden_cells_leave_dom_order_gaps_instead_of_invented_columns(extract):
    result = extract('''<main><table><tr><td>A</td><td hidden>HIDDEN_SYNTHETIC_CELL</td>
      <td>B</td><td aria-label="Password">SENSITIVE_SYNTHETIC_CELL</td><td>C</td></tr></table></main>''')
    assert_compatible(result)
    table = result["tables"][0]
    assert table["rows"] == [["A", "B", "C"]]
    assert [cell["dom_order"] for cell in table["structure"]["rows"][0]["cells"]] == [0, 2, 4]
    assert "table_cells_omitted" in table["structure"]["limitations"]
    assert "HIDDEN_SYNTHETIC_CELL" not in json.dumps(result)
    assert "SENSITIVE_SYNTHETIC_CELL" not in json.dumps(result)


def test_duplicate_header_ids_and_outside_action_labels_are_unresolved(extract):
    result = extract('''<main><span id="outside">External label</span><table><tr><th id="duplicate">A</th><th id="duplicate">B</th></tr>
      <tr><td headers="duplicate"><button aria-labelledby="outside">Local label</button></td></tr></table></main>''')
    assert_compatible(result)
    cell = result["tables"][0]["structure"]["rows"][1]["cells"][0]
    assert cell["header_refs"] == [{"id": "duplicate", "status": "unresolved", "row_dom_order": None, "cell_dom_order": None}]
    assert cell["actions"][0]["label"] == "Local label"
    assert "table_action_label_relationship_unresolved" in result["limitations"]


def test_cell_limit_preserves_legacy_shape_and_discloses_truncation(extract):
    html = '<main><table><tr>' + ''.join(f'<td>Cell {i}</td>' for i in range(101)) + '</tr></table></main>'
    result = extract(html)
    assert_compatible(result)
    table = result["tables"][0]
    assert len(table["rows"][0]) == 100
    assert len(table["structure"]["rows"][0]["cells"]) == 100
    assert table["rows"][0][-1] == "Cell 99"
    assert "table_items_truncated" in table["structure"]["limitations"]
    assert "dom_items_truncated" in result["limitations"]


def test_empty_native_cells_stay_present_and_action_labels_are_cell_scoped(extract):
    result = extract('''<main><table><tr><td></td><td><button aria-label="Synthetic icon action"></button></td>
      <td><span id="local-action-label">Synthetic local label</span><button aria-labelledby="local-action-label"></button></td>
      <td><button hidden>SYNTHETIC_HIDDEN_ACTION</button><div class="MuiChip-root" hidden>SYNTHETIC_HIDDEN_CHIP</div></td>
      </tr></table></main>''')
    assert_compatible(result)
    table = result["tables"][0]
    assert table["rows"] == [["", "", "Synthetic local label", ""]]
    cells = table["structure"]["rows"][0]["cells"]
    assert cells[1]["actions"][0]["label"] == "Synthetic icon action"
    assert cells[2]["actions"][0]["label"] == "Synthetic local label"
    assert cells[3]["actions"] == cells[3]["chips"] == []
    assert "SYNTHETIC_HIDDEN" not in json.dumps(result)


def test_native_table_with_nested_role_cells_is_marked_ambiguous(extract):
    result = extract('''<main><table><tr><td>Outer native cell <span role="cell">Ambiguous role cell</span></td></tr></table></main>''')
    assert_compatible(result)
    table = result["tables"][0]
    assert table["rows"] == [["Outer native cell"]]
    assert table["structure"]["status"] == "ambiguous_mixed"
    assert "mixed_table_structure_unsupported" in table["structure"]["limitations"]
    assert "nested_cell_structure_unsupported" in table["structure"]["limitations"]


def test_role_table_nested_inside_native_cell_is_a_separate_observation(extract):
    result = extract('''<main><table><tr><td>Native text<div role="table"><div role="row">
      <div role="cell">Separate ARIA text</div></div></div></td></tr></table></main>''')
    assert_compatible(result)
    assert len(result["tables"]) == 2
    assert result["tables"][0]["rows"] == [["Native text"]]
    assert result["tables"][1]["rows"] == [["Separate ARIA text"]]
    assert result["tables"][0]["structure"]["status"] == "observed_native"
    assert result["tables"][1]["structure"]["status"] == "unsupported_non_native"
