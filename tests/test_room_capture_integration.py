"""Synthetic real-orchestrator room capture regressions; no browser or Hub."""
from types import SimpleNamespace

import pytest

import test_room_capture as fixture
import quick_relist.room_capture as room_module
import quick_relist.matched_capture as matched_module
from quick_relist.capture import READ_PAGE_SCRIPT, _digest, verify_capture_integrity
from quick_relist.source_fields import extract_source_fields


class IntegratedDriver(fixture.Driver):
    """Room fixture supporting the matched page collector without navigation."""

    def find_elements(self, by, selector):
        assert by == 'css selector' and selector == matched_module.TAB_SELECTOR
        return []

    def execute_script(self, script, *args):
        if script == matched_module.READINESS_SCRIPT:
            return {'ready': True, 'busy': False, 'dialog': self.modal is not None}
        result = super().execute_script(script, *args)
        if script == READ_PAGE_SCRIPT and self.modal is None:
            result['fields'] = [{'id': 'feature-bedroom-input', 'name': '', 'label': 'Bedrooms',
                'tag': 'input', 'type': 'text', 'value': str(self.counts['bedroom'])}]
        return result


@pytest.mark.parametrize('change_between_stages', [False, True])
def test_matched_room_capture_pairs_dialogs_with_exact_saved_fields(monkeypatch, change_between_stages):
    clock = SimpleNamespace(now=0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.now,
        sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds))
    monkeypatch.setattr(room_module, 'time', fake_time)
    monkeypatch.setattr(matched_module, 'time', fake_time)
    monkeypatch.setattr(room_module, '_keyboard_escape', lambda active: active.modal.send_keys(room_module.ESCAPE))
    driver = IntegratedDriver(clock)
    driver.counts = {d.key: (2 if d.key == 'bedroom' else 0) for d in room_module.ROOM_OPTIONS}
    hub = SimpleNamespace(driver=driver, editor_bindings={fixture.REF: {'listing_id': '123456', 'handle': 'editor'}})

    class Capture(matched_module.MatchedHubCapture):
        def _capture_rooms(self, sample, url):
            if change_between_stages:
                driver.counts['bedroom'] = 3
            return super()._capture_rooms(sample, url)

    result = Capture(hub, fixture.REF, include_room_options=True,
        timeout=2, poll_interval=.1, stable_for=.2).capture()
    inventory = extract_source_fields(result, expected_rxcu=fixture.REF)
    count = next(e for e in inventory['entries'] if e['key'] == 'feature_bedroom')
    assert count['value'] == '2'  # The guarded page remains an immutable observation.
    assert inventory['status'] == 'partial' and verify_capture_integrity(result)
    page = result['pages'][0]
    if change_between_stages:
        assert result['status'] == 'stopped'
        assert page['dialogs'] == []
        assert result['room_option_capture'][0]['stop_code'] == 'main_changed'
        assert fixture.actions(driver) == []
    else:
        assert result['status'] == 'partial'
        assert len(page['dialogs']) == 1
        dialog = page['dialogs'][0]
        assert len(dialog['room_groups']) == 2
        assert dialog['source']['main_fields_sha256'] == _digest({k: page[k] for k in ('fields', 'rich_text')})
        assert dialog['source']['main_fields_unchanged_verified'] is True
        assert fixture.actions(driver) == [('click', 'bedroom'), ('escape', 'bedroom')]
