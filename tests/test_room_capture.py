"""Synthetic, offline tests. No real listing data, browser, or Hub writes."""
from copy import deepcopy
from types import SimpleNamespace
import json
import os
import shutil
import subprocess

import pytest

from quick_relist.capture import READ_PAGE_SCRIPT
from quick_relist.hub_readonly import IDENTITY_SCRIPT
from quick_relist.room_capture import (
    ESCAPE, ROOM_OPTIONS, ROOM_POSITION_SCRIPT, ROOM_STATE_SCRIPT, RoomOptionDescriptor,
    capture_existing_room_options,
)
import quick_relist.room_capture as module

BASE = 'https://hub.remax.co.za/app/listings/edit/123456'
REF = 'RXCU12345'


def payload(url):
    return {'href': url, 'ready_state': 'complete', 'fields': [
        {'name': 'synthetic', 'type': 'text', 'value': 'unchanged'}],
        'rich_text': [], 'dialogs': [], 'sections': [], 'lists': [], 'tables': [],
        'media': [], 'editor_links': [], 'discovery': [], 'limitations': []}


class Element:
    def __init__(self, driver, kind, descriptor):
        self.driver, self.kind, self.descriptor = driver, kind, descriptor

    def click(self):
        assert self.kind == 'opener', 'No dialog button may be clicked'
        assert self.descriptor in ROOM_OPTIONS
        d = self.driver
        assert d.modal is None
        d.calls.append(('click', self.descriptor.key))
        d.pending = self.descriptor
        d.open_at = d.clock.now + d.open_delay
        d.hook('click')
        if d.click_error:
            raise RuntimeError('PRIVATE DRIVER VALUE')

    def send_keys(self, key):
        assert self.kind == 'dialog' and key == ESCAPE
        d = self.driver
        assert d.modal is self
        d.calls.append(('escape', self.descriptor.key))
        d.close_at = d.clock.now + d.close_delay
        d.hook('escape')
        if d.escape_error:
            raise RuntimeError('PRIVATE DRIVER VALUE')


class Driver:
    def __init__(self, clock):
        self.clock = clock
        self.current_window_handle = 'editor'
        self.window_handles = ['editor', 'results']
        self.current_url = BASE
        self.calls = []
        self.modal = None
        self.pending = None
        self.open_at = self.close_at = float('inf')
        self.open_delay = self.close_delay = .2
        self.click_error = self.escape_error = False
        self.counts = {d.key: 2 for d in ROOM_OPTIONS}
        self.openers = {d.key: Element(self, 'opener', d) for d in ROOM_OPTIONS}
        self.extra_overlay = False
        self.expected_dialog = True
        self.opener_count = self.count_count = 1
        self.shape_ok = True
        self.matches = True
        self.main_value = 'unchanged'
        self.hook = lambda kind: None
        self.group_error = False
        self.dialog_payload_error = False
        self.unstable_dialog = False
        self.ready = True
        self.busy = False

    def transition(self):
        if self.pending is not None and self.clock.now >= self.open_at:
            self.modal = Element(self, 'dialog', self.pending)
            self.pending = None
            self.open_at = float('inf')
        if self.modal is not None and self.clock.now >= self.close_at:
            self.modal = None
            self.close_at = float('inf')
            self.hook('closed')

    def execute_script(self, script, *args):
        assert script in (IDENTITY_SCRIPT, READ_PAGE_SCRIPT, ROOM_STATE_SCRIPT, ROOM_POSITION_SCRIPT)
        self.transition()
        self.calls.append(('read', script))
        if script == ROOM_POSITION_SCRIPT:
            descriptor = next(d for d in ROOM_OPTIONS if d.key == args[0]['key'])
            assert args[1] is self.openers[descriptor.key] and args[2] == self.counts[descriptor.key]
            assert args[3] is False, 'Legacy clean visible fixture never needs scrolling'
            self.hook('position')
            return 'clear'
        if script == IDENTITY_SCRIPT:
            assert self.modal is None, 'Hidden background identity must not be inspected during a modal'
            result = {'outer_count': 1, 'inner_count': 1,
                      'outer_matches': self.matches, 'inner_matches': self.matches}
            self.hook('identity')
            return result
        if script == ROOM_STATE_SCRIPT:
            descriptor = next(d for d in ROOM_OPTIONS if d.key == args[0]['key'])
            overlays = [] if self.modal is None else [{'element': self.modal, 'expected': self.expected_dialog}]
            if self.extra_overlay:
                overlays.append({'element': 'unrelated', 'expected': False})
            result = {'href': self.current_url, 'ready': self.ready, 'busy': self.busy,
                      'overlays': overlays, 'feature': {'opener_count': self.opener_count,
                          'count_count': self.count_count, 'shape_ok': self.shape_ok,
                          'element': self.openers[descriptor.key], 'count': self.counts[descriptor.key]}}
            self.hook('state')
            return result
        result = payload(self.current_url)
        if self.modal is None:
            result['fields'][0]['value'] = self.main_value
            self.hook('main_payload')
        else:
            if self.dialog_payload_error:
                raise RuntimeError('PRIVATE DRIVER VALUE')
            descriptor = self.modal.descriptor
            groups = [{'title': f'{descriptor.noun} {i}', 'index': i,
                'fields': [{'name': 'description', 'type': 'textarea', 'value': f'Synthetic room {i}'}],
                'rich_text': []} for i in range(1, self.counts[descriptor.key] + 1)]
            if self.group_error:
                groups[-1]['index'] = 1
            dialog = {'title': descriptor.opener, 'fields': [], 'rich_text': [],
                      'text': 'Synthetic rooms', 'room_groups': groups, 'unknown_metadata': 'preserved'}
            if self.unstable_dialog:
                dialog['text'] += str(self.clock.now)
            result['fields'] = []
            result['dialogs'] = [dialog]
            self.hook('dialog_payload')
        return result


@pytest.fixture
def setup(monkeypatch):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.now,
        sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds)))
    driver = Driver(clock)
    monkeypatch.setattr(module, '_keyboard_escape', lambda active: active.modal.send_keys(ESCAPE))
    hub = SimpleNamespace(driver=driver, editor_bindings={REF: {'listing_id': '123456', 'handle': 'editor'}})
    return hub, driver, clock


def capture(hub, **kwargs):
    return capture_existing_room_options(hub, 'RXCU-12345', expected_listing_id='123456',
        expected_url=BASE, timeout=2, poll_interval=.1, stable_for=.2, **kwargs)


def actions(driver):
    return [(a, b) for a, b in driver.calls if a != 'read']


def test_capture_five_delayed_existing_dialogs_and_preserve_full_records(setup):
    hub, driver, clock = setup
    result = capture(hub)
    assert result['status'] == 'partial' and result['stop_reason'] is None
    assert result['pending_dialogs'] == [] and len(result['dialogs']) == len(ROOM_OPTIONS)
    assert actions(driver) == [(action, d.key) for d in ROOM_OPTIONS for action in ('click', 'escape')]
    for dialog, descriptor in zip(result['dialogs'], ROOM_OPTIONS):
        assert dialog['title'] == descriptor.opener
        assert dialog['unknown_metadata'] == 'preserved'
        assert len(dialog['room_groups']) == 2
        source = dialog['source']
        assert source['reference_checked_before_and_after'] is True
        assert source['main_fields_unchanged_verified'] is True
        assert source['close_verified'] is True
        assert source['main_fields_sha256'] == result['main_fields_fingerprint']
        assert source['complete'] is False and source['server_writes_verified'] is False
    assert driver.modal is None and clock.now > 2
    assert result['complete'] is False and result['server_writes_verified'] is False


@pytest.mark.parametrize('count,reason', [(0, 'zero_existing_rooms'), (None, 'count_unqualified'), (2.5, 'count_unqualified')])
def test_zero_or_unqualified_counts_never_click(setup, count, reason):
    hub, driver, _ = setup
    driver.counts['bedroom'] = count
    result = capture(hub, descriptors=(ROOM_OPTIONS[0],))
    assert result['status'] == 'partial' and result['dialogs'] == []
    assert result['observations'] == [{'key': 'bedroom', 'status': 'skipped', 'reason': reason}]
    assert not actions(driver)


@pytest.mark.parametrize('attribute,value', [('opener_count', 2), ('count_count', 2), ('shape_ok', False)])
def test_ambiguous_or_changed_openers_never_click(setup, attribute, value):
    hub, driver, _ = setup
    setattr(driver, attribute, value)
    result = capture(hub)
    assert result['stop_code'] == 'opener_ambiguous' and not actions(driver)


@pytest.mark.parametrize('kind', ['binding', 'driver', 'window', 'handle', 'route', 'reference'])
def test_initial_source_guard_changes_rejected(setup, kind):
    hub, driver, _ = setup
    if kind == 'binding': hub.editor_bindings.clear()
    if kind == 'driver': hub.driver = None
    if kind == 'window': driver.window_handles = ['results']
    if kind == 'handle': driver.current_window_handle = 'results'
    if kind == 'route': driver.current_url += '/media'
    if kind == 'reference': driver.matches = False
    result = capture(hub)
    assert result['status'] == 'stopped' and not actions(driver)


@pytest.mark.parametrize('kind', ['binding', 'driver', 'window', 'handle', 'route'])
def test_mid_modal_guard_change_does_not_escape_changed_context(setup, kind):
    hub, driver, _ = setup
    def hook(event):
        if event != 'dialog_payload': return
        if kind == 'binding': hub.editor_bindings.clear()
        if kind == 'driver': hub.driver = object()
        if kind == 'window': driver.window_handles.append('new')
        if kind == 'handle': driver.current_window_handle = 'results'
        if kind == 'route': driver.current_url += '/media'
    driver.hook = hook
    result = capture(hub)
    assert result['status'] == 'stopped' and result['dialogs'] == []
    assert actions(driver) == [('click', 'bedroom')]


@pytest.mark.parametrize('kind', ['wrong_title', 'duplicate'])
def test_unexpected_open_outcome_left_for_operator(setup, kind):
    hub, driver, _ = setup
    def hook(event):
        if event == 'click':
            if kind == 'wrong_title': driver.expected_dialog = False
            else: driver.extra_overlay = True
    driver.hook = hook
    result = capture(hub)
    assert result['stop_code'] == 'dialog_changed'
    assert actions(driver) == [('click', 'bedroom')]


@pytest.mark.parametrize('kind', ['exception', 'timeout'])
def test_uncertain_open_never_retried_or_blind_closed(setup, kind):
    hub, driver, _ = setup
    if kind == 'exception': driver.click_error = True
    else: driver.open_delay = 100
    result = capture(hub)
    assert result['stop_code'] == ('open_uncertain' if kind == 'exception' else 'open_timeout')
    assert actions(driver) == [('click', 'bedroom')]
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['payload_error', 'unstable', 'groups'])
def test_verified_modal_read_failure_escapes_once_and_checks_main(setup, kind):
    hub, driver, _ = setup
    if kind == 'payload_error': driver.dialog_payload_error = True
    if kind == 'unstable': driver.unstable_dialog = True
    if kind == 'groups': driver.group_error = True
    result = capture(hub)
    assert result['stop_code'] == {'payload_error': 'dialog_unreadable', 'unstable': 'dialog_timeout',
                                   'groups': 'room_groups_unresolved'}[kind]
    assert actions(driver) == [('click', 'bedroom'), ('escape', 'bedroom')]
    assert driver.modal is None and not result['dialogs']
    assert bool(result['pending_dialogs']) == (kind == 'groups')
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['exception', 'timeout'])
def test_failed_escape_is_uncertain_preserves_pending_and_never_retries(setup, kind):
    hub, driver, _ = setup
    if kind == 'exception': driver.escape_error = True
    else: driver.close_delay = 100
    result = capture(hub)
    assert result['stop_code'] == ('escape_uncertain' if kind == 'exception' else 'close_timeout')
    assert actions(driver) == [('click', 'bedroom'), ('escape', 'bedroom')]
    assert not result['dialogs'] and len(result['pending_dialogs']) == 1
    assert result['pending_dialogs'][0]['source']['close_verified'] is False
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['fields', 'reference'])
def test_changed_post_close_source_not_promoted(setup, kind):
    hub, driver, _ = setup
    def hook(event):
        if event == 'closed':
            if kind == 'fields': driver.main_value = 'changed'
            else: driver.matches = False
    driver.hook = hook
    result = capture(hub)
    assert result['stop_code'] == ('main_changed' if kind == 'fields' else 'identity_changed')
    assert not result['dialogs'] and len(result['pending_dialogs']) == 1
    assert actions(driver) == [('click', 'bedroom'), ('escape', 'bedroom')]


@pytest.mark.parametrize('event', ['before', 'click', 'dialog_payload', 'escape'])
def test_cancellation_checked_between_rpcs_without_cleanup_after_cancel(setup, event):
    hub, driver, _ = setup
    cancelled = SimpleNamespace(value=event == 'before')
    driver.hook = lambda kind: setattr(cancelled, 'value', True) if kind == event else None
    result = capture(hub, cancelled=lambda: cancelled.value)
    assert result['status'] == 'cancelled' and result['stop_code'] == 'cancelled'
    assert actions(driver) == ([] if event == 'before' else [('click', 'bedroom')] +
                               ([('escape', 'bedroom')] if event == 'escape' else []))


@pytest.mark.parametrize('kind', ['wrong_dialog', 'escape', 'cancel', 'count_shape'])
def test_earlier_successful_dialog_survives_later_interruption(setup, kind):
    hub, driver, _ = setup
    cancelled = SimpleNamespace(value=False)
    def hook(event):
        if event == 'click' and driver.pending.key == 'bathroom':
            if kind == 'wrong_dialog': driver.expected_dialog = False
            if kind == 'escape': driver.escape_error = True
            if kind == 'cancel': cancelled.value = True
        if event == 'closed' and len([x for x in actions(driver) if x[0] == 'escape']) == 1 and kind == 'count_shape':
            driver.shape_ok = False
    driver.hook = hook
    result = capture(hub, cancelled=lambda: cancelled.value)
    assert result['status'] in ('stopped', 'cancelled')
    assert len(result['dialogs']) == 1
    assert result['dialogs'][0]['source']['close_verified'] is True
    assert result['dialogs'][0]['source']['opener'] == ROOM_OPTIONS[0].opener


def test_preexisting_overlay_is_not_dismissed(setup):
    hub, driver, _ = setup
    driver.extra_overlay = True
    result = capture(hub)
    assert result['stop_code'] == 'initial_dialog' and not actions(driver)


def test_unknown_descriptor_and_invalid_wait_limits_rejected_before_browser_use(setup):
    hub, driver, _ = setup
    with pytest.raises(ValueError):
        capture(hub, descriptors=(RoomOptionDescriptor('bad', 'Add option', 'bad', 'Bad'),))
    with pytest.raises(ValueError):
        capture_existing_room_options(hub, REF, expected_listing_id='123456', expected_url=BASE, timeout=float('nan'))
    assert not driver.calls


# Execute the actual read-only structural probe against offline DOM fixtures.
# Uses the repository's already documented optional jsdom installation only.
@pytest.fixture(scope='module')
def probe():
    node = shutil.which('node')
    env = dict(os.environ)
    if not env.get('NODE_PATH') and os.path.isdir('/tmp/quick-relist-dom-runtime/node_modules'):
        env['NODE_PATH'] = '/tmp/quick-relist-dom-runtime/node_modules'
    if not node or subprocess.run([node, '-e', "require.resolve('jsdom')"], env=env,
                                  capture_output=True, timeout=10).returncode:
        pytest.skip('Optional Node/jsdom runtime unavailable')
    runner = r"""
const {JSDOM}=require('jsdom'); const fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const w=new JSDOM(input.html,{url:input.url,runScripts:'outside-only'}).window;
Object.defineProperty(w.document,'readyState',{value:'complete'});
w.Element.prototype.getClientRects=function(){return [{x:0,y:0,width:10,height:10}]};
const denied=()=>{throw new Error('Prohibited side effect or private read')};
w.HTMLElement.prototype.click=denied; w.HTMLFormElement.prototype.submit=denied;
for(const k of ['fetch','XMLHttpRequest','WebSocket','open']) w[k]=denied;
for(const k of ['localStorage','sessionStorage']) Object.defineProperty(w,k,{get:denied});
Object.defineProperty(w.document,'cookie',{get:denied,set:denied});
const before=w.document.documentElement.outerHTML;
const result=w.Function(input.script)(input.descriptor);
if(before!==w.document.documentElement.outerHTML) throw new Error('Probe mutated DOM');
if(result.feature?.element) result.feature.element='verified-opener';
for(const overlay of result.overlays) overlay.element='visible-dialog';
process.stdout.write(JSON.stringify(result)); w.close();
"""
    def run(html):
        completed = subprocess.run([node, '-e', runner], env=env, input=json.dumps({
            'html': html, 'url': BASE, 'script': ROOM_STATE_SCRIPT,
            'descriptor': {'key': 'bedroom', 'opener': 'Set bedrooms options',
                           'count_id': 'feature-bedroom-input', 'noun': 'Bedroom'}}),
            text=True, capture_output=True, timeout=20)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    return run


HTML = '''<main><div><div class="flex alignItems_flexend"><h6>Bedrooms</h6>
<div class="MuiFormControl-root"><div class="MuiInputBase-root">
<input id="feature-bedroom-input" type="text" value="2"></div></div></div>
<div><span class="MuiTypography-caption">Set bedrooms options</span></div></div></main>'''


def test_actual_probe_matches_exact_caption_and_count_structure(probe):
    state = probe(HTML)
    assert state['feature'] == {'opener_count': 1, 'count_count': 1, 'shape_ok': True,
                                'count': 2, 'element': 'verified-opener'}


@pytest.mark.parametrize('before,after', [
    ('type="text"', 'type="number"'), ('type="text"', 'type="text" name="bedrooms"'),
    ('type="text"', 'type="text" disabled'), ('type="text"', 'type="text" readonly'),
    ('MuiTypography-caption"', 'MuiTypography-caption" role="button"'),
    ('MuiTypography-caption"', 'MuiTypography-caption" title="edit"'),
    ('MuiTypography-caption"', 'MuiTypography-caption" tabindex="0"'),
    ('class="flex alignItems_flexend"', 'class="wrong"'),
])
def test_actual_probe_rejects_changed_semantics(probe, before, after):
    assert probe(HTML.replace(before, after))['feature']['shape_ok'] is False


@pytest.mark.parametrize('value,expected', [('0', 0), ('2.5', None), ('-1', None), (' 2 ', None), ('1e2', None)])
def test_actual_probe_does_not_infer_room_count(probe, value, expected):
    assert probe(HTML.replace('value="2"', f'value="{value}"'))['feature']['count'] == expected


def test_actual_probe_ignores_hidden_onboarding_and_requires_exact_labelled_h2(probe):
    hidden = '<div role="dialog" aria-hidden="true"><h2>Onboarding</h2></div>'
    assert probe(HTML + hidden)['overlays'] == []
    modal = '<div role="dialog" aria-labelledby="dynamic-42"><h2 id="dynamic-42">Set bedrooms options</h2></div>'
    state = probe(HTML + hidden + modal)
    assert state['overlays'] == [{'element': 'visible-dialog', 'expected': True}]
    assert probe(HTML + modal.replace('Set bedrooms options', 'Done'))['overlays'][0]['expected'] is False


def test_actual_probe_never_matches_add_option_or_whitespace_label(probe):
    assert probe(HTML.replace('Set bedrooms options', 'Add option'))['feature']['opener_count'] == 0
    assert probe(HTML.replace('Set bedrooms options', ' Set bedrooms options'))['feature']['opener_count'] == 0


def test_expected_accepted_page_baseline_prevents_stale_page_pairing(setup):
    from quick_relist.capture import _digest
    hub, driver, _ = setup
    prior = payload(BASE)
    fingerprint = _digest({k: prior[k] for k in ('fields', 'rich_text')})
    driver.main_value = 'changed after accepted details observation'
    result = capture(hub, expected_main_fingerprint=fingerprint)
    assert result['stop_code'] == 'main_changed'
    assert not result['dialogs'] and not actions(driver)


def test_expected_accepted_page_baseline_succeeds_without_timestamp_dependency(setup):
    from quick_relist.capture import _digest
    hub, driver, _ = setup
    prior = payload(BASE)
    fingerprint = _digest({k: prior[k] for k in ('fields', 'rich_text')})
    result = capture(hub, expected_main_fingerprint=fingerprint)
    assert result['status'] == 'partial' and result['main_fields_fingerprint'] == fingerprint


@pytest.mark.parametrize('state_key,value', [('busy', True), ('ready', False)])
def test_post_read_busy_or_unready_dialog_never_becomes_accepted(setup, state_key, value):
    hub, driver, _ = setup
    reads = SimpleNamespace(count=0)
    def hook(event):
        if event == 'dialog_payload':
            reads.count += 1
            if reads.count == 4:
                setattr(driver, state_key, value)
        if event == 'escape':
            driver.busy = False
            driver.ready = True
    driver.hook = hook
    result = capture(hub, descriptors=(ROOM_OPTIONS[0],))
    assert result['stop_code'] == 'dialog_timeout'
    assert not result['dialogs'] and not result['pending_dialogs']
    assert actions(driver) == [('click', 'bedroom'), ('escape', 'bedroom')]


def test_capture_error_and_failed_cleanup_both_have_safe_visible_reasons(setup):
    hub, driver, _ = setup
    driver.dialog_payload_error = True
    driver.escape_error = True
    result = capture(hub)
    assert result['stop_code'] == 'dialog_unreadable'
    assert result['cleanup_stop_code'] == 'escape_uncertain'
    assert 'Escape outcome is uncertain' in result['stop_reason']
    assert 'PRIVATE' not in result['stop_reason']
    assert actions(driver) == [('click', 'bedroom'), ('escape', 'bedroom')]


def test_escape_uses_keyboard_action_without_targeting_nonfocusable_dialog(monkeypatch):
    actions_module = pytest.importorskip('selenium.webdriver.common.action_chains')
    calls = []
    driver = object()
    class Actions:
        def __init__(self, target):
            assert target is driver
            calls.append('create')
        def send_keys(self, key):
            assert key == ESCAPE
            calls.append('escape')
            return self
        def perform(self):
            calls.append('perform')
    monkeypatch.setattr(actions_module, 'ActionChains', Actions)
    module._keyboard_escape(driver)
    assert calls == ['create', 'escape', 'perform']
