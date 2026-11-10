"""Partial, read-only Hub page inspection. Never performs a listing mutation.

This is deliberately NOT the production listing adapter. It captures only visible
controls on one editor tab and cannot certify that a listing backup is complete.
"""
from __future__ import annotations

from urllib.parse import urlsplit
from pathlib import Path
from uuid import uuid4
import json
import os
import re
import stat
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
const identity = e => {
  const match = visibleText(e).match(/^Edit listing\s*-\s*(RXCU-?[0-9]{3,20})\s*\(/i);
  return match ? match[1].toUpperCase().replace('-', '') : null;
};
const outer = [...document.querySelectorAll('h2')].filter(e => visible(e) && !e.closest('main')).map(identity).filter(Boolean);
const inner = [...document.querySelectorAll('main h4')].filter(visible).map(identity).filter(Boolean);
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

    def open(self):
        if self.driver is not None:
            raise RuntimeError("A browser session is already open")
        try:
            from selenium import webdriver
        except ImportError as exc:
            raise RuntimeError("Install the browser extra to open Chrome: pip install 'quick-relist[browser]'") from exc
        options = webdriver.ChromeOptions()
        options.add_argument("--incognito")
        # Selenium starts a dedicated visible browser. No stored password or OTP
        # is requested by or passed to this application.
        self.driver = webdriver.Chrome(options=options)
        self.driver.get(MANAGE_URL)

    def search_reference(self, rxcu):
        """Submit a read-only Manage Listings search; do not infer a match.

        Hub filters may hide listings, so an empty result never proves absence.
        The exact displayed search prompt and button must be unique and visible.
        """
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
        current_route = parse_editor_url(link.get_attribute("href"))
        if current_route["page_key"] != "details" or current_route["listing_id"] != listing_id:
            raise ValueError("The editor link changed. Inspect Hub before continuing.")
        old_handles = set(self.driver.window_handles)
        try:
            link.click()  # Exactly once. Never click Archive or retry an uncertain click.
        except Exception:
            raise RuntimeError("Opening the editor is uncertain. Inspect Hub before continuing.") from None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            new_handles = set(self.driver.window_handles) - old_handles
            if len(new_handles) > 1:
                raise RuntimeError("More than one new tab appeared. Inspect Hub before continuing.")
            if len(new_handles) == 1:
                handle = next(iter(new_handles))
                self.driver.switch_to.window(handle)
                try:
                    identity = self.verify_editor_reference(reference, listing_id)
                except ValueError:
                    # Page may be loading, but never repeat the link click.
                    time.sleep(poll_interval)
                    continue
                self.editor_bindings[reference] = {"listing_id": listing_id, "handle": handle}
                return {**identity, "row_link_verified": True, "listing_id": listing_id, "complete": False}
            time.sleep(poll_interval)
        raise RuntimeError("The editor did not become verifiable. Inspect the new tab before continuing.")

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

    def verify_editor_reference(self, expected_rxcu, expected_listing_id):
        """Verify both observed editor headings and the current numeric route.

        This does not prove which account owns the record or independently bind
        the route to a results-row link. No address text leaves the browser.
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
                   and element.text.strip() == "Download photo pack"
                   and element.get_attribute("type") == "button"]
        if len(buttons) != 1:
            raise ValueError("The photo-pack button was not uniquely available")
        root = Path(os.path.abspath(download_root))
        _prepare_root(root)
        # Browser download routing is session-wide. One unresolved attempt
        # blocks all later attempts, including different listings and restarts.
        marker = root / "download-review-required.json"
        if marker.exists() or marker.is_symlink():
            # Never silently retry after a previous attempt, even after restart.
            raise PreviousDownloadExists(root)
        directory = root / ("download-" + uuid4().hex)
        _private_directory(directory)
        try:
            # A dedicated directory avoids mixing downloads or overwriting files.
            self.driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
                "behavior": "allow", "downloadPath": str(directory), "eventsEnabled": False})
        except Exception:
            raise RuntimeError("The browser could not prepare a private download folder; no click was made") from None
        self.verify_editor_reference(reference, expected_listing_id)
        if (self.driver.current_window_handle != handle or tuple(sorted(self.driver.window_handles)) != handles
                or self.driver.current_url != url):
            raise ValueError("The editor changed before the download; no click was made")
        try:
            # A durable intention survives process exit and prevents any blind
            # duplicate download until the operator reviews prior local files.
            with _private_file(marker) as receipt:
                receipt.write(json.dumps({"version": 1, "directory": directory.name, "state": "attempted"}).encode("utf-8"))
                receipt.flush()
                os.fsync(receipt.fileno())
            if os.name != "nt":
                descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except FileExistsError:
            raise PreviousDownloadExists(root) from None
        except Exception:
            raise RuntimeError("The download could not be recorded safely; no click was made") from None
        try:
            buttons[0].click()  # Exactly once; never retry an uncertain click.
        except Exception:
            raise DownloadOutcomeUnknown(directory) from None
        deadline = time.monotonic() + timeout
        stable = None
        stable_count = 0
        while time.monotonic() < deadline:
            try:
                if (self.driver.current_window_handle != handle or tuple(sorted(self.driver.window_handles)) != handles
                        or self.driver.current_url != url):
                    raise DownloadOutcomeUnknown(directory)
                entries = list(directory.iterdir())
                if len(entries) > 10:
                    raise DownloadOutcomeUnknown(directory)
                partial = [entry for entry in entries if entry.name.lower().endswith(".crdownload")]
                finished = [entry for entry in entries if entry.suffix.lower() == ".zip"]
                if len(finished) > 1 or any(entry not in partial and entry not in finished for entry in entries):
                    raise DownloadOutcomeUnknown(directory)
                if not partial and len(finished) == 1:
                    info = finished[0].lstat()
                    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size <= 0:
                        raise DownloadOutcomeUnknown(directory)
                    marker = (finished[0].name, info.st_size, info.st_mtime_ns)
                    stable_count = stable_count + 1 if marker == stable else 1
                    stable = marker
                    if stable_count >= 3:
                        if not zipfile.is_zipfile(finished[0]):
                            raise DownloadOutcomeUnknown(directory)
                        # CDP events are not available here to bind a browser
                        # download GUID/frame to this click. A lone ZIP is only a
                        # candidate; never silently associate or import it.
                        return {"candidate_path": str(finished[0]), "directory": str(directory), "complete": False,
                                "attribution_verified": False,
                                "warning": "A ZIP appeared, but it cannot be attributed to this listing. Manually inspect it before import. Further downloads are paused pending review."}
                else:
                    stable, stable_count = None, 0
            except DownloadOutcomeUnknown:
                raise
            except Exception:
                raise DownloadOutcomeUnknown(directory) from None
            time.sleep(poll_interval)
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
        if self.driver is not None:
            try:
                self.driver.quit()
            finally:
                self.driver = None
                self.editor_bindings.clear()
