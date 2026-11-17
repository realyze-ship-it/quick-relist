"""Production room extractor against synthetic jsdom, not native layout/Hub.

The bounded rectangle shim models visibility only. Actual load is awaited;
network/storage/actions are denied, excluded values are poisoned using scoped
selectors (never duplicate-ID global lookup), and live input/textarea states are
compared before/after extraction. All data is synthetic.
"""
from copy import deepcopy
import json
import shutil
import subprocess
from types import SimpleNamespace
import pytest

from quick_relist.capture import READ_PAGE_SCRIPT, _validate_observation, verify_capture_integrity
from quick_relist.drafts import DraftStore
from quick_relist.source_fields import extract_source_fields
from quick_relist.transfer_plan import build_transfer_plan
from room_shape_fixture import SECTIONS, TITLE_SETUP, fixture_html
from test_segmented_dates_runtime import WAIT_FOR_COMPLETE
from test_transfer_plan import full_capture, seal, pins

URL = 'https://hub.remax.co.za/app/listings/edit/123456'  # Existing synthetic fixture identity.
RUNNER = WAIT_FOR_COMPLETE + r'''
const {JSDOM}=require('jsdom'), input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
(async()=>{
 const dom=new JSDOM(input.html,{url:input.url,pretendToBeVisual:true,runScripts:'outside-only'}), w=dom.window;
 try {
  await waitForComplete(w);
  Object.defineProperty(w.Element.prototype,'getClientRects',{value:function(){
   for(let e=this;e;e=e.parentElement) if(e.hidden||w.getComputedStyle(e).display==='none'||(e.tagName==='DIALOG'&&!e.open)) return [];
   return [{x:0,y:0,width:100,height:20,top:0,right:100,bottom:20,left:0}];
  }});
  w.Function(input.setup)();
  const denied=()=>{throw new Error('Prohibited side effect or excluded value read');};
  for(const key of ['localStorage','sessionStorage']) Object.defineProperty(w,key,{get:denied});
  Object.defineProperty(w.document,'cookie',{get:denied,set:denied});
  for(const key of ['fetch','XMLHttpRequest','WebSocket','open']) w[key]=denied;
  w.HTMLElement.prototype.click=denied;
  w.HTMLFormElement.prototype.submit=denied;w.HTMLFormElement.prototype.requestSubmit=denied;
  const ig=Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype,'value').get;
  const tg=Object.getOwnPropertyDescriptor(w.HTMLTextAreaElement.prototype,'value').get;
  const signature=()=>JSON.stringify([w.document.documentElement.outerHTML,w.location.href,
   [...w.document.querySelectorAll('input,textarea')].map(e=>[e.id,(e.tagName==='INPUT'?ig:tg).call(e),e.checked,e.indeterminate,e.disabled,e.readOnly])]);
  const before=signature();
  for(const selector of input.deny_selectors){
   const nodes=[...w.document.querySelectorAll(selector)];if(!nodes.length) throw new Error('Missing synthetic excluded control');
   for(const e of nodes) Object.defineProperty(e,'value',{get:denied,set:denied});
  }
  const result=w.Function(input.script)();
  if(signature()!==before) throw new Error('Extractor changed DOM or controls');
  process.stdout.write(JSON.stringify(result));
 } finally {dom.window.close();}
})().catch(e=>{process.stderr.write(String(e.stack||e));process.exitCode=1;});
'''


@pytest.fixture(scope='module')
def extract_room():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable; no installation attempted')
    p = subprocess.run([node, '-e', "require.resolve('jsdom')"], capture_output=True, text=True, timeout=10)
    if p.returncode:
        if 'MODULE_NOT_FOUND' in p.stderr:
            pytest.skip('Optional jsdom unavailable; no installation attempted')
        pytest.fail(p.stderr)

    def extract(html=None, *, setup='', denied=(), rooms=2, category='Bedroom'):
        p = subprocess.run([node, '-e', RUNNER], input=json.dumps(dict(html=html or fixture_html(category, rooms=rooms),
            url=URL, setup=TITLE_SETUP + setup, script=READ_PAGE_SCRIPT, deny_selectors=list(denied))),
            capture_output=True, text=True, timeout=30)
        assert p.returncode == 0, p.stderr
        raw = json.loads(p.stdout)
        assert raw['ready_state'] == 'complete'
        assert _validate_observation(raw, URL)['dialogs'] == raw['dialogs']
        return raw
    return extract


def unresolved(raw, indexes=()):
    assert [g['index'] for g in raw['dialogs'][0]['room_groups']] == list(indexes)
    assert 'room_group_structure_unresolved' in raw['limitations']


@pytest.mark.parametrize('category', SECTIONS)
def test_exact_categories_repeated_ids_local_labels_and_raw_state(extract_room, category):
    raw = extract_room(category=category, setup='''
      const inputs=[...document.querySelectorAll('input[name=has_built_in_cupboards]')];
      inputs[0].checked=false;inputs[1].checked=true;inputs[0].value=' 0007 ';inputs[1].value='007';
      inputs[0].disabled=true;inputs[0].readOnly=true;inputs[0].setAttribute('data-indeterminate','false');
      inputs[0].parentElement.classList.add('Mui-checked');
    ''')
    assert len(raw['dialogs']) == 1
    groups = raw['dialogs'][0]['room_groups']
    assert [g['title'] for g in groups] == [category + ' 1', category + ' 2']
    assert [len(g['fields']) for g in groups] == [len(SECTIONS[category]) + 1] * 2
    fields = [g['fields'][0] for g in groups]
    assert [f['id'] for f in fields] == ['has_built_in_cupboards'] * 2
    assert [f['label'] for f in fields] == ['Synthetic has_built_in_cupboards 1', 'Synthetic has_built_in_cupboards 2']
    assert [f['checked'] for f in fields] == [False, True]
    assert [f['value'] for f in fields] == [' 0007 ', '007']
    assert fields[0]['disabled'] is fields[0]['read_only'] is True
    assert all(f['observed_room_checkbox_proxy'] for f in fields)
    assert not any(g['mapping_verified'] for g in groups)
    assert groups[0]['fields'][-1]['value'] == '  Synthetic room 1\n  Second line  '
    assert not raw['limitations']
    assert 'SYNTHETIC_MEASURE_ONLY' not in json.dumps(raw)
    assert 'SYNTHETIC_HIDDEN_MODAL' not in json.dumps(raw)


SHAPE_CHANGES = [
    "block.firstElementChild.textContent='Bedroom 0'", "block.firstElementChild.textContent='Bedroom 01'",
    "block.firstElementChild.textContent='Bedroom 9007199254740992'", "block.firstElementChild.textContent='Bathroom 1'",
    "block.children[3].textContent='Unknown options'", "block.children[3].hidden=true",
    "for(const h of [...block.querySelectorAll('h6')].slice(1)) h.remove()",
    "for(const h of [...block.querySelectorAll('h6')].slice(1)){const p=document.createElement('p');p.textContent=h.textContent;h.replaceWith(p)}",
    "block.children[4].insertAdjacentHTML('beforeend','<h6>Bedroom 9</h6>')",
    "block.children[4].insertAdjacentHTML('beforeend','<section><input value=SYNTHETIC_NESTED></section>')",
    "block.children[4].classList.remove('MuiGrid-container')",
    "block.children[4].firstElementChild.classList.remove('MuiGrid-grid-sm-4')",
    "block.children[1].insertAdjacentHTML('beforeend','<button>Unknown action</button>')",
    "block.children[1].insertAdjacentHTML('beforeend','<a href=about:blank>Unknown link</a>')",
    "block.lastElementChild.insertAdjacentHTML('beforeend','<input value=SYNTHETIC_EXTRA>')",
    "block.lastElementChild.children[0].setAttribute('for','unknown')",
    "block.lastElementChild.children[1].children[0].name='unknown'",
    "block.lastElementChild.children[1].children[1].removeAttribute('aria-hidden')",
    "block.lastElementChild.children[1].children[1].name='unknown'",
    "block.lastElementChild.children[1].children[2].removeAttribute('aria-hidden')",
    "block.querySelector('input[name=has_open_plan]').id=block.querySelector('input[name=has_open_plan]').name='has_built_in_cupboards'",
]


@pytest.mark.parametrize('change', SHAPE_CHANGES)
def test_unknown_shapes_do_not_merge_or_fall_back_to_legacy(extract_room, change):
    raw = extract_room(rooms=1, setup="const block=document.querySelector('[data-fixture=room-1]');" + change,
                       denied=['input.PrivateSwitchBase-input'])
    unresolved(raw)


PROXY_CHANGES = [
    "input.style.display='none'", "input.style.visibility='hidden'", "input.style.position='static'",
    "input.hidden=true", "input.setAttribute('aria-hidden','true')", "input.setAttribute('aria-label','Synthetic alias')",
    "input.setAttribute('aria-labelledby','other')", "input.setAttribute('tabindex','-1')",
    "input.type='hidden'", "input.type='password'", "input.type='radio'", "input.name='other'",
    "input.style.opacity='0';input.classList.remove('PrivateSwitchBase-input')", "input.getClientRects=()=>[]",
    "input.parentElement.classList.remove('MuiCheckbox-root')", "input.parentElement.style.opacity='0'",
    "input.parentElement.lastElementChild.removeAttribute('aria-hidden')",
    "input.parentElement.lastElementChild.removeAttribute('focusable')",
    "label.setAttribute('for',input.id)", "label.classList.remove('MuiFormControlLabel-root')", "label.hidden=true",
    "label.setAttribute('aria-label','Password')", "label.lastElementChild.textContent=''",
    "label.lastElementChild.hidden=true", "label.lastElementChild.innerHTML='<span>Nested caption</span>'",
    "label.insertAdjacentHTML('beforeend','<input type=hidden value=SYNTHETIC_EXTRA>')",
]


@pytest.mark.parametrize('change', PROXY_CHANGES)
def test_proxy_qualification_precedes_every_hidden_value_read(extract_room, change):
    raw = extract_room(rooms=1, setup="const input=document.querySelector('input.PrivateSwitchBase-input');const label=input.parentElement.parentElement;" + change,
                       denied=['[data-fixture=room-1] input'])
    unresolved(raw)
    assert not any(f['type'] == 'checkbox' for f in raw['dialogs'][0]['fields'])
    assert 'SYNTHETIC_EXTRA' not in json.dumps(raw)


@pytest.mark.parametrize('attribute,value', [('role','checkbox'),('role','switch'),('role','button'),('role','link'),
    ('role','menuitem'),('contenteditable','plaintext-only'),('contenteditable','true'),('aria-checked','mixed')])
@pytest.mark.parametrize('location', ['root', 'button'])
def test_unknown_interactive_roles_and_editors_include_room_root(extract_room, attribute, value, location):
    setup = "const block=document.querySelector('[data-fixture=room-1]');"
    if location == 'button':
        setup += "const extra=document.createElement('div');block.children[1].append(extra);"
    else:
        setup += 'const extra=block;'
    raw = extract_room(rooms=1, setup=setup + f'extra.setAttribute({json.dumps(attribute)},{json.dumps(value)});',
                       denied=['input.PrivateSwitchBase-input'])
    unresolved(raw)


@pytest.mark.parametrize('state', ["input.indeterminate=true", "input.setAttribute('aria-checked','mixed')",
    "input.setAttribute('data-indeterminate','true')", "label.lastElementChild.setAttribute('aria-checked','mixed')"])
@pytest.mark.parametrize('break_shape', ['',
    "document.querySelector('.MuiDialogContent-root').insertAdjacentHTML('beforeend','<div class=MuiDialogContent-root></div>');",
    "const block=document.querySelector('[data-fixture=room-1]');const replacement=document.createElement('section');replacement.innerHTML=block.innerHTML;block.replaceWith(replacement);"])
@pytest.mark.parametrize('opacity', ['0', '1'])
def test_mixed_states_never_leak_flat_booleans_before_early_returns(extract_room, state, break_shape, opacity):
    raw = extract_room(rooms=1, setup=break_shape + "const input=document.querySelector('input.PrivateSwitchBase-input');const label=input.parentElement.parentElement;"
        + f"input.style.opacity='{opacity}';" + state, denied=['input.PrivateSwitchBase-input'])
    unresolved(raw)
    assert not any(f['name'] == 'has_built_in_cupboards' for f in raw['dialogs'][0]['fields'])


@pytest.mark.parametrize('change', [
    "document.querySelector('.MuiDialogContent-root').setAttribute('role','group')",
    "document.querySelector('.MuiDialogContent-root').setAttribute('aria-checked','mixed')",
    "document.querySelector('[role=dialog]').setAttribute('aria-checked','mixed')",
])
def test_ancestor_refusal_cannot_accept_description_only_groups(extract_room, change):
    raw = extract_room(setup=change, denied=['input.PrivateSwitchBase-input'])
    unresolved(raw)
    assert not any(f['type'] == 'checkbox' for f in raw['dialogs'][0]['fields'])


@pytest.mark.parametrize('body', [
    '<section><input name=unknown value=Synthetic></section>',
    '<label>Hidden<input id=unknown type=checkbox style="opacity:0"></label>',
    '<label for=repeated>Explicit label</label><input id=repeated name=repeated type=checkbox>',
    '<label>Ambiguous<input type=checkbox aria-label="Other label"></label>',
])
def test_legacy_means_supported_direct_controls_not_arbitrary_subtrees(extract_room, body):
    html = '<main><div role=dialog aria-label="Set bedrooms options"><div class=MuiDialogContent-root>'
    html += ''.join(f'<div><h6>Bedroom {i}</h6>{body}</div>' for i in (1,2)) + '</div></div></main>'
    unresolved(extract_room(html))


def test_legacy_implicit_labels_preserve_room_local_ownership(extract_room):
    html = '<main><div role=dialog aria-label="Set bedrooms options"><div class=MuiDialogContent-root>'
    html += ''.join(f'<div><h6>Bedroom {i}</h6><label>Option {i}<input type=checkbox id=repeated name=repeated'
        + (' checked' if i == 2 else '') + f'></label><textarea name=description>  Room {i}  </textarea></div>' for i in (1,2))
    raw = extract_room(html + '</div></div></main>')
    groups = raw['dialogs'][0]['room_groups']
    assert [g['fields'][0]['label'] for g in groups] == ['Option 1', 'Option 2']
    assert [g['fields'][0]['checked'] for g in groups] == [False, True]
    assert [g['fields'][1]['value'] for g in groups] == ['  Room 1  ', '  Room 2  ']
    assert not raw['limitations']


def test_unrelated_hidden_and_credential_inputs_stay_excluded(extract_room):
    raw = extract_room(rooms=1, setup='''
      document.querySelector('main').insertAdjacentHTML('beforeend','<input type=hidden id=private-hidden value=SYNTHETIC_PRIVATE_HIDDEN>');
      const input=document.querySelector('input.PrivateSwitchBase-input');input.id=input.name='credential_token';input.value='SYNTHETIC_PRIVATE_CREDENTIAL';
    ''', denied=['#private-hidden', 'input.PrivateSwitchBase-input'])
    unresolved(raw)
    assert 'SYNTHETIC_PRIVATE_' not in json.dumps(raw)


def test_raw_json_sqlite_inventory_transfer_and_integrity(extract_room, tmp_path):
    raw = extract_room()
    source, target = full_capture(), full_capture(True)
    for record in (source, target):
        record['pages'][0]['dialogs'] = deepcopy(raw['dialogs'])
        record['pages'][0]['limitations'] = deepcopy(raw['limitations'])
        seal(record)
    original = deepcopy(source)
    source = json.loads(json.dumps(source))
    store = DraftStore(tmp_path / 'synthetic.sqlite3')
    try:
        ident = store.save_capture('synthetic-room-job', source)
    finally:
        store.close()
    store = DraftStore(tmp_path / 'synthetic.sqlite3')
    try:
        restored = store.get_capture(ident)['capture']
    finally:
        store.close()
    assert restored == original and verify_capture_integrity(restored)
    inventory = extract_source_fields(restored, expected_listing_id='123456', expected_rxcu='RXCU12345')
    related = inventory['related_records'][0]
    assert related['path'] == 'pages/0/dialogs/0' and related['status'] == 'unresolved'
    assert related['record'] == raw['dialogs'][0]
    assert len(inventory['unknowns']) == 8 and len({o['path'] for o in inventory['unknowns']}) == 8
    plan = build_transfer_plan(restored, target, **pins(restored, target))
    assert plan['inventories']['source']['related_records'][0] == related
    assert plan['inventories']['source']['unknowns'] == inventory['unknowns']
    assert plan['status'] == 'partial_read_only'
    assert all(not r['complete'] and not r['live_ready'] for r in (restored, inventory, plan))
    assert restored['identity']['account_verified'] is False
    assert not any(row['key'] in {'has_built_in_cupboards','has_open_plan','has_carpets','description'} for group in plan['groups'].values() for row in group)
    related['record']['room_groups'][0]['fields'][0]['value'] = 'Changed copy'
    assert restored == original
    restored['pages'][0]['dialogs'][0]['room_groups'][0]['fields'][0]['checked'] = False
    assert not verify_capture_integrity(restored)


@pytest.mark.parametrize('ambiguous', [False, True])
def test_exact_output_through_guarded_matched_capture(extract_room, monkeypatch, ambiguous):
    import quick_relist.room_capture as rooms
    import quick_relist.matched_capture as matched
    from test_room_capture import REF, actions
    from test_room_capture_integration import IntegratedDriver
    raw = extract_room(setup="document.querySelector('[data-fixture=room-1]').children[3].remove();" if ambiguous else '')
    clock = SimpleNamespace(now=0)
    time = SimpleNamespace(monotonic=lambda: clock.now, sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds))
    monkeypatch.setattr(rooms, 'time', time); monkeypatch.setattr(matched, 'time', time)
    monkeypatch.setattr(rooms, '_keyboard_escape', lambda driver: driver.modal.send_keys(rooms.ESCAPE))
    driver = IntegratedDriver(clock)
    driver.counts = {d.key: (2 if d.key == 'bedroom' else 0) for d in rooms.ROOM_OPTIONS}
    original = driver.execute_script
    def execute(script, *args):
        result = original(script, *args)
        if script == READ_PAGE_SCRIPT and driver.modal is not None:
            result['dialogs'] = deepcopy(raw['dialogs']); result['limitations'] = deepcopy(raw['limitations'])
        return result
    driver.execute_script = execute
    hub = SimpleNamespace(driver=driver, editor_bindings={REF: {'listing_id':'123456','handle':'editor'}})
    result = matched.MatchedHubCapture(hub, REF, include_room_options=True, timeout=2, poll_interval=.1, stable_for=.2).capture()
    assert verify_capture_integrity(result) and result['complete'] is result['live_ready'] is False
    assert actions(driver) == [('click','bedroom'),('escape','bedroom')]
    inventory = extract_source_fields(result, expected_rxcu=REF)
    if ambiguous:
        assert result['status'] == 'stopped' and result['pages'][0]['dialogs'] == []
        assert result['room_option_capture'][0]['stop_code'] == 'room_groups_unresolved'
        assert inventory['pending_room_observation_count'] == 1 and inventory['related_records'] == []
    else:
        assert result['status'] == 'partial'
        saved = result['pages'][0]['dialogs'][0]
        assert saved['room_groups'] == raw['dialogs'][0]['room_groups']
        assert saved['fields'] == raw['dialogs'][0]['fields']
        assert saved['source']['close_verified'] is True and saved['source']['complete'] is False


@pytest.mark.parametrize('title', ['constructor', '__proto__', 'toString', 'Unknown synthetic room'])
def test_only_own_exact_room_titles_enter_qualification(extract_room, title):
    raw = extract_room(rooms=1, setup='document.querySelector("[role=dialog]").setAttribute("aria-label",' + json.dumps(title) + ');',
                       denied=['input.PrivateSwitchBase-input'])
    assert raw['dialogs'][0]['room_groups'] == []
    assert not any(f['type'] == 'checkbox' for f in raw['dialogs'][0]['fields'])


def test_observed_bedroom_grid_counts_and_outside_actions_preserve_all_scopes(extract_room):
    raw = extract_room(setup="""
      for (const [roomIndex, block] of [...document.querySelectorAll('[data-fixture^=room-]')].entries()) {
        const grids=[...block.children].filter(el=>el.classList.contains('MuiGrid-container'));
        for (const [section, grid] of grids.entries()) {
          for(let i=1;i<[6,16,7][section];i++) {
            const item=grid.firstElementChild.cloneNode(true), input=item.querySelector('input');
            input.id=input.name='synthetic_option_'+section+'_'+i;
            input.checked=(roomIndex+section+i)%2===0;input.value=' 00'+i+' ';
            item.querySelector('.MuiFormControlLabel-label').textContent='Synthetic option '+section+' '+i+' room '+roomIndex;
            grid.append(item);
          }
        }
      }
    """)
    groups = raw['dialogs'][0]['room_groups']
    assert [len(g['fields']) for g in groups] == [30,30]
    assert len(raw['dialogs'][0]['fields']) == 60
    assert sum(f.get('observed_room_checkbox_proxy') is True for f in raw['dialogs'][0]['fields']) == 58
    for group in groups:
        assert len({f['name'] for f in group['fields']}) == 30
    for left, right in zip(groups[0]['fields'][:-1], groups[1]['fields'][:-1]):
        assert left['id'] == right['id'] and left['name'] == right['name']
        assert left['label'] != right['label']
    assert not raw['limitations']
