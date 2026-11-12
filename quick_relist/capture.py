"""Read-only, *partial* observations of Hub listing editor pages.

This module is not a HubAdapter. It cannot certify a backup, map an RXCU to a
listing, authenticate an account, or enable the relist state machine. It never
calls Engine.snapshot and has no save/archive/create/publish operations.

``PartialHubCapture(driver)`` uses the user's CURRENT browser tab, not a guessed
editor tab. ``capture_current_page()`` records visible controls and any dialogs
that the operator has already opened. Repeated calls retain separate dated
observations. ``capture_listing(expected_listing_id)`` may navigate the pinned
tab, but only to query-free same-listing editor links actually observed in DOM.
It never clicks a button, opens a dialog, downloads a URL, or submits a form.

Returned business data may be private. Keep it in memory or protected local
storage; do not log it. No browser storage, cookies, network payloads, arbitrary
hidden fields, password fields, OTP fields or unsanitized rich HTML are inspected.
Only allowlisted business-selector codes paired with visible controls are read
from the observed hidden native selection inputs. Opacity-zero room checkboxes
require the exact observed visible, room-local MUI label proxy. The five supported segmented
date controls retain separately observed raw inputs and visible date parts.
There is deliberately no persistence, telemetry, or credential handling here.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from .business_fields import LIFESTYLE_FIELDS, GENERAL_FIELDS

from .date_capture import (
    DATE_CAPTURE_HELPERS, DATE_NAMES, is_segmented_date_record,
    segmented_date_diagnostic_valid, segmented_date_record_valid,
)

SCHEMA_VERSION = "hub-visible-capture/v1"
_URLISH = re.compile(r"(?:https?://|www\.|/)[^\s<>\"']+|[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+(?:[/?#][^\s<>\"']*)|(?:data|javascript):[^\s<>\"']+", re.IGNORECASE)
EDITOR_PAGES = ("details", "deal_particulars", "media", "portals")
MEDIA_FIELDS = (
    "marketing_header", "brochure_description", "rich_text_description",
    "virtualtour_url", "videotour_url", "p24_youtube_video_id",
    "matterport_video_id", "eyespy360_url",
)
_EDITOR_PATH = re.compile(
    r"^/app/listings/edit/(?P<listing_id>[A-Za-z0-9_-]{1,128})"
    r"(?:/(?P<page>particulars|deal_particulars|media|portals))?/?$"
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SECRET_FIELD = re.compile(
    r"password|passwd|passcode|one.?time|(?:^|[_\s-])otp(?:$|[_\s-])|"
    r"(?:^|[_\s-])pin(?:$|[_\s-])|token|secret|credential|csrf|authorization|"
    r"credit.?card|card.?number|card.?security|(?:^|[_\s-])cvv(?:$|[_\s-])",
    re.IGNORECASE,
)


class CaptureError(ValueError):
    """Safe to display: messages contain no captured values or browser URLs."""


class CaptureSafetyError(CaptureError):
    """The observation stopped because the page/tab identity was not stable."""


class CaptureReadError(CaptureError):
    """The DOM observation was unavailable or malformed; no write was attempted."""


def parse_editor_url(url: str) -> dict[str, str]:
    """Validate a literal observed editor URL; never normalize an unsafe route."""
    try:
        parsed = urlsplit(url)
        match = _EDITOR_PATH.fullmatch(parsed.path)
        valid = (
            isinstance(url, str) and parsed.scheme == "https"
            and parsed.hostname == "hub.remax.co.za"
            and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
            and "?" not in url and "#" not in url and "\\" not in url
            and not any(ord(c) < 33 or ord(c) == 127 for c in url)
            and match is not None
        )
        if valid:
            # Hub opens an editor at its bare URL, but its Listing particulars
            # tab returns to /particulars. Both are the same business page;
            # retain the actual path for provenance and tab-change checks.
            page = match.group("page")
            return {"listing_id": match.group("listing_id"),
                    "page_key": "details" if page in (None, "particulars") else page,
                    "page_path": parsed.path}
    except (AttributeError, TypeError, ValueError):
        pass
    raise CaptureSafetyError("Use a direct HTTPS Hub listing editor page with no query or fragment.")


# Only DOM reads are performed. Detached rich-text serialization preserves a
# small formatting vocabulary; its lossiness is recorded, never hidden.
# Limits protect the local app; every truncation is disclosed in the result.
READ_PAGE_SCRIPT = r"""
return (() => {
  const href = location.href;
  const limitations = new Set();
  const scrubURLs = value => String(value == null ? '' : value).replace(
    /(?:https?:\/\/|www\.|\/)[^\s<>"']+|[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+(?:[/?#][^\s<>"']*)|(?:data|javascript):[^\s<>"']+/gi,
    raw => {
      try {
        const u = new URL(raw.startsWith('www.') ? 'https://' + raw : raw, location.href);
        if (!['https:', 'http:'].includes(u.protocol) || u.username || u.password) {
          limitations.add('url_credentials_and_queries_omitted'); return '[URL omitted]';
        }
        if (u.search || u.hash) {
          limitations.add('url_credentials_and_queries_omitted');
          return raw.split(/[?#]/, 1)[0];
        }
      } catch (_) { limitations.add('url_credentials_and_queries_omitted'); return '[URL omitted]'; }
      return raw;
    });
  const cut = (value, limit=100000) => {
    const text = scrubURLs(value);
    if (text.length > limit) limitations.add('dom_text_truncated');
    return text.slice(0, limit);
  };
  const limited = (nodes, limit) => {
    const array = Array.from(nodes);
    if (array.length > limit) limitations.add('dom_items_truncated');
    return array.slice(0, limit);
  };
  const visible = el => {
    if (!el || !el.getClientRects || !el.getClientRects().length) return false;
    if (el.closest('[hidden],[aria-hidden="true"]')) return false;
    for (let current = el; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse'
          || Number(style.opacity || 1) === 0) return false;
    }
    return true;
  };
  const secret = /password|passwd|passcode|one.?time|(?:^|[_\s-])otp(?:$|[_\s-])|(?:^|[_\s-])pin(?:$|[_\s-])|token|secret|credential|csrf|authorization|credit.?card|card.?number|card.?security|(?:^|[_\s-])cvv(?:$|[_\s-])/i;
  const sensitiveContext = el => {
    for (let current = el; current && current.nodeType === Node.ELEMENT_NODE; current = current.parentElement) {
      const labelled = (current.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map(id => document.getElementById(id)?.textContent || '').join(' ');
      const labels = current.labels ? Array.from(current.labels).map(l => l.textContent || '').join(' ') : '';
      if (secret.test([current.id, current.getAttribute('name'), current.getAttribute('autocomplete'),
        current.getAttribute('aria-label'), labelled, labels].join(' '))) return true;
    }
    return false;
  };
  const safeControl = el => {
    const type = (el.type || '').toLowerCase();
    const key = [el.name, el.id, el.autocomplete, el.getAttribute('aria-label')].join(' ');
    return visible(el) && !sensitiveContext(el) && !['hidden', 'password', 'file', 'submit', 'button', 'reset', 'image'].includes(type) && !secret.test(key);
  };
  const textWithin = (el, acceptsParent) => {
    if (!el) return '';
    const parts = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let node;
    let length = 0;
    while ((node = walker.nextNode())) {
      const p = node.parentElement;
      if (!acceptsParent(p)) continue;
      if (!visible(p) || p.closest('script,style,noscript,template,input,textarea,select,[hidden],[aria-hidden="true"]')) continue;
      if (sensitiveContext(p)) continue;
      const text = node.textContent || '';
      parts.push(text);
      length += text.length;
      if (length > 100000) { limitations.add('dom_text_truncated'); break; }
    }
    return cut(parts.join(' ').replace(/\s+/g, ' ').trim());
  };
  const textOf = el => textWithin(el, () => true);
  const labelFor = el => {
    if (el.getAttribute('aria-label')) return cut(el.getAttribute('aria-label'));
    const ids = el.getAttribute('aria-labelledby');
    if (ids) return cut(ids.split(/\s+/).map(id => textOf(document.getElementById(id))).join(' ').trim());
    if (el.labels && el.labels.length) return cut(Array.from(el.labels).map(textOf).join(' ').trim());
    return textOf(el.closest('label'));
  };
  const scopeFor = el => {
    const group = el.closest('fieldset,section,article,[role="group"]');
    return group ? textOf(group.querySelector('legend,h1,h2,h3,h4,h5,h6,[role="heading"]')) : '';
  };
  const fieldValue = el => {
    const raw = String(el.value == null ? '' : el.value);
    if ((el.type === 'url' || /(?:^|[_\s-])url(?:$|[_\s-])/i.test([el.name, el.id, labelFor(el)].join(' '))) && raw.trim()) {
      try {
        const u = new URL(raw, location.href);
        if (!['https:', 'http:'].includes(u.protocol) || u.username || u.password) {
          limitations.add('url_credentials_and_queries_omitted'); return '[URL omitted]';
        }
        if (u.search || u.hash) {
          limitations.add('url_credentials_and_queries_omitted'); return cut(raw.split(/[?#]/, 1)[0]);
        }
      } catch (_) { limitations.add('url_credentials_and_queries_omitted'); return '[URL omitted]'; }
    }
    return cut(raw);
  };
  /* __SEGMENTED_DATE_CAPTURE_HELPERS__ */
  // Local room ownership is established before generic dialog fields are read.
  // Neither map is a general exception for hidden or credential-like inputs.
  const roomControlLabels = new Map(), roomCheckboxProxies = new Set(), refusedRoomControls = new Set();
  const businessProxyLabel = el => {
    // MUI renders these native states through visible label proxies. This is
    // not permission to read arbitrary hidden fields: only the named editor
    // controls, their native checked state, and their containing visible label
    // qualify. Room dialogs have a separate, explicitly scoped collector.
    if (!/^\/app\/listings\/edit\/[A-Za-z0-9_-]{1,128}(?:\/particulars)?\/?$/.test(location.pathname)
        || el.closest('[role="dialog"],dialog') || sensitiveContext(el)
        || !el.classList.contains('PrivateSwitchBase-input') || el.indeterminate
        || ['hidden','aria-hidden','aria-checked'].some(a => el.hasAttribute(a))) return null;
    const labels = {...__LIFESTYLE_FIELDS__, poa:'Price on application', featured:'Featured',
      address_hidden:'Show address on web', transfer_cost:'No Transfer Duty Applicable'};
    const checkbox = el.type === 'checkbox' && Object.hasOwn(labels, el.name);
    const radioLabels = {sale_state_id:['New','For Sale','Price Reduced','Offer Made','Sold'],
      rental_state_id:['To Rent','Available Immediately','Price Reduced','Leased']};
    const radio = el.type === 'radio' && Object.hasOwn(radioLabels, el.name);
    if (!checkbox && !radio) return null;
    const style = getComputedStyle(el), proxy = el.parentElement, label = proxy?.parentElement;
    if (Number(style.opacity || 1) !== 0 || style.display === 'none'
        || style.visibility !== 'visible' || style.position !== 'absolute'
        || !Array.from(el.getClientRects()).some(r => r.width > 0 && r.height > 0)
        || !proxy?.matches(checkbox ? 'span.MuiCheckbox-root' : 'span.MuiRadio-root')
        || !visible(proxy) || !label?.matches('label.MuiFormControlLabel-root')
        || !visible(label) || sensitiveContext(label) || label.hasAttribute('for')
        || label.querySelectorAll('input,textarea,select').length !== 1
        || ![null,'false'].includes(el.getAttribute('data-indeterminate'))) return null;
    const caption = textOf(label);
    if (checkbox && caption !== labels[el.name]) return null;
    if (radio && (!label.parentElement?.matches('div[role="radiogroup"][aria-label="' + el.name + '"]')
        || !radioLabels[el.name].includes(caption))) return null;
    return caption;
  };
  const fieldsFor = root => {
    const proxies = new Map();
    const fields = limited(root.querySelectorAll('input,select,textarea'), 2000)
      .filter(el => {
        if (refusedRoomControls.has(el) || isSegmentedDateControl(el)) return false;
        if (safeControl(el) || roomCheckboxProxies.has(el)) return true;
        const label = businessProxyLabel(el);
        if (label) { proxies.set(el, label); return true; }
        return false;
      }).map((el, index) => ({
        dom_order: index, tag: el.tagName.toLowerCase(), type: el.type || '',
        id: cut(el.id), name: cut(el.name), label: roomControlLabels.get(el) || proxies.get(el) || labelFor(el), section: scopeFor(el),
        ...(roomCheckboxProxies.has(el) ? {observed_room_checkbox_proxy: true} : {}),
        ...(proxies.has(el) ? {observed_business_checked_proxy: true} : {}),
        value: fieldValue(el), checked: ['checkbox', 'radio'].includes(el.type) ? el.checked : null,
        disabled: !!el.disabled, read_only: !!el.readOnly,
        selected_options: el.tagName === 'SELECT' ? limited(el.selectedOptions, 1000).map(o => ({value: cut(o.value), text: cut(o.textContent)})) : null,
        option_count: el.tagName === 'SELECT' ? el.options.length : null
      }));
    // Only these business selectors were observed with a visible MUI combo
    // immediately followed by its aria-hidden native input. Never generalize
    // this exception to arbitrary hidden fields or credential-like controls.
    const observedSelects = ['country_id','property_type_id','mandate_type_id',
      'office_id','listing_type_id','land_area_units','floor_area_units',
      'apartment_descriptive_property_type','deed_office_id','rental_price_period'];
    for (const name of observedSelects) {
      const combos = Array.from(root.querySelectorAll('div[role="combobox"]#mui-component-select-' + name))
        .filter(el => visible(el) && !sensitiveContext(el));
      const inputs = Array.from(root.querySelectorAll('input#' + name + '[name="' + name + '"][aria-hidden="true"]'));
      const code = inputs.length === 1 ? String(inputs[0].value) : '';
      const codeShape = name === 'country_id' ? /^[A-Za-z]{2}$/.test(code)
        : name === 'rental_price_period' ? /^per (day|week|month|year)$/.test(code)
        : /^[A-Za-z0-9_.:-]{0,128}$/.test(code);
      if (combos.length === 1 && inputs.length === 1 && combos[0].nextElementSibling === inputs[0]
          && combos[0].parentElement === inputs[0].parentElement && inputs[0].type === 'text'
          && !sensitiveContext(inputs[0]) && codeShape && textOf(combos[0])) {
        const record = {dom_order: fields.length, tag: 'div', type: 'custom-combobox',
          id: name, name, label: name === 'country_id' ? 'Country' : labelFor(combos[0]), section: scopeFor(combos[0]),
          value: textOf(combos[0]), checked: null, disabled: !!inputs[0].disabled, read_only: false,
          selected_options: [{value: cut(code), text: textOf(combos[0])}], option_count: null,
          observed_business_selection_code: true};
        if (name === 'country_id') record.observed_hidden_country_code = true;
        fields.push(record);
        limitations.add(name === 'country_id' ? 'country_option_resolution_unverified' : 'business_select_option_resolution_unverified');
      } else if (combos.length || inputs.length) {
        limitations.add(name === 'country_id' ? 'country_selector_unresolved' : 'business_selector_unresolved');
      }
    }
    // Bank mandate is an observed nameless MUI selector. Bind it by its
    // own visible form-control label, never by a fabricated HTML name.
    const bankControls = Array.from(root.querySelectorAll('div.MuiFormControl-root'))
      .filter(el => Array.from(el.children).some(c => c.matches('label') && textOf(c) === 'Bank mandate'));
    for (const container of bankControls) {
      const combos = Array.from(container.querySelectorAll('div[role="combobox"]')).filter(visible);
      const native = combos.length === 1 ? combos[0].nextElementSibling : null;
      if (bankControls.length === 1 && native?.matches('input.MuiSelect-nativeInput[aria-hidden="true"]')
          && !native.name && !native.id && !sensitiveContext(container) && /^\d{1,8}$/.test(native.value)) {
        fields.push({dom_order: fields.length, tag:'div', type:'custom-combobox', id:'', name:'',
          label:'Bank mandate', section:scopeFor(container), value:textOf(combos[0]), checked:null,
          disabled:!!native.disabled, read_only:false, option_count:null,
          selected_options:[{value:cut(native.value), text:textOf(combos[0])}],
          observed_business_selection_code:true, observed_label_bound_selector:'Bank mandate'});
      } else limitations.add('bank_mandate_selector_unresolved');
    }
    fields.push(...dateFieldsFor(root, fields.length));
    return fields;
  };
  const escapeText = text => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const formatTags = new Set(['P','BR','B','STRONG','I','EM','U','S','UL','OL','LI','DIV','SPAN','H1','H2','H3','H4','H5','H6','BLOCKQUOTE','PRE','CODE']);
  const richHTML = root => {
    let nodes = 0;
    const serialize = (node, depth=0) => {
      if (++nodes > 20000 || depth > 100) { limitations.add('rich_text_truncated'); return ''; }
      if (node.nodeType === Node.TEXT_NODE) return sensitiveContext(node.parentElement) ? '' : escapeText(cut(node.textContent));
      if (node.nodeType !== Node.ELEMENT_NODE) return '';
      if (['SCRIPT','STYLE','IFRAME','INPUT','TEXTAREA','SELECT','BUTTON','OBJECT','EMBED','TEMPLATE','NOSCRIPT'].includes(node.tagName)) return '';
      if (sensitiveContext(node)) return '';
      if (!visible(node)) return '';
      const inner = Array.from(node.childNodes).map(child => serialize(child, depth + 1)).join('');
      if (!formatTags.has(node.tagName)) return inner;
      const tag = node.tagName.toLowerCase();
      return tag === 'br' ? '<br>' : '<' + tag + '>' + inner + '</' + tag + '>';
    };
    return cut(Array.from(root.childNodes).map(node => serialize(node)).join(''));
  };
  const descriptionLabel = el => {
    const explicit = labelFor(el);
    if (explicit) return {label: explicit};
    // Current Hub uses TipTap with a visible label in the adjacent form
    // control, not DraftJS's aria-label. Bind that one observed relationship.
    if (!/^\/app\/listings\/edit\/[A-Za-z0-9_-]{1,128}\/media\/?$/.test(location.pathname)
        || !el.matches('div.tiptap.ProseMirror[role="textbox"][contenteditable="true"]'))
      return {label: ''};
    const editor = el.closest('.html-editor'), form = el.closest('.MuiFormControl-root');
    const row = form?.parentElement;
    if (!editor || !form || !row?.classList.contains('MuiGrid-root')
        || row.children.length !== 2 || row.children[1] !== form
        || !row.children[0].classList.contains('MuiFormControl-root')
        || !visible(row.children[0]) || sensitiveContext(row.children[0])
        || textOf(row.children[0]) !== 'Listing description'
        || row.querySelectorAll('[contenteditable="true"]').length !== 1
        || form.querySelectorAll('.html-editor').length !== 1)
      return {label: ''};
    return {label: 'Listing description', observed_description_label: true};
  };
  const richFor = root => {
    const found = limited(root.querySelectorAll('[contenteditable="true"],.rdw-editor-main,.rdw-editor'), 200).filter(visible);
    // Prefer the actual editable over an unlabelled presentation wrapper.
    // Labels belong to the selected element only: never borrow from siblings,
    // a toolbar, or an arbitrary labelled descendant. Separate editables stay
    // separate, so the inventory can report duplicate labels as ambiguous.
    const editables = found.filter(el => el.getAttribute('contenteditable') === 'true');
    const selected = found.filter(el => {
      if (el.getAttribute('contenteditable') === 'true') {
        if (el.querySelector('[contenteditable="true"]'))
          limitations.add('rich_text_nested_editables_unresolved');
        return !editables.some(other => other !== el && other.contains(el));
      }
      // Keep legacy wrapper-only observations, but do not fall back to a
      // wrapper when its editable is hidden, sensitive, or outside the limit.
      return !el.querySelector('[contenteditable="true"]')
        && !found.some(other => other !== el && other.contains(el));
    });
    return selected.filter(el => !sensitiveContext(el))
      .map((el, index) => ({dom_order: index, id: cut(el.id), name: cut(el.getAttribute('name')),
        ...descriptionLabel(el), section: scopeFor(el), text: textOf(el),
        sanitized_html: richHTML(el), format_fidelity_verified: false}));
  };
  const unsupportedRoomNode = (node, dialog) => node.hasAttribute('aria-checked')
    || (node.hasAttribute('data-indeterminate') && node.getAttribute('data-indeterminate') !== 'false')
    || node.hasAttribute('contenteditable') || (node !== dialog && node.hasAttribute('role'));
  const roomCheckbox = el => {
    // The observed native input is opacity-zero. Only its exact visible MUI
    // label proxy qualifies. Repeated IDs never supply a document-global label.
    const proxy = el.parentElement, label = proxy?.parentElement;
    const caption = label?.children[1], icon = proxy?.children[1];
    const style = getComputedStyle(el);
    if (el.tagName !== 'INPUT' || el.type !== 'checkbox' || !el.id || el.name !== el.id
        || refusedRoomControls.has(el) || el.indeterminate
        || ![null, 'false'].includes(el.getAttribute('data-indeterminate'))
        || !el.classList.contains('PrivateSwitchBase-input') || sensitiveContext(el)
        || ['hidden', 'aria-hidden', 'aria-label', 'aria-labelledby', 'aria-checked', 'tabindex'].some(a => el.hasAttribute(a))
        || Number(style.opacity || 1) !== 0 || style.display !== 'block'
        || style.visibility !== 'visible' || style.position !== 'absolute'
        || !Array.from(el.getClientRects()).some(r => r.width > 0 && r.height > 0)
        || !proxy?.matches('span.MuiButtonBase-root.MuiCheckbox-root.PrivateSwitchBase-root')
        || !visible(proxy) || proxy.children.length !== 2 || proxy.children[0] !== el
        || icon?.tagName.toLowerCase() !== 'svg' || icon.getAttribute('aria-hidden') !== 'true'
        || icon.getAttribute('focusable') !== 'false'
        || !label?.matches('label.MuiFormControlLabel-root.MuiFormControlLabel-labelPlacementEnd')
        || !visible(label) || sensitiveContext(label) || label.hasAttribute('for')
        || label.children.length !== 2 || label.children[0] !== proxy
        || label.querySelectorAll('input,select,textarea').length !== 1
        || !caption?.matches('span.MuiTypography-root.MuiTypography-body1.MuiFormControlLabel-label')
        || !visible(caption) || caption.children.length) return null;
    return textOf(caption) || null;
  };
  const sectionedRoomControls = (block, count) => {
    const controls = [];
    for (let i = 0; i < count; i++) {
      const grid = block.children[4 + i * 3];
      if (!grid.matches('div.MuiGrid-root.MuiGrid-container.MuiGrid-direction-xs-row')
          || !visible(grid) || !grid.children.length) return null;
      for (const item of grid.children) {
        if (!item.matches('div.MuiGrid-root.MuiGrid-direction-xs-row.MuiGrid-grid-xs-12.MuiGrid-grid-sm-4')
            || !visible(item) || item.children.length !== 1) return null;
        const label = item.children[0], input = label.querySelector('input');
        if (!input || input.parentElement?.parentElement !== label) return null;
        const text = roomCheckbox(input);
        if (!text) return null;
        controls.push([input, text]);
        if (controls.length > 1998) return null;  // fieldsFor also counts both textareas.
      }
    }
    // Unknown trailing subtrees are not folded into a description. The second
    // textarea is only a measuring control; its value is never inspected.
    const field = block.lastElementChild, label = field.children[0], base = field.children[1];
    if (!field.matches('div.MuiFormControl-root.MuiFormControl-fullWidth.MuiTextField-root')
        || !visible(field) || field.children.length !== 2 || label?.tagName !== 'LABEL'
        || !visible(label) || sensitiveContext(label)
        || !base?.matches('div.MuiInputBase-root.MuiOutlinedInput-root.MuiInputBase-multiline')
        || !visible(base) || base.children.length !== 3) return null;
    const description = base.children[0], measure = base.children[1], outline = base.children[2];
    if (description.tagName !== 'TEXTAREA' || description.name !== 'description'
        || !description.id || label.getAttribute('for') !== description.id || !safeControl(description)
        || Array.from(document.querySelectorAll('[id]')).filter(el => el.id === description.id).length !== 1
        || measure.tagName !== 'TEXTAREA' || measure.hasAttribute('id') || measure.hasAttribute('name')
        || measure.getAttribute('aria-hidden') !== 'true' || !measure.readOnly
        || measure.getAttribute('tabindex') !== '-1' || getComputedStyle(measure).visibility !== 'hidden'
        || getComputedStyle(measure).position !== 'absolute'
        || outline.tagName !== 'FIELDSET' || outline.getAttribute('aria-hidden') !== 'true'
        || new Set(controls.map(([input]) => input.id)).size !== controls.length
        || block.querySelectorAll('input,select,textarea').length !== controls.length + 2
        || block.querySelectorAll('button').length !== 1 || block.querySelector('button') !== block.children[1]
        || block.querySelector('a[href],[contenteditable],.rdw-editor-main,.rdw-editor')) return null;
    return controls;
  };
  const legacyRoomControls = block => {
    // The supported legacy shape uses direct controls/implicit labels, not an
    // arbitrary subtree with one H6. Explicit duplicate-ID labels are ambiguous.
    const controls = [], ids = new Set(), names = new Set(), children = Array.from(block.children);
    if (children[0]?.tagName !== 'H6') return null;
    for (const child of children.slice(1)) {
      if (child.tagName === 'BR' && !child.children.length) continue;
      if (child.tagName === 'TEXTAREA' && child.getAttribute('aria-hidden') === 'true'
          && child.readOnly && child.getAttribute('tabindex') === '-1' && !child.id && !child.name) continue;
      let control = child, label = '';
      if (child.tagName === 'LABEL') {
        if (child.hasAttribute('for') || child.children.length !== 1 || !visible(child)) return null;
        control = child.children[0]; label = textOf(child);
        if (!label || control.hasAttribute('aria-label') || control.hasAttribute('aria-labelledby')) return null;
      } else if (['checkbox', 'radio'].includes(control.type) || control.labels?.length) return null;
      if (!['INPUT', 'TEXTAREA'].includes(control.tagName) || !safeControl(control)
          || refusedRoomControls.has(control)) return null;
      if (['checkbox', 'radio'].includes(control.type)) {
        if ((control.id && ids.has(control.id)) || (control.name && names.has(control.name))) return null;
        if (control.id) ids.add(control.id);
        if (control.name) names.add(control.name);
      }
      controls.push([control, label]);
    }
    return controls.length ? controls : null;
  };
  const roomGroups = (dialog, title) => {
    const roomLabels = {'Set bedrooms options': 'Bedroom', 'Set bathrooms options': 'Bathroom',
      'Set kitchens options': 'Kitchen', 'Set parkings options': 'Parking', 'Set lounges options': 'Lounge',
      'Set dining rooms options': 'Dining room', 'Set garages options':'Garage',
      'Set braais options':'Braai', 'Set studies options':'Study', 'Set entrance halls options':'Entrance hall'};
    if (!Object.prototype.hasOwnProperty.call(roomLabels, title)) return [];
    const label = roomLabels[title];
    // Run before structural early returns. Unsupported native/local proxy state
    // must not leak through generic flat fields as an ordinary checked boolean.
    for (const el of dialog.querySelectorAll('input[type="checkbox"],input[type="radio"]')) {
      const local = el.closest('label');
      let refused = el.indeterminate || (local && dialog.contains(local)
        && [local, ...local.querySelectorAll('[aria-checked],[data-indeterminate],[role],[contenteditable]')]
          .some(node => unsupportedRoomNode(node, dialog)));
      for (let current = el; current && dialog.contains(current); current = current.parentElement) {
        if (unsupportedRoomNode(current, dialog)) refused = true;
      }
      if (refused) { refusedRoomControls.add(el); limitations.add('room_group_structure_unresolved'); }
    }
    const contents = Array.from(dialog.querySelectorAll('.MuiDialogContent-root')).filter(visible);
    if (contents.length !== 1) { limitations.add('room_group_structure_unresolved'); return []; }
    const sections = {
      'Bedroom': ['Bedroom options', 'Room options', 'Floor options'],
      'Bathroom': ['Bathroom options', 'Room options', 'Floor options'],
      'Kitchen': ['Kitchen options', 'Room options', 'Floor options'],
      'Parking': ['Parking options', 'Room options', 'Floor options'],
      'Lounge': ['Room options', 'Floor options'],
      'Dining room': ['Room options', 'Floor options'],
      'Garage': ['Garage options', 'Room options', 'Floor options'],
      'Braai': ['Braai options'], 'Study': ['Study options'], 'Entrance hall': ['Entrance hall options'],
    };
    const seen = new Set(), groups = [];
    if (contents[0].children.length > 200) limitations.add('room_group_structure_unresolved');
    for (const block of limited(contents[0].children, 200)) {
      if (!visible(block) || block.tagName === 'BR') continue;
      if (block.tagName !== 'DIV') { limitations.add('room_group_structure_unresolved'); continue; }
      const headings = Array.from(block.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')).filter(visible);
      const rawDirect = Array.from(block.children).filter(el => el.tagName === 'H6'), direct = rawDirect.filter(visible);
      const unsupported = [block, ...block.querySelectorAll('[role],[aria-checked],[data-indeterminate],[contenteditable]')]
        .some(node => unsupportedRoomNode(node, dialog))
        || Array.from(block.querySelectorAll('input')).some(el => el.indeterminate || refusedRoomControls.has(el));
      if (unsupported) {
        for (const el of block.querySelectorAll('input[type="checkbox"],input[type="radio"]')) refusedRoomControls.add(el);
        limitations.add('room_group_structure_unresolved'); continue;
      }
      const sectioned = direct.length === sections[label].length + 1
        && direct.slice(1).every((el, i) => textOf(el) === sections[label][i])
        && Array.from(block.children).map(el => el.tagName).join(',') ===
          ['H6', 'BUTTON', 'BR', ...sections[label].flatMap(() => ['H6', 'DIV', 'BR']), 'DIV'].join(',');
      // MUI section markers prevent a damaged sectioned room falling through
      // to legacy and accepting only its description while omitting checkboxes.
      const legacy = rawDirect.length === 1 && !block.querySelector(
        '.MuiGrid-container,.MuiFormControlLabel-root,.MuiCheckbox-root,.PrivateSwitchBase-input')
        ? legacyRoomControls(block) : null;
      if (headings.length !== direct.length || direct.length !== rawDirect.length || (!sectioned && !legacy)) {
        limitations.add('room_group_structure_unresolved'); continue;
      }
      const heading = textOf(direct[0]), match = heading.match(new RegExp('^' + label + ' ([1-9][0-9]*)$'));
      if (!match || !Number.isSafeInteger(Number(match[1])) || seen.has(match[1])) {
        limitations.add('room_group_structure_unresolved'); continue;
      }
      const controls = sectioned ? sectionedRoomControls(block, sections[label].length) : legacy;
      if (!controls) { limitations.add('room_group_structure_unresolved'); continue; }
      controls.forEach(([el, text]) => {
        if (text) roomControlLabels.set(el, text);
        if (sectioned) roomCheckboxProxies.add(el);
      });
      seen.add(match[1]);
      groups.push({title: heading, index: Number(match[1]), fields: fieldsFor(block),
        rich_text: richFor(block), mapping_verified: false});
    }
    if (!groups.length) limitations.add('room_group_structure_unresolved');
    return groups;
  };
  // Main/form scoping avoids unrelated account menus where the page exposes it.
  const mains = Array.from(document.querySelectorAll('main,[role="main"]')).filter(visible);
  const root = mains.length === 1 ? mains[0] : document.body;
  if (root === document.body) limitations.add('main_content_scope_unverified');
  if (!root) return {href, ready_state: document.readyState, unavailable: true};
  const dialogs = limited(document.querySelectorAll('[role="dialog"],[aria-modal="true"],dialog[open]'), 100)
    .filter(el => visible(el) && !sensitiveContext(el)).map((el, index) => {
      const title = labelFor(el) || textOf(el.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]'));
      const room_groups = roomGroups(el, title);
      return {dom_order: index, title, fields: fieldsFor(el), rich_text: richFor(el),
        text: textOf(el), room_groups};
    });
  const groups = new Set(root.querySelectorAll('fieldset,section,article,[role="group"]'));
  root.querySelectorAll('h1,h2,h3,h4,h5,h6,legend,[role="heading"]').forEach(h => { if (visible(h) && h.parentElement) groups.add(h.parentElement); });
  const sections = limited(groups, 400).filter(el => visible(el) && !sensitiveContext(el)).map((el, index) => ({dom_order: index,
    title: textOf(el.querySelector('legend,h1,h2,h3,h4,h5,h6,[role="heading"]')), text: textOf(el)}));
  const lists = limited(root.querySelectorAll('ul,ol,dl,[role="list"]'), 300).filter(visible).map((el, index) => ({dom_order: index,
    section: scopeFor(el), items: limited(el.children, 2000).filter(visible).map(textOf)}));
  const tableSelector = 'table,[role="table"],[role="grid"]';
  const rowSelector = 'tr,[role="row"]';
  const cellSelector = 'th,td,[role="cell"],[role="gridcell"],[role="columnheader"],[role="rowheader"]';
  const agentsFor = table => {
    if (!/^\/app\/listings\/edit\/[0-9]+(?:\/particulars)?\/?$/.test(location.pathname)
        || table.tagName !== 'TABLE' || table.closest('[role="dialog"],dialog')) return null;
    const headers = Array.from(table.querySelectorAll('thead th')).filter(e => e.closest('table') === table);
    if (headers.map(textOf).join('|') !== 'Is primary agent?|Name|Action') return null;
    const rows = Array.from(table.querySelectorAll('tbody tr')).filter(e => e.closest('table') === table);
    if (!rows.length || rows.length > 100) return null;
    const agents = [];
    for (const row of rows) {
      const cells = Array.from(row.cells);
      if (!visible(row) || cells.length !== 3 || !cells.every(visible)) return null;
      const inputs = cells[0].querySelectorAll('input');
      if (inputs.length !== 1) return null;
      const input = inputs[0], proxy = input.parentElement;
      if (input.type !== 'checkbox' || input.name || input.indeterminate || sensitiveContext(input)
          || input.hasAttribute('hidden') || input.hasAttribute('aria-hidden')
          || !input.classList.contains('PrivateSwitchBase-input')
          || !proxy?.matches('span.MuiCheckbox-root') || !visible(proxy)
          || !textOf(cells[1])) return null;
      agents.push({name:textOf(cells[1]), primary:input.checked});
    }
    return agents;
  };
  const tables = limited(root.querySelectorAll(tableSelector), 100)
    .filter(el => visible(el) && !sensitiveContext(el)).map((el, index) => {
      const notes = new Set(['table_semantics_unverified', 'grid_positions_not_inferred',
        'implicit_header_associations_not_inferred']);
      const take = (items, limit) => {
        const values = Array.from(items);
        if (values.length > limit) notes.add('table_items_truncated');
        return limited(values, limit);
      };
      const attr = (node, name) => node.hasAttribute(name) ? cut(node.getAttribute(name), 1000) : null;
      const ownTable = node => node.closest(tableSelector) === el;
      const ownRow = row => {
        const ancestor = row.parentElement.closest(rowSelector);
        return ownTable(row) && (!ancestor || !ownTable(ancestor));
      };
      // Nested tables get their own record. They never contribute text, rows,
      // cells, actions or chips to a containing table/cell's observations.
      const nested = el.querySelector(tableSelector);
      if (nested) notes.add('nested_table_content_excluded');
      const candidateRows = Array.from(el.querySelectorAll(rowSelector)).filter(ownTable);
      const nestedRows = candidateRows.some(row => !ownRow(row));
      if (nestedRows) notes.add('nested_row_structure_unsupported');
      const rowNodes = take(candidateRows.filter(ownRow), 2000);
      const observations = [];
      const headerNodes = [];
      let native = el.tagName === 'TABLE' && !nestedRows;
      if (el.tagName !== 'TABLE') notes.add('non_native_table_structure_unsupported');
      for (const [rowOrder, row] of rowNodes.entries()) {
        if (!visible(row) || sensitiveContext(row)) { notes.add('table_rows_omitted'); continue; }
        if (row.tagName !== 'TR') native = false;
        const ownCell = node => {
          const ancestor = node.parentElement.closest(cellSelector);
          return ownTable(node) && node.closest(rowSelector) === row
            && (!ancestor || !ownTable(ancestor) || ancestor.closest(rowSelector) !== row);
        };
        const candidateCells = Array.from(row.querySelectorAll(cellSelector)).filter(node => ownTable(node)
          && node.closest(rowSelector) === row);
        if (candidateCells.some(node => !ownCell(node))) {
          notes.add('nested_cell_structure_unsupported'); native = false;
        }
        const cellNodes = take(candidateCells.filter(ownCell), 100);
        const cells = [];
        for (const [cellOrder, cell] of cellNodes.entries()) {
          if (!visible(cell) || sensitiveContext(cell)) { notes.add('table_cells_omitted'); continue; }
          const isNative = ['TH', 'TD'].includes(cell.tagName);
          if (!isNative) native = false;
          const inCell = node => ownTable(node) && node.closest(rowSelector) === row
            && node.closest(cellSelector) === cell;
          const cellText = node => textWithin(node, inCell);
          const visibleLocal = node => inCell(node) && visible(node) && !sensitiveContext(node);
          const actionLabel = node => {
            if (node.hasAttribute('aria-label')) return cut(node.getAttribute('aria-label'), 1000);
            const ids = (node.getAttribute('aria-labelledby') || '').trim().split(/\s+/).filter(Boolean);
            if (ids.length) {
              const labels = take(ids, 100).map(id => document.getElementById(id));
              if (labels.length === ids.length && labels.every(label => label && visibleLocal(label))) {
                return cut(labels.map(cellText).join(' ').trim(), 1000);
              }
              notes.add('table_action_label_relationship_unresolved');
            }
            return cut(cellText(node), 1000);
          };
          const actions = take(Array.from(cell.querySelectorAll('button,a,[role="button"]'))
            .filter(node => visibleLocal(node) && !node.parentElement.closest('button,a,[role="button"]')), 100)
            .map(node => ({tag: node.tagName.toLowerCase(), role: attr(node, 'role'), label: actionLabel(node),
              disabled: typeof node.disabled === 'boolean' ? node.disabled : null,
              aria_disabled: attr(node, 'aria-disabled')}));
          // Only this recognizable chip root is observed. No status is derived
          // from a chip's text, colour, row width or proximity to another row.
          const chips = take(Array.from(cell.querySelectorAll('.MuiChip-root'))
            .filter(node => visibleLocal(node) && !node.parentElement.closest('.MuiChip-root')), 100)
            .map(node => ({text: cellText(node)}));
          if (chips.length) notes.add('chip_detection_limited_to_mui_roots');
          const headerIDs = take((cell.getAttribute('headers') || '').trim().split(/\s+/).filter(Boolean), 100);
          const record = {dom_order: cellOrder, tag: cell.tagName.toLowerCase(), role: attr(cell, 'role'),
            id: attr(cell, 'id'), text: cellText(cell),
            rowspan_attribute: attr(cell, 'rowspan'), rowspan_property: isNative ? cell.rowSpan : null,
            colspan_attribute: attr(cell, 'colspan'), colspan_property: isNative ? cell.colSpan : null,
            scope_attribute: attr(cell, 'scope'), scope_property: isNative ? cut(cell.scope, 1000) : null,
            headers_attribute: attr(cell, 'headers'), headers_property: isNative ? cut(cell.headers, 1000) : null,
            header_ids: headerIDs.map(id => cut(id, 1000)), header_refs: [], actions, chips};
          if (isNative && (cell.rowSpan !== 1 || cell.colSpan !== 1)) notes.add('table_spans_present');
          if (isNative && ['rowspan', 'colspan'].some(name => cell.hasAttribute(name)
              && !/^[0-9]+$/.test(cell.getAttribute(name)))) notes.add('table_span_attribute_invalid');
          cells.push(record);
          headerNodes.push({node: cell, record, rowOrder, rawHeaderIDs: headerIDs});
        }
        const group = row.parentElement;
        observations.push({dom_order: rowOrder, tag: row.tagName.toLowerCase(), role: attr(row, 'role'),
          row_group_tag: group && ['THEAD', 'TBODY', 'TFOOT'].includes(group.tagName) ? group.tagName.toLowerCase() : null,
          cells});
      }
      // Explicit headers IDREFs are retained, never turned into grid positions
      // or implicit/portal associations. A reference can point only to a unique
      // captured native header in this table; even that is not semantic proof.
      const byID = new Map();
      for (const item of headerNodes) {
        if (!item.node.id) continue;
        const matches = byID.get(item.node.id) || [];
        matches.push(item); byID.set(item.node.id, matches);
      }
      for (const item of headerNodes) {
        item.record.header_refs = item.rawHeaderIDs.map(rawID => {
          const id = cut(rawID, 1000);
          const matches = byID.get(rawID) || [];
          const target = matches.length === 1 ? matches[0] : null;
          if (target && rawID === id && target.node.tagName === 'TH' && target.record.id === id
              && document.getElementById(rawID) === target.node) {
            return {id, status: 'observed_header', row_dom_order: target.rowOrder,
              cell_dom_order: target.record.dom_order};
          }
          notes.add('explicit_header_relationship_unresolved');
          return {id, status: 'unresolved', row_dom_order: null, cell_dom_order: null};
        });
      }
      if (new Set(observations.map(row => row.cells.length)).size > 1) notes.add('table_row_cell_counts_vary');
      const status = el.tagName !== 'TABLE' ? 'unsupported_non_native'
        : native ? 'observed_native' : 'ambiguous_mixed';
      if (status === 'ambiguous_mixed') notes.add('mixed_table_structure_unsupported');
      const captions = take(Array.from(el.children).filter(node => node.tagName === 'CAPTION'
        && visible(node) && !sensitiveContext(node)), 100);
      for (const note of notes) limitations.add(note);
      const observedAgents = agentsFor(el);
      return {dom_order: index, section: scopeFor(el),
        ...(observedAgents ? {observed_agents:observedAgents} : {}),
        rows: observations.map(row => row.cells.map(cell => cell.text)),
        structure: {schema_version: 'table-dom/v1', tag: el.tagName.toLowerCase(), role: attr(el, 'role'),
          captions: captions.map(node => textWithin(node, ownTable)), status, mapping_verified: false,
          complete: false, rows: observations, limitations: Array.from(notes)}};
    });
  const referenceFor = value => {
    if (!value) return null;
    try {
      const u = new URL(value, location.href);
      if (!['https:', 'http:'].includes(u.protocol) || u.username || u.password) {
        limitations.add('media_source_omitted'); return null;
      }
      if (u.search || u.hash) limitations.add('url_credentials_and_queries_omitted');
      return {url: u.origin + u.pathname, query_omitted: !!u.search, fragment_omitted: !!u.hash};
    } catch (_) { limitations.add('media_source_omitted'); return null; }
  };
  const media = limited(root.querySelectorAll('img,video,audio,iframe'), 2000).filter(el => visible(el) && !sensitiveContext(el)).map((el, index) => {
    const figure = el.closest('figure');
    const section = scopeFor(el);
    const caption = figure ? textOf(figure.querySelector('figcaption')) : '';
    const hint = /floor\s*plan/i.test(section + ' ' + caption + ' ' + (el.alt || '')) ? 'floorplan' : el.tagName.toLowerCase();
    // Observed Hub image-card metadata. The combined grid includes photos and
    // floorplans; nearby upload-widget headings cannot classify these cards.
    const position = el.getAttribute('pos');
    const card = el.tagName === 'IMG' && el.alt === 'preview' && /^image-[0-9]+$/.test(el.id)
      && /^[0-9]+$/.test(position || '') && Number.isSafeInteger(Number(position));
    return {dom_order: index, tag: el.tagName.toLowerCase(), kind_hint: hint,
      section, alt: cut(el.alt), title: cut(el.title), caption,
      observed_asset_id: card ? el.id.slice(6) : null,
      observed_position: card ? Number(position) : null,
      original_source_ref: card ? referenceFor(el.getAttribute('data-full-src')) : null,
      source_ref: referenceFor(el.currentSrc || el.getAttribute('src')),
      width: Number(el.naturalWidth || el.videoWidth || 0), height: Number(el.naturalHeight || el.videoHeight || 0),
      cover_verified: false, original_bytes_verified: false, order_verified: false};
  });
  // Only editor links are candidates. No external/download links are followed.
  const editor_links = limited(root.querySelectorAll('a[href]'), 5000).filter(visible).flatMap(el => {
    try {
      const u = new URL(el.getAttribute('href'), location.href);
      if (!u.pathname.startsWith('/app/listings/edit/')) return [];
      if (u.search || u.hash || u.username || u.password) { limitations.add('unsafe_editor_links_omitted'); return []; }
      return [{url: u.href, text: textOf(el)}];
    } catch (_) { return []; }
  });
  const discovery = limited(root.querySelectorAll('button,[role="button"],[aria-expanded]'), 1000).filter(visible)
    .filter(el => !sensitiveContext(el)).map((el, index) => ({dom_order: index, label: labelFor(el) || textOf(el), expanded: el.getAttribute('aria-expanded'),
      controls: cut(el.getAttribute('aria-controls')), inspected: false}));
  const fields = fieldsFor(root);
  discovery.push(...dateRejectionDiagnostics);
  return {href, ready_state: document.readyState, fields, rich_text: richFor(root), dialogs,
    sections, lists, tables, media, editor_links, discovery, limitations: Array.from(limitations)};
})();
""".replace("/* __SEGMENTED_DATE_CAPTURE_HELPERS__ */", DATE_CAPTURE_HELPERS).replace(
    "__LIFESTYLE_FIELDS__", json.dumps({**LIFESTYLE_FIELDS, **GENERAL_FIELDS}))

_BASE_BLINDSPOTS = (
    ("partial_not_backup", "Visible DOM observations are not a complete listing backup and cannot authorize relisting."),
    ("reference_unverified", "The route listing ID has not been independently matched to an RXCU reference."),
    ("account_unverified", "The authenticated account, agency and source ownership have not been verified."),
    ("page_binding_unverified", "A matching URL does not independently prove that every rendered value belongs to that listing."),
    ("nested_dialogs_unverified", "Only already-open visible dialogs are read. Room options and other unopened nested dialogs may be missing."),
    ("related_records_unverified", "Parties, documents, notes and agents are generic visible observations; no exhaustive mapping or attachment bytes are verified."),
    ("media_unverified", "Media observations are DOM order and visible captions only. Originals, completeness, saved order, cover, captions and floorplan metadata are unverified."),
    ("rich_text_unverified", "Rich text is sanitized to a formatting subset; exact formatting and editor state are unverified."),
    ("portal_status_unverified", "Visible portal controls do not independently verify saved targets or live publication status."),
    ("async_state_unverified", "Asynchronous, lazy-loaded, collapsed, off-screen and paginated content may be missing or stale."),
    ("revision_unverified", "These observations are not an atomic server revision and may differ between pages or capture times."),
    ("sensitive_controls_omitted", "Hidden controls are excluded except narrowly allowlisted, structurally matched business-selector codes, raw inputs of five supported visible segmented date controls, and opacity-zero checkboxes within exact observed visible room-label proxies. Password, OTP, file and credential-like controls, browser secrets and recognized URL query/fragment data are deliberately excluded. Unlabelled secrets embedded in ordinary business prose cannot be exhaustively detected."),
    ("address_schema_unverified", "Address fields and autocomplete selections have not been mapped or validated against the required Hub schema."),
)
_PAYLOAD_LISTS = ("fields", "rich_text", "dialogs", "sections", "lists", "tables", "media", "editor_links", "discovery", "limitations")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _validate_table_structure(table: dict) -> None:
    """Validate the additive observation without requiring it on older captures.

    Coordinates are DOM sequence numbers only. They are never interpreted as
    grid columns, portal assignments, saved values or header semantics.
    """
    if "structure" not in table:
        return

    def fail():
        raise CaptureReadError("The editor returned malformed table observations.")

    def strings(value, limit):
        return isinstance(value, list) and len(value) <= limit and all(isinstance(x, str) for x in value)

    def optional_string(value):
        return value is None or isinstance(value, str)

    structure = table["structure"]
    if (not isinstance(structure, dict) or structure.get("schema_version") != "table-dom/v1"
            or not isinstance(structure.get("status"), str)
            or structure["status"] not in {"observed_native", "unsupported_non_native", "ambiguous_mixed"}
            or structure.get("mapping_verified") is not False or structure.get("complete") is not False
            or not isinstance(structure.get("tag"), str) or not optional_string(structure.get("role"))
            or not strings(structure.get("captions"), 100) or not strings(structure.get("limitations"), 100)):
        fail()
    rows = structure.get("rows")
    if not isinstance(rows, list) or len(rows) > 2000:
        fail()
    coordinates = {}
    previous_row = -1
    for row in rows:
        if (not isinstance(row, dict) or type(row.get("dom_order")) is not int
                or not previous_row < row["dom_order"] < 2000 or not isinstance(row.get("tag"), str)
                or not optional_string(row.get("role"))
                or not optional_string(row.get("row_group_tag"))
                or row.get("row_group_tag") not in {None, "thead", "tbody", "tfoot"}
                or not isinstance(row.get("cells"), list) or len(row["cells"]) > 100):
            fail()
        previous_row = row["dom_order"]
        previous_cell = -1
        for cell in row["cells"]:
            if (not isinstance(cell, dict) or type(cell.get("dom_order")) is not int
                    or not previous_cell < cell["dom_order"] < 100
                    or not isinstance(cell.get("tag"), str) or not isinstance(cell.get("text"), str)
                    or any(not optional_string(cell.get(key)) for key in
                           ("role", "id", "rowspan_attribute", "colspan_attribute", "scope_attribute",
                            "scope_property", "headers_attribute", "headers_property"))
                    or not strings(cell.get("header_ids"), 100)):
                fail()
            previous_cell = cell["dom_order"]
            for key, minimum, maximum in (("rowspan_property", 0, 65534), ("colspan_property", 1, 1000)):
                value = cell.get(key)
                if value is not None and (type(value) is not int or not minimum <= value <= maximum):
                    fail()
            for key in ("actions", "chips", "header_refs"):
                if (not isinstance(cell.get(key), list) or len(cell[key]) > 100
                        or any(not isinstance(item, dict) for item in cell[key])):
                    fail()
            if any(not isinstance(chip.get("text"), str) for chip in cell["chips"]):
                fail()
            for action in cell["actions"]:
                if (not isinstance(action.get("tag"), str) or not isinstance(action.get("label"), str)
                        or not optional_string(action.get("role")) or not optional_string(action.get("aria_disabled"))
                        or action.get("disabled") is not None and type(action["disabled"]) is not bool):
                    fail()
            coordinates[(row["dom_order"], cell["dom_order"])] = cell
    if table.get("rows") != [[cell["text"] for cell in row["cells"]] for row in rows]:
        fail()
    for cell in coordinates.values():
        if cell["header_ids"] != [ref.get("id") for ref in cell["header_refs"]]:
            fail()
        for ref in cell["header_refs"]:
            if ref.get("status") == "unresolved":
                if ref.get("row_dom_order") is not None or ref.get("cell_dom_order") is not None:
                    fail()
            elif ref.get("status") == "observed_header":
                if any(type(ref.get(key)) is not int for key in ("row_dom_order", "cell_dom_order")):
                    fail()
                target = coordinates.get((ref["row_dom_order"], ref["cell_dom_order"]))
                if target is None or target["tag"] != "th" or target.get("id") != ref["id"]:
                    fail()
            else:
                fail()


def _validate_observation(raw: Any, expected_url: str) -> dict:
    """Reject malformed extraction and redact unsafe control values defensively."""
    try:
        encoded = json.dumps(raw, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise CaptureReadError("The editor returned malformed observation data.") from None
    if len(encoded) > 16_000_000:
        raise CaptureReadError("The editor observation exceeded the safe in-memory size limit.")
    if not isinstance(raw, dict) or raw.get("href") != expected_url:
        raise CaptureSafetyError("The editor changed while it was being read. No new observation was accepted.")
    if raw.get("ready_state") != "complete" or raw.get("unavailable"):
        raise CaptureReadError("The editor is not ready. Wait for it to finish loading, then inspect again.")
    if any(not isinstance(raw.get(key), list) for key in _PAYLOAD_LISTS):
        raise CaptureReadError("The editor returned an incomplete observation structure.")
    result = {key: deepcopy(raw[key]) for key in _PAYLOAD_LISTS}
    if any(not isinstance(item, dict) for key in _PAYLOAD_LISTS if key != "limitations" for item in result[key]):
        raise CaptureReadError("The editor returned malformed observation entries.")
    if any(not isinstance(item, str) for item in result["limitations"]):
        raise CaptureReadError("The editor returned malformed limitation entries.")
    diagnostic_names = set()
    for item in result["discovery"]:
        if any(key in item for key in ("kind", "schema_version", "field_key", "count_cap", "counts",
                                      "status", "complete", "date_value_observed", "reasons", "segment_rejections")):
            if (not segmented_date_diagnostic_valid(item) or item["field_key"] in diagnostic_names
                    or len(diagnostic_names) >= len(DATE_NAMES)
                    or "segmented_date_segments_unresolved" not in result["limitations"]
                    or any(reason not in result["limitations"] for reason in item["reasons"])):
                raise CaptureReadError("The editor returned malformed segmented date diagnostics.")
            diagnostic_names.add(item["field_key"])

    def clean_fields(fields):
        if not isinstance(fields, list) or any(not isinstance(f, dict) for f in fields):
            raise CaptureReadError("The editor returned malformed field entries.")
        safe = []
        for field in fields:
            identifiers = " ".join(str(field.get(key, "")) for key in ("name", "id", "label", "autocomplete"))
            if not isinstance(field.get("type", ""), str):
                raise CaptureReadError("The editor returned malformed field types.")
            if field.get("type", "").lower() in {"password", "hidden", "file", "submit", "button", "reset", "image"} or _SECRET_FIELD.search(identifiers):
                result["limitations"].append("sensitive_control_omitted")
                continue
            if is_segmented_date_record(field) and not segmented_date_record_valid(field):
                raise CaptureReadError("The editor returned malformed segmented date observations.")
            safe.append(field)
        return safe

    for table in result["tables"]:
        _validate_table_structure(table)
        if 'observed_agents' in table:
            agents = table['observed_agents']
            if (not isinstance(agents, list) or not 0 < len(agents) <= 100
                    or any(not isinstance(a, dict) or set(a) != {'name', 'primary'}
                           or not isinstance(a['name'], str) or not a['name']
                           or type(a['primary']) is not bool for a in agents)):
                raise CaptureReadError('The listing agent observations are malformed.')

    result["fields"] = clean_fields(result["fields"])
    for dialog in result["dialogs"]:
        dialog["fields"] = clean_fields(dialog.get("fields"))
        if not isinstance(dialog.get("rich_text"), list):
            raise CaptureReadError("The editor returned malformed dialog entries.")
        if "room_groups" in dialog:
            groups = dialog["room_groups"]
            if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
                raise CaptureReadError("The editor returned malformed room groups.")
            for group in groups:
                if (not isinstance(group.get("title"), str) or type(group.get("index")) is not int
                        or group["index"] <= 0 or not isinstance(group.get("rich_text"), list)):
                    raise CaptureReadError("The editor returned malformed room groups.")
                group["fields"] = clean_fields(group.get("fields"))
    def scrub_string(value):
        def redact(match):
            raw = match.group(0)
            try:
                parsed = urlsplit("https://" + raw if raw.startswith("www.") else raw)
                if parsed.username or parsed.password or parsed.scheme not in ("", "https", "http"):
                    result["limitations"].append("url_credentials_and_queries_omitted")
                    return "[URL omitted]"
                if parsed.query or parsed.fragment:
                    result["limitations"].append("url_credentials_and_queries_omitted")
                    return re.split(r"[?#]", raw, maxsplit=1)[0]
            except ValueError:
                result["limitations"].append("url_credentials_and_queries_omitted")
                return "[URL omitted]"
            return raw
        return _URLISH.sub(redact, value)

    def scrub_values(value):
        if isinstance(value, str):
            return scrub_string(value)
        if isinstance(value, list):
            return [scrub_values(item) for item in value]
        if isinstance(value, dict):
            return {key: scrub_values(item) for key, item in value.items()}
        return value

    # Keep editor-link URLs untouched for strict rejection below. Redacting a
    # query then following the altered link would invent a navigation target.
    for key in _PAYLOAD_LISTS:
        if key not in {"editor_links", "limitations"}:
            result[key] = scrub_values(result[key])
    for link in result["editor_links"]:
        if "text" in link:
            link["text"] = scrub_values(link["text"])
    result["limitations"] = sorted(set(result["limitations"]))
    return result


class PartialHubCapture:
    """In-memory, tab-pinned observation session; never a qualified adapter.

    ``capture_current_page`` may be called again after the operator opens a room
    dialog or navigates to another known editor page for the same listing. No
    automatic merging takes place. ``get_result`` returns a detached copy of
    historical observations without accessing the browser.
    """
    is_live_ready = False

    def __init__(self, driver):
        self.driver = driver
        self._handle = None
        self._handles = None
        self._listing_id = None
        self._last_url = None
        self._pages: list[dict] = []
        self._stopped = False

    def _position(self):
        if self.driver is None:
            raise CaptureReadError("Open the Hub browser before inspecting the current page.")
        try:
            handle = self.driver.current_window_handle
            handles = tuple(sorted(self.driver.window_handles))
            url = self.driver.current_url
        except Exception:
            raise CaptureReadError("The browser session could not be read. No observation was accepted.") from None
        if not isinstance(handle, str) or not handle or handle not in handles or len(handles) != len(set(handles)):
            raise CaptureSafetyError("The current browser tab could not be identified safely.")
        route = parse_editor_url(url)
        return handle, handles, url, route

    def _check(self, expected_listing_id=None, expected_url=None):
        if expected_listing_id is not None and (not isinstance(expected_listing_id, str) or not _SAFE_ID.fullmatch(expected_listing_id)):
            raise CaptureSafetyError("A valid expected listing ID is required; an RXCU reference is not a verified listing ID.")
        position = self._position()
        handle, handles, url, route = position
        if self._handle is not None and (handle != self._handle or handles != self._handles):
            raise CaptureSafetyError("Browser tabs changed. Return to the intended editor and start a new partial capture session.")
        listing_id = expected_listing_id if expected_listing_id is not None else self._listing_id
        if listing_id is not None and route["listing_id"] != listing_id:
            raise CaptureSafetyError("The editor is for a different listing. No observation was accepted.")
        if self._listing_id is not None and route["listing_id"] != self._listing_id:
            raise CaptureSafetyError("The capture session is pinned to a different listing. Start a new session.")
        if expected_url is not None and url != expected_url:
            raise CaptureSafetyError("The editor redirected or changed during inspection. No new observation was accepted.")
        return position

    def capture_current_page(self, expected_listing_id: str | None = None) -> dict:
        """Read one current page/open dialog and append it to this partial session."""
        try:
            handle, handles, url, route = self._check(expected_listing_id)
            # Pin before evaluating so redirects/tabs appearing during the read
            # are checked against this exact session, including the first read.
            if self._handle is None:
                self._handle, self._handles, self._listing_id = handle, handles, route["listing_id"]
            try:
                raw = self.driver.execute_script(READ_PAGE_SCRIPT)
            except Exception:
                raise CaptureReadError("The editor could not be read. No new observation was accepted.") from None
            self._check(expected_listing_id, expected_url=url)
            data = _validate_observation(raw, url)
            safe_links = []
            rejected = 0
            for link in data["editor_links"]:
                try:
                    linked_route = parse_editor_url(link.get("url"))
                    if linked_route["listing_id"] != self._listing_id:
                        raise CaptureSafetyError("Different listing link")
                except CaptureSafetyError:
                    rejected += 1
                    continue
                safe_links.append({"url": link["url"], "text": str(link.get("text", "")), "page_key": linked_route["page_key"]})
            data["editor_links"] = safe_links
            data["rejected_editor_link_count"] = rejected
            if rejected:
                data["limitations"].append("unsafe_editor_links_omitted")
            data.update({"page_key": route["page_key"], "page_path": route["page_path"],
                         "observation_number": len(self._pages) + 1,
                         "observed_at": datetime.now(timezone.utc).isoformat(), "complete": False})
            data["integrity"] = {"algorithm": "sha256", "sha256": _digest(data)}
            self._check(expected_listing_id, expected_url=url)
            self._last_url = url
            self._pages.append(data)
            self._stopped = False
            return self.get_result()
        except CaptureError:
            self._stopped = True
            raise

    def capture_listing(self, expected_listing_id: str) -> dict:
        """Read reachable observed editor links, at most once per route this call.

        The current page remains the starting point. Navigation leaves the
        browser on the final observed page and is not silently reversed. Open
        dialogs cause a stop before navigation to avoid losing their context.
        Missing links/routes stay missing; this does not synthesize endpoint URLs.
        """
        if expected_listing_id is None:
            raise CaptureSafetyError("An expected listing ID is required for multi-page inspection.")
        self.capture_current_page(expected_listing_id)
        visited = {self._pages[-1]["page_key"]}
        while True:
            latest = self._pages[-1]
            if latest["dialogs"]:
                latest["limitations"].append("navigation_stopped_for_open_dialog")
                latest.pop("integrity")
                latest["integrity"] = {"algorithm": "sha256", "sha256": _digest(latest)}
                break
            candidates = [link for page in self._pages for link in page["editor_links"] if link["page_key"] not in visited]
            if not candidates:
                break
            target = candidates[0]
            try:
                self._check(expected_listing_id, expected_url=self._last_url)
                # No element click, form submission or guessed route is used.
                try:
                    self.driver.get(target["url"])
                except Exception:
                    raise CaptureReadError("Editor navigation did not finish. Check the current tab before inspecting again.") from None
                self._check(expected_listing_id, expected_url=target["url"])
                self.capture_current_page(expected_listing_id)
            except CaptureError:
                self._stopped = True
                raise
            visited.add(target["page_key"])
        return self.get_result()

    def get_result(self) -> dict:
        """Return immutable-by-copy partial history, without reading the browser."""
        pages = deepcopy(self._pages)
        observed = [key for key in EDITOR_PAGES if any(p["page_key"] == key for p in pages)]
        missing = [key for key in EDITOR_PAGES if key not in observed]
        media_fields = {key: [] for key in MEDIA_FIELDS}
        category_hints = {key: [] for key in ("parties", "documents", "notes", "rooms", "floorplans")}
        for page in pages:
            fields = list(page["fields"])
            for dialog in page["dialogs"]:
                fields.extend(dialog["fields"])
            for field in fields:
                identifiers = [str(field.get(key, "")).strip().casefold().replace(" ", "_") for key in ("name", "id", "label")]
                for key in MEDIA_FIELDS:
                    if key in identifiers:
                        media_fields[key].append({"observation_number": page["observation_number"], "field_dom_order": field.get("dom_order"), "in_dialog": field not in page["fields"]})
            if page["rich_text"] or any(d["rich_text"] for d in page["dialogs"]):
                media_fields["rich_text_description"].append({"observation_number": page["observation_number"], "mapping_verified": False})
            for section in page["sections"]:
                title = str(section.get("title", "")).casefold()
                for key, pattern in (("parties", r"\bpart(?:y|ies)\b"), ("documents", r"\bdocuments?\b"), ("notes", r"\bnotes?\b"), ("rooms", r"\brooms?\b"), ("floorplans", r"\bfloor\s*plans?\b")):
                    if re.search(pattern, title):
                        category_hints[key].append({"observation_number": page["observation_number"], "section_dom_order": section.get("dom_order"), "mapping_verified": False})
        blindspots = [{"code": code, "message": message} for code, message in _BASE_BLINDSPOTS]
        if missing:
            blindspots.append({"code": "editor_pages_missing", "message": "Some known editor pages have no observation in this session."})
        limitations = sorted({str(item) for page in pages for item in page["limitations"]})
        result = {
            "schema_version": SCHEMA_VERSION, "capture_kind": "partial_visible_dom",
            "listing_id": self._listing_id, "complete": False, "live_ready": False,
            "status": "stopped" if self._stopped else "partial",
            "identity": {"route_verified": bool(pages), "reference_verified": False,
                         "account_verified": False, "page_binding_verified": False},
            "pages": pages,
            "coverage": {"known_pages": list(EDITOR_PAGES), "observed_pages": observed,
                         "missing_pages": missing, "media_fields": media_fields,
                         "category_hints": category_hints, "exhaustive": False},
            "blindspots": blindspots, "limitations": limitations,
            "warning": "Partial, unverified observations only. Not a source backup and not safe to use for relisting.",
        }
        result["integrity"] = {"algorithm": "sha256", "sha256": _digest(result)}
        return result


def verify_capture_integrity(result: Any) -> bool:
    """Check accidental corruption only, not authenticity or completeness.

    The digest is unkeyed and can be recomputed by anyone. It is never evidence
    that observations are complete, current, private, authentic or Hub-verified.
    """
    try:
        if not isinstance(result, dict) or result.get("schema_version") != SCHEMA_VERSION or result.get("complete") is not False:
            return False
        data = deepcopy(result)
        receipt = data.pop("integrity")
        if receipt != {"algorithm": "sha256", "sha256": _digest(data)}:
            return False
        if not isinstance(data.get("pages"), list) or any(not isinstance(page, dict) for page in data["pages"]):
            return False
        for page in data["pages"]:
            receipt = page.pop("integrity")
            if receipt != {"algorithm": "sha256", "sha256": _digest(page)} or page.get("complete") is not False:
                return False
        return True
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        return False
