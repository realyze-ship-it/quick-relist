"""Modeled media metadata service. Never connects to or claims to emulate Hub.

Only this synthetic service declares zero-or-one cover and atomic per-stage
revision CAS. Its ledger survives a page or browser restart, NOT server restart.
A new server gets a new epoch. Upload receipts alone cannot establish the
metadata-stage revision: the fixture explicitly initializes that separate state.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from threading import Lock, Thread
import time

from quick_relist.hub_form_writer import _json, _digest
from quick_relist.media_metadata import MetadataRequest, require
from media_transfer_fixture import fixture_request as upload_fixture_request


def fixture_request(directory, *, no_cover=False):
    upload = upload_fixture_request(directory)
    captions = ('  First image\nSecond line  ', '', '\nThird image\n')
    files = tuple(replace(f,metadata_json=_json({'caption':captions[i],'cover':i==1 and not no_cover}))
                  for i,f in enumerate(upload.files))
    upload = replace(upload,files=files)
    receipts = [{'media_id':f.media_id,'upload_id':'synthetic-upload-'+f.media_id,'sha256':f.sha256,
                 'size':f.size,'kind':f.kind,'source_position':f.position,'request':upload.fingerprint} for f in files]
    # This baseline is modeled separately, not inferred from an upload receipt.
    baseline = [{k:r[k] for k in ('media_id','upload_id','sha256','size','kind')} |
                {'position':i,'caption':'Existing synthetic caption','cover':i==0}
                for i,r in enumerate((receipts[2],receipts[0],receipts[1]))]
    context = replace(upload.context,expected_draft_revision='metadata-initial-revision')
    return MetadataRequest.from_transfer(upload,context,receipts,baseline)


class Model:
    def __init__(self, request, *, reject=False, delay=0, scenario='normal'):
        self.request=request; self.epoch=secrets.token_hex(32)
        self.cards=request.baseline; self.revision=request.context.expected_draft_revision
        self.receipts=[]; self.lock=Lock(); self.posts=0; self.reject=reject
        self.delay=delay; self.scenario=scenario; self.trusted_events=[]

    def state(self):
        return {'request_hash':self.request.fingerprint,'context':asdict(self.request.context),
                'revision':self.revision,'cards':json.loads(_json(self.cards)),
                'receipts':json.loads(_json(self.receipts))}

    def command(self):
        if any(r['status']=='not_applied' for r in self.receipts) or len(self.receipts)==len(self.request.stages()):return None
        return self.request.command(len(self.receipts),self.revision,self.cards)

    def apply(self, command, payload, events=()):
        self.posts += 1
        require(_json(payload)==_json(command.get('payload')))
        old = next((r for r in self.receipts if r['operation_id']==command.get('operation_id')),None)
        if old:
            require(old['command_hash']==_digest(command))
            return old
        require(_json(command)==_json(self.command()) and _json(payload)==_json(command['payload']))
        previous=self.revision; before=_digest(self.cards)
        if not self.reject:
            self.cards=self.request.after(self.cards,command['ordinal'])
            self.revision='synthetic-metadata-revision-'+str(command['ordinal']+1)
        receipt={'operation_id':command['operation_id'],'command_hash':_digest(command),
                 'request_hash':self.request.fingerprint,'ordinal':command['ordinal'],
                 'status':'not_applied' if self.reject else 'applied','terminal':True,
                 'previous_revision':previous,'revision':self.revision,
                 'before_hash':before,'after_hash':_digest(self.cards)}
        self.receipts.append(receipt); self.trusted_events.extend(events)
        return receipt


class OfflineProvider:
    """Tests coordinator behavior only; not browser qualification."""
    def __init__(self, model): self.model=model; self.calls=0; self.before_perform=None; self.fail=None
    def binding(self):return {'epoch':self.model.epoch,'origin':'http://127.0.0.1:12345/synthetic','request_hash':self.model.request.fingerprint}
    def inspect(self):return self.model.state()
    def preflight(self,command,cards):require(_json(command)==_json(self.model.command()) and _json(cards)==_json(self.model.cards))
    def perform(self,command,cards):
        self.calls+=1
        if self.before_perform:self.before_perform(command)
        if self.fail=='before':raise RuntimeError('Private synthetic failure')
        self.model.apply(command,command['payload'])
        if self.fail=='after':raise KeyboardInterrupt()


def document(model, nonce):
    r=model.request; command=model.command(); state=model.state()
    cards=[]
    for c in state['cards']:
        attrs=' '.join('data-'+key.replace('_','-')+'="'+escape(str(value).lower() if type(value) is bool else str(value),quote=True)+'"'
                       for key,value in c.items() if key!='caption')
        cards.append('<article data-saved-card '+attrs+'><span data-saved-caption>'+escape(c['caption'])+'</span></article>')
    receipts=''.join('<pre data-modeled-receipt>'+escape(_json(x))+'</pre>' for x in state['receipts'])
    editor=''
    if command:
        stage=command['payload']; kind=stage['kind']
        editor='<pre data-command>'+escape(_json(command))+'</pre><section data-editor="'+kind+'">'
        if kind=='caption':editor+='<label>Exact synthetic caption<textarea name="caption"></textarea></label>'
        elif kind=='cover':
            for c in state['cards']:
                editor+='<label>'+escape(c['media_id'])+'<input type="radio" name="cover" value="'+escape(c['media_id'],quote=True)+'"'+(' checked' if c['cover'] else '')+'></label>'
            editor+='<label>No cover<input type="radio" name="cover" value=""'+(' checked' if not any(c['cover'] for c in state['cards']) else '')+'></label>'
        else:
            editor+='<p id="DndDescribedBy-model">Space picks up; arrow keys move; Space drops; Escape cancels.</p><div data-editor-grid>'
            for c in state['cards']:
                editor+='<div data-sortable data-media-id="'+escape(c['media_id'],quote=True)+'" role="button" aria-roledescription="sortable" tabindex="0" aria-disabled="false" aria-describedby="DndDescribedBy-model">'+escape(c['media_id'])+'</div>'
            editor+='</div>'
        editor+='<button type="button" data-commit>Save modeled metadata</button></section>'
    config={'command':command,'cards':state['cards'],'scenario':model.scenario}
    replacements={'NONCE':nonce,'CONTEXT':escape(_json(asdict(r.context)),quote=True),'REQUEST':r.fingerprint,
                  'REVISION':escape(state['revision'],quote=True),'CARDS':''.join(cards),'RECEIPTS':receipts,'EDITOR':editor,
                  'CONFIG':_json(config).replace('<','\\u003c').replace('>','\\u003e')}
    html=(Path(__file__).parent/'fixtures/hub_media_metadata.html').read_text(encoding='utf-8')
    for key,value in replacements.items():html=html.replace('@@'+key+'@@',value)
    return html


@contextmanager
def serve(request, *, reject=False, delay=0, scenario='normal', model=None):
    model = Model(request,reject=reject,delay=delay,scenario=scenario) if model is None else model
    nonce=model.epoch
    path=f'/quick-relist-fixture/{nonce}/draft/{request.context.draft_id}/media'
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!=path:self.send_error(404);return
            with model.lock:data=document(model,nonce).encode('utf-8')
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
        def do_POST(self):
            if self.path!=path+'/commit':self.send_error(404);return
            try:
                size=int(self.headers.get('Content-Length','0'));require(0<size<1024*1024)
                body=json.loads(self.rfile.read(size));require(type(body) is dict and set(body)=={'command','payload','events'})
                require(type(body['events']) is list and len(body['events'])<10000)
                require(all(type(e) is dict and set(e)=={'type','trusted'} and e['type'] in ('input','change','click','keydown')
                            and type(e['trusted']) is bool for e in body['events']))
                with model.lock:receipt=model.apply(body['command'],body['payload'],body['events'])
            except Exception:self.send_error(409);return
            if model.delay:time.sleep(model.delay)
            data=_json(receipt).encode();self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers()
            try:self.wfile.write(data)
            except ConnectionError:
                # A page refresh/closed browser may abort a delayed response.
                # The modeled operation was already committed above; never
                # repeat it merely because its client has disconnected.
                pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}{path}',nonce,model
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
