"""Partial, read-only Hub page inspection. Never performs a listing mutation.

This is deliberately NOT the production listing adapter. It captures only visible
controls on one editor tab and cannot certify that a listing backup is complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
from pathlib import Path
from uuid import uuid4
import json
import hashlib
import os
import re
import stat
import shutil
import tempfile
import time
import zipfile

MANAGE_URL = "https://hub.remax.co.za/app/listings"
_EDITOR_PATH = re.compile(r"^/app/listings/edit/[^/]+(?:/(?:deal_particulars|media|portals))?/?$")



IDENTITY_SCRIPT = r"""
const wanted = arguments[0].toUpperCase();
const visible = e => {
  if (!e || !e.getClientRects().length) return false;
  for (let parent = e; parent; parent = parent.parentElement) {
    if (parent.getAttribute('aria-hidden') === 'true' || parent.hasAttribute('hidden')) return false;
    const s = getComputedStyle(parent);
    if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse' || Number(s.opacity || 1) === 0) return false;
  }
  return true;
};
const visibleText = heading => {
  const walker = document.createTreeWalker(heading, NodeFilter.SHOW_TEXT);
  let text = '', node;
  while ((node = walker.nextNode())) {
    let el = node.parentElement, allowed = true;
    while (el) {
      if (!visible(el)) { allowed = false; break; }
      if (el === heading) break;
      el = el.parentElement;
    }
    if (allowed && el === heading) text += node.nodeValue || '';
  }
  return text.trim();
};
const identityText = text => {
  const match = text.match(/^Edit listing\s*-\s*(RXCU-?[0-9]{3,20})\s*\(/i);
  return match ? match[1].toUpperCase().replace('-', '') : null;
};
const identity = e => identityText(visibleText(e));
const outerHeadings = [...document.querySelectorAll('h2')].filter(e => !e.closest('main'));
const outerVisible = outerHeadings.filter(visible).map(identity).filter(Boolean);
const innerEvidence = [...document.querySelectorAll('main h4')].filter(visible)
  .map(e => ({element:e, reference:identity(e)})).filter(item => item.reference);
let inner = innerEvidence.map(item => item.reference);
const positiveBox = e => [...e.getClientRects()].some(r =>
  Number.isFinite(r.width) && Number.isFinite(r.height) && r.width > 0 && r.height > 0);
const readableVisibleStyles = (e, transparentSelf=false) => {
  for (let p = e; p; p = p.parentElement) {
    if (p.hasAttribute('hidden') || p.getAttribute('aria-hidden') === 'true') return false;
    const s = getComputedStyle(p);
    if (typeof s.display !== 'string' || !s.display.trim() || s.display === 'none' || s.visibility !== 'visible') return false;
    if (!(transparentSelf && p === e)) {
      if (typeof s.opacity !== 'string' || !s.opacity.trim()) return false;
      const opacity = Number(s.opacity);
      if (!Number.isFinite(opacity) || opacity <= 0 || opacity > 1) return false;
    }
  }
  return true;
};
// The observed page can keep its duplicate outer H2 at self opacity:0 while
// the main H4 is visible. Qualify that exact direct-text shape as consistency
// evidence only. Never override wrong/duplicate visible outer identities.
const transparentDuplicate = e => {
  if (e.children.length !== 0 || e.hasAttribute('hidden') || e.hasAttribute('aria-hidden') ||
      !positiveBox(e)) return false;
  const self = getComputedStyle(e);
  if (self.display !== 'block' || self.visibility !== 'visible' || self.opacity !== '0') return false;
  return readableVisibleStyles(e, true);
};
// With no element children, textContent is direct text only. No arbitrary
// hidden descendants, attribute values or browser state are read. All four
// fields must still match; the main H4 remains fully visible and authoritative.
const outer = outerVisible.length ? outerVisible : outerHeadings.filter(transparentDuplicate)
  .map(e => identityText(e.textContent.trim())).filter(Boolean);
// In fallback mode, a sole matching main H4 must also be the observed direct-
// text leaf, with positive-area geometry and readable visible self/ancestor
// styles. Nested text cannot supply the authoritative reference. Keep raw wrong or
// duplicate identities intact so stricter eligibility cannot hide a conflict.
if (!outerVisible.length && innerEvidence.length === 1 && inner[0] === wanted &&
    (innerEvidence[0].element.children.length !== 0 || !positiveBox(innerEvidence[0].element) ||
     !readableVisibleStyles(innerEvidence[0].element))) inner = [];
// outer_count/matches describe qualifying identity evidence, not visibility.
// Callers must still require both exact counts/matches and their binding guards.
return {outer_count: outer.length, inner_count: inner.length,
        outer_matches: outer.length === 1 && outer[0] === wanted,
        inner_matches: inner.length === 1 && inner[0] === wanted};
"""


ROW_BINDING_SCRIPT = r"""
const [row, combo, menu, link, wanted] = arguments;
if (!row.isConnected || !row.contains(combo) || !menu.isConnected || !menu.contains(link)) return false;
const textWithinRow = p => {
  const walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
  let text = '', node;
  while ((node = walker.nextNode())) {
    let el = node.parentElement, allowed = true;
    while (el && el !== row) {
      const style = getComputedStyle(el);
      if (el.hasAttribute('hidden') || el.getAttribute('aria-hidden') === 'true' || style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || Number(style.opacity || 1) === 0) { allowed = false; break; }
      el = el.parentElement;
    }
    if (allowed && el === row) text += node.nodeValue || '';
  }
  return text.trim();
};
const refs = [...row.querySelectorAll('p')].map(p => textWithinRow(p).match(/^Ref:\s*(RXCU-?[0-9]{3,20})$/i)).filter(Boolean).map(m => m[1].toUpperCase().replace('-', ''));
return refs.length === 1 && refs[0] === wanted.toUpperCase();
"""


SOURCE_CONTEXT_SCRIPT = r"""
const [root, row, wanted] = arguments;
if (root !== document.documentElement || !row.isConnected || row.ownerDocument !== document) return false;
const references = candidate => {
  if (!candidate.isConnected || !candidate.getClientRects().length) return [];
  for (let ancestor = candidate.parentElement; ancestor; ancestor = ancestor.parentElement) {
    const style = getComputedStyle(ancestor);
    if (ancestor.hasAttribute('hidden') || style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || Number(style.opacity || 1) === 0) return [];
  }
  const textWithinRow = p => {
    const walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
    let text = '', node;
    while ((node = walker.nextNode())) {
      let el = node.parentElement, allowed = true;
      while (el) {
        const style = getComputedStyle(el);
        if (el.hasAttribute('hidden') || el.getAttribute('aria-hidden') === 'true' || style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || Number(style.opacity || 1) === 0) { allowed = false; break; }
        if (el === candidate) break;
        el = el.parentElement;
      }
      if (allowed && el === candidate) text += node.nodeValue || '';
    }
    return text.trim();
  };
  return [...candidate.querySelectorAll('p')].map(p => textWithinRow(p).match(/^Ref:\s*(RXCU-?[0-9]{3,20})$/i)).filter(Boolean).map(m => m[1].toUpperCase().replace('-', ''));
};
// The old actions menu may have closed. It is not a recovery precondition.
// Ignore accessibility hiding ABOVE the row caused by that menu, as at click.
const refs = references(row);
const matches = [...document.querySelectorAll('[role="row"]')].filter(candidate => {
  const values = references(candidate);
  return values.length === 1 && values[0] === wanted;
});
return refs.length === 1 && refs[0] === wanted && matches.length === 1 && matches[0] === row;
"""


@dataclass
class _EditorOpening:
    """Session-only evidence for one attempted, already-verified link click."""
    driver: object
    reference: str
    listing_id: str
    source_handle: str
    source_handles: frozenset[str]
    source_url: str
    source_root: object
    source_row: object
    target_url: str
    target_handle: str | None = None
    route_observed: bool = False
    blocked: bool = False


class DownloadOutcomeUnknown(RuntimeError):
    """A download may have started. Files in directory must be checked first."""
    def __init__(self, directory):
        super().__init__("The photo-pack outcome is uncertain. Check the private download folder before trying again.")
        self.directory = str(directory)


class PreviousDownloadExists(RuntimeError):
    """A prior download needs review before another attempt for this listing."""
    def __init__(self, directory):
        super().__init__("A previous photo-pack attempt needs review before another download.")
        self.directory = str(directory)


class ReadOnlyHub:
    """Visible Chrome session; user signs in and navigates the Hub manually.

    Creating this class does not launch a browser. Only editor URLs on the exact
    Hub host may be inspected; a per-tab extraction is explicitly incomplete.
    """
    def __init__(self, driver=None):
        self.driver = driver
        self.editor_bindings = {}
        self._pending_editor: _EditorOpening | None = None
        self._managed_default_driver = None
        self._profile_path = None
        self._profile_identity = None
        self._browser_closed = False

    def open(self):
        if self.driver is not None or self._profile_path is not None or self._pending_editor is not None:
            raise RuntimeError("A browser session is already open or awaiting cleanup")
        try:
            from selenium import webdriver
        except ImportError as exc:
            raise RuntimeError("Install the browser extra to open Chrome: pip install 'quick-relist[browser]'") from exc
        from .media import _private_directory
        profile = Path(tempfile.gettempdir()) / ("quick-relist-browser-" + uuid4().hex)
        _private_directory(profile)
        info = profile.lstat()
        self._profile_path, self._profile_identity = profile, (info.st_dev, info.st_ino)
        self._browser_closed = False
        options = webdriver.ChromeOptions()
        options.add_argument("--user-data-dir=" + str(profile))
        options.add_experimental_option("prefs", {"credentials_enable_service": False,
                                                   "profile.password_manager_enabled": False})
        # A fresh owner-only disposable profile avoids the implicit incognito
        # download-context failure observed on Windows. Never reuse the user's
        # normal profile or saved credentials. Sign-in remains manual.
        try:
            self.driver = webdriver.Chrome(options=options)
            self._managed_default_driver = self.driver
            self.driver.get(MANAGE_URL)
        except Exception:
            raise RuntimeError("The dedicated Hub browser could not be opened. Close any new browser window and restart Quick Relist before trying again. Protected temporary session files may remain.") from None

    def search_reference(self, rxcu):
        """Submit a read-only Manage Listings search; do not infer a match.

        Hub filters may hide listings, so an empty result never proves absence.
        The exact displayed search prompt and button must be unique and visible.
        """
        if self._pending_editor is not None:
            raise RuntimeError("An editor opening is unresolved. Recheck it before starting another search, or safely close and restart the browser session.")
        if self.driver is None:
            raise RuntimeError("Open the Hub browser first")
        from .core import normalize_rxcu
        reference = normalize_rxcu(rxcu)
        from selenium.webdriver.common.by import By
        original = self.driver.current_window_handle
        matches = []
        for handle in self.driver.window_handles:
            self.driver.switch_to.window(handle)
            parsed = urlsplit(self.driver.current_url)
            if parsed.scheme == "https" and parsed.hostname == "hub.remax.co.za" and parsed.port in (None, 443) and parsed.path.rstrip("/") == "/app/listings":
                matches.append(handle)
        if len(matches) != 1:
            self.driver.switch_to.window(original)
            raise ValueError("Open exactly one Manage Listings tab in the Hub browser first")
        self.driver.switch_to.window(matches[0])
        # Both strings were observed in Hub: the visible label and input placeholder.
        prompts = {"Search fusion ref, listing ID or address", "Enter fusion ref, listing ID or address"}
        candidates = self.driver.find_elements(By.XPATH, "//input[@placeholder or @aria-label or @id]")
        inputs = []
        for el in candidates:
            if not el.is_displayed() or not el.is_enabled():
                continue
            attrs = (el.get_attribute("placeholder"), el.get_attribute("aria-label"))
            if any(value in prompts for value in attrs):
                inputs.append(el)
        if len(inputs) != 1:
            raise ValueError("The Hub search field could not be identified uniquely; check the page layout")
        buttons = [el for el in self.driver.find_elements(By.XPATH, "//button")
                   if el.is_displayed() and el.is_enabled() and el.text.strip() == "Get listings"]
        if len(buttons) != 1:
            raise ValueError("The Get listings button could not be identified uniquely; check the page layout")
        inputs[0].clear()
        # Hub displays the Fusion reference with a hyphen.
        inputs[0].send_keys("RXCU-" + reference[4:])
        buttons[0].click()
        return {"searched": True, "warning": "Review the results and filters manually; this does not verify a match."}

    def open_listing_editor(self, rxcu, *, timeout=15.0, poll_interval=0.1):
        """Open the exact matching result's Edit Listing link, never Archive.

        The search must already be submitted. Ambiguous results, controls,
        links, new tabs, or headings stop the operation without a blind retry.
        Account ownership and complete source coverage remain unverified.
        """
        if self._pending_editor is not None:
            raise RuntimeError("An editor opening is unresolved. Recheck the existing opening; do not open another editor.")
        if self.driver is None:
            raise RuntimeError("Open the Hub browser first")
        if not (0.01 <= poll_interval <= 2 and 0.05 <= timeout <= 120):
            raise ValueError("Invalid editor wait limits")
        from selenium.webdriver.common.by import By
        from .capture import parse_editor_url
        from .core import normalize_rxcu
        reference = normalize_rxcu(rxcu)
        parsed = urlsplit(self.driver.current_url)
        if (parsed.scheme != "https" or parsed.hostname != "hub.remax.co.za" or parsed.port not in (None, 443)
                or parsed.username is not None or parsed.password is not None or parsed.path.rstrip("/") != "/app/listings"
                or parsed.query or parsed.fragment):
            raise ValueError("Open the Hub Manage Listings results tab first")
        def matching_rows():
            matches = []
            for row in self.driver.find_elements(By.CSS_SELECTOR, '[role="row"]'):
                if not row.is_displayed():
                    continue
                refs = []
                for p in row.find_elements(By.TAG_NAME, "p"):
                    if not p.is_displayed():
                        continue
                    match = re.fullmatch(r"Ref:\s*(RXCU-?[0-9]{3,20})", p.text.strip(), re.IGNORECASE)
                    if match:
                        refs.append(normalize_rxcu(match.group(1)))
                if refs == [reference]:
                    matches.append(row)
            return matches
        rows = matching_rows()
        if len(rows) != 1:
            raise ValueError("The search does not show one unique exact RXCU result. Check filters and results.")
        controls = [e for e in rows[0].find_elements(By.CSS_SELECTOR, '[role="combobox"]') if e.is_displayed() and e.is_enabled()]
        if len(controls) != 1:
            raise ValueError("The result's Select control was not uniquely available")
        if any(menu.is_displayed() for menu in self.driver.find_elements(By.CSS_SELECTOR, '[role="listbox"]')):
            raise ValueError("Close any existing open menu before selecting this result")
        try:
            controls[0].click()
        except Exception:
            raise RuntimeError("The Select menu outcome is uncertain. Inspect Hub before continuing.") from None
        deadline = time.monotonic() + timeout
        links = []
        while time.monotonic() < deadline:
            links = []
            controlled = controls[0].get_attribute("aria-controls") or controls[0].get_attribute("aria-owns")
            expanded = controls[0].get_attribute("aria-expanded")
            menus = [m for m in self.driver.find_elements(By.CSS_SELECTOR, '[role="listbox"]') if m.is_displayed()]
            if not controlled or any(c.isspace() for c in controlled) or expanded != "true" or len(menus) != 1 or menus[0].get_attribute("id") != controlled:
                time.sleep(poll_interval)
                continue
            for option in menus[0].find_elements(By.CSS_SELECTOR, '[role="option"]'):
                if not option.is_displayed():
                    continue
                for link in option.find_elements(By.TAG_NAME, "a"):
                    if link.is_displayed() and link.is_enabled() and link.text.strip() == "Edit Listing":
                        try:
                            route = parse_editor_url(link.get_attribute("href"))
                        except ValueError:
                            continue
                        if route["page_key"] == "details" and re.fullmatch(r"[0-9]+", route["listing_id"]):
                            links.append((link, route["listing_id"]))
            if links:
                break
            time.sleep(poll_interval)
        if len(links) != 1:
            raise ValueError("A unique Edit Listing link and matching result could not be verified")
        link, listing_id = links[0]
        # Recheck ownership immediately before the single read-only navigation.
        current_menus = [m for m in self.driver.find_elements(By.CSS_SELECTOR, '[role="listbox"]') if m.is_displayed()]
        current_control = controls[0].get_attribute("aria-controls") or controls[0].get_attribute("aria-owns")
        if (controls[0].get_attribute("aria-expanded") != "true" or current_control != controlled
                or len(current_menus) != 1 or current_menus[0].get_attribute("id") != controlled
                or not link.is_displayed() or not link.is_enabled()):
            raise ValueError("The selected result menu changed. Inspect Hub before continuing.")
        # MUI hides the row's ancestor from accessibility while its menu is
        # open. Recheck the row's own exact reference, ownership and the link
        # within the same controlled menu without exposing address text.
        if self.driver.execute_script(ROW_BINDING_SCRIPT, rows[0], controls[0], current_menus[0], link, reference) is not True:
            raise ValueError("The selected row or menu changed. Inspect Hub before continuing.")
        target_url = link.get_attribute("href")
        current_route = parse_editor_url(target_url)
        if current_route["page_key"] != "details" or current_route["listing_id"] != listing_id:
            raise ValueError("The editor link changed. Inspect Hub before continuing.")
        source_handle = self.driver.current_window_handle
        old_handles = frozenset(self.driver.window_handles)
        source_url = self.driver.current_url
        source_root = self.driver.execute_script("return document.documentElement;")
        # Store the intent BEFORE clicking. A thrown click can still have opened
        # a tab, so neither an exception nor a timeout permits a second click.
        intent = _EditorOpening(self.driver, reference, listing_id, source_handle,
                                old_handles, source_url, source_root, rows[0], target_url)
        if (source_url != parsed.geturl() or source_handle not in old_handles
                or self.driver.execute_script(SOURCE_CONTEXT_SCRIPT, source_root, rows[0], reference) is not True
                or self.driver.current_window_handle != source_handle
                or frozenset(self.driver.window_handles) != old_handles or self.driver.current_url != source_url
                or link.get_attribute("href") != target_url):
            raise ValueError("The source results changed before opening. No editor link was clicked.")
        self._pending_editor = intent
        self.editor_bindings.pop(reference, None)
        try:
            link.click()  # Exactly once. Never click Archive or retry an uncertain click.
        except Exception:
            raise RuntimeError("Opening the editor is uncertain. Use Recheck opened editor (read only) for this selected job; do not open it again.") from None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.recheck_opened_editor(reference)
            if not result.get("editor_pending"):
                return result
            time.sleep(poll_interval)
        raise RuntimeError("The editor is still unverified. Leave its tabs and results unchanged, then use Recheck opened editor (read only) for this selected job.")

    def editor_open_state(self, rxcu=None):
        """In-memory UI eligibility only; browser checks run in the worker.

        Observed conflicts are sticky. No reset, forced binding or cross-session
        recovery is supported. A different selection does not consume intent.
        """
        intent = self._pending_editor
        if intent is None:
            return "none"
        if self.driver is not intent.driver or self.driver is None:
            intent.blocked = True
        if intent.blocked:
            return "blocked"
        from .core import normalize_rxcu
        try:
            return "pending" if normalize_rxcu(rxcu) == intent.reference else "other"
        except (TypeError, ValueError):
            return "other"

    def recheck_opened_editor(self, rxcu):
        """Read one outstanding opening, never click or navigate a URL.

        Only an additional tab from this session's verified click can bind.
        A blank/loading target remains pending; any observed context, route or
        qualifying title conflict blocks this intent until confirmed close.
        """
        state = self.editor_open_state(rxcu)
        if state != "pending":
            raise RuntimeError("There is no safely recheckable opening for this selected job. Select the original job, or safely close and restart the browser session.")
        intent = self._pending_editor
        try:
            driver = intent.driver
            handles = frozenset(driver.window_handles)
            new_handles = handles - intent.source_handles
            if (not intent.source_handles.issubset(handles) or len(new_handles) > 1
                    or (intent.target_handle is not None and new_handles != {intent.target_handle})):
                raise ValueError("Changed tabs")
            # Pin the first observed additional tab, even while it is blank.
            if intent.target_handle is None and new_handles:
                intent.target_handle = next(iter(new_handles))
            if driver.current_window_handle not in {intent.source_handle, intent.target_handle}:
                raise ValueError("Changed active context")
            self._check_editor_source(intent, handles)
            if intent.target_handle is None:
                return {"editor_pending": True, "complete": False}
            driver.switch_to.window(intent.target_handle)
            url = driver.current_url
            if url == "about:blank" and not intent.route_observed:
                self._editor_headings_pending(driver.execute_script(IDENTITY_SCRIPT, intent.reference))
                self._check_editor_target(intent, handles, url)
                return {"editor_pending": True, "complete": False}
            if url != intent.target_url:
                raise ValueError("Changed editor route")
            intent.route_observed = True
            result = self._read_editor_identity(intent.reference, intent.listing_id)
            self._check_editor_target(intent, handles, url)
            # Missing headings can load later. A wrong or duplicate qualifying
            # title is a conflict, not a transient loading condition.
            pending_headings = self._editor_headings_pending(result)
            self._check_editor_source(intent, handles)
            driver.switch_to.window(intent.target_handle)
            self._check_editor_target(intent, handles, url)
            if pending_headings:
                return {"editor_pending": True, "complete": False}
            # Repeat the guarded identity read after the source round trip.
            # Headings can disappear while loading even after an earlier match.
            final_identity = self._read_editor_identity(intent.reference, intent.listing_id)
            self._check_editor_target(intent, handles, url)
            if self._editor_headings_pending(final_identity):
                return {"editor_pending": True, "complete": False}
            identity = self._verified_editor_identity(final_identity)
            self.editor_bindings[intent.reference] = {"listing_id": intent.listing_id, "handle": intent.target_handle}
            self._pending_editor = None
            return {**identity, "row_link_verified": True, "listing_id": intent.listing_id, "complete": False}
        except Exception:
            intent.blocked = True
            raise RuntimeError("The opened editor cannot be safely matched in this session. Do not retry opening it. Safely close the dedicated browser and restart; no listing was changed.") from None

    @staticmethod
    def _editor_headings_pending(result):
        if not isinstance(result, dict):
            raise ValueError("Unreadable editor identity")
        for prefix in ("outer", "inner"):
            count, matches = result.get(prefix + "_count"), result.get(prefix + "_matches")
            if (type(count) is not int or count not in (0, 1)
                    or type(matches) is not bool or matches != (count == 1)):
                raise ValueError("Conflicting editor identity")
        return result["outer_count"] == 0 or result["inner_count"] == 0

    def _check_editor_source(self, intent, handles):
        driver = intent.driver
        if self.driver is not driver or frozenset(driver.window_handles) != handles:
            raise ValueError("Changed browser context")
        driver.switch_to.window(intent.source_handle)
        if (driver.current_url != intent.source_url
                or driver.execute_script(SOURCE_CONTEXT_SCRIPT, intent.source_root, intent.source_row, intent.reference) is not True
                or driver.current_window_handle != intent.source_handle or driver.current_url != intent.source_url
                or frozenset(driver.window_handles) != handles):
            raise ValueError("Changed source context")

    def _check_editor_target(self, intent, handles, url):
        driver = intent.driver
        if (self.driver is not driver or driver.current_window_handle != intent.target_handle
                or frozenset(driver.window_handles) != handles or driver.current_url != url):
            raise ValueError("Changed target context")

    @staticmethod
    def _is_editor_url(url):
        try:
            parsed = urlsplit(url)
            return (parsed.scheme == "https" and parsed.hostname == "hub.remax.co.za"
                    and parsed.port in (None, 443) and bool(_EDITOR_PATH.fullmatch(parsed.path)))
        except ValueError:
            return False

    def select_editor_tab(self):
        """Find one editor tab. Refuse ambiguity instead of guessing a listing."""
        if self.driver is None:
            raise RuntimeError("Open the Hub browser first")
        original = self.driver.current_window_handle
        matches = []
        for handle in self.driver.window_handles:
            self.driver.switch_to.window(handle)
            if self._is_editor_url(self.driver.current_url):
                matches.append(handle)
        if len(matches) != 1:
            self.driver.switch_to.window(original)
            raise ValueError("Open exactly one Hub listing editor tab before inspecting")
        self.driver.switch_to.window(matches[0])

    def _read_editor_identity(self, expected_rxcu, expected_listing_id):
        """Read exact editor titles and the current numeric route.

        This does not prove which account owns the record or independently bind
        the route to a results-row link. The main H4 must be fully visible; the
        outer H2 may be the qualified transparent direct-text duplicate. This
        does not claim two visible titles. No address text leaves the browser.
        """
        if self.driver is None:
            raise RuntimeError("Open the Hub browser first")
        from .capture import parse_editor_url
        from .core import normalize_rxcu
        reference = normalize_rxcu(expected_rxcu)
        if not isinstance(expected_listing_id, str) or not re.fullmatch(r"[0-9]+", expected_listing_id):
            raise ValueError("A numeric listing ID from the editor is required")
        try:
            handle = self.driver.current_window_handle
            handles = tuple(sorted(self.driver.window_handles))
            url = self.driver.current_url
            if parse_editor_url(url)["listing_id"] != expected_listing_id:
                raise ValueError("The current editor does not match the intended listing")
            result = self.driver.execute_script(IDENTITY_SCRIPT, reference)
            if (self.driver.current_window_handle != handle or tuple(sorted(self.driver.window_handles)) != handles
                    or self.driver.current_url != url):
                raise ValueError("The editor changed during identity verification")
        except ValueError:
            raise
        except Exception:
            raise ValueError("The editor identity could not be verified") from None
        if not isinstance(result, dict):
            raise ValueError("The editor identity could not be verified")
        return result

    def verify_editor_reference(self, expected_rxcu, expected_listing_id):
        """Require exact main-title/outer-title identity and a stable numeric route."""
        result = self._read_editor_identity(expected_rxcu, expected_listing_id)
        return self._verified_editor_identity(result)

    @staticmethod
    def _verified_editor_identity(result):
        if (not isinstance(result, dict) or result.get("outer_count") != 1 or result.get("inner_count") != 1
                or result.get("outer_matches") is not True or result.get("inner_matches") is not True):
            raise ValueError("The editor headings do not uniquely match the selected RXCU")
        return {"editor_reference_matches": True, "route_matches": True,
                "account_verified": False, "row_link_verified": False, "complete": False}

    def download_photo_pack(self, expected_rxcu, expected_listing_id, download_root, *, timeout=120.0, poll_interval=0.25):
        """Request one photo-pack ZIP in a new private folder; never a Hub write.

        The exact editor reference and route are checked immediately before the
        single click. The result is NOT a complete/verified original backup.
        If outcome is uncertain, leave every file in place and do not retry.
        """
        if self.driver is None:
            raise RuntimeError("Open the Hub browser first")
        if not (0.01 <= poll_interval <= 5 and 0.05 <= timeout <= 600):
            raise ValueError("Invalid download wait limits")
        from selenium.webdriver.common.by import By
        from .capture import parse_editor_url
        from .core import normalize_rxcu
        from .media import _prepare_root, _private_directory, _private_file
        reference = normalize_rxcu(expected_rxcu)
        binding = self.editor_bindings.get(reference)
        if (not isinstance(binding, dict) or binding.get("listing_id") != expected_listing_id
                or binding.get("handle") != self.driver.current_window_handle):
            raise ValueError("Open the exact matching editor from the search result before downloading")
        self.verify_editor_reference(reference, expected_listing_id)
        handle = self.driver.current_window_handle
        handles = tuple(sorted(self.driver.window_handles))
        url = self.driver.current_url
        if parse_editor_url(url)["page_key"] != "media":
            raise ValueError("Open the intended listing's Media editor first")
        buttons = [element for element in self.driver.find_elements(By.TAG_NAME, "button")
                   if element.is_displayed() and element.is_enabled()
                   and " ".join(element.text.split()).casefold() == "download photo pack"
                   and element.get_attribute("type") == "button"]
        if len(buttons) != 1:
            raise ValueError("The photo-pack button was not uniquely available")
        root = Path(os.path.abspath(download_root))
        _prepare_root(root)
        from .downloads import download_lock
        with download_lock(root):
            # Browser download routing is session-wide. One unresolved attempt
            # blocks all later attempts, including different listings and restarts.
            marker = root / "download-review-required.json"
            if marker.exists() or marker.is_symlink():
                # Never silently retry after a previous attempt, even after restart.
                raise PreviousDownloadExists(root)
            directory = root / ("download-" + uuid4().hex)
            _private_directory(directory)
            from .photo_downloads import PhotoDownloadEvents
            managed_default = self.driver is self._managed_default_driver
            with PhotoDownloadEvents(self.driver, directory, reference, use_default_context=managed_default) as events:
                if not events.supported:
                    try:
                        # Only our own disposable profile is known to be the
                        # dedicated default context. External sessions require
                        # a verified explicit context; never guess one.
                        settings = {"behavior": "allow", "downloadPath": str(directory), "eventsEnabled": False}
                        target = self.driver.execute_cdp_cmd("Target.getTargetInfo", {}).get("targetInfo", {})
                        context = target.get("browserContextId")
                        if (target.get("type") != "page" or target.get("targetId") != handle
                                or (not managed_default and (not isinstance(context, str) or not context))):
                            raise ValueError("The private browser context could not be identified")
                        if not managed_default:
                            settings["browserContextId"] = context
                        self.driver.execute_cdp_cmd("Browser.setDownloadBehavior", settings)
                    except Exception:
                        raise RuntimeError("The browser could not prepare a private download folder; no click was made") from None
                self.verify_editor_reference(reference, expected_listing_id)
                if (self.driver.current_window_handle != handle or tuple(sorted(self.driver.window_handles)) != handles
                        or self.driver.current_url != url):
                    raise ValueError("The editor changed before the download; no click was made")
                try:
                    # Durable intention prevents blind retries after restart.
                    with _private_file(marker) as receipt:
                        receipt.write(json.dumps({"version": 1, "directory": directory.name, "state": "attempted"}).encode("utf-8"))
                        receipt.flush()
                        os.fsync(receipt.fileno())
                    from .downloads import _sync_directory
                    _sync_directory(root)
                except FileExistsError:
                    raise PreviousDownloadExists(root) from None
                except Exception:
                    raise RuntimeError("The download could not be recorded safely; no click was made") from None
                if events.supported and not events.arm():
                    raise RuntimeError("The browser download state changed before the request. No click was made; review the download folder.")
                try:
                    buttons[0].click()  # Exactly once; never retry an uncertain click.
                except Exception:
                    raise DownloadOutcomeUnknown(directory) from None
                result = self._wait_photo_pack(directory, reference, expected_listing_id, handle, handles, url,
                                               events if events.supported else None, timeout, poll_interval)
            # Cleanup/late callback uncertainty invalidates earlier attribution.
            event_guid = result.pop("_event_guid", None)
            archive_hash = result.pop("_archive_sha256", None)
            archive_size = result.pop("_archive_size", None)
            if event_guid and archive_hash and not events.failed:
                result["attribution_verified"] = True
                result["download_evidence"] = {"method": "browser_frame_guid_and_reference", "guid": event_guid,
                    "rxcu": reference, "listing_id": expected_listing_id, "completion_observed": True,
                    "archive_sha256": archive_hash, "archive_size": archive_size,
                    "account_verified": False, "exact_click_causality_verified": False}
                result["warning"] = "Browser events matched this completed ZIP to the selected editor frame and RXCU filename. Account, original resolution, contents, order and completeness remain unverified."
            return result

    def _wait_photo_pack(self, directory, reference, listing_id, handle, handles, url, events, timeout, poll_interval):
        """Preserve an uncertain file; only matched completed GUIDs are assigned."""
        from .media import _publish_no_replace
        deadline = time.monotonic() + timeout
        stable, stable_count, candidate = None, 0, None
        guid_pattern = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
        while time.monotonic() < deadline:
            try:
                if (self.driver.current_window_handle != handle or tuple(sorted(self.driver.window_handles)) != handles
                        or self.driver.current_url != url):
                    raise DownloadOutcomeUnknown(directory)
                completion = events.poll() if events is not None else None
                entries = list(directory.iterdir())
                if len(entries) > 10:
                    raise DownloadOutcomeUnknown(directory)
                partial = [entry for entry in entries if entry.name.lower().endswith(".crdownload")]
                finished = [entry for entry in entries if entry.suffix.lower() == ".zip"
                            or (events is not None and guid_pattern.fullmatch(entry.name))]
                if len(finished) > 1 or any(entry not in partial and entry not in finished for entry in entries):
                    raise DownloadOutcomeUnknown(directory)
                if not partial and len(finished) == 1:
                    path = finished[0]
                    info = path.lstat()
                    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
                            or getattr(info, "st_file_attributes", 0) & 0x400 or not 0 < info.st_size <= 256 * 1024 * 1024):
                        raise DownloadOutcomeUnknown(directory)
                    signature = (path.name, info.st_size, info.st_mtime_ns)
                    stable_count = stable_count + 1 if signature == stable else 1
                    stable = signature
                    if stable_count >= 3:
                        if not zipfile.is_zipfile(path):
                            raise DownloadOutcomeUnknown(directory)
                        candidate = {"candidate_path": str(path), "directory": str(directory), "complete": False,
                                     "attribution_verified": False,
                                     "warning": "A ZIP appeared, but its listing association is unverified. Review it before manual import. Further requests remain paused."}
                        if completion is not None and path.name == completion.guid and info.st_size == completion.received_bytes:
                            # Bind the completion to actual bytes, not a stale
                            # pathname/size. Import must verify this digest too.
                            from .downloads import _stamp
                            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
                            with os.fdopen(descriptor, "rb") as stream:
                                opened = os.fstat(stream.fileno())
                                if not stat.S_ISREG(opened.st_mode) or _stamp(opened)[:4] != _stamp(info)[:4]:
                                    raise DownloadOutcomeUnknown(directory)
                                digest, count = hashlib.sha256(), 0
                                while chunk := stream.read(64 * 1024):
                                    count += len(chunk)
                                    if count > completion.received_bytes:
                                        raise DownloadOutcomeUnknown(directory)
                                    digest.update(chunk)
                                if count != completion.received_bytes or _stamp(os.fstat(stream.fileno())) != _stamp(opened):
                                    raise DownloadOutcomeUnknown(directory)
                            self.verify_editor_reference(reference, listing_id)
                            if events.poll() == completion and not events.failed:
                                if _stamp(path.lstat()) != _stamp(info):
                                    raise DownloadOutcomeUnknown(directory)
                                renamed = directory / ("photo-pack-" + completion.guid + ".zip")
                                _publish_no_replace(path, renamed)
                                if _stamp(renamed.lstat())[:4] != _stamp(info)[:4]:
                                    raise DownloadOutcomeUnknown(directory)
                                candidate["candidate_path"] = str(renamed)
                                candidate["_event_guid"] = completion.guid
                                candidate["_archive_sha256"] = digest.hexdigest()
                                candidate["_archive_size"] = count
                                return candidate
                        if events is None or events.failed:
                            return candidate
                else:
                    stable, stable_count, candidate = None, 0, None
            except DownloadOutcomeUnknown:
                raise
            except Exception:
                raise DownloadOutcomeUnknown(directory) from None
            time.sleep(poll_interval)
        if candidate is not None:
            return candidate  # File observed, event outcome unknown; never retry.
        raise DownloadOutcomeUnknown(directory)

    def inspect_current_editor_page(self):
        if self.driver is None:
            raise RuntimeError("Open the Hub browser first")
        self.select_editor_tab()
        # Reuse the stricter collector rather than a second, less-filtered DOM
        # extraction path. The output remains incomplete and is not saved here.
        from .capture import PartialHubCapture
        return PartialHubCapture(self.driver).capture_current_page()

    def close(self):
        if self._pending_editor is not None and self.driver is not self._pending_editor.driver:
            self._pending_editor.blocked = True
            raise RuntimeError("The original Hub browser session cannot be confirmed closed. Close every Quick Relist browser manually and restart Quick Relist.")
        if self.driver is not None:
            # Preserve the driver and bindings if quit has an uncertain outcome.
            # Recovery must not mistake a failed close for a stopped browser.
            try:
                self.driver.quit()
            except Exception:
                raise RuntimeError("The Hub browser could not be confirmed closed. Close every Quick Relist browser manually, restart Quick Relist, then review the previous download.") from None
            self.driver = None
            self._managed_default_driver = None
            self.editor_bindings.clear()
            self._pending_editor = None
            self._browser_closed = True
        if self._profile_path is not None and self._browser_closed:
            try:
                try:
                    info = self._profile_path.lstat()
                except FileNotFoundError:
                    self._profile_path = self._profile_identity = None
                    return
                if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400
                        or (info.st_dev, info.st_ino) != self._profile_identity):
                    raise ValueError("The disposable profile path changed")
                shutil.rmtree(self._profile_path)
                self._profile_path = self._profile_identity = None
            except Exception:
                raise RuntimeError("The Hub browser closed, but protected temporary session files could not be removed. Close Quick Relist before checking its temporary browser folder.") from None
