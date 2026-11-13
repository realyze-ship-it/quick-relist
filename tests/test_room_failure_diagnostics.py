"""Bounded room-opener diagnostics through real collectors, storage and review.

Only synthetic drivers, records and offscreen widgets. No browser or Hub use.
"""
import builtins
import json
from types import SimpleNamespace

import pytest

import test_room_capture as fixture
from test_room_capture import setup  # noqa: F401: shared synthetic pytest fixture
from test_room_capture_integration import IntegratedDriver
from test_matched_capture import NativeTab
import quick_relist.room_capture as room_module
import quick_relist.matched_capture as matched_module
from quick_relist.capture import READ_PAGE_SCRIPT, verify_capture_integrity
from quick_relist.drafts import DraftStore
from quick_relist.source_fields import extract_source_fields


PRIVATE = 'PRIVATE_SYNTHETIC_EXCEPTION_DATA'
KINDS = [
    ('ElementClickInterceptedException', 'click_intercepted', 'intercepted click'),
    ('StaleElementReferenceException', 'stale_element', 'stale element'),
    ('ElementNotInteractableException', 'not_interactable', 'element not interactable'),
    ('MoveTargetOutOfBoundsException', 'move_target_out_of_bounds', 'move target out of bounds'),
    ('TimeoutException', 'timeout', 'timeout'),
    ('WebDriverException', 'other_exception', 'unclassified exception'),
]


def selenium_error(name):
    exceptions = pytest.importorskip('selenium.common.exceptions')
    return getattr(exceptions, name)(PRIVATE, screen=PRIVATE, stacktrace=[PRIVATE])


def raise_on_click(driver, error):
    def hook(event):
        if event == 'click':
            raise error  # The fixture already scheduled an open: outcome is uncertain.
    driver.hook = hook


def assert_stopped_safely(result, driver, kind, room='bedroom'):
    assert result['status'] == 'stopped' and result['stop_code'] == 'open_uncertain'
    assert result['open_failure'] == {'kind': kind, 'room_key': room}
    assert result['dialogs'] == result['pending_dialogs'] == []
    assert result['cleanup_stop_code'] is None
    assert result['complete'] is result['server_writes_verified'] is False
    assert 'outcome remains uncertain' in result['stop_reason']
    assert 'opener was not retried' in result['stop_reason']
    assert PRIVATE not in json.dumps(result)
    assert fixture.actions(driver) == [('click', room)]
    assert driver.calls[-1] == ('click', room)  # No read, cleanup or retry after failure.
    assert driver.pending is not None  # Failure is never treated as proof of no click.


@pytest.mark.parametrize('name,kind,label', KINDS)
@pytest.mark.parametrize('room', [d.key for d in room_module.ROOM_OPTIONS])
def test_exact_known_exception_types_are_bounded_and_never_retried(setup, name, kind, label, room):
    hub, driver, _ = setup
    raise_on_click(driver, selenium_error(name))
    descriptor = next(d for d in room_module.ROOM_OPTIONS if d.key == room)
    result = fixture.capture(hub, descriptors=(descriptor,))
    assert_stopped_safely(result, driver, kind, room)
    assert label in result['stop_reason']
    assert name not in json.dumps(result)  # No arbitrary exception/class serialization.


@pytest.mark.parametrize('variant', ['ordinary', 'private_class', 'spoofed_class', 'subclass', 'unprintable'])
def test_unknown_and_spoofed_exceptions_remain_generic(setup, variant):
    hub, driver, _ = setup
    error_type = RuntimeError
    if variant == 'private_class':
        error_type = type(PRIVATE, (Exception,), {})
    elif variant == 'spoofed_class':
        error_type = type('ElementClickInterceptedException', (Exception,),
                          {'__module__': 'selenium.common.exceptions'})
    elif variant == 'subclass':
        errors = pytest.importorskip('selenium.common.exceptions')
        error_type = type(PRIVATE, (errors.ElementClickInterceptedException,), {})
    elif variant == 'unprintable':
        class Unprintable(Exception):
            def __str__(self):
                pytest.fail('Exception text must never be read')
            def __repr__(self):
                pytest.fail('Exception representation must never be read')
        error_type = Unprintable
    raise_on_click(driver, error_type(PRIVATE))
    result = fixture.capture(hub)
    assert_stopped_safely(result, driver, 'other_exception')


def test_missing_optional_selenium_keeps_generic_diagnostic(setup, monkeypatch):
    hub, driver, _ = setup
    original_import = builtins.__import__
    def no_selenium(name, *args, **kwargs):
        if name == 'selenium.common.exceptions':
            raise ImportError(PRIVATE)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_selenium)
    raise_on_click(driver, RuntimeError(PRIVATE))
    assert_stopped_safely(fixture.capture(hub), driver, 'other_exception')


@pytest.mark.parametrize('diagnostic', [None, [], PRIVATE,
    {'kind': PRIVATE, 'room_key': 'bedroom'},
    {'kind': 'click_intercepted', 'room_key': PRIVATE},
    {'kind': 'click_intercepted', 'room_key': 'bedroom', 'message': PRIVATE},
    {'kind': [], 'room_key': 'bedroom'}])
def test_note_renderer_rejects_nonallowlisted_data(diagnostic):
    assert room_module.room_open_failure_note(diagnostic) == ''


@pytest.mark.parametrize('outcome', ['success', 'timeout', 'cancelled', 'ambiguous'])
def test_nonexception_room_outcomes_do_not_gain_failure_classifications(setup, outcome):
    hub, driver, _ = setup
    if outcome == 'timeout':
        driver.open_delay = 100
    elif outcome == 'ambiguous':
        driver.shape_ok = False
    result = fixture.capture(hub, descriptors=(room_module.ROOM_OPTIONS[0],),
                             cancelled=lambda: outcome == 'cancelled')
    assert result['open_failure'] is None
    assert 'Room opener diagnostic' not in (result['stop_reason'] or '')
    assert result['stop_code'] == {'success': None, 'timeout': 'open_timeout',
                                  'cancelled': 'cancelled', 'ambiguous': 'opener_ambiguous'}[outcome]


def matched_failure(monkeypatch, error, navigation='native'):
    clock = SimpleNamespace(now=0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.now,
        sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds))
    monkeypatch.setattr(room_module, 'time', fake_time)
    monkeypatch.setattr(matched_module, 'time', fake_time)
    monkeypatch.setattr(room_module, '_keyboard_escape', lambda *_: pytest.fail('No blind cleanup'))

    class Driver(IntegratedDriver):
        def find_elements(self, by, selector):
            assert (by, selector) == ('css selector', matched_module.TAB_SELECTOR)
            return ([NativeTab(self, label, key) for label, key in matched_module.TAB_PAGES.items()]
                    if navigation == 'native' else [])

        def execute_script(self, script, *args):
            result = super().execute_script(script, *args)
            if navigation == 'links' and script == READ_PAGE_SCRIPT:
                result['editor_links'] = [{'url': fixture.BASE + '/media', 'text': 'Media'}]
            return result

        def set_page_load_timeout(self, _seconds):
            pytest.fail('No navigation may follow an uncertain room opener')

        def get(self, _url):
            pytest.fail('No GET may follow an uncertain room opener')

    driver = Driver(clock)
    driver.timeouts = SimpleNamespace(page_load=300)
    raise_on_click(driver, error)
    hub = SimpleNamespace(driver=driver, editor_bindings={fixture.REF:
        {'listing_id': '123456', 'handle': 'editor'}})
    result = matched_module.capture_matched_listing(hub, fixture.REF, include_room_options=True,
        timeout=2, poll_interval=.1, stable_for=.2)
    return result, driver


@pytest.mark.parametrize('name,kind,label', KINDS)
@pytest.mark.parametrize('navigation', ['native', 'links'])
def test_actual_matched_result_and_private_store_round_trip(monkeypatch, tmp_path, name, kind, label, navigation):
    result, driver = matched_failure(monkeypatch, selenium_error(name), navigation)
    assert result['status'] == 'stopped' and len(result['pages']) == 1
    assert result['pages'][0]['page_key'] == 'details'
    assert result['pages'][0]['fields'][0]['value'] == '2'
    assert result['pages'][0]['dialogs'] == result['pending_room_observations'] == []
    assert result['room_option_capture'][0]['open_failure'] == {'kind': kind, 'room_key': 'bedroom'}
    assert result['room_option_capture'][0]['stop_code'] == 'open_uncertain'
    assert label in result['stop_reason'] and 'outcome remains uncertain' in result['stop_reason']
    assert result['complete'] is result['live_ready'] is False
    assert result['coverage']['exhaustive'] is False
    assert result['matched_source']['account_verified'] is False
    assert result['matched_source']['fidelity_verified'] is False
    assert result['matched_source']['nested_dialogs_verified'] is False
    assert fixture.actions(driver) == [('click', 'bedroom')]
    assert driver.calls[-1] == ('click', 'bedroom')
    assert verify_capture_integrity(result)
    assert PRIVATE not in json.dumps(result)
    inventory = extract_source_fields(result, expected_rxcu=fixture.REF)
    assert inventory['status'] == 'partial' and inventory['capture_status'] == 'stopped'

    path = tmp_path / 'private-synthetic' / 'drafts.sqlite3'
    store = DraftStore(path)
    try:
        ident = store.save_capture('synthetic-job', result)
    finally:
        store.close()
    reopened = DraftStore(path)
    try:
        restored = reopened.get_capture(ident)['capture']
        assert restored == result
        assert verify_capture_integrity(restored)
        assert PRIVATE not in path.read_bytes().decode('utf-8', errors='ignore')
    finally:
        reopened.close()


def test_existing_saved_observation_review_displays_static_diagnostic(monkeypatch):
    widgets = pytest.importorskip('PySide6.QtWidgets')
    from quick_relist.ui import CapturedObservationsDialog
    app = widgets.QApplication.instance() or widgets.QApplication([])
    result, driver = matched_failure(monkeypatch, selenium_error('ElementClickInterceptedException'))
    store = DraftStore(':memory:')
    dialog = None
    try:
        store.save_capture('synthetic-job', result)
        dialog = CapturedObservationsDialog({'rxcu': fixture.REF}, store.list_captures('synthetic-job'), store)
        dialog.show()
        app.processEvents()
        visible = dialog.coverage.text()
        assert 'Room opener diagnostic (bedroom): intercepted click.' in visible
        assert 'outcome remains uncertain' in visible and 'opener was not retried' in visible
        assert 'INCOMPLETE' in visible
        saved_json = dialog.source_text.toPlainText()
        assert json.loads(saved_json) == result and 'click_intercepted' in saved_json
        assert PRIVATE not in visible + saved_json
        assert fixture.actions(driver) == [('click', 'bedroom')]
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            app.processEvents()
        store.close()
