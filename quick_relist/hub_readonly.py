"""Partial, read-only Hub page inspection. Never performs a listing mutation.

This is deliberately NOT the production listing adapter. It captures only visible
controls on one editor tab and cannot certify that a listing backup is complete.
"""
from __future__ import annotations

from urllib.parse import urlsplit
import re

MANAGE_URL = "https://hub.remax.co.za/app/listings"
_EDITOR_PATH = re.compile(r"^/app/listings/edit/[^/]+(?:/(?:deal_particulars|media|portals))?/?$")

# Extract visible form controls only. Avoid hidden inputs, password fields and
# browser-stored secrets. These values can contain sensitive information: the
# caller must not log, upload, or display them without the operator's intent.
_INSPECT = r"""
const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const labelFor = el => {
  const labelled = el.getAttribute('aria-label');
  if (labelled) return labelled;
  const ids = el.getAttribute('aria-labelledby');
  if (ids) return ids.split(/\s+/).map(id => document.getElementById(id)?.innerText || '').join(' ').trim();
  if (el.id) {
    const l = [...document.querySelectorAll('label')].find(x => x.htmlFor === el.id);
    if (l) return l.innerText.trim();
  }
  return el.closest('label')?.innerText?.trim() || '';
};
return [...document.querySelectorAll('input, select, textarea, [contenteditable="true"]')]
  .filter(el => visible(el) && !['hidden', 'password', 'file'].includes((el.type || '').toLowerCase()))
  .map(el => ({
    tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '',
    label: labelFor(el), value: el.isContentEditable ? el.innerHTML : el.value,
    checked: ['checkbox', 'radio'].includes(el.type) ? el.checked : null,
    selected_text: el.tagName === 'SELECT' ? [...el.selectedOptions].map(o => o.textContent.trim()) : null
  }));
"""


class ReadOnlyHub:
    """Visible Chrome session; user signs in and navigates the Hub manually.

    Creating this class does not launch a browser. Only editor URLs on the exact
    Hub host may be inspected; a per-tab extraction is explicitly incomplete.
    """
    def __init__(self, driver=None):
        self.driver = driver

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
        prompt = "Search fusion ref, listing ID or address"
        candidates = self.driver.find_elements(By.XPATH, "//input[@placeholder or @aria-label or @id]")
        inputs = []
        for el in candidates:
            if not el.is_displayed() or not el.is_enabled():
                continue
            attrs = (el.get_attribute("placeholder"), el.get_attribute("aria-label"))
            if prompt in attrs:
                inputs.append(el)
        if len(inputs) != 1:
            raise ValueError("The Hub search field could not be identified uniquely; check the page layout")
        buttons = [el for el in self.driver.find_elements(By.XPATH, "//button")
                   if el.is_displayed() and el.is_enabled() and el.text.strip() == "Get listings"]
        if len(buttons) != 1:
            raise ValueError("The Get listings button could not be identified uniquely; check the page layout")
        inputs[0].clear()
        inputs[0].send_keys(reference)
        buttons[0].click()
        return {"searched": True, "warning": "Review the results and filters manually; this does not verify a match."}

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

    def inspect_current_editor_page(self):
        if self.driver is None:
            raise RuntimeError("Open the Hub browser first")
        self.select_editor_tab()
        parsed = urlsplit(self.driver.current_url)
        if not self._is_editor_url(self.driver.current_url):
            raise ValueError("Open a Hub listing editor tab before inspecting")
        fields = self.driver.execute_script(_INSPECT)
        if not isinstance(fields, list):
            raise RuntimeError("Could not inspect this page")
        # Never assert completeness: this omits other tabs, nested dialogs,
        # documents, media bytes, portal status and potentially custom widgets.
        return {"page_path": parsed.path, "fields": fields, "complete": False,
                "warning": "One visible page only. Not a complete listing backup."}

    def close(self):
        if self.driver is not None:
            try:
                self.driver.quit()
            finally:
                self.driver = None
