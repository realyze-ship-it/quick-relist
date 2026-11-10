"""stdlib orchestration tests. Fakes are not browser/React qualification."""
from copy import deepcopy
from dataclasses import asdict, replace
import secrets
import unittest

from quick_relist.hub_form_writer import (
    CHECKBOX_NAMES, COMBO_NAMES, DATE_NAMES, FORM_STATE_SCRIPT, MENU_STATE_SCRIPT,
    CONTROL, TAB, ESCAPE, FieldTransfer, FormTransferRequest, HubDraftFormWriter,
    TransferRejected, _digest, _json, _kind, bind_synthetic_fixture_for_tests,
)
from hub_form_fixture import date_value, fixture_request
from segmented_date_fixtures import date_record


class Element:
    def __init__(self, driver, key, index=None, option=None):
        self.driver, self.key, self.index, self.option = driver, key, index, option

    def __deepcopy__(self, memo):
        return self

    def clear(self):
        self.driver.event('clear', self.key)
        self.driver.state['fields'][self.key]['value'] = ''

    def send_keys(self, *parts):
        d = self.driver
        d.event('keys', self.key)
        if parts == (ESCAPE,):
            d.menu = None
            d.state['overlays'] = []
        elif CONTROL in parts:
            value = d.state['fields'][self.key]['value']
            values = [int(p['aria_valuenow']) for p in value['parts']]
            values[self.index] = int(parts[-2])
            if d.clamp and self.index == 2: values[2] = 20
            d.state['fields'][self.key]['value'] = date_value(*values)
            d.update_date(self.key)
        elif parts != (TAB,):
            d.state['fields'][self.key]['value'] = ''.join(parts)

    def click(self):
        d, key = self.driver, self.key
        d.event('click', key)
        f = d.state['fields'][key]
        if self.option:
            f['value'] = {k:self.option[k] for k in ('code','text')}
            d.menu = None
            d.state['overlays'] = []
        elif f['kind'] == 'combo':
            d.menu = key
            d.state['overlays'] = [{'id':'synthetic-menu','role':'listbox','tag':'UL'}]
        elif f['kind'] == 'checkbox':
            f['value']['checked'] = not f['value']['checked']
        elif f['kind'] == 'radio':
            for i,v in enumerate(f['value']): v['checked'] = i == self.index


class Driver:
    capabilities = {'browserName':'chrome'}
    current_window_handle = 'synthetic-window'
    window_handles = ['synthetic-window']

    def __init__(self, request):
        self.nonce = secrets.token_hex(32)
        self.current_url = f'http://127.0.0.1:18413/quick-relist-fixture/{self.nonce}/draft/{request.context.draft_id}/{request.context.page}'
        self.calls, self.events, self.menu, self.clamp = [], [], None, False
        self.interrupt_at = None
        self.on_event = None
        self.switch_to = self
        self.active_element = Element(self, 'focus')
        self.state = {'href':self.current_url,'ready':True,'nonce':self.nonce,'synthetic':'true',
                      'context':_json(asdict(request.context)),'fields':{},'errors':[],'overlays':[]}
        self.options = {}
        for field in request.fields:
            key, kind = field.key, _kind(field.key,request.context.page)
            f = {'kind':kind,'value':field.baseline,'element':Element(self,key),
                 'shape':{'tag':'DIV' if kind=='combo' else 'INPUT','id':key,'name':key,'type':'text','disabled':False,'readonly':False}}
            if kind in ('date','radio'):
                f['elements'] = [Element(self,key,i) for i in range(3 if kind=='date' else 5)]
            self.state['fields'][key] = f
            if kind == 'date': self.update_date(key)
            if kind == 'combo':
                self.options[key] = [dict(field.baseline,selected=True,disabled=False),dict(field.source,selected=False,disabled=False)]

    def update_date(self,key):
        f = self.state['fields'][key]
        record = date_record(DATE_NAMES.index(key),f['value']['raw'])
        for part,values in zip(record['date_picker']['segments'],f['value']['parts']):part.update(values)
        f['date_record'] = record

    def event(self,kind,key):
        self.events.append((kind,key))
        if self.on_event: self.on_event(kind,key)
        if self.interrupt_at == len(self.events): raise RuntimeError('private browser payload must not leak')

    def execute_script(self,script,*args):
        self.calls.append(script)
        if script == FORM_STATE_SCRIPT: return deepcopy(self.state)
        if script == MENU_STATE_SCRIPT:
            if self.menu != args[0]: return None
            options = deepcopy(self.options[self.menu])
            for option in options: option['element'] = Element(self,self.menu,option=option)
            return {'id':'synthetic-menu','options':options}
        raise AssertionError('Unexpected script')


def bound(request):
    d = Driver(request)
    return d,bind_synthetic_fixture_for_tests(d,request,nonce=d.nonce)


class HubFormWriterTests(unittest.TestCase):
    def test_public_writer_never_reads_driver(self):
        class Forbidden:
            def __getattribute__(self,key): raise AssertionError('Driver accessed')
        report = HubDraftFormWriter(Forbidden()).transfer(fixture_request(keys=['street_name']))
        self.assertEqual(report.code,'live_provider_unqualified')
        self.assertFalse(report.live_qualified)

    def test_hub_and_nonlocal_urls_rejected_before_dom(self):
        for url in ('https://hub.remax.co.za/app/listings/edit/222222','http://127.0.0.1.evil.test:8888/','http://localhost:80/'):
            request = fixture_request(keys=['street_name']); d=Driver(request);d.current_url=url
            with self.assertRaises(TransferRejected): bind_synthetic_fixture_for_tests(d,request,nonce=d.nonce)
            self.assertEqual(d.calls,[]);self.assertEqual(d.events,[])

    def test_unknown_field_and_special_keys_rejected(self):
        r=fixture_request(keys=['street_name'])
        for key,value in [('suburb','Synthetic suburb'),('rich_text_description','Text'),('mandate_expiry','renew today'),('street_name','Bad\ue007')]:
            with self.assertRaises(TransferRejected): replace(r,fields=(FieldTransfer.from_values(key,value,value),))

    def test_exact_text_counts_false_checkbox_and_radio(self):
        r=fixture_request(keys=['street_name','feature-bedroom-input','address_hidden','sale_state_id'])
        d,w=bound(r);report=w.transfer()
        self.assertEqual(report.status,'supported_fields_verified',report)
        self.assertFalse(report.saved);self.assertFalse(report.complete_recreation)
        for field in r.fields:self.assertEqual(d.state['fields'][field.key]['value'],field.source)
        self.assertFalse(d.state['fields']['address_hidden']['value']['checked'])
        self.assertEqual(d.state['fields']['feature-bedroom-input']['value'],'03')

    def test_combo_exact_codes_and_all_nine_handlers(self):
        r=fixture_request(keys=list(COMBO_NAMES));d,w=bound(r);report=w.transfer()
        self.assertEqual(report.status,'supported_fields_verified',report)
        for f in r.fields:self.assertEqual(d.state['fields'][f.key]['value'],f.source)
        self.assertEqual(len([e for e in d.events if e[0]=='click']),27)

    def test_bad_combo_option_preflight_before_text_edits(self):
        r=fixture_request(keys=['street_name','property_type_id']);d,w=bound(r)
        d.options['property_type_id'][1]['code']='missing'
        report=w.transfer()
        self.assertEqual(report.code,'requested_option_unavailable')
        self.assertEqual(report.attempted_fields,())
        self.assertEqual(d.state['fields']['street_name']['value'],r.fields[0].baseline)

    def test_duplicate_option_and_changed_menu_fail_closed(self):
        r=fixture_request(keys=['property_type_id']);d,w=bound(r)
        d.options['property_type_id'][1]['code']='1'
        report=w.transfer();self.assertEqual(report.code,'menu_options_ambiguous')
        self.assertTrue(report.form_state_uncertain)
        r=fixture_request(keys=['property_type_id']);d,w=bound(r)
        opens=0
        def change(kind,key):
            nonlocal opens
            if kind=='click' and key=='property_type_id':
                opens+=1
                if opens==2:d.options[key][1]['text']='Changed'
        d.on_event=change
        report=w.transfer();self.assertEqual(report.code,'menu_options_changed')
        self.assertEqual(report.edited_fields,())

    def test_whole_batch_missing_disabled_or_structural_rejection_before_edits(self):
        for change in ('missing','disabled','duplicate'):
            r=fixture_request(keys=['street_name','street_number']);d,w=bound(r)
            if change=='missing':del d.state['fields']['street_number']
            elif change=='disabled':d.state['fields']['street_number']['shape']['disabled']=True
            else:d.state['errors']=['duplicate_control']
            report=w.transfer();self.assertEqual(report.status,'blocked');self.assertEqual(d.events,[])
        r=fixture_request(keys=['street_name','office_id']);d=Driver(r)
        d.state['fields']['office_id']['shape']['disabled']=True
        w=bind_synthetic_fixture_for_tests(d,r,nonce=d.nonce)
        self.assertEqual(w.transfer().code,'requested_control_disabled');self.assertEqual(d.events,[])

    def test_baseline_context_window_and_route_changes_block(self):
        for change in ('baseline','context','window','route','node'):
            r=fixture_request(keys=['street_name']);d,w=bound(r)
            if change=='baseline':d.state['fields']['street_name']['value']='Changed'
            elif change=='context':d.state['context']='{}'
            elif change=='window':d.current_window_handle='different'
            elif change=='node':d.state['fields']['street_name']['element']=Element(d,'street_name')
            else:d.current_url+='?changed'
            self.assertEqual(w.transfer().status,'blocked');self.assertEqual(d.events,[])

    def test_mid_run_interruption_retains_partial_report_and_never_retries(self):
        r=fixture_request(keys=['street_name','street_number']);d,w=bound(r);d.interrupt_at=4
        report=w.transfer()
        self.assertEqual(report.status,'partial_or_uncertain')
        self.assertEqual(report.edited_fields,('street_name',))
        self.assertEqual(report.uncertain_field,'street_number')
        self.assertNotIn('private',repr(report))
        n=len(d.events);self.assertEqual(w.transfer().code,'fixture_transfer_already_attempted');self.assertEqual(len(d.events),n)

    def test_unrelated_change_stops_without_rollback(self):
        r=fixture_request(keys=['street_name','street_number']);d,w=bound(r)
        d.on_event=lambda *_:d.state['fields']['street_number'].update(value='Changed independently')
        report=w.transfer();self.assertEqual(report.code,'unrelated_field_changed');self.assertEqual(report.attempted_fields,('street_name',))
        self.assertEqual(d.state['fields']['street_name']['value'],'')

    def test_all_five_dates_exact_and_mandate_years_not_renewed(self):
        r=fixture_request(keys=list(DATE_NAMES));d,w=bound(r);report=w.transfer()
        self.assertEqual(report.status,'supported_fields_verified',report)
        for f in r.fields:self.assertEqual(d.state['fields'][f.key]['value'],f.source)
        self.assertEqual(d.state['fields']['mandate_signed_date']['value']['parts'][0]['text'],'2001')
        self.assertEqual(d.state['fields']['mandate_expiry']['value']['parts'][0]['text'],'2002')

    def test_date_clamp_and_raw_format_mismatch_are_uncertain(self):
        r=fixture_request(keys=['mandate_expiry']);d,w=bound(r);d.clamp=True
        report=w.transfer();self.assertEqual(report.code,'date_part_readback_mismatch');self.assertTrue(report.form_state_uncertain)
        source=r.fields[0].source;source['raw']='different format'
        r=replace(r,fields=(FieldTransfer.from_values('mandate_expiry',source,r.fields[0].baseline),));d,w=bound(r)
        report=w.transfer();self.assertEqual(report.code,'field_readback_mismatch')

    def test_exact_approved_address_override_is_bound_and_other_fields_keep_source(self):
        r=fixture_request(keys=['street_name','selling_price'])
        address={'street_name':'  Explicit synthetic correction  '}
        r=replace(r,context=replace(r.context,approved_address_sha256=_digest(address)),approved_address_json=_json(address))
        d,w=bound(r)
        self.assertEqual(w.transfer().status,'supported_fields_verified')
        self.assertEqual(d.state['fields']['street_name']['value'],address['street_name'])
        self.assertEqual(d.state['fields']['selling_price']['value'],r.fields[1].source)
        with self.assertRaises(TransferRejected):replace(r,approved_address_json=_json({'street_name':'Unbound'}))

    def test_source_dates_cannot_be_address_overrides(self):
        r=fixture_request(keys=['mandate_expiry'])
        with self.assertRaises(TransferRejected):replace(r,approved_address_json=_json({'mandate_expiry':'today'}))

    def test_textareas_exact_newlines(self):
        r=fixture_request('media',keys=['marketing_header','brochure_description']);d,w=bound(r)
        self.assertEqual(w.transfer().status,'supported_fields_verified')
        self.assertEqual(d.state['fields']['marketing_header']['kind'],'textarea')
        self.assertIn('\n',r.fields[0].source)
        self.assertEqual(d.state['fields']['marketing_header']['value'],r.fields[0].source)
        self.assertEqual(d.state['fields']['brochure_description']['value'],r.fields[1].source)

    def test_marketing_header_line_endings_are_not_normalized_in_request(self):
        r=fixture_request('media',keys=['marketing_header'])
        raw='  Synthetic header\r\nSecond line\rFinal line\n'
        r=replace(r,fields=(FieldTransfer.from_values('marketing_header',raw,r.fields[0].baseline),))
        self.assertEqual(r.fields[0].source,raw)
        self.assertEqual(r.desired(r.fields[0]),raw)

    def test_fresh_nonce_is_one_use(self):
        r=fixture_request(keys=['street_name']);d,w=bound(r)
        with self.assertRaisesRegex(TransferRejected,'nonce_already_used'):bind_synthetic_fixture_for_tests(d,r,nonce=d.nonce)


if __name__=='__main__':unittest.main()
