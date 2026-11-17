"""Opt-in actual Chrome composition on one synthetic service. Never visits Hub."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import time
import unittest

from quick_relist.draft_pipeline import DraftPipelineRequest
from quick_relist.hub_draft_pipeline import bind_synthetic_pipeline_for_tests
from quick_relist.hub_form_writer import _json, _digest
from quick_relist.media_metadata import MetadataStore
from draft_pipeline_fixture import fixture_request, serve


class PipelineChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('QUICK_RELIST_CHROME_TESTS')!='1':raise unittest.SkipTest('Real Chrome qualification not requested')
        path=Path(os.environ.get('QUICK_RELIST_CHROMEDRIVER',''))
        if not path.is_file():raise unittest.SkipTest('Existing ChromeDriver required')
        cls.path=path.resolve();cls.driver,cls.profile=cls.browser()
    @classmethod
    def browser(cls):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        profile=TemporaryDirectory(prefix='quick-relist-pipeline-synthetic-')
        options=Options();options.add_argument('--headless=new');options.add_argument('--window-size=1600,1600')
        options.add_argument('--disable-background-networking');options.add_argument('--no-first-run');options.add_argument('--no-default-browser-check')
        options.add_argument('--user-data-dir='+profile.name)
        if os.environ.get('QUICK_RELIST_CHROME_BINARY'):options.binary_location=os.environ['QUICK_RELIST_CHROME_BINARY']
        try:return webdriver.Chrome(service=Service(str(cls.path)),options=options),profile
        except Exception:profile.cleanup();raise
    @classmethod
    def tearDownClass(cls):cls.driver.quit();cls.profile.cleanup()
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.request=fixture_request(self.tmp.name)
        self.db=Path(self.tmp.name)/'private'/'pipeline.sqlite';self.store=MetadataStore(self.db);self.addCleanup(lambda:self.store.close())
    def bind(self,origin,service,driver=None):
        driver=driver or self.driver;driver.get(origin+service.state_path)
        return bind_synthetic_pipeline_for_tests(driver,self.request,self.store,origin=origin,epoch=service.epoch)
    def wait(self,flow,count):
        end=time.monotonic()+5
        while time.monotonic()<end:
            report=flow.reconcile_for_testing()
            if report.completed_stages==count:return report
            time.sleep(.05)
        self.fail('Expected pipeline stage not observed: '+repr(report))
    def finish(self,flow,start=0):
        for i in range(start,self.request.count):
            flow.advance_for_testing();report=self.wait(flow,i+1)
        return report
    def test_one_service_native_fields_bytes_metadata_and_saved_comparison(self):
        with serve(self.request) as (origin,service):
            flow=self.bind(origin,service);report=self.finish(flow)
            self.assertEqual(report.status,'synthetic_complete')
            self.assertEqual(_json(service.fields),_json(self.request.desired_fields))
            self.assertEqual(_json(service.cards),_json(service.metadata.request.desired))
            self.assertEqual(service.byte_posts,3);self.assertEqual(service.metadata_posts,5)
            self.assertTrue(all(service.trusted));self.assertEqual(len(service.trusted),3)
            self.assertTrue(any(e['trusted'] and e['type']=='keydown' for e in service.metadata.trusted_events))
            self.assertFalse(report.full_fidelity);self.assertFalse(report.hub_persistence_verified)
    def test_exact_approved_address_projection_reaches_same_saved_draft(self):
        address={'street_name':'Approved exact synthetic street'};forms=list(self.request.forms)
        forms[0]=replace(forms[0],context=replace(forms[0].context,approved_address_sha256=_digest(address)),approved_address_json=_json(address))
        self.request=replace(self.request,forms=tuple(forms),upload=replace(self.request.upload,
            context=replace(self.request.context,approved_address_sha256=_digest(address))),approved_address_json=_json(address))
        with serve(self.request) as (origin,service):
            flow=self.bind(origin,service);self.finish(flow)
            self.assertEqual(service.fields['details']['street_name'],address['street_name'])
    def test_full_browser_and_journal_restart_after_upload_never_reuploads(self):
        with serve(self.request) as (origin,service):
            first,profile=self.browser()
            try:
                flow=self.bind(origin,service,first)
                for i in range(len(self.request.forms)+1):flow.advance_for_testing();self.wait(flow,i+1)
            finally:first.quit();profile.cleanup()
            self.store.close();self.store=MetadataStore(self.db)
            second,profile=self.browser()
            try:
                flow=self.bind(origin,service,second)
                before=(service.field_posts,service.byte_posts)
                self.assertEqual(flow.reconcile_for_testing().completed_stages,4)
                self.finish(flow,start=4)
                self.assertEqual((service.field_posts,service.byte_posts),before)
            finally:second.quit();profile.cleanup()
    def test_partial_upload_reopen_remains_uncertain_without_reselection(self):
        with serve(self.request,stop_upload_after=1) as (origin,service):
            flow=self.bind(origin,service)
            for i in range(len(self.request.forms)):flow.advance_for_testing();self.wait(flow,i+1)
            self.assertEqual(flow.advance_for_testing().status,'uncertain');posts=service.byte_posts
            self.store.close();self.store=MetadataStore(self.db)
            flow=self.bind(origin,service)
            self.assertEqual(flow.reconcile_for_testing().status,'uncertain')
            self.assertEqual(flow.advance_for_testing().status,'uncertain');self.assertEqual(service.byte_posts,posts)
    def test_lost_field_response_is_reconciled_without_retyping(self):
        with serve(self.request,drop_response_stage=0) as (origin,service):
            flow=self.bind(origin,service);flow.advance_for_testing();self.wait(flow,1)
            posts=service.field_posts
            self.store.close();self.store=MetadataStore(self.db)
            flow=self.bind(origin,service);self.assertEqual(flow.reconcile_for_testing().completed_stages,1)
            self.assertEqual(service.field_posts,posts)
    def test_final_saved_tamper_never_returns_complete(self):
        with serve(self.request) as (origin,service):
            flow=self.bind(origin,service);self.finish(flow)
            with service.lock:service.fields['details']['street_name']='Changed externally'
            self.assertEqual(flow.reconcile_for_testing().status,'blocked')

    def test_changed_service_revision_after_native_preflight_cannot_commit(self):
        with serve(self.request) as (origin,service):
            flow=self.bind(origin,service);original=flow.provider.preflight
            def conflict(command):
                original(command)
                with service.lock:service.revision='external-revision-after-render'
            flow.provider.preflight=conflict
            self.assertEqual(flow.advance_for_testing().status,'uncertain')
            self.assertEqual(service.receipts,[])
            self.assertEqual(service.fields,self.request.initial_fields)

    def test_delayed_responses_are_observed_before_handler_navigation(self):
        with serve(self.request,response_delay=.25) as (origin,service):
            flow=self.bind(origin,service)
            for index in range(self.request.count):
                report=flow.advance_for_testing()
                self.assertEqual(report.completed_stages,index+1,report)
            self.assertEqual(report.status,'synthetic_complete')
            self.assertEqual(service.byte_posts,3);self.assertEqual(service.metadata_posts,5)


if __name__=='__main__':unittest.main()
