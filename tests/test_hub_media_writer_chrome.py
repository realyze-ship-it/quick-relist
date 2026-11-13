"""Opt-in actual Selenium file transfer; never navigates to Hub."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from dataclasses import asdict
from quick_relist.hub_form_writer import TransferRejected, _json
from quick_relist.hub_media_writer import bind_synthetic_media_for_tests, FILE_BYTES_SCRIPT
from media_transfer_fixture import fixture_request, serve


class MediaChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('QUICK_RELIST_CHROME_TESTS')!='1':raise unittest.SkipTest('Real Chrome qualification not requested')
        path=Path(os.environ.get('QUICK_RELIST_CHROMEDRIVER',''))
        if not path.is_file():raise unittest.SkipTest('Existing chromedriver required; no download attempted')
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        cls.profile=TemporaryDirectory(prefix='quick-relist-media-synthetic-')
        options=Options();options.add_argument('--headless=new');options.add_argument('--window-size=1400,1200')
        options.add_argument('--disable-background-networking');options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check');options.add_argument('--user-data-dir='+cls.profile.name)
        if os.environ.get('QUICK_RELIST_CHROME_BINARY'):options.binary_location=os.environ['QUICK_RELIST_CHROME_BINARY']
        cls.driver=webdriver.Chrome(service=Service(str(path.resolve())),options=options)
    @classmethod
    def tearDownClass(cls):
        cls.driver.quit();cls.profile.cleanup()
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.request=fixture_request(self.tmp.name)
    def wait_receipts(self,writer):
        end=time.monotonic()+5
        while time.monotonic()<end:
            report=writer.observe()
            if report.status=='synthetic_receipts_observed':return report
            time.sleep(.05)
        return report
    def test_real_selection_posts_exact_bytes_to_server(self):
        with serve(self.request) as (url,nonce,ledger):
            self.driver.get(url);writer=bind_synthetic_media_for_tests(self.driver,self.request,nonce=nonce)
            first=writer.transfer();report=self.wait_receipts(writer)
            self.assertEqual(report.status,'synthetic_receipts_observed',report)
            self.assertEqual(len(ledger),3);self.assertEqual(set(report.modeled_receipt_ids),{f.media_id for f in self.request.files})
            stats=self.driver.execute_script('return window.fixtureStats;')
            self.assertEqual(stats,{'changes':2,'trusted':2,'posts':3})
            self.assertTrue(first.upload_state_uncertain);self.assertFalse(report.saved);self.assertFalse(report.full_fidelity)
            writer.transfer();self.assertEqual(self.driver.execute_script('return window.fixtureStats.posts;'),3)
    def test_delayed_receipts_readonly_recovery(self):
        with serve(self.request,delay=.5) as (url,nonce,ledger):
            self.driver.get(url);writer=bind_synthetic_media_for_tests(self.driver,self.request,nonce=nonce)
            self.assertEqual(writer.transfer().status,'partial_or_uncertain')
            self.assertEqual(self.wait_receipts(writer).status,'synthetic_receipts_observed')
            self.assertEqual(len(ledger),3)
    def test_disabled_ambiguous_unknown_wrong_context_no_upload(self):
        for scenario in ('disabled','ambiguous','unknown','wrong-context','fallback-role','aria-widget','disabled-fieldset'):
            with self.subTest(scenario=scenario),serve(self.request,scenario) as (url,nonce,ledger):
                self.driver.get(url)
                with self.assertRaises(TransferRejected):bind_synthetic_media_for_tests(self.driver,self.request,nonce=nonce)
                self.assertEqual(ledger,[])
    def test_server_consumption_without_dom_receipt_never_claims_success(self):
        with serve(self.request,'no-receipt') as (url,nonce,ledger):
            self.driver.get(url);writer=bind_synthetic_media_for_tests(self.driver,self.request,nonce=nonce)
            writer.transfer();time.sleep(.5);report=writer.observe()
            self.assertEqual(report.code,'modeled_receipts_pending');self.assertTrue(report.upload_state_uncertain)
            self.assertEqual(len(ledger),3)
    def test_wrong_receipt_rejected(self):
        with serve(self.request,'wrong-receipt') as (url,nonce,ledger):
            self.driver.get(url);writer=bind_synthetic_media_for_tests(self.driver,self.request,nonce=nonce)
            writer.transfer();time.sleep(.3)
            self.assertEqual(writer.observe().code,'modeled_receipt_mismatch')
    def test_async_byte_reader_checks_context_and_metadata_before_bytes(self):
        with serve(self.request) as (url,nonce,ledger):
            self.driver.get(url);writer=bind_synthetic_media_for_tests(self.driver,self.request,nonce=nonce)
            writer.transfer();self.wait_receipts(writer)
            state=writer._read();element=state['elements']['photo']
            binding={'href':url,'nonce':nonce,'context':_json(asdict(self.request.context)), 'request':self.request.fingerprint}
            # Fixture-only adversarial trap, never a writer-side state mutation.
            self.driver.execute_script("window.testByteReads=0;File.prototype.arrayBuffer=function(){window.testByteReads++;throw Error('unexpected byte read');};")
            metadata=[dict(x) for x in state['files']['photo']];metadata[0]['size']=2**40
            result=self.driver.execute_async_script(FILE_BYTES_SCRIPT,element,binding,metadata)
            self.assertFalse(result['ok']);self.assertEqual(self.driver.execute_script('return window.testByteReads;'),0)
            self.driver.execute_script("document.getElementById('synthetic-media').dataset.context='{}';")
            result=self.driver.execute_async_script(FILE_BYTES_SCRIPT,element,binding,state['files']['photo'])
            self.assertFalse(result['ok']);self.assertEqual(self.driver.execute_script('return window.testByteReads;'),0)
    def test_file_change_after_binding_blocks_before_selection(self):
        with serve(self.request) as (url,nonce,ledger):
            self.driver.get(url);writer=bind_synthetic_media_for_tests(self.driver,self.request,nonce=nonce)
            Path(self.request.files[-1].local_path).write_bytes(b'changed')
            self.assertEqual(writer.transfer().status,'blocked');self.assertEqual(ledger,[])


if __name__=='__main__':unittest.main()
