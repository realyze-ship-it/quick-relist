"""Synthetic orchestrator safety tests. No browser or real listing interaction."""
from types import SimpleNamespace
import json

import pytest

import quick_relist.room_capture as module
import quick_relist.matched_capture as matched
from quick_relist.capture import verify_capture_integrity
from test_room_capture_integration import IntegratedDriver
import test_room_capture as fixture

PRIVATE = 'PRIVATE_POSITION_DOM_URL_VALUE_EXCEPTION'


class PositionDriver(IntegratedDriver):
    def __init__(self, clock):
        super().__init__(clock)
        self.position_status = 'clear'
        self.after_scroll = 'clear'
        self.position_reads = self.scrolls = 0
        self.position_error = None
        self.scroll_error = None

    def execute_script(self, script, *args):
        if script != module.ROOM_POSITION_SCRIPT:
            return super().execute_script(script, *args)
        self.calls.append(('read', script))
        self.position_reads += 1
        assert args[0] == vars(module.ROOM_OPTIONS[0])
        assert args[1] is self.openers['bedroom'] and args[2] == self.counts['bedroom']
        if self.position_error:
            raise self.position_error
        if args[3]:
            self.scrolls += 1
            self.calls.append(('scroll', 'bedroom'))
            self.position_status = self.after_scroll
            self.hook('scroll')
            if self.scroll_error:
                raise self.scroll_error
            return 'scrolled'
        self.hook('position')
        return self.position_status


@pytest.fixture
def setup_position(monkeypatch):
    clock = SimpleNamespace(now=0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.now,
        sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds))
    monkeypatch.setattr(module, 'time', fake_time)
    monkeypatch.setattr(matched, 'time', fake_time)
    monkeypatch.setattr(module, '_keyboard_escape', lambda active: active.modal.send_keys(module.ESCAPE))
    driver = PositionDriver(clock)
    driver.counts = {d.key: (2 if d.key == 'bedroom' else 0) for d in module.ROOM_OPTIONS}
    hub = SimpleNamespace(driver=driver, editor_bindings={fixture.REF:
        {'listing_id': '123456', 'handle': 'editor'}})
    return hub, driver, clock


def run(hub):
    return fixture.capture(hub, descriptors=(module.ROOM_OPTIONS[0],))


@pytest.mark.parametrize('needs_scroll', [False, True])
def test_clean_or_repositioned_target_gets_one_click_and_escape(setup_position, needs_scroll):
    hub, driver, _ = setup_position
    driver.position_status = 'needs_scroll' if needs_scroll else 'clear'
    result = run(hub)
    assert result['status'] == 'partial' and result['stop_code'] is None
    assert driver.scrolls == int(needs_scroll)
    assert fixture.actions(driver) == ([('scroll', 'bedroom')] if needs_scroll else []) + [
        ('click', 'bedroom'), ('escape', 'bedroom')]
    assert result['dialogs'][0]['source']['main_fields_unchanged_verified'] is True
    assert result['complete'] is result['server_writes_verified'] is False


@pytest.mark.parametrize('after', ['needs_scroll', 'invalid', 'changed', None, {}, PRIVATE])
def test_persistent_obstruction_and_unknown_geometry_stop_without_click(setup_position, after):
    hub, driver, _ = setup_position
    driver.position_status, driver.after_scroll = 'needs_scroll', after
    result = run(hub)
    assert result['status'] == 'stopped'
    assert result['stop_code'] == ('opener_obscured' if after == 'needs_scroll' else
                                  'opener_ambiguous' if after == 'changed' else 'opener_position_unreadable')
    assert fixture.actions(driver) == [('scroll', 'bedroom')]
    assert driver.scrolls == 1 and result['open_failure'] is None
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('stage', ['first_position', 'scroll'])
@pytest.mark.parametrize('change,code', [('reference', 'identity_changed'), ('binding', 'binding_changed'),
    ('driver', 'binding_changed'), ('handle', 'position_changed'), ('tabs', 'position_changed'),
    ('route', 'position_changed'), ('count', 'main_changed'), ('main', 'main_changed'),
    ('node', 'opener_ambiguous'), ('shape', 'opener_ambiguous'), ('duplicate', 'opener_ambiguous')])
def test_source_and_feature_changes_are_rechecked_before_click(setup_position, stage, change, code):
    hub, driver, _ = setup_position
    driver.position_status = 'needs_scroll'
    def hook(event):
        if (stage == 'first_position' and event != 'position') or (stage == 'scroll' and event != 'scroll'):
            return
        driver.hook = lambda _event: None
        if change == 'reference': driver.matches = False
        if change == 'binding': hub.editor_bindings.clear()
        if change == 'driver': hub.driver = object()
        if change == 'handle': driver.current_window_handle = 'results'
        if change == 'tabs': driver.window_handles.append('new')
        if change == 'route': driver.current_url += '/media'
        if change == 'count': driver.counts['bedroom'] = 3
        if change == 'main':
            # IntegratedDriver returns the count field, so model an additional
            # main-field change without changing the verified count itself.
            original = driver.execute_script
            def changed(script, *args):
                result = original(script, *args)
                if script == fixture.READ_PAGE_SCRIPT:
                    result['fields'].append({'name': 'changed', 'type': 'text', 'value': PRIVATE})
                return result
            driver.execute_script = changed
        if change == 'node': driver.openers['bedroom'] = fixture.Element(driver, 'opener', module.ROOM_OPTIONS[0])
        if change == 'shape': driver.shape_ok = False
        if change == 'duplicate': driver.opener_count = 2
    driver.hook = hook
    result = run(hub)
    assert result['stop_code'] == code
    assert fixture.actions(driver) == ([('scroll', 'bedroom')] if stage == 'scroll' else [])
    assert result['open_failure'] is None and PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('event', ['position', 'scroll'])
def test_cancellation_does_not_dispatch_after_view_read_or_scroll(setup_position, event):
    hub, driver, _ = setup_position
    driver.position_status = 'needs_scroll'
    cancel = SimpleNamespace(value=False)
    driver.hook = lambda kind: setattr(cancel, 'value', True) if kind == event else None
    result = fixture.capture(hub, descriptors=(module.ROOM_OPTIONS[0],), cancelled=lambda: cancel.value)
    assert result['stop_code'] == 'cancelled'
    assert fixture.actions(driver) == ([('scroll', 'bedroom')] if event == 'scroll' else [])


@pytest.mark.parametrize('phase', ['read', 'scroll'])
def test_unknown_or_stale_position_outcomes_are_not_retried(setup_position, phase):
    hub, driver, _ = setup_position
    exceptions = pytest.importorskip('selenium.common.exceptions')
    error = exceptions.StaleElementReferenceException(PRIVATE)
    if phase == 'read': driver.position_error = error
    else:
        driver.position_status = 'needs_scroll'
        driver.scroll_error = error
    result = run(hub)
    assert result['stop_code'] == ('opener_position_unreadable' if phase == 'read' else 'opener_scroll_uncertain')
    assert fixture.actions(driver) == ([] if phase == 'read' else [('scroll', 'bedroom')])
    assert result['open_failure'] is None and PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('name,kind', [('StaleElementReferenceException', 'stale_element'),
                                     ('ElementClickInterceptedException', 'click_intercepted')])
def test_later_click_exception_retains_uncertainty_and_never_retries(setup_position, name, kind):
    hub, driver, _ = setup_position
    errors = pytest.importorskip('selenium.common.exceptions')
    driver.position_status = 'needs_scroll'
    def hook(event):
        if event == 'click': raise getattr(errors, name)(PRIVATE)
    driver.hook = hook
    result = run(hub)
    assert result['stop_code'] == 'open_uncertain'
    assert result['open_failure'] == {'kind': kind, 'room_key': 'bedroom'}
    assert fixture.actions(driver) == [('scroll', 'bedroom'), ('click', 'bedroom')]
    assert driver.calls[-1] == ('click', 'bedroom') and driver.pending is not None
    assert PRIVATE not in json.dumps(result)


def test_final_position_is_rechecked_not_reused(setup_position):
    hub, driver, _ = setup_position
    def hook(event):
        if event == 'position' and driver.position_reads == 2:
            driver.position_status = 'needs_scroll'
    driver.hook = hook
    result = run(hub)
    assert result['stop_code'] == 'opener_obscured'
    assert driver.position_reads == 2 and driver.scrolls == 0 and not fixture.actions(driver)


def test_count_change_with_unchanged_snapshot_still_stops(setup_position):
    hub, driver, _ = setup_position
    original = driver.execute_script
    def frozen_snapshot(script, *args):
        result = original(script, *args)
        if script == fixture.READ_PAGE_SCRIPT: result['fields'][0]['value'] = '2'
        return result
    driver.execute_script = frozen_snapshot
    driver.position_status = 'needs_scroll'
    driver.hook = lambda event: driver.counts.update(bedroom=3) if event == 'scroll' else None
    assert run(hub)['stop_code'] == 'opener_ambiguous'
    assert fixture.actions(driver) == [('scroll', 'bedroom')]


def test_matched_collector_preserves_guarded_page_and_stops_after_obstruction(setup_position):
    hub, driver, _ = setup_position
    driver.position_status = driver.after_scroll = 'needs_scroll'
    result = matched.capture_matched_listing(hub, fixture.REF, include_room_options=True,
        timeout=2, poll_interval=.1, stable_for=.2)
    assert result['status'] == 'stopped' and len(result['pages']) == 1
    assert result['pages'][0]['dialogs'] == []
    assert result['room_option_capture'][0]['stop_code'] == 'opener_obscured'
    assert verify_capture_integrity(result) and result['live_ready'] is False
    assert fixture.actions(driver) == [('scroll', 'bedroom')]


@pytest.mark.parametrize('flag', ['busy', 'unready'])
def test_postscroll_unsettled_main_times_out_without_reposition_or_click(setup_position, flag):
    hub, driver, clock = setup_position
    driver.position_status = 'needs_scroll'
    scroll_at = []
    def hook(event):
        if event != 'scroll': return
        scroll_at.append(clock.now)
        if flag == 'busy': driver.busy = True
        else: driver.ready = False
    driver.hook = hook
    result = run(hub)
    assert result['stop_code'] == 'main_timeout'
    assert fixture.actions(driver) == [('scroll', 'bedroom')]
    assert driver.scrolls == 1 and clock.now - scroll_at[0] <= 2.1


def test_overlay_appearing_after_scroll_stops_without_dismissing_it(setup_position):
    hub, driver, _ = setup_position
    driver.position_status = 'needs_scroll'
    driver.hook = lambda event: setattr(driver, 'extra_overlay', True) if event == 'scroll' else None
    result = run(hub)
    assert result['stop_code'] == 'initial_dialog'
    assert fixture.actions(driver) == [('scroll', 'bedroom')]
    assert driver.extra_overlay is True and result['cleanup_stop_code'] is None
