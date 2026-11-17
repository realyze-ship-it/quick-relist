"""Exact production script with explicit synthetic geometry and hit models."""
import json
import shutil
import subprocess

import pytest

from quick_relist.tab_positioning import TAB_POSITION_SCRIPT
from tab_positioning_fixture import LABELS, tab_html

RUNNER = r"""
const {JSDOM}=require('jsdom'),fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const w=new JSDOM(input.html,{url:'https://hub.remax.co.za/app/listings/edit/123456',runScripts:'outside-only'}).window;
Object.defineProperty(w.document,'readyState',{value:input.fault==='loading'?'loading':'complete'});
Object.defineProperty(w,'innerWidth',{value:input.viewport?.[0] ?? 800});
Object.defineProperty(w,'innerHeight',{value:input.viewport?.[1] ?? 600});
Object.defineProperty(w.document.documentElement,'clientWidth',{value:input.client?.[0] ?? 785});
Object.defineProperty(w.document.documentElement,'clientHeight',{value:input.client?.[1] ?? 600});
const expected=Object.fromEntries(input.labels.map((l,i)=>[l,w.document.getElementById('tab-'+i)])),e=expected['Deal Particulars'];
let rects=input.rects ?? [{x:170,y:280,width:140,height:48}],scrolls=0,points=[],options=[];
w.Element.prototype.getClientRects=function(){return this===e?rects:[{x:0,y:0,width:20,height:20}]};
w.document.elementFromPoint=(x,y)=>{
  points.push([x,y]);
  if(input.hit==='none') return null;
  if(input.hit==='overlay') return w.document.getElementById('cover');
  if(input.hit==='parent') return e.parentElement;
  if(input.hit==='other') return expected['Media'];
  if(input.hit==='child') return e.firstElementChild;
  if(input.hit==='deep') return e.querySelector('span span span');
  return e;
};
w.Element.prototype.scrollIntoView=function(opts){
  if(this!==e||++scrolls>1) throw new Error('Wrong or repeated scroll');
  options.push(opts);
};
if(input.fault==='nan')rects[0].x=NaN;
if(input.fault==='infinity')rects[0].height=Infinity;
if(input.fault==='string')rects[0].x='170';
if(input.fault==='duplicate')e.parentElement.appendChild(e.cloneNode(true));
if(input.fault==='replaced')expected['Deal Particulars']=e.cloneNode(true);
if(input.fault==='detached')e.remove();
if(input.fault==='foreign')expected['Deal Particulars']=w.document.implementation.createHTMLDocument().importNode(e,true);
if(input.fault==='shadow') {const host=w.document.createElement('div');w.document.body.append(host);host.attachShadow({mode:'open'}).append(e)}
if(input.fault==='disabled')e.disabled=true;
if(input.fault==='aria-disabled')e.setAttribute('aria-disabled','true');
if(input.fault==='selection')e.setAttribute('aria-selected','true');
if(input.fault==='tabindex')e.setAttribute('tabindex','0');
if(input.fault==='type')e.setAttribute('type','submit');
if(input.fault==='label')e.append(' PRIVATE');
if(input.fault==='role')e.setAttribute('role','button');
if(input.fault==='aria-label')e.setAttribute('aria-label','Delete listing');
if(input.fault==='wrong-main')w.document.body.append(e.parentElement);
if(input.fault==='nested-tab') {const wrap=w.document.createElement('div');e.replaceWith(wrap);wrap.append(e)}
if(input.fault==='unknown-selected'){const u=e.cloneNode();u.textContent='History';u.setAttribute('aria-selected','true');e.parentElement.append(u)}
if(input.fault==='busy')w.document.querySelector('main').setAttribute('aria-busy','true');
if(input.fault==='dialog')w.document.getElementById('cover').setAttribute('role','dialog');
if(input.fault==='aria-modal')w.document.getElementById('cover').setAttribute('aria-modal','true');
if(input.fault==='hidden')e.hidden=true;
if(input.fault==='opacity')e.style.opacity=0;
if(input.fault==='split-text')e.replaceChildren(w.document.createTextNode('Deal '),w.document.createTextNode('Particulars'));
if(input.fault==='span-event')e.firstElementChild.setAttribute('onclick','void 0');
if(input.fault==='span-editable')e.firstElementChild.setAttribute('contenteditable','true');
if(input.fault==='span-link')e.firstElementChild.setAttribute('href','#');
if(input.fault==='span-role')e.firstElementChild.setAttribute('role','tab');
if(input.fault==='unreadable')w.document.elementFromPoint=()=>{throw new Error('PRIVATE_SYNTHETIC_ERROR')};
const denied=()=>{throw new Error('Unexpected business mutation or private read')};
w.HTMLElement.prototype.click=denied;w.HTMLFormElement.prototype.submit=denied;w.HTMLFormElement.prototype.requestSubmit=denied;
for(const name of ['fetch','XMLHttpRequest','WebSocket','open'])w[name]=denied;
for(const name of ['localStorage','sessionStorage'])Object.defineProperty(w,name,{get:denied});
Object.defineProperty(w.document,'cookie',{get:denied,set:denied});
const before=w.document.documentElement.outerHTML,url=w.location.href;
let status;
try{status=w.Function(input.script)(input.fault==='route'?url+'/media':url,'Listing particulars','Deal Particulars',expected,input.scroll===true)}
catch(_error){status='fixture-caught-exception'}
if(before!==w.document.documentElement.outerHTML||url!==w.location.href)throw new Error('Positioning changed DOM or route');
process.stdout.write(JSON.stringify({status,scrolls,points,options}));w.close();
"""


@pytest.fixture(scope='module')
def probe():
    node=shutil.which('node')
    if not node: pytest.skip('Node unavailable; no tab DOM qualification')
    check=subprocess.run([node,'-e',"require.resolve('jsdom')"],capture_output=True,text=True,timeout=10)
    if check.returncode: pytest.skip('Optional jsdom unavailable; no tab DOM qualification')
    def run(**kwargs):
        child=kwargs.pop('child','text')
        result=subprocess.run([node,'-e',RUNNER],input=json.dumps({'html':tab_html(child=child,overlay='header'),
            'labels':LABELS,'script':TAB_POSITION_SCRIPT,**kwargs}),text=True,capture_output=True,timeout=20)
        assert result.returncode==0,result.stderr
        return json.loads(result.stdout)
    return run


@pytest.mark.parametrize('rect,point',[
    ({'x':170,'y':280,'width':140,'height':48},[240,304]),
    ({'x':170.4,'y':280.2,'width':140.8,'height':48.7},[240,304]),
    ({'x':-20,'y':-8,'width':140,'height':20},[60,6]),
    ({'x':170,'y':590,'width':140,'height':20},[240,595]),
    ({'x':775,'y':280,'width':40,'height':20},[787,290]),
])
def test_native_first_rectangle_clipped_and_floored(probe,rect,point):
    assert probe(rects=[rect],client=[800,600])=={'status':'clear','scrolls':0,'points':[point],'options':[]}


def test_first_rectangle_not_union(probe):
    result=probe(rects=[{'x':30,'y':100,'width':20,'height':16},{'x':30,'y':116,'width':200,'height':16}])
    assert result['status']=='clear' and result['points']==[[40,108]]


@pytest.mark.parametrize('rects',[[],[{'x':30,'y':200,'width':0,'height':16}],
    [{'x':30,'y':200,'width':120,'height':-1}],[{'x':30,'y':200,'width':.25,'height':16}],
    [{'x':30,'y':200,'width':120,'height':.5}],
    [{'x':30,'y':200,'width':120,'height':16},{'x':30,'y':220,'width':0,'height':16}]])
def test_bad_rectangles_never_scroll_or_hit(probe,rects):
    result=probe(rects=rects,scroll=True)
    assert result=={'status':'invalid' if rects else 'changed','scrolls':0,'points':[],'options':[]}


@pytest.mark.parametrize('fault',['nan','infinity','string'])
def test_invalid_numeric_geometry_fails_closed(probe,fault):
    result=probe(fault=fault,scroll=True)
    assert result=={'status':'invalid','scrolls':0,'points':[],'options':[]}


@pytest.mark.parametrize('viewport,client',[([0,600],[800,600]),([800,-1],[800,600]),([800,600],[0,600])])
def test_invalid_viewports_fail_closed(probe,viewport,client):
    assert probe(viewport=viewport,client=client,scroll=True)['status']=='invalid'


@pytest.mark.parametrize('rect',[
    {'x':30,'y':900,'width':120,'height':16},{'x':30,'y':599.5,'width':120,'height':16},
    {'x':30.1,'y':200.1,'width':1,'height':1},{'x':785,'y':200,'width':15,'height':16}])
def test_offscreen_flooring_sliver_and_scrollbar_require_repositioning(probe,rect):
    assert probe(rects=[rect])=={'status':'needs_scroll','scrolls':0,'points':[],'options':[]}


@pytest.mark.parametrize('child,hit',[('inline','child'),('ripple','target'),('ripple','child'),('ripple','deep')])
def test_inert_inline_and_ripple_descendants_are_native_button_hits(probe,child,hit):
    result=probe(child=child,hit=hit)
    assert result['status']=='clear' and result['scrolls']==0


@pytest.mark.parametrize('child',['interactive','role','nested'])
@pytest.mark.parametrize('hit',['target','child'])
def test_interactive_descendants_invalidate_whole_target(probe,child,hit):
    assert probe(child=child,hit=hit,scroll=True)=={'status':'changed','scrolls':0,'points':[],'options':[]}


@pytest.mark.parametrize('fault',['span-event','span-editable','span-link','span-role'])
def test_inert_tag_does_not_imply_inert_semantics(probe,fault):
    assert probe(child='inline',fault=fault,scroll=True)['status']=='changed'


@pytest.mark.parametrize('hit',['none','overlay','parent','other'])
def test_unrelated_or_missing_hit_requires_at_most_one_exact_scroll(probe,hit):
    assert probe(hit=hit)['status']=='needs_scroll'
    result=probe(hit=hit,scroll=True)
    assert result['status']=='scrolled' and result['scrolls']==1
    assert result['options']==[{'behavior':'instant','block':'center','inline':'center'}]


def test_clear_point_never_scrolls_even_when_permitted(probe):
    result=probe(scroll=True)
    assert result['status']=='clear' and result['scrolls']==0


@pytest.mark.parametrize('fault',['duplicate','replaced','detached','foreign','shadow','disabled','aria-disabled',
    'selection','tabindex','type','label','role','aria-label','wrong-main','nested-tab','unknown-selected',
    'busy','dialog','aria-modal','hidden','opacity','loading','route'])
def test_rpc_revalidates_native_shape_source_selection_owner_and_overlays(probe,fault):
    assert probe(hit='overlay',scroll=True,fault=fault)=={'status':'changed','scrolls':0,'points':[],'options':[]}


def test_split_text_is_same_exact_label(probe):
    assert probe(fault='split-text')['status']=='clear'


def test_unreadable_hit_does_not_scroll_or_return_private_error(probe):
    result=probe(fault='unreadable',scroll=True)
    assert result['status']=='fixture-caught-exception' and result['scrolls']==0
    assert 'PRIVATE' not in json.dumps(result)
