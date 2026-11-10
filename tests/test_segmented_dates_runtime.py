"""Production DOM extractor against synthetic jsdom; optional runtime dependency.

No installation is performed. CI already supplies Node/jsdom. Uses a documented
rectangle shim because jsdom does not implement layout. Real Chrome still needs
the offline tests/fixtures/segmented_dates.html qualification on Windows.
"""
import json
import shutil
import subprocess
import unittest

from quick_relist.capture import READ_PAGE_SCRIPT
from quick_relist.date_capture import DATE_NAMES, DATE_TYPE, segmented_date_record_valid
from segmented_date_fixtures import RAW_DATES, date_record, fixture_html, picker_html

RUNNER = r"""
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM(input.html, {url:'https://hub.remax.co.za/app/listings/edit/123456', pretendToBeVisual:true, runScripts:'outside-only'});
const w = dom.window;
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
dom.window.close();
"""


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
            capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def dates(self, result):
        return [field for field in result["fields"] if field.get("type") == DATE_TYPE]

    def test_all_five_exact_fields_and_parts_match_saved_schema(self):
        result = self.extract()
        dates = self.dates(result)
        self.assertEqual([field["name"] for field in dates], list(DATE_NAMES))
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
