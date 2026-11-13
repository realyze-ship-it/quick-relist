"""Durable receipts for the ordinary Hub UI workflow.

An uncertain click is never automatically repeated. These records describe
what the application observed; they do not claim server-side idempotency.
"""
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4


class RunNeedsReview(RuntimeError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _source_key(reference):
    from .core import normalize_rxcu
    return normalize_rxcu(reference)


class RunStore:
    def __init__(self, path):
        if str(path) != ':memory:':
            path = Path(path)
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError('The run journal must be stored in the private app folder.')
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('PRAGMA synchronous=FULL')
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS relist_runs (
          id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
          binding_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS relist_actions (
          run_id TEXT NOT NULL REFERENCES relist_runs(id), action TEXT NOT NULL,
          started_at TEXT NOT NULL, finished_at TEXT, state TEXT NOT NULL,
          receipt_json TEXT, PRIMARY KEY(run_id, action)
        );
        CREATE TABLE IF NOT EXISTS relist_events (
          id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES relist_runs(id),
          recorded_at TEXT NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL
        );
        ''')
        self.conn.commit()

    def close(self):
        with self._lock:
            self.conn.close()

    def start(self, job_id, *, source_capture_id, address_revision_id, reference, listing_id):
        binding = {'source_capture_id': source_capture_id, 'address_revision_id': address_revision_id,
                   'reference': reference, 'listing_id': listing_id}
        if any(not isinstance(v, str) or not v for v in [job_id, *binding.values()]):
            raise ValueError('A collected listing and saved address review are required.')
        encoded = _json(binding)
        with self._lock, self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            previous = self.conn.execute('SELECT * FROM relist_runs WHERE job_id=?', (job_id,)).fetchone()
            if previous:
                if previous['binding_json'] != encoded:
                    raise RunNeedsReview('This listing already has a started run. Review that run before changing its source or address.')
                return previous['id']
            self._assert_source_available(job_id, reference, listing_id)
            ident = uuid4().hex
            self.conn.execute('INSERT INTO relist_runs VALUES(?,?,?,?)', (ident, job_id, _now(), encoded))
            return ident

    def _assert_source_available(self, job_id, reference, listing_id=None):
        # A new saved batch must not bypass an earlier source's journal.
        # Check legacy rows too; their references may contain a hyphen.
        for row in self.conn.execute('SELECT job_id,binding_json FROM relist_runs WHERE job_id!=?', (job_id,)):
            binding = json.loads(row['binding_json'])
            if (_source_key(binding['reference']) == _source_key(reference)
                    or listing_id is not None and binding['listing_id'] == listing_id):
                raise RunNeedsReview('This original already has a relist record in another saved batch. Open that batch and check its outcome; a new batch cannot create another replacement.')

    def assert_source_available(self, job_id, reference, listing_id=None):
        with self._lock:
            self._assert_source_available(job_id, reference, listing_id)

    def get(self, job_id):
        with self._lock:
            row = self.conn.execute('SELECT * FROM relist_runs WHERE job_id=?', (job_id,)).fetchone()
            if row is None:
                return None
            actions = self.conn.execute('SELECT * FROM relist_actions WHERE run_id=? ORDER BY started_at', (row['id'],)).fetchall()
            events = self.conn.execute('SELECT * FROM relist_events WHERE run_id=? ORDER BY id', (row['id'],)).fetchall()
        return {'id': row['id'], 'job_id': job_id, 'binding': json.loads(row['binding_json']),
                'actions': [{**dict(a), 'receipt': json.loads(a['receipt_json']) if a['receipt_json'] else None} for a in actions],
                'events': [{'kind': e['kind'], 'data': json.loads(e['data_json'])} for e in events]}

    def note(self, run_id, kind, data):
        with self._lock, self.conn:
            self.conn.execute('INSERT INTO relist_events(run_id,recorded_at,kind,data_json) VALUES(?,?,?,?)',
                              (run_id, _now(), kind, _json(data)))

    def reconcile_removal(self, run_id, receipt):
        """Record a fresh read of completed removal; never replay that click."""
        if receipt.get('reconciled_by') != 'fresh_source_and_portal_read' or receipt.get('observed', {}).get('published') is not False:
            raise RunNeedsReview('A fresh source and removal observation is required.')
        with self._lock, self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            actions = self.conn.execute('SELECT action,state FROM relist_actions WHERE run_id=?', (run_id,)).fetchall()
            if len(actions) != 1 or actions[0]['action'] != 'remove_portals' or actions[0]['state'] != 'uncertain':
                raise RunNeedsReview('Only a stopped removal with no later actions can be reconciled here.')
            self.conn.execute("UPDATE relist_actions SET state='done', finished_at=?, receipt_json=? WHERE run_id=? AND action='remove_portals'",
                              (_now(), _json(receipt), run_id))
            self.conn.execute('INSERT INTO relist_events(run_id,recorded_at,kind,data_json) VALUES(?,?,?,?)',
                              (run_id, _now(), 'removal_reconciled', _json(receipt)))

    def perform_once(self, run_id, action, operation):
        """Commit intent before a UI action; never repeat an uncertain attempt."""
        with self._lock, self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            existing = self.conn.execute('SELECT * FROM relist_actions WHERE run_id=? AND action=?', (run_id, action)).fetchone()
            if existing:
                if existing['state'] == 'done':
                    return json.loads(existing['receipt_json'])
                if existing['state'] != 'retry_ready' or action not in ('archive','copy_media_text'):
                    raise RunNeedsReview('A previous Hub action has an uncertain result. Check the existing listing before continuing; it was not retried.')
            unresolved = self.conn.execute("SELECT 1 FROM relist_actions WHERE run_id=? AND state!='done' AND action!=?", (run_id,action)).fetchone()
            if unresolved:
                raise RunNeedsReview('Resolve the earlier uncertain Hub action before starting another step.')
            if existing:
                self.conn.execute('INSERT INTO relist_events(run_id,recorded_at,kind,data_json) VALUES(?,?,?,?)',
                                  (run_id,_now(),'checked_' + action + '_retry',_json(dict(existing))))
                self.conn.execute("UPDATE relist_actions SET started_at=?,finished_at=NULL,state='started',receipt_json=NULL WHERE run_id=? AND action=?", (_now(),run_id,action))
            else:
                self.conn.execute('INSERT INTO relist_actions VALUES(?,?,?,?,?,?)',
                                  (run_id, action, _now(), None, 'started', None))
        try:
            receipt = operation()
            if not isinstance(receipt, dict) or not receipt:
                raise ValueError('A nonempty observed outcome is required.')
            encoded = _json(receipt)
        except BaseException:
            with self._lock, self.conn:
                self.conn.execute("UPDATE relist_actions SET state='uncertain', finished_at=? WHERE run_id=? AND action=?",
                                  (_now(), run_id, action))
            raise
        with self._lock, self.conn:
            self.conn.execute("UPDATE relist_actions SET state='done', finished_at=?, receipt_json=? WHERE run_id=? AND action=?",
                              (_now(), encoded, run_id, action))
        return receipt

    def reconcile_archive_not_applied(self, run_id, receipt):
        """Allow archive only after a freshly matched row still offers Archive."""
        if receipt.get('reconciled_by') != 'fresh_manage_listing_archive_action' or receipt.get('archived') is not False:
            raise RunNeedsReview('The existing archive outcome must be checked first.')
        with self._lock, self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            run=self.conn.execute('SELECT binding_json FROM relist_runs WHERE id=?',(run_id,)).fetchone()
            binding=json.loads(run['binding_json']) if run else {}
            if any(receipt.get(k)!=binding.get(k) for k in ('reference','listing_id')):
                raise RunNeedsReview('The archive check belongs to a different listing.')
            actions=self.conn.execute('SELECT * FROM relist_actions WHERE run_id=?',(run_id,)).fetchall()
            states={a['action']:a['state'] for a in actions}
            if states.get('remove_portals')!='done' or set(states)!={'remove_portals','archive'} or states['archive'] not in ('uncertain','retry_ready'):
                raise RunNeedsReview('This run cannot resume at archiving.')
            self.conn.execute('INSERT INTO relist_events(run_id,recorded_at,kind,data_json) VALUES(?,?,?,?)',
                              (run_id,_now(),'archive_not_applied',_json({'prior_action':dict(next(a for a in actions if a['action']=='archive')),'observed':receipt})))
            self.conn.execute("UPDATE relist_actions SET state='retry_ready',receipt_json=? WHERE run_id=? AND action='archive'",(_json(receipt),run_id))

    def reconcile_uploaded_photo(self, run_id, action, receipt):
        """Confirm a received photo without issuing another upload."""
        match = re.fullmatch(r'upload_photo_([0-9]+)_attempt_[1-3]', action)
        comparison = receipt.get('photo_comparison', {})
        count = receipt.get('received_count')
        if (not match or receipt.get('reconciled_by') != 'fresh_replacement_photo_comparison'
                or not isinstance(count, int) or count <= int(match[1])
                or comparison.get('order_matches') is not True
                or comparison.get('all_received') is not True
                or len(comparison.get('matches', [])) != count):
            raise RunNeedsReview('A complete comparison of the received photo prefix is required.')
        with self._lock, self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            rows = self.conn.execute('SELECT * FROM relist_actions WHERE run_id=?', (run_id,)).fetchall()
            actions = {row['action']: row for row in rows}
            if any(name not in actions or actions[name]['state'] != 'done' for name in ('remove_portals','archive','create')):
                raise RunNeedsReview('The completed original and replacement steps are required.')
            target = json.loads(actions['create']['receipt_json'])
            if any(receipt.get(key) != target.get(key) for key in ('reference','listing_id')):
                raise RunNeedsReview('The photo observation belongs to a different replacement.')
            existing = actions.get(action)
            if existing is None or existing['state'] not in ('uncertain','started'):
                raise RunNeedsReview('Only an unresolved upload can be reconciled.')
            self.conn.execute('INSERT INTO relist_events(run_id,recorded_at,kind,data_json) VALUES(?,?,?,?)',
                              (run_id,_now(),'upload_reconciled',_json({'prior_action':dict(existing),'observed':receipt})))
            self.conn.execute("UPDATE relist_actions SET state='done',finished_at=?,receipt_json=? WHERE run_id=? AND action=?",
                              (_now(),_json(receipt),run_id,action))

    def reconcile_media_text(self, run_id, receipt):
        """Reconcile the exact existing copy's saved descriptions before editing."""
        outcome = receipt.get('outcome')
        if (receipt.get('reconciled_by') != 'fresh_unpublished_media_read'
                or outcome not in ('matching','empty','formatting_changed')):
            raise RunNeedsReview('Fresh saved descriptions and unpublished status are required.')
        with self._lock, self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            rows = self.conn.execute('SELECT * FROM relist_actions WHERE run_id=?', (run_id,)).fetchall()
            actions = {row['action']: row for row in rows}
            if actions.get('create') is None or actions['create']['state'] != 'done':
                raise RunNeedsReview('The existing replacement must be recorded.')
            target = json.loads(actions['create']['receipt_json'])
            if any(receipt.get(key) != target.get(key) for key in ('reference','listing_id')):
                raise RunNeedsReview('The description check belongs to a different replacement.')
            existing = actions.get('copy_media_text')
            if existing is None or existing['state'] not in ('uncertain','started','retry_ready'):
                raise RunNeedsReview('Only a stopped description save can be reconciled.')
            if any(a['state'] != 'done' for n,a in actions.items() if n != 'copy_media_text'):
                raise RunNeedsReview('Resolve earlier upload outcomes first.')
            self.conn.execute('INSERT INTO relist_events(run_id,recorded_at,kind,data_json) VALUES(?,?,?,?)',
                              (run_id,_now(),'media_text_reconciled',_json({'prior_action':dict(existing),'observed':receipt})))
            self.conn.execute('UPDATE relist_actions SET state=?,finished_at=?,receipt_json=? WHERE run_id=? AND action=?',
                              ('done' if outcome == 'matching' else 'retry_ready',_now(),_json(receipt),run_id,'copy_media_text'))
