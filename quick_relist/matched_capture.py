"""Guarded, read-only traversal of a previously matched Hub editor.

No listing writes, network/storage reads or guessed routes. The four directly
observed native editor tabs may be clicked. Optional existing-room capture opens
only verified room-option captions and closes their dialogs with Escape.
The returned JSON is persistable even after interruption: only fully guarded
observations are retained. Stability is a bounded DOM heuristic, not proof of
hydration, account ownership, exact fidelity or a complete source backup.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
import time

from .capture import CaptureError, CaptureReadError, CaptureSafetyError, PartialHubCapture, _digest, parse_editor_url
from .core import normalize_rxcu
from .hub_readonly import IDENTITY_SCRIPT

# Generic DOM semantics, not invented Hub loading selectors. No text/values
# leave the browser through this probe. Dialogs/menus are never dismissed.
READINESS_SCRIPT = r"""
return (() => {
  const visible = e => {
    if (!e.getClientRects().length) return false;
    for (let p = e; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (p.hidden || p.getAttribute('aria-hidden') === 'true' ||
          s.display === 'none' || s.visibility === 'hidden' ||
          s.visibility === 'collapse' || Number(s.opacity || 1) === 0) return false;
    }
    return true;
  };
  const any = selector => [...document.querySelectorAll(selector)].some(visible);
  return {ready: document.readyState === 'complete',
    busy: any('[aria-busy="true"],[role="progressbar"]'),
    dialog: any('[role="dialog"],[role="alertdialog"],dialog[open],[role="listbox"],[role="menu"]')};
})();
"""


TAB_SELECTOR = 'button[role="tab"][type="button"]'
# Exact textContent from the inspected Hub DOM. Selenium .text is unsuitable:
# CSS text-transform makes the visible strings uppercase on this screen.
TAB_PAGES = {'Listing particulars': 'details', 'Deal Particulars': 'deal_particulars',
             'Media': 'media', 'Portals': 'portals'}


class _Cancelled(Exception):
    pass


class MatchedHubCapture(PartialHubCapture):
    """One-shot capture. Inspect ``stop_reason`` and ``coverage`` in the result.

    ``cancelled`` is an optional zero-argument boolean callback, checked between
    browser commands and waits. In-flight WebDriver commands cannot be cancelled
    here. Each navigation temporarily uses at most ``timeout`` seconds for
    WebDriver page loading; the prior timeout is restored. ``timeout`` also
    bounds each page's polling, but cannot bound a hung driver transport RPC.
    """

    def __init__(self, hub, rxcu, *, timeout=15.0, poll_interval=0.2,
                 stable_for=1.0, cancelled=None, include_room_options=False):
        super().__init__(hub.driver)
        self.hub = hub
        self.reference = normalize_rxcu(rxcu)
        if not (0.01 <= poll_interval <= 2 and 0.05 <= stable_for < timeout <= 120):
            raise ValueError("Invalid capture wait limits")
        self.timeout, self.poll_interval, self.stable_for = timeout, poll_interval, stable_for
        self.cancelled = cancelled or (lambda: False)
        if type(include_room_options) is not bool:
            raise ValueError("Room capture mode must be explicit")
        self.include_room_options = include_room_options
        self._room_results = []
        self._pending_rooms = []
        self._binding = None
        self._started = False
        self._cancelled = False
        self._reason = None

    def _cancel(self):
        if self.cancelled():
            raise _Cancelled()

    def _guard(self, url):
        self._cancel()
        if self.hub.driver is not self.driver or self.hub.editor_bindings.get(self.reference) != self._binding:
            raise CaptureSafetyError("The matched editor binding changed. Start a new capture from the matching result.")
        self._check(self._listing_id, expected_url=url)

    def _identity(self, url, *, allow_loading=False):
        self._guard(url)
        identity = self.driver.execute_script(IDENTITY_SCRIPT, self.reference)
        self._guard(url)
        if not isinstance(identity, dict):
            raise CaptureSafetyError("The visible editor identity could not be verified.")
        pairs = [(identity.get(prefix + '_count'), identity.get(prefix + '_matches')) for prefix in ('outer', 'inner')]
        # Missing headings may hydrate. A visible different/ambiguous reference
        # is never retried, even if it would later change to the expected one.
        if any(type(count) is not int or count not in (0, 1) or
               (count == 1 and match is not True) or
               (count == 0 and match is not False) for count, match in pairs):
            raise CaptureSafetyError("The visible editor reference changed or is ambiguous.")
        if all(count == 1 and match is True for count, match in pairs):
            return True
        if allow_loading:
            return False
        raise CaptureSafetyError("The visible editor reference is missing. No new observation was accepted.")

    def _tabs(self, url):
        """Read only the observed native tab semantics, never infer a route."""
        self._guard(url)
        tabs = {}
        selected = []
        ready = True
        for element in self.driver.find_elements('css selector', TAB_SELECTOR):
            if not element.is_displayed():
                continue
            label = ' '.join((element.get_attribute('textContent') or '').split())
            key = TAB_PAGES.get(label)
            if element.get_attribute('aria-selected') == 'true':
                selected.append(key)
            if key is None:
                continue  # Stats, Notes, History, etc. are never candidates.
            if key in tabs:
                raise CaptureSafetyError("The editor tab controls are ambiguous. No tab was clicked.")
            if (element.tag_name.lower() != 'button' or element.get_attribute('type') != 'button'
                    or element.get_attribute('role') != 'tab'):
                raise CaptureSafetyError("The editor tab control changed. No tab was clicked.")
            active = element.get_attribute('aria-selected')
            if active not in ('true', 'false') or element.get_attribute('tabindex') != ('0' if active == 'true' else '-1'):
                raise CaptureSafetyError("The editor tab selection could not be verified.")
            ready = ready and element.is_enabled() and element.get_attribute('aria-disabled') != 'true'
            tabs[key] = element
        if tabs:
            ready = ready and selected == [parse_editor_url(url)['page_key']]
        self._guard(url)
        return tabs, ready

    def _readiness(self, url):
        self._guard(url)
        state = self.driver.execute_script(READINESS_SCRIPT)
        self._guard(url)
        if not isinstance(state, dict) or any(type(state.get(k)) is not bool for k in ('ready', 'busy', 'dialog')):
            raise CaptureReadError("The editor readiness could not be read.")
        if state['dialog']:
            raise CaptureSafetyError("Close the open dialog or menu manually before starting a new matched capture.")
        return state['ready'] and not state['busy'] and self._tabs(url)[1]

    def _settled_page(self, url):
        deadline = time.monotonic() + self.timeout
        fingerprint = None
        unchanged_since = None
        while time.monotonic() < deadline:
            self._guard(url)
            ready = self._readiness(url)
            matched = self._identity(url, allow_loading=True)
            if ready and matched:
                # Reuse the existing filtering, validation, URL rejection and
                # integrity conventions. Trial samples never enter our history.
                sample = PartialHubCapture(self.driver).capture_current_page(self._listing_id)['pages'][0]
                self._identity(url)
                ready = self._readiness(url)
                self._identity(url)
                if sample['dialogs']:
                    raise CaptureSafetyError("An open dialog interrupted capture. No new observation was accepted.")
                content = {k: v for k, v in sample.items() if k not in ('integrity', 'observed_at', 'observation_number')}
                # An empty shell is not a usable observation. This still cannot
                # establish completeness of lazy-loaded or collapsed content.
                populated = any(sample[k] for k in ('fields', 'rich_text', 'sections', 'tables', 'lists', 'media'))
                if ready and populated:
                    current = _digest(content)
                    if current != fingerprint:
                        fingerprint, unchanged_since = current, time.monotonic()
                    elif time.monotonic() - unchanged_since >= self.stable_for:
                        self._identity(url)
                        sample['observation_number'] = len(self._pages) + 1
                        sample['matched_source'] = {'rxcu': self.reference, 'listing_id': self._listing_id,
                            'reference_checked_before_and_after': True, 'pinned_tab_checked': True,
                            'dom_stability_seconds': self.stable_for, 'hydration_complete_verified': False}
                        sample.pop('integrity')
                        sample['integrity'] = {'algorithm': 'sha256', 'sha256': _digest(sample)}
                        self._guard(url)
                        return sample
                else:
                    fingerprint, unchanged_since = None, None
            else:
                fingerprint, unchanged_since = None, None
            self._cancel()
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        raise CaptureReadError("The editor did not become stably readable within the wait limit. Earlier observations were retained.")

    def _navigate(self, source_url, target):
        # Browser timeout configuration only; no listing/application mutation.
        # Never start GET if timeout setup or the immediately preceding guards
        # fail. The timeout exception is uncertain navigation, never retried.
        prior = self.driver.timeouts.page_load
        if type(prior) not in (int, float) or not math.isfinite(prior) or prior <= 0:
            raise CaptureReadError("The browser navigation timeout could not be bounded safely.")
        try:
            self.driver.set_page_load_timeout(min(prior, self.timeout))
            self._identity(source_url)
            if not self._readiness(source_url):
                raise CaptureSafetyError("The source page became busy before navigation.")
            self._identity(source_url)
            self.driver.get(target)
        finally:
            self.driver.set_page_load_timeout(prior)

    def _navigate_tab(self, source_url, key):
        """Click one freshly verified observed tab, then watch actual routing."""
        prior = self.driver.timeouts.page_load
        if type(prior) not in (int, float) or not math.isfinite(prior) or prior <= 0:
            raise CaptureReadError("The browser navigation timeout could not be bounded safely.")
        try:
            self.driver.set_page_load_timeout(min(prior, self.timeout))
            self._identity(source_url)
            if not self._readiness(source_url):
                raise CaptureSafetyError("The source page became busy before tab navigation.")
            self._identity(source_url)
            # Reacquire and validate the actual native element after the final
            # identity probe, rather than trusting a previously retained handle.
            tabs, ready = self._tabs(source_url)
            if not ready or key not in tabs or key == parse_editor_url(source_url)['page_key']:
                raise CaptureSafetyError("The observed editor tab is no longer available. No tab was clicked.")
            tabs[key].click()  # Exactly once. Never retry an uncertain click.
        finally:
            self.driver.set_page_load_timeout(prior)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self._guard(None)  # Pins handle/window set, binding and numeric ID.
            actual = self.driver.current_url
            self._guard(actual)
            route = parse_editor_url(actual)
            if actual != source_url and route['page_key'] != key:
                raise CaptureSafetyError("The editor tab navigated somewhere unexpected. Inspect Hub before continuing.")
            ready = self._readiness(actual)
            matched = self._identity(actual, allow_loading=True)
            if route['page_key'] == key and ready and matched:
                return actual  # This URL came from the browser, never synthesis.
            self._cancel()
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        raise CaptureReadError("The editor tab navigation did not become verifiable. Inspect Hub before trying again.")

    def _capture_rooms(self, sample, url):
        from .room_capture import capture_existing_room_options
        result = capture_existing_room_options(self.hub, self.reference,
            expected_listing_id=self._listing_id, expected_url=url, timeout=self.timeout,
            expected_main_fingerprint=_digest({key: sample[key] for key in ('fields', 'rich_text')}),
            poll_interval=self.poll_interval, stable_for=self.stable_for, cancelled=self.cancelled)
        self._pending_rooms.extend(deepcopy(result.get('pending_dialogs', [])))
        self._room_results.append({key: deepcopy(result.get(key)) for key in
            ('status', 'stop_reason', 'stop_code', 'observations', 'complete', 'server_writes_verified')})
        accepted = []
        for dialog in result.get('dialogs', []):
            source = dialog.get('source', {})
            if (source.get('rxcu') != self.reference or source.get('listing_id') != self._listing_id
                    or any(source.get(key) is not True for key in ('reference_checked_before_and_after',
                        'pinned_tab_checked', 'main_fields_unchanged_verified', 'close_verified'))):
                self._pending_rooms.append(deepcopy(dialog))
                continue
            accepted.append(deepcopy(dialog))
        sample['dialogs'].extend(accepted)
        sample['room_options_observed'] = len(accepted)
        sample.pop('integrity')
        sample['integrity'] = {'algorithm': 'sha256', 'sha256': _digest(sample)}
        if result.get('status') != 'partial' or self._pending_rooms:
            self._cancelled = result.get('status') == 'cancelled'
            raise CaptureReadError('Room-option capture stopped. Earlier guarded pages and dialogs were retained. Check the current editor before continuing; uncertain dialog data remains separate and unverified.')
        self._identity(url)

    def capture(self):
        """Return partial JSON on success, safety stop, read failure or cancel.

        Invalid RXCU/options raise at construction. A capture instance is single
        use; rerunning would risk mixing observations from separate attempts.
        """
        if self._started:
            raise ValueError("Start a new matched capture session instead of resuming this attempt")
        self._started = True
        try:
            self._cancel()
            binding = self.hub.editor_bindings.get(self.reference)
            if (not isinstance(binding, dict) or not isinstance(binding.get('listing_id'), str)
                    or not re.fullmatch(r'[0-9]+', binding['listing_id'])
                    or not isinstance(binding.get('handle'), str) or not binding['handle']):
                raise CaptureSafetyError("Open the exact matching editor from its search result before capturing.")
            self._binding = deepcopy(binding)
            handle, handles, url, route = self._position()
            if handle != binding['handle'] or route['listing_id'] != binding['listing_id']:
                raise CaptureSafetyError("The selected browser tab is not the matched editor. No observation was accepted.")
            self._handle, self._handles, self._listing_id = handle, handles, binding['listing_id']
            # A stale/reused tab must already match; missing headings here do
            # not justify treating an old binding as fresh navigation evidence.
            self._identity(url)
            visited = set()
            while True:
                sample = self._settled_page(url)
                self._pages.append(sample)
                self._last_url = url
                visited.add(sample['page_key'])
                if self.include_room_options and sample['page_key'] == 'details':
                    self._capture_rooms(sample, url)
                tabs, ready = self._tabs(url)
                tab_candidates = [key for key in TAB_PAGES.values() if key in tabs and key not in visited]
                if tab_candidates:
                    if not ready:
                        raise CaptureSafetyError("The editor tabs changed before navigation.")
                    url = self._navigate_tab(url, tab_candidates[0])
                    continue
                candidates = [link for page in self._pages for link in page['editor_links'] if link['page_key'] not in visited]
                if not candidates:
                    break
                target = candidates[0]['url']
                self._identity(url)
                if not self._readiness(url):
                    raise CaptureSafetyError("The source page became busy before navigation. Earlier observations were retained.")
                self._identity(url)
                # Literal, validated, previously observed same-listing URL.
                # Legacy link fallback: exactly one GET, no retries or form mutation.
                self._navigate(url, target)
                self._guard(target)
                url = target
        except (_Cancelled, KeyboardInterrupt):
            self._cancelled = self._stopped = True
            self._reason = 'Capture cancelled. Only earlier fully guarded observations were retained.'
        except CaptureError as exc:
            self._stopped = True
            self._reason = str(exc)
        except Exception:
            self._stopped = True
            # Never echo driver exception strings, which may contain secrets.
            self._reason = 'Capture stopped after a browser read or navigation failure. Inspect the current tab before starting again. Earlier guarded observations were retained.'
        return self.get_result()

    def get_result(self):
        result = super().get_result()
        result.pop('integrity')
        guarded = bool(self._pages) and all(p.get('matched_source') for p in self._pages)
        result['capture_kind'] = 'matched_partial_visible_dom'
        result['identity'].update(reference_verified=guarded)
        # Visible headings bind the observation attempt, not every server value.
        # Do not promote the older broad page-fidelity flag to verified.
        if guarded:
            result['blindspots'] = [item for item in result['blindspots'] if item['code'] != 'reference_unverified']
        result['matched_source'] = {'rxcu': self.reference, 'listing_id': self._listing_id,
            'row_link_binding_used': guarded, 'all_accepted_observations_guarded': guarded,
            'historical_observations_only': True, 'account_verified': False,
            'fidelity_verified': False, 'nested_dialogs_verified': False}
        result['status'] = 'cancelled' if self._cancelled else result['status']
        result['stop_reason'] = self._reason
        result['room_option_capture'] = deepcopy(self._room_results)
        result['pending_room_observations'] = deepcopy(self._pending_rooms)
        result['limitations'] = sorted(set(result['limitations']) | {'bounded_dom_stability_not_complete_hydration'})
        if self._pending_rooms:
            result['limitations'].append('room_dialog_close_or_identity_unverified')
        result['warning'] = 'Matched partial observations only. Not a complete source backup and not safe to use for relisting.'
        result['integrity'] = {'algorithm': 'sha256', 'sha256': _digest(result)}
        return result


def capture_matched_listing(hub, rxcu, **options):
    """Capture observed editor pages; always inspect status/stop_reason/coverage."""
    return MatchedHubCapture(hub, rxcu, **options).capture()
