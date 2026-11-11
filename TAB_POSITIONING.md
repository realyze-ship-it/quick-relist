# Bounded native editor-tab positioning

This is a read-only capture safeguard. It does not qualify account ownership,
complete source fidelity, absence of server writes or live relisting.

## Evidence and scope

The authorized capture on c451923 accepted four room dialogs/six groups and
then stopped before leaving Listing particulars with a generic browser
read/navigation error. Its underlying exception was discarded, so the cause
cannot be recovered. Subsequent passive inspection found Deal Particulars
partly behind the fixed Hub header/search and RE/MAX PreView banner. No tab
click was retried. Geometry observed after docked developer-tools reflow is not
the original geometry and is not embedded in this implementation.

The observed target is an inactive native `button`, `type=button`, `role=tab`,
with exact textContent, `aria-selected=false` and `tabindex=-1`, directly inside
a tablist under the main editor. Listing particulars alone is selected. Class
names and hardcoded screen coordinates do not decide the target. Existing
matched binding, numeric editor route, pinned window/window-set, visible
identity, tab-set and readiness guards remain required.

## Bounded behavior

Before the sole existing ordinary WebDriver tab click:

1. Retain the observed known tab handles. Freshly recheck source binding,
   route, identity, readiness, the same set/handles and selection before and
   after every position RPC. A changed node is not replaced with a new target.
2. In that RPC, verify the literal source URL, document readiness, no visible
   busy/dialog/menu/modal state, the unique main, exact known native buttons,
   source-only selection, native owner document and connectedness. Targets
   with alternate accessible labels or interactive descendants fail closed.
   Inert span descendants, including MUI ripple spans, may qualify. Descendant
   roles, tabindex, editable/link or inline-event semantics cannot redirect
   a click. A hit must be the verified button itself or its inert span child.
3. Compute the WebDriver in-view point from its **first actual client
   rectangle**, clipped to the viewport and floored. Require finite positive
   viewport dimensions and finite rectangles at least one CSS pixel in both
   dimensions. Reject empty/tiny rectangles; a sliver, out-of-view point or
   scrollbar point cannot qualify as clear. An unrelated overlay, ancestor,
   iframe, another tab or missing hit cannot qualify.
4. If clear, do not explicitly scroll. Otherwise request at most one instant
   centered block-and-inline `scrollIntoView` of that exact validated button.
   A newly clear point on the scroll RPC causes no scroll. Do not use offsets,
   hide overlays, alter styles/fields, synthesize navigation, force a JavaScript
   click or change to an alternate target.
5. Recheck the complete source/selection/target state and actual point again.
   Persistent obstruction, invalid geometry, changed state, cancellation or
   failed/uncertain scroll stops before clicking. No click or scroll retry is
   made. The existing single native click and postnavigation verification
   remain unchanged; a later uncertain click is never retried.

Calls are not atomic. Layout/DOM can change after a passing probe, and ordinary
WebDriver clicking may perform its own implicit scrolling. Source/DOM equality
cannot prove no scroll-induced page events or server writes. The existing
polling limits cannot bound an in-flight driver transport stall.

## Diagnostics and privacy

The frozen matched-capture diagnostics remain in place. Three static phases
identify the initial position read, the scroll call or its associated guards,
and the final position recheck. The scroll phase does not prove that scrolling
was attempted or completed. Native navigation-attempt tracking still becomes
true only immediately before the tab click or link GET, not for a scroll.
Exceptions remain typed using the existing exact allowlist, with no exception
text, arbitrary class names, geometry, selectors, hit nodes or private source
values. Fixed guard/read messages stop invalid or uncertain results. Timeout
restoration failure remains separate and cannot replace a primary error,
guard stop or cancellation. No timeout cleanup retry was added.

## Qualification

- `tests/test_tab_positioning.py` drives the real matched collector with fake
  source/driver responses. It checks clean/one-scroll paths, all four pages,
  guarded-source and tab changes, persistent obstruction, uncertain results,
  cancellation, exact click counts, typed private diagnostics and cleanup
  precedence. Existing diagnostics tests run against the same integration.
- `tests/test_tab_positioning_dom.py` runs the exact production script in
  jsdom with explicit synthetic geometry and hit-testing. This is not native
  browser evidence.
- `tests/test_tab_positioning_chrome.py` is opt-in native script/helper and
  ordinary-click qualification on generated localhost HTML. It exercises
  actual fixed-header, horizontal-scroller and offscreen geometry, inert
  spans/ripples, persistent obstruction, invalid dimensions, changed state,
  cancellation and a late click interception. It is not the full production
  matched controller or real Hub. The normal route parser rejects the fixture.

Native tests skip before creating a browser/profile/server unless
`QUICK_RELIST_CHROME_TESTS=1` and an explicit existing
`QUICK_RELIST_CHROMEDRIVER` are supplied. An existing browser can be selected
with `QUICK_RELIST_CHROME_BINARY`. No installation or driver download occurs.
The native module is appended to the existing single focused CI command with
its unchanged job and six-minute timeout. Its presence is not evidence that
CI or native qualification has run.

References: [WebDriver interactability](https://www.w3.org/TR/webdriver/#element-interactability),
[WebDriver click](https://www.w3.org/TR/webdriver/#element-click),
[scrollIntoView](https://developer.mozilla.org/en-US/docs/Web/API/Element/scrollIntoView),
[elementFromPoint](https://developer.mozilla.org/en-US/docs/Web/API/Document/elementFromPoint).
