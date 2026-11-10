# Quick Relist

A local-first Windows desktop workspace for preparing relisting jobs one listing at a time. This is an **early, non-operational development build**. It cannot yet capture, remove, archive, recreate, or publish real Hub listings. Do not use it to relist property.

## Intended workflow

1. Enter one or more RXCU references and open the Hub session. Sign in and enter OTP yourself.
2. Capture the complete original listing and full-size media, verify the exact source and original portal choices, then review and approve the replacement address before any destructive operation.
3. Recheck the original; remove it from the selected portals and verify removal; archive it using “Listing has been cancelled” and “Mandate expired”.
4. Recreate the same sale or rental listing with the approved address and captured details and media. Compare the saved replacement with the original before publishing to the approved portals. Distinguish submission from confirmed publication.

An optional read-only inspector opens a separate visible Chrome session for manual sign-in and can inspect controls on one editor page. It does not create a complete listing backup or make changes. The Hub connector and the above workflow are **not complete**. Do not represent this scaffold as working automation. The safety state machine is a development aid, not evidence of an end-to-end verified process. Hub screens may change; full coverage, field fidelity, downloads, account checks, asynchronous portal states, uncertain action recovery, and live publishing all require implementation and controlled testing. If an operation has an uncertain outcome, check Hub before trying again. Archiving and replacement are not atomic and cannot be automatically rolled back.

## Run locally

Requires Python 3.11+ and PySide6. From a virtual environment, install with `python -m pip install -e .` then run `python -m quick_relist`. For the read-only Chrome inspector, install `python -m pip install -e ".[browser]"`. For tests: `python -m pip install -e ".[test]"` then `python -m pytest`. Selenium may need a compatible Chrome and driver. Do not enter passwords or OTPs into this app; sign in directly in the browser.

Batch records are stored locally under the operating system’s per-user data directory (on Windows, `%LOCALAPPDATA%\QuickRelist`). They are not encrypted by this app. Limit access to the PC and use appropriate disk protection. The browser inspector displays a partial page only in memory; it is not a source backup.

All tests must use synthetic data. Do not test destructive actions against a real listing without specific permission. Never commit real listing records, images, screenshots, credentials, OTPs, browser profiles, local databases, or logs. Local data may include private property information and should be protected and backed up by the operator.

## Before a release

Implement and validate the real connector against current Hub screens; verify all source fields, nested dialogs and full-size media, address edits, portal synchronization, safe interruption and uncertain outcome reconciliation. Test a controlled sale and rental end-to-end only with explicit permission for the particular listings. Validate the Windows installer, signed distribution and dependency licensing, including Qt obligations. No installer or releases are currently provided.
