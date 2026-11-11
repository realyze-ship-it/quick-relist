"""One synthetic service: native field events, byte uploads and metadata ledger."""
from contextlib import contextmanager
from dataclasses import asdict, replace
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import secrets
import time
from threading import RLock, Thread

from quick_relist.hub_form_writer import FieldTransfer, FormTransferRequest, _json, _digest
from quick_relist.draft_pipeline import DraftPipelineRequest, validate_pipeline_state
from quick_relist.media_metadata import require
from media_metadata_fixture import Model as MetadataModel, document as metadata_document
from media_transfer_fixture import fixture_request as media_request, fixture_document as upload_document


def fixture_request(directory):
    upload=media_request(directory)
    files=tuple(replace(f,metadata_json=_json({'caption':('  First\nline  ','','\nThird\n')[i],'cover':i==1})) for i,f in enumerate(upload.files))
    upload=replace(upload,files=files)
    keys=(('details','street_name'),('deal_particulars','total_commission_value'),('media','marketing_header'))
    forms=[]
    for page,key in keys:
        context=replace(upload.context,page=page,approved_address_sha256=_digest({}))
        forms.append(FormTransferRequest(context,(FieldTransfer.from_values(key,'  Synthetic '+key+('\nLine two' if key=='marketing_header' else ''),'Old synthetic '+key),)))
    upload=replace(upload,context=replace(upload.context,approved_address_sha256=_digest({})))
    return DraftPipelineRequest(tuple(forms),upload)


class Service:
    def __init__(self,request,*,stop_upload_after=None,drop_response_stage=None,response_delay=0):
        self.request=request;self.epoch=secrets.token_hex(32);self.lock=RLock()
        self.fields=request.initial_fields;self.cards=[];self.uploads=[];self.receipts=[];self.snapshots=[]
        self.revision=request.context.expected_draft_revision;self.metadata=None;self.upload_request=None;self.draft_state='draft'
        self.stop_upload_after=stop_upload_after;self.drop_response_stage=drop_response_stage;self.response_delay=response_delay
        self.field_posts=0;self.byte_posts=0;self.metadata_posts=0;self.trusted=[]
    def nonce(self,index):
        if index=='metadata':return _digest({'epoch':self.epoch,'stage':index})
        return _digest({'epoch':self.epoch,'stage':index,'command':self.command_at(index)})
    def path(self,index):
        page=self.request.forms[index].context.page if index<len(self.request.forms) else 'media'
        nonce=self.metadata.epoch if index>len(self.request.forms) and self.metadata else self.nonce(index)
        return f'/quick-relist-fixture/{nonce}/draft/{self.request.context.draft_id}/{page}'
    @property
    def state_path(self):return f'/quick-relist-pipeline/{self.epoch}/state'
    def state(self):
        return {'request_hash':self.request.fingerprint,'context':asdict(self.request.context),'draft_state':self.draft_state,'revision':self.revision,
                'fields':self.fields,'cards':self.cards,'upload_receipts':self.uploads,'receipts':self.receipts,
                'snapshots':self.snapshots,'metadata_receipts':self.metadata.receipts if self.metadata else [],
                'partial_upload':bool(self.uploads) and len(self.receipts)==len(self.request.forms)}
    def append(self,command):
        snapshot=json.loads(_json({'fields':self.fields,'cards':self.cards,'upload_receipts':self.uploads}))
        self.receipts.append({'command':command,'command_hash':_digest(command),'revision':self.revision,'state_hash':_digest(snapshot)})
        self.snapshots.append(snapshot)
    def command_at(self,index):
        validate_pipeline_state(self.request,self.state())
        require(type(index) is int and 0<=index<=len(self.receipts) and index<self.request.count)
        if index<len(self.receipts):return self.receipts[index]['command']
        return self.request.command(index,self.revision)
    def check_command(self,index,command):
        require(index==len(self.receipts))
        require(_json(command)==_json(self.command_at(index)))
        return command
    def fields_post(self,index,body):
        self.field_posts+=1;command=self.check_command(index,body.get('command'));form=self.request.form(index,command['expected_revision'])
        require(set(body)=={'request_hash','fields','trusted','command'} and body['request_hash']==self.request.fingerprint and type(body['trusted']) is bool)
        require(_json(body['command'])==_json(command))
        desired={f.key:form.desired(f) for f in form.fields}
        if _json(body['fields'])!=_json(desired):return False
        self.fields={**self.fields,form.context.page:json.loads(_json(body['fields']))}
        self.revision='pipeline-fields-'+str(index+1);self.trusted.append(body['trusted']);self.append(command);return True
    def upload_post(self,kind,body,command=None):
        self.byte_posts+=1;index=len(self.request.forms)
        command=self.check_command(index,self.command_at(index) if command is None else command)
        require(self.stop_upload_after is None or len(self.uploads)<self.stop_upload_after)
        if self.upload_request is None:self.upload_request=self.request.upload_at(self.revision)
        matches=[f for f in self.upload_request.files if f.kind==kind and f.size==len(body) and f.sha256==hashlib.sha256(body).hexdigest()]
        require(len(matches)==1);f=matches[0];require(all(r['media_id']!=f.media_id for r in self.uploads))
        receipt=next(r for r in self.request.expected_upload_receipts(self.upload_request) if r['media_id']==f.media_id)
        self.uploads.append(receipt)
        self.cards += self.request.initial_cards((f,));self.cards[-1]['position']=len(self.cards)-1
        if len(self.uploads)==len(self.request.upload.files):
            self.revision='pipeline-upload-complete'
            self.append(command)
            req=self.request.metadata(self.revision,self.uploads,self.cards,self.upload_request)
            self.metadata=MetadataModel(req);self.metadata.epoch=self.nonce('metadata')
        return receipt
    def metadata_post(self,body):
        self.metadata_posts+=1;require(self.metadata is not None)
        index=len(self.receipts);require(index>len(self.request.forms) and index<self.request.count)
        command=self.check_command(index,self.command_at(index))
        require(self.metadata.revision==self.revision)
        require(body['command']['expected_revision']==command['expected_revision'])
        require(body['command']['ordinal']==index-len(self.request.forms)-1)
        receipt=self.metadata.apply(body['command'],body['payload'],body['events'])
        self.cards=self.metadata.cards;self.revision=self.metadata.revision;self.append(command)
        return receipt
    def field_document(self,index):
        command=self.command_at(index);form=self.request.form(index,command['expected_revision']);nonce=self.nonce(index)
        controls=[]
        for field in form.fields:
            tag='textarea' if field.key=='marketing_header' else 'input'
            label='Marketing header description' if field.key=='marketing_header' else field.key
            controls.append(f'<label for="{field.key}">{label}</label><{tag} id="{field.key}" name="{field.key}"></{tag}>' if tag=='textarea' else f'<label for="{field.key}">{label}</label><input id="{field.key}" name="{field.key}" type="text">')
        config=_json({'baseline':{f.key:f.baseline for f in form.fields},'request_hash':self.request.fingerprint,'command':command}).replace('<','\\u003c')
        return ('<!doctype html><meta charset="utf-8"><style>input,textarea{display:block;width:500px;height:70px;margin:12px}</style>'
            +f'<body data-synthetic-only="true" data-fixture-nonce="{nonce}"><main id="quick-relist-synthetic-form" data-context="{escape(_json(asdict(form.context)),quote=True)}">'+''.join(controls)+'</main>'
            +'''<script>const config='''+config+''';for(const [key,value] of Object.entries(config.baseline))document.getElementById(key).value=value;
const send=event=>{const fields={};for(const key of Object.keys(config.baseline))fields[key]=document.getElementById(key).value;
fetch(location.pathname+'/fields',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_hash:config.request_hash,command:config.command,fields,trusted:event.isTrusted})}).then(r=>r.json()).then(r=>{if(r.applied===true&&r.receipt.command.operation_id===config.command.operation_id){let output=document.getElementById('pipeline-save-receipt');if(!output){output=document.createElement('output');output.id='pipeline-save-receipt';document.body.append(output);}output.textContent=JSON.stringify(r.receipt);}}).catch(()=>{});};
for(const name of ['input','change'])document.addEventListener(name,send);</script>''')


@contextmanager
def serve(request,**options):
    model=Service(request,**options)
    class Handler(BaseHTTPRequestHandler):
        def reply(self,data,content='text/html; charset=utf-8'):
            data=data.encode() if isinstance(data,str) else data
            try:
                self.send_response(200);self.send_header('Content-Type',content);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers()
                self.wfile.write(data)
            except ConnectionError:pass
        def do_GET(self):
            with model.lock:
                if self.path==model.state_path:
                    data=f'<main id="pipeline-state" data-epoch="{model.epoch}" data-request="{request.fingerprint}"><pre>'+escape(_json(model.state()))+'</pre></main>'
                elif len(model.receipts)==len(request.forms) and self.path==model.path(len(request.forms)):
                    upload=request.upload_at(model.revision);data=upload_document(upload,model.nonce(len(request.forms)))
                elif model.metadata and self.path==model.path(len(request.forms)+1):
                    data=metadata_document(model.metadata,model.metadata.epoch)
                else:
                    indexes=[i for i in range(len(request.forms)) if i==len(model.receipts) and self.path==model.path(i)]
                    if not indexes:self.send_error(404);return
                    data=model.field_document(indexes[0])
            self.reply(data)
        def do_POST(self):
            try:
                size=int(self.headers.get('Content-Length','0'));require(0<size<=32*1024*1024);body=self.rfile.read(size)
                with model.lock:
                    stage=len(model.receipts)
                    if stage<len(request.forms) and self.path==model.path(stage)+'/fields':
                        applied=model.fields_post(stage,json.loads(body));result={'applied':applied,'receipt':model.receipts[-1] if applied else None}
                    elif stage==len(request.forms) and self.path.startswith(model.path(len(request.forms))+'/upload/'):
                        result=model.upload_post(self.path.rsplit('/',1)[1],body,model.command_at(stage))
                    elif model.metadata and self.path==model.path(len(request.forms)+1)+'/commit':result=model.metadata_post(json.loads(body))
                    else:raise ValueError()
                    drop=model.drop_response_stage==stage
                if drop:self.close_connection=True;return
                if model.response_delay:time.sleep(model.response_delay)
                self.reply(_json(result),'application/json')
            except Exception:
                try:self.send_error(409)
                except ConnectionError:pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:yield origin,model
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


class OfflineProvider:
    """Coordinator fault tests only; actual browser qualification is separate."""
    def __init__(self,service):self.service=service;self.fail_before=None;self.fail_after=None;self.calls=[];self.prepared=None
    def binding(self):return {'epoch':self.service.epoch,'origin':'http://127.0.0.1:12345','request_hash':self.service.request.fingerprint}
    def inspect(self):return json.loads(_json(self.service.state()))
    def preflight(self,command):
        require(_json(command)==_json(self.service.command_at(command['ordinal'])))
    def _child(self,store):
        from quick_relist.media_metadata import MetadataWorkflow
        model=self.service.metadata;require(model is not None)
        service=self.service
        class Inner:
            def binding(self):return {'epoch':model.epoch,'origin':'http://127.0.0.1:12345/metadata','request_hash':model.request.fingerprint}
            def inspect(self):return model.state()
            def preflight(self,command,cards):require(_json(command)==_json(model.command()) and _json(cards)==_json(model.cards))
            def perform(self,command,cards):service.metadata_post({'command':command,'payload':command['payload'],'events':[]})
        return MetadataWorkflow(store,model.request,Inner())
    def _proof(self,store):
        request=self.service.metadata.request
        row=store.conn.execute('SELECT request_json,draft_key,server_binding_json FROM metadata_bindings WHERE operation_id=?',(request.operation_id,)).fetchone()
        require(row is not None and row['request_json']==_json(asdict(request)))
        return {'operation_id':request.operation_id,'request_hash':request.fingerprint,'binding_hash':_digest(dict(row))}
    def prepare_child(self,store):
        self.prepared=self._child(store);self.prepared._load(create=True);return self._proof(store)
    def check_child_binding(self,store,binding):require(_json(self._proof(store))==_json(binding))
    def reconcile_child(self,store):
        if not self.service.metadata:return
        rows=store.conn.execute('SELECT event_json FROM pipeline_events WHERE operation_id=?',(self.service.request.operation_id,)).fetchall()
        enrolled=[json.loads(r[0]) for r in rows if json.loads(r[0]).get('phase')=='child']
        if not enrolled:require(not self.service.metadata.receipts);return
        require(len(enrolled)==1);self.check_child_binding(store,enrolled[0]['binding'])
        report=self._child(store)._reconcile_locked()
        require(report.status in ('ready','synthetic_complete','uncertain') and report.completed_stages==len(self.service.metadata.receipts))
    def perform(self,command,store):
        self.calls.append(command);i=command['ordinal'];request=self.service.request
        if i==self.fail_before:raise TimeoutError('Synthetic private error')
        if command['stage']=='fields':
            f=request.form(i,command['expected_revision'])
            self.service.fields_post(i,{'request_hash':request.fingerprint,'command':command,'fields':{x.key:f.desired(x) for x in f.fields},'trusted':False})
        elif command['stage']=='upload':
            from pathlib import Path
            for kind in ('photo','floorplan'):
                for f in request.upload.files:
                    if f.kind==kind:self.service.upload_post(kind,Path(f.local_path).read_bytes(),command)
        else:self.prepared._advance_locked()
        if i==self.fail_after:raise KeyboardInterrupt()
