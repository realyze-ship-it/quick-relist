"""Read-only observations of the observed sale and rental Hub date pickers.

The JavaScript fragment is inserted into capture.READ_PAGE_SCRIPT, sharing its
visible/sensitiveContext/textOf/scopeFor/cut helpers and limitations set. It
reads DOM controls only, never calendars, component state, storage or APIs.
Raw input and ordered segment strings are separate observations, not parsed or
reconciled dates. The Python validator checks stored structural evidence, not
its authenticity. No observation authorizes a write or renewal.
"""
from __future__ import annotations

DATE_NAMES = (
    "mandate_signed_date", "mandate_expiry", "date_listed", "expiry_date", "on_market_since", "occupation_date",
)
DATE_LABELS = (
    "Mandate signed date", "Mandate expiry date", "Date listed", "Listing expiry date", "On market since", "Occupation date",
)
DATE_SCHEMA = "mui-segmented-date-dom/v1"
DATE_FALSE_CONTAINER_SCHEMA = "mui-segmented-date-dom/v2"
DATE_EMPTY_PLACEHOLDER_SCHEMA = "mui-segmented-date-dom/v3"
DATE_FALSE_CONTAINER_VARIANT = "bound-false-container"
DATE_TYPE = "mui-segmented-date"
DATE_DIAGNOSTIC_KIND = "segmented_date_rejection"
DATE_DIAGNOSTIC_SCHEMA = "mui-segmented-date-rejection/v1"
DATE_DIAGNOSTIC_COUNT_CAP = 100  # A count of 100 means 100 or more.
DATE_DIAGNOSTIC_COUNTS = (
    "segments", "contenteditable", "contenteditable_true", "containers", "buttons", "visible_buttons",
)
DATE_DIAGNOSTIC_SHAPE_REASONS = (
    "segmented_date_segment_count_not_three", "segmented_date_container_count_not_one",
    "segmented_date_button_count_not_one", "segmented_date_button_not_safe_visible",
    "segmented_date_button_outside_group", "segmented_date_contenteditable_count_not_three",
    "segmented_date_container_not_safe_visible", "segmented_date_container_tag_not_div",
    "segmented_date_container_classes_missing",
)
DATE_DIAGNOSTIC_SEGMENT_REASONS = (
    "segmented_date_segment_tag_not_span", "segmented_date_section_tag_not_span",
    "segmented_date_section_parent_not_container", "segmented_date_segment_group_mismatch",
    "segmented_date_section_class_missing", "segmented_date_segment_classes_missing",
    "segmented_date_section_not_safe_visible", "segmented_date_segment_not_safe_visible",
    "segmented_date_segment_has_children", "segmented_date_segment_contenteditable_not_true",
    "segmented_date_segment_aria_label_order_mismatch", "segmented_date_segment_aria_labelledby_present",
    "segmented_date_segment_aria_hidden_present", "segmented_date_segment_style_present",
    "segmented_date_segment_aria_readonly_not_false", "segmented_date_segment_aria_disabled_not_false",
    "segmented_date_segment_tabindex_mismatch", "segmented_date_segment_aria_valuenow_missing",
    "segmented_date_segment_aria_valuetext_presence_mismatch",
)
DATE_UNRESOLVED_ISSUES = (
    "date_semantics_unresolved", "date_business_validation_unverified", "date_requiredness_unverified",
)
_INPUT_CLASSES = ("MuiPickersInputBase-input", "MuiPickersOutlinedInput-input")
_GROUP_CLASSES = ("MuiPickersInputBase-root", "MuiPickersOutlinedInput-root")
_WRAPPER_CLASSES = ("MuiFormControl-root", "MuiPickersTextField-root")
_SEGMENT_CLASSES = ("MuiPickersSectionList-sectionContent", "MuiPickersInputBase-sectionContent")
_CONTAINER_CLASSES = ("MuiPickersSectionList-root", "MuiPickersInputBase-sectionsContainer")
_BINDING_COUNTS = {
    "document_named_input_count": 1, "document_input_id_count": 1,
    "document_label_id_count": 1, "document_labels_for_input_count": 1,
    "wrapper_input_count": 1, "wrapper_label_count": 1, "wrapper_group_count": 1,
    "wrapper_spinbutton_count": 3, "wrapper_contenteditable_count": 3,
    "wrapper_button_count": 1, "group_section_list_count": 1,
}
_BINDING_RELATIONS = (
    "input_parent_is_group", "group_parent_is_wrapper", "label_in_wrapper",
    "sections_in_group", "calendar_in_group", "capture_root_contains_wrapper",
)
_FALSE_CONTAINER_COUNTS = {
    "wrapper_contenteditable_true_count": 3, "wrapper_contenteditable_false_count": 1,
    "wrapper_contenteditable_other_count": 0,
}
_FALSE_CONTAINER_RELATIONS = (
    "container_parent_is_group", "contenteditable_nodes_are_container_and_segments",
)

# This fragment is intentionally not a standalone browser task. It is injected
# once before fieldsFor; calls use the supplied capture root, including dialogs.
DATE_CAPTURE_HELPERS = r"""
  const dateNames = ['mandate_signed_date','mandate_expiry','date_listed','expiry_date','on_market_since','occupation_date'];
  const dateLabels = ['Mandate signed date','Mandate expiry date','Date listed','Listing expiry date','On market since','Occupation date'];
  // Rejections only, one record per known name across repeated root/dialog
  // scans. Counts saturate at 100. No values, identifiers or arbitrary strings.
  const dateRejectionDiagnostics = [];
  const isSegmentedDateControl = el => !!(el && el.tagName === 'INPUT' && (
    el.classList.contains('MuiPickersInputBase-input') || el.classList.contains('MuiPickersOutlinedInput-input')
    || el.closest('.MuiPickersTextField-root,.MuiPickersInputBase-root,.MuiPickersOutlinedInput-root')));
  const dateFieldsFor = (root, startOrder=0) => {
    const records = [];
    const attr = (el, name) => el.getAttribute(name);
    const classes = el => Array.from(el.classList);
    const hasClasses = (el, names) => !!el && names.every(name => el.classList.contains(name));
    const all = (scope, selector) => Array.from(scope.querySelectorAll(selector));
    const within = (scope, selector) => (scope.matches && scope.matches(selector) ? [scope] : []).concat(all(scope, selector));
    const same = (items, node) => items.length === 1 && items[0] === node;
    const uniqueID = el => !!el.id && !/\s/.test(el.id)
      && same(all(document, '[id]').filter(node => node.id === el.id), el);
    const safeVisible = el => !!el && visible(el) && !sensitiveContext(el);
    const expectedLabel = (label, name) => textOf(label).replace(/\s*\*\s*$/, '').trim() === dateLabels[dateNames.indexOf(name)];
    // Bounded exact observations only. Never silently sanitize/truncate a date
    // into a different value. The shared sanitization policy is not weakened.
    const exactString = value => typeof value === 'string' && value.length <= 1000 && cut(value, 1000) === value;
    const exactTree = value => value === null || typeof value === 'boolean' || typeof value === 'number'
      || (typeof value === 'string' && exactString(value))
      || (Array.isArray(value) && value.every(exactTree))
      || (value && typeof value === 'object' && Object.values(value).every(exactTree));
    const reject = code => limitations.add(code);
    const diagnoseSegments = (name, wrapper, group, segments, containers, buttons, checkSegments, falseContainer) => {
      const reasons = [], segmentRejections = [];
      const note = (list, failed, code) => { if (failed) list.push('segmented_date_' + code); };
      const editable = all(wrapper, '[contenteditable]');
      if (!checkSegments) {
        note(reasons, segments.length !== 3, 'segment_count_not_three');
        note(reasons, containers.length !== 1, 'container_count_not_one');
        note(reasons, buttons.length !== 1, 'button_count_not_one');
        note(reasons, editable.length !== 3 && !falseContainer, 'contenteditable_count_not_three');
        if (buttons.length === 1) {
          note(reasons, !safeVisible(buttons[0]), 'button_not_safe_visible');
          note(reasons, !group.contains(buttons[0]), 'button_outside_group');
        }
        if (containers.length === 1) {
          note(reasons, !safeVisible(containers[0]), 'container_not_safe_visible');
          note(reasons, containers[0].tagName !== 'DIV', 'container_tag_not_div');
          note(reasons, !hasClasses(containers[0], ['MuiPickersSectionList-root','MuiPickersInputBase-sectionsContainer']),
            'container_classes_missing');
        }
      } else {
        // This branch is reached only after the original exact count gate.
        // Record failed predicates, not the unexpected attribute contents.
        segments.slice(0, 3).forEach((segment, index) => {
          const section = segment.parentElement, failed = [];
          note(failed, segment.tagName !== 'SPAN', 'segment_tag_not_span');
          note(failed, !section || section.tagName !== 'SPAN', 'section_tag_not_span');
          note(failed, !section || section.parentElement !== containers[0], 'section_parent_not_container');
          note(failed, segment.closest('[role="group"]') !== group, 'segment_group_mismatch');
          note(failed, !hasClasses(section, ['MuiPickersSectionList-section']), 'section_class_missing');
          note(failed, !hasClasses(segment, ['MuiPickersSectionList-sectionContent','MuiPickersInputBase-sectionContent']),
            'segment_classes_missing');
          note(failed, !safeVisible(section), 'section_not_safe_visible');
          note(failed, !safeVisible(segment), 'segment_not_safe_visible');
          note(failed, !!segment.children.length, 'segment_has_children');
          note(failed, attr(segment, 'contenteditable') !== 'true', 'segment_contenteditable_not_true');
          note(failed, attr(segment, 'aria-label') !== ['Year','Month','Day'][index], 'segment_aria_label_order_mismatch');
          note(failed, segment.hasAttribute('aria-labelledby'), 'segment_aria_labelledby_present');
          note(failed, segment.hasAttribute('aria-hidden'), 'segment_aria_hidden_present');
          note(failed, segment.hasAttribute('style'), 'segment_style_present');
          note(failed, attr(segment, 'aria-readonly') !== 'false', 'segment_aria_readonly_not_false');
          note(failed, attr(segment, 'aria-disabled') !== 'false', 'segment_aria_disabled_not_false');
          note(failed, attr(segment, 'tabindex') !== (index === 0 ? '0' : '-1'), 'segment_tabindex_mismatch');
          note(failed, !segment.hasAttribute('aria-valuenow'), 'segment_aria_valuenow_missing');
          note(failed, segment.hasAttribute('aria-valuetext') !== (index !== 0), 'segment_aria_valuetext_presence_mismatch');
          if (failed.length) segmentRejections.push({dom_order:index, reasons:failed});
          reasons.push(...failed);
        });
      }
      reasons.forEach(reject);
      if (dateRejectionDiagnostics.some(item => item.field_key === name)) return;
      const cap = count => Math.min(count, 100);
      dateRejectionDiagnostics.push({kind:'segmented_date_rejection', schema_version:'mui-segmented-date-rejection/v1',
        status:'unresolved_structural_diagnostic', field_key:name, complete:false, date_value_observed:false,
        count_cap:100, counts:{segments:cap(segments.length), contenteditable:cap(editable.length),
          contenteditable_true:cap(editable.filter(el => attr(el, 'contenteditable') === 'true').length),
          containers:cap(containers.length), buttons:cap(buttons.length), visible_buttons:cap(buttons.filter(visible).length)},
        reasons:Array.from(new Set(reasons)), segment_rejections:segmentRejections,
        ...(falseContainer ? {contenteditable_binding:'container_false_and_three_true_segments'} : {})});
    };
    const inputs = within(root, 'input');
    const wrappers = within(root, '.MuiPickersTextField-root');
    const groups = within(root, '.MuiPickersInputBase-root[role="group"],.MuiPickersOutlinedInput-root[role="group"]');
    if (inputs.length > 2000 || wrappers.length > 200 || groups.length > 200) {
      reject('segmented_date_items_limit_exceeded'); return records;
    }
    for (const input of inputs) {
      if (isSegmentedDateControl(input) && !dateNames.includes(input.name)) reject('segmented_date_name_unsupported');
    }
    // An unsupported/orphaned picker must leave a gap even when its hidden
    // input has no supported name. No value is read in this discovery pass.
    for (const wrapper of wrappers) {
      const named = all(wrapper, 'input');
      if (named.length !== 1) reject('segmented_date_input_binding_unresolved');
      else if (!dateNames.includes(named[0].name)) reject('segmented_date_name_unsupported');
    }
    for (const group of groups) {
      if (!group.parentElement || !wrappers.includes(group.parentElement)) reject('segmented_date_wrapper_unresolved');
    }
    for (const name of dateNames) {
      const named = inputs.filter(el => el.name === name);
      const candidates = named.filter(el => isSegmentedDateControl(el) || attr(el, 'aria-hidden') === 'true' || el.type === 'hidden');
      if (!candidates.length) continue; // Ordinary legacy/native date controls remain compatible.
      if (named.length !== 1 || candidates.length !== 1
          || !same(all(document, 'input').filter(el => el.name === name), candidates[0])) {
        reject('segmented_date_input_binding_unresolved'); continue;
      }
      const input = candidates[0], group = input.parentElement, wrapper = group && group.parentElement;
      if (!group || !wrapper || group.tagName !== 'DIV' || wrapper.tagName !== 'DIV'
          || attr(group, 'role') !== 'group'
          || !hasClasses(input, ['MuiPickersInputBase-input','MuiPickersOutlinedInput-input'])
          || !hasClasses(group, ['MuiPickersInputBase-root','MuiPickersOutlinedInput-root'])
          || !hasClasses(wrapper, ['MuiFormControl-root','MuiPickersTextField-root'])
          || !root.contains(wrapper) || !safeVisible(wrapper) || !safeVisible(group)
          || sensitiveContext(input) || !uniqueID(input)
          || attr(input, 'type') !== null || input.type !== 'text'
          || attr(input, 'aria-hidden') !== 'true' || attr(input, 'tabindex') !== '-1'
          || input.hasAttribute('hidden') || attr(group, 'aria-describedby') !== null
          || !same(all(wrapper, 'input'), input) || !same(all(wrapper, '[role="group"]'), group)
          || all(wrapper, '.MuiPickersTextField-root').length) {
        reject('segmented_date_shape_unresolved'); continue;
      }
      const labels = all(wrapper, 'label'), label = labels[0];
      if (labels.length !== 1 || !safeVisible(label) || !uniqueID(label)
          || attr(group, 'aria-labelledby') !== label.id || attr(label, 'for') !== input.id
          || !same(all(document, 'label').filter(el => attr(el, 'for') === input.id), label)
          || !same(Array.from(input.labels || []), label) || !expectedLabel(label, name)
          || all(label, 'input,textarea,select,[contenteditable]').length) {
        reject('segmented_date_label_binding_unresolved'); continue;
      }
      const segments = all(wrapper, '[role="spinbutton"]');
      const containers = all(group, '.MuiPickersSectionList-root');
      const buttons = all(wrapper, 'button,[role="button"]');
      const editable = all(wrapper, '[contenteditable]');
      // A separate read-only variant, not a general allowance for four nodes.
      // The exact extra node must be this directly bound section-list container.
      const falseContainer = segments.length === 3 && containers.length === 1 && editable.length === 4
        && containers[0].parentElement === group && attr(containers[0], 'contenteditable') === 'false'
        && editable.includes(containers[0])
        && segments.every(segment => editable.includes(segment) && attr(segment, 'contenteditable') === 'true');
      // Hub renders an empty, unfocused picker with opacity-zero placeholders.
      // Only the exact empty tuple is supported: no hidden date value is read.
      const emptyObserved = segments.length === 3 && segments.every((segment, index) =>
        attr(segment, 'aria-valuenow') === null && attr(segment, 'aria-valuetext') === 'Empty'
        && segment.textContent === ['YYYY','MM','DD'][index]);
      const emptyPlaceholder = falseContainer && emptyObserved
        && getComputedStyle(containers[0]).opacity === '0';
      const safeDateVisible = el => {
        if (safeVisible(el)) return true;
        if (!emptyPlaceholder || !el || !containers[0].contains(el) || sensitiveContext(el)
            || !el.getClientRects().length || el.closest('[hidden],[aria-hidden="true"]')) return false;
        for (let node = el; node && node !== group; node = node.parentElement) {
          const style = getComputedStyle(node);
          if (style.display === 'none' || style.visibility !== 'visible'
              || (node !== containers[0] && Number(style.opacity || 1) === 0)) return false;
        }
        return safeVisible(group);
      };
      if (segments.length !== 3 || containers.length !== 1 || buttons.length !== 1
          || !safeVisible(buttons[0]) || !group.contains(buttons[0])
          || (editable.length !== 3 && !falseContainer)
          || !safeDateVisible(containers[0]) || containers[0].tagName !== 'DIV'
          || !hasClasses(containers[0], ['MuiPickersSectionList-root','MuiPickersInputBase-sectionsContainer'])) {
        reject('segmented_date_segments_unresolved');
        diagnoseSegments(name, wrapper, group, segments, containers, buttons, false, falseContainer); continue;
      }
      const container = containers[0];
      const parts = [];
      let valid = true;
      for (let index = 0; index < segments.length; index++) {
        const segment = segments[index], section = segment.parentElement;
        const part = ['Year','Month','Day'][index];
        if (segment.tagName !== 'SPAN' || !section || section.tagName !== 'SPAN'
            || section.parentElement !== container || segment.closest('[role="group"]') !== group
            || !hasClasses(section, ['MuiPickersSectionList-section'])
            || !hasClasses(segment, ['MuiPickersSectionList-sectionContent','MuiPickersInputBase-sectionContent'])
            || !safeDateVisible(section) || !safeDateVisible(segment) || segment.children.length
            || attr(segment, 'contenteditable') !== 'true' || attr(segment, 'aria-label') !== part
            || attr(segment, 'aria-labelledby') !== null || attr(segment, 'aria-hidden') !== null
            || attr(segment, 'style') !== null || attr(segment, 'aria-readonly') !== 'false'
            || attr(segment, 'aria-disabled') !== 'false' || attr(segment, 'tabindex') !== (index === 0 ? '0' : '-1')
            || (!emptyObserved && (attr(segment, 'aria-valuenow') === null
            || (index === 0 ? attr(segment, 'aria-valuetext') !== null : attr(segment, 'aria-valuetext') === null)))) {
          valid = false; break;
        }
        parts.push({dom_order:index, tag:'span', role:'spinbutton', classes:classes(segment),
          parent_tag:'span', parent_classes:classes(section), contenteditable:attr(segment,'contenteditable'),
          aria_label:attr(segment,'aria-label'), aria_labelledby:attr(segment,'aria-labelledby'),
          aria_hidden:attr(segment,'aria-hidden'), style:attr(segment,'style'),
          aria_readonly:attr(segment,'aria-readonly'), aria_disabled:attr(segment,'aria-disabled'),
          tabindex:attr(segment,'tabindex'), aria_valuenow:attr(segment,'aria-valuenow'),
          aria_valuetext:attr(segment,'aria-valuetext'), text:segment.textContent,
          visible:!emptyPlaceholder, sensitive_context:false, child_element_count:segment.children.length});
      }
      if (!valid) {
        reject('segmented_date_segments_unresolved');
        diagnoseSegments(name, wrapper, group, segments, containers, buttons, true, falseContainer); continue;
      }
      const raw = input.value;
      if (emptyObserved && raw !== '') { reject('segmented_date_raw_observation_omitted'); continue; }
      const record = {dom_order:startOrder + records.length, tag:'input', type:'mui-segmented-date',
        id:input.id, name, label:textOf(label), section:scopeFor(group), value:raw,
        checked:null, disabled:!!input.disabled, read_only:!!input.readOnly, selected_options:null, option_count:null,
        date_picker:{schema_version:'mui-segmented-date-dom/v1', status:'observed_supported',
          semantics_verified:false, business_validation_verified:false, requiredness_verified:false,
          input:{tag:'input', id:input.id, name, type_attribute:attr(input,'type'), type_property:input.type,
            aria_hidden:attr(input,'aria-hidden'), tabindex:attr(input,'tabindex'), classes:classes(input),
            value:raw, disabled:!!input.disabled, read_only:!!input.readOnly,
            required_attribute:attr(input,'required'), required_property:!!input.required,
            aria_required:attr(input,'aria-required'), hidden_attribute:attr(input,'hidden'), sensitive_context:false},
          label:{tag:'label', id:label.id, for:attr(label,'for'), text:textOf(label),
            text_content:label.textContent, visible:true, sensitive_context:false},
          group:{tag:'div', role:'group', classes:classes(group), aria_labelledby:attr(group,'aria-labelledby'),
            aria_describedby:attr(group,'aria-describedby'), aria_required:attr(group,'aria-required'),
            visible:true, sensitive_context:false},
          wrapper:{tag:'div', classes:classes(wrapper), visible:true, sensitive_context:false},
          container:{tag:'div', classes:classes(container), visible:!emptyPlaceholder, sensitive_context:false},
          segments:parts,
          binding:{document_named_input_count:1, document_input_id_count:1, document_label_id_count:1,
            document_labels_for_input_count:1, wrapper_input_count:1, wrapper_label_count:1, wrapper_group_count:1,
            wrapper_spinbutton_count:3, wrapper_contenteditable_count:3, wrapper_button_count:1, group_section_list_count:1,
            input_parent_is_group:true, group_parent_is_wrapper:true, label_in_wrapper:true,
            sections_in_group:true, calendar_in_group:true, capture_root_contains_wrapper:true}}};
      if (emptyObserved) record.date_picker.empty_observed = true;
      if (falseContainer) {
        record.date_picker.schema_version = 'mui-segmented-date-dom/v2';
        record.date_picker.shape_variant = 'bound-false-container';
        record.date_picker.container.contenteditable = 'false';
        Object.assign(record.date_picker.binding, {wrapper_contenteditable_count:4,
          wrapper_contenteditable_true_count:3, wrapper_contenteditable_false_count:1,
          wrapper_contenteditable_other_count:0, container_parent_is_group:true,
          contenteditable_nodes_are_container_and_segments:true});
      }
      if (emptyPlaceholder) {
        record.date_picker.schema_version = 'mui-segmented-date-dom/v3';
        record.date_picker.shape_variant = 'empty-opacity-placeholder';
        record.date_picker.container.placeholder_opacity = '0';
      }
      if (!exactTree(record)) { reject('segmented_date_raw_observation_omitted'); continue; }
      records.push(record);
      reject('date_semantics_unresolved'); reject('date_business_validation_unverified'); reject('date_requiredness_unverified');
    }
    return records;
  };
"""


def segmented_date_diagnostic_valid(item: dict) -> bool:
    """Closed, value-free rejection schema, never evidence of a resolved date.

    Exact keys and static reasons prevent this additive discovery type becoming
    a channel for arbitrary DOM strings. This validates shape, not authenticity.
    """
    keys = {"kind", "schema_version", "status", "field_key", "complete", "date_value_observed",
            "count_cap", "counts", "reasons", "segment_rejections"}
    if (not isinstance(item, dict) or set(item) not in (keys, keys | {"contenteditable_binding"})
            or item["kind"] != DATE_DIAGNOSTIC_KIND
            or item["schema_version"] != DATE_DIAGNOSTIC_SCHEMA
            or item["status"] != "unresolved_structural_diagnostic" or item["field_key"] not in DATE_NAMES
            or item["complete"] is not False or item["date_value_observed"] is not False
            or type(item["count_cap"]) is not int or item["count_cap"] != DATE_DIAGNOSTIC_COUNT_CAP):
        return False
    counts = item["counts"]
    if (not isinstance(counts, dict) or set(counts) != set(DATE_DIAGNOSTIC_COUNTS)
            or any(type(value) is not int or not 0 <= value <= DATE_DIAGNOSTIC_COUNT_CAP for value in counts.values())
            or counts["contenteditable_true"] > counts["contenteditable"]
            or counts["visible_buttons"] > counts["buttons"]):
        return False
    false_container = "contenteditable_binding" in item
    if false_container and (item["contenteditable_binding"] != "container_false_and_three_true_segments"
            or counts["contenteditable"] != 4 or counts["contenteditable_true"] != 3
            or counts["segments"] != 3 or counts["containers"] != 1):
        return False

    def reasons_valid(reasons, allowed):
        return (isinstance(reasons, list) and 0 < len(reasons) <= len(allowed)
                and all(isinstance(reason, str) and reason in allowed for reason in reasons)
                and len(reasons) == len(set(reasons)))

    reasons, segments = item["reasons"], item["segment_rejections"]
    if (not reasons_valid(reasons, DATE_DIAGNOSTIC_SHAPE_REASONS + DATE_DIAGNOSTIC_SEGMENT_REASONS)
            or not isinstance(segments, list) or len(segments) > 3):
        return False
    previous = -1
    for segment in segments:
        if (not isinstance(segment, dict) or set(segment) != {"dom_order", "reasons"}
                or type(segment["dom_order"]) is not int or not previous < segment["dom_order"] < 3
                or not reasons_valid(segment["reasons"], DATE_DIAGNOSTIC_SEGMENT_REASONS)):
            return False
        previous = segment["dom_order"]
    if segments:
        return (counts["segments"] == 3 and counts["contenteditable"] == (4 if false_container else 3)
                and counts["containers"] == counts["buttons"] == counts["visible_buttons"] == 1
                and set(reasons) == {reason for segment in segments for reason in segment["reasons"]})
    return all(reason in DATE_DIAGNOSTIC_SHAPE_REASONS for reason in reasons)


def is_segmented_date_record(field: dict) -> bool:
    """Recognize claims before considering ordinary/native legacy date records."""
    return "date_picker" in field or field.get("type") == DATE_TYPE


def segmented_date_record_valid(field: dict) -> bool:
    """Fail closed on malformed saved records; a type/schema marker is not proof.

    Validate required evidence and cross-bind IDs, names, labels and raw values.
    Do not parse date strings, reconcile components or infer requiredness.
    Unknown extra properties remain in the original control/provenance.
    """
    def string(value):
        return isinstance(value, str) and len(value) <= 1000

    def optional(value):
        return value is None or string(value)

    def has_classes(node, required):
        actual = node.get("classes")
        return (isinstance(actual, list) and all(string(x) for x in actual)
                and len(actual) == len(set(actual)) and set(required).issubset(actual))

    def visible(node):
        return node.get("visible") is True and node.get("sensitive_context") is False

    def identifier(value):
        return string(value) and bool(value) and not any(c.isspace() for c in value)

    if not isinstance(field, dict):
        return False
    data = field.get("date_picker")
    if (field.get("name") not in DATE_NAMES or field.get("tag") != "input" or field.get("type") != DATE_TYPE
            or not string(field.get("value")) or not identifier(field.get("id"))
            or not isinstance(data, dict) or data.get("schema_version") not in (DATE_SCHEMA, DATE_FALSE_CONTAINER_SCHEMA, DATE_EMPTY_PLACEHOLDER_SCHEMA)
            or data.get("status") != "observed_supported"
            or any(data.get(key) is not False for key in
                   ("semantics_verified", "business_validation_verified", "requiredness_verified"))):
        return False
    if any(not isinstance(data.get(key), dict) for key in ("input", "label", "group", "wrapper", "container", "binding")):
        return False
    native, label, group, wrapper, container, binding = (data[k] for k in
        ("input", "label", "group", "wrapper", "container", "binding"))
    empty_placeholder = data['schema_version'] == DATE_EMPTY_PLACEHOLDER_SCHEMA
    if empty_placeholder and (data.get('empty_observed') is not True or field['value'] != ''
            or container.get('placeholder_opacity') != '0'):
        return False
    if not empty_placeholder and 'placeholder_opacity' in container:
        return False
    def date_visible(node):
        return (node.get('visible') is False and node.get('sensitive_context') is False) if empty_placeholder else visible(node)
    if (native.get("tag") != "input" or any(native.get(k) != field[k] for k in ("id", "name", "value"))
            or native.get("type_attribute", False) is not None or native.get("type_property") != "text"
            or native.get("aria_hidden") != "true" or native.get("tabindex") != "-1"
            or native.get("hidden_attribute", False) is not None or native.get("sensitive_context") is not False
            or not has_classes(native, _INPUT_CLASSES)
            or any(type(native.get(k)) is not bool for k in ("disabled", "read_only", "required_property"))
            or any(native.get(k) is not field.get(k) for k in ("disabled", "read_only"))
            or any(k not in native or not optional(native[k]) for k in ("required_attribute", "aria_required"))):
        return False
    def normalized_label(value):
        if not string(value):
            return None
        value = " ".join(value.split())
        return value[:-1].rstrip() if value.endswith("*") else value

    expected_label = DATE_LABELS[DATE_NAMES.index(field["name"])]
    if (label.get("tag") != "label" or not identifier(label.get("id")) or label["id"] == native["id"]
            or label.get("for") != native["id"] or label.get("text") != field.get("label")
            or not string(label.get("text_content")) or not visible(label)
            or normalized_label(label.get("text")) != expected_label
            or normalized_label(label.get("text_content")) != expected_label):
        return False
    if (group.get("tag") != "div" or group.get("role") != "group" or not has_classes(group, _GROUP_CLASSES)
            or group.get("aria_labelledby") != label["id"] or group.get("aria_describedby", False) is not None
            or "aria_required" not in group or not optional(group["aria_required"]) or not visible(group)
            or wrapper.get('tag') != 'div' or not visible(wrapper)
            or container.get('tag') != 'div' or not date_visible(container)
            or not has_classes(wrapper, _WRAPPER_CLASSES) or not has_classes(container, _CONTAINER_CLASSES)):
        return False
    false_container = data["schema_version"] in (DATE_FALSE_CONTAINER_SCHEMA, DATE_EMPTY_PLACEHOLDER_SCHEMA)
    if false_container:
        expected_variant = 'empty-opacity-placeholder' if empty_placeholder else DATE_FALSE_CONTAINER_VARIANT
        if (data.get("shape_variant") != expected_variant or container.get("contenteditable") != "false"
                or any(type(binding.get(k)) is not int or binding[k] != value for k, value in _FALSE_CONTAINER_COUNTS.items())
                or any(binding.get(k) is not True for k in _FALSE_CONTAINER_RELATIONS)):
            return False
    elif ("shape_variant" in data or "contenteditable" in container
            or any(key in binding for key in (*_FALSE_CONTAINER_COUNTS, *_FALSE_CONTAINER_RELATIONS))):
        return False  # A new variant cannot be laundered through legacy validation.
    expected_counts = dict(_BINDING_COUNTS, wrapper_contenteditable_count=4) if false_container else _BINDING_COUNTS
    if (any(type(binding.get(k)) is not int or binding[k] != value for k, value in expected_counts.items())
            or any(binding.get(k) is not True for k in _BINDING_RELATIONS)):
        return False
    parts = data.get("segments")
    if not isinstance(parts, list) or len(parts) != 3:
        return False
    empty = data.get('empty_observed') is True
    if 'empty_observed' in data and (not empty or field['value'] != ''):
        return False
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            return False
        if (type(part.get("dom_order")) is not int or part["dom_order"] != index
                or part.get("tag") != "span" or part.get("role") != "spinbutton"
                or not has_classes(part, _SEGMENT_CLASSES) or part.get("parent_tag") != "span"
                or not isinstance(part.get("parent_classes"), list)
                or not all(string(x) for x in part["parent_classes"])
                or "MuiPickersSectionList-section" not in part["parent_classes"]
                or part.get("contenteditable") != "true" or part.get("aria_label") != ("Year", "Month", "Day")[index]
                or any(part.get(k, False) is not None for k in ("aria_labelledby", "aria_hidden", "style"))
                or any(part.get(k) != "false" for k in ("aria_readonly", "aria_disabled"))
                or part.get("tabindex") != ("0" if index == 0 else "-1")
                or not string(part.get("text"))
                or (empty and (part.get('aria_valuenow', False) is not None
                    or part.get('aria_valuetext') != 'Empty' or part.get('text') != ('YYYY','MM','DD')[index]))
                or (not empty and (not string(part.get("aria_valuenow"))
                    or (part.get("aria_valuetext", False) is not None if index == 0 else not string(part.get("aria_valuetext")))))
                or not date_visible(part) or type(part.get("child_element_count")) is not int or part["child_element_count"] != 0):
            return False
    return True


def date_observed_parts(field: dict) -> list[dict]:
    """Raw ordered component values for comparison, without incidental DOM IDs."""
    return [{key: part[key] for key in ("dom_order", "aria_label", "text", "aria_valuenow", "aria_valuetext")}
            for part in field["date_picker"]["segments"]]
