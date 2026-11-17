"""Bounded positioning of an already source-guarded native editor tab.

Only an optional, single centered instant scroll is dispatched here. The caller
retains the sole ordinary WebDriver click and all navigation verification.
"""
from .capture import CaptureReadError, CaptureSafetyError


# No coordinates, DOM values, URLs or hit nodes are returned. Revalidate source,
# selection and native semantics in the same RPC as geometry/optional scroll.
TAB_POSITION_SCRIPT = r"""
return (() => {
  const [sourceUrl, sourceLabel, targetLabel, expected, scroll] = arguments;
  if (location.href !== sourceUrl || document.readyState !== 'complete' ||
      typeof sourceLabel !== 'string' || typeof targetLabel !== 'string' ||
      sourceLabel === targetLabel || !expected || typeof expected !== 'object') return 'changed';
  const visible = e => {
    if (!e || !e.getClientRects().length) return false;
    for (let p=e; p; p=p.parentElement) {
      const s=getComputedStyle(p);
      if (p.hidden || p.getAttribute('aria-hidden') === 'true' || s.display === 'none' ||
          s.visibility === 'hidden' || s.visibility === 'collapse' || Number(s.opacity || 1) === 0) return false;
    }
    return true;
  };
  if ([...document.querySelectorAll('[aria-busy="true"],[role="progressbar"],[role="dialog"],[role="alertdialog"],[aria-modal="true"],dialog[open],[role="listbox"],[role="menu"]')].some(visible)) return 'changed';
  const labels=['Listing particulars','Deal Particulars','Media','Portals'];
  const keys=Object.keys(expected), e=expected[targetLabel], source=expected[sourceLabel];
  if (!keys.length || keys.some(k => !labels.includes(k)) || !e || !source) return 'changed';
  const mains=[...document.querySelectorAll('main,[role="main"]')].filter(visible);
  if (mains.length !== 1) return 'changed';
  const main=mains[0], list=e.closest?.('[role="tablist"]');
  if (!list || !main.contains(list)) return 'changed';
  const native = (node,label) => node instanceof HTMLButtonElement &&
    node.ownerDocument === document && node.isConnected && node.getRootNode() === document &&
    main.contains(node) && node.parentElement === list && visible(node) &&
    node.getAttribute('type') === 'button' && node.getAttribute('role') === 'tab' &&
    node.textContent === label && !node.disabled && !node.matches(':disabled') && !node.hasAttribute('disabled') &&
    !node.hasAttribute('aria-label') && !node.hasAttribute('aria-labelledby') &&
    node.getAttribute('aria-disabled') !== 'true' && !node.isContentEditable &&
    node.getAttribute('aria-selected') === (label === sourceLabel ? 'true' : 'false') &&
    node.getAttribute('tabindex') === (label === sourceLabel ? '0' : '-1');
  const actual=[...document.querySelectorAll('button[role="tab"][type="button"]')].filter(visible);
  const known=actual.filter(n => labels.includes(n.textContent));
  if (known.length !== keys.length || keys.some(k => !native(expected[k],k) ||
      known.filter(n => n.textContent === k).length !== 1 || !known.includes(expected[k])) ||
      actual.filter(n => n.getAttribute('aria-selected') === 'true').length !== 1 ||
      !actual.includes(source)) return 'changed';
  // Observed MUI native buttons may contain inert inline label/ripple spans.
  // Do not borrow another interactive element as the hit target, even if it
  // preserves aggregate text or sits outside the hit-point subtree.
  const inertSpan = n => n.tagName === 'SPAN' && n.ownerDocument === document &&
    n.isConnected && !n.isContentEditable &&
    ![...n.attributes].some(a => /^on/i.test(a.name) ||
      ['tabindex','href','contenteditable','disabled','aria-disabled'].includes(a.name)) &&
    (!n.hasAttribute('role') || ['none','presentation'].includes(n.getAttribute('role')));
  if ([...e.querySelectorAll('*')].some(n => !inertSpan(n))) return 'changed';
  const rects=[...e.getClientRects()], w=window.innerWidth, h=window.innerHeight;
  const cw=document.documentElement.clientWidth, ch=document.documentElement.clientHeight;
  if (![w,h,cw,ch].every(n => Number.isFinite(n) && n > 0) || !rects.length ||
      rects.some(r => ![r.x,r.y,r.width,r.height,r.x+r.width,r.y+r.height].every(Number.isFinite) ||
        r.width < 1 || r.height < 1)) return 'invalid';
  // WebDriver uses the FIRST client rectangle, clips to the initial viewport
  // and floors its in-view center. A union/bounding center is not equivalent.
  const r=rects[0], left=Math.max(0,r.x), right=Math.min(w,r.x+r.width),
    top=Math.max(0,r.y), bottom=Math.min(h,r.y+r.height);
  const x=Math.floor((left+right)/2), y=Math.floor((top+bottom)/2);
  let clear=false;
  if (right-left >= 1 && bottom-top >= 1 && x >= left && x < right && y >= top && y < bottom &&
      x < cw && y < ch) {
    const hit=document.elementFromPoint(x,y);
    clear=hit === e || !!(hit && e.contains(hit) && inertSpan(hit));
  }
  if (clear) return 'clear';
  if (scroll !== true) return 'needs_scroll';
  e.scrollIntoView({behavior:'instant',block:'center',inline:'center'});
  return 'scrolled';
})();
"""


def prepare_native_tab(driver, *, source_url, source_label, target_label,
                       tabs, recheck, cancelled, phase):
    """Return the unchanged target after at most one explicit scroll.

    ``tabs`` and ``recheck()`` map observed labels to WebElements. The callback
    must freshly guard binding, route, identity, readiness and tab selection.
    Exceptions retain the caller's static diagnostic phase. Neither a failed
    scroll nor a later uncertain native click may be retried by the caller.
    """
    expected = dict(tabs)

    def check():
        cancelled()
        current = recheck()
        if set(current) != set(expected) or any(current[k] != expected[k] for k in expected):
            raise CaptureSafetyError('The observed editor tabs changed during positioning. No tab was clicked.')
        cancelled()

    def probe(scroll=False):
        check()
        status = driver.execute_script(TAB_POSITION_SCRIPT, source_url, source_label,
                                       target_label, expected, scroll)
        check()
        if type(status) is not str or status not in {'clear', 'needs_scroll', 'scrolled', 'changed', 'invalid'}:
            raise CaptureReadError('The editor tab positioning outcome could not be verified. No tab was clicked or retried.')
        if status == 'changed':
            raise CaptureSafetyError('The editor tab source, selection or native control changed. No tab was clicked.')
        if status == 'invalid' or (not scroll and status == 'scrolled'):
            raise CaptureReadError('The editor tab position could not be verified safely. No tab was clicked.')
        if scroll and status not in {'clear', 'scrolled'}:
            raise CaptureReadError('The editor tab scrolling outcome is unverified. No tab was clicked or retried.')
        return status

    phase('tab_position')
    if probe() == 'needs_scroll':
        phase('tab_scroll')
        probe(scroll=True)
    phase('tab_position_recheck')
    if probe() != 'clear':
        raise CaptureSafetyError('The editor tab remains obstructed after its bounded view check. No tab was clicked.')
    return expected[target_label]
