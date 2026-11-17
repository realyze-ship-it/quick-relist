"""Offline composition/journal faults. Native interaction proof is separate."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quick_relist.draft_pipeline import DraftPipeline, validate_pipeline_state
from quick_relist.hub_draft_pipeline import HubDraftPipeline
from quick_relist.hub_form_writer import _json, _digest
from quick_relist.media_metadata import MetadataStore
from draft_pipeline_fixture import fixture_request, Service, OfflineProvider


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.request=fixture_request(self.tmp.name);self.path=Path(self.tmp.name)/'private'/'pipeline.sqlite'
        self.store=MetadataStore(self.path);self.addCleanup(lambda:self.store.close())
        self.service=Service(self.request);self.provider=OfflineProvider(self.service)
        self.flow=DraftPipeline(self.store,self.request,self.provider)
    def finish(self):
        for i in range(self.request.count):
            report=self.flow.advance_for_testing()
            self.assertEqual(report.completed_stages,i+1,report)
        return report
    def test_integrated_fields_actual_bytes_metadata_and_final_comparison(self):
        report=self.finish();self.assertEqual(report.status,'synthetic_complete')
        self.assertEqual(self.service.fields,self.request.desired_fields)
        self.assertEqual(_json(self.service.cards),_json(self.service.metadata.request.desired))
        self.assertEqual(self.service.byte_posts,3);self.assertEqual(self.service.metadata_posts,5)
        self.assertEqual(len(self.service.receipts),8+1)
        self.assertFalse(report.live_supported);self.assertFalse(report.full_fidelity);self.assertIsNone(report.saved)
    def test_crash_after_every_stage_reconciles_without_replaying(self):
        for i in range(self.request.count):
            self.provider.fail_after=i
            self.assertEqual(self.flow.advance_for_testing().status,'uncertain')
            calls=len(self.provider.calls)
            self.store.close();self.store=MetadataStore(self.path);self.flow=DraftPipeline(self.store,self.request,self.provider)
            self.assertEqual(self.flow.advance_for_testing().status,'uncertain')
            self.assertEqual(self.flow.reconcile_for_testing().completed_stages,i+1)
            self.assertEqual(len(self.provider.calls),calls)
        self.assertEqual(self.service.byte_posts,3)
    def test_missing_field_receipt_never_retries(self):
        self.provider.fail_before=0;self.assertEqual(self.flow.advance_for_testing().status,'uncertain')
        for _ in range(3):
            self.assertEqual(self.flow.reconcile_for_testing().status,'uncertain')
            self.assertEqual(self.flow.advance_for_testing().status,'uncertain')
        self.assertEqual(len(self.provider.calls),1)
    def test_partial_upload_stays_uncertain_without_reselection(self):
        for _ in self.request.forms:self.flow.advance_for_testing()
        self.service.stop_upload_after=1
        self.assertEqual(self.flow.advance_for_testing().status,'uncertain')
        posts=self.service.byte_posts
        for _ in range(2):
            self.assertEqual(self.flow.reconcile_for_testing().status,'uncertain')
            self.assertEqual(self.flow.advance_for_testing().status,'uncertain')
        self.assertEqual(self.service.byte_posts,posts);self.assertEqual(len(self.service.uploads),1)
    def test_source_or_final_media_change_blocks(self):
        self.finish();self.service.cards[0]['caption']='Different saved caption'
        self.assertEqual(self.flow.reconcile_for_testing().status,'blocked')
    def test_changed_epoch_blocks_without_dispatch(self):
        self.flow.advance_for_testing();self.service.epoch='0'*64
        self.assertEqual(self.flow.advance_for_testing().status,'blocked');self.assertEqual(len(self.provider.calls),1)
    def test_fresh_local_store_cannot_accept_progressed_service(self):
        self.flow.advance_for_testing()
        other=MetadataStore(Path(self.tmp.name)/'other'/'pipeline.sqlite')
        try:self.assertEqual(DraftPipeline(other,self.request,self.provider).advance_for_testing().status,'blocked')
        finally:other.close()
        self.assertEqual(len(self.provider.calls),1)
    def test_public_entrypoints_do_not_access_provider(self):
        class Trap:
            def __getattribute__(self,name):raise AssertionError(name)
        self.assertEqual(HubDraftPipeline(Trap()).transfer(object()).status,'blocked')
        self.assertEqual(DraftPipeline(self.store,self.request,Trap()).execute().status,'blocked')
    def test_typed_history_rejects_boolean_ordinal(self):
        self.flow.advance_for_testing();self.service.receipts[0]['command']['ordinal']=False
        with self.assertRaises(ValueError):validate_pipeline_state(self.request,self.service.state())
    def test_global_address_projection_preserved_and_bound(self):
        address={'street_name':'Approved synthetic street'};forms=list(self.request.forms)
        forms[0]=replace(forms[0],context=replace(forms[0].context,approved_address_sha256=_digest(address)),approved_address_json=_json(address))
        request=replace(self.request,forms=tuple(forms),upload=replace(self.request.upload,
            context=replace(self.request.context,approved_address_sha256=_digest(address))),approved_address_json=_json(address))
        service=Service(request);provider=OfflineProvider(service)
        other=MetadataStore(Path(self.tmp.name)/'approved'/'pipeline.sqlite')
        try:
            flow=DraftPipeline(other,request,provider)
            for _ in range(request.count):report=flow.advance_for_testing()
            self.assertEqual(report.status,'synthetic_complete')
            self.assertEqual(service.fields['details']['street_name'],address['street_name'])
            self.assertNotEqual(request.forms[0].fields[0].source,address['street_name'])
        finally:other.close()

    def test_one_process_lock_covers_external_effect_and_metadata_child(self):
        for _ in range(len(self.request.forms)+1):self.flow.advance_for_testing()
        other=MetadataStore(self.path);competing=DraftPipeline(other,self.request,OfflineProvider(self.service))
        original=self.service.metadata_post;results=[]
        def held(body):
            receipt=original(body)
            results.append(competing.reconcile_for_testing().status)
            results.append(competing.advance_for_testing().status)
            child=OfflineProvider(self.service)._child(other)
            results.append(child.reconcile_for_testing().status)
            results.append(child.advance_for_testing().status)
            return receipt
        self.service.metadata_post=held
        try:
            self.assertEqual(self.flow.advance_for_testing().completed_stages,5)
            self.assertEqual(results,['blocked']*4)
            self.assertEqual(self.service.metadata_posts,1)
        finally:other.close()
    def test_changed_revision_or_fields_after_render_never_commits(self):
        for target in ('revision','fields'):
            with self.subTest(target=target):
                service=Service(self.request);provider=OfflineProvider(service)
                other=MetadataStore(Path(self.tmp.name)/('cas-'+target)/'pipeline.sqlite')
                flow=DraftPipeline(other,self.request,provider);original=provider.perform
                def conflict(command,store):
                    if target=='revision':service.revision='changed-after-render'
                    else:service.fields['details']['street_name']='Concurrent external change'
                    return original(command,store)
                provider.perform=conflict
                try:
                    self.assertEqual(flow.advance_for_testing().status,'uncertain')
                    self.assertEqual(service.receipts,[])
                finally:other.close()
    def test_upload_and_metadata_post_cannot_adopt_new_revision(self):
        for target in (3,4):
            with self.subTest(target=target):
                service=Service(self.request);provider=OfflineProvider(service)
                other=MetadataStore(Path(self.tmp.name)/('stage-cas-'+str(target))/'pipeline.sqlite')
                flow=DraftPipeline(other,self.request,provider)
                try:
                    for _ in range(target):flow.advance_for_testing()
                    command=self.request.command(target,service.revision);provider.preflight(command)
                    service.revision='changed-after-render'
                    if target==3:
                        with self.assertRaises(ValueError):service.upload_post('photo',Path(self.request.upload.files[0].local_path).read_bytes())
                    else:
                        inner=service.metadata.command()
                        with self.assertRaises(ValueError):service.metadata_post({'command':inner,'payload':inner['payload'],'events':[]})
                    self.assertEqual(len(service.receipts),target)
                finally:other.close()


    def test_unpublished_draft_precondition_is_explicit(self):
        self.service.draft_state='published'
        self.assertEqual(self.flow.advance_for_testing().status,'blocked')
        self.assertEqual(self.provider.calls,[])
        self.service.draft_state='draft';self.finish()
        self.service.draft_state='published'
        self.assertEqual(self.flow.reconcile_for_testing().status,'blocked')

if __name__=='__main__':unittest.main()
