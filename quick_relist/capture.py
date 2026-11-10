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
storage; do not log it. No browser storage, cookies, network payloads, hidden
fields, password fields, OTP fields or unsanitized rich HTML are inspected.
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
    r"(?:/(?P<page>deal_particulars|media|portals))?/?$"
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
            return {"listing_id": match.group("listing_id"),
                    "page_key": match.group("page") or "details",
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
    const style = getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse';
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
  const textOf = el => {
    if (!el) return '';
    const parts = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let node;
    let length = 0;
    while ((node = walker.nextNode())) {
      const p = node.parentElement;
      if (!visible(p) || p.closest('script,style,noscript,template,input,textarea,select,[hidden],[aria-hidden="true"]')) continue;
      if (sensitiveContext(p)) continue;
      const text = node.textContent || '';
      parts.push(text);
      length += text.length;
      if (length > 100000) { limitations.add('dom_text_truncated'); break; }
    }
    return cut(parts.join(' ').replace(/\s+/g, ' ').trim());
  };
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
  const fieldsFor = root => limited(root.querySelectorAll('input,select,textarea'), 2000)
    .filter(safeControl).map((el, index) => ({
      dom_order: index, tag: el.tagName.toLowerCase(), type: el.type || '',
      id: cut(el.id), name: cut(el.name), label: labelFor(el), section: scopeFor(el),
      value: fieldValue(el), checked: ['checkbox', 'radio'].includes(el.type) ? el.checked : null,
      disabled: !!el.disabled, read_only: !!el.readOnly,
      selected_options: el.tagName === 'SELECT' ? limited(el.selectedOptions, 1000).map(o => ({value: cut(o.value), text: cut(o.textContent)})) : null,
      option_count: el.tagName === 'SELECT' ? el.options.length : null
    }));
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
  const richFor = root => {
    const found = limited(root.querySelectorAll('[contenteditable="true"],.rdw-editor-main,.rdw-editor'), 200).filter(visible);
    return found.filter(el => !found.some(other => other !== el && other.contains(el)))
      .filter(el => !sensitiveContext(el))
      .map((el, index) => ({dom_order: index, id: cut(el.id), name: cut(el.getAttribute('name')),
        label: labelFor(el), section: scopeFor(el), text: textOf(el),
        sanitized_html: richHTML(el), format_fidelity_verified: false}));
  };
  // Main/form scoping avoids unrelated account menus where the page exposes it.
  const mains = Array.from(document.querySelectorAll('main,[role="main"]')).filter(visible);
  const root = mains.length === 1 ? mains[0] : document.body;
  if (root === document.body) limitations.add('main_content_scope_unverified');
  if (!root) return {href, ready_state: document.readyState, unavailable: true};
  const dialogs = limited(document.querySelectorAll('[role="dialog"],[aria-modal="true"],dialog[open]'), 100)
    .filter(el => visible(el) && !sensitiveContext(el)).map((el, index) => ({dom_order: index, title: labelFor(el) || textOf(el.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]')),
      fields: fieldsFor(el), rich_text: richFor(el), text: textOf(el)}));
  const groups = new Set(root.querySelectorAll('fieldset,section,article,[role="group"]'));
  root.querySelectorAll('h1,h2,h3,h4,h5,h6,legend,[role="heading"]').forEach(h => { if (visible(h) && h.parentElement) groups.add(h.parentElement); });
  const sections = limited(groups, 400).filter(el => visible(el) && !sensitiveContext(el)).map((el, index) => ({dom_order: index,
    title: textOf(el.querySelector('legend,h1,h2,h3,h4,h5,h6,[role="heading"]')), text: textOf(el)}));
  const lists = limited(root.querySelectorAll('ul,ol,dl,[role="list"]'), 300).filter(visible).map((el, index) => ({dom_order: index,
    section: scopeFor(el), items: limited(el.children, 2000).filter(visible).map(textOf)}));
  const tables = limited(root.querySelectorAll('table,[role="table"],[role="grid"]'), 100).filter(visible).map((el, index) => ({dom_order: index,
    section: scopeFor(el), rows: limited(el.querySelectorAll('tr,[role="row"]'), 2000).filter(visible).map(row =>
      limited(row.querySelectorAll('th,td,[role="cell"],[role="gridcell"],[role="columnheader"]'), 100).filter(visible).map(textOf))}));
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
    return {dom_order: index, tag: el.tagName.toLowerCase(), kind_hint: hint,
      section, alt: cut(el.alt), title: cut(el.title), caption,
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
  return {href, ready_state: document.readyState, fields: fieldsFor(root), rich_text: richFor(root), dialogs,
    sections, lists, tables, media, editor_links, discovery, limitations: Array.from(limitations)};
})();
"""

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
    ("sensitive_controls_omitted", "Hidden, password, OTP, file and credential-like controls, browser secrets and recognized URL query/fragment data are deliberately excluded. Unlabelled secrets embedded in ordinary business prose cannot be exhaustively detected."),
    ("address_schema_unverified", "Address fields and autocomplete selections have not been mapped or validated against the required Hub schema."),
)
_PAYLOAD_LISTS = ("fields", "rich_text", "dialogs", "sections", "lists", "tables", "media", "editor_links", "discovery", "limitations")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


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
            safe.append(field)
        return safe

    result["fields"] = clean_fields(result["fields"])
    for dialog in result["dialogs"]:
        dialog["fields"] = clean_fields(dialog.get("fields"))
        if not isinstance(dialog.get("rich_text"), list):
            raise CaptureReadError("The editor returned malformed dialog entries.")
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
