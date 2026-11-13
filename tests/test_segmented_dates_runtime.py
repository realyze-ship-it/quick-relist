"""Production DOM extractor against synthetic jsdom; optional runtime dependency.

No installation is performed. CI already supplies Node/jsdom. Uses a documented
rectangle shim because jsdom does not implement layout. Real Chrome still needs
the offline tests/fixtures/segmented_dates.html qualification on Windows.
"""
import json
import shutil
import subprocess
import unittest

from quick_relist.capture import READ_PAGE_SCRIPT, _validate_observation
from quick_relist.date_capture import (DATE_DIAGNOSTIC_KIND, DATE_NAMES, DATE_TYPE,
    segmented_date_diagnostic_valid, segmented_date_record_valid)
from segmented_date_fixtures import RAW_DATES, date_record, fixture_html, picker_html

WAIT_FOR_COMPLETE = r"""
const waitForComplete = (w, timeoutMs = 2000) => new Promise((resolve, reject) => {
  const cleanup = () => { clearTimeout(timer); w.removeEventListener('load', ready); };
  const ready = () => {
    if (w.document.readyState !== 'complete') return;
    cleanup(); resolve();
  };
  const timer = setTimeout(() => {
    cleanup(); reject(new Error('Synthetic document did not reach readyState complete'));
  }, timeoutMs);
  w.addEventListener('load', ready);
  ready();
});
"""

RUNNER = WAIT_FOR_COMPLETE + r"""
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function run() {
const dom = new JSDOM(input.html, {url:'https://hub.remax.co.za/app/listings/edit/123456', pretendToBeVisual:true, runScripts:'outside-only'});
const w = dom.window;
try {
// JSDOM returns before its load event. Never overwrite readyState or relax the
// production boundary to accept an observation taken before loading completes.
await waitForComplete(w);
Object.defineProperty(w.Element.prototype, 'getClientRects', {value:function() {
  for (let el=this; el; el=el.parentElement) {
    if (el.hidden || w.getComputedStyle(el).display === 'none' || (el.tagName === 'DIALOG' && !el.open)) return [];
  }
  return [{x:0,y:0,width:100,height:20,top:0,right:100,bottom:20,left:0}];
}});
if (input.setup) w.Function(input.setup)();
const denied = () => { throw new Error('Prohibited side effect or raw value read'); };
for (const key of ['localStorage','sessionStorage']) Object.defineProperty(w,key,{get:denied});
Object.defineProperty(w.document,'cookie',{get:denied,set:denied});
for (const key of ['fetch','XMLHttpRequest','WebSocket','open']) w[key]=denied;
w.HTMLElement.prototype.click=denied;
w.HTMLFormElement.prototype.submit=denied;
w.HTMLFormElement.prototype.requestSubmit=denied;
const valueGetter = Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype,'value').get;
const signature = () => JSON.stringify({html:w.document.documentElement.outerHTML, href:w.location.href,
  values:Array.from(w.document.querySelectorAll('input')).map(el=>[el.id,valueGetter.call(el),el.checked,el.disabled,el.readOnly])});
const before = signature();
for (const id of input.deny_values || []) {
  const el = w.document.getElementById(id);
  if (el) Object.defineProperty(el,'value',{get:denied,set:denied});
}
const result = w.Function(input.script)();
if (signature() !== before) throw new Error('Extractor changed DOM or controls');
process.stdout.write(JSON.stringify(result));
} finally { dom.window.close(); }
}
run().catch(error => { console.error(error.message); process.exitCode = 1; });
"""


class SyntheticReadyStateWaitTests(unittest.TestCase):
    def test_wait_checks_actual_state_and_has_a_bounded_failure(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable; synthetic readiness helper test not run')
        script = WAIT_FOR_COMPLETE + r"""
          const assert = require('node:assert/strict');
          (async () => {
            const w = new EventTarget();
            w.document = {readyState:'complete'};
            await waitForComplete(w, 100); // Already loaded is accepted without a new event.
            w.document.readyState = 'loading';
            let settled = false;
            const pending = waitForComplete(w, 1000).then(() => { settled = true; });
            await Promise.resolve();
            assert.equal(settled, false);
            w.document.readyState = 'interactive';
            w.dispatchEvent(new Event('load'));
            await Promise.resolve();
            assert.equal(settled, false); // The event alone is insufficient.
            w.document.readyState = 'complete';
            w.dispatchEvent(new Event('load'));
            await pending;
            assert.equal(settled, true);
            w.document.readyState = 'loading';
            await assert.rejects(waitForComplete(w, 20), /did not reach readyState complete/);
            process.stdout.write('readiness checks passed');
          })().catch(error => { console.error(error); process.exitCode = 1; });
        """
        result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'readiness checks passed')

    def test_runner_closes_window_on_timeout_and_post_load_failure(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable; synthetic runner cleanup test not run')
        script = r"""
          const assert = require('node:assert/strict'), vm = require('node:vm');
          const runner = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
          (async () => {
            // Test doubles exercise the exact runner's failure cleanup, not DOM
            // qualification. No real jsdom readiness property is overwritten.
            for (const state of ['loading', 'complete']) {
              let closed = 0;
              const errors = [], delays = [], output = [], processState = {stdout:{write:x=>output.push(x)}};
              const w = new EventTarget();
              w.document = {readyState:state};
              w.close = () => { closed++; };
              w.Element = class {};
              w.Function = () => { throw new Error('Synthetic post-load setup failure'); };
              const context = {
                require: name => {
                  if (name === 'node:fs') return {readFileSync:()=>JSON.stringify({html:'', setup:'synthetic'})};
                  if (name === 'jsdom') return {JSDOM:class { constructor() { this.window = w; } }};
                  throw new Error('Unexpected dependency');
                },
                setTimeout: (callback, delay) => { delays.push(delay); return setTimeout(callback, Math.min(delay, 20)); },
                clearTimeout, console:{error:x=>errors.push(x)}, process:processState
              };
              await vm.runInNewContext(runner, context);
              assert.deepEqual(delays, [2000]);
              assert.equal(w.document.readyState, state);
              assert.equal(closed, 1);
              assert.equal(processState.exitCode, 1);
              assert.deepEqual(output, []);
              assert.deepEqual(errors, [state === 'loading'
                ? 'Synthetic document did not reach readyState complete' : 'Synthetic post-load setup failure']);
            }
            process.stdout.write('runner cleanup checks passed');
          })().catch(error => { console.error(error); process.exitCode = 1; });
        """
        result = subprocess.run([node, '-e', script], input=json.dumps(RUNNER),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'runner cleanup checks passed')


class SegmentedDateRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("Node unavailable; production DOM runtime tests not run")
        probe = subprocess.run([cls.node, "-e", "require('jsdom')"], capture_output=True, text=True, timeout=10)
        if probe.returncode:
            if "MODULE_NOT_FOUND" in probe.stderr:
                raise unittest.SkipTest("Optional jsdom unavailable; production DOM runtime tests not run")
            raise AssertionError(probe.stderr)

    def extract(self, html=None, *, setup="", deny_values=()):
        completed = subprocess.run([self.node, "-e", RUNNER], input=json.dumps({"html": html or fixture_html(),
            "script": READ_PAGE_SCRIPT, "setup": setup, "deny_values": list(deny_values)}),
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result['ready_state'], 'complete')
        return result

    def dates(self, result):
        return [field for field in result["fields"] if field.get("type") == DATE_TYPE]

    def diagnostics(self, result):
        records = [item for item in result['discovery'] if item.get('kind') == DATE_DIAGNOSTIC_KIND]
        self.assertTrue(all(segmented_date_diagnostic_valid(item) for item in records))
        cleaned = _validate_observation(result, result['href'])
        self.assertEqual([item for item in cleaned['discovery'] if item.get('kind') == DATE_DIAGNOSTIC_KIND], records)
        return records

    def test_all_five_exact_fields_and_parts_match_saved_schema(self):
        result = self.extract()
        dates = self.dates(result)
        self.assertEqual([field["name"] for field in dates], list(DATE_NAMES[:5]))
        self.assertEqual([field["value"] for field in dates], list(RAW_DATES))
        for index, field in enumerate(dates):
            self.assertTrue(segmented_date_record_valid(field))
            expected = date_record(index)
            expected["dom_order"] = index + 1  # Ordinary native field precedes appended date observations.
            self.assertEqual(field, expected)
        self.assertEqual(len(result["rich_text"]), 16)  # Fifteen parts plus unrelated editable, all retained.
        self.assertEqual(result["fields"][0]["id"], "ordinary-date")
        self.assertIn("date_semantics_unresolved", result["limitations"])
        self.assertIn("date_requiredness_unverified", result["limitations"])
        self.assertEqual(self.diagnostics(result), [])

    def test_observed_empty_picker_is_distinct_from_missing_or_partial_parts(self):
        for false_container in (False, True):
            result = self.extract(fixture_html(false_container=false_container), setup='''
              const input=document.querySelector('input[name="mandate_signed_date"]');
              input.value='';
              input.parentElement.querySelectorAll('[role="spinbutton"]').forEach((part,index)=>{
                part.removeAttribute('aria-valuenow'); part.setAttribute('aria-valuetext','Empty');
                part.textContent=['YYYY','MM','DD'][index];
              });
            ''')
            field=next(f for f in self.dates(result) if f['name']=='mandate_signed_date')
            self.assertTrue(field['date_picker']['empty_observed'])
            self.assertEqual(field['value'],'')
            self.assertTrue(segmented_date_record_valid(field))
            self.assertEqual(self.diagnostics(result),[])
            from copy import deepcopy
            for index in range(3):
                broken=deepcopy(field)
                broken['date_picker']['segments'][index]['aria_valuenow']='1'
                self.assertFalse(segmented_date_record_valid(broken))
            broken=deepcopy(field)
            broken['date_picker']['empty_observed']=False
            self.assertFalse(segmented_date_record_valid(broken))

    def test_false_container_variant_matches_explicit_saved_evidence(self):
        result = self.extract(fixture_html(false_container=True))
        dates = self.dates(result)
        self.assertEqual([field['name'] for field in dates], list(DATE_NAMES[:5]))
        self.assertEqual([field['value'] for field in dates], list(RAW_DATES))
        for index, field in enumerate(dates):
            expected = date_record(index, false_container=True)
            expected['dom_order'] = index + 1
            self.assertEqual(field, expected)
            self.assertTrue(segmented_date_record_valid(field))
        self.assertEqual(self.diagnostics(result), [])
        for code in ('date_semantics_unresolved', 'date_business_validation_unverified', 'date_requiredness_unverified'):
            self.assertIn(code, result['limitations'])
        legacy = self.extract(fixture_html(false_container=True), setup="""
          for(const container of document.querySelectorAll('.MuiPickersSectionList-root'))
            container.removeAttribute('contenteditable');
        """)
        self.assertEqual(self.dates(legacy), self.dates(self.extract()))

    def test_false_container_variant_rejects_other_values_nodes_and_relations(self):
        prefix = """const native=document.getElementById('fixture-input-0');
          const group=native.parentElement,wrapper=group.parentElement;
          const container=group.querySelector('.MuiPickersSectionList-root');
          const segment=container.querySelector('[role=spinbutton]');
        """
        setups = [f"container.setAttribute('contenteditable',{json.dumps(value)})"
                  for value in ('', 'true', 'plaintext-only', 'FALSE', ' false ', 'PRIVATE_MARKER_5467')]
        setups += [
            "container.removeAttribute('contenteditable'); group.setAttribute('contenteditable','false')",
            "container.removeAttribute('contenteditable'); segment.parentElement.setAttribute('contenteditable','false')",
            "container.removeAttribute('contenteditable'); wrapper.insertAdjacentHTML('beforeend','<i contenteditable=false>Extra</i>')",
            "wrapper.insertAdjacentHTML('beforeend','<i contenteditable=false>Extra</i>')",
            "const extra=document.createElement('div');group.insertBefore(extra,container);extra.appendChild(container)",
            "container.hidden=true",
            "container.classList.remove('MuiPickersInputBase-sectionsContainer')",
            "container.outerHTML=container.outerHTML.replace(/^<div/, '<section').replace(/div>$/, 'section>')",
            "segment.setAttribute('contenteditable','false')",
            "segment.removeAttribute('contenteditable')",
            "segment.setAttribute('aria-readonly','true')",
            "group.appendChild(segment.parentElement)",
            "document.getElementById('fixture-label-0').textContent='Unbound date label'",
        ]
        for setup in setups:
            with self.subTest(setup=setup):
                result = self.extract('<main>' + picker_html(false_container=True) + '</main>', setup=prefix+setup,
                                      deny_values=['fixture-input-0'])
                self.assertEqual(self.dates(result), [])
                self.assertTrue(any(code.startswith('segmented_date_') for code in result['limitations']))
                self.assertNotIn('PRIVATE_MARKER_5467', json.dumps(self.diagnostics(result)))

    def test_false_container_rejection_preserves_value_free_segment_diagnostics(self):
        result = self.extract('<main>' + picker_html(raw='PRIVATE_RAW_VALUE', false_container=True) + '</main>',
            setup="""const part=document.querySelector('[role=spinbutton]');
              part.setAttribute('aria-readonly','PRIVATE_ATTRIBUTE_VALUE');
              part.textContent='PRIVATE_TEXT_VALUE';
            """, deny_values=['fixture-input-0'])
        item, = self.diagnostics(result)
        self.assertEqual(item['contenteditable_binding'], 'container_false_and_three_true_segments')
        self.assertEqual(item['counts']['contenteditable'], 4)
        self.assertEqual(item['counts']['contenteditable_true'], 3)
        self.assertEqual(item['segment_rejections'], [{'dom_order':0,
                         'reasons':['segmented_date_segment_aria_readonly_not_false']}])
        self.assertNotIn('PRIVATE_', json.dumps(item))
        self.assertNotIn('PRIVATE_RAW_VALUE', json.dumps(result))

    def test_false_container_raw_sanitization_still_omits_unpreservable_values(self):
        for raw in ('X'*1001, 'https://example.invalid/date?private=synthetic', 'javascript:synthetic'):
            with self.subTest(raw_kind=raw[:15]):
                result = self.extract('<main>' + picker_html(raw=raw, false_container=True) + '</main>')
                self.assertEqual(self.dates(result), [])
                self.assertIn('segmented_date_raw_observation_omitted', result['limitations'])

    def test_rejection_counts_identify_each_container_and_control_condition(self):
        cases = {
            'segment_count_not_three': 'segment.remove()',
            'container_count_not_one': "container.classList.remove('MuiPickersSectionList-root')",
            'button_count_not_one': "group.insertAdjacentHTML('beforeend','<button>Extra</button>')",
            'contenteditable_count_not_three': "wrapper.insertAdjacentHTML('beforeend','<i contenteditable=false>Extra</i>')",
            'button_not_safe_visible': 'button.hidden=true',
            'button_outside_group': 'wrapper.appendChild(button)',
            'container_not_safe_visible': 'container.hidden=true',
            'container_tag_not_div': "container.outerHTML=container.outerHTML.replace(/^<div/, '<section').replace(/div>$/, 'section>')",
            'container_classes_missing': "container.classList.remove('MuiPickersInputBase-sectionsContainer')",
        }
        prefix = """const native=document.getElementById('fixture-input-0');
        const group=native.parentElement, wrapper=group.parentElement;
        const segment=wrapper.querySelector('[role=spinbutton]');
        const container=group.querySelector('.MuiPickersSectionList-root'), button=group.querySelector('button');
        """
        for reason, setup in cases.items():
            with self.subTest(reason=reason):
                result = self.extract('<main>' + picker_html() + '</main>', setup=prefix+setup,
                                      deny_values=['fixture-input-0'])
                self.assertEqual(self.dates(result), [])
                item, = self.diagnostics(result)
                code = 'segmented_date_' + reason
                self.assertIn(code, item['reasons'])
                self.assertIn(code, result['limitations'])
                self.assertIn('segmented_date_segments_unresolved', result['limitations'])
                self.assertEqual(item['segment_rejections'], [])

    def test_segment_rejections_identify_strict_predicates_without_values(self):
        cases = {
            'segment_tag_not_span': "segment.outerHTML=segment.outerHTML.replace(/^<span/, '<div').replace(/span>$/, 'div>')",
            'section_tag_not_span': "section.outerHTML=section.outerHTML.replace(/^<span/, '<div').replace(/span>$/, 'div>')",
            'section_parent_not_container': "group.insertBefore(section, group.querySelector('.MuiPickersSectionList-root'))",
            'section_class_missing': "section.classList.remove('MuiPickersSectionList-section')",
            'segment_classes_missing': "segment.classList.remove('MuiPickersInputBase-sectionContent')",
            'section_not_safe_visible': 'section.hidden=true',
            'segment_not_safe_visible': 'segment.hidden=true',
            'segment_has_children': "segment.innerHTML='<b>SYNTHETIC_CHILD_VALUE</b>'",
            'segment_contenteditable_not_true': "segment.setAttribute('contenteditable','false')",
            'segment_aria_label_order_mismatch': "segment.setAttribute('aria-label','Unknown synthetic label')",
            'segment_aria_labelledby_present': "segment.setAttribute('aria-labelledby','SYNTHETIC_REFERENCE')",
            'segment_aria_hidden_present': "segment.setAttribute('aria-hidden','false')",
            'segment_style_present': "segment.setAttribute('style','color:red')",
            'segment_aria_readonly_not_false': "segment.setAttribute('aria-readonly','true')",
            'segment_aria_disabled_not_false': "segment.setAttribute('aria-disabled','true')",
            'segment_tabindex_mismatch': "segment.setAttribute('tabindex','9')",
            'segment_aria_valuenow_missing': "segment.removeAttribute('aria-valuenow')",
            'segment_aria_valuetext_presence_mismatch': "segment.setAttribute('aria-valuetext','SYNTHETIC_YEAR_VALUE')",
        }
        prefix = """const native=document.getElementById('fixture-input-0');
        const group=native.parentElement, wrapper=group.parentElement;
        const segment=wrapper.querySelector('[role=spinbutton]'), section=segment.parentElement;
        """
        for reason, setup in cases.items():
            with self.subTest(reason=reason):
                result = self.extract('<main>' + picker_html() + '</main>', setup=prefix+setup,
                                      deny_values=['fixture-input-0'])
                self.assertEqual(self.dates(result), [])
                item, = self.diagnostics(result)
                code = 'segmented_date_' + reason
                self.assertIn(code, item['reasons'])
                self.assertTrue(any(part['dom_order'] == 0 and code in part['reasons']
                                    for part in item['segment_rejections']))
                self.assertNotIn('SYNTHETIC_', json.dumps(item))

    def test_diagnostics_exclude_all_arbitrary_values_and_attributes(self):
        marker = 'PRIVATE_MARKER_5467'
        setup = """const native=document.getElementById('fixture-input-0');
        const group=native.parentElement,wrapper=group.parentElement;
        native.setAttribute('data-marker','PRIVATE_MARKER_5467');
        for(const segment of wrapper.querySelectorAll('[role=spinbutton]')) {
          segment.textContent='PRIVATE_MARKER_5467';
          segment.classList.add('PRIVATE_MARKER_5467');
          segment.id='PRIVATE_MARKER_5467_'+segment.getAttribute('aria-label');
          for (const attr of ['aria-valuenow','aria-valuetext','aria-label','contenteditable',
                             'aria-readonly','aria-disabled','tabindex','aria-labelledby','style'])
            segment.setAttribute(attr,'PRIVATE_MARKER_5467');
        }
        """
        result = self.extract('<main>' + picker_html(raw=marker) + '</main>', setup=setup,
                              deny_values=['fixture-input-0'])
        item, = self.diagnostics(result)
        self.assertNotIn(marker, json.dumps(item))
        self.assertNotIn(marker, json.dumps(result['limitations']))
        self.assertEqual(item['counts']['contenteditable_true'], 0)
        self.assertEqual(len(item['segment_rejections']), 3)
        self.assertEqual([part['dom_order'] for part in item['segment_rejections']], [0, 1, 2])
        self.assertFalse(item['date_value_observed'])

    def test_diagnostic_counts_saturate_and_repeated_roots_deduplicate(self):
        result = self.extract('<main><div role="dialog">' + ''.join(picker_html(i) for i in range(5)) + '</div></main>',
            setup="""for(const wrapper of document.querySelectorAll('.MuiPickersTextField-root')) {
              const segment=wrapper.querySelector('[role=spinbutton]');
              for(let n=0;n<150;n++) {
                segment.parentElement.appendChild(segment.cloneNode(true));
                wrapper.insertAdjacentHTML('beforeend','<button hidden>Extra</button>');
              }
            }""", deny_values=['fixture-input-'+str(i) for i in range(5)])
        records = self.diagnostics(result)
        self.assertEqual([item['field_key'] for item in records], list(DATE_NAMES[:5]))
        for item in records:
            self.assertEqual(item['count_cap'], 100)
            self.assertEqual(item['counts'], {'segments':100, 'contenteditable':100, 'contenteditable_true':100,
                                             'containers':1, 'buttons':100, 'visible_buttons':1})
            self.assertEqual(item['segment_rejections'], [])
            self.assertLess(len(json.dumps(item)), 1500)

    def test_diagnostics_never_bypass_earlier_binding_or_visibility_gates(self):
        setups = (
            "document.getElementById('fixture-input-0').name='unrecognized_date'",
            "document.body.insertAdjacentHTML('beforeend','<input name=mandate_signed_date>')",
            "document.getElementById('fixture-input-0').classList.remove('MuiPickersOutlinedInput-input')",
            "document.getElementById('fixture-label-0').textContent='Unknown date label'",
            "document.querySelector('.MuiPickersTextField-root').hidden=true",
        )
        for setup in setups:
            with self.subTest(setup=setup):
                result = self.extract('<main>' + picker_html() + '</main>', setup=setup,
                                      deny_values=['fixture-input-0'])
                self.assertEqual(self.dates(result), [])
                self.assertEqual(self.diagnostics(result), [])

    def test_live_input_property_and_segment_values_are_read_separately(self):
        result = self.extract(setup="""
          document.getElementById('fixture-input-0').value=' 2050/12/31 ';
          const part=document.querySelector('[aria-label="Month"]');
          part.textContent='Dec'; part.setAttribute('aria-valuenow','12'); part.setAttribute('aria-valuetext','December');
        """)
        field = self.dates(result)[0]
        self.assertEqual(field["value"], " 2050/12/31 ")
        self.assertEqual(field["date_picker"]["segments"][1]["text"], "Dec")
        self.assertEqual(field["date_picker"]["segments"][1]["aria_valuetext"], "December")

    def test_malformed_bindings_fail_before_reading_the_hidden_value(self):
        setups = {
            "duplicate_input_id": "document.body.insertAdjacentHTML('beforeend','<i id=fixture-input-0>duplicate</i>')",
            "duplicate_label_id": "document.body.insertAdjacentHTML('beforeend','<i id=fixture-label-0>duplicate</i>')",
            "duplicate_external_label": "document.body.insertAdjacentHTML('beforeend','<label for=fixture-input-0>duplicate</label>')",
            "duplicate_name": "document.body.insertAdjacentHTML('beforeend','<input name=mandate_signed_date aria-hidden=true>')",
            "two_wrapper_labels": "wrapper.insertAdjacentHTML('beforeend','<label>Extra label</label>')",
            "missing_label": "label.remove()",
            "wrong_label": "label.textContent='Different date'",
            "wrong_label_for": "label.htmlFor='another-input'",
            "external_labelledby": "group.setAttribute('aria-labelledby','external-label')",
            "multiple_labelledby": "group.setAttribute('aria-labelledby','fixture-label-0 external-label')",
            "unexpected_describedby": "group.setAttribute('aria-describedby','help')",
            "missing_group_role": "group.removeAttribute('role')",
            "missing_input_class": "native.classList.remove('MuiPickersOutlinedInput-input')",
            "missing_wrapper_class": "wrapper.classList.remove('MuiFormControl-root')",
            "missing_group_class": "group.classList.remove('MuiPickersOutlinedInput-root')",
            "visible_native_fallback_forbidden": "native.removeAttribute('aria-hidden')",
            "explicit_input_type": "native.setAttribute('type','text')",
            "hidden_native_type": "native.setAttribute('type','hidden')",
            "different_input_tabindex": "native.setAttribute('tabindex','0')",
            "missing_segment": "segment.remove()",
            "duplicate_segment": "segment.parentElement.appendChild(segment.cloneNode(true))",
            "wrong_order": "segment.setAttribute('aria-label','Day')",
            "missing_valuenow": "segment.removeAttribute('aria-valuenow')",
            "unexpected_year_valuetext": "segment.setAttribute('aria-valuetext','2099')",
            "nested_segment_element": "segment.innerHTML='<b>2099</b>'",
            "segment_aria_hidden": "segment.setAttribute('aria-hidden','true')",
            "segment_style": "segment.setAttribute('style','color:red')",
            "disabled_segment": "segment.setAttribute('aria-disabled','true')",
            "external_segment_label": "segment.setAttribute('aria-labelledby','external-label')",
            "wrong_section_parent": "group.appendChild(segment.parentElement)",
            "extra_contenteditable": "wrapper.insertAdjacentHTML('beforeend','<span contenteditable=false>Extra</span>')",
            "extra_button": "group.insertAdjacentHTML('beforeend','<button>Extra</button>')",
            "hidden_button": "group.querySelector('button').hidden=true",
            "input_outside_group": "wrapper.appendChild(native)",
        }
        prefix = """const native=document.getElementById('fixture-input-0');
        const group=native.parentElement,wrapper=group.parentElement;
        const label=document.getElementById('fixture-label-0');
        const segment=wrapper.querySelector('[role=spinbutton]');
        """
        for name, setup in setups.items():
            with self.subTest(shape=name):
                result = self.extract('<main>' + picker_html(raw="SYNTHETIC_RAW_SENTINEL") + '</main>',
                    setup=prefix+setup, deny_values=["fixture-input-0"])
                self.assertEqual(result["fields"], [])
                self.assertTrue(any(code.startswith("segmented_date_") for code in result["limitations"]))
                self.assertNotIn("SYNTHETIC_RAW_SENTINEL", json.dumps(result))

    def test_hidden_wrapper_and_credential_context_are_omitted(self):
        for wrapper in ('<section hidden>{}</section>', '<section aria-hidden="true">{}</section>',
                        '<section style="display:none">{}</section>', '<section style="visibility:hidden">{}</section>',
                        '<section style="opacity:0">{}</section>', '<section aria-label="Account password">{}</section>'):
            with self.subTest(wrapper=wrapper):
                result = self.extract('<main>' + wrapper.format(picker_html()) + '</main>', deny_values=["fixture-input-0"])
                self.assertEqual(self.dates(result), [])
                self.assertEqual(result["rich_text"], [])
                self.assertIn("segmented_date_shape_unresolved", result["limitations"])

    def test_nonallowlisted_picker_hidden_input_is_never_read(self):
        result = self.extract('<main>' + picker_html(name="unrecognized_date", raw="SYNTHETIC_HIDDEN_SENTINEL") + '</main>',
            deny_values=["fixture-input-0"])
        self.assertEqual(result["fields"], [])
        self.assertIn("segmented_date_name_unsupported", result["limitations"])
        self.assertNotIn("SYNTHETIC_HIDDEN_SENTINEL", json.dumps(result))

    def test_cross_group_segment_is_not_assigned_to_another_date(self):
        result = self.extract('<main>' + picker_html(0) + picker_html(1) + '</main>', setup="""
          const a=document.querySelector('[data-fixture=date-0] .MuiPickersSectionList-root');
          const b=document.querySelector('[data-fixture=date-1] .MuiPickersSectionList-root');
          const moved=a.firstElementChild; a.appendChild(b.firstElementChild); b.appendChild(moved);
        """, deny_values=["fixture-input-0", "fixture-input-1"])
        self.assertEqual(result["fields"], [])
        self.assertIn("segmented_date_segments_unresolved", result["limitations"])
        self.assertEqual(len(result["rich_text"]), 6)

    def test_no_values_inferred_from_missing_inputs(self):
        result = self.extract('<main>' + picker_html() + '</main>', setup="document.getElementById('fixture-input-0').remove()")
        self.assertEqual(result["fields"], [])
        self.assertIn("segmented_date_input_binding_unresolved", result["limitations"])
        self.assertEqual(len(result["rich_text"]), 3)

    def test_unpreservable_raw_values_are_omitted_not_transformed(self):
        for raw in ('X'*1001, 'https://example.invalid/date?private=synthetic', 'javascript:synthetic'):
            with self.subTest(raw_kind=raw[:15]):
                result = self.extract('<main>' + picker_html(raw=raw) + '</main>')
                self.assertEqual(result["fields"], [])
                self.assertIn("segmented_date_raw_observation_omitted", result["limitations"])

    def test_asterisk_and_requiredness_attributes_stay_raw(self):
        result = self.extract(setup="""
          const el=document.getElementById('fixture-input-2');
          el.setAttribute('required','required'); el.setAttribute('aria-required','false');
          el.parentElement.setAttribute('aria-required','true');
        """)
        record = self.dates(result)[2]
        self.assertTrue(segmented_date_record_valid(record))
        self.assertEqual(record["label"], "Date listed*")
        self.assertFalse(record["date_picker"]["requiredness_verified"])
        self.assertEqual(record["date_picker"]["input"]["aria_required"], "false")
        self.assertTrue(record["date_picker"]["input"]["required_property"])

    def test_supported_groups_in_dialog_capture_root_are_observed(self):
        result = self.extract('<main>' + picker_html(0) + '<div role="dialog" aria-label="Synthetic dialog">' + picker_html(1) + '</div></main>')
        self.assertEqual([field["name"] for field in self.dates(result)], list(DATE_NAMES[:2]))
        self.assertEqual([field["name"] for field in result["dialogs"][0]["fields"]], [DATE_NAMES[1]])
        self.assertEqual(len(result["dialogs"][0]["rich_text"]), 3)

    def test_ordinary_native_and_text_dates_still_captured(self):
        result = self.extract('<main><input name="mandate_signed_date" value="  raw  "><input name="expiry_date" type="date" value="2040-01-02"></main>')
        self.assertEqual([field["type"] for field in result["fields"]], ["text", "date"])
        self.assertEqual([field["value"] for field in result["fields"]], ["  raw  ", "2040-01-02"])


if __name__ == "__main__":
    unittest.main()
