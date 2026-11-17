"""Exact production identity DOM results through opening and shared consumers.

All HTML/records are synthetic. Node/jsdom tests are read-only; controller tests
use inert driver doubles, never Chrome, Hub or a real user record.
"""
from copy import deepcopy
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from quick_relist import room_capture

from quick_relist.hub_readonly import IDENTITY_SCRIPT, ReadOnlyHub
from editor_identity_fixture import IDENTITY_RUNNER, identity_cases, identity_style_faults, page
from test_editor_recheck import Driver as OpeningDriver, LOADING, REF


CASES = identity_cases()
STYLE_FAULTS = identity_style_faults()


@pytest.fixture(scope="module")
def identities():
    if not shutil.which("node"):
        pytest.skip("Optional Node/jsdom unavailable")
    probe = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, text=True, timeout=10)
    if probe.returncode:
        pytest.skip("Optional jsdom unavailable; no packages are installed by tests")
    cases = {name: html for name, (html, _expected) in CASES.items()}
    cases.update({name: fault.get("html", page()) for name, fault in STYLE_FAULTS.items()})
    result = subprocess.run(["node", "-e", IDENTITY_RUNNER],
                            input=json.dumps({"script": IDENTITY_SCRIPT, "cases": cases, "style_faults": STYLE_FAULTS}),
                            text=True, capture_output=True, timeout=60, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("case", CASES)
def test_exact_presentation_shape_and_contradictions(identities, case):
    assert identities[case] == CASES[case][1]
    assert set(identities[case]) == {"outer_count", "inner_count", "outer_matches", "inner_matches"}


@pytest.mark.parametrize("case", STYLE_FAULTS)
def test_unreadable_ancestor_style_never_infers_visibility(identities, case):
    assert identities[case] == STYLE_FAULTS[case]["expected"]


def pending_opening():
    driver = OpeningDriver()
    hub = ReadOnlyHub(driver)
    with pytest.raises(RuntimeError, match="still unverified"):
        hub.open_listing_editor(REF, timeout=.05, poll_interval=.01)
    assert driver.link.clicks == 1 and hub.editor_open_state(REF) == "pending"
    return hub, driver


@pytest.mark.parametrize("first,final", [
    ("visible_pair", "qualified_transparent"),
    ("qualified_transparent", "visible_pair"),
    ("qualified_transparent", "qualified_transparent"),
])
def test_valid_presentation_transitions_bind_only_original_intent(identities, first, final):
    hub, driver = pending_opening()
    driver.identity = deepcopy(identities[first])
    driver.after_identity = lambda: setattr(driver, "identity", deepcopy(identities[final]))
    result = hub.recheck_opened_editor(REF)
    assert result["row_link_verified"] and result["editor_reference_matches"]
    assert result["account_verified"] is False and result["complete"] is False
    assert hub.editor_bindings[REF] == {"listing_id": "123", "handle": "target"}
    assert driver.link.clicks == driver.combo.clicks == 1 and driver.archive.clicks == 0


@pytest.mark.parametrize("final", ["missing_main_h4", "transparent_main_h4", "transparent_zero_area",
                                  "transparent_ancestor_opacity", "transparent_nested_span",
                                  "zero_area_main_h4", "zero_width_main_h4", "zero_height_main_h4",
                                  "h4_missing_opacity", "main_missing_opacity",
                                  "zero_area_reference_descendant", "nested_visible_main_h4"])
def test_missing_qualified_evidence_at_final_read_stays_pending(identities, final):
    hub, driver = pending_opening()
    driver.identity = deepcopy(identities["qualified_transparent"])
    driver.after_identity = lambda: setattr(driver, "identity", deepcopy(identities[final]))
    assert hub.recheck_opened_editor(REF)["editor_pending"] is True
    assert hub.editor_bindings == {} and hub.editor_open_state(REF) == "pending"
    driver.after_identity = None
    driver.identity = deepcopy(identities["qualified_transparent"])
    assert hub.recheck_opened_editor(REF)["row_link_verified"] is True
    assert driver.link.clicks == 1


@pytest.mark.parametrize("case", ["zero_area_main_h4", "zero_width_main_h4", "zero_height_main_h4",
                                 "hidden_main_h4", "h4_missing_opacity", "main_missing_opacity",
                                 "zero_area_reference_descendant", "nested_visible_main_h4"])
def test_initial_authoritative_title_failure_cannot_create_binding(identities, case):
    hub, driver = pending_opening()
    driver.identity = deepcopy(identities[case])
    assert hub.recheck_opened_editor(REF)["editor_pending"] is True
    assert hub.editor_bindings == {} and driver.link.clicks == 1


@pytest.mark.parametrize("final", ["transparent_wrong", "transparent_duplicates", "wrong_main_h4",
                                  "visible_wrong_beats_fallback", "visible_duplicates_beat_fallback",
                                  "h4_duplicate_other_main", "wrong_zero_area_main_h4_stays_conflict",
                                  "duplicate_zero_area_main_h4_stays_conflict",
                                  "wrong_h4_missing_opacity", "duplicate_h4_missing_opacity",
                                  "nested_wrong_main_h4_stays_conflict", "duplicate_nested_h4_stays_conflict"])
def test_final_contradiction_is_sticky_even_if_presentation_later_matches(identities, final):
    hub, driver = pending_opening()
    driver.identity = deepcopy(identities["qualified_transparent"])
    driver.after_identity = lambda: setattr(driver, "identity", deepcopy(identities[final]))
    with pytest.raises(RuntimeError, match="cannot be safely matched"):
        hub.recheck_opened_editor(REF)
    driver.after_identity = None
    driver.identity = deepcopy(identities["qualified_transparent"])
    with pytest.raises(RuntimeError):
        hub.recheck_opened_editor(REF)
    assert hub.editor_open_state(REF) == "blocked" and hub.editor_bindings == {}
    assert driver.link.clicks == 1


def test_standalone_title_verification_does_not_create_a_binding(identities):
    driver = OpeningDriver()
    driver.add_target()
    driver.current_window_handle = "target"
    driver.identity = deepcopy(identities["qualified_transparent"])
    hub = ReadOnlyHub(driver)
    result = hub.verify_editor_reference(REF, "123")
    assert result["row_link_verified"] is False and result["account_verified"] is False
    assert hub.editor_bindings == {} and hub.editor_open_state(REF) == "none"
    with pytest.raises(RuntimeError):
        hub.recheck_opened_editor(REF)
    assert driver.link.clicks == 0


def consumer_setup(monkeypatch, kind, identity):
    if kind == "matched":
        import quick_relist.matched_capture as module
        from test_matched_capture import Driver, capture
    else:
        import quick_relist.room_capture as module
        from test_room_capture import Driver, capture
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now,
                         sleep=lambda seconds: setattr(clock, "now", clock.now + seconds)))
    driver = Driver() if kind == "matched" else Driver(clock)
    if kind == "room":
        monkeypatch.setattr(module, "_keyboard_escape", lambda active: active.modal.send_keys(module.ESCAPE))
    driver.identity = deepcopy(identity)
    original = driver.execute_script

    def execute(script, *args):
        if script != IDENTITY_SCRIPT:
            return original(script, *args)
        if kind == "room":
            driver.transition()
            assert driver.modal is None, "Never inspect hidden modal-background identity"
        driver.calls.append(("read", script))
        result = deepcopy(driver.identity)
        driver.hook("identity")
        return result
    driver.execute_script = execute
    hub = SimpleNamespace(driver=driver, editor_bindings={REF: {"listing_id": "123456", "handle": "editor"}})
    return hub, driver, capture


@pytest.mark.parametrize("kind", ["matched", "room"])
def test_shared_consumers_accept_qualified_pair_only_as_partial_read_only(identities, monkeypatch, kind):
    hub, driver, capture = consumer_setup(monkeypatch, kind, identities["qualified_transparent"])
    result = capture(hub)
    assert result["status"] == "partial" and result["complete"] is False
    if kind == "matched":
        assert len(result["pages"]) == 4
        assert result["identity"]["reference_verified"] is True
        assert result["live_ready"] is False and result["matched_source"]["account_verified"] is False
        assert result["matched_source"]["fidelity_verified"] is False
    else:
        assert len(result["dialogs"]) == len(room_capture.ROOM_OPTIONS) and result["pending_dialogs"] == []
        assert result["server_writes_verified"] is False
        assert all(dialog["source"]["complete"] is False for dialog in result["dialogs"])


@pytest.mark.parametrize("kind", ["matched", "room"])
@pytest.mark.parametrize("case", ["transparent_wrong", "transparent_duplicates", "wrong_main_h4",
                                 "transparent_main_h4", "hidden_main_h4", "hidden_h4_ancestor", "transparent_ancestor_opacity",
                                 "zero_area_main_h4", "zero_width_main_h4", "zero_height_main_h4",
                                 "h4_missing_opacity", "main_missing_opacity",
                                 "wrong_zero_area_main_h4_stays_conflict", "duplicate_zero_area_main_h4_stays_conflict",
                                 "wrong_h4_missing_opacity", "duplicate_h4_missing_opacity",
                                 "zero_area_reference_descendant", "nested_visible_main_h4",
                                 "nested_wrong_main_h4_stays_conflict", "duplicate_nested_h4_stays_conflict"])
def test_shared_consumers_reject_conflict_or_missing_authoritative_evidence(identities, monkeypatch, kind, case):
    hub, driver, capture = consumer_setup(monkeypatch, kind, identities[case])
    result = capture(hub)
    assert result["status"] == "stopped" and result["complete"] is False
    assert result["pages" if kind == "matched" else "dialogs"] == []
    assert not any(call[0] in ("get", "click", "escape") for call in driver.calls)


@pytest.mark.parametrize("kind", ["matched", "room"])
@pytest.mark.parametrize("case", ["transparent_wrong", "missing_main_h4", "transparent_duplicates",
                                 "zero_area_main_h4", "zero_width_main_h4", "zero_height_main_h4",
                                 "h4_missing_opacity", "main_missing_opacity",
                                 "wrong_zero_area_main_h4_stays_conflict", "duplicate_zero_area_main_h4_stays_conflict",
                                 "wrong_h4_missing_opacity", "duplicate_h4_missing_opacity",
                                 "zero_area_reference_descendant", "nested_visible_main_h4",
                                 "nested_wrong_main_h4_stays_conflict", "duplicate_nested_h4_stays_conflict"])
def test_shared_consumers_discard_payload_when_evidence_changes(identities, monkeypatch, kind, case):
    hub, driver, capture = consumer_setup(monkeypatch, kind, identities["qualified_transparent"])
    def changed(event):
        if event == ("payload" if kind == "matched" else "main_payload"):
            driver.identity = deepcopy(identities[case])
    driver.hook = changed
    result = capture(hub)
    assert result["status"] == "stopped" and result["complete"] is False
    assert result["pages" if kind == "matched" else "dialogs"] == []


@pytest.mark.parametrize("kind", ["matched", "room"])
@pytest.mark.parametrize("change", ["binding", "driver", "route", "handle", "windows"])
def test_shared_consumer_binding_guards_are_not_replaced_by_qualified_titles(identities, monkeypatch, kind, change):
    hub, driver, capture = consumer_setup(monkeypatch, kind, identities["qualified_transparent"])
    def changed(event):
        if event != "identity": return
        if change == "binding": hub.editor_bindings.clear()
        if change == "driver": hub.driver = object()
        if change == "route": driver.current_url += "/media"
        if change == "handle": driver.current_window_handle = "results"
        if change == "windows": driver.window_handles.append("unexpected")
    driver.hook = changed
    result = capture(hub)
    assert result["status"] == "stopped" and result["complete"] is False
    assert result["pages" if kind == "matched" else "dialogs"] == []
    assert not any(call[0] in ("get", "click", "escape") for call in driver.calls)
