"""Stdlib-only shape/inventory/comparison tests; no browser or live records."""
from copy import deepcopy
from pathlib import Path
import unittest

from quick_relist.capture import READ_PAGE_SCRIPT, CaptureReadError, _validate_observation
from quick_relist.date_capture import (DATE_NAMES, DATE_TYPE, DATE_SCHEMA, DATE_FALSE_CONTAINER_SCHEMA, DATE_UNRESOLVED_ISSUES,
    date_observed_parts, segmented_date_record_valid)
from quick_relist.source_fields import extract_source_fields
from quick_relist.replacement_review import compare_replacement_fields
from segmented_date_fixtures import RAW_DATES, capture, date_record, fixture_html


def entries(result):
    return {row["key"]: row for row in result["entries"]}


def compare(a, b):
    return compare_replacement_fields(capture(a), capture(b, replacement=True),
        expected_original_rxcu="RXCU12345", expected_replacement_rxcu="RXCU23456")


class SegmentedDateTests(unittest.TestCase):
    def test_false_container_variant_preserves_raw_observations_and_unresolved_semantics(self):
        fields = [date_record(i, false_container=True) for i in range(5)]
        result = extract_source_fields(capture(fields))
        for index, field in enumerate(fields):
            self.assertTrue(segmented_date_record_valid(field))
            row = entries(result)[DATE_NAMES[index]]
            self.assertEqual(row['value'], RAW_DATES[index])
            self.assertEqual(row['date_parts'], date_observed_parts(field))
            self.assertEqual(row['status'], 'unresolved')
            self.assertEqual(row['issues'], list(DATE_UNRESOLVED_ISSUES))
        for flag in ('complete', 'live_ready'):
            self.assertFalse(result[flag])
        # The variant differs only in explicit new structural evidence.
        new = fields[0]['date_picker']
        old = date_record()['date_picker']
        for key in ('input', 'label', 'group', 'wrapper', 'segments'):
            self.assertEqual(new[key], old[key])
        self.assertEqual(old['schema_version'], DATE_SCHEMA)
        self.assertEqual(new['schema_version'], DATE_FALSE_CONTAINER_SCHEMA)

    def test_false_container_variant_requires_all_additional_binding_evidence(self):
        good = date_record(false_container=True)
        mutations = [
            lambda f: f['date_picker'].pop('shape_variant'),
            lambda f: f['date_picker'].update(shape_variant='other'),
            lambda f: f['date_picker'].update(schema_version=DATE_SCHEMA),
            lambda f: f['date_picker']['container'].pop('contenteditable'),
            lambda f: f['date_picker']['binding'].update(wrapper_contenteditable_count=3),
            lambda f: f['date_picker']['binding'].update(wrapper_contenteditable_count=True),
            lambda f: f['date_picker']['segments'][0].update(contenteditable='false'),
            lambda f: f['date_picker']['segments'][1].update(aria_label='Day'),
        ]
        for value in (None, '', 'true', 'FALSE', ' false ', 'invalid', False):
            mutations.append(lambda f, value=value: f['date_picker']['container'].update(contenteditable=value))
        for key in ('wrapper_contenteditable_true_count', 'wrapper_contenteditable_false_count',
                    'wrapper_contenteditable_other_count', 'container_parent_is_group',
                    'contenteditable_nodes_are_container_and_segments'):
            mutations.append(lambda f, key=key: f['date_picker']['binding'].pop(key))
            mutations.append(lambda f, key=key: f['date_picker']['binding'].update({key:'invalid'}))
            mutations.append(lambda f, key=key: f['date_picker']['binding'].update({key:False}))
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                field = deepcopy(good)
                mutate(field)
                self.assertFalse(segmented_date_record_valid(field))
                row = entries(extract_source_fields(capture([field])))[DATE_NAMES[0]]
                self.assertEqual(row['issues'], ['segmented_date_shape_invalid'])
                self.assertNotIn('value', row)
        count_only = date_record()
        count_only['date_picker'].update(schema_version=DATE_FALSE_CONTAINER_SCHEMA, shape_variant='bound-false-container')
        count_only['date_picker']['binding']['wrapper_contenteditable_count'] = 4
        self.assertFalse(segmented_date_record_valid(count_only))
        # Resetting only the schema/count cannot turn new-variant evidence into v1.
        downgraded = deepcopy(good)
        downgraded['date_picker'].update(schema_version=DATE_SCHEMA)
        downgraded['date_picker'].pop('shape_variant')
        downgraded['date_picker']['binding']['wrapper_contenteditable_count'] = 3
        self.assertFalse(segmented_date_record_valid(downgraded))

    def test_false_container_collector_boundary_keeps_strict_variant_validation(self):
        raw = {key: [] for key in ('rich_text', 'dialogs', 'sections', 'lists', 'tables',
                                   'media', 'editor_links', 'discovery', 'limitations')}
        raw.update(href='https://hub.remax.co.za/app/listings/edit/123456', ready_state='complete',
                   fields=[date_record(false_container=True)])
        self.assertEqual(_validate_observation(raw, raw['href'])['fields'], raw['fields'])
        raw['fields'][0]['date_picker']['binding']['contenteditable_nodes_are_container_and_segments'] = False
        with self.assertRaises(CaptureReadError):
            _validate_observation(raw, raw['href'])

    def test_all_five_preserve_raw_dates_parts_and_provenance(self):
        fields = [date_record(i) for i in range(5)]
        original = capture(fields)
        before = deepcopy(original)
        result = extract_source_fields(original)
        self.assertEqual(original, before)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["complete"])
        self.assertFalse(result["live_ready"])
        for index, name in enumerate(DATE_NAMES):
            row = entries(result)[name]
            self.assertEqual(row["value"], RAW_DATES[index])
            self.assertEqual(row["date_parts"], date_observed_parts(fields[index]))
            self.assertEqual(row["status"], "unresolved")
            self.assertEqual(row["issues"], list(DATE_UNRESOLVED_ISSUES))
            self.assertFalse(row["date_semantics_verified"])
            self.assertFalse(row["date_business_validation_verified"])
            self.assertEqual(row["observations"][0]["field"], fields[index])
            self.assertEqual(row["observations"][0]["path"], f"pages/0/fields/{index}")
            self.assertEqual(row["observations"][0]["page_sha256"], original["pages"][0]["integrity"]["sha256"])
            self.assertEqual(row["date_parts"][0]["text"], "2099")  # Deliberate raw/parts mismatch is not reconciled.
        self.assertEqual(result["unknowns"], [])

    def test_blank_invalid_and_whitespace_dates_are_observations_not_parsed(self):
        for raw in ("", "   ", "\u200e00.00.0000\u200f", "  not a date  ", "29/02/1900"):
            with self.subTest(raw=raw):
                row = entries(extract_source_fields(capture([date_record(raw=raw)])))[DATE_NAMES[0]]
                self.assertEqual(row["value"], raw)
                self.assertEqual(row["status"], "unresolved")

    def test_requiredness_never_inferred_from_asterisk_or_attributes(self):
        control = date_record(2)
        row = entries(extract_source_fields(capture([control])))[DATE_NAMES[2]]
        self.assertEqual(row["date_requiredness"]["label_text"], "Date listed*")
        self.assertFalse(row["date_requiredness"]["input_required_property"])
        self.assertFalse(row["date_requiredness"]["verified"])
        control["date_picker"]["input"].update(required_attribute="required", required_property=True, aria_required="true")
        control["date_picker"]["group"]["aria_required"] = "false"
        row = entries(extract_source_fields(capture([control])))[DATE_NAMES[2]]
        self.assertFalse(row["date_requiredness"]["verified"])
        self.assertEqual(row["date_requiredness"]["input_required_attribute"], "required")
        self.assertEqual(row["status"], "unresolved")

    def test_marker_alone_or_malformed_evidence_never_validates(self):
        mutations = [
            lambda f: f.pop("date_picker"),
            lambda f: f.update(date_picker={"schema_version": "mui-segmented-date-dom/v1", "status": "observed_supported"}),
            lambda f: f["date_picker"].update(segments=[]),
            lambda f: f["date_picker"]["binding"].update(document_input_id_count=2),
            lambda f: f["date_picker"]["binding"].update(wrapper_label_count=True),
            lambda f: f["date_picker"]["binding"].update(sections_in_group=False),
            lambda f: f["date_picker"]["wrapper"].update(visible=False),
            lambda f: f["date_picker"]["input"].update(sensitive_context=True),
            lambda f: f["date_picker"]["input"].update(type_attribute="text"),
            lambda f: f["date_picker"]["input"].update(value="different"),
            lambda f: f["date_picker"]["input"].update(name="other"),
            lambda f: f["date_picker"]["label"].update(id=f["id"]),
            lambda f: f["date_picker"]["label"].update(text_content="Conflicting label"),
            lambda f: f["date_picker"]["label"].update({"for": "elsewhere"}),
            lambda f: f["date_picker"]["group"].update(aria_labelledby="elsewhere"),
            lambda f: f["date_picker"]["segments"][0].update(aria_label="Month"),
            lambda f: f["date_picker"]["segments"][0].update(aria_valuetext="year"),
            lambda f: f["date_picker"]["segments"][1].update(aria_valuetext=None),
            lambda f: f["date_picker"]["segments"][2].update(child_element_count=1),
            lambda f: f["date_picker"]["segments"][2].update(dom_order=True),
            lambda f: f["date_picker"]["segments"][2].update(classes=[{}]),
            lambda f: f["date_picker"].update(semantics_verified=True),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                control = date_record()
                mutate(control)
                self.assertFalse(segmented_date_record_valid(control))
                row = entries(extract_source_fields(capture([control])))[DATE_NAMES[0]]
                self.assertEqual(row["status"], "unresolved")
                self.assertEqual(row["issues"], ["segmented_date_shape_invalid"])
                self.assertNotIn("value", row)
                self.assertEqual(row["observations"][0]["field"], control)

    def test_each_required_metadata_member_is_checked(self):
        good = date_record()
        for node_name in ("input", "label", "group", "wrapper", "container", "binding"):
            for key in good["date_picker"][node_name]:
                with self.subTest(node=node_name, key=key):
                    control = deepcopy(good)
                    del control["date_picker"][node_name][key]
                    self.assertFalse(segmented_date_record_valid(control))
        for key in good["date_picker"]["segments"][0]:
            with self.subTest(segment_key=key):
                control = deepcopy(good)
                del control["date_picker"]["segments"][0][key]
                self.assertFalse(segmented_date_record_valid(control))

    def test_unknown_name_marker_stays_unmapped(self):
        control = date_record()
        control["name"] = control["date_picker"]["input"]["name"] = "unsupported_date"
        self.assertFalse(segmented_date_record_valid(control))
        result = extract_source_fields(capture([control]))
        self.assertEqual(len(result["unknowns"]), 1)
        self.assertEqual(entries(result)[DATE_NAMES[0]]["status"], "missing")

    def test_ordinary_legacy_native_dates_remain_compatible(self):
        for control_type in ("text", "date", "datetime-local"):
            fields = [{"name": name, "id": name, "tag": "input", "type": control_type, "value": "  0000  "} for name in DATE_NAMES]
            for row in (entries(extract_source_fields(capture(fields)))[name] for name in DATE_NAMES):
                self.assertEqual(row["status"], "observed")
                self.assertEqual(row["value"], "  0000  ")
                self.assertNotIn("date_parts", row)

    def test_duplicate_controls_are_ambiguous_not_copy_values(self):
        control = date_record()
        row = entries(extract_source_fields(capture([control, control])))[DATE_NAMES[0]]
        self.assertEqual(row["status"], "ambiguous")
        self.assertNotIn("value", row)
        self.assertEqual(len(row["observations"]), 2)

    def test_comparison_matches_raw_observations_but_stays_unresolved(self):
        control = date_record()
        result = compare([control], [control])
        row = entries(result)[DATE_NAMES[0]]
        self.assertEqual(row["status"], "unresolved")
        self.assertEqual(row["observed_comparison"], "matched")
        for key in ("complete", "live_ready", "publish_ready", "fidelity_verified"):
            self.assertFalse(result[key])

    def test_raw_input_or_part_changes_are_visible_without_semantic_upgrade(self):
        original = date_record()
        mutations = [lambda f: (f.update(value=" 2040-01-02 "), f["date_picker"]["input"].update(value=" 2040-01-02 ")),
            lambda f: f["date_picker"]["segments"][1].update(text="NOVEMBER"),
            lambda f: f["date_picker"]["segments"][2].update(aria_valuenow="019"),
            lambda f: f["date_picker"]["segments"][2].update(aria_valuetext="nineteenth")]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                replacement = deepcopy(original)
                mutate(replacement)
                row = entries(compare([original], [replacement]))[DATE_NAMES[0]]
                self.assertEqual(row["observed_comparison"], "changed")
                self.assertEqual(row["status"], "unresolved")

    def test_malformed_date_is_not_comparable_even_when_values_equal(self):
        good = date_record()
        bad = deepcopy(good)
        bad["date_picker"]["binding"]["document_label_id_count"] = 2
        row = entries(compare([good], [bad]))[DATE_NAMES[0]]
        self.assertIsNone(row["observed_comparison"])

    def test_rich_segment_duplicates_are_retained_as_unresolved_coverage(self):
        rich = {"label": "Year", "text": "2099", "sanitized_html": "2099", "format_fidelity_verified": False}
        original = capture([date_record()], rich_text=[rich, rich, rich])
        replacement = capture([date_record()], replacement=True, rich_text=[rich, rich, rich])
        inventory = extract_source_fields(original)
        self.assertEqual(len(inventory["unknowns"]), 3)
        self.assertEqual(len({obs["path"] for obs in inventory["unknowns"]}), 3)
        result = compare_replacement_fields(original, replacement,
            expected_original_rxcu="RXCU12345", expected_replacement_rxcu="RXCU23456")
        self.assertEqual(len(result["unresolved_coverage"]), 6)

    def test_collector_boundary_rejects_malformed_date_claims(self):
        raw = {key: [] for key in ("rich_text", "dialogs", "sections", "lists", "tables", "media", "editor_links", "discovery", "limitations")}
        raw.update(href="https://hub.remax.co.za/app/listings/edit/123456", ready_state="complete", fields=[date_record()])
        result = _validate_observation(raw, raw["href"])
        self.assertEqual(result["fields"], raw["fields"])
        raw["fields"][0]["date_picker"].pop("segments")
        with self.assertRaises(CaptureReadError):
            _validate_observation(raw, raw["href"])

    def test_production_injection_and_offline_fixture_stay_in_sync(self):
        self.assertEqual(READ_PAGE_SCRIPT.count("const dateFieldsFor ="), 1)
        self.assertNotIn("__SEGMENTED_DATE_CAPTURE_HELPERS__", READ_PAGE_SCRIPT)
        # Date controls must be excluded before either the ordinary-control or
        # room-proxy branch. Keep the parentheses so neither branch can bypass it.
        guarded_filter = (
            ".filter(el => !refusedRoomControls.has(el) && !isSegmentedDateControl(el) "
            "&& (safeControl(el) || roomCheckboxProxies.has(el)))"
        )
        self.assertEqual(" ".join(READ_PAGE_SCRIPT.split()).count(guarded_filter), 1)
        self.assertIn("fields.push(...dateFieldsFor(root, fields.length))", READ_PAGE_SCRIPT)
        fixture = Path(__file__).parent / "fixtures" / "segmented_dates.html"
        self.assertEqual(fixture.read_text(encoding="utf-8"), fixture_html())


if __name__ == "__main__":
    unittest.main()
