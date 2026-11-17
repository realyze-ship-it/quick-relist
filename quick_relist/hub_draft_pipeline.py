"""Strict localhost-only browser composition. Public Hub facade always blocks."""
from dataclasses import asdict
import json
import re
import time
from urllib.parse import urlsplit

from .draft_pipeline import DraftPipelineRequest, DraftPipeline, PipelineReport, validate_pipeline_state
from .hub_form_writer import bind_synthetic_fixture_for_tests, _json, _digest
from .hub_media_writer import bind_synthetic_media_for_tests
from .hub_media_metadata_writer import bind_synthetic_metadata_for_tests
from .media_metadata import require, MetadataStore


class HubDraftPipeline:
    def __init__(self,driver):self.driver=driver
    def transfer(self,request):return PipelineReport('blocked',code='live_provider_unqualified')


READ_STATE=r'''
return (()=>{const b=arguments[0],root=document.getElementById('pipeline-state');
if(location.href!==b.url||!root||document.querySelectorAll('#pipeline-state').length!==1
||root.dataset.epoch!==b.epoch||root.dataset.request!==b.request||document.querySelector('input,button,textarea,select,iframe,[contenteditable],[role]'))return null;
const rows=root.querySelectorAll('pre');if(rows.length!==1||root.children.length!==1||rows[0].parentElement!==root)return null;
const text=rows[0].textContent;if(text.length>2000000)return null;
try{return JSON.parse(text);}catch(_){return null;}})();
'''


READ_FIELD_RECEIPT=r'''
return (()=>{const b=arguments[0],root=document.getElementById('quick-relist-synthetic-form');
const bound=()=>location.href===b.url&&document.body.dataset.syntheticOnly==='true'
&&document.body.dataset.fixtureNonce===b.nonce&&root&&root.matches('main')
&&document.querySelectorAll('#quick-relist-synthetic-form').length===1&&root.getAttribute('data-context')===b.context;
if(!bound())return null;const nodes=document.querySelectorAll('#pipeline-save-receipt');
if(nodes.length!==1||nodes[0].tagName!=='OUTPUT'||nodes[0].parentElement!==document.body)return null;
const text=nodes[0].textContent;if(text.length>10000||!bound())return null;
try{return JSON.parse(text);}catch(_){return null;}})();
'''


def bind_synthetic_pipeline_for_tests(driver,request,store,*,origin,epoch):
    require(type(request) is DraftPipelineRequest and type(store) is MetadataStore)
    require(type(epoch) is str and re.fullmatch('[a-f0-9]{64}',epoch))
    parts=urlsplit(origin)
    require(parts.scheme=='http' and parts.hostname in ('127.0.0.1','localhost') and parts.port
            and origin==f'http://{parts.hostname}:{parts.port}' and not parts.username and not parts.password)
    require(driver.capabilities.get('browserName','').lower()=='chrome')
    provider=SyntheticPipelineBrowser(driver,request,origin,epoch)
    require(driver.current_url==provider.state_url)
    validate_pipeline_state(request,provider.inspect())
    return DraftPipeline(store,request,provider)


class SyntheticPipelineBrowser:
    def __init__(self,driver,request,origin,epoch):
        self.driver,self.request,self.origin,self.epoch=driver,request,origin,epoch
        self.handle=driver.current_window_handle;self.handles=tuple(sorted(driver.window_handles))
        self.state_url=f'{origin}/quick-relist-pipeline/{epoch}/state'
        self.prepared=None;self.meta=None;self.routes={};self.nonces={}
    def nonce(self,index):
        if index=='metadata':return _digest({'epoch':self.epoch,'stage':index})
        return self.nonces[index]
    def route(self,index,command=None):
        if index>len(self.request.forms):
            return f'{self.origin}/quick-relist-fixture/{self.nonce("metadata")}/draft/{self.request.context.draft_id}/media'
        if command is not None:
            nonce=_digest({'epoch':self.epoch,'stage':index,'command':command})
            page=self.request.forms[index].context.page if index<len(self.request.forms) else 'media'
            self.routes[index]=f'{self.origin}/quick-relist-fixture/{nonce}/draft/{self.request.context.draft_id}/{page}'
            self.nonces[index]=nonce
        return self.routes[index]
    def _guard(self):
        require(self.driver.current_window_handle==self.handle and tuple(sorted(self.driver.window_handles))==self.handles)
        allowed={self.state_url,*self.routes.values(),self.route(len(self.request.forms)+1)}
        require(self.driver.current_url in allowed)
    def _nav(self,url):
        self._guard();self.driver.get(url);self._guard();require(self.driver.current_url==url)
    def binding(self):
        self._guard()
        return {'epoch':self.epoch,'origin':self.origin,'request_hash':self.request.fingerprint}
    def inspect(self):
        self._nav(self.state_url)
        state=self.driver.execute_script(READ_STATE,{'url':self.state_url,'epoch':self.epoch,'request':self.request.fingerprint})
        self._guard();require(self.driver.current_url==self.state_url)
        validate_pipeline_state(self.request,state)
        return state
    def _metadata_request(self):
        state=self.inspect();_,request=validate_pipeline_state(self.request,state)
        require(request is not None);return state,request
    def preflight(self,command):
        state=self.inspect();require(len(state['receipts'])==command['ordinal'] and state['revision']==command['expected_revision'])
        require(not state['partial_upload'])
        index=command['ordinal'] if command['stage']=='fields' else len(self.request.forms)+(command['stage']=='metadata')
        self._nav(self.route(index,command if command['stage']!='metadata' else None))
        if command['stage']=='fields':
            request=self.request.form(command['ordinal'],command['expected_revision'])
            self.prepared=bind_synthetic_fixture_for_tests(self.driver,request,nonce=self.nonce(command['ordinal']))
        elif command['stage']=='upload':
            request=self.request.upload_at(command['expected_revision'])
            self.prepared=bind_synthetic_media_for_tests(self.driver,request,nonce=self.nonce(len(self.request.forms)))
        else:
            _,self.meta=validate_pipeline_state(self.request,state)
            require(self.meta is not None);self.prepared=None
    def _child_row(self,store,request):
        row=store.conn.execute('SELECT request_json,draft_key,server_binding_json FROM metadata_bindings WHERE operation_id=?',(request.operation_id,)).fetchone()
        require(row is not None)
        require(row['request_json']==_json(asdict(request)))
        return {'operation_id':request.operation_id,'request_hash':request.fingerprint,
                'binding_hash':_digest(dict(row))}
    def prepare_child(self,store):
        require(self.meta is not None)
        workflow=bind_synthetic_metadata_for_tests(self.driver,self.meta,store,nonce=self.nonce('metadata'))
        # Binding is persisted before the outer intent or any inner action.
        workflow._load(create=True)
        self.prepared=workflow
        return self._child_row(store,self.meta)
    def check_child_binding(self,store,binding):
        # Pending journal loads must not navigate and cancel in-flight work.
        # This proves the previously enrolled local identity only; explicit
        # reconciliation separately derives the request from service history.
        require(type(binding) is dict and set(binding)=={'operation_id','request_hash','binding_hash'})
        row=store.conn.execute('SELECT request_json,draft_key,server_binding_json FROM metadata_bindings WHERE operation_id=?',(binding['operation_id'],)).fetchone()
        require(row is not None and _digest(dict(row))==binding['binding_hash'])
        require(_digest(json.loads(row['request_json']))==binding['request_hash'])
    def perform(self,command,store):
        require(self.prepared is not None)
        observed=False;end=time.monotonic()+3
        if command['stage']=='metadata':
            self.prepared._advance_locked()
            target=command['ordinal']-len(self.request.forms)
            while time.monotonic()<end:
                report=self.prepared._reconcile_locked()
                if report.completed_stages==target and report.status in ('ready','synthetic_complete'):
                    observed=True;break
                time.sleep(.025)
        elif command['stage']=='fields':
            self.prepared.transfer()
            while time.monotonic()<end:
                self.prepared._guard()
                receipt=self.driver.execute_script(READ_FIELD_RECEIPT,{'url':self.route(command['ordinal']),
                    'nonce':self.nonce(command['ordinal']),
                    'context':_json(asdict(self.request.form(command['ordinal'],command['expected_revision']).context))})
                if (type(receipt) is dict and _json(receipt.get('command'))==_json(command)
                        and receipt.get('command_hash')==_digest(command)):
                    observed=True;break
                time.sleep(.025)
        else:
            self.prepared.transfer()
            while time.monotonic()<end:
                report=self.prepared.observe()
                if report.status=='synthetic_receipts_observed':observed=True;break
                time.sleep(.025)
        self.prepared=None
        return observed
    def reconcile_child(self,store):
        state=self.inspect();_,request=validate_pipeline_state(self.request,state)
        if request is None:return
        rows=store.conn.execute('SELECT event_json FROM pipeline_events WHERE operation_id=?',(self.request.operation_id,)).fetchall()
        enrolled=[json.loads(row[0]) for row in rows if json.loads(row[0]).get('phase')=='child']
        if not enrolled:
            require(not state['metadata_receipts']);return
        require(len(enrolled)==1)
        require(_json(self._child_row(store,request))==_json(enrolled[0]['binding']))
        self._nav(self.route(len(self.request.forms)+1))
        workflow=bind_synthetic_metadata_for_tests(self.driver,request,store,nonce=self.nonce('metadata'))
        report=workflow._reconcile_locked()
        require(report.status in ('ready','synthetic_complete','uncertain') and report.completed_stages==len(state['metadata_receipts']))
