"""Guarded, read-only traversal of a previously matched Hub editor.

No listing writes, network/storage reads or guessed routes. The four directly
observed native editor tabs may be clicked. Optional existing-room capture opens
only verified room-option captions and closes their dialogs with Escape.
The returned JSON is persistable even after interruption: only fully guarded
observations are retained. Stability is a bounded DOM heuristic, not proof of
hydration, account ownership, exact fidelity or a complete source backup.
"""
from __future__ import annotations

from contextlib import contextmanager
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


# Diagnostics contain only these static labels, never exception text, class
# names, URLs, selectors, element properties or other browser/source values.
_FAILURE_PHASES = {
    'source_check': 'initial source check',
    'page_read': 'page read/probe',
    'room_capture': 'room capture',
    'tab_probe': 'tab read/probe',
    'link_precheck': 'link navigation precheck',
    'tab_precheck': 'tab navigation precheck',
    'tab_position': 'tab position read',
    'tab_scroll': 'tab scroll call or its guard check (scroll outcome may be uncertain)',
    'tab_position_recheck': 'tab position recheck',
    'timeout_read': 'navigation timeout read',
    'timeout_setup': 'navigation timeout setup',
    'tab_click': 'tab click call',
    'link_navigation': 'link navigation call',
    'tab_wait': 'tab navigation verification',
    'link_verify': 'link navigation verification',
    'timeout_restore': 'navigation timeout restoration',
    'unknown_phase': 'unclassified phase',
}
_FAILURE_KINDS = {
    'click_intercepted': 'intercepted click',
    'stale_element': 'stale element',
    'not_interactable': 'element not interactable',
    'move_target_out_of_bounds': 'move target out of bounds',
    'timeout': 'timeout',
    'javascript_error': 'JavaScript execution error',
    'no_such_window': 'browser window unavailable',
    'invalid_session': 'browser session unavailable',
    'interrupted': 'interrupted',
    'other_exception': 'unclassified exception',
}


def _browser_failure_kind(exc):
    if any(type(exc) is known for known in (_Cancelled, KeyboardInterrupt)):
        return 'interrupted'
    try:
        from selenium.common.exceptions import (ElementClickInterceptedException,
            ElementNotInteractableException, InvalidSessionIdException,
            JavascriptException, MoveTargetOutOfBoundsException,
            NoSuchWindowException, StaleElementReferenceException, TimeoutException)
    except ImportError:
        return 'other_exception'
    # Exact types only. Subclasses and names that imitate Selenium stay generic.
    for exception_type, kind in (
            (ElementClickInterceptedException, 'click_intercepted'),
            (StaleElementReferenceException, 'stale_element'),
            (ElementNotInteractableException, 'not_interactable'),
            (MoveTargetOutOfBoundsException, 'move_target_out_of_bounds'),
            (TimeoutException, 'timeout'),
            (JavascriptException, 'javascript_error'),
            (NoSuchWindowException, 'no_such_window'),
            (InvalidSessionIdException, 'invalid_session')):
        if type(exc) is exception_type:
            return kind
    return 'other_exception'


def matched_capture_failure_note(diagnostic):
    """Render fixed labels only, including on the existing saved-result review."""
    if type(diagnostic) is not dict or set(diagnostic) != {
            'phase', 'target_page_key', 'kind', 'navigation_attempted_in_capture'}:
        return ''
    phase, page, kind = (diagnostic[key] for key in ('phase', 'target_page_key', 'kind'))
    attempted = diagnostic['navigation_attempted_in_capture']
    if (type(phase) is not str or phase not in _FAILURE_PHASES
            or type(kind) is not str or kind not in _FAILURE_KINDS
            or (page is not None and (type(page) is not str or page not in TAB_PAGES.values()))
            or type(attempted) is not bool):
        return ''
    navigation = ('A tab/link navigation call was attempted during this capture; '
                  'this capture-wide flag does not identify a failed call or establish its outcome.' if attempted else
                  'No tab/link navigation call was attempted during this capture.')
    return (f'Matched capture diagnostic ({_FAILURE_PHASES[phase]}, '
            f'{page if page is not None else "page not established"}): {_FAILURE_KINDS[kind]}. '
            + navigation)


class MatchedHubCapture(PartialHubCapture):
    """One-shot capture. Inspect ``stop_reason`` and ``coverage`` in the result.

    ``cancelled`` is an optional zero-argument boolean callback, checked between
    browser commands and waits. In-flight WebDriver commands cannot be cancelled
    here. Each navigation temporarily uses at most ``timeout`` seconds for
    WebDriver page loading; restoration of the prior timeout is attempted and
    failures are reported separately. ``timeout`` also bounds each page's polling, but cannot bound a hung driver transport RPC.
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
        self._failure_phase = 'source_check'
        self._target_page_key = None
        self._navigation_attempted_in_capture = False
        self._browser_failure = None
        self._timeout_restore_failure = None

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
        # Missing headings may hydrate. A visible or qualified duplicate-title
        # conflict is never retried, even if it later changes to the expected one.
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

    def _failure_diagnostic(self, exc, *, phase=None):
        phase = self._failure_phase if phase is None else phase
        page = self._target_page_key
        return {'phase': phase if type(phase) is str and phase in _FAILURE_PHASES else 'unknown_phase',
                'target_page_key': page if type(page) is str and page in TAB_PAGES.values() else None,
                'kind': _browser_failure_kind(exc),
                'navigation_attempted_in_capture': self._navigation_attempted_in_capture is True}

    @contextmanager
    def _navigation_timeout(self):
        # Same configuration calls as before, including restoration after failed
        # setup. Cleanup failure must not replace a primary error/cancellation.
        self._failure_phase = 'timeout_read'
        prior = self.driver.timeouts.page_load
        if type(prior) not in (int, float) or not math.isfinite(prior) or prior <= 0:
            raise CaptureReadError("The browser navigation timeout could not be bounded safely.")
        primary_failed = False
        try:
            self._failure_phase = 'timeout_setup'
            self.driver.set_page_load_timeout(min(prior, self.timeout))
            yield
        except BaseException:
            primary_failed = True
            raise
        finally:
            try:
                self.driver.set_page_load_timeout(prior)
            except (Exception, KeyboardInterrupt) as exc:
                self._timeout_restore_failure = self._failure_diagnostic(exc, phase='timeout_restore')
                if not primary_failed:
                    self._failure_phase = 'timeout_restore'
                    raise

    def _navigate(self, source_url, target):
        # Browser timeout configuration only; no listing/application mutation.
        # Never start GET if timeout setup or the immediately preceding guards
        # fail. The timeout exception is uncertain navigation, never retried.
        with self._navigation_timeout():
            self._failure_phase = 'link_precheck'
            self._identity(source_url)
            if not self._readiness(source_url):
                raise CaptureSafetyError("The source page became busy before navigation.")
            self._identity(source_url)
            self._failure_phase = 'link_navigation'
            self._navigation_attempted_in_capture = True  # Set before dispatch, not on return.
            self.driver.get(target)

    def _navigate_tab(self, source_url, key):
        """Click one freshly verified observed tab, then watch actual routing."""
        with self._navigation_timeout():
            self._failure_phase = 'tab_precheck'
            self._identity(source_url)
            if not self._readiness(source_url):
                raise CaptureSafetyError("The source page became busy before tab navigation.")
            self._identity(source_url)
            # Reacquire and validate the actual native element after the final
            # identity probe, rather than trusting a previously retained handle.
            tabs, ready = self._tabs(source_url)
            if not ready or key not in tabs or key == parse_editor_url(source_url)['page_key']:
                raise CaptureSafetyError("The observed editor tab is no longer available. No tab was clicked.")
            from .tab_positioning import prepare_native_tab

            def recheck():
                self._identity(source_url)
                if not self._readiness(source_url):
                    raise CaptureSafetyError("The source page became busy during tab positioning. No tab was clicked.")
                self._identity(source_url)
                current, current_ready = self._tabs(source_url)
                if not current_ready:
                    raise CaptureSafetyError("The editor tab selection changed during positioning. No tab was clicked.")
                return {label: current[page] for label, page in TAB_PAGES.items() if page in current}

            def phase(value):
                self._failure_phase = value

            labels = {page: label for label, page in TAB_PAGES.items()}
            target = prepare_native_tab(self.driver, source_url=source_url,
                source_label=labels[parse_editor_url(source_url)['page_key']], target_label=labels[key],
                tabs={label: tabs[page] for label, page in TAB_PAGES.items() if page in tabs},
                recheck=recheck, cancelled=self._cancel, phase=phase)
            self._cancel()
            self._failure_phase = 'tab_click'
            self._navigation_attempted_in_capture = True  # Even a raised click can have acted.
            target.click()  # Exactly once. Never retry an uncertain click.
        self._failure_phase = 'tab_wait'
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
        from .room_capture import capture_existing_room_options, room_open_failure_note
        result = capture_existing_room_options(self.hub, self.reference,
            expected_listing_id=self._listing_id, expected_url=url, timeout=self.timeout,
            expected_main_fingerprint=_digest({key: sample[key] for key in ('fields', 'rich_text')}),
            poll_interval=self.poll_interval, stable_for=self.stable_for, cancelled=self.cancelled)
        self._pending_rooms.extend(deepcopy(result.get('pending_dialogs', [])))
        self._room_results.append({key: deepcopy(result.get(key)) for key in
            ('status', 'stop_reason', 'stop_code', 'open_failure', 'observations', 'complete', 'server_writes_verified')})
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
            reason = 'Room-option capture stopped. Earlier guarded pages and dialogs were retained. Check the current editor before continuing; uncertain dialog data remains separate and unverified.'
            if result.get('stop_code') == 'open_uncertain':
                note = room_open_failure_note(result.get('open_failure'))
                if note:
                    reason += ' ' + note
            raise CaptureReadError(reason)
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
            self._target_page_key = route['page_key']
            # A stale/reused tab must already match; missing headings here do
            # not justify treating an old binding as fresh navigation evidence.
            self._identity(url)
            visited = set()
            while True:
                self._failure_phase = 'page_read'
                sample = self._settled_page(url)
                self._pages.append(sample)
                self._last_url = url
                visited.add(sample['page_key'])
                if self.include_room_options and sample['page_key'] == 'details':
                    self._failure_phase = 'room_capture'
                    self._capture_rooms(sample, url)
                self._failure_phase = 'tab_probe'
                tabs, ready = self._tabs(url)
                tab_candidates = [key for key in TAB_PAGES.values() if key in tabs and key not in visited]
                if tab_candidates:
                    if not ready:
                        raise CaptureSafetyError("The editor tabs changed before navigation.")
                    self._target_page_key = tab_candidates[0]
                    url = self._navigate_tab(url, tab_candidates[0])
                    continue
                candidates = [link for page in self._pages for link in page['editor_links'] if link['page_key'] not in visited]
                if not candidates:
                    break
                target = candidates[0]['url']
                self._target_page_key = candidates[0]['page_key']
                self._failure_phase = 'link_precheck'
                self._identity(url)
                if not self._readiness(url):
                    raise CaptureSafetyError("The source page became busy before navigation. Earlier observations were retained.")
                self._identity(url)
                # Literal, validated, previously observed same-listing URL.
                # Legacy link fallback: exactly one GET, no retries or form mutation.
                self._navigate(url, target)
                self._failure_phase = 'link_verify'
                self._guard(target)
                url = target
        except (_Cancelled, KeyboardInterrupt):
            self._cancelled = self._stopped = True
            self._reason = 'Capture cancelled. Only earlier fully guarded observations were retained.'
        except Exception as exc:
            self._stopped = True
            if any(type(exc) is known for known in (CaptureError, CaptureReadError, CaptureSafetyError)) and self._failure_phase != 'timeout_restore':
                self._reason = str(exc)  # Existing internal, fixed guard/read reasons.
            else:
                # Never echo driver exception strings, which may contain secrets.
                self._reason = 'Capture stopped after a browser read or navigation failure. Inspect the current tab before starting again. Earlier guarded observations were retained.'
                if self._failure_phase != 'timeout_restore':
                    self._browser_failure = self._failure_diagnostic(exc)
        if self._browser_failure:
            self._reason += ' ' + matched_capture_failure_note(self._browser_failure)
        if self._timeout_restore_failure:
            self._reason += ' Browser timeout restoration failed or was interrupted; restoration is unverified. '
            self._reason += matched_capture_failure_note(self._timeout_restore_failure)
        return self.get_result()

    def get_result(self):
        result = super().get_result()
        result.pop('integrity')
        guarded = bool(self._pages) and all(p.get('matched_source') for p in self._pages)
        result['capture_kind'] = 'matched_partial_visible_dom'
        result['identity'].update(reference_verified=guarded)
        # The visible main title and qualifying outer-title consistency bind
        # the observation attempt, not every server value or two visible titles.
        # Do not promote the older broad page-fidelity flag to verified.
        if guarded:
            result['blindspots'] = [item for item in result['blindspots'] if item['code'] != 'reference_unverified']
        result['matched_source'] = {'rxcu': self.reference, 'listing_id': self._listing_id,
            'row_link_binding_used': guarded, 'all_accepted_observations_guarded': guarded,
            'historical_observations_only': True, 'account_verified': False,
            'fidelity_verified': False, 'nested_dialogs_verified': False}
        result['status'] = 'cancelled' if self._cancelled else result['status']
        result['stop_reason'] = self._reason
        result['browser_failure'] = deepcopy(self._browser_failure)
        result['timeout_restore_failure'] = deepcopy(self._timeout_restore_failure)
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
