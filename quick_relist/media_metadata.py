"""Synthetic-only durable media metadata stages; never a live Hub provider.

The journal reuses CreationStore's private storage and process lock. Modeled
receipts and CAS are requirements of the localhost test service, not discovered
Hub capabilities. Missing records never establish non-application. No upload,
delete, publication, source write, or implicit retry is implemented here.
"""
from dataclasses import asdict, dataclass
import json
import re

from .creation import CreationStore
from .hub_form_writer import TransferContext, TransferRejected, _json, _digest
from .hub_media_writer import MediaTransferRequest, _id


def require(ok):
    if not ok:
        raise TransferRejected('metadata_evidence_rejected')


def caption_valid(value):
    # Native textarea line endings are LF. Never silently normalize source CR,
    # NUL, webdriver key tokens, or unsupported control characters.
    if type(value) is not str:
        return False
    try:
        return (len(value.encode('utf-8')) <= 8192
                and all(c == '\n' or ord(c) >= 32 and not 0x7F <= ord(c) <= 0x9F and not 0xE000 <= ord(c) <= 0xF8FF for c in value))
    except UnicodeError:
        return False



@dataclass(frozen=True, slots=True)
class MetadataRequest:
    context: TransferContext
    parent_operation_id: str
    parent_hash: str
    upload_request_hash: str
    assets_json: str
    baseline_json: str

    def __post_init__(self):
        require(type(self.context) is TransferContext and self.context.page == 'media')
        require(_id(self.parent_operation_id))
        require(all(type(v) is str and re.fullmatch('[a-f0-9]{64}', v)
                    for v in (self.parent_hash, self.upload_request_hash)))
        for raw in (self.assets_json, self.baseline_json):
            require(type(raw) is str and len(raw.encode('utf-8')) <= 1024*1024)
            try:
                value = json.loads(raw)
                require(_json(value) == raw)
            except (ValueError, TypeError):
                raise TransferRejected('metadata_evidence_rejected') from None
        assets = self.assets
        require(type(assets) is list and 0 < len(assets) <= 100)
        keys = {'media_id', 'upload_id', 'sha256', 'size', 'kind', 'source_position', 'caption', 'cover'}
        for i, a in enumerate(assets):
            require(type(a) is dict and set(a) == keys)
            require(_id(a['media_id']) and _id(a['upload_id']))
            require(type(a['sha256']) is str and re.fullmatch('[a-f0-9]{64}', a['sha256']))
            require(type(a['size']) is int and 0 < a['size'] <= 32*1024*1024)
            require(a['kind'] in ('photo', 'floorplan') and type(a['kind']) is str)
            require(type(a['source_position']) is int and a['source_position'] == i)
            require(caption_valid(a['caption']) and type(a['cover']) is bool)
        require(sum(a['cover'] for a in assets) <= 1)
        require(sum(a['size'] for a in assets) <= 128*1024*1024)
        require(sum(len(a['caption'].encode('utf-8')) for a in assets) <= 128*1024)
        for key in ('media_id', 'upload_id', 'sha256'):
            require(len({a[key] for a in assets}) == len(assets))
        self.validate_cards(self.baseline)

    @classmethod
    def from_transfer(cls, transfer, context, receipts, baseline):
        """Bind modeled consumed-byte receipts; this does not authenticate Hub."""
        require(type(transfer) is MediaTransferRequest and type(context) is TransferContext)
        left, right = asdict(transfer.context), asdict(context)
        left.pop('expected_draft_revision'); right.pop('expected_draft_revision')
        require(_json(left) == _json(right))
        expected = [{'media_id': f.media_id, 'upload_id': 'synthetic-upload-'+f.media_id,
                     'sha256': f.sha256, 'size': f.size, 'kind': f.kind,
                     'source_position': f.position, 'request': transfer.fingerprint} for f in transfer.files]
        require(type(receipts) is list and len(receipts) == len(expected))
        require(sorted(map(_json, receipts)) == sorted(map(_json, expected)))
        assets = []
        for f, receipt in zip(transfer.files, expected):
            meta = json.loads(f.metadata_json)
            require(set(meta) == {'caption', 'cover'})  # Unknown/rotation/tags are NOT discarded.
            assets.append({k: receipt[k] for k in ('media_id', 'upload_id', 'sha256', 'size', 'kind', 'source_position')} | meta)
        return cls(context, transfer.parent_operation_id, transfer.parent_hash,
                   transfer.fingerprint, _json(assets), _json(baseline))

    @property
    def assets(self): return json.loads(self.assets_json)
    @property
    def baseline(self): return json.loads(self.baseline_json)
    @property
    def fingerprint(self): return _digest(asdict(self))
    @property
    def operation_id(self): return _digest({'metadata_parent': self.parent_operation_id, 'request': self.fingerprint})
    @property
    def desired(self):
        return [{k: v for k, v in a.items() if k != 'source_position'} | {'position': i}
                for i, a in enumerate(self.assets)]

    def validate_cards(self, cards):
        require(type(cards) is list and len(cards) == len(self.assets))
        by_id = {a['media_id']: a for a in self.assets}
        require(all(type(c) is dict and set(c) == {'media_id','upload_id','sha256','size','kind','caption','cover','position'} for c in cards))
        require(len({c['media_id'] for c in cards if type(c['media_id']) is str}) == len(cards))
        for i, card in enumerate(cards):
            require(card['media_id'] in by_id)
            require(type(card['position']) is int and card['position'] == i)
            require(caption_valid(card['caption']) and type(card['cover']) is bool)
            source = by_id[card['media_id']]
            require(_json({k: card[k] for k in ('media_id','upload_id','sha256','size','kind')}) ==
                    _json({k: source[k] for k in ('media_id','upload_id','sha256','size','kind')}))
        require(sum(c['cover'] for c in cards) <= 1)

    def stages(self):
        return ([{'kind': 'caption', 'media_id': a['media_id'], 'caption': a['caption']} for a in self.assets]
                + [{'kind': 'cover', 'media_id': next((a['media_id'] for a in self.assets if a['cover']), None)}]
                + [{'kind': 'order', 'media_ids': [a['media_id'] for a in self.assets]}])

    def after(self, cards, ordinal):
        self.validate_cards(cards)
        result = json.loads(_json(cards)); stage = self.stages()[ordinal]
        if stage['kind'] == 'caption':
            next(c for c in result if c['media_id'] == stage['media_id'])['caption'] = stage['caption']
        elif stage['kind'] == 'cover':
            for c in result: c['cover'] = c['media_id'] == stage['media_id']
        else:
            lookup = {c['media_id']: c for c in result}
            result = [lookup[key] | {'position': i} for i, key in enumerate(stage['media_ids'])]
        return result

    def command(self, ordinal, revision, cards):
        require(type(ordinal) is int and 0 <= ordinal < len(self.stages()))
        require(_id(revision)); self.validate_cards(cards)
        return {'operation_id': _digest({'request': self.fingerprint, 'ordinal': ordinal}),
                'request_hash': self.fingerprint, 'ordinal': ordinal, 'expected_revision': revision,
                'before_hash': _digest(cards), 'after_hash': _digest(self.after(cards, ordinal)),
                'payload': self.stages()[ordinal]}


class MetadataStore(CreationStore):
    """Private separate DB. Append-only tables do not modify creation records."""
    def __init__(self, path):
        super().__init__(path)
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS metadata_bindings(
          operation_id TEXT PRIMARY KEY, request_json TEXT NOT NULL, draft_key TEXT UNIQUE NOT NULL, server_binding_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata_events(
          sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL
          REFERENCES metadata_bindings(operation_id), event_json TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS immutable_metadata_binding_update BEFORE UPDATE ON metadata_bindings
          BEGIN SELECT RAISE(ABORT, 'Immutable metadata binding'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_metadata_binding_delete BEFORE DELETE ON metadata_bindings
          BEGIN SELECT RAISE(ABORT, 'Immutable metadata binding'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_metadata_event_update BEFORE UPDATE ON metadata_events
          BEGIN SELECT RAISE(ABORT, 'Append-only metadata journal'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_metadata_event_delete BEFORE DELETE ON metadata_events
          BEGIN SELECT RAISE(ABORT, 'Append-only metadata journal'); END;
        ''')


@dataclass(frozen=True, slots=True)
class MetadataReport:
    status: str
    completed_stages: int = 0
    pending_operation_id: str | None = None
    code: str = 'synthetic_only'
    live_supported: bool = False
    hub_persistence_verified: bool = False
    full_fidelity: bool = False
    saved: bool | None = None


class MetadataWorkflow:
    """The public write path blocks. Only explicit testing methods can dispatch.

    A terminal negative is reported for manual review, never retried by this
    slice. Retry UI/authorization is deliberately not implemented.
    """
    def __init__(self, store, request, provider):
        require(type(store) is MetadataStore and type(request) is MetadataRequest)
        self.store, self.request, self.provider = store, request, provider

    def execute(self): return MetadataReport('blocked', code='live_provider_unqualified')

    def _append(self, event):
        with self.store._tx() as db:
            db.execute('INSERT INTO metadata_events(operation_id,event_json) VALUES(?,?)',
                       (self.request.operation_id, _json(event)))

    def _receipt(self, command, receipt):
        require(type(receipt) is dict and set(receipt) == {'operation_id','command_hash','request_hash','ordinal',
                 'status','terminal','previous_revision','revision','before_hash','after_hash'})
        expected = {'operation_id': command['operation_id'], 'command_hash': _digest(command),
                    'request_hash': self.request.fingerprint, 'ordinal': command['ordinal'],
                    'terminal': True, 'previous_revision': command['expected_revision'],
                    'before_hash': command['before_hash']}
        require(_json({k: receipt.get(k) for k in expected}) == _json(expected))
        require(receipt['status'] in ('applied', 'not_applied') and _id(receipt['revision']))
        require(receipt['after_hash'] == (command['after_hash'] if receipt['status'] == 'applied' else command['before_hash']))
        require((receipt['revision'] != command['expected_revision']) if receipt['status'] == 'applied'
                else receipt['revision'] == command['expected_revision'])
        return json.loads(_json(receipt))

    def _load(self, create=False):
        r = self.request
        # Revalidate even objects reconstructed from untrusted local state.
        MetadataRequest(**asdict(r) | {'context': r.context})
        server_binding = self.provider.binding()
        require(type(server_binding) is dict and set(server_binding) == {'epoch','origin','request_hash'})
        require(type(server_binding['epoch']) is str and re.fullmatch('[a-f0-9]{64}',server_binding['epoch']))
        require(type(server_binding['origin']) is str and 0 < len(server_binding['origin']) < 512)
        require(server_binding['request_hash'] == r.fingerprint)
        row = self.store.conn.execute('SELECT request_json,draft_key,server_binding_json FROM metadata_bindings WHERE operation_id=?',
                                      (r.operation_id,)).fetchone()
        binding = _json(asdict(r)); key = _digest({'account':r.context.account_id, 'agency':r.context.agency_id,
                                                  'draft':r.context.draft_id})
        if row is None:
            require(create)
            with self.store._tx() as db:
                db.execute('INSERT INTO metadata_bindings VALUES(?,?,?,?)', (r.operation_id,binding,key,_json(server_binding)))
        else: require(row['request_json'] == binding and row['draft_key'] == key
                      and row['server_binding_json'] == _json(server_binding))
        cards, revision, ordinal, pending, rejected = r.baseline, r.context.expected_draft_revision, 0, None, False
        revisions = {revision}
        for row in self.store.conn.execute('SELECT event_json FROM metadata_events WHERE operation_id=? ORDER BY sequence', (r.operation_id,)):
            event = json.loads(row[0]); require(_json(event) == row[0] and type(event) is dict)
            if event.get('phase') == 'intent':
                require(set(event) == {'phase','command'} and pending is None and not rejected)
                command = r.command(ordinal, revision, cards)
                require(_json(command) == _json(event['command'])); pending = command
            elif event.get('phase') == 'receipt':
                require(set(event) == {'phase','receipt'} and pending is not None and not rejected)
                receipt = self._receipt(pending, event['receipt'])
                if receipt['status'] == 'applied':
                    require(receipt['revision'] not in revisions); revisions.add(receipt['revision'])
                    cards = r.after(cards, ordinal); revision = receipt['revision']; ordinal += 1; pending = None
                else: rejected = True
            else: require(False)
        return cards, revision, ordinal, pending, rejected

    def _observe(self, cards, revision, ordinal, pending, rejected):
        r = self.request; state = self.provider.inspect()
        require(type(state) is dict and set(state) == {'request_hash','context','revision','cards','receipts'})
        require(state['request_hash'] == r.fingerprint and _json(state['context']) == _json(asdict(r.context)))
        r.validate_cards(state['cards']); require(type(state['receipts']) is list)
        # Validate the complete remote chain, including every previously journaled
        # stage, not just the pending key. No unexpected future stage is accepted.
        remote_cards, remote_rev = r.baseline, r.context.expected_draft_revision
        remote_ordinal, remote_rejected = 0, False
        remote_revisions = {remote_rev}
        for receipt in state['receipts']:
            require(not remote_rejected and remote_ordinal < len(r.stages()))
            cmd = r.command(remote_ordinal, remote_rev, remote_cards)
            clean = self._receipt(cmd, receipt)
            if clean['status'] == 'applied':
                require(clean['revision'] not in remote_revisions); remote_revisions.add(clean['revision'])
                remote_cards = r.after(remote_cards, remote_ordinal); remote_rev = clean['revision']; remote_ordinal += 1
            else: remote_rejected = True
        require(_json(state['cards']) == _json(remote_cards) and state['revision'] == remote_rev)
        require(remote_ordinal in (ordinal, ordinal + (1 if pending and not rejected else 0)))
        # Local receipts must be an exact prefix of authoritative modeled history.
        local_receipts = [json.loads(row[0])['receipt'] for row in self.store.conn.execute(
            "SELECT event_json FROM metadata_events WHERE operation_id=? ORDER BY sequence", (r.operation_id,))
            if json.loads(row[0]).get('phase') == 'receipt']
        require(_json(state['receipts'][:len(local_receipts)]) == _json(local_receipts))
        if pending and not rejected and len(state['receipts']) == len(local_receipts) + 1:
            receipt = self._receipt(pending, state['receipts'][-1])
            self._append({'phase':'receipt','receipt':receipt})
            return self._load()
        require(len(state['receipts']) == len(local_receipts))
        require(not remote_rejected or rejected)
        require(_json(cards) == _json(remote_cards) and revision == remote_rev)
        return cards, revision, ordinal, pending, rejected

    @staticmethod
    def _report(state, count):
        _, _, ordinal, pending, rejected = state
        return MetadataReport('manual_review' if rejected else 'uncertain' if pending else
                              'synthetic_complete' if ordinal == count else 'ready', ordinal,
                              pending['operation_id'] if pending else None,
                              'terminal_not_applied_no_retry' if rejected else 'synthetic_only')

    def _reconcile_locked(self):
        """Private composition helper; caller must hold this store's lock."""
        try:
            state = self._load(); state = self._observe(*state)
            return self._report(state, len(self.request.stages()))
        except (Exception, KeyboardInterrupt):
            return MetadataReport('blocked', code='reconciliation_unverified')

    def reconcile_for_testing(self):
        try:
            with self.store.operation_lock():
                return self._reconcile_locked()
        except (Exception, KeyboardInterrupt):
            return MetadataReport('blocked', code='reconciliation_unverified')

    def _advance_locked(self):
        """Private composition helper; caller must hold this store's lock."""
        try:
            state = self._load(create=True)
            if state[3] is not None or state[4]:
                return self._report(state, len(self.request.stages()))
            state = self._observe(*state)
            cards, revision, ordinal, pending, rejected = state
            if pending or rejected or ordinal == len(self.request.stages()):
                return self._report(state, len(self.request.stages()))
            command = self.request.command(ordinal, revision, cards)
            self.provider.preflight(command, cards)
            self._append({'phase':'intent','command':command})
            try:
                self.provider.perform(command, cards)
            except (Exception, KeyboardInterrupt):
                return MetadataReport('uncertain', ordinal, command['operation_id'], 'interaction_uncertain')
            try:
                return self._report(self._observe(*self._load()), len(self.request.stages()))
            except (Exception, KeyboardInterrupt):
                return MetadataReport('uncertain', ordinal, command['operation_id'], 'post_interaction_readback_unverified')
        except (Exception, KeyboardInterrupt):
            return MetadataReport('blocked', code='metadata_preflight_or_journal_rejected')

    def advance_for_testing(self):
        try:
            with self.store.operation_lock():
                return self._advance_locked()
        except (Exception, KeyboardInterrupt):
            return MetadataReport('blocked', code='metadata_preflight_or_journal_rejected')
