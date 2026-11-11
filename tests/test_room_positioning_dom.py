"""Exact positioning script under synthetic geometry, not a native-browser claim."""
from dataclasses import asdict
import json
import shutil
import subprocess

import pytest

from quick_relist.room_capture import ROOM_OPTIONS, ROOM_POSITION_SCRIPT
from room_positioning_fixture import positioning_html

RUNNER = r"""
const {JSDOM}=require('jsdom'), fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const w=new JSDOM(input.html,{url:'https://hub.remax.co.za/app/listings/edit/123456',runScripts:'outside-only'}).window;
Object.defineProperty(w.document,'readyState',{value:'complete'});
w.document.head.insertAdjacentHTML('beforeend','<style>span,b,i,em,strong,small{opacity:1;visibility:visible}</style>');
Object.defineProperty(w,'innerWidth',{value:input.viewport?.[0] ?? 800});
Object.defineProperty(w,'innerHeight',{value:input.viewport?.[1] ?? 600});
Object.defineProperty(w.document.documentElement,'clientWidth',{value:input.client?.[0] ?? 785});
Object.defineProperty(w.document.documentElement,'clientHeight',{value:input.client?.[1] ?? 600});
const e=w.document.getElementById('opener');
let rects=input.rects || [{x:30,y:200,width:120,height:16}], scrolls=0, points=[], options=[];
w.Element.prototype.getClientRects=function(){return this===e ? rects : [{x:0,y:0,width:20,height:20}]};
w.document.elementFromPoint=(x,y)=>{
  points.push([x,y]);
  if(input.hit==='none') return null;
  if(input.hit==='overlay') return w.document.getElementById('help');
  if(input.hit==='parent') return e.parentElement;
  if(input.hit==='child') return e.firstElementChild;
  return e;
};
w.Element.prototype.scrollIntoView=function(opts){
  if(this!==e || ++scrolls>1) throw new Error('Wrong or repeated scroll');
  options.push(opts);
};
if(input.fault==='nan') rects[0].x=NaN;
if(input.fault==='infinity') rects[0].height=Infinity;
if(input.fault==='string') rects[0].x='30';
if(input.fault==='count') w.document.querySelector('input').value='3';
if(input.fault==='duplicate') e.parentElement.appendChild(e.cloneNode(true));
if(input.fault==='interactive-child') e.firstElementChild.setAttribute('tabindex','0');
if(input.fault==='hidden-child') e.firstElementChild.setAttribute('aria-hidden','true');
if(input.fault==='split-text') e.replaceChildren(w.document.createTextNode('Set '),w.document.createTextNode('bedrooms'),w.document.createTextNode(' options'));
if(input.fault==='unreadable') w.document.elementFromPoint=()=>{throw new Error('PRIVATE_SYNTHETIC_ERROR')};
const denied=()=>{throw new Error('Unexpected business mutation or private read')};
w.HTMLElement.prototype.click=denied;
w.HTMLFormElement.prototype.submit=denied; w.HTMLFormElement.prototype.requestSubmit=denied;
for(const name of ['fetch','XMLHttpRequest','WebSocket','open']) w[name]=denied;
for(const name of ['localStorage','sessionStorage']) Object.defineProperty(w,name,{get:denied});
Object.defineProperty(w.document,'cookie',{get:denied,set:denied});
const before=w.document.documentElement.outerHTML, url=w.location.href;
let status;
try {status=w.Function(input.script)(input.descriptor,input.fault==='replaced' ? e.cloneNode(true) : e,2,input.scroll===true)}
catch(_err) {status='fixture-caught-exception'}
if(before!==w.document.documentElement.outerHTML || url!==w.location.href) throw new Error('Positioning changed DOM or route');
process.stdout.write(JSON.stringify({status,scrolls,points,options})); w.close();
"""


@pytest.fixture(scope='module')
def probe_position():
    node = shutil.which('node')
    if not node: pytest.skip('Node unavailable; no script geometry qualification')
    check = subprocess.run([node, '-e', "require.resolve('jsdom')"], capture_output=True, text=True, timeout=10)
    if check.returncode: pytest.skip('Optional jsdom unavailable; no script geometry qualification')
    def run(**kwargs):
        child = kwargs.pop('child', 'text')
        result = subprocess.run([node, '-e', RUNNER], input=json.dumps({
            'html': positioning_html(child=child), 'descriptor': asdict(ROOM_OPTIONS[0]),
            'script': ROOM_POSITION_SCRIPT, **kwargs}), text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    return run


@pytest.mark.parametrize('rect,point', [
    ({'x':30,'y':200,'width':120,'height':16}, [90,208]),
    ({'x':30.4,'y':200.2,'width':120.8,'height':16.7}, [90,208]),
    ({'x':-20,'y':-8,'width':120,'height':20}, [50,6]),
    ({'x':30,'y':590,'width':120,'height':20}, [90,595]),
    ({'x':775,'y':200,'width':40,'height':20}, [787,210]),
])
def test_first_rect_clipped_floored_webdriver_point(probe_position, rect, point):
    result = probe_position(rects=[rect], client=[800,600])
    assert result == {'status':'clear','scrolls':0,'points':[point],'options':[]}


def test_multiline_uses_first_client_rectangle_not_union(probe_position):
    result = probe_position(rects=[{'x':30,'y':100,'width':20,'height':16},
                                  {'x':30,'y':116,'width':200,'height':16}])
    assert result['points'] == [[40,108]] and result['status'] == 'clear'


@pytest.mark.parametrize('rects', [[], [{'x':30,'y':200,'width':0,'height':16}],
    [{'x':30,'y':200,'width':120,'height':-1}], [{'x':30,'y':200,'width':.25,'height':16}],
    [{'x':30,'y':200,'width':120,'height':.5}],
    [{'x':30,'y':200,'width':120,'height':16},{'x':30,'y':220,'width':0,'height':16}]])
def test_tiny_or_nonpositive_rectangles_fail_without_scroll_or_hit(probe_position, rects):
    result = probe_position(rects=rects, scroll=True)
    assert result == {'status':'invalid' if rects else 'changed','scrolls':0,'points':[],'options':[]}


@pytest.mark.parametrize('fault', ['nan','infinity','string'])
def test_nonfinite_or_nonnumeric_rectangles_fail_without_scroll(probe_position, fault):
    result = probe_position(fault=fault, scroll=True)
    assert result['status'] == 'invalid' and result['scrolls'] == 0 and result['points'] == []


@pytest.mark.parametrize('viewport,client', [([0,600],[800,600]),([800,-1],[800,600]),([800,600],[0,600])])
def test_invalid_viewports_fail_closed(probe_position, viewport, client):
    assert probe_position(viewport=viewport,client=client,scroll=True)['status'] == 'invalid'


@pytest.mark.parametrize('rect', [
    {'x':30,'y':900,'width':120,'height':16},
    {'x':30,'y':599.5,'width':120,'height':16},
    {'x':30.1,'y':200.1,'width':1,'height':1},
    {'x':785,'y':200,'width':15,'height':16},
])
def test_offscreen_subpixel_intersection_or_scrollbar_point_requires_view_change(probe_position, rect):
    result = probe_position(rects=[rect])
    assert result['status'] == 'needs_scroll' and result['scrolls'] == 0 and result['points'] == []


@pytest.mark.parametrize('hit', ['none','overlay','parent'])
def test_no_hit_or_unrelated_footprint_cannot_qualify(probe_position, hit):
    assert probe_position(hit=hit)['status'] == 'needs_scroll'
    result = probe_position(hit=hit, scroll=True)
    assert result['status'] == 'scrolled' and result['scrolls'] == 1
    assert result['options'] == [{'behavior':'instant','block':'center','inline':'center'}]


def test_scroll_flag_never_scrolls_a_clean_target(probe_position):
    result = probe_position(scroll=True)
    assert result['status'] == 'clear' and result['scrolls'] == 0


@pytest.mark.parametrize('child', ['span','button','empty_button','empty_span','nested_empty_button'])
@pytest.mark.parametrize('hit', ['child','target'])
def test_every_element_descendant_invalidates_leaf_caption_before_hit_or_scroll(probe_position, child, hit):
    result = probe_position(child=child, hit=hit, scroll=True)
    assert result == {'status':'changed','scrolls':0,'points':[],'options':[]}


def test_observed_three_text_nodes_still_qualify_as_leaf_caption(probe_position):
    result = probe_position(fault='split-text')
    assert result['status'] == 'clear' and result['scrolls'] == 0


@pytest.mark.parametrize('fault', ['count','duplicate','replaced'])
def test_rpc_revalidates_exact_node_count_and_shape_before_scroll(probe_position, fault):
    result = probe_position(hit='overlay',scroll=True,fault=fault)
    assert result == {'status':'changed','scrolls':0,'points':[],'options':[]}


def test_unreadable_hit_does_not_scroll_or_serialize_private_error(probe_position):
    result = probe_position(fault='unreadable',scroll=True)
    assert result['status'] == 'fixture-caught-exception' and result['scrolls'] == 0
    assert 'PRIVATE' not in json.dumps(result)
