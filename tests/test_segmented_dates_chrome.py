"""Opt-in native Chrome qualification of the exact date capture DOM script.

Existing synthetic HTML only, served on loopback with a disposable profile and
an explicitly supplied existing driver. No Hub URL spoofing or controller/source
binding qualification. QUICK_RELIST_CHROME_TESTS=1 opts in; without it the class
skips before creating a profile, server or browser. No downloads or installs.
"""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quick_relist.capture import READ_PAGE_SCRIPT, CaptureSafetyError, parse_editor_url
from quick_relist.date_capture import (
    DATE_DIAGNOSTIC_KIND, DATE_NAMES, DATE_TYPE,
    segmented_date_diagnostic_valid, segmented_date_record_valid,
)
from segmented_date_fixtures import date_record, fixture_html
from test_editor_recheck_chrome import serve_synthetic_html


class SegmentedDateChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('QUICK_RELIST_CHROME_TESTS') != '1':
            raise unittest.SkipTest('Native date-script Chrome qualification not requested')
        driver_path = Path(os.environ.get('QUICK_RELIST_CHROMEDRIVER', ''))
        if not driver_path.is_file():
            raise unittest.SkipTest('Existing chromedriver required; no download attempted')
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except ImportError:
            raise unittest.SkipTest('Selenium is not installed; no installation attempted') from None
        profile = TemporaryDirectory(prefix='quick-relist-dates-synthetic-')
        cls.addClassCleanup(profile.cleanup)
        options = Options()
        for argument in ('--headless=new', '--window-size=1400,1200', '--disable-background-networking',
                         '--no-first-run', '--no-default-browser-check', '--user-data-dir=' + profile.name):
            options.add_argument(argument)
        if os.environ.get('QUICK_RELIST_CHROME_BINARY'):
            options.binary_location = os.environ['QUICK_RELIST_CHROME_BINARY']
        cls.driver = webdriver.Chrome(service=Service(str(driver_path.resolve())), options=options)
        cls.addClassCleanup(cls.driver.quit)
        cls.driver.set_page_load_timeout(10)
        cls.driver.set_script_timeout(5)

    def read_fixture(self, setup='', *, reject_first=False, false_container=False):
        with serve_synthetic_html(fixture_html(false_container=false_container)) as url:
            self.driver.get(url)
            # Script-only qualification must not relax the production Hub guard.
            with self.assertRaises(CaptureSafetyError):
                parse_editor_url(url)
            if setup:
                self.driver.execute_script(setup)
            self.driver.execute_script("""
              const denied = () => { throw new Error('Unexpected write, network or hidden-state read'); };
              for (const key of ['fetch','XMLHttpRequest','WebSocket','open']) window[key] = denied;
              for (const key of ['localStorage','sessionStorage'])
                Object.defineProperty(window, key, {get:denied, configurable:true});
              Object.defineProperty(document, 'cookie', {get:denied, set:denied, configurable:true});
              HTMLElement.prototype.click = denied;
              HTMLFormElement.prototype.submit = denied;
              HTMLFormElement.prototype.requestSubmit = denied;
              if (arguments[0]) Object.defineProperty(document.getElementById('fixture-input-0'),
                'value', {get:denied, set:denied});
            """, reject_first)
            signature = """const value = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').get;
              return [document.documentElement.outerHTML, location.href,
                Array.from(document.querySelectorAll('input')).map(el =>
                  [value.call(el), el.checked, el.disabled, el.readOnly])];"""
            before = self.driver.execute_script(signature)
            result = self.driver.execute_script(READ_PAGE_SCRIPT)
            self.assertEqual(self.driver.execute_script(signature), before)
            self.assertEqual(result['href'], url)
            self.assertEqual(result['ready_state'], 'complete')
            return result

    def first_diagnostic(self, result):
        dates = [field for field in result['fields'] if field.get('type') == DATE_TYPE]
        self.assertEqual([field['name'] for field in dates], list(DATE_NAMES[1:]))
        records = [item for item in result['discovery'] if item.get('kind') == DATE_DIAGNOSTIC_KIND]
        self.assertEqual(len(records), 1)
        item = records[0]
        self.assertTrue(segmented_date_diagnostic_valid(item))
        self.assertEqual(item['field_key'], DATE_NAMES[0])
        self.assertFalse(item['complete'])
        self.assertFalse(item['date_value_observed'])
        self.assertIn('segmented_date_segments_unresolved', result['limitations'])
        self.assertTrue(set(item['reasons']) <= set(result['limitations']))
        self.assertTrue(all(0 <= count <= item['count_cap'] == 100 for count in item['counts'].values()))
        self.assertLessEqual(len(item['segment_rejections']), 3)
        return item

    def test_accepted_dates_match_existing_fixture_exactly(self):
        result = self.read_fixture()
        dates = [field for field in result['fields'] if field.get('type') == DATE_TYPE]
        self.assertEqual([field['name'] for field in dates], list(DATE_NAMES))
        for index, field in enumerate(dates):
            self.assertTrue(segmented_date_record_valid(field))
            expected = date_record(index)
            expected['dom_order'] = index + 1  # Ordinary native date precedes appended date records.
            self.assertEqual(field, expected)
        self.assertFalse(any(item.get('kind') == DATE_DIAGNOSTIC_KIND for item in result['discovery']))

    def test_exact_false_container_variant_and_unrelated_extra_node(self):
        result = self.read_fixture(false_container=True)
        dates = [field for field in result['fields'] if field.get('type') == DATE_TYPE]
        self.assertEqual([field['name'] for field in dates], list(DATE_NAMES))
        for index, field in enumerate(dates):
            expected = date_record(index, false_container=True)
            expected['dom_order'] = index + 1
            self.assertEqual(field, expected)
            self.assertTrue(segmented_date_record_valid(field))
        self.assertFalse(any(item.get('kind') == DATE_DIAGNOSTIC_KIND for item in result['discovery']))
        for code in ('date_semantics_unresolved', 'date_business_validation_unverified', 'date_requiredness_unverified'):
            self.assertIn(code, result['limitations'])
        # The same count and false value on another node must not qualify.
        rejected = self.read_fixture("""
          const group=document.getElementById('fixture-input-0').parentElement;
          group.querySelector('.MuiPickersSectionList-root').removeAttribute('contenteditable');
          group.insertAdjacentHTML('beforeend','<i contenteditable=false>PRIVATE_MARKER_5467</i>');
        """, reject_first=True, false_container=True)
        item = self.first_diagnostic(rejected)
        self.assertEqual(item['counts']['contenteditable'], 4)
        self.assertIn('segmented_date_contenteditable_count_not_three', item['reasons'])
        self.assertNotIn('PRIVATE_MARKER_5467', json.dumps(item))

    def test_segment_rejection_excludes_privacy_marker_from_projection(self):
        marker = 'PRIVATE_MARKER_5467'
        result = self.read_fixture("""
          const input = document.getElementById('fixture-input-0');
          const segment = input.parentElement.querySelector('[role=spinbutton]');
          input.value = 'PRIVATE_MARKER_5467';
          segment.textContent = 'PRIVATE_MARKER_5467';
          segment.classList.add('PRIVATE_MARKER_5467');
          segment.setAttribute('aria-valuenow','PRIVATE_MARKER_5467');
          segment.setAttribute('aria-readonly','PRIVATE_MARKER_5467');
        """, reject_first=True)
        item = self.first_diagnostic(result)
        reason = 'segmented_date_segment_aria_readonly_not_false'
        self.assertIn(reason, item['reasons'])
        self.assertEqual(item['segment_rejections'], [{'dom_order': 0, 'reasons': [reason]}])
        self.assertNotIn(marker, json.dumps(item))
        self.assertNotIn(marker, json.dumps(result['limitations']))

    def test_outer_counts_and_hidden_button_reject_with_precise_reasons(self):
        cases = (
            ('counts', """const group=document.getElementById('fixture-input-0').parentElement;
              const segment=group.querySelector('[role=spinbutton]');
              for(let n=0;n<150;n++) {
                segment.parentElement.appendChild(segment.cloneNode(true));
                group.insertAdjacentHTML('beforeend','<button hidden>Extra synthetic button</button>');
              }""", {'segments':100, 'contenteditable':100, 'contenteditable_true':100,
                      'containers':1, 'buttons':100, 'visible_buttons':1},
             {'segmented_date_segment_count_not_three', 'segmented_date_button_count_not_one',
              'segmented_date_contenteditable_count_not_three'}),
            ('hidden_button', "document.getElementById('fixture-input-0').parentElement.querySelector('button').hidden=true;",
             {'segments':3, 'contenteditable':3, 'contenteditable_true':3,
              'containers':1, 'buttons':1, 'visible_buttons':0}, {'segmented_date_button_not_safe_visible'}),
        )
        for name, setup, counts, reasons in cases:
            with self.subTest(case=name):
                item = self.first_diagnostic(self.read_fixture(setup, reject_first=True))
                self.assertEqual(item['counts'], counts)
                self.assertEqual(set(item['reasons']), reasons)
                self.assertEqual(item['segment_rejections'], [])


if __name__ == '__main__':
    unittest.main()
