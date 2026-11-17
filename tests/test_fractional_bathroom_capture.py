"""Synthetic fractional bathroom observations. No real Hub values or actions."""
from copy import deepcopy
from dataclasses import asdict
from html import escape
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import quick_relist.room_capture as rooms
import quick_relist.matched_capture as matched
from quick_relist.capture import READ_PAGE_SCRIPT, _digest, verify_capture_integrity
from quick_relist.drafts import DraftStore
from quick_relist.source_fields import extract_source_fields
from quick_relist.replacement_review import compare_replacement_fields
from quick_relist.transfer_plan import build_transfer_plan
from room_positioning_fixture import positioning_html
from test_room_capture import Driver, Element, REF, BASE, actions, capture, setup as setup_integer
from test_transfer_plan import full_capture, pins
from test_room_shape_runtime import extract_room  # Exact production extractor fixture.

BATHROOM = rooms.ROOM_OPTIONS[1]
RAW = '08.3750'  # Deliberately synthetic. No relationship to group cardinality.
FORMAT = 'positive_nonintegral_decimal'


def observation(raw=RAW):
    return {'format': FORMAT, 'raw': raw}


PROBE = r'''
const {JSDOM}=require('jsdom'), input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const w=new JSDOM(input.html,{url:input.url,runScripts:'outside-only'}).window;
Object.defineProperty(w.document,'readyState',{value:'complete'});
Object.defineProperty(w,'innerWidth',{value:800});Object.defineProperty(w,'innerHeight',{value:600});
Object.defineProperty(w.document.documentElement,'clientWidth',{value:800});
Object.defineProperty(w.document.documentElement,'clientHeight',{value:600});
w.Element.prototype.getClientRects=function(){return [{x:30,y:180,width:120,height:20}]};
let scrolls=0; const e=w.document.getElementById('opener');
w.document.elementFromPoint=()=>input.obscured?w.document.getElementById('help'):e;
w.Element.prototype.scrollIntoView=function(options){
 if(this!==e||++scrolls>1||JSON.stringify(options)!==JSON.stringify({behavior:'instant',block:'center',inline:'center'}))
   throw new Error('Unexpected scroll');
};
Object.defineProperty(w.document.querySelector('input'),'value',{value:input.raw,writable:true,configurable:true});
w.Function(input.setup||'')();
const denied=()=>{throw new Error('Prohibited read or business mutation')};
for(const key of ['localStorage','sessionStorage'])Object.defineProperty(w,key,{get:denied});
for(const key of ['fetch','XMLHttpRequest','WebSocket','open'])w[key]=denied;
Object.defineProperty(w.document,'cookie',{get:denied,set:denied});
w.HTMLElement.prototype.click=denied;w.HTMLFormElement.prototype.submit=denied;w.HTMLFormElement.prototype.requestSubmit=denied;
for(const selector of input.denied||[])for(const node of w.document.querySelectorAll(selector))
 Object.defineProperty(node,'value',{get:denied,set:denied});
const before=w.document.documentElement.outerHTML, url=w.location.href;
const state=w.Function(input.state)(input.descriptor);
const status=w.Function(input.position)(input.descriptor,e,input.expected,input.scroll===true);
if(before!==w.document.documentElement.outerHTML||url!==w.location.href)throw new Error('DOM or route changed');
if(state.feature)state.feature.element=state.feature.element===e?'verified-opener':null;
process.stdout.write(JSON.stringify({state,status,scrolls}));w.close();
'''


@pytest.fixture(scope='module')
def probe():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable; no installation attempted')
    check = subprocess.run([node, '-e', "require.resolve('jsdom')"], capture_output=True, text=True, timeout=10)
    if check.returncode:
        pytest.skip('jsdom unavailable; no installation attempted')

    def run(raw=RAW, descriptor=BATHROOM, expected=None, **kwargs):
        html = (positioning_html().replace('Set bedrooms options', descriptor.opener)
                .replace('feature-bedroom-input', descriptor.count_id)
                .replace('Bedrooms', descriptor.noun + 's'))
        html = html.replace('value="2"', 'value="' + escape(raw, quote=True) + '"')
        result = subprocess.run([node, '-e', PROBE], input=json.dumps(dict(
            html=html, raw=raw, url=BASE, descriptor=asdict(descriptor), state=rooms.ROOM_STATE_SCRIPT,
            position=rooms.ROOM_POSITION_SCRIPT, expected=observation(raw) if expected is None else expected,
            **kwargs)), text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    return run


@pytest.mark.parametrize('raw', [RAW, '0.625', '0006.1250', '31.0625'])
def test_production_probe_preserves_exact_raw_without_numeric_group_guess(probe, raw):
    result = probe(raw)
    assert result['state']['feature']['count'] is None
    assert result['state']['feature']['count_observation'] == observation(raw)
    assert result['status'] == 'clear' and result['scrolls'] == 0
    assert rooms._fractional_bathroom_count(BATHROOM, result['state']['feature']).raw == raw


UNSUPPORTED = ['', '0.0', '8.000', '8.', '.375', '-8.375', '+8.375', '8,375', '8e-1',
               'NaN', 'Infinity', ' 8.375', '8.375 ', '8.375\n', '8.375\r', '8.375\t',
               '８.３７５', '8. 375', '9' * 130 + '.375', '9007199254740992.375']


@pytest.mark.parametrize('raw', UNSUPPORTED)
def test_unsupported_formats_fail_probe_and_python_without_position_or_scroll(probe, raw):
    result = probe(raw, obscured=True, scroll=True)
    assert result['state']['feature']['count'] is None
    assert 'count_observation' not in result['state']['feature']
    assert result['status'] == 'changed' and result['scrolls'] == 0
    assert rooms._fractional_bathroom_count(BATHROOM, {'count': None, 'count_observation': observation(raw)}) is None


@pytest.mark.parametrize('descriptor', [d for d in rooms.ROOM_OPTIONS if d != BATHROOM])
def test_decimal_support_does_not_extend_to_other_categories(probe, descriptor):
    result = probe(descriptor=descriptor, obscured=True, scroll=True)
    assert result['state']['feature']['count'] is None
    assert 'count_observation' not in result['state']['feature']
    assert result['status'] == 'changed' and result['scrolls'] == 0
    assert rooms._fractional_bathroom_count(descriptor, {'count': None, 'count_observation': observation()}) is None


@pytest.mark.parametrize('expected', [observation('8.3750'), observation('08.375'), observation('08.6250'),
                                     {'format': 'integer', 'raw': RAW}, {'format': FORMAT, 'raw': RAW, 'extra': True},
                                     8, 9, 8.375, None, False, [], RAW])
def test_positioning_rechecks_same_raw_and_format(probe, expected):
    # JSON null must be passed explicitly rather than the helper's default.
    expected = {'format': FORMAT, 'raw': None} if expected is None else expected
    result = probe(expected=expected, obscured=True, scroll=True)
    assert result['status'] == 'changed' and result['scrolls'] == 0


@pytest.mark.parametrize('setup', [
    "document.querySelector('input').disabled=true", "document.querySelector('input').readOnly=true",
    "document.querySelector('input').type='hidden'", "document.querySelector('input').style.opacity='0'",
    "document.querySelector('input').setAttribute('name','unknown')",
    "document.querySelector('input').setAttribute('inputmode','decimal')",
    "document.querySelector('input').after(document.querySelector('input').cloneNode(true))",
    "document.getElementById('opener').appendChild(document.createElement('span'))",
])
def test_original_structural_checks_precede_count_read_and_scroll(probe, setup):
    result = probe(setup=setup, obscured=True, scroll=True, denied=['input'])
    assert result['status'] == 'changed' and result['scrolls'] == 0


def test_unrelated_hidden_input_not_read_and_single_scroll_is_view_only(probe):
    result = probe(setup="document.querySelector('main').insertAdjacentHTML('beforeend','<input type=hidden id=unrelated value=PRIVATE>')",
                   denied=['#unrelated'], obscured=True, scroll=True)
    assert result['status'] == 'scrolled' and result['scrolls'] == 1
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('descriptor', rooms.ROOM_OPTIONS)
@pytest.mark.parametrize('raw', ['2', '002'])
def test_integer_probe_and_positioning_keep_original_representation(probe, descriptor, raw):
    result = probe(raw, descriptor=descriptor, expected=2)
    assert result['state']['feature']['count'] == 2
    assert 'count_observation' not in result['state']['feature']
    assert result['status'] == 'clear'


class FractionalDriver(Driver):
    """Use exact production probe and page extraction results through controller."""
    def __init__(self, clock, feature, page):
        super().__init__(clock)
        self.feature = deepcopy(feature)
        self.raw = RAW
        self.dialog_page = deepcopy(page)
        self.position_status = self.after_scroll = 'clear'
        self.position_reads = self.scrolls = 0
        self.frozen_main = False
        self.position_error = self.scroll_error = None
        self.malformed_observation = None
        self.counts = {d.key: 0 for d in rooms.ROOM_OPTIONS}

    def find_elements(self, by, selector):
        assert by == 'css selector' and selector == matched.TAB_SELECTOR
        return []

    def execute_script(self, script, *args):
        if script == matched.READINESS_SCRIPT:
            return {'ready': True, 'busy': False, 'dialog': self.modal is not None}
        if script == rooms.ROOM_POSITION_SCRIPT:
            self.calls.append(('read', script))
            self.position_reads += 1
            assert args[0] == asdict(BATHROOM)
            if args[1] != self.openers['bathroom'] or args[2] != observation(self.raw):
                return 'changed'
            if self.position_error:
                raise self.position_error
            if args[3]:
                self.scrolls += 1
                self.calls.append(('scroll', 'bathroom'))
                self.position_status = self.after_scroll
                self.hook('scroll')
                if self.scroll_error:
                    raise self.scroll_error
                return 'scrolled'
            self.hook('position')
            return self.position_status
        if script == READ_PAGE_SCRIPT and self.modal is not None:
            self.calls.append(('read', script))
            result = deepcopy(self.dialog_page)
            self.hook('dialog_payload')
            return result
        result = super().execute_script(script, *args)
        if script == rooms.ROOM_STATE_SCRIPT and args[0]['key'] == 'bathroom':
            result['feature'] = deepcopy(self.feature)
            result['feature'].update(element=self.openers['bathroom'], shape_ok=self.shape_ok,
                opener_count=self.opener_count, count_count=self.count_count,
                count_observation=deepcopy(self.malformed_observation) if self.malformed_observation is not None else observation(self.raw))
        if script == READ_PAGE_SCRIPT and self.modal is None:
            result['fields'] = [{'id': BATHROOM.count_id, 'name': '', 'label': 'Bathrooms',
                'tag': 'input', 'type': 'text', 'value': RAW if self.frozen_main else self.raw,
                'disabled': False, 'read_only': False}]
        return result


@pytest.fixture(scope='module')
def fractional_payloads(probe, extract_room):
    return probe()['state']['feature'], extract_room(category='Bathroom', rooms=2)


@pytest.fixture
def setup_fractional(monkeypatch, fractional_payloads):
    clock = SimpleNamespace(now=0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.now,
        sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds))
    monkeypatch.setattr(rooms, 'time', fake_time)
    monkeypatch.setattr(matched, 'time', fake_time)
    monkeypatch.setattr(rooms, '_keyboard_escape', lambda active: active.modal.send_keys(rooms.ESCAPE))
    driver = FractionalDriver(clock, *fractional_payloads)
    hub = SimpleNamespace(driver=driver, editor_bindings={REF: {'listing_id': '123456', 'handle': 'editor'}})
    return hub, driver, clock


def run(hub, **kwargs):
    return capture(hub, descriptors=(BATHROOM,), **kwargs)


@pytest.mark.parametrize('group_count', [1, 2, 3, 7, 11, 200])
@pytest.mark.parametrize('scroll', [False, True])
def test_observed_group_cardinality_is_independent_of_display(setup_fractional, group_count, scroll):
    hub, driver, _ = setup_fractional
    template = driver.dialog_page['dialogs'][0]['room_groups'][0]
    driver.dialog_page['dialogs'][0]['room_groups'] = [dict(deepcopy(template), index=i, title=f'Bathroom {i}')
                                                    for i in range(1, group_count + 1)]
    if scroll:
        driver.position_status = 'needs_scroll'
    result = run(hub)
    assert result['stop_code'] is None and result['status'] == 'partial'
    assert actions(driver) == ([('scroll', 'bathroom')] if scroll else []) + [('click', 'bathroom'), ('escape', 'bathroom')]
    source = result['dialogs'][0]['source']
    assert source['feature_count'] is None
    assert source['feature_count_observation'] == observation()
    assert source['observed_room_group_count'] == group_count
    assert source['display_count_relationship_verified'] is False
    assert source['main_fields_unchanged_verified'] is source['close_verified'] is source['reference_checked_before_and_after'] is True
    assert source['complete'] is source['server_writes_verified'] is False
    assert not any(g['mapping_verified'] for g in result['dialogs'][0]['room_groups'])
    assert result['observations'] == [{'key': 'bathroom', 'status': 'observed', 'observed_room_group_count': group_count,
                                      'display_count_relationship_verified': False}]
    assert RAW not in json.dumps(result['observations']) and driver.modal is None


@pytest.mark.parametrize('raw', ['08.6250', '8.3750', '08.375', '8', '', '0.0'])
@pytest.mark.parametrize('stage', ['position', 'scroll'])
@pytest.mark.parametrize('frozen', [False, True])
def test_count_or_raw_format_change_stops_before_click(setup_fractional, raw, stage, frozen):
    hub, driver, _ = setup_fractional
    driver.frozen_main = frozen
    driver.position_status = 'needs_scroll'
    driver.hook = lambda event: setattr(driver, 'raw', raw) if event == stage else None
    result = run(hub)
    assert result['stop_code'] == ('opener_ambiguous' if frozen else 'main_changed')
    assert actions(driver) == ([('scroll', 'bathroom')] if stage == 'scroll' else [])
    assert not result['dialogs']
    assert RAW not in (result['stop_reason'] or '')
    assert result['observations'] == []


@pytest.mark.parametrize('change,code', [('binding','binding_changed'), ('reference','identity_changed'),
    ('driver','binding_changed'), ('tabs','position_changed'), ('handle','position_changed'), ('route','position_changed'),
    ('node','opener_ambiguous'), ('duplicate','opener_ambiguous'), ('shape','opener_ambiguous')])
def test_all_existing_position_guards_still_apply(setup_fractional, change, code):
    hub, driver, _ = setup_fractional
    driver.position_status = 'needs_scroll'
    def hook(event):
        if event != 'scroll': return
        if change == 'binding': hub.editor_bindings.clear()
        if change == 'reference': driver.matches = False
        if change == 'driver': hub.driver = object()
        if change == 'tabs': driver.window_handles.append('new')
        if change == 'handle': driver.current_window_handle = 'results'
        if change == 'route': driver.current_url += '/media'
        if change == 'node': driver.openers['bathroom'] = Element(driver, 'opener', BATHROOM)
        if change == 'duplicate': driver.count_count = 2
        if change == 'shape': driver.shape_ok = False
    driver.hook = hook
    result = run(hub)
    assert result['stop_code'] == code and actions(driver) == [('scroll', 'bathroom')]


@pytest.mark.parametrize('count_observation', [False, [], {}, {'raw': RAW}, {'format': FORMAT, 'raw': 8.375},
                                              {'format': FORMAT, 'raw': RAW, 'extra': 1}, observation('8.000')])
def test_malformed_or_ambiguous_wire_observation_never_opens(setup_fractional, count_observation):
    hub, driver, _ = setup_fractional
    driver.malformed_observation = count_observation
    result = run(hub)
    assert result['observations'] == [{'key': 'bathroom', 'status': 'skipped', 'reason': 'count_unqualified'}]
    assert not actions(driver)


@pytest.mark.parametrize('failure', ['empty', 'duplicate', 'gap', 'reverse', 'title', 'too_many',
                                    'room_group_structure_unresolved', 'dom_items_truncated', 'dom_text_truncated', 'rich_text_truncated'])
def test_structure_or_limit_failure_keeps_pending_and_escapes_once(setup_fractional, failure):
    hub, driver, _ = setup_fractional
    groups = driver.dialog_page['dialogs'][0]['room_groups']
    if failure == 'empty': groups.clear()
    elif failure == 'duplicate': groups[1]['index'] = 1
    elif failure == 'gap': groups[1].update(index=3, title='Bathroom 3')
    elif failure == 'reverse': groups.reverse()
    elif failure == 'title': groups[1]['title'] = 'Kitchen 2'
    elif failure == 'too_many':
        groups[:] = [dict(deepcopy(groups[0]), index=i, title=f'Bathroom {i}') for i in range(1, 202)]
    else: driver.dialog_page['limitations'].append(failure)
    result = run(hub)
    assert result['stop_code'] == 'room_groups_unresolved'
    assert not result['dialogs'] and len(result['pending_dialogs']) == 1
    assert actions(driver) == [('click', 'bathroom'), ('escape', 'bathroom')]
    assert result['pending_dialogs'][0]['source']['close_verified'] is False
    assert driver.modal is None


@pytest.mark.parametrize('event', ['position', 'scroll', 'click', 'dialog_payload', 'escape'])
def test_cancellation_issues_no_new_cleanup_actions(setup_fractional, event):
    hub, driver, _ = setup_fractional
    cancelled = SimpleNamespace(value=False)
    driver.position_status = 'needs_scroll'
    driver.hook = lambda kind: setattr(cancelled, 'value', True) if kind == event else None
    result = run(hub, cancelled=lambda: cancelled.value)
    expected = [] if event == 'position' else [('scroll', 'bathroom')]
    if event in ('click', 'dialog_payload', 'escape'): expected += [('click', 'bathroom')]
    if event == 'escape': expected += [('escape', 'bathroom')]
    assert result['stop_code'] == 'cancelled' and actions(driver) == expected


@pytest.mark.parametrize('failure,code', [('escape_error','escape_uncertain'), ('close_delay','close_timeout'),
                                         ('changed_raw','main_changed'), ('changed_reference','identity_changed')])
def test_failed_or_changed_close_never_promotes_or_retries(setup_fractional, failure, code):
    hub, driver, _ = setup_fractional
    if failure == 'escape_error': driver.escape_error = True
    if failure == 'close_delay': driver.close_delay = 100
    if failure == 'changed_raw': driver.hook = lambda event: setattr(driver, 'raw', '8.3750') if event == 'closed' else None
    if failure == 'changed_reference': driver.hook = lambda event: setattr(driver, 'matches', False) if event == 'closed' else None
    result = run(hub)
    assert result['stop_code'] == code and not result['dialogs'] and len(result['pending_dialogs']) == 1
    assert result['pending_dialogs'][0]['source']['close_verified'] is False
    assert actions(driver) == [('click', 'bathroom'), ('escape', 'bathroom')]
    assert 'PRIVATE' not in json.dumps(result) and RAW not in result['stop_reason']


@pytest.mark.parametrize('phase,code', [('position','opener_position_unreadable'), ('scroll','opener_scroll_uncertain'),
                                      ('click','open_uncertain')])
def test_uncertain_action_not_retried_and_no_raw_diagnostics(setup_fractional, phase, code):
    hub, driver, _ = setup_fractional
    driver.position_status = 'needs_scroll'
    if phase == 'position': driver.position_error = RuntimeError(RAW)
    elif phase == 'scroll': driver.scroll_error = RuntimeError(RAW)
    else: driver.click_error = True
    result = run(hub)
    assert result['stop_code'] == code
    assert actions(driver) == ([] if phase == 'position' else [('scroll', 'bathroom')] +
                               ([('click', 'bathroom')] if phase == 'click' else []))
    assert RAW not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)


def test_persistent_obstruction_never_clicks(setup_fractional):
    hub, driver, _ = setup_fractional
    driver.position_status = driver.after_scroll = 'needs_scroll'
    result = run(hub)
    assert result['stop_code'] == 'opener_obscured' and actions(driver) == [('scroll', 'bathroom')]


def test_matched_capture_persistence_inventory_hash_and_comparison_remain_unqualified(setup_fractional, tmp_path):
    hub, driver, _ = setup_fractional
    result = matched.capture_matched_listing(hub, REF, include_room_options=True,
                                             timeout=2, poll_interval=.1, stable_for=.2)
    assert result['status'] == 'partial' and verify_capture_integrity(result)
    assert len(result['pages'][0]['dialogs']) == 1
    assert actions(driver) == [('click', 'bathroom'), ('escape', 'bathroom')]
    assert RAW not in json.dumps(result['room_option_capture'])
    original = deepcopy(result)
    store = DraftStore(tmp_path / 'fractional-synthetic.sqlite3')
    try: ident = store.save_capture('synthetic-fractional-job', json.loads(json.dumps(result)))
    finally: store.close()
    store = DraftStore(tmp_path / 'fractional-synthetic.sqlite3')
    try: restored = store.get_capture(ident)['capture']
    finally: store.close()
    assert restored == original and verify_capture_integrity(restored)
    inventory = extract_source_fields(restored, expected_rxcu=REF)
    entry = next(e for e in inventory['entries'] if e['key'] == 'feature_bathroom')
    assert entry['value'] == RAW
    related = inventory['related_records'][0]
    assert related['status'] == 'unresolved'
    assert related['record']['source']['feature_count_observation'] == observation()
    target = full_capture(True)
    plan = build_transfer_plan(restored, target, **pins(restored, target))
    assert plan['status'] == 'partial_read_only' and plan['inventories']['source']['related_records'][0] == related
    assert not any(row['key'] == 'description' for group in plan['groups'].values() for row in group)
    review = compare_replacement_fields(restored, target, expected_original_rxcu=REF, expected_replacement_rxcu='RXCU23456')
    assert review['status'] == 'partial' and review['room_groups'] == []
    assert any('room_group_structure_unresolved' in row['issues'] for row in review['unresolved_coverage'])
    assert all(record['complete'] is record['live_ready'] is False for record in (restored, inventory, plan, review))
    assert restored['identity']['account_verified'] is False
    assert restored['matched_source']['fidelity_verified'] is False
    changed = deepcopy(restored)
    changed['pages'][0]['dialogs'][0]['source']['feature_count_observation']['raw'] = '8.3750'
    assert not verify_capture_integrity(changed)
    changed = deepcopy(restored)
    changed['pages'][0]['dialogs'][0]['source']['observed_room_group_count'] = 1
    assert not verify_capture_integrity(changed)
    assert restored == original


def test_exact_bathroom_description_structure_retains_visible_field_not_measure(extract_room):
    raw = extract_room(category='Bathroom', rooms=2,
                       denied=['textarea[aria-hidden="true"]'])
    groups = raw['dialogs'][0]['room_groups']
    assert [g['title'] for g in groups] == ['Bathroom 1', 'Bathroom 2']
    assert [g['fields'][-1]['name'] for g in groups] == ['description', 'description']
    assert [g['fields'][-1]['value'] for g in groups] == [
        '  Synthetic room 1\n  Second line  ', '  Synthetic room 2\n  Second line  ']
    assert not raw['limitations']
    assert 'SYNTHETIC_MEASURE_ONLY' not in json.dumps(raw)


@pytest.mark.parametrize('change', [
    "block.lastElementChild.remove()",
    "block.lastElementChild.querySelector('textarea').name='unknown'",
    "block.children[3].textContent='Unknown options'",
    "block.insertAdjacentHTML('beforeend','<input type=hidden value=PRIVATE_HIDDEN>')",
])
def test_damaged_bathroom_shape_remains_unresolved(extract_room, change):
    raw = extract_room(category='Bathroom', rooms=1,
                       setup="const block=document.querySelector('[data-fixture=room-1]');" + change,
                       denied=['input.PrivateSwitchBase-input'])
    assert 'room_group_structure_unresolved' in raw['limitations']
    assert raw['dialogs'][0]['room_groups'] == []
    assert 'PRIVATE_HIDDEN' not in json.dumps(raw)


def test_integer_orchestrator_keeps_legacy_source_and_summary_shapes(setup_integer):
    hub, driver, _ = setup_integer
    result = capture(hub, descriptors=(BATHROOM,))
    source = result['dialogs'][0]['source']
    assert source['feature_count'] == 2 and type(source['feature_count']) is int
    assert set(source) == {'rxcu', 'listing_id', 'page_key', 'opener', 'title', 'feature_count',
        'main_fields_sha256', 'reference_checked_before', 'reference_checked_before_and_after',
        'pinned_tab_checked', 'main_fields_unchanged_verified', 'close_verified', 'dom_stability_seconds',
        'observed_at', 'limitations', 'complete', 'server_writes_verified'}
    assert result['observations'] == [{'key': 'bathroom', 'status': 'observed', 'count': 2}]
    assert actions(driver) == [('click', 'bathroom'), ('escape', 'bathroom')]


def test_conflicting_integer_and_decimal_wire_counts_remain_unqualified(setup_fractional):
    hub, driver, _ = setup_fractional
    driver.feature['count'] = 8
    result = run(hub)
    assert result['observations'] == [{'key': 'bathroom', 'status': 'skipped', 'reason': 'count_unqualified'}]
    assert not actions(driver)


@pytest.mark.parametrize('phase', ['initial', 'recheck'])
def test_missing_count_metadata_is_not_an_explicit_null(setup_fractional, phase):
    hub, driver, _ = setup_fractional
    if phase == 'initial':
        driver.feature.pop('count')
    else:
        driver.hook = lambda event: driver.feature.pop('count', None) if event == 'position' else None
    result = run(hub)
    assert actions(driver) == []
    assert result['dialogs'] == [] and result['pending_dialogs'] == []
    if phase == 'initial':
        assert result['observations'] == [{'key': 'bathroom', 'status': 'skipped', 'reason': 'count_unqualified'}]
    else:
        assert result['stop_code'] == 'opener_ambiguous'
        assert result['observations'] == []
