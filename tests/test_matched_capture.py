"""Synthetic matched-source fixtures. No live Hub or mutation API."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from quick_relist.capture import EDITOR_PAGES, READ_PAGE_SCRIPT, verify_capture_integrity
from quick_relist.hub_readonly import IDENTITY_SCRIPT
from quick_relist.matched_capture import READINESS_SCRIPT, MatchedHubCapture, capture_matched_listing
import quick_relist.matched_capture as module

BASE = 'https://hub.remax.co.za/app/listings/edit/123456'
REF = 'RXCU12345'
URLS = [BASE, BASE + '/deal_particulars', BASE + '/media', BASE + '/portals']


def observation(url, links=()):
    return {'href': url, 'ready_state': 'complete',
            'fields': [{'name': 'synthetic', 'type': 'text', 'value': 'hydrated'}],
            'rich_text': [], 'dialogs': [], 'sections': [], 'lists': [], 'tables': [],
            'media': [], 'editor_links': [{'url': link, 'text': 'Editor tab'} for link in links],
            'discovery': [], 'limitations': []}


class Driver:
    def __init__(self):
        self.current_url = BASE
        self.current_window_handle = 'editor'
        self.window_handles = ['results', 'editor']
        self.pages = {url: observation(url, URLS) for url in URLS}
        self.calls = []
        self.hook = lambda kind: None
        self.timeouts = SimpleNamespace(page_load=300.0)
        self.ready = True
        self.busy = False
        self.dialog = False
        self.matches = True
        self.identity_missing = 0
        self.hydrating = 0
        self.after_navigation = lambda: None

    def execute_script(self, script, *args):
        from quick_relist.tab_positioning import TAB_POSITION_SCRIPT
        if script == TAB_POSITION_SCRIPT:
            self.calls.append(('tab_position', args[-1]))
            self.hook('tab_position')
            return 'clear'
        assert script in (READ_PAGE_SCRIPT, IDENTITY_SCRIPT, READINESS_SCRIPT)
        self.calls.append(('read', script))
        if script == IDENTITY_SCRIPT:
            assert args == (REF,)
            absent = self.identity_missing > 0
            self.identity_missing = max(0, self.identity_missing - 1)
            result = {'outer_count': 0 if absent else 1, 'inner_count': 0 if absent else 1,
                      'outer_matches': self.matches and not absent, 'inner_matches': self.matches and not absent}
            self.hook('identity')
            return result
        if script == READINESS_SCRIPT:
            result = {'ready': self.ready, 'busy': self.busy, 'dialog': self.dialog}
            self.hook('readiness')
            return result
        result = deepcopy(self.pages[self.current_url])
        if self.hydrating:
            result['fields'][0]['value'] = 'loading-' + str(self.hydrating)
            self.hydrating -= 1
        self.hook('payload')
        return result

    def find_elements(self, by, selector):
        assert by == 'css selector'
        assert selector == module.TAB_SELECTOR
        return []

    def set_page_load_timeout(self, seconds):
        self.timeouts.page_load = seconds
        self.calls.append(('timeout', seconds))

    def get(self, url):
        assert self.timeouts.page_load <= 2
        assert any(url == link['url'] for page in self.pages.values() for link in page['editor_links'])
        self.calls.append(('get', url))
        self.current_url = url
        self.after_navigation()


@pytest.fixture
def setup(monkeypatch):
    clock = SimpleNamespace(now=0)
    def sleep(seconds):
        clock.now += seconds
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    driver = Driver()
    hub = SimpleNamespace(driver=driver, editor_bindings={REF: {'listing_id': '123456', 'handle': 'editor'}})
    return hub, driver, clock


def capture(hub, **kwargs):
    return capture_matched_listing(hub, 'RXCU-12345', timeout=2, poll_interval=.1, stable_for=.2, **kwargs)


def assert_partial(result):
    assert result['complete'] is False
    assert result['live_ready'] is False
    assert result['coverage']['exhaustive'] is False
    assert result['matched_source']['account_verified'] is False
    assert result['matched_source']['fidelity_verified'] is False
    assert result['matched_source']['nested_dialogs_verified'] is False
    assert verify_capture_integrity(result)


def test_traverse_all_observed_tabs_after_async_hydration(setup):
    hub, driver, clock = setup
    driver.hydrating = 3
    def hydrate():
        driver.identity_missing = 2
        driver.hydrating = 3
    driver.after_navigation = hydrate
    result = capture(hub)
    assert result['status'] == 'partial'
    assert result['stop_reason'] is None
    assert result['coverage']['observed_pages'] == list(EDITOR_PAGES)
    assert [page['fields'][0]['value'] for page in result['pages']] == ['hydrated'] * 4
    assert [call[1] for call in driver.calls if call[0] == 'get'] == URLS[1:]
    assert result['identity']['reference_verified'] is True
    assert result['identity']['page_binding_verified'] is False
    assert driver.timeouts.page_load == 300.0
    assert result['matched_source']['all_accepted_observations_guarded'] is True
    assert all(p['matched_source']['reference_checked_before_and_after'] for p in result['pages'])
    assert_partial(result)


def test_missing_links_and_unsafe_links_do_not_guess_routes(setup):
    hub, driver, _ = setup
    driver.pages[BASE]['editor_links'] = [{'url': BASE + '/media?secret=synthetic'},
        {'url': BASE.replace('123456', '999999')}, {'url': 'https://example.com/edit'}]
    result = capture(hub)
    assert result['coverage']['observed_pages'] == ['details']
    assert result['coverage']['missing_pages'] == list(EDITOR_PAGES)[1:]
    assert not any(c[0] == 'get' for c in driver.calls)
    assert 'synthetic' not in str(result['pages'][0]['editor_links'])
    assert_partial(result)


def test_capture_can_return_from_media_to_observed_particulars_link(setup):
    hub, driver, _ = setup
    details_url = BASE + '/particulars'
    urls = [details_url, *URLS[1:]]
    driver.pages = {url: observation(url, urls) for url in urls}
    driver.current_url = BASE + '/media'
    result = capture(hub)
    assert result['stop_reason'] is None
    assert set(result['coverage']['observed_pages']) == set(EDITOR_PAGES)
    details = [page for page in result['pages'] if page['page_key'] == 'details']
    assert len(details) == 1
    assert details[0]['page_path'] == '/app/listings/edit/123456/particulars'
    assert_partial(result)


@pytest.mark.parametrize('change', ['wrong_reference', 'wrong_handle', 'wrong_route', 'no_binding', 'nonnumeric', 'missing_heading'])
def test_stale_or_unmatched_initial_editor_rejected(setup, change):
    hub, driver, _ = setup
    if change == 'wrong_reference': driver.matches = False
    if change == 'wrong_handle': driver.current_window_handle = 'results'
    if change == 'wrong_route': driver.current_url = URLS[0].replace('123456', '777777')
    if change == 'no_binding': hub.editor_bindings.clear()
    if change == 'nonnumeric': hub.editor_bindings[REF]['listing_id'] = 'not-numeric'
    if change == 'missing_heading': driver.identity_missing = 1
    result = capture(hub)
    assert result['status'] == 'stopped'
    assert result['pages'] == []
    assert result['identity']['reference_verified'] is False
    assert not any(script == READ_PAGE_SCRIPT for kind, script in driver.calls)
    assert_partial(result)


@pytest.mark.parametrize('change', ['window_set', 'selected_tab', 'reference', 'binding', 'driver', 'route', 'dialog'])
def test_changes_during_payload_never_accept_page(setup, change):
    hub, driver, _ = setup
    def hook(kind):
        if kind != 'payload': return
        if change == 'window_set': driver.window_handles.append('unexpected')
        if change == 'selected_tab': driver.current_window_handle = 'results'
        if change == 'reference': driver.matches = False
        if change == 'binding': hub.editor_bindings.clear()
        if change == 'driver': hub.driver = Driver()
        if change == 'route': driver.current_url = URLS[1]
        if change == 'dialog': driver.dialog = True
    driver.hook = hook
    result = capture(hub)
    assert result['status'] == 'stopped'
    assert not result['pages']
    assert_partial(result)


def test_later_wrong_identity_retains_only_guarded_progress(setup):
    hub, driver, _ = setup
    driver.after_navigation = lambda: setattr(driver, 'matches', False)
    result = capture(hub)
    assert result['status'] == 'stopped'
    assert result['coverage']['observed_pages'] == ['details']
    assert result['matched_source']['all_accepted_observations_guarded'] is True
    assert len([c for c in driver.calls if c[0] == 'get']) == 1
    assert_partial(result)


@pytest.mark.parametrize('failure', [RuntimeError('private fake password'), KeyboardInterrupt()])
def test_failed_or_interrupted_navigation_preserves_progress_and_no_retry(setup, failure):
    hub, driver, _ = setup
    def fail(): raise failure
    driver.after_navigation = fail
    result = capture(hub)
    assert result['status'] == ('cancelled' if isinstance(failure, KeyboardInterrupt) else 'stopped')
    assert result['coverage']['observed_pages'] == ['details']
    assert 'private fake' not in str(result)
    assert len([c for c in driver.calls if c[0] == 'get']) == 1
    assert_partial(result)


def test_cancellation_between_pages_preserves_history(setup):
    hub, driver, _ = setup
    result = capture(hub, cancelled=lambda: driver.current_url != BASE)
    assert result['status'] == 'cancelled'
    assert result['coverage']['observed_pages'] == ['details']
    assert_partial(result)


def test_cancel_before_any_reads(setup):
    hub, driver, _ = setup
    result = capture(hub, cancelled=lambda: True)
    assert result['status'] == 'cancelled'
    assert not result['pages'] and not driver.calls
    assert_partial(result)


@pytest.mark.parametrize('block', ['busy', 'not_ready', 'empty', 'changing', 'dialog'])
def test_readiness_is_bounded_and_empty_or_unsettled_page_not_accepted(setup, block):
    hub, driver, clock = setup
    if block == 'busy': driver.busy = True
    if block == 'not_ready': driver.ready = False
    if block == 'empty': driver.pages[BASE]['fields'] = []
    if block == 'changing': driver.hydrating = 1000
    if block == 'dialog': driver.dialog = True
    result = capture(hub)
    assert result['status'] == 'stopped'
    assert not result['pages']
    assert clock.now <= 2.1
    assert not any(c[0] == 'get' for c in driver.calls)
    assert_partial(result)


def test_unknown_browser_failure_safe_and_one_shot_result_detached(setup):
    hub, driver, _ = setup
    def fail(kind): raise RuntimeError('https://private.example?token=secret')
    driver.hook = fail
    session = MatchedHubCapture(hub, REF)
    result = session.capture()
    assert result['status'] == 'stopped'
    assert 'token' not in str(result)
    result['pages'].append({})
    assert session.get_result()['pages'] == []
    with pytest.raises(ValueError): session.capture()


def test_navigation_timeout_restored_after_uncertain_failure(setup):
    hub, driver, _ = setup
    def timeout():
        assert driver.timeouts.page_load == 2
        raise TimeoutError('private fake URL')
    driver.after_navigation = timeout
    result = capture(hub)
    assert result['status'] == 'stopped'
    assert len(result['pages']) == 1
    assert driver.timeouts.page_load == 300
    assert [call for call in driver.calls if call[0] == 'timeout'] == [('timeout', 2), ('timeout', 300)]
    assert len([call for call in driver.calls if call[0] == 'get']) == 1
    assert_partial(result)


def test_timeout_setup_failure_does_not_navigate(setup):
    hub, driver, _ = setup
    def reject(seconds):
        if seconds == 2: raise RuntimeError('driver unavailable')
    driver.set_page_load_timeout = reject
    result = capture(hub)
    assert result['status'] == 'stopped'
    assert len(result['pages']) == 1
    assert not any(call[0] == 'get' for call in driver.calls)
    assert_partial(result)


def test_cancel_during_navigation_configuration_stops_before_get(setup):
    hub, driver, _ = setup
    cancelled = [False]
    original = driver.set_page_load_timeout
    def timeout(seconds):
        original(seconds)
        cancelled[0] = True
    driver.set_page_load_timeout = timeout
    result = capture(hub, cancelled=lambda: cancelled[0])
    assert result['status'] == 'cancelled'
    assert len(result['pages']) == 1
    assert not any(call[0] == 'get' for call in driver.calls)
    assert driver.timeouts.page_load == 300
    assert_partial(result)


def test_page_links_discovered_only_on_later_pages_are_traversed(setup):
    hub, driver, _ = setup
    for index, url in enumerate(URLS):
        driver.pages[url] = observation(url, URLS[index + 1:index + 2])
    result = capture(hub)
    assert result['coverage']['observed_pages'] == list(EDITOR_PAGES)
    assert_partial(result)


def test_unexpected_redirect_after_get_preserves_earlier_page(setup):
    hub, driver, _ = setup
    driver.after_navigation = lambda: setattr(driver, 'current_url', BASE + '/media?unexpected=1')
    result = capture(hub)
    assert result['status'] == 'stopped'
    assert len(result['pages']) == 1
    assert 'unexpected=1' not in str(result)
    assert_partial(result)


def test_missing_reference_after_payload_is_not_retried(setup):
    hub, driver, _ = setup
    driver.hook = lambda kind: setattr(driver, 'identity_missing', 2) if kind == 'payload' else None
    result = capture(hub)
    assert result['status'] == 'stopped'
    assert result['pages'] == []
    assert len([c for c in driver.calls if c == ('read', READ_PAGE_SCRIPT)]) == 1
    assert_partial(result)


class NativeTab:
    tag_name = 'button'

    def __init__(self, driver, label, key=None):
        self.driver, self.label, self.key = driver, label, key
        self.enabled = True
        self.visible = True
        self.attrs = {}

    @property
    def text(self):
        raise AssertionError('Use observed textContent, not CSS-transformed .text')

    def is_displayed(self): return self.visible
    def is_enabled(self): return self.enabled

    def get_attribute(self, name):
        selected = self.key == module.parse_editor_url(self.driver.current_url)['page_key']
        values = {'textContent': self.label, 'role': 'tab', 'type': 'button',
                  'aria-selected': 'true' if selected else 'false',
                  'tabindex': '0' if selected else '-1', 'aria-disabled': None}
        return self.attrs.get(name, values.get(name))

    def click(self):
        assert self.label in module.TAB_PAGES, 'Never click unrelated tabs'
        assert self.enabled and self.visible
        assert self.driver.timeouts.page_load <= 2
        self.driver.calls.append(('tab_click', self.key))
        self.driver.on_tab_click(self.key)


def use_native_tabs(driver):
    # Actual Hub buttons have no href. Include every observed unrelated tab to
    # ensure none can be clicked by a broad button/text matching heuristic.
    tabs = [NativeTab(driver, label, key) for label, key in module.TAB_PAGES.items()]
    tabs += [NativeTab(driver, label) for label in ('Stats', 'Property management', 'Show days', 'Notes', 'History')]
    driver.find_elements = lambda by, selector: tabs if (by, selector) == ('css selector', module.TAB_SELECTOR) else []
    driver.on_tab_click = lambda key: setattr(driver, 'current_url', URLS[list(EDITOR_PAGES).index(key)])
    for page in driver.pages.values(): page['editor_links'] = []
    return tabs


def test_actual_native_buttons_with_no_href_traverse_only_four_pages(setup):
    hub, driver, _ = setup
    use_native_tabs(driver)
    result = capture(hub)
    assert result['status'] == 'partial'
    assert result['coverage']['observed_pages'] == list(EDITOR_PAGES)
    assert [c[1] for c in driver.calls if c[0] == 'tab_click'] == list(EDITOR_PAGES)[1:]
    assert not any(c[0] == 'get' for c in driver.calls)
    assert driver.timeouts.page_load == 300
    assert_partial(result)


def test_native_tabs_wait_for_disabled_controls_delayed_route_and_hydration(setup, monkeypatch):
    hub, driver, clock = setup
    tabs = use_native_tabs(driver)
    for tab in tabs: tab.enabled = False
    pending = []
    original_sleep = module.time.sleep
    def click(key): pending[:] = [key, clock.now + .2]
    driver.on_tab_click = click
    def sleep(seconds):
        original_sleep(seconds)
        if clock.now >= .3:
            for tab in tabs: tab.enabled = True
        if pending and clock.now >= pending[1]:
            driver.current_url = URLS[list(EDITOR_PAGES).index(pending[0])]
            driver.identity_missing = 2
            driver.hydrating = 3
            pending.clear()
    monkeypatch.setattr(module.time, 'sleep', sleep)
    result = capture(hub)
    assert result['status'] == 'partial'
    assert result['coverage']['observed_pages'] == list(EDITOR_PAGES)
    assert all(p['fields'][0]['value'] == 'hydrated' for p in result['pages'])
    assert len([c for c in driver.calls if c[0] == 'tab_click']) == 3
    assert_partial(result)


@pytest.mark.parametrize('failure', ['raises', 'wrong_route', 'wrong_reference', 'modal', 'no_navigation', 'cancel', 'new_window'])
def test_native_tab_uncertainty_retains_progress_and_never_retries(setup, failure):
    hub, driver, _ = setup
    use_native_tabs(driver)
    cancelled = [False]
    def click(key):
        if failure == 'raises': raise RuntimeError('private browser information')
        if failure == 'wrong_route': driver.current_url = URLS[2]
        if failure == 'wrong_reference': driver.matches = False
        if failure == 'modal': driver.dialog = True
        if failure == 'cancel': cancelled[0] = True
        if failure == 'new_window': driver.window_handles.append('unexpected')
        # no_navigation deliberately leaves the source URL unchanged.
    driver.on_tab_click = click
    result = capture(hub, cancelled=lambda: cancelled[0])
    assert result['status'] == ('cancelled' if failure == 'cancel' else 'stopped')
    assert result['coverage']['observed_pages'] == ['details']
    assert len([c for c in driver.calls if c[0] == 'tab_click']) == 1
    assert driver.timeouts.page_load == 300
    assert 'private browser' not in str(result)
    assert_partial(result)


@pytest.mark.parametrize('bad', ['duplicate', 'wrong_type', 'missing_selected', 'wrong_tabindex', 'unrelated_selected'])
def test_native_tab_ambiguity_never_clicks(setup, bad):
    hub, driver, _ = setup
    tabs = use_native_tabs(driver)
    if bad == 'duplicate': tabs.append(NativeTab(driver, 'Media', 'media'))
    if bad == 'wrong_type': tabs[1].attrs['type'] = 'submit'
    if bad == 'missing_selected': tabs[1].attrs['aria-selected'] = None
    if bad == 'wrong_tabindex': tabs[0].attrs['tabindex'] = '-1'
    if bad == 'unrelated_selected': tabs[-1].attrs['aria-selected'] = 'true'
    result = capture(hub)
    assert result['status'] == 'stopped'
    assert not any(c[0] in ('tab_click', 'get') for c in driver.calls)
    assert_partial(result)


def test_native_missing_tabs_are_not_synthesized(setup):
    hub, driver, _ = setup
    tabs = use_native_tabs(driver)
    tabs[:] = [t for t in tabs if t.key in ('details', 'media')]
    result = capture(hub)
    assert result['status'] == 'partial'
    assert result['coverage']['observed_pages'] == ['details', 'media']
    assert [c[1] for c in driver.calls if c[0] == 'tab_click'] == ['media']
    assert_partial(result)


def _room_dialog(**source_updates):
    source = {'rxcu': REF, 'listing_id': '123456', 'page_key': 'details',
              'opener': 'Set bedrooms options', 'title': 'Set bedrooms options', 'feature_count': 1,
              'reference_checked_before_and_after': True, 'pinned_tab_checked': True,
              'main_fields_unchanged_verified': True, 'close_verified': True,
              'complete': False, 'server_writes_verified': False}
    source.update(source_updates)
    return {'title': 'Set bedrooms options', 'fields': [{'name': 'has_carpets', 'value': 'on', 'type': 'checkbox', 'checked': True}],
            'rich_text': [], 'room_groups': [{'title': 'Bedroom 1', 'index': 1, 'fields': [], 'rich_text': []}], 'source': source}


def test_optional_room_capture_merges_only_guarded_dialogs_and_rehashes(setup, monkeypatch):
    from quick_relist import room_capture
    hub, driver, _ = setup
    calls = []
    def rooms(*args, **kwargs):
        calls.append((args, kwargs))
        return {'status': 'partial', 'dialogs': [_room_dialog()], 'pending_dialogs': [], 'observations': [],
                'stop_reason': None, 'stop_code': None, 'complete': False, 'server_writes_verified': False}
    monkeypatch.setattr(room_capture, 'capture_existing_room_options', rooms)
    result = capture(hub, include_room_options=True)
    assert len(calls) == 1 and calls[0][0] == (hub, REF)
    assert calls[0][1]['expected_listing_id'] == '123456'
    assert calls[0][1]['expected_url'] == BASE
    assert result['pages'][0]['dialogs'] == [_room_dialog()]
    assert result['pages'][0]['room_options_observed'] == 1
    assert result['pending_room_observations'] == []
    assert result['coverage']['observed_pages'] == list(EDITOR_PAGES)
    assert_partial(result)


@pytest.mark.parametrize('status', ['stopped', 'cancelled'])
def test_uncertain_room_dialog_stays_separate_and_stops_tab_navigation(setup, monkeypatch, status):
    from quick_relist import room_capture
    hub, driver, _ = setup
    pending = _room_dialog(reference_checked_before_and_after=False, close_verified=False)
    monkeypatch.setattr(room_capture, 'capture_existing_room_options', lambda *a, **kw: {
        'status': status, 'dialogs': [_room_dialog()], 'pending_dialogs': [pending], 'observations': [],
        'stop_reason': 'Synthetic stop', 'stop_code': 'close_unverified', 'complete': False, 'server_writes_verified': False})
    result = capture(hub, include_room_options=True)
    assert result['status'] == status
    assert len(result['pages']) == 1
    assert result['pages'][0]['fields'][0]['value'] == 'hydrated'
    assert result['pages'][0]['dialogs'] == [_room_dialog()]
    assert result['pending_room_observations'] == [pending]
    assert 'room_dialog_close_or_identity_unverified' in result['limitations']
    assert not any(call[0] == 'get' for call in driver.calls)
    assert_partial(result)


def test_room_dialog_guard_mismatch_is_not_promoted_to_accepted_page(setup, monkeypatch):
    from quick_relist import room_capture
    hub, driver, _ = setup
    conflicting = _room_dialog(listing_id='999999')
    monkeypatch.setattr(room_capture, 'capture_existing_room_options', lambda *a, **kw: {
        'status': 'partial', 'dialogs': [conflicting], 'pending_dialogs': [], 'observations': []})
    result = capture(hub, include_room_options=True)
    assert result['status'] == 'stopped'
    assert result['pages'][0]['dialogs'] == []
    assert result['pending_room_observations'] == [conflicting]
    assert_partial(result)


def test_room_capture_not_enabled_implicitly(setup, monkeypatch):
    from quick_relist import room_capture
    hub, driver, _ = setup
    def forbidden(*args, **kwargs): raise AssertionError('Room capture was not requested')
    monkeypatch.setattr(room_capture, 'capture_existing_room_options', forbidden)
    result = capture(hub)
    assert result['status'] == 'partial'
    assert result['room_option_capture'] == []
    assert result['pending_room_observations'] == []
