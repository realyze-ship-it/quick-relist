"""Closed-schema, value-free date rejection evidence; synthetic data only."""
from copy import deepcopy
import json
import unittest

from quick_relist.capture import (
    CaptureReadError, PartialHubCapture, READ_PAGE_SCRIPT, _digest, _validate_observation, verify_capture_integrity,
)
from quick_relist.date_capture import (
    DATE_DIAGNOSTIC_COUNT_CAP, DATE_DIAGNOSTIC_COUNTS, DATE_DIAGNOSTIC_KIND,
    DATE_DIAGNOSTIC_SCHEMA, DATE_NAMES, segmented_date_diagnostic_valid,
)
from quick_relist.source_fields import extract_source_fields
from segmented_date_fixtures import capture

BASE = 'https://hub.remax.co.za/app/listings/edit/synthetic-diagnostic'
REASON = 'segmented_date_segment_aria_readonly_not_false'


def diagnostic():
    return {'kind': DATE_DIAGNOSTIC_KIND, 'schema_version': DATE_DIAGNOSTIC_SCHEMA,
            'status': 'unresolved_structural_diagnostic', 'field_key': DATE_NAMES[0],
            'complete': False, 'date_value_observed': False, 'count_cap': DATE_DIAGNOSTIC_COUNT_CAP,
            'counts': dict(zip(DATE_DIAGNOSTIC_COUNTS, (3, 3, 3, 1, 1, 1))),
            'reasons': [REASON], 'segment_rejections': [{'dom_order': 0, 'reasons': [REASON]}]}


def observation(item=None):
    item = diagnostic() if item is None else item
    raw = {key: [] for key in ('fields', 'rich_text', 'dialogs', 'sections', 'lists', 'tables',
                               'media', 'editor_links', 'discovery', 'limitations')}
    raw.update(href=BASE, ready_state='complete', discovery=[item],
               limitations=['segmented_date_segments_unresolved', *item.get('reasons', [])])
    return raw


class Driver:
    current_url = BASE
    current_window_handle = 'synthetic'
    window_handles = ['synthetic']

    def execute_script(self, script):
        assert script == READ_PAGE_SCRIPT
        return observation()


class DateDiagnosticTests(unittest.TestCase):
    def test_false_container_segment_diagnostic_requires_explicit_bounded_evidence(self):
        item = diagnostic()
        item['counts']['contenteditable'] = 4
        self.assertFalse(segmented_date_diagnostic_valid(item))
        item['contenteditable_binding'] = 'container_false_and_three_true_segments'
        self.assertTrue(segmented_date_diagnostic_valid(item))
        self.assertEqual(_validate_observation(observation(item), BASE)['discovery'], [item])
        for key, value in (('contenteditable', 3), ('contenteditable_true', 2), ('segments', 4), ('containers', 2)):
            bad = deepcopy(item)
            bad['counts'][key] = value
            self.assertFalse(segmented_date_diagnostic_valid(bad))
        for value in ('PRIVATE_MARKER', None, True, {}, []):
            bad = deepcopy(item)
            bad['contenteditable_binding'] = value
            self.assertFalse(segmented_date_diagnostic_valid(bad))

    def test_closed_diagnostic_survives_capture_without_resolved_values(self):
        raw = observation()
        before = deepcopy(raw)
        self.assertTrue(segmented_date_diagnostic_valid(raw['discovery'][0]))
        result = _validate_observation(raw, BASE)
        self.assertEqual(raw, before)
        self.assertEqual(result['discovery'], raw['discovery'])
        saved = PartialHubCapture(Driver()).capture_current_page()
        self.assertEqual(saved['pages'][0]['discovery'], raw['discovery'])
        self.assertEqual(saved['pages'][0]['fields'], [])
        self.assertFalse(saved['complete'])
        self.assertFalse(saved['live_ready'])
        self.assertTrue(verify_capture_integrity(saved))

    def test_diagnostic_does_not_become_source_date_or_coverage_proof(self):
        saved = capture([])
        saved['pages'][0]['discovery'] = [diagnostic()]
        for value in (saved['pages'][0], saved):
            value.pop('integrity')
            value['integrity'] = {'algorithm': 'sha256', 'sha256': _digest(value)}
        inventory = extract_source_fields(saved)
        row = next(row for row in inventory['entries'] if row['key'] == DATE_NAMES[0])
        self.assertEqual(row['status'], 'missing')
        self.assertNotIn('value', row)
        self.assertFalse(inventory['complete'])
        self.assertFalse(inventory['live_ready'])

    def test_each_required_key_is_mandatory(self):
        good = diagnostic()
        for key in good:
            with self.subTest(key=key):
                bad = deepcopy(good)
                del bad[key]
                self.assertFalse(segmented_date_diagnostic_valid(bad))
                with self.assertRaisesRegex(CaptureReadError, 'date diagnostics'):
                    _validate_observation(observation(bad), BASE)
        for key in good['counts']:
            bad = diagnostic()
            del bad['counts'][key]
            self.assertFalse(segmented_date_diagnostic_valid(bad))

    def test_arbitrary_strings_extras_and_malformed_claims_fail_closed(self):
        marker = 'PRIVATE_MARKER_5467 https://secret.invalid/?token=private'
        mutations = [
            lambda x: x.update(field_key=marker), lambda x: x.update(schema_version=marker),
            lambda x: x.update(kind=marker), lambda x: x.update(status=marker),
            lambda x: x.update(complete=True), lambda x: x.update(date_value_observed=True),
            lambda x: x.update(value=marker), lambda x: x.update(reasons=[marker]),
            lambda x: x['counts'].update(source_id=marker),
            lambda x: x['segment_rejections'][0].update(text=marker),
            lambda x: x['segment_rejections'][0].update(reasons=[marker]),
            lambda x: x.update(count_cap=True), lambda x: x.update(count_cap=101),
            lambda x: x.update(reasons=[]), lambda x: x['reasons'].append(REASON),
            lambda x: x['segment_rejections'][0].update(dom_order=True),
            lambda x: x['segment_rejections'][0].update(dom_order=-1),
            lambda x: x['segment_rejections'][0].update(dom_order=3),
            lambda x: x['segment_rejections'].append(deepcopy(x['segment_rejections'][0])),
            lambda x: x.update(segment_rejections=x['segment_rejections'] * 4),
            lambda x: x.update(segment_rejections=[]),
            lambda x: x['counts'].update(segments=2),
            lambda x: x['counts'].update(contenteditable_true=4),
            lambda x: x['counts'].update(visible_buttons=2),
            lambda x: x['reasons'].append('segmented_date_button_count_not_one'),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                bad = diagnostic()
                mutate(bad)
                self.assertFalse(segmented_date_diagnostic_valid(bad))
                with self.assertRaises(CaptureReadError) as caught:
                    _validate_observation(observation(bad), BASE)
                self.assertNotIn(marker, str(caught.exception))

    def test_count_caps_and_type_bounds(self):
        for key in DATE_DIAGNOSTIC_COUNTS:
            for value in (-1, 101, True, 1.5, None, '3'):
                with self.subTest(key=key, value=value):
                    bad = diagnostic()
                    bad['counts'][key] = value
                    self.assertFalse(segmented_date_diagnostic_valid(bad))
        good = diagnostic()
        good['counts'] = {key: 100 for key in DATE_DIAGNOSTIC_COUNTS}
        good.update(reasons=['segmented_date_segment_count_not_three'], segment_rejections=[])
        self.assertTrue(segmented_date_diagnostic_valid(good))
        self.assertEqual(_validate_observation(observation(good), BASE)['discovery'], [good])

    def test_deduplication_known_names_and_umbrella_are_enforced(self):
        raw = observation()
        raw['discovery'] *= 2
        with self.assertRaises(CaptureReadError):
            _validate_observation(raw, BASE)
        raw = observation()
        raw['discovery'] = [dict(diagnostic(), field_key=name) for name in DATE_NAMES]
        self.assertEqual(len(_validate_observation(raw, BASE)['discovery']), 5)
        for code in ('segmented_date_segments_unresolved', REASON):
            raw = observation()
            raw['limitations'].remove(code)
            with self.assertRaises(CaptureReadError):
                _validate_observation(raw, BASE)

    def test_legacy_discovery_and_global_size_limit_are_unchanged(self):
        raw = observation()
        legacy = {'dom_order': 0, 'label': 'Synthetic action', 'expanded': None, 'controls': '', 'inspected': False}
        raw['discovery'].insert(0, legacy)
        self.assertEqual(_validate_observation(raw, BASE)['discovery'][0], legacy)
        raw['discovery'][0]['label'] = 'x' * 16_000_001
        with self.assertRaisesRegex(CaptureReadError, 'size limit'):
            _validate_observation(raw, BASE)

    def test_diagnostic_is_small_and_contains_no_generic_data_container(self):
        self.assertLess(len(json.dumps(diagnostic())), 1500)
        self.assertNotIn('date_picker', diagnostic())
        self.assertNotIn('value', diagnostic())


if __name__ == '__main__':
    unittest.main()
