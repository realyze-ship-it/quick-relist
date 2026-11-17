"""Offline metadata contracts; browser tests are separate and opt-in."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from quick_relist.hub_form_writer import _json, _digest, TransferRejected
from quick_relist.hub_media_metadata_writer import HubMediaMetadataWriter
from quick_relist.media_metadata import MetadataRequest, MetadataStore, MetadataWorkflow
from media_metadata_fixture import fixture_request, Model, OfflineProvider


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.request=fixture_request(self.tmp.name)
        self.path=Path(self.tmp.name)/'private'/'metadata.sqlite'
        self.store=MetadataStore(self.path);self.addCleanup(lambda:self.store.close())
        self.model=Model(self.request);self.provider=OfflineProvider(self.model)
        self.workflow=MetadataWorkflow(self.store,self.request,self.provider)

    def test_complete_exact_mixed_kind_order_captions_and_floorplan_cover(self):
        for i in range(len(self.request.stages())):
            report=self.workflow.advance_for_testing();self.assertEqual(report.completed_stages,i+1,report)
        self.assertEqual(report.status,'synthetic_complete')
        self.assertEqual(_json(self.model.cards),_json(self.request.desired))
        self.assertEqual([c['kind'] for c in self.model.cards],['photo','floorplan','photo'])
        self.assertEqual([c['cover'] for c in self.model.cards],[False,True,False])
        self.assertFalse(report.full_fidelity);self.assertFalse(report.hub_persistence_verified);self.assertIsNone(report.saved)
        self.workflow.advance_for_testing();self.assertEqual(self.provider.calls,5)

    def test_empty_and_leading_newline_are_distinct_not_trimmed(self):
        for _ in self.request.stages():self.workflow.advance_for_testing()
        self.assertEqual([c['caption'] for c in self.model.cards],['  First image\nSecond line  ','','\nThird image\n'])

    def test_intent_is_committed_before_provider_interaction(self):
        def check(command):
            other=sqlite3.connect(self.path)
            try:
                rows=other.execute('SELECT event_json FROM metadata_events').fetchall()
                self.assertEqual(json.loads(rows[-1][0]),{'phase':'intent','command':command})
            finally:other.close()
        self.provider.before_perform=check
        self.assertEqual(self.workflow.advance_for_testing().completed_stages,1)

    def test_lost_response_reopens_store_and_recovers_without_repeat(self):
        self.provider.fail='after'
        self.assertEqual(self.workflow.advance_for_testing().status,'uncertain')
        self.store.close();self.store=MetadataStore(self.path)
        fresh=MetadataWorkflow(self.store,self.request,self.provider)
        self.assertEqual(fresh.advance_for_testing().status,'uncertain')
        self.assertEqual(self.provider.calls,1)
        self.assertEqual(fresh.reconcile_for_testing().completed_stages,1)
        self.assertEqual(self.provider.calls,1)
        self.provider.fail=None
        self.assertEqual(fresh.advance_for_testing().completed_stages,2)

    def test_absent_receipt_stays_unknown_and_no_automatic_retry(self):
        self.provider.fail='before'
        self.assertEqual(self.workflow.advance_for_testing().status,'uncertain')
        for _ in range(3):
            self.assertEqual(self.workflow.reconcile_for_testing().status,'uncertain')
            self.assertEqual(self.workflow.advance_for_testing().status,'uncertain')
        self.assertEqual(self.provider.calls,1);self.assertEqual(self.model.receipts,[])

    def test_late_receipt_can_be_observed_only(self):
        self.provider.fail='before';self.workflow.advance_for_testing()
        command=self.model.command();self.model.apply(command,command['payload'])
        self.assertEqual(self.workflow.reconcile_for_testing().completed_stages,1)
        self.assertEqual(self.provider.calls,1)

    def test_terminal_rejection_never_retried(self):
        self.model.reject=True
        report=self.workflow.advance_for_testing()
        self.assertEqual(report.status,'manual_review');self.assertEqual(report.code,'terminal_not_applied_no_retry')
        for _ in range(2):self.workflow.advance_for_testing();self.workflow.reconcile_for_testing()
        self.assertEqual(self.provider.calls,1);self.assertEqual(self.model.cards,self.request.baseline)

    def test_missing_server_history_blocks_after_confirmed_progress(self):
        self.workflow.advance_for_testing()
        self.model.receipts=[];self.model.cards=self.request.baseline;self.model.revision=self.request.context.expected_draft_revision
        self.assertEqual(self.workflow.reconcile_for_testing().status,'blocked')
        self.assertEqual(self.workflow.advance_for_testing().status,'blocked');self.assertEqual(self.provider.calls,1)

    def test_wrong_server_receipt_or_extra_future_operation_rejected(self):
        self.provider.fail='after';self.workflow.advance_for_testing()
        self.model.receipts[0]['ordinal']=False
        self.assertEqual(self.workflow.reconcile_for_testing().status,'blocked')
        self.model.receipts[0]['ordinal']=0
        command=self.model.command();self.model.apply(command,command['payload'])
        self.assertEqual(self.workflow.reconcile_for_testing().status,'blocked')

    def test_journal_tables_are_immutable(self):
        self.workflow.advance_for_testing()
        for table in ('metadata_bindings','metadata_events'):
            with self.assertRaises(sqlite3.IntegrityError):self.store.conn.execute('DELETE FROM '+table)
            self.store.conn.rollback()

    def test_draft_cannot_be_rebound_to_changed_request(self):
        self.workflow.advance_for_testing()
        assets=self.request.assets;assets[0]['caption']='Changed'
        changed=replace(self.request,assets_json=_json(assets))
        model=Model(changed);provider=OfflineProvider(model)
        self.assertEqual(MetadataWorkflow(self.store,changed,provider).advance_for_testing().status,'blocked')
        self.assertEqual(provider.calls,0)

    def test_public_browser_facade_never_touches_driver(self):
        class Trap:
            def __getattribute__(self,name):raise AssertionError(name)
        report=HubMediaMetadataWriter(Trap()).transfer(object())
        self.assertEqual(report.status,'blocked')

    def test_no_cover_is_not_first_image_default(self):
        request=fixture_request(self.tmp.name,no_cover=True)
        model=Model(request);provider=OfflineProvider(model)
        other=MetadataStore(Path(self.tmp.name)/'other-private'/'journal.sqlite')
        try:
            workflow=MetadataWorkflow(other,request,provider)
            for _ in request.stages():report=workflow.advance_for_testing()
            self.assertEqual(report.status,'synthetic_complete');self.assertFalse(any(c['cover'] for c in model.cards))
        finally:other.close()

    def test_process_lock_excludes_other_coordinator(self):
        other=MetadataStore(self.path)
        try:
            with self.store.operation_lock():
                report=MetadataWorkflow(other,self.request,self.provider).advance_for_testing()
            self.assertEqual(report.status,'blocked');self.assertEqual(self.provider.calls,0)
        finally:other.close()

    def test_report_does_not_leak_caption_or_exception(self):
        self.provider.fail='before'
        report=self.workflow.advance_for_testing()
        data=_json(asdict(report))
        self.assertNotIn('Private synthetic failure',data)
        self.assertNotIn('First image',data);self.assertNotIn(self.tmp.name,data)


if __name__=='__main__':unittest.main()
