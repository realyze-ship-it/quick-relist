"""Native controls in a nonce-bound localhost model ONLY. No Hub registration.

The order widget models the read-only observed sortable keyboard instructions.
Caption/cover save controls, server receipts and revision semantics are invented
fixture contracts, not inspected Hub capabilities. All JS here reads DOM only.
"""
from dataclasses import asdict
import json
import re
from urllib.parse import urlsplit

from .hub_form_writer import _json, _digest, _local_url, TransferRejected
from .media_metadata import MetadataRequest, MetadataStore, MetadataWorkflow, MetadataReport, require


class HubMediaMetadataWriter:
    def __init__(self, driver): self.driver = driver
    def transfer(self, request): return MetadataReport('blocked', code='live_provider_unqualified')


READ_METADATA = r"""
return (()=>{
 const b=arguments[0],q=(r,s)=>[...r.querySelectorAll(s)],root=document.getElementById('synthetic-metadata');
 if(!root||q(document,'#synthetic-metadata').length!==1||location.href!==b.url||root.dataset.synthetic!=='true'
 ||root.dataset.nonce!==b.nonce||root.dataset.epoch!==b.nonce||root.dataset.request!==b.request
 ||root.dataset.context!==b.context||root.dataset.ready!=='true')return null;
 const out={revision:null,cards:[],receipts:[],command:null,editor:null,nodes:{},errors:[]};
 const fail=()=>out.errors.push('structure');
 const safe=e=>{if(!e)return false;for(let p=e;p;p=p.parentElement){const s=getComputedStyle(p);if(p.hidden||p.inert||p.getAttribute('aria-hidden')==='true'||p.getAttribute('aria-disabled')==='true'||s.display==='none'||['hidden','collapse'].includes(s.visibility)||Number(s.opacity||1)===0)return false;}return !!e.getClientRects().length;};
 const attrs=(e,names)=>[...e.attributes].every(a=>names.includes(a.name));
 const enabled=e=>safe(e)&&!e.disabled&&!e.matches(':disabled')&&!e.readOnly;
 if(q(document,'iframe,dialog,[contenteditable],input[type=file],select,[aria-pressed],[aria-checked],[aria-valuenow],[aria-valuetext]').length)fail();
 const grids=q(root,'[data-saved-grid]'),cards=q(root,'[data-saved-card]'),receipts=q(root,'pre[data-modeled-receipt]');
 if(grids.length!==1||grids[0]?.parentElement!==root)fail();
 for(const e of cards){const cap=q(e,'[data-saved-caption]');
  if(e.tagName!=='ARTICLE'||e.parentElement!==grids[0]||cap.length!==1||e.children.length!==1||cap[0]?.tagName!=='SPAN'
   ||cap[0]?.parentElement!==e||!safe(e)||!safe(cap[0])||!attrs(e,['data-saved-card','data-media-id','data-upload-id','data-sha256','data-size','data-kind','data-position','data-cover']))fail();}
 if(grids[0]&&grids[0].children.length!==cards.length)fail();
 for(const e of receipts)if(e.parentElement!==root||!safe(e))fail();
 const commands=q(root,'pre[data-command]'),editors=q(root,'section[data-editor]');
 const all=q(document,'input,textarea,button'),allowed=[],sortables=[];
 let editor=null,kind=null,caption=null,cover=[];
 if(commands.length===0){if(editors.length||all.length)fail();}
 else{
  if(commands.length!==1||editors.length!==1){fail();return out;}
  editor=editors[0];kind=editor.dataset.editor;
  if(commands[0].parentElement!==root||editor.parentElement!==root||!safe(editor)||!safe(commands[0]))fail();
  const save=q(editor,'button[data-commit]');
  if(save.length!==1||save[0]?.tagName!=='BUTTON'||save[0]?.getAttribute('type')!=='button'||!enabled(save[0])
   ||!attrs(save[0],['type','data-commit','data-sent'])||save[0]?.textContent!=='Save modeled metadata')fail();
  allowed.push(save[0]);out.nodes.save=save[0];
  if(kind==='caption'){
   const inputs=q(editor,'textarea');caption=inputs[0];
   if(inputs.length!==1||!enabled(caption)||caption?.getAttribute('name')!=='caption'||!attrs(caption,['name']))fail();
   allowed.push(caption);out.nodes.caption=caption;
  }else if(kind==='cover'){
   cover=q(editor,'input');
   for(const e of cover){if(!enabled(e)||e.getAttribute('type')!=='radio'||e.getAttribute('name')!=='cover'
      ||!attrs(e,['type','name','value','checked']))fail();allowed.push(e);}
  }else if(kind==='order'){
   const list=q(editor,'[data-editor-grid]');if(list.length!==1)fail();
   for(const e of q(editor,'[data-sortable]')){const description=document.getElementById(e.getAttribute('aria-describedby'));
    if(e.parentElement!==list[0]||e.tagName!=='DIV'||e.getAttribute('role')!=='button'||e.getAttribute('tabindex')!=='0'
     ||e.getAttribute('aria-roledescription')!=='sortable'||e.getAttribute('aria-disabled')!=='false'||!safe(e)
     ||!description||!root.contains(description)||description.textContent!=='Space picks up; arrow keys move; Space drops; Escape cancels.'
     ||!attrs(e,['data-sortable','data-media-id','role','aria-roledescription','tabindex','aria-disabled','aria-describedby']))fail();sortables.push(e);}
   if(list[0]&&list[0].children.length!==sortables.length)fail();
  }else fail();
 }
 if(all.length!==allowed.length||all.some(e=>!allowed.includes(e)))fail();
 if(q(document,'[role],[aria-roledescription],[tabindex]').some(e=>!sortables.includes(e)))fail();
 // Complete structural/visibility/allowlist pass BEFORE values, captions,
 // command payloads, selected states or receipts can be read.
 if(out.errors.length)return out;
 const parse=t=>{try{return JSON.parse(t);}catch(_){fail();return null;}};
 out.revision=root.dataset.revision;
 for(const e of cards)out.cards.push({media_id:e.dataset.mediaId,upload_id:e.dataset.uploadId,sha256:e.dataset.sha256,size:parse(e.dataset.size),
  kind:e.dataset.kind,position:parse(e.dataset.position),caption:e.querySelector('[data-saved-caption]').textContent,cover:parse(e.dataset.cover)});
 for(const e of receipts)out.receipts.push(parse(e.textContent));
 if(commands.length){out.command=parse(commands[0].textContent);
  if(kind==='caption')out.editor={kind,value:caption.value};
  else if(kind==='cover'){out.nodes.cover=Object.create(null);const choices=[];
   for(const e of cover){if(Object.hasOwn(out.nodes.cover,e.value))fail();out.nodes.cover[e.value]=e;choices.push({value:e.value,checked:e.checked});}
   out.editor={kind,choices};
  }else{out.nodes.order=Object.create(null);const ids=[];
   for(const e of sortables){if(Object.hasOwn(out.nodes.order,e.dataset.mediaId))fail();out.nodes.order[e.dataset.mediaId]=e;ids.push(e.dataset.mediaId);}
   out.editor={kind,ids,picked:editor.dataset.picked||null};}
 }
 return out;
})();

"""


def bind_synthetic_metadata_for_tests(driver, request, store, *, nonce):
    require(type(request) is MetadataRequest and type(store) is MetadataStore)
    require(type(nonce) is str and re.fullmatch('[a-f0-9]{64}', nonce))
    try:
        require(driver.capabilities.get('browserName','').lower() == 'chrome')
        require(_local_url(driver.current_url, nonce, request.context))
        provider = _SyntheticMetadataBrowser(driver, request, nonce)
        provider.inspect()
        # Recovery can bind the same service epoch from a fresh browser. The
        # immutable journal, not a resettable in-process nonce set, owns history.
        return MetadataWorkflow(store, request, provider)
    except TransferRejected: raise
    except Exception: raise TransferRejected('metadata_fixture_binding_rejected') from None


class _SyntheticMetadataBrowser:
    def __init__(self, driver, request, nonce):
        self.driver, self.request, self.nonce = driver, request, nonce
        self.url, self.handle = driver.current_url, driver.current_window_handle
        self.handles = tuple(sorted(driver.window_handles))
        self._nodes = None

    def binding(self):
        self._read()
        return {'epoch':self.nonce, 'origin':self.url, 'request_hash':self.request.fingerprint}

    def _read(self):
        d, r = self.driver, self.request
        require(d.current_url == self.url and _local_url(d.current_url,self.nonce,r.context)
                and d.current_window_handle == self.handle and tuple(sorted(d.window_handles)) == self.handles)
        out = d.execute_script(READ_METADATA, {'url':self.url,'nonce':self.nonce,
                           'request':r.fingerprint,'context':_json(asdict(r.context))})
        require(d.current_url == self.url and d.current_window_handle == self.handle and tuple(sorted(d.window_handles)) == self.handles)
        require(type(out) is dict and not out.get('errors'))
        require(set(out) == {'revision','cards','receipts','command','editor','nodes','errors'})
        r.validate_cards(out['cards'])
        require(type(out['receipts']) is list)
        return out

    def inspect(self):
        out = self._read()
        return {'request_hash':self.request.fingerprint,'context':asdict(self.request.context),
                'revision':out['revision'],'cards':out['cards'],'receipts':out['receipts']}

    def _check(self, command, cards, expected_editor=None, nodes=None):
        out = self._read(); require(_json(out['command']) == _json(command))
        require(out['revision'] == command['expected_revision'] and _json(out['cards']) == _json(cards))
        editor, stage = out['editor'], command['payload']
        require(type(editor) is dict and editor.get('kind') == stage['kind'])
        if stage['kind'] == 'caption':
            baseline = next(c for c in cards if c['media_id'] == stage['media_id'])['caption']
            expected = {'kind':'caption','value':baseline} if expected_editor is None else expected_editor
        elif stage['kind'] == 'cover':
            expected = {'kind':'cover','choices':[{'value':c['media_id'],'checked':c['cover']} for c in cards]
                         + [{'value':'','checked':not any(c['cover'] for c in cards)}]} if expected_editor is None else expected_editor
        else:
            expected = {'kind':'order','ids':[c['media_id'] for c in cards],'picked':None} if expected_editor is None else expected_editor
        require(_json(editor) == _json(expected))
        if nodes is not None: require(out['nodes'] == nodes)
        return out

    def preflight(self, command, cards): self._check(command,cards)

    def perform(self, command, cards):
        out = self._check(command,cards); nodes = out['nodes']; stage = command['payload']
        if stage['kind'] == 'caption':
            nodes['caption'].clear()
            editor = {'kind':'caption','value':''}; self._check(command,cards,editor,nodes)
            if stage['caption']: nodes['caption'].send_keys(stage['caption'])
            editor['value'] = stage['caption']; self._check(command,cards,editor,nodes)
        elif stage['kind'] == 'cover':
            key = stage['media_id'] or ''
            nodes['cover'][key].click()
            editor = {'kind':'cover','choices':[{'value':c['media_id'],'checked':c['media_id']==key} for c in cards]
                      + [{'value':'','checked':key==''}]}
            self._check(command,cards,editor,nodes)
        else:
            ids = [c['media_id'] for c in cards]
            editor = {'kind':'order','ids':ids,'picked':None}
            for position, key in enumerate(stage['media_ids']):
                index = ids.index(key)
                if index == position: continue
                node = nodes['order'][key]
                node.send_keys(' '); editor['picked'] = key; self._check(command,cards,editor,nodes)
                while index > position:
                    node.send_keys('\ue013')  # Native WebDriver ArrowUp.
                    ids[index],ids[index-1] = ids[index-1],ids[index]; index -= 1
                    self._check(command,cards,editor,nodes)
                node.send_keys(' '); editor['picked'] = None; self._check(command,cards,editor,nodes)
        self._check(command,cards,editor,nodes)
        nodes['save'].click()  # Exactly once; caller has already committed intent.
