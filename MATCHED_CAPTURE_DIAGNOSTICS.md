# Matched-capture failure diagnostics

Unexpected exceptions that reach the matched collector now retain a bounded
`browser_failure` record. A separate `timeout_restore_failure` records a failed
or interrupted attempt to restore the browser's page-load timeout. Either can
be null. Existing saved captures without these fields remain compatible. Their
static summaries appear through the existing stop-reason review; the UI and
launcher are unchanged.

Each record contains only:

- `phase`: a fixed operation label, distinguishing source/page/tab reads,
  navigation prechecks and timeout configuration, the tab click or link GET
  call, navigation verification, and timeout restoration.
- `target_page_key`: one of `details`, `deal_particulars`, `media`, `portals`,
  or null if no known page was established. During reads it is the page being
  collected; after choosing navigation it is the intended destination. It is
  not proof that the browser reached that page.
- `kind`: an exact known Selenium exception category or `other_exception`.
  Known categories are intercepted click, stale element, not interactable,
  out-of-bounds target, timeout, JavaScript error, missing browser window and
  invalid browser session. Cleanup interruptions can be `interrupted`.
  Unknown classes, subclasses and lookalike class names stay generic. Type
  checks use identity only, never exception-metaclass equality or inequality. Selenium
  remains optional for offline use.
- `navigation_attempted_in_capture`: whether any tab click or link GET call was attempted
  during this capture. It is set **before** dispatch and stays true after any
  attempted call, including a call that raised without a visible route change.
  It excludes room-opener clicks and is not evidence of navigation success,
  failure, exact click causality, server persistence or absence of writes.

No exception messages, arbitrary class names, stack traces, URLs, selectors,
element properties, DOM values or credentials enter these diagnostics. Existing
private source observations can still contain source data as before. Internal
capture guard/read errors keep their existing bounded reasons, as do ordinary
cancellations. When a lower-level reader has already replaced an exception with
a generic `CaptureReadError`, the original driver category is not recovered or
invented. These diagnostics cannot explain a previously discarded exception.

The existing timeout setup/restoration calls occur in the same order. If a
primary error, guard stop or cancellation is already in flight, a restoration
failure is recorded separately and cannot replace it. If restoration alone
fails, capture stops before further browser work. Restoration is unverified
even when its call may have taken effect before raising. No cleanup retry is
performed. A cancellation remains the primary outcome when cleanup also fails.

No browser read, click, navigation, scroll, retry, readiness wait or guard has
been added or relaxed. Room collection and the single-page reader are unchanged.
Only already guarded pages/dialogs are retained; incomplete, fidelity and live
readiness flags remain unchanged. An in-flight WebDriver transport stall remains
outside the polling timeout's control.

`tests/test_matched_failure_diagnostics.py` uses synthetic drivers, private local
SQLite records and an offscreen review widget. It covers reads before and after
navigation, known/unknown/spoofed errors, failed setup/restoration, cancellation,
immutable detached results, room payload retention, old records and privacy.
It does not exercise a live browser, Windows or Hub. Follow the existing
read-only test plan after any stop: inspect the current editor manually and do
not retry based on a diagnostic category.
