# Connector implementation checklist

The desktop shell has experimental read-only Hub navigation, but the safety model does not implement live relisting. A real connector must be implemented and verified before enabling any listing change. The following observations are a guide, not a stable API or an exhaustive mapping:

- Manage Listings filters can hide results. Confirm transaction type, status, office and agent filters before declaring an RXCU absent; exact-match and identity-check a single listing.
- Edit and archive open new tabs. Track browser window handles, verify the page and return to the correct listing.
- Capture every relevant editor tab, nested dialog, party, document, note, agent and portal selection. Preserve media order, captions, cover, floorplans and all descriptions. Verify downloaded photo-pack files, not only links or thumbnails. If access or capture is incomplete, stop before portal removal.
- Sale and rental both have address verification. Suburb is an autocomplete selection, not merely typed text. Operator must approve corrections before any removal or archive.
- Portal statuses initially load asynchronously. Verify each originally selected portal has actually been removed; do not infer removal from clicking a control.
- Archive uses cancellation and the mandate-expired reason. Confirm the identity and resulting status.
- Save creation particulars before later tabs. Preserve sale/rental differences and mandatory dates; do not invent mandate data. Capture the new ID as soon as it exists. Upload and compare all details and media before submission. Publication can remain pending after submission.
- Never retry a possibly executed archive, create or publish operation without reconciling the current Hub state. Retain local source data securely when manual recovery is needed. This system cannot promise rollback after archiving.

Unverified selectors, completeness rules and actual portal outcomes must fail closed. These observations contain no real listing data.

Live connector qualification must also demonstrate an atomic server-side expected-revision precondition for each destructive operation. A read followed by a click is not an atomic concurrency safeguard. Hub support for this is unknown; do not set the connector ready or enable live writes until the requirement is met and independently verified. Required address values and suburb selection must be validated against the actual Hub form, not inferred from a partial inspection.

The read-only editor opener checks a unique RXCU row, its controlled Select menu, the numeric editor URL and both visible reference headings. Account ownership, result completeness and capture fidelity remain unverified. The photo-pack helper cannot attribute a ZIP to a click with browser events, so it leaves the file unassigned for manual review and blocks subsequent automatic download attempts. The local recovery workflow shares a process lock with requests, checks the reviewed file inventory again before clearing the pause, preserves the original intention in a private receipt and leaves all files in place. It requires successful closure of the controlled browser plus operator confirmation that every older/orphaned Quick Relist browser is closed. It cannot independently verify another process’s browser state or guarantee against malicious same-user file changes. Invalid or linked records keep the pause. Windows and actual download/recovery behavior remain untested.
