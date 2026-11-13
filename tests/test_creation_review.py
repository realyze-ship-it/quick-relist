"""Independent adversarial review; synthetic state only, no external service."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creation_fixture import Crash, SyntheticCreationProvider
from test_creation import creation_request
from quick_relist.creation import CreationBlocked, CreationStore, DraftCreator
from quick_relist.mutations import canonical


class IndependentCreationReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.request = creation_request(self.root)
        self.path = self.root / 'private-ledger' / 'review.sqlite3'
        self.store = CreationStore(self.path)
        self.provider = SyntheticCreationProvider(self.request)
        self.creator = DraftCreator(self.store, self.provider)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def reopen(self):
        self.store.close()
        self.store = CreationStore(self.path)
        self.creator = DraftCreator(self.store, self.provider)

    def test_each_pre_remote_process_crash_requires_read_only_resolution(self):
        count = len(self.creator._plan(self.request)) - 1
        for ordinal in range(count):
            with self.subTest(ordinal=ordinal):
                with patch.object(self.provider, 'execute', side_effect=Crash):
                    with self.assertRaises(Crash):
                        self.creator.advance_for_testing(self.request)
                self.reopen()
                calls = len(self.provider.calls)
                with self.assertRaises(CreationBlocked):
                    self.creator.advance_for_testing(self.request, retry_not_applied=True)
                self.creator.reconcile_for_testing(self.request)
                self.assertEqual(len(self.provider.calls), calls)
                with self.assertRaises(CreationBlocked):
                    self.creator.advance_for_testing(self.request)
                self.creator.advance_for_testing(self.request, retry_not_applied=True)
                self.assertEqual(len(self.provider.calls), calls + 1)
        self.assertEqual(self.creator.advance_for_testing(self.request)['status'], 'complete')
        self.assertEqual(len(self.provider.drafts), 1)
        self.assertEqual(len(self.provider.uploads), 1)

    def test_source_race_after_preflight_is_rejected_by_atomic_provider(self):
        preflight = self.provider.preflight
        def race(command):
            response = preflight(command)
            self.provider.source_revision = 'concurrent-source-edit'
            return response
        with patch.object(self.provider, 'preflight', side_effect=race):
            with self.assertRaises(CreationBlocked):
                self.creator.advance_for_testing(self.request)
        self.assertEqual(self.provider.drafts, {})
        self.assertEqual(self.creator.status(self.request)['status'], 'needs_reconciliation')

    def test_draft_race_after_preflight_is_rejected_by_atomic_provider(self):
        first = self.creator.advance_for_testing(self.request)
        preflight = self.provider.preflight
        def race(command):
            response = preflight(command)
            self.provider.drafts[first['draft_id']]['revision'] = 'concurrent-edit'
            return response
        with patch.object(self.provider, 'preflight', side_effect=race):
            with self.assertRaises(CreationBlocked):
                self.creator.advance_for_testing(self.request)
        self.assertEqual(self.provider.drafts[first['draft_id']]['fields'], {})

    def test_media_change_during_preflight_never_reaches_upload(self):
        upload = next(i for i, (stage, _) in enumerate(self.creator._plan(self.request)) if stage == 'upload')
        for _ in range(upload):
            self.creator.advance_for_testing(self.request)
        preflight = self.provider.preflight
        def race(command):
            response = preflight(command)
            Path(self.request.snapshot['media_manifest'][0]['local_path']).write_bytes(b'changed')
            return response
        calls = len(self.provider.calls)
        with patch.object(self.provider, 'preflight', side_effect=race):
            with self.assertRaises(CreationBlocked):
                self.creator.advance_for_testing(self.request)
        self.assertEqual(len(self.provider.calls), calls)
        self.assertEqual(self.provider.uploads, [])

    def test_wrong_consumed_bytes_remain_uncertain_until_authoritative_receipt(self):
        upload = next(i for i, (stage, _) in enumerate(self.creator._plan(self.request)) if stage == 'upload')
        for _ in range(upload):
            self.creator.advance_for_testing(self.request)
        execute = self.provider.execute
        def wrong_receipt(command, content):
            response = execute(command, content)
            response['consumed']['sha256'] = '0' * 64
            return response
        with patch.object(self.provider, 'execute', side_effect=wrong_receipt):
            with self.assertRaises(CreationBlocked):
                self.creator.advance_for_testing(self.request)
        self.reopen()
        self.assertEqual(self.creator.status(self.request)['status'], 'needs_reconciliation')
        self.creator.reconcile_for_testing(self.request)
        self.assertEqual(self.creator.advance_for_testing(self.request)['status'], 'complete')
        self.assertEqual(len(self.provider.uploads), 1)

    def test_missing_ledger_is_never_terminal_absence(self):
        for method in (self.creator.status, self.creator.progress_for_testing,
                       self.creator.reconcile_for_testing, self.creator.checkpoint_for_testing):
            with self.subTest(method=method.__name__):
                with self.assertRaises(CreationBlocked):
                    method(self.request)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM creation_bindings').fetchone()[0], 0)

    def test_no_provider_testing_seam_also_fails_without_intent(self):
        creator = DraftCreator(self.store)
        with self.assertRaises(CreationBlocked):
            creator.advance_for_testing(self.request)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM creation_bindings').fetchone()[0], 0)

    def test_hardlinked_database_is_rejected(self):
        link = self.root / 'alias.sqlite3'
        os.link(self.path, link)
        with self.assertRaises(CreationBlocked):
            CreationStore(link)

    def test_corrupt_receipt_revision_cannot_advance(self):
        self.creator.advance_for_testing(self.request)
        row = self.store.conn.execute('SELECT sequence,event_json FROM creation_events ORDER BY sequence DESC LIMIT 1').fetchone()
        event = json.loads(row['event_json'])
        event['receipt']['previous_draft_revision'] = 'invented-parent'
        with self.store._tx() as db:
            db.execute('DROP TRIGGER immutable_creation_event_update')
            db.execute('UPDATE creation_events SET event_json=? WHERE sequence=?', (canonical(event), row['sequence']))
        calls = len(self.provider.calls)
        with self.assertRaises(CreationBlocked):
            self.creator.advance_for_testing(self.request)
        self.assertEqual(len(self.provider.calls), calls)

    def test_verify_response_extras_and_exceptions_never_complete(self):
        writes = len(self.creator._plan(self.request)) - 1
        for _ in range(writes):
            self.creator.advance_for_testing(self.request)
        self.provider.inspection_change = lambda response: response.update(secret='SECRET/local/path')
        with self.assertRaises(CreationBlocked) as caught:
            self.creator.advance_for_testing(self.request)
        self.assertNotIn('SECRET', str(caught.exception))
        self.provider.inspection_change = None
        with patch.object(self.provider, 'inspect', side_effect=RuntimeError('SECRET/local/path')):
            with self.assertRaises(CreationBlocked) as caught:
                self.creator.advance_for_testing(self.request)
        self.assertNotIn('SECRET', str(caught.exception))
        events = self.store.conn.execute('SELECT event_json FROM creation_events').fetchall()
        self.assertFalse(any(json.loads(row[0])['phase'] == 'complete' for row in events))
        self.assertFalse(any('SECRET' in row[0] for row in events))


if __name__ == '__main__':
    unittest.main()
