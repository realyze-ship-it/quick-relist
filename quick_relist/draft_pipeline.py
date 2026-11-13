"""Synthetic-only composition of existing field, upload and metadata handlers.

Starts with a pinned unpublished draft, not allocation or a complete Hub source.
No live provider, implicit retry, rollback or publication is implemented.
"""
from dataclasses import asdict, dataclass, replace
import json

from .hub_form_writer import FormTransferRequest, TEXT_FIELDS, ADDRESS_NAMES, _json, _digest
from .hub_media_writer import MediaTransferRequest
from .media_metadata import MetadataRequest, MetadataStore, require


def identity(context):
    return {k:v for k,v in asdict(context).items() if k not in ('page','expected_draft_revision')}


@dataclass(frozen=True, slots=True)
class DraftPipelineRequest:
    forms: tuple[FormTransferRequest, ...]
    upload: MediaTransferRequest
    approved_address_json: str = "{}"

    def __post_init__(self):
        require(type(self.forms) is tuple and 0 < len(self.forms) <= 3)
        require(type(self.upload) is MediaTransferRequest)
        require(all(type(f) is FormTransferRequest for f in self.forms))
        require(len({f.context.page for f in self.forms}) == len(self.forms))
        approval=json.loads(self.approved_address_json)
        require(type(approval) is dict and _json(approval)==self.approved_address_json)
        require(set(approval).issubset(ADDRESS_NAMES) and _digest(approval)==self.upload.context.approved_address_sha256)
        address_keys={x.key for f in self.forms if f.context.page=='details' for x in f.fields}
        require(set(approval).issubset(address_keys))
        for form in self.forms:
            left,right=identity(form.context),identity(self.upload.context)
            left.pop('approved_address_sha256');right.pop('approved_address_sha256')
            require(_json(left) == _json(right))
            projection={k:v for k,v in approval.items() if k in {f.key for f in form.fields}}
            require(form.approved_address_json==_json(projection) and form.context.approved_address_sha256==_digest(projection))
            require(form.context.expected_draft_revision == self.upload.context.expected_draft_revision)
            # This composition qualifies text fields only. Other standalone
            # handlers remain available separately, never silently omitted here.
            require(all(f.key in TEXT_FIELDS[form.context.page] for f in form.fields))
        for f in self.upload.files:
            require(set(json.loads(f.metadata_json)) == {'caption','cover'})
        require(len(_json(asdict(self)).encode()) <= 2*1024*1024)
        # Validate source metadata without inventing an executable saved state.
        self.metadata(self.upload.context.expected_draft_revision,
                      self.expected_upload_receipts(self.upload), self.initial_cards(self.upload.files))

    @property
    def fingerprint(self): return _digest(asdict(self))
    @property
    def operation_id(self): return _digest({'pipeline':self.fingerprint})
    @property
    def context(self): return self.upload.context
    @property
    def count(self): return len(self.forms)+1+len(self.upload.files)+2
    @property
    def initial_fields(self):
        return {f.context.page:{x.key:x.baseline for x in f.fields} for f in self.forms}
    @property
    def desired_fields(self):
        return {f.context.page:{x.key:f.desired(x) for x in f.fields} for f in self.forms}
    @staticmethod
    def initial_cards(files):
        return [{'media_id':f.media_id,'upload_id':'synthetic-upload-'+f.media_id,
                 'sha256':f.sha256,'size':f.size,'kind':f.kind,'position':i,'caption':'','cover':False}
                for i,f in enumerate(files)]
    @staticmethod
    def expected_upload_receipts(upload):
        return [{'media_id':f.media_id,'upload_id':'synthetic-upload-'+f.media_id,
                 'sha256':f.sha256,'size':f.size,'kind':f.kind,'source_position':f.position,
                 'request':upload.fingerprint} for f in upload.files]
    def form(self, ordinal, revision):
        f=self.forms[ordinal]
        return replace(f,context=replace(f.context,expected_draft_revision=revision))
    def upload_at(self, revision):
        return replace(self.upload,context=replace(self.context,expected_draft_revision=revision))
    def metadata(self, revision, receipts, cards, upload=None):
        upload=self.upload if upload is None else upload
        return MetadataRequest.from_transfer(upload,replace(upload.context,expected_draft_revision=revision),receipts,cards)
    def command(self, ordinal, revision):
        require(type(ordinal) is int and 0 <= ordinal < self.count)
        stage='fields' if ordinal < len(self.forms) else 'upload' if ordinal == len(self.forms) else 'metadata'
        return {'operation_id':_digest({'parent':self.fingerprint,'ordinal':ordinal}),
                'request_hash':self.fingerprint,'ordinal':ordinal,'stage':stage,'expected_revision':revision}


def prepare_store(store):
    require(type(store) is MetadataStore)
    store.conn.executescript('''
    CREATE TABLE IF NOT EXISTS pipeline_bindings(operation_id TEXT PRIMARY KEY, request_json TEXT NOT NULL,
      binding_json TEXT NOT NULL, draft_key TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS pipeline_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
      operation_id TEXT NOT NULL REFERENCES pipeline_bindings(operation_id), event_json TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS pipeline_binding_update BEFORE UPDATE ON pipeline_bindings
      BEGIN SELECT RAISE(ABORT,'Immutable pipeline binding'); END;
    CREATE TRIGGER IF NOT EXISTS pipeline_binding_delete BEFORE DELETE ON pipeline_bindings
      BEGIN SELECT RAISE(ABORT,'Immutable pipeline binding'); END;
    CREATE TRIGGER IF NOT EXISTS pipeline_event_update BEFORE UPDATE ON pipeline_events
      BEGIN SELECT RAISE(ABORT,'Append-only pipeline journal'); END;
    CREATE TRIGGER IF NOT EXISTS pipeline_event_delete BEFORE DELETE ON pipeline_events
      BEGIN SELECT RAISE(ABORT,'Append-only pipeline journal'); END;
    ''')


@dataclass(frozen=True, slots=True)
class PipelineReport:
    status: str
    completed_stages: int = 0
    code: str = 'synthetic_only'
    live_supported: bool = False
    full_fidelity: bool = False
    hub_persistence_verified: bool = False
    allocation_verified: bool = False
    saved: bool | None = None


class DraftPipeline:
    def __init__(self, store, request, provider):
        require(type(request) is DraftPipelineRequest)
        prepare_store(store)
        self.store,self.request,self.provider=store,request,provider
    def execute(self): return PipelineReport('blocked',code='live_provider_unqualified')
    def _append(self,event):
        with self.store._tx() as db:
            db.execute('INSERT INTO pipeline_events(operation_id,event_json) VALUES(?,?)',
                       (self.request.operation_id,_json(event)))
    def _load(self,create=False):
        r=self.request;binding=self.provider.binding()
        require(type(binding) is dict and set(binding)=={'epoch','origin','request_hash'})
        require(binding['request_hash']==r.fingerprint)
        row=self.store.conn.execute('SELECT * FROM pipeline_bindings WHERE operation_id=?',(r.operation_id,)).fetchone()
        key=_digest({k:v for k,v in identity(r.context).items() if k in ('account_id','agency_id','draft_id')})
        if row is None:
            require(create)
            with self.store._tx() as db:db.execute('INSERT INTO pipeline_bindings VALUES(?,?,?,?)',
                (r.operation_id,_json(asdict(r)),_json(binding),key))
        else:require(row['request_json']==_json(asdict(r)) and row['binding_json']==_json(binding) and row['draft_key']==key)
        receipts=[];pending=None;revision=r.context.expected_draft_revision;seen={revision};child=None
        for row in self.store.conn.execute('SELECT event_json FROM pipeline_events WHERE operation_id=? ORDER BY sequence',(r.operation_id,)):
            event=json.loads(row[0]);require(_json(event)==row[0])
            if event.get('phase')=='child':
                require(set(event)=={'phase','binding'} and child is None and pending is None and len(receipts)==len(r.forms)+1)
                child=event['binding'];self.provider.check_child_binding(self.store,child)
            elif event.get('phase')=='intent':
                require(set(event)=={'phase','command'} and pending is None)
                pending=r.command(len(receipts),revision);require(_json(event['command'])==_json(pending))
                if pending['stage']=='metadata':require(child is not None)
            elif event.get('phase')=='receipt':
                require(set(event)=={'phase','receipt'} and pending is not None)
                receipt=event['receipt'];self._receipt(pending,receipt)
                require(receipt['revision'] not in seen);seen.add(receipt['revision'])
                receipts.append(receipt);revision=receipt['revision'];pending=None
            else:require(False)
        if child is not None:self.provider.check_child_binding(self.store,child)
        return receipts,pending,revision
    def _receipt(self,command,receipt):
        require(type(receipt) is dict and set(receipt)=={'command','command_hash','revision','state_hash'})
        require(_json(receipt['command'])==_json(command) and receipt['command_hash']==_digest(command))
        require(type(receipt['revision']) is str and 0<len(receipt['revision'])<=128)
        require(receipt['revision']!=command['expected_revision'])
        require(type(receipt['state_hash']) is str and len(receipt['state_hash'])==64)
    def _observe(self,loaded):
        receipts,pending,revision=loaded
        state=self.provider.inspect()
        remote,_=validate_pipeline_state(self.request,state)
        require(_json(remote[:len(receipts)])==_json(receipts))
        require(len(remote) in (len(receipts),len(receipts)+(1 if pending else 0)))
        if len(remote)>len(receipts):
            receipt=remote[-1];self._receipt(pending,receipt)
            self._append({'phase':'receipt','receipt':receipt});return self._load()
        return loaded
    def _report(self,loaded):
        receipts,pending,_=loaded
        return PipelineReport('uncertain' if pending else 'synthetic_complete' if len(receipts)==self.request.count else 'ready',len(receipts))
    def advance_for_testing(self):
        try:
            with self.store.operation_lock():
                loaded=self._load(create=True)
                if loaded[1]:return self._report(loaded)
                loaded=self._observe(loaded)
                receipts,_,revision=loaded
                if len(receipts)==self.request.count:return self._report(loaded)
                command=self.request.command(len(receipts),revision)
                self.provider.preflight(command)
                if command['stage']=='metadata':
                    child=self.provider.prepare_child(self.store)
                    existing=[json.loads(row[0]) for row in self.store.conn.execute('SELECT event_json FROM pipeline_events WHERE operation_id=?',(self.request.operation_id,)) if json.loads(row[0]).get('phase')=='child']
                    if not existing:self._append({'phase':'child','binding':child})
                    else:require(_json(existing[0]['binding'])==_json(child))
                self._append({'phase':'intent','command':command})
                try:
                    if self.provider.perform(command,self.store) is False:
                        return PipelineReport('uncertain',len(receipts),'same_page_receipt_unobserved')
                except (Exception,KeyboardInterrupt):
                    return PipelineReport('uncertain',len(receipts),'interaction_uncertain')
                try:
                    self.provider.reconcile_child(self.store)
                    return self._report(self._observe(self._load()))
                except (Exception,KeyboardInterrupt):
                    return PipelineReport('uncertain',len(receipts),'post_interaction_readback_unverified')
        except (Exception,KeyboardInterrupt):return PipelineReport('blocked',code='pipeline_evidence_rejected')
    def reconcile_for_testing(self):
        try:
            with self.store.operation_lock():
                self.provider.reconcile_child(self.store)
                return self._report(self._observe(self._load()))
        except (Exception,KeyboardInterrupt):return PipelineReport('blocked',code='pipeline_reconciliation_unverified')


def validate_pipeline_state(request, state):
    """Replay exact complete modeled history; no provider success flag is trusted."""
    from types import SimpleNamespace
    from .media_metadata import MetadataWorkflow
    r=request
    require(type(state) is dict and set(state)=={'request_hash','context','draft_state','revision','fields','cards','upload_receipts',
        'receipts','snapshots','metadata_receipts','partial_upload'})
    require(state['request_hash']==r.fingerprint and _json(state['context'])==_json(asdict(r.context)))
    require(state['draft_state']=='draft')
    require(type(state['receipts']) is list and type(state['snapshots']) is list and len(state['receipts'])==len(state['snapshots'])<=r.count)
    revision=r.context.expected_draft_revision;seen={revision};fields=r.initial_fields;cards=[];uploads=[];metadata=None
    meta_receipts=state['metadata_receipts'];require(type(meta_receipts) is list)
    for i,(receipt,snapshot) in enumerate(zip(state['receipts'],state['snapshots'])):
        command=r.command(i,revision)
        require(type(receipt) is dict and set(receipt)=={'command','command_hash','revision','state_hash'})
        require(_json(receipt['command'])==_json(command) and receipt['command_hash']==_digest(command))
        require(type(receipt['revision']) is str and receipt['revision'] not in seen and 0<len(receipt['revision'])<=128)
        require(type(snapshot) is dict and set(snapshot)=={'fields','cards','upload_receipts'})
        require(receipt['state_hash']==_digest(snapshot))
        if i<len(r.forms):
            fields={**fields,r.forms[i].context.page:r.desired_fields[r.forms[i].context.page]}
        elif i==len(r.forms):
            upload=r.upload_at(revision);uploads=r.expected_upload_receipts(upload)
            require(sorted(map(_json,snapshot['upload_receipts']))==sorted(map(_json,uploads)))
            cards=snapshot['cards']
            metadata=r.metadata(receipt['revision'],snapshot['upload_receipts'],cards,upload)
            # Uploads create empty captions and explicit no-cover only in this
            # synthetic service. No first-photo or filename inference is used.
            require(all(c['caption']=='' and c['cover'] is False for c in cards))
        else:
            j=i-len(r.forms)-1;require(metadata is not None and j<len(meta_receipts))
            inner=metadata.command(j,revision,cards)
            clean=MetadataWorkflow._receipt(SimpleNamespace(request=metadata),inner,meta_receipts[j])
            require(clean['status']=='applied' and clean['revision']==receipt['revision'])
            cards=metadata.after(cards,j)
        require(_json(snapshot['fields'])==_json(fields) and _json(snapshot['cards'])==_json(cards))
        require(sorted(map(_json,snapshot['upload_receipts']))==sorted(map(_json,uploads)))
        revision=receipt['revision'];seen.add(revision)
    require(len(meta_receipts)==max(0,len(state['receipts'])-len(r.forms)-1))
    require(state['revision']==revision and _json(state['fields'])==_json(fields))
    partial=state['partial_upload'];require(type(partial) is bool)
    if partial:
        require(len(state['receipts'])==len(r.forms))
        expected=r.expected_upload_receipts(r.upload_at(revision));actual=state['upload_receipts']
        require(type(actual) is list and 0<len(actual)<len(expected))
        require(len(set(map(_json,actual)))==len(actual) and all(_json(a) in set(map(_json,expected)) for a in actual))
        by_id={f.media_id:f for f in r.upload.files}
        require(_json(state['cards'])==_json(r.initial_cards([by_id[a['media_id']] for a in actual])))
    else:
        require(_json(state['cards'])==_json(cards))
        require(sorted(map(_json,state['upload_receipts']))==sorted(map(_json,uploads)))
    if len(state['receipts'])==r.count:
        require(metadata is not None and _json(cards)==_json(metadata.desired) and _json(fields)==_json(r.desired_fields))
    return state['receipts'],metadata
