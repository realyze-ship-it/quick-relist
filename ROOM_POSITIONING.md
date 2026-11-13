# Bounded room-opener positioning

This is a read-only preparation safeguard, not live relisting qualification.
It does not change fields, click dialog buttons, enable any mutation provider,
or repair room grouping. The existing exception diagnostic remains separate.

## Evidence and limits

An earlier Windows capture stopped with an uncertain opener result, without
retaining the exception class. Later passive observation found the bedroom
caption near the lower viewport edge and a fixed, transparent Help iframe
footprint overlapping that region. A subsequent fresh capture with a normally
positioned viewport opened the bedroom dialog, then stopped at a separate
room-grouping mismatch. This does not establish the original exception's
cause, prove a historical intercepted click, or prove no server writes.

## Behavior

- The existing exact caption, five-category allowlist, count/control shape,
  matched binding, numeric editor route, tab set, visible reference and
  observed main-field fingerprint checks remain required.
- Before the ordinary WebDriver click, the app rechecks the structural probe
  and computes the click point from the **first** actual client rectangle,
  clipped to the initial viewport and floored as specified by WebDriver.
  This qualifies the click point, not visibility of the whole control.
- Rectangles and viewport dimensions must be finite and positive. Client
  rectangles smaller than one CSS pixel, empty rectangles and points landing
  outside the clipped area or on the scrollbar region cannot qualify.
- The observed caption contains text nodes only, with no element children.
  That leaf-only shape is required even when an empty or nested child would
  preserve the aggregate caption text. A read-only `elementFromPoint` hit must
  be the verified caption itself. No descendant, iframe, unrelated overlay,
  ancestor or missing hit qualifies. Dispatch always targets that caption.
- A clean point causes no explicit scroll. Otherwise, valid geometry permits
  at most one `scrollIntoView` request with instant behavior and centered block
  and inline alignment. No offsets, forced JavaScript click, style changes,
  overlay dismissal/removal, navigation or alternative click target is used.
  Source, main-field, exact node, count and shape checks precede that request.
- Following a scroll, the observed main fields must settle unchanged within
  the existing bounded wait. Identity and state are read again, the same node
  is reacquired and revalidated, and the exact click point is hit-tested again.
  A remaining obstruction stops capture before any click. A zero/unsupported
  initial count retains its existing explicit gap behavior; a count that
  changes during positioning stops rather than becoming a skipped room.
- There is one final fresh source/field/feature/point check on the no-scroll
  path too. These browser calls are **not atomic**. A later layout or DOM
  change may still cause the sole ordinary WebDriver click to fail. Its
  existing typed diagnostic and uncertain-outcome handling are unchanged;
  neither a click nor a scroll is retried after an uncertain outcome.

The new saved failure text is selected from fixed, allowlisted messages. It
contains no rectangles, coordinates, hit nodes, URLs, selectors, values,
exception messages or arbitrary class names. Existing partial source capture
records remain private and can contain source data as before.

Polling waits remain bounded by the existing limits. An in-flight driver
transport stall cannot be bounded by those polling limits. Scroll-induced
page events are possible; DOM field equality cannot prove no server writes.

## Qualification

- `tests/test_room_positioning.py` drives the real room orchestrator and a
  matched-collector integration with synthetic browser responses. It covers
  one/no scroll, the sole click, persistent obstruction, source/node/count/
  field/tab changes, cancellation, unknown or stale positioning outcomes,
  typed later click failures, no retries and static/private diagnostics.
- `tests/test_room_positioning_dom.py` executes the exact production script in
  jsdom. Geometry and hit-testing are explicit synthetic models, not native
  browser evidence. Cases cover partially clipped and multiple rectangles,
  first-rectangle/flooring behavior, fractional and tiny dimensions,
  scrollbars, invalid dimensions, revalidation, split text-node compatibility
  and rejection of all element descendants, including empty/nested buttons.
- `tests/test_room_positioning_chrome.py` is optional native **script and
  ordinary-click** qualification against generated localhost HTML. It uses
  actual Chrome geometry, a transparent fixed iframe footprint, a persistent
  obstruction, an offscreen caption, tiny rectangles, leaf-only captions, changed nodes/counts
  and a late interception. It does not run the production full controller
  against localhost and asserts that the unchanged Hub route parser rejects
  the fixture URL. It cannot qualify real Hub timing, Windows, source
  completeness, account ownership or live relisting.

Native tests skip before creating a server/profile/browser unless
`QUICK_RELIST_CHROME_TESTS=1` and an existing explicit
`QUICK_RELIST_CHROMEDRIVER` are provided. An existing Chrome may be selected
with `QUICK_RELIST_CHROME_BINARY`. No browser or driver installation/download
is attempted. The module is included in the repository's existing single
focused native CI command, not a new job. Adding it is not evidence that CI or
native Chrome has run.

## Primary references

- [WebDriver element interactability](https://www.w3.org/TR/webdriver/#element-interactability)
- [WebDriver element click](https://www.w3.org/TR/webdriver/#element-click)
- [Element.scrollIntoView](https://developer.mozilla.org/en-US/docs/Web/API/Element/scrollIntoView)
- [Document.elementFromPoint](https://developer.mozilla.org/en-US/docs/Web/API/Document/elementFromPoint)
