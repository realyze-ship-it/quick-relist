"""Bounded matched-capture diagnostics with synthetic drivers and local storage."""
import builtins
from copy import deepcopy
import json
import sqlite3

import pytest

import quick_relist.matched_capture as module
from quick_relist.capture import CaptureReadError, CaptureSafetyError, _digest, verify_capture_integrity
from quick_relist.drafts import DraftStore
from quick_relist.source_fields import extract_source_fields
from test_matched_capture import (BASE, REF, URLS, _room_dialog, assert_partial,
                                  setup, use_native_tabs)  # noqa: F401

PRIVATE = 'PRIVATE_SYNTHETIC_EXCEPTION_URL_CREDENTIAL_DOM'
KINDS = [
    ('ElementClickInterceptedException', 'click_intercepted'),
    ('StaleElementReferenceException', 'stale_element'),
    ('ElementNotInteractableException', 'not_interactable'),
    ('MoveTargetOutOfBoundsException', 'move_target_out_of_bounds'),
    ('TimeoutException', 'timeout'),
    ('JavascriptException', 'javascript_error'),
    ('NoSuchWindowException', 'no_such_window'),
    ('InvalidSessionIdException', 'invalid_session'),
    ('WebDriverException', 'other_exception'),
]


def error(name):
    errors = pytest.importorskip('selenium.common.exceptions')
    return getattr(errors, name)(PRIVATE, screen=PRIVATE, stacktrace=[PRIVATE])


def session(hub):
    return module.MatchedHubCapture(hub, REF, timeout=2, poll_interval=.1, stable_for=.2)


def actions(driver):
    return [call for call in driver.calls if call[0] in ('tab_click', 'get')]


def diagnostic(phase, kind, attempted=False, page='deal_particulars'):
    return {'phase': phase, 'target_page_key': page, 'kind': kind,
            'navigation_attempted_in_capture': attempted}


def fail_navigation(driver, navigation, failure, *, change_route=True):
    def fail(*args):
        if change_route:
            driver.current_url = URLS[1]
        raise failure
    if navigation == 'tab':
        use_native_tabs(driver)
        driver.on_tab_click = fail
    else:
        driver.after_navigation = fail


def fail_restoration(driver, failure):
    original = driver.set_page_load_timeout
    def configure(seconds):
        if seconds == 300:
            driver.calls.append(('restore_attempt', seconds))
            raise failure
        original(seconds)
    driver.set_page_load_timeout = configure


def assert_private_partial(result):
    assert_partial(result)
    assert PRIVATE not in json.dumps(result)
    for key in ('browser_failure', 'timeout_restore_failure'):
        if result[key] is not None:
            assert set(result[key]) == {'phase', 'target_page_key', 'kind', 'navigation_attempted_in_capture'}
            assert module.matched_capture_failure_note(result[key]) in result['stop_reason']


@pytest.mark.parametrize('name,kind', KINDS)
@pytest.mark.parametrize('navigation', ['tab', 'link'])
def test_known_exact_errors_after_dispatch_keep_only_guarded_pages(setup, name, kind, navigation):
    hub, driver, _ = setup
    fail_navigation(driver, navigation, error(name))
    result = session(hub).capture()
    phase = 'tab_click' if navigation == 'tab' else 'link_navigation'
    assert result['browser_failure'] == diagnostic(phase, kind, True)
    assert result['timeout_restore_failure'] is None
    assert result['status'] == 'stopped' and len(result['pages']) == 1
    assert driver.current_url == URLS[1] and len(actions(driver)) == 1
    assert driver.timeouts.page_load == 300
    assert name not in json.dumps(result)
    assert 'does not identify a failed call or establish its outcome' in result['stop_reason']
    assert_private_partial(result)


@pytest.mark.parametrize('change_route', [False, True])
def test_raised_tab_call_is_attempted_even_when_route_stays_at_source(setup, change_route):
    hub, driver, _ = setup
    fail_navigation(driver, 'tab', error('TimeoutException'), change_route=change_route)
    result = session(hub).capture()
    assert result['browser_failure']['navigation_attempted_in_capture'] is True
    assert len(actions(driver)) == 1
    assert_private_partial(result)


@pytest.mark.parametrize('phase', ['source_check', 'page_read', 'tab_probe', 'tab_precheck', 'link_precheck'])
def test_pre_navigation_probe_failures_have_static_phase_and_no_dispatch(setup, phase):
    hub, driver, _ = setup
    current = session(hub)
    if phase == 'tab_precheck':
        use_native_tabs(driver)
    original = driver.find_elements
    def find(*args):
        if phase == 'tab_probe' and current._failure_phase == phase:
            raise error('StaleElementReferenceException')
        return original(*args)
    driver.find_elements = find
    def hook(kind):
        if phase != 'tab_probe' and current._failure_phase == phase:
            raise error('JavascriptException')
    driver.hook = hook
    result = current.capture()
    page = 'deal_particulars' if phase.endswith('precheck') else 'details'
    kind = 'stale_element' if phase == 'tab_probe' else 'javascript_error'
    assert result['browser_failure'] == diagnostic(phase, kind, page=page)
    assert not actions(driver)
    assert_private_partial(result)


@pytest.mark.parametrize('navigation', ['tab', 'link'])
@pytest.mark.parametrize('phase', ['timeout_read', 'timeout_setup'])
def test_timeout_configuration_failure_has_no_navigation(setup, navigation, phase):
    hub, driver, _ = setup
    if navigation == 'tab': use_native_tabs(driver)
    if phase == 'timeout_read':
        class Timeouts:
            @property
            def page_load(self):
                raise error('InvalidSessionIdException')
        driver.timeouts = Timeouts()
    else:
        original = driver.set_page_load_timeout
        def configure(seconds):
            if seconds == 2:
                raise error('InvalidSessionIdException')
            original(seconds)
        driver.set_page_load_timeout = configure
    result = session(hub).capture()
    assert result['browser_failure'] == diagnostic(phase, 'invalid_session')
    assert not actions(driver)
    assert result['timeout_restore_failure'] is None
    assert_private_partial(result)


@pytest.mark.parametrize('phase', ['tab_wait', 'page_read', 'tab_probe', 'timeout_setup'])
def test_later_reads_and_next_setup_never_erase_an_earlier_attempt(setup, phase):
    hub, driver, _ = setup
    use_native_tabs(driver)
    current = session(hub)
    def should_fail():
        return driver.current_url != BASE and current._failure_phase == phase
    def hook(kind):
        if should_fail(): raise error('JavascriptException')
    driver.hook = hook
    original_find = driver.find_elements
    def find(*args):
        if should_fail(): raise error('JavascriptException')
        return original_find(*args)
    driver.find_elements = find
    original_set = driver.set_page_load_timeout
    def configure(seconds):
        if seconds == 2 and should_fail(): raise error('JavascriptException')
        original_set(seconds)
    driver.set_page_load_timeout = configure
    result = current.capture()
    expected_page = 'media' if phase == 'timeout_setup' else 'deal_particulars'
    assert result['browser_failure'] == diagnostic(phase, 'javascript_error', True, expected_page)
    assert len(actions(driver)) == 1
    assert 'No tab/link navigation' not in result['stop_reason']
    assert_private_partial(result)


@pytest.mark.parametrize('variant', ['ordinary', 'private_class', 'spoofed_class', 'subclass', 'capture_subclass', 'unprintable'])
def test_unknown_spoofed_and_hostile_errors_are_generic_without_stringification(setup, variant):
    hub, driver, _ = setup
    kind = RuntimeError
    if variant == 'private_class':
        kind = type(PRIVATE, (Exception,), {})
    elif variant == 'spoofed_class':
        kind = type('TimeoutException', (Exception,), {'__module__': 'selenium.common.exceptions'})
    elif variant == 'subclass':
        kind = type(PRIVATE, (type(error('TimeoutException')),), {})
    elif variant == 'capture_subclass':
        kind = type(PRIVATE, (CaptureReadError,), {})
    elif variant == 'unprintable':
        class Unprintable(Exception):
            def __str__(self): pytest.fail('Never read exception text')
            def __repr__(self): pytest.fail('Never read exception representation')
        kind = Unprintable
    fail_navigation(driver, 'tab', kind(PRIVATE))
    result = session(hub).capture()
    assert result['browser_failure'] == diagnostic('tab_click', 'other_exception', True)
    assert_private_partial(result)


def test_optional_selenium_absence_stays_generic(setup, monkeypatch):
    hub, driver, _ = setup
    original = builtins.__import__
    def no_selenium(name, *args, **kwargs):
        if name == 'selenium.common.exceptions': raise ImportError(PRIVATE)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_selenium)
    fail_navigation(driver, 'link', RuntimeError(PRIVATE))
    result = session(hub).capture()
    assert result['browser_failure']['kind'] == 'other_exception'
    assert_private_partial(result)


@pytest.mark.parametrize('navigation', ['tab', 'link'])
@pytest.mark.parametrize('primary', ['driver', 'cancelled', 'keyboard', 'guard', 'none'])
def test_restoration_failure_does_not_mask_primary_or_cancellation(setup, navigation, primary):
    hub, driver, _ = setup
    current = session(hub)
    primary_error = {'driver': error('TimeoutException'), 'cancelled': module._Cancelled(),
                     'keyboard': KeyboardInterrupt(), 'guard': CaptureSafetyError('Synthetic fixed guard stop')}
    if primary == 'none':
        if navigation == 'tab': use_native_tabs(driver)
    else:
        fail_navigation(driver, navigation, primary_error[primary])
    fail_restoration(driver, error('InvalidSessionIdException'))
    result = current.capture()
    assert result['timeout_restore_failure'] == diagnostic('timeout_restore', 'invalid_session', True)
    assert result['status'] == ('cancelled' if primary in ('cancelled', 'keyboard') else 'stopped')
    if primary == 'driver':
        assert result['browser_failure'] == diagnostic(
            'tab_click' if navigation == 'tab' else 'link_navigation', 'timeout', True)
    else:
        assert result['browser_failure'] is None
    if primary == 'guard': assert result['stop_reason'].startswith('Synthetic fixed guard stop')
    if primary in ('cancelled', 'keyboard'): assert result['stop_reason'].startswith('Capture cancelled.')
    assert 'restoration is unverified' in result['stop_reason']
    assert driver.timeouts.page_load == 2
    assert driver.calls[-1] == ('restore_attempt', 300)
    assert len(actions(driver)) == len(result['pages']) == 1
    assert_private_partial(result)


@pytest.mark.parametrize('primary', ['setup', 'cancelled'])
def test_failed_restoration_before_dispatch_preserves_pre_navigation_state(setup, primary):
    hub, driver, _ = setup
    use_native_tabs(driver)
    current = session(hub)
    original = driver.set_page_load_timeout
    def configure(seconds):
        original(seconds)
        if seconds == 300: raise error('InvalidSessionIdException')
        if primary == 'setup': raise error('TimeoutException')
    driver.set_page_load_timeout = configure
    current.cancelled = lambda: primary == 'cancelled' and current._failure_phase == 'tab_precheck'
    result = current.capture()
    assert result['status'] == ('cancelled' if primary == 'cancelled' else 'stopped')
    assert result['timeout_restore_failure'] == diagnostic('timeout_restore', 'invalid_session')
    assert result['browser_failure'] == (None if primary == 'cancelled' else diagnostic('timeout_setup', 'timeout'))
    assert not actions(driver)
    assert_private_partial(result)


@pytest.mark.parametrize('has_primary', [False, True])
def test_keyboard_interrupt_in_restoration_is_honest_and_keeps_primary(setup, has_primary):
    hub, driver, _ = setup
    if has_primary: fail_navigation(driver, 'link', error('TimeoutException'))
    fail_restoration(driver, KeyboardInterrupt())
    result = session(hub).capture()
    assert result['status'] == ('stopped' if has_primary else 'cancelled')
    assert result['timeout_restore_failure'] == diagnostic('timeout_restore', 'interrupted', True)
    assert_private_partial(result)


@pytest.mark.parametrize('outcome', ['success', 'guard', 'poll_timeout', 'cancelled', 'wrapped_read'])
def test_existing_bounded_outcomes_are_not_reclassified_as_driver_errors(setup, outcome):
    hub, driver, _ = setup
    current = session(hub)
    if outcome == 'guard': driver.matches = False
    if outcome == 'poll_timeout': driver.ready = False
    if outcome == 'cancelled': current.cancelled = lambda: True
    if outcome == 'wrapped_read':
        def hook(kind):
            if kind == 'payload': raise error('JavascriptException')
        driver.hook = hook
    result = current.capture()
    assert result['browser_failure'] is result['timeout_restore_failure'] is None
    assert 'Matched capture diagnostic' not in (result['stop_reason'] or '')
    assert_private_partial(result)


@pytest.mark.parametrize('value', [None, [], PRIVATE,
    dict(diagnostic('tab_click', 'timeout'), phase=PRIVATE),
    dict(diagnostic('tab_click', 'timeout'), target_page_key=PRIVATE),
    dict(diagnostic('tab_click', 'timeout'), kind=PRIVATE),
    dict(diagnostic('tab_click', 'timeout'), navigation_attempted_in_capture=1),
    dict(diagnostic('tab_click', 'timeout'), kind=[]),
    dict(diagnostic('tab_click', 'timeout'), extra=PRIVATE)])
def test_renderer_rejects_nonallowlisted_values(value):
    assert module.matched_capture_failure_note(value) == ''


def test_initial_failure_before_page_is_known_never_records_a_route(setup):
    hub, driver, _ = setup
    current = session(hub)
    def failed_callback(): raise RuntimeError(PRIVATE)
    current.cancelled = failed_callback
    result = current.capture()
    assert result['browser_failure'] == diagnostic('source_check', 'other_exception', page=None)
    assert not driver.calls
    assert_private_partial(result)


def test_detached_results_source_integrity_immutable_store_and_old_record_compatibility(setup, monkeypatch, tmp_path):
    from quick_relist import room_capture
    hub, driver, _ = setup
    fail_navigation(driver, 'tab', error('TimeoutException'))
    fail_restoration(driver, error('InvalidSessionIdException'))
    monkeypatch.setattr(room_capture, 'capture_existing_room_options', lambda *a, **kw: {
        'status': 'partial', 'dialogs': [_room_dialog()], 'pending_dialogs': [], 'observations': [],
        'complete': False, 'server_writes_verified': False})
    current = session(hub)
    current.include_room_options = True
    result = current.capture()
    before = deepcopy(result)
    assert result['pages'][0]['dialogs'] == [_room_dialog()]
    inventory = extract_source_fields(result, expected_rxcu=REF)
    assert inventory['status'] == 'partial' and inventory['capture_status'] == 'stopped'
    assert result == before
    path = tmp_path / 'private' / 'captures.sqlite3'
    store = DraftStore(path)
    try:
        ident = store.save_capture('synthetic-job', result)
        result['browser_failure']['kind'] = PRIVATE
        result['timeout_restore_failure']['phase'] = PRIVATE
        result['pages'][0]['dialogs'].clear()
        assert current.get_result() == before
        restored = store.get_capture(ident)['capture']
        assert restored == before and verify_capture_integrity(restored)
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            store.conn.execute('UPDATE captures SET observation_json=? WHERE id=?', ('{}', ident))
        old = deepcopy(before)
        for key in ('browser_failure', 'timeout_restore_failure', 'integrity'): old.pop(key)
        old['stop_reason'] = 'Historical generic failure'
        old['integrity'] = {'algorithm': 'sha256', 'sha256': _digest(old)}
        legacy_id = store.save_capture('legacy-job', old)
        assert verify_capture_integrity(store.get_capture(legacy_id)['capture'])
        assert extract_source_fields(old, expected_rxcu=REF)['status'] == 'partial'
    finally:
        store.close()
    reopened = DraftStore(path)
    try:
        assert reopened.get_capture(ident)['capture'] == before
    finally:
        reopened.close()
    assert PRIVATE not in path.read_bytes().decode('utf-8', errors='ignore')
    assert_private_partial(before)


def test_existing_review_shows_bounded_diagnostics_without_ui_change(setup):
    widgets = pytest.importorskip('PySide6.QtWidgets')
    from quick_relist.ui import CapturedObservationsDialog
    hub, driver, _ = setup
    fail_navigation(driver, 'tab', error('TimeoutException'))
    fail_restoration(driver, error('InvalidSessionIdException'))
    result = session(hub).capture()
    app = widgets.QApplication.instance() or widgets.QApplication([])
    store = DraftStore(':memory:')
    dialog = None
    try:
        store.save_capture('synthetic-job', result)
        dialog = CapturedObservationsDialog({'rxcu': REF}, store.list_captures('synthetic-job'), store)
        dialog.show()
        app.processEvents()
        visible = dialog.coverage.text()
        assert 'tab click call, deal_particulars): timeout' in visible
        assert 'navigation timeout restoration' in visible
        assert 'INCOMPLETE' in visible
        saved = dialog.source_text.toPlainText()
        assert json.loads(saved) == result
        assert PRIVATE not in visible + saved
        assert len(actions(driver)) == 1
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            app.processEvents()
        store.close()


@pytest.mark.parametrize('comparison', ['claims_equal', 'raises'])
@pytest.mark.parametrize('base_kind', ['unknown', 'capture_subclass', 'selenium_subclass'])
@pytest.mark.parametrize('navigation', ['tab', 'link'])
@pytest.mark.parametrize('where', ['dispatch', 'cleanup'])
def test_hostile_metaclass_cannot_spoof_exact_types_or_force_stringification(
        setup, comparison, base_kind, navigation, where):
    hub, driver, _ = setup
    inspected = []
    base = (CaptureReadError if base_kind == 'capture_subclass' else
            type(error('TimeoutException')) if base_kind == 'selenium_subclass' else Exception)

    class PoisonedType(type):
        def __eq__(cls, other):
            inspected.append('equality')
            if comparison == 'raises':
                raise RuntimeError(PRIVATE)
            return True

        def __ne__(cls, other):
            pytest.fail('Never call exception metaclass inequality')

    class Unknown(base, metaclass=PoisonedType):
        def __str__(self):
            pytest.fail('Never stringify a metaclass-spoofed or subclass exception')

        def __repr__(self):
            pytest.fail('Never represent a metaclass-spoofed or subclass exception')

    failure = Unknown(PRIVATE)
    assert module._browser_failure_kind(failure) == 'other_exception'
    if where == 'dispatch':
        fail_navigation(driver, navigation, failure)
    else:
        if navigation == 'tab': use_native_tabs(driver)
        fail_restoration(driver, failure)
    result = session(hub).capture()
    assert result['status'] == 'stopped'
    field = 'browser_failure' if where == 'dispatch' else 'timeout_restore_failure'
    assert result[field]['kind'] == 'other_exception'
    assert result[field]['navigation_attempted_in_capture'] is True
    assert len(actions(driver)) == 1 and len(result['pages']) == 1
    assert inspected == []
    assert_private_partial(result)
