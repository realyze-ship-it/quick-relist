# Live validation and remaining coverage

This records the current real Hub workflow. Older read-only and synthetic-engine design notes are historical; they do not describe the current desktop controls.

## Verified live

An operator-authorized rental pilot has used the app to:

- Collect the listing particulars, original dates, two agents, descriptions, feature options, six nonzero room dialogs and all 13 original photos.
- Save the operator's edited address locally, then prepare and check the corresponding rental form.
- Remove the original from its four portals and archive it using the rental-only **Listing has been withdrawn** option.
- Create one replacement and resume that same identity after interrupted media steps.
- Upload all 13 images individually, check downloaded content and order, and avoid repeating an upload whose receipt was confirmed by a fresh read.
- Save the full main description with its original formatting and literal heading markers, plus an explicitly reviewed shorter brochure summary required by Hub's 250-character limit.
- Reopen the replacement and verify particulars, room options, descriptions, captions and all photos before submitting publication to the original four destinations.

Portal requests can remain pending. The app only records a completed relist when Hub returns all required destination identifiers; **Check publication** checks existing requests without resubmitting them. Exact references, addresses, receipts and screenshots remain in the private local workspace.

A separate authorized sale pilot completed manually through the current Hub screens, with all 20 original photos and publication to four destinations. That proves the observed sale screens and saved result; it does not qualify the entire automated sale runner.

## Operational observations

Sale and rental have different fields and archive reasons. Rentals omit Deal Particulars. Sales use **Listing has been cancelled → Mandate expired**. Source dates are preserved, never renewed or invented.

Hub can accept only part of a photo upload. The app sends one image at a time and verifies the received prefix before continuing. Explicit failure permits up to three attempts for that missing image. An uncertain missing image stops the run; absence alone does not authorize another upload.

The current pilot saved media while the replacement remained unpublished. The app checks that state before proceeding and after saving. Historical guides describe Media **Next** as a publishing action, so its behavior must not be generalized beyond the guarded current workflow.

Windows Chrome requires native HTML clipboard data to retain the source description. The app supplies UTF-8 CF_HTML byte offsets and handles a literal leading markdown heading without converting it to bold. Saved-page comparison, not successful paste alone, establishes fidelity.

## Local validation

- Broad suite: 2,050 checks passed; 83 optional browser checks skipped.
- A later focused suite passed 159 checks covering portal moderation, Windows clipboard, brochure review, interrupted copy recovery, runner journal and Qt worker lifetime changes.
- The Windows portable package is built with PyInstaller. Its offline self-check passed inside the actual executable: Qt window construction and bundled driver-manager availability, with no Hub or user-data access. Development attachment/reload is disabled in the packaged launcher. Native interaction with the new executable was unavailable to the automation tool; this check is not a full packaged live Hub run.
- All committed tests use synthetic data. No real listing captures, photos, credentials, OTPs or browser profiles belong in this repository.

## Limits still enforced

The writer currently requires empty custom-feature, party, document and note tables and no floorplans. Unsupported or unresolved fields stop before removing the original. This is not universal support for every Hub listing type.

Unknown creation outcomes, unsupported interrupted states and mismatched saved content require investigation. The app does not erase uncertain journal entries or repeat creation to recover. It cannot atomically transact or roll back Hub changes, and a source recheck cannot prevent another user editing the listing afterwards.

Before a broad production release, qualify an automated sale run, a live multi-listing batch, additional partial-upload failures and the excluded related-record paths. Passing synthetic checks does not establish those live outcomes.
