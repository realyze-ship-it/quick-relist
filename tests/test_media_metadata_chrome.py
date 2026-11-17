"""Opt-in actual Chrome synthetic metadata/persistence/recovery tests only."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import time
import unittest

from quick_relist.hub_form_writer import _json, TransferRejected
from quick_relist.hub_media_metadata_writer import bind_synthetic_metadata_for_tests
from quick_relist.media_metadata import MetadataRequest, MetadataStore
from quick_relist.hub_media_writer import bind_synthetic_media_for_tests
from media_transfer_fixture import fixture_request as upload_request, serve as upload_serve
from media_metadata_fixture import fixture_request, serve


class MetadataChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('QUICK_RELIST_CHROME_TESTS')!='1':raise unittest.SkipTest('Real Chrome qualification not requested')
        path=Path(os.environ.get('QUICK_RELIST_CHROMEDRIVER',''))
        if not path.is_file():raise unittest.SkipTest('Existing chromedriver required; no download attempted')
        cls.driver_path=path.resolve();cls.driver,cls.profile=cls.new_browser()
    @classmethod
    def new_browser(cls):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        profile=TemporaryDirectory(prefix='quick-relist-metadata-synthetic-')
        options=Options();options.add_argument('--headless=new');options.add_argument('--window-size=1600,1600')
        options.add_argument('--disable-background-networking');options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check');options.add_argument('--user-data-dir='+profile.name)
        if os.environ.get('QUICK_RELIST_CHROME_BINARY'):options.binary_location=os.environ['QUICK_RELIST_CHROME_BINARY']
        try:return webdriver.Chrome(service=Service(str(cls.driver_path)),options=options),profile
        except Exception:profile.cleanup();raise
    @classmethod
    def tearDownClass(cls):cls.driver.quit();cls.profile.cleanup()
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.request=fixture_request(self.tmp.name)
        self.path=Path(self.tmp.name)/'private'/'metadata.sqlite'
        self.store=MetadataStore(self.path);self.addCleanup(lambda:self.store.close())
    def bind(self,url,nonce,driver=None):
        driver=driver or self.driver;driver.get(url)
        return bind_synthetic_metadata_for_tests(driver,self.request,self.store,nonce=nonce)
    def wait_stage(self,workflow,count):
        end=time.monotonic()+6;report=None
        while time.monotonic()<end:
            report=workflow.reconcile_for_testing()
            if report.completed_stages>=count or report.status=='manual_review':return report
            time.sleep(.05)
        self.fail('Modeled metadata stage not observed: '+repr(report))
    def test_native_caption_cover_keyboard_order_exact_persistence(self):
        with serve(self.request) as (url,nonce,model):
            workflow=self.bind(url,nonce)
            for index,_ in enumerate(self.request.stages()):
                workflow.advance_for_testing();report=self.wait_stage(workflow,index+1)
            self.assertEqual(report.status,'synthetic_complete')
            self.assertEqual(_json(model.cards),_json(self.request.desired));self.assertEqual(model.posts,5)
            self.driver.refresh();fresh=bind_synthetic_metadata_for_tests(self.driver,self.request,self.store,nonce=nonce)
            self.assertEqual(fresh.reconcile_for_testing().status,'synthetic_complete')
            self.assertEqual(model.posts,5)
            self.assertTrue(any(e=={'type':'keydown','trusted':True} for e in model.trusted_events))
            self.assertTrue(any(e=={'type':'click','trusted':True} for e in model.trusted_events))
            self.assertFalse(report.hub_persistence_verified);self.assertFalse(report.full_fidelity);self.assertIsNone(report.saved)
    def test_server_hashed_upload_receipts_bind_metadata_fixture(self):
        upload=upload_request(self.tmp.name)
        upload=replace(upload,files=tuple(replace(f,metadata_json=_json({'caption':a['caption'],'cover':a['cover']}))
                                         for f,a in zip(upload.files,self.request.assets)))
        with upload_serve(upload) as (url,nonce,ledger):
            self.driver.get(url);writer=bind_synthetic_media_for_tests(self.driver,upload,nonce=nonce)
            writer.transfer();end=time.monotonic()+5
            while time.monotonic()<end:
                report=writer.observe()
                if report.status=='synthetic_receipts_observed':break
                time.sleep(.05)
            self.assertEqual(report.status,'synthetic_receipts_observed');self.assertEqual(len(ledger),3)
            # An independently initialized synthetic metadata revision is needed;
            # the upload receipts have no revision/persistence capability.
            self.request=MetadataRequest.from_transfer(upload,self.request.context,list(ledger),self.request.baseline)
        with serve(self.request) as (url,nonce,model):
            workflow=self.bind(url,nonce)
            for index,_ in enumerate(self.request.stages()):workflow.advance_for_testing();self.wait_stage(workflow,index+1)
            self.assertEqual(_json(model.cards),_json(self.request.desired));self.assertEqual(model.posts,5)

    def test_browser_and_store_restart_recovers_lost_response_without_write(self):
        with serve(self.request,scenario='no-reload') as (url,nonce,model):
            initial,initial_profile=self.new_browser()
            try:
                workflow=self.bind(url,nonce,initial);workflow.advance_for_testing()
                end=time.monotonic()+3
                while not model.receipts and time.monotonic()<end:time.sleep(.05)
                self.assertEqual(len(model.receipts),1)
            finally:initial.quit();initial_profile.cleanup()
            self.store.close();self.store=MetadataStore(self.path)
            driver,profile=self.new_browser()
            try:
                fresh=self.bind(url,nonce,driver)
                self.assertEqual(fresh.advance_for_testing().status,'uncertain')
                self.assertEqual(fresh.reconcile_for_testing().completed_stages,1)
                self.assertEqual(model.posts,1)
            finally:driver.quit();profile.cleanup()
    def test_delayed_receipt_recovery_never_repeats_click(self):
        with serve(self.request,delay=.6,scenario='no-reload') as (url,nonce,model):
            workflow=self.bind(url,nonce)
            self.assertEqual(workflow.advance_for_testing().status,'uncertain')
            self.assertEqual(workflow.advance_for_testing().status,'uncertain')
            end=time.monotonic()+3
            while not model.receipts and time.monotonic()<end:time.sleep(.05)
            self.driver.refresh()
            self.wait_stage(workflow,1)
            self.assertEqual(model.posts,1)
    def test_terminal_rejected_action_never_retried(self):
        with serve(self.request,reject=True) as (url,nonce,model):
            workflow=self.bind(url,nonce);workflow.advance_for_testing();report=self.wait_stage(workflow,1)
            self.assertEqual(report.status,'manual_review')
            workflow.advance_for_testing();workflow.reconcile_for_testing();self.assertEqual(model.posts,1)
    def test_native_no_cover_preserved(self):
        self.request=fixture_request(self.tmp.name,no_cover=True)
        with serve(self.request) as (url,nonce,model):
            workflow=self.bind(url,nonce)
            for index,_ in enumerate(self.request.stages()):workflow.advance_for_testing();self.wait_stage(workflow,index+1)
            self.assertFalse(any(c['cover'] for c in model.cards));self.assertEqual(model.posts,5)
    def test_structural_and_context_errors_stop_before_value_getters(self):
        scripts=(
            "const e=document.createElement('input');root.append(e);",
            "const f=document.createElement('fieldset');f.disabled=true;caption.parentNode.insertBefore(f,caption);f.append(caption);",
            "caption.name='unexpected';",
            "caption.setAttribute('role','textbox');",
            "caption.parentElement.hidden=true;",
            "root.dataset.context='{}';",
            "const f=document.createElement('fieldset');f.disabled=true;const b=root.querySelector('button');b.parentNode.insertBefore(f,b);f.append(b);",
        )
        for scenario in scripts:
            with self.subTest(scenario=scenario),serve(self.request) as (url,nonce,model):
                self.driver.get(url)
                self.driver.execute_script("const root=document.getElementById('synthetic-metadata'),caption=root.querySelector('textarea');"+scenario+"window.readTraps=0;Object.defineProperty(caption,'value',{get(){window.readTraps++;throw Error('private value read');}});for(const span of root.querySelectorAll('[data-saved-caption]'))Object.defineProperty(span,'textContent',{get(){window.readTraps++;throw Error('private caption read');}});")
                with self.assertRaises(TransferRejected):bind_synthetic_metadata_for_tests(self.driver,self.request,self.store,nonce=nonce)
                self.assertEqual(self.driver.execute_script('return window.readTraps;'),0);self.assertEqual(model.posts,0)
    def test_new_server_epoch_cannot_resume_pending_journal(self):
        with serve(self.request,scenario='no-reload') as (url,nonce,model):
            workflow=self.bind(url,nonce);workflow.advance_for_testing()
        with serve(self.request) as (url,nonce,model):
            fresh=self.bind(url,nonce)
            self.assertEqual(fresh.reconcile_for_testing().status,'blocked')
            self.assertEqual(fresh.advance_for_testing().status,'blocked');self.assertEqual(model.posts,0)


if __name__=='__main__':unittest.main()
