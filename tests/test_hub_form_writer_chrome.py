"""Opt-in real Chrome qualification against freshly served synthetic forms.

Set QUICK_RELIST_CHROME_TESTS=1 and QUICK_RELIST_CHROMEDRIVER to an existing
chromedriver executable. Optional QUICK_RELIST_CHROME_BINARY selects Chrome.
No automatic installation/driver download or existing user profile is used.
"""
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from quick_relist.hub_form_writer import (
    COMBO_NAMES, DATE_NAMES, FieldTransfer, HubDraftFormWriter, TransferRejected,
    _snapshot, bind_synthetic_fixture_for_tests,
)
from hub_form_fixture import fixture_document, fixture_request


@contextmanager
def serve(request,scenario='normal'):
    nonce=secrets.token_hex(32)
    path=f'/quick-relist-fixture/{nonce}/draft/{request.context.draft_id}/{request.context.page}'
    data=fixture_document(request,nonce,scenario).encode()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!=path:self.send_error(404);return
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}{path}',nonce
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


class HubFormChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('QUICK_RELIST_CHROME_TESTS')!='1':raise unittest.SkipTest('Real Chrome qualification not requested')
        driver_path=Path(os.environ.get('QUICK_RELIST_CHROMEDRIVER',''))
        if not driver_path.is_file():raise unittest.SkipTest('Existing chromedriver path required; no download attempted')
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        cls.profile=TemporaryDirectory(prefix='quick-relist-synthetic-')
        options=Options()
        options.add_argument('--headless=new')
        options.add_argument('--window-size=1400,1200')
        options.add_argument('--disable-background-networking')
        options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check')
        options.add_argument('--user-data-dir='+cls.profile.name)
        if os.environ.get('QUICK_RELIST_CHROME_BINARY'):options.binary_location=os.environ['QUICK_RELIST_CHROME_BINARY']
        cls.driver=webdriver.Chrome(service=Service(str(driver_path.resolve())),options=options)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        cls.profile.cleanup()

    def stats(self):
        return self.driver.execute_script('return JSON.parse(JSON.stringify(window.fixtureStats));')

    def run_case(self,request,scenario='normal',before=None):
        with serve(request,scenario) as (url,nonce):
            self.driver.get(url)
            writer=bind_synthetic_fixture_for_tests(self.driver,request,nonce=nonce)
            if before:before(writer)
            report=writer.transfer()
            return report,self.stats(),writer

    def test_all_handlers_emit_events_and_exact_readbacks(self):
        for page in ('details','media','deal_particulars'):
            with self.subTest(page=page):
                request=fixture_request(page)
                report,stats,writer=self.run_case(request)
                self.assertEqual(report.status,'supported_fields_verified',report)
                self.assertEqual(report.verified_fields,tuple(f.key for f in request.fields))
                self.assertEqual(report.saved,None)
                self.assertFalse(report.commit_action_clicked)
                self.assertFalse(report.persistence_verified)
                self.assertGreater(stats['fieldWrites'],0)
                self.assertGreater(stats['trustedEvents'],0)
                self.assertEqual(stats['saveClicks'],0)
                self.assertEqual(stats['calendarClicks'],0)
                for field in request.fields:self.assertEqual(writer._expected[field.key]['value'],field.source)
                if page=='media':
                    self.assertEqual(writer._expected['marketing_header']['kind'],'textarea')
                    self.assertIn('\n',writer._expected['marketing_header']['value'])
                    self.assertEqual(writer._expected['marketing_header']['value'],next(f.source for f in request.fields if f.key=='marketing_header'))
                if page=='details':
                    self.assertEqual(stats['menuOpens'],18)
                    self.assertFalse(writer._expected['address_hidden']['value']['checked'])
                    self.assertEqual(writer._expected['feature-bedroom-input']['value'],'03')
                    self.assertEqual(writer._expected['mandate_signed_date']['value']['parts'][0]['text'],'2001')
                    self.assertEqual(writer._expected['mandate_expiry']['value']['parts'][0]['text'],'2002')

    def test_duplicate_unknown_and_malformed_controls_reject_without_writes(self):
        for scenario in ('duplicate','missing','unknown','unknown_custom_control','bad_date_shape','bad_checkbox_label','bad_combo_sibling','secret_guard'):
            with self.subTest(scenario=scenario):
                request=fixture_request(keys=['street_name'])
                with serve(request,scenario) as (url,nonce):
                    self.driver.get(url)
                    with self.assertRaises(TransferRejected):bind_synthetic_fixture_for_tests(self.driver,request,nonce=nonce)
                    stats=self.stats()
                    self.assertEqual(stats['fieldWrites'],0)
                    self.assertEqual(stats['menuOpens'],0)
                    self.assertEqual(stats.get('secretReads',0),0)

    def test_disabled_controls_reject_whole_batch_before_writes(self):
        for scenario,keys in [('disabled',['street_number','street_name']),('disabled_combo',['street_name','office_id'])]:
            report,stats,_=self.run_case(fixture_request(keys=keys),scenario)
            self.assertEqual(report.code,'requested_control_disabled')
            self.assertEqual(stats['fieldWrites'],0)

    def test_bad_option_code_preflight_before_any_edits(self):
        request=fixture_request(keys=['street_name','property_type_id'])
        source=dict(request.fields[1].source,code='404')
        request=replace(request,fields=(request.fields[0],FieldTransfer.from_values('property_type_id',source,request.fields[1].baseline)))
        report,stats,_=self.run_case(request)
        self.assertEqual(report.code,'requested_option_unavailable')
        self.assertEqual(stats['fieldWrites'],0)
        self.assertEqual(report.attempted_fields,())

    def test_changed_options_not_clicked(self):
        report,stats,_=self.run_case(fixture_request(keys=['property_type_id']),'changed_options')
        self.assertEqual(report.code,'menu_options_changed')
        self.assertEqual(stats['fieldWrites'],0)

    def test_changed_baseline_context_and_window_before_edit(self):
        request=fixture_request(keys=['street_name'])
        for setup in (
            "document.querySelector('[name=street_name]').value='Changed independently'",
            "document.querySelector('main').setAttribute('data-context','{}')",
            "history.pushState({},'',location.pathname+'?changed')",
        ):
            report,stats,_=self.run_case(request,before=lambda _:self.driver.execute_script(setup))
            self.assertEqual(report.status,'blocked')
            self.assertEqual(stats['fieldWrites'],0)
        with serve(request) as (url,nonce):
            self.driver.get(url);writer=bind_synthetic_fixture_for_tests(self.driver,request,nonce=nonce)
            old=self.driver.current_window_handle
            self.driver.switch_to.new_window('tab')
            try:self.assertEqual(writer.transfer().code,'fixture_identity_changed')
            finally:self.driver.close();self.driver.switch_to.window(old)

    def test_interruption_after_first_clear_has_no_retry(self):
        request=fixture_request(keys=['street_name','street_number'])
        report,stats,writer=self.run_case(request,'interrupt')
        self.assertEqual(report.status,'partial_or_uncertain')
        self.assertEqual(report.uncertain_field,'street_name')
        self.assertTrue(report.form_state_uncertain)
        self.assertEqual(report.attempted_fields,('street_name',))
        self.assertEqual(writer.transfer().code,'fixture_transfer_already_attempted')
        self.assertEqual(self.stats(),stats)

    def test_dependent_field_reset_is_not_silently_accepted(self):
        report,stats,_=self.run_case(fixture_request(keys=['property_type_id']),'dependent_reset')
        self.assertEqual(report.code,'unrelated_field_changed')
        self.assertTrue(report.form_state_uncertain)
        self.assertEqual(report.edited_fields,())
        self.assertEqual(stats['fieldWrites'],1)

    def test_date_clamp_and_raw_mismatch_stop_without_renewal(self):
        request=fixture_request(keys=['mandate_expiry'])
        report,stats,_=self.run_case(request,'date_clamp')
        self.assertEqual(report.code,'date_part_readback_mismatch')
        self.assertEqual(report.verified_fields,())
        self.assertTrue(report.form_state_uncertain)
        source=dict(request.fields[0].source,raw='2002-05-23')
        request=replace(request,fields=(FieldTransfer.from_values('mandate_expiry',source,request.fields[0].baseline),))
        report,_,_=self.run_case(request)
        self.assertEqual(report.code,'field_readback_mismatch')

    def test_public_writer_refuses_even_on_valid_local_fixture(self):
        request=fixture_request(keys=['street_name'])
        with serve(request) as (url,_):
            self.driver.get(url)
            self.assertEqual(HubDraftFormWriter(self.driver).transfer(request).code,'live_provider_unqualified')
            self.assertEqual(self.stats()['fieldWrites'],0)


if __name__=='__main__':unittest.main()
