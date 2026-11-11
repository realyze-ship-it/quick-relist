"""Bounded observation of five verified existing-room dialogs.

Only exact observed caption openers are clicked, once each. Escape is the sole
close action. This observes DOM field equality, never proves no server writes.
No navigation, field editing, network/storage reads, form/button clicks or retry
of uncertain actions is supported. Results contain private, partial source data.
One view-only centered scroll is permitted per verified opener when necessary.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import math
import re
import time

from .capture import PartialHubCapture, _digest, parse_editor_url
from .core import normalize_rxcu
from .hub_readonly import IDENTITY_SCRIPT


@dataclass(frozen=True)
class RoomOptionDescriptor:
    key: str
    opener: str
    count_id: str
    noun: str


ROOM_OPTIONS = (
    RoomOptionDescriptor('bedroom', 'Set bedrooms options', 'feature-bedroom-input', 'Bedroom'),
    RoomOptionDescriptor('bathroom', 'Set bathrooms options', 'feature-bathroom-input', 'Bathroom'),
    RoomOptionDescriptor('kitchen', 'Set kitchens options', 'feature-kitchen-input', 'Kitchen'),
    RoomOptionDescriptor('parking', 'Set parkings options', 'feature-parking-input', 'Parking'),
    RoomOptionDescriptor('lounge', 'Set lounges options', 'feature-lounge-input', 'Lounge'),
)
ESCAPE = '\ue00c'  # WebDriver Escape; no Selenium import required for offline use.


def _keyboard_escape(driver):
    # MUI's dialog paper is not necessarily focusable. Send one standard
    # WebDriver keyboard action to the current browser focus, as a user presses
    # Escape, rather than trying to focus/type into the dialog element.
    from selenium.webdriver.common.action_chains import ActionChains
    ActionChains(driver).send_keys(ESCAPE).perform()

# Read-only structural probe. Never derive selectors or click targets from data.
ROOM_STATE_SCRIPT = r"""
return (() => {
  const wanted = arguments[0];
  const visible = e => {
    if (!e || !e.getClientRects().length) return false;
    for (let p=e; p; p=p.parentElement) {
      const s=getComputedStyle(p);
      if (p.hidden || p.getAttribute('aria-hidden') === 'true' || s.display === 'none' ||
          s.visibility === 'hidden' || s.visibility === 'collapse' || Number(s.opacity || 1) === 0) return false;
    }
    return true;
  };
  const overlayNodes = [...document.querySelectorAll('[role="dialog"],[role="alertdialog"],[aria-modal="true"],dialog[open],[role="listbox"],[role="menu"]')].filter(visible);
  const overlays = overlayNodes.map(e => {
    const ids = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const headings = ids.length === 1 ? [...document.querySelectorAll('h2[id]')].filter(h => h.id === ids[0] && e.contains(h) && visible(h)) : [];
    return {element:e, expected: e.tagName === 'DIV' && e.getAttribute('role') === 'dialog' &&
      headings.length === 1 && headings[0].textContent === wanted.opener};
  });
  const result = {href:location.href, ready:document.readyState === 'complete',
    busy:[...document.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible), overlays, feature:null};
  if (overlays.length) return result;
  const mains = [...document.querySelectorAll('main,[role="main"]')].filter(visible);
  if (mains.length !== 1) return result;
  const root=mains[0];
  const openers=[...root.querySelectorAll('span.MuiTypography-caption')].filter(e => visible(e) && e.textContent === wanted.opener);
  const counts=[...root.querySelectorAll('input[id]')].filter(e => e.id === wanted.count_id);
  const f={opener_count:openers.length, count_count:counts.length, shape_ok:false, count:null, element:null};
  result.feature=f;
  if (openers.length !== 1 || counts.length !== 1) return result;
  const e=openers[0], input=counts[0], inputBase=input.parentElement;
  const control=inputBase?.parentElement, layout=control?.parentElement, common=layout?.parentElement;
  const shape=visible(input) && input.type === 'text' && !input.hasAttribute('name') && !input.disabled && !input.readOnly &&
    !['step','min','max','inputmode','pattern'].some(a => input.hasAttribute(a)) &&
    e.childElementCount === 0 && !['role','title','aria-label','tabindex'].some(a => e.hasAttribute(a)) &&
    !e.closest('button,a,[role="button"],[contenteditable="true"]') && e.getAttribute('aria-disabled') !== 'true' &&
    inputBase?.matches('div.MuiInputBase-root') && control?.matches('div.MuiFormControl-root') &&
    layout?.matches('div.flex.alignItems_flexend') && [...layout.children].some(n => n.tagName === 'H6' && visible(n)) &&
    common?.tagName === 'DIV' && e.parentElement?.tagName === 'DIV' && e.parentElement.parentElement === common &&
    common.children.length === 2 && common.children[0] === layout && common.children[1] === e.parentElement;
  f.shape_ok=!!shape;
  if (shape) {
    f.element=e;
    const value=String(input.value);
    if (/^[0-9]+$/.test(value) && Number.isSafeInteger(Number(value))) f.count=Number(value);
  }
  return result;
})();
"""

# Re-run the same structural probe inside each positioning RPC. The only
# optional side effect is one instant scroll of that exact verified node.
# Return static statuses only, never geometry, hit nodes or private DOM text.
ROOM_POSITION_SCRIPT = r"""
return (() => {
  const wanted=arguments[0], expected=arguments[1], count=arguments[2], scroll=arguments[3];
  const state=(function() {
""" + ROOM_STATE_SCRIPT + r"""
  })(wanted);
  const f=state.feature;
  if (!state.ready || state.busy || state.overlays.length || !f ||
      f.opener_count !== 1 || f.count_count !== 1 || !f.shape_ok ||
      f.element !== expected || f.count !== count || !Number.isSafeInteger(count) || count <= 0)
    return 'changed';
  const e=f.element;
  if (!e.isConnected || e.ownerDocument !== document || e.getRootNode() !== document) return 'changed';
  const rects=[...e.getClientRects()], w=window.innerWidth, h=window.innerHeight;
  const cw=document.documentElement.clientWidth, ch=document.documentElement.clientHeight;
  if (![w,h,cw,ch].every(n => Number.isFinite(n) && n > 0) || !rects.length ||
      rects.some(r => ![r.x,r.y,r.width,r.height,r.x+r.width,r.y+r.height].every(Number.isFinite) ||
        r.width < 1 || r.height < 1)) return 'invalid';
  // WebDriver's in-view center: FIRST client rect, clipped to the initial
  // viewport, then floored. Not the bounding-box center or an offset guess.
  const r=rects[0], left=Math.max(0,r.x), right=Math.min(w,r.x+r.width),
    top=Math.max(0,r.y), bottom=Math.min(h,r.y+r.height);
  const x=Math.floor((left+right)/2), y=Math.floor((top+bottom)/2);
  let clear=false;
  if (right-left >= 1 && bottom-top >= 1 && x >= left && x < right && y >= top && y < bottom &&
      x < cw && y < ch) {
    // The observed caption has text nodes only. No descendant may supply a
    // click target, even if an empty child preserves the aggregate caption.
    clear=document.elementFromPoint(x,y) === e;
  }
  if (clear) return 'clear';
  if (scroll !== true) return 'needs_scroll';
  e.scrollIntoView({behavior:'instant', block:'center', inline:'center'});
  return 'scrolled';
})();
"""

_REASONS = {
    'binding_changed': 'The matched editor binding changed. No further room action was taken.',
    'position_changed': 'The editor route or browser tabs changed. Inspect the current tab before continuing.',
    'identity_changed': 'The visible editor reference is missing, different or ambiguous. No further room action was taken.',
    'initial_dialog': 'Close the open dialog or menu manually before starting room capture.',
    'state_unreadable': 'The room controls could not be read safely. Earlier observations were retained.',
    'main_unreadable': 'The main source fields could not be read safely. Earlier observations were retained.',
    'main_changed': 'The observed main source fields changed. Inspect Hub before continuing.',
    'main_timeout': 'The main source fields did not settle within the wait limit.',
    'opener_ambiguous': 'The room opener or count is missing, changed or ambiguous. No opener was clicked.',
    'opener_position_unreadable': 'The room opener position could not be verified safely. No opener was clicked.',
    'opener_obscured': 'The room opener is not safely reachable after the bounded view check. No opener was clicked.',
    'opener_scroll_uncertain': 'The view-scrolling outcome is uncertain. No opener was clicked and the scroll was not retried.',
    'open_uncertain': 'The room-opening outcome is uncertain. Inspect the current tab; the opener was not retried.',
    'open_timeout': 'The expected room dialog did not appear within the wait limit. The opener was not retried.',
    'dialog_changed': 'The open dialog is different or ambiguous. It was left for manual inspection.',
    'dialog_unreadable': 'The verified room dialog could not be read safely. Earlier observations were retained.',
    'dialog_timeout': 'The verified room dialog did not settle within the wait limit.',
    'room_groups_unresolved': 'The captured room groups do not match the observed room count. They remain unverified.',
    'escape_uncertain': 'The Escape outcome is uncertain. Inspect the current dialog; Escape was not retried.',
    'close_timeout': 'The room dialog did not finish closing within the wait limit. Escape was not retried.',
    'cancelled': 'Room capture was cancelled. Earlier observations were retained; inspect any open dialog manually.',
    'browser_failure': 'Room capture stopped after a browser read failure. Inspect the current tab before continuing.',
}


_OPEN_FAILURE_LABELS = {
    'click_intercepted': 'intercepted click',
    'stale_element': 'stale element',
    'not_interactable': 'element not interactable',
    'move_target_out_of_bounds': 'move target out of bounds',
    'timeout': 'timeout',
    'other_exception': 'unclassified exception',
}


def _open_failure_kind(exc):
    # Selenium is optional for offline use. Match exact known types, never
    # class names, messages, attributes or traceback data. Unknown subclasses
    # also stay generic; a diagnostic is not proof of the click's outcome.
    try:
        from selenium.common.exceptions import (ElementClickInterceptedException,
            ElementNotInteractableException, MoveTargetOutOfBoundsException,
            StaleElementReferenceException, TimeoutException)
    except ImportError:
        return 'other_exception'
    for exception_type, kind in (
            (ElementClickInterceptedException, 'click_intercepted'),
            (StaleElementReferenceException, 'stale_element'),
            (ElementNotInteractableException, 'not_interactable'),
            (MoveTargetOutOfBoundsException, 'move_target_out_of_bounds'),
            (TimeoutException, 'timeout')):
        if type(exc) is exception_type:
            return kind
    return 'other_exception'


def room_open_failure_note(diagnostic):
    """Render only bounded static diagnostics on existing local review surfaces."""
    if type(diagnostic) is not dict or set(diagnostic) != {'kind', 'room_key'}:
        return ''
    kind, room = diagnostic['kind'], diagnostic['room_key']
    if (type(kind) is not str or kind not in _OPEN_FAILURE_LABELS
            or type(room) is not str or room not in {d.key for d in ROOM_OPTIONS}):
        return ''
    return (f'Room opener diagnostic ({room}): {_OPEN_FAILURE_LABELS[kind]}. '
            'The room-opening outcome remains uncertain; the opener was not retried.')


class _Stop(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(_REASONS[code])


class _Snapshot(PartialHubCapture):
    def __init__(self, owner):
        super().__init__(owner.driver)
        self.owner = owner

    def _check(self, expected_listing_id=None, expected_url=None):
        return self.owner.guard()


class _RoomCapture:
    def __init__(self, hub, rxcu, *, expected_listing_id, expected_url, descriptors,
                 timeout, poll_interval, stable_for, cancelled, expected_main_fingerprint):
        self.hub, self.driver = hub, hub.driver
        self.reference = normalize_rxcu(rxcu)
        self.listing_id, self.url = expected_listing_id, expected_url
        self.timeout, self.poll, self.stable = timeout, poll_interval, stable_for
        self.cancelled = cancelled or (lambda: False)
        self.descriptors = tuple(ROOM_OPTIONS if descriptors is None else descriptors)
        # Callers may select/reorder verified descriptors, not supply new paths.
        if (not self.descriptors or len(self.descriptors) != len(set(self.descriptors))
                or any(d not in ROOM_OPTIONS for d in self.descriptors)):
            raise ValueError('Use distinct verified room descriptors only')
        limits = (timeout, poll_interval, stable_for)
        if (any(type(x) not in (float, int) or not math.isfinite(x) for x in limits)
                or not .01 <= poll_interval <= 2 or not .05 <= stable_for < timeout <= 120):
            raise ValueError('Invalid capture wait limits')
        self.binding = None
        self.position = None
        if expected_main_fingerprint is not None and (not isinstance(expected_main_fingerprint, str)
                or not re.fullmatch(r'[0-9a-f]{64}', expected_main_fingerprint)):
            raise ValueError('Invalid expected main-field fingerprint')
        self.baseline = expected_main_fingerprint
        self.modal = None
        self.descriptor = None
        self.escape_attempted = False
        self.result = {'dialogs': [], 'pending_dialogs': [], 'observations': [], 'status': 'partial',
                       'stop_code': None, 'stop_reason': None, 'cleanup_stop_code': None,
                       'open_failure': None,
                       'complete': False, 'server_writes_verified': False, 'main_fields_fingerprint': None}

    def check_cancel(self):
        if self.cancelled():
            raise _Stop('cancelled')

    def rpc(self, call):
        self.check_cancel()
        value = call()
        self.check_cancel()
        return value

    def get_position(self):
        handle = self.rpc(lambda: self.driver.current_window_handle)
        handles = self.rpc(lambda: self.driver.window_handles)
        url = self.rpc(lambda: self.driver.current_url)
        if (not isinstance(handles, (tuple, list)) or any(not isinstance(h, str) for h in handles)
                or not isinstance(handle, str) or not handle or handle not in handles
                or len(handles) != len(set(handles))):
            raise _Stop('position_changed')
        try:
            route = parse_editor_url(url)
        except Exception:
            raise _Stop('position_changed') from None
        return handle, tuple(sorted(handles)), url, route

    def guard(self):
        self.check_cancel()
        if self.hub.driver is not self.driver or self.hub.editor_bindings.get(self.reference) != self.binding:
            raise _Stop('binding_changed')
        position = self.get_position()
        if position != self.position:
            raise _Stop('position_changed')
        return position

    def identity(self, *, allow_missing=False):
        self.guard()
        data = self.rpc(lambda: self.driver.execute_script(IDENTITY_SCRIPT, self.reference))
        self.guard()
        if not isinstance(data, dict):
            raise _Stop('identity_changed')
        pairs = [(data.get(p + '_count'), data.get(p + '_matches')) for p in ('outer', 'inner')]
        if all(type(c) is int and c == 1 and m is True for c, m in pairs):
            return True
        if allow_missing and all(type(c) is int and c in (0, 1) and m is (c == 1) for c, m in pairs):
            return False
        raise _Stop('identity_changed')

    def state(self):
        self.guard()
        state = self.rpc(lambda: self.driver.execute_script(ROOM_STATE_SCRIPT, asdict(self.descriptor)))
        self.guard()
        if (not isinstance(state, dict) or state.get('href') != self.url
                or any(type(state.get(k)) is not bool for k in ('ready', 'busy'))
                or not isinstance(state.get('overlays'), list)
                or any(not isinstance(d, dict) or 'element' not in d or type(d.get('expected')) is not bool
                       for d in state['overlays'])):
            raise _Stop('state_unreadable')
        return state

    def same_modal(self, state):
        overlays = state['overlays']
        if len(overlays) != 1 or not overlays[0]['expected']:
            raise _Stop('dialog_changed')
        element = overlays[0]['element']
        if self.modal is not None and element != self.modal:
            raise _Stop('dialog_changed')
        return element

    def wait(self, deadline):
        self.check_cancel()
        time.sleep(min(self.poll, max(0, deadline - time.monotonic())))
        self.check_cancel()

    def snapshot(self):
        self.guard()
        try:
            page = _Snapshot(self).capture_current_page(self.listing_id)['pages'][0]
        except _Stop:
            raise
        except Exception:
            raise _Stop('dialog_unreadable' if self.modal is not None else 'main_unreadable') from None
        self.guard()
        return page

    @staticmethod
    def fingerprint(page):
        return _digest({k: page[k] for k in ('fields', 'rich_text')})

    def settled_main(self, *, closing=False):
        deadline, fingerprint, since = time.monotonic() + self.timeout, None, None
        while time.monotonic() < deadline:
            state = self.state()
            if state['overlays']:
                if not closing:
                    raise _Stop('initial_dialog')
                self.same_modal(state)
            elif state['ready'] and not state['busy'] and self.identity(allow_missing=closing):
                page = self.snapshot()
                self.identity()
                after = self.state()
                if after['overlays']:
                    raise _Stop('dialog_changed')
                if page['dialogs'] or not page['fields'] or 'main_content_scope_unverified' in page['limitations']:
                    raise _Stop('main_unreadable')
                current = self.fingerprint(page)
                if self.baseline is not None and current != self.baseline:
                    raise _Stop('main_changed')
                if after['ready'] and not after['busy']:
                    if current != fingerprint:
                        fingerprint, since = current, time.monotonic()
                    elif time.monotonic() - since >= self.stable:
                        self.identity()
                        return current
                else:
                    fingerprint, since = None, None
            else:
                fingerprint, since = None, None
            self.wait(deadline)
        raise _Stop('close_timeout' if closing else 'main_timeout')

    @staticmethod
    def checked_feature(state):
        if state['overlays']:
            raise _Stop('initial_dialog')
        feature = state['feature']
        if (not state['ready'] or state['busy'] or not isinstance(feature, dict)
                or type(feature.get('opener_count')) is not int or feature.get('opener_count') != 1
                or type(feature.get('count_count')) is not int or feature.get('count_count') != 1
                or feature.get('shape_ok') is not True or feature.get('element') is None):
            raise _Stop('opener_ambiguous')
        return feature

    def recheck_opener(self, original, count):
        # Reacquire after browser calls, including scrolling. Existing node
        # identity, source identity and fields must all still match. This is
        # evidence around calls, not an atomic guarantee against later changes.
        self.identity()
        page = self.snapshot()
        if page['dialogs'] or not page['fields'] or 'main_content_scope_unverified' in page['limitations']:
            raise _Stop('main_unreadable')
        if self.fingerprint(page) != self.baseline:
            raise _Stop('main_changed')
        self.identity()
        feature = self.checked_feature(self.state())
        if (feature['element'] != original['element'] or type(feature.get('count')) is not int
                or feature['count'] != count):
            raise _Stop('opener_ambiguous')
        return feature

    def position_opener(self, feature, count, *, scroll=False):
        self.guard()
        try:
            status = self.rpc(lambda: self.driver.execute_script(ROOM_POSITION_SCRIPT,
                asdict(self.descriptor), feature['element'], count, scroll))
        except _Stop:
            raise
        except Exception:
            raise _Stop('opener_scroll_uncertain' if scroll else 'opener_position_unreadable') from None
        self.guard()
        if status == 'changed':
            raise _Stop('opener_ambiguous')
        if type(status) is not str or status not in {'clear', 'needs_scroll', 'scrolled'} or (not scroll and status == 'scrolled'):
            raise _Stop('opener_position_unreadable')
        return status

    def open(self):
        self.identity()
        feature = self.checked_feature(self.state())
        count = feature.get('count')
        if type(count) is not int or count < 0:
            self.result['observations'].append({'key': self.descriptor.key, 'status': 'skipped', 'reason': 'count_unqualified'})
            return None
        if count == 0:
            self.result['observations'].append({'key': self.descriptor.key, 'status': 'skipped', 'reason': 'zero_existing_rooms'})
            return None
        original = feature
        if self.position_opener(feature, count) == 'needs_scroll':
            feature = self.recheck_opener(original, count)
            status = self.position_opener(feature, count, scroll=True)
            if status == 'needs_scroll':
                raise _Stop('opener_obscured')
            if status == 'scrolled':
                self.settled_main()
        feature = self.recheck_opener(original, count)
        if self.position_opener(feature, count) != 'clear':
            raise _Stop('opener_obscured')
        self.guard()
        try:
            self.rpc(feature['element'].click)  # Once. Never retry on any outcome.
        except _Stop:
            raise
        except Exception as exc:
            room = next(d.key for d in ROOM_OPTIONS if d == self.descriptor)
            self.result['open_failure'] = {'kind': _open_failure_kind(exc), 'room_key': room}
            raise _Stop('open_uncertain') from None
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            state = self.state()
            if state['overlays']:
                self.modal = self.same_modal(state)
                return count
            self.wait(deadline)
        raise _Stop('open_timeout')

    def read_dialog(self, count):
        deadline, fingerprint, since = time.monotonic() + self.timeout, None, None
        while time.monotonic() < deadline:
            state = self.state()
            self.same_modal(state)
            if state['ready'] and not state['busy']:
                page = self.snapshot()
                after = self.state()
                self.same_modal(after)
                if not after['ready'] or after['busy']:
                    fingerprint, since = None, None
                    self.wait(deadline)
                    continue
                dialogs = page['dialogs']
                if len(dialogs) != 1 or dialogs[0].get('title') != self.descriptor.opener:
                    raise _Stop('dialog_changed')
                dialog = deepcopy(dialogs[0])
                current = _digest({'dialog': dialog, 'limitations': page['limitations']})
                if current != fingerprint:
                    fingerprint, since = current, time.monotonic()
                elif time.monotonic() - since >= self.stable:
                    self.guard()
                    dialog['source'] = {'rxcu': self.reference, 'listing_id': self.listing_id,
                        'page_key': 'details', 'opener': self.descriptor.opener, 'title': self.descriptor.opener,
                        'feature_count': count, 'main_fields_sha256': self.baseline,
                        'reference_checked_before': True,
                        'reference_checked_before_and_after': False, 'pinned_tab_checked': True,
                        'main_fields_unchanged_verified': False, 'close_verified': False,
                        'dom_stability_seconds': self.stable, 'observed_at': page['observed_at'],
                        'limitations': deepcopy(page['limitations']), 'complete': False,
                        'server_writes_verified': False}
                    self.result['pending_dialogs'].append(dialog)
                    groups = dialog.get('room_groups')
                    if (not isinstance(groups, list) or len(groups) != count
                            or 'room_group_structure_unresolved' in page['limitations']
                            or any(not isinstance(g, dict) or type(g.get('index')) is not int
                                   or g.get('index') != i or g.get('title') != f'{self.descriptor.noun} {i}'
                                   for i, g in enumerate(groups, 1))):
                        raise _Stop('room_groups_unresolved')
                    return dialog
            else:
                fingerprint, since = None, None
            self.wait(deadline)
        raise _Stop('dialog_timeout')

    def close(self):
        self.same_modal(self.state())
        self.guard()
        self.escape_attempted = True
        try:
            self.rpc(lambda: _keyboard_escape(self.driver))
        except _Stop:
            raise
        except Exception:
            raise _Stop('escape_uncertain') from None
        self.settled_main(closing=True)
        self.modal = None

    def run(self):
        try:
            self.check_cancel()
            binding = self.hub.editor_bindings.get(self.reference)
            if (not isinstance(binding, dict) or not isinstance(self.listing_id, str)
                    or not re.fullmatch(r'[0-9]+', self.listing_id)
                    or binding.get('listing_id') != self.listing_id
                    or not isinstance(binding.get('handle'), str) or not binding['handle']):
                raise _Stop('binding_changed')
            self.binding = deepcopy(binding)
            self.position = self.get_position()
            if (self.position[0] != binding['handle'] or self.position[2] != self.url
                    or self.position[3]['listing_id'] != self.listing_id
                    or self.position[3]['page_key'] != 'details'):
                raise _Stop('position_changed')
            self.descriptor = self.descriptors[0]
            self.identity()
            self.baseline = self.settled_main()
            self.result['main_fields_fingerprint'] = self.baseline
            for descriptor in self.descriptors:
                self.descriptor = descriptor
                self.escape_attempted = False
                self.settled_main()
                count = self.open()
                if count is None:
                    continue
                dialog = self.read_dialog(count)
                self.close()
                dialog['source'].update(reference_checked_before_and_after=True,
                    main_fields_unchanged_verified=True, close_verified=True)
                self.result['dialogs'].append(dialog)
                self.result['pending_dialogs'].remove(dialog)
                self.result['observations'].append({'key': descriptor.key, 'status': 'observed', 'count': count})
        except (Exception, KeyboardInterrupt) as exc:
            code = exc.code if isinstance(exc, _Stop) else 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'browser_failure'
            # Cleanup is permissible only for a previously verified, still unique
            # modal on the same pinned editor. Cancellation never issues new RPCs.
            if self.modal is not None and not self.escape_attempted and code != 'cancelled':
                try:
                    self.close()
                except (Exception, KeyboardInterrupt) as cleanup:
                    self.result['cleanup_stop_code'] = cleanup.code if isinstance(cleanup, _Stop) else 'browser_failure'
            reason = _REASONS[code]
            if code == 'open_uncertain':
                reason += ' ' + room_open_failure_note(self.result['open_failure'])
            cleanup_code = self.result['cleanup_stop_code']
            if cleanup_code:
                reason += ' Cleanup: ' + _REASONS[cleanup_code]
            self.result.update(status='cancelled' if code == 'cancelled' else 'stopped',
                               stop_code=code, stop_reason=reason)
        return deepcopy(self.result)


def capture_existing_room_options(hub, rxcu, *, expected_listing_id, expected_url,
                                  descriptors=None, timeout=15, poll_interval=.2,
                                  stable_for=.5, cancelled=None, expected_main_fingerprint=None):
    """Return successful and pending full dialogs, gaps, and a safe stop reason.

    ``status == 'partial'`` with no stop reason means this bounded attempt
    finished, NOT that the source is complete. ``dialogs`` passed post-close
    reference and observed-main-field checks; ``pending_dialogs`` did not pass
    all qualification checks and must not be merged into a guarded source page.
    ``expected_main_fingerprint`` binds to an already captured details page:
    pass _digest({k: page[k] for k in ('fields', 'rich_text')}). A mismatch stops
    before any click, preventing new dialogs being paired with stale fields.
    Zero/unsupported counts are explicit gaps. Only verified descriptors from
    ROOM_OPTIONS may be selected. Commands in flight cannot be cancelled, and
    driver transport stalls cannot be bounded by the polling timeout.
    """
    return _RoomCapture(hub, rxcu, expected_listing_id=expected_listing_id,
        expected_url=expected_url, descriptors=descriptors, timeout=timeout,
        poll_interval=poll_interval, stable_for=stable_for, cancelled=cancelled,
        expected_main_fingerprint=expected_main_fingerprint).run()
