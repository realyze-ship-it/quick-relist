"""Independent safety regressions. Fakes/Node do not qualify a live browser.

The deliberately small DOM model executes the real reader to test discovery
and read ordering only. It does not model WebDriver, layout, events or React.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import json
import shutil
import subprocess
import unittest

from hub_form_fixture import fixture_request
from quick_relist.hub_form_writer import (
    FieldTransfer, FORM_STATE_SCRIPT, TransferRejected, _json, _spec,
    bind_synthetic_fixture_for_tests,
)
from test_hub_form_writer import Driver, Element, bound


DOM_MODEL = r"""
const request=JSON.parse(require('fs').readFileSync(0,'utf8'));
let secretReads=0;
class Node {
  constructor(tag,attrs={}) {this.tagName=tag.toUpperCase();this.attrs=attrs;this.children=[];this.parentElement=null;this.hidden=false;this.disabled=false;this.readOnly=false;this.textContent='';this.labels=[];this._value='';}
  get id(){return this.attrs.id||'';}
  get name(){return this.attrs.name||'';}
  get type(){return this.attrs.type||'text';}
  get value(){if(this.attrs['data-read-trap']){secretReads++;throw Error('must not read');}return this._value;}
  getAttribute(k){return Object.hasOwn(this.attrs,k)?this.attrs[k]:null;}
  hasAttribute(k){return Object.hasOwn(this.attrs,k);}
  get classList(){return {contains: c=>(this.attrs.class||'').split(/\s+/).includes(c)};}
  append(n){n.parentElement=this;this.children.push(n);}
  getClientRects(){return [{}];}
  contains(n){return n===this||this.children.some(c=>c.contains(n));}
  matches(selector){return selector.split(',').some(s=>{
    s=s.trim();if(!s||s.includes(' ')||s.includes(':'))return false;
    let valid=true;
    s=s.replace(/\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]/g,(_,k,v)=>{valid=valid&&this.hasAttribute(k)&&(v===undefined||this.getAttribute(k)===v);return '';});
    s=s.replace(/#([\w-]+)/g,(_,id)=>{valid=valid&&this.id===id;return '';});
    s=s.replace(/\.([\w-]+)/g,(_,c)=>{valid=valid&&this.classList.contains(c);return '';});
    return valid&&(!s||s==='*'||s.toUpperCase()===this.tagName);
  });}
  querySelectorAll(s){return this.children.flatMap(n=>[...(n.matches(s)?[n]:[]),...n.querySelectorAll(s)]);}
  closest(s){for(let n=this;n;n=n.parentElement)if(n.matches(s))return n;return null;}
}
const document=new Node('document'),body=new Node('body'),root=new Node('main',{id:'quick-relist-synthetic-form','data-context':'{}'});
document.append(body);body.append(root);body.dataset={fixtureNonce:'fixture',syntheticOnly:'true'};document.body=body;document.readyState='complete';
document.getElementById=id=>document.querySelectorAll('[id]').find(n=>n.id===id)||null;
for(const attrs of request.controls)root.append(new Node(attrs.tag||'div',attrs));
const location={href:'http://127.0.0.1:12345/synthetic'};
const getComputedStyle=()=>({display:'block',visibility:'visible',opacity:'1'});
const state=new Function('document','location','getComputedStyle',request.script)(document,location,getComputedStyle,request.spec);
process.stdout.write(JSON.stringify({errors:state.errors,fields:Object.keys(state.fields),secretReads}));
"""


class HubFormReaderReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('node'):
            raise unittest.SkipTest('Node unavailable; reader discovery regression not executed')

    def read_controls(self, controls):
        # The production reader expects the spec in arguments[0]. Pass its
        # dependencies through a wrapper instead of modifying its body.
        script = 'const spec = arguments[3]; return (function(){' + FORM_STATE_SCRIPT + '}).call(null, spec);'
        spec=dict(_spec('details'),href='http://127.0.0.1:12345/synthetic',nonce='fixture',context='{}')
        result = subprocess.run(
            ['node', '-e', DOM_MODEL], input=json.dumps({'script':script,'spec':spec,'controls':controls}),
            text=True, capture_output=True, check=True, timeout=10,
        )
        return json.loads(result.stdout)

    def test_empty_form_has_no_discovery_error(self):
        self.assertEqual(self.read_controls([])['errors'], [])

    def test_unknown_native_control_is_rejected_without_value_read(self):
        result=self.read_controls([{'tag':'input','name':'unrecognized_business_field','data-read-trap':'true'}])
        self.assertIn('unsupported_control',result['errors'])
        self.assertEqual(result['secretReads'],0)

    def test_known_text_control_in_credential_context_is_not_read(self):
        for attribute,value in [('aria-label','Password'),('autocomplete','current-password'),('data-sensitive','true')]:
            with self.subTest(attribute=attribute):
                result=self.read_controls([{'tag':'input','id':'street_name','name':'street_name',attribute:value,'data-read-trap':'true'}])
                self.assertTrue(result['errors'])
                self.assertEqual(result['secretReads'],0)

    def test_unknown_value_bearing_aria_controls_are_rejected(self):
        for role in ('combobox','switch','checkbox','radio','listbox','slider','spinbutton','textbox',
                     'switch checkbox','future-widget switch','  switch  ','future-widget\ttextbox'):
            with self.subTest(role=role):
                result=self.read_controls([{'role':role,'aria-label':'Unverified business field','tabindex':'0'}])
                self.assertIn('unsupported_value_control',result['errors'],f'Unsupported {role} silently omitted from snapshot')
                self.assertEqual(result['secretReads'],0)

    def test_unknown_native_toggle_button_is_rejected(self):
        for pressed in ('true','false','mixed'):
            with self.subTest(pressed=pressed):
                result=self.read_controls([{'tag':'button','aria-pressed':pressed,'aria-label':'Unverified business flag'}])
                self.assertIn('unsupported_value_control',result['errors'])
                self.assertEqual(result['secretReads'],0)

    def test_unaccounted_aria_value_attributes_are_rejected(self):
        for attribute,value in (('aria-checked','true'),('aria-valuenow','1'),('aria-valuetext','Synthetic')):
            with self.subTest(attribute=attribute):
                result=self.read_controls([{'tag':'span',attribute:value,'aria-label':'Unverified business value'}])
                self.assertIn('unsupported_value_control',result['errors'])
                self.assertEqual(result['secretReads'],0)


class HubFormOrchestrationReviewTests(unittest.TestCase):
    def test_each_bound_context_identity_change_blocks_before_events(self):
        for name,value in asdict(fixture_request(keys=['street_name']).context).items():
            with self.subTest(name=name):
                request=fixture_request(keys=['street_name']);driver,writer=bound(request)
                altered=asdict(request.context);altered[name]=value+'-drift'
                driver.state['context']=_json(altered)
                report=writer.transfer()
                self.assertEqual(report.code,'fixture_identity_changed')
                self.assertEqual(driver.events,[])

    def test_new_window_and_hidden_tab_disappearance_block_before_events(self):
        for handles in (['synthetic-window','unexpected-window'],[]):
            with self.subTest(handles=handles):
                driver,writer=bound(fixture_request(keys=['street_name']))
                driver.window_handles=handles
                self.assertEqual(writer.transfer().code,'fixture_identity_changed')
                self.assertEqual(driver.events,[])

    def test_rejected_fixture_binding_consumes_nonce_and_never_reads_dom(self):
        request=fixture_request(keys=['street_name']);driver=Driver(request)
        driver.current_url='https://example.invalid/'
        with self.assertRaisesRegex(TransferRejected,'fixture_origin_or_route_rejected'):
            bind_synthetic_fixture_for_tests(driver,request,nonce=driver.nonce)
        with self.assertRaisesRegex(TransferRejected,'fixture_nonce_already_used'):
            bind_synthetic_fixture_for_tests(driver,request,nonce=driver.nonce)
        self.assertEqual(driver.calls,[]);self.assertEqual(driver.events,[])

    def test_checkbox_true_polarity_is_copied_not_inverted_by_label(self):
        request=fixture_request(keys=['address_hidden']);original=request.fields[0]
        request=replace(request,fields=(FieldTransfer.from_values(original.key,original.baseline,original.source),))
        driver,writer=bound(request);report=writer.transfer()
        self.assertEqual(report.status,'supported_fields_verified')
        self.assertTrue(driver.state['fields']['address_hidden']['value']['checked'])
        self.assertEqual(driver.events,[('click','address_hidden')])

    def test_unrequested_existing_field_is_guarded_during_transfer(self):
        request=fixture_request(keys=['street_name']);driver=Driver(request)
        extra=deepcopy(driver.state['fields']['street_name'])
        extra['element']=Element(driver,'street_number');extra['shape']=dict(extra['shape'],id='street_number',name='street_number')
        extra['value']='Original unrequested number';driver.state['fields']['street_number']=extra
        writer=bind_synthetic_fixture_for_tests(driver,request,nonce=driver.nonce)
        driver.on_event=lambda *_:driver.state['fields']['street_number'].update(value='Unexpected reset')
        report=writer.transfer()
        self.assertEqual(report.code,'unrelated_field_changed')
        self.assertTrue(report.form_state_uncertain)
        self.assertEqual(report.edited_fields,())
        self.assertEqual(driver.events,[('clear','street_name')])

    def test_noop_and_edit_never_claim_persistence_verified(self):
        for noop in (True,False):
            request=fixture_request(keys=['street_name'])
            if noop:
                f=request.fields[0];request=replace(request,fields=(FieldTransfer.from_values(f.key,f.baseline,f.baseline),))
            driver,writer=bound(request);report=writer.transfer()
            self.assertEqual(report.status,'supported_fields_verified')
            self.assertIsNone(report.saved)
            self.assertFalse(report.commit_action_clicked)
            self.assertFalse(report.persistence_verified)
            self.assertFalse(report.live_qualified)
            self.assertFalse(report.complete_recreation)
            if noop:self.assertEqual(driver.events,[])


if __name__=='__main__':unittest.main()
