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
- Confirm publication identifiers on all four original destinations, including Private Property after moderation completed.

Portal requests can remain pending. The app only records a completed relist when Hub returns all required destination identifiers; **Check publication** checks existing requests without resubmitting them. Exact references, addresses, receipts and screenshots remain in the private local workspace.

A separate authorized sale pilot completed manually through the current Hub screens, with all 20 original photos and publication to four destinations. That proves the observed sale screens and saved result; it does not qualify the entire automated sale runner.

The next automated mixed run passed sale form preparation and the fresh source check, removed all original sale portals and confirmed cancellation / mandate-expired archiving. Hub then returned a server error on replacement Save. The create action remains uncertain and the rental has not started. No automatic create retry or publication followed. Recovery through Hub subsequently unarchived the original and restored its website and Property24 identifiers; Private Property is in moderation, and Global rejected restoration because the original external identifier already exists. The original run journal remains unresolved rather than claiming a completed replacement. This run does not qualify automated sale or mixed relisting.

Recovery also exposed a fidelity gap: the townhouse descriptive property type was absent from the capture and therefore from the attempted new form. The observed field is now captured and copied with its exact display text and code. Old townhouse captures without that field stop before source removal, and unknown visible business selectors now produce a blocking limitation instead of silently disappearing. This is a confirmed copy defect; it has not been established as the cause of Hub's server error. The reviewed nonnumeric Erf and scheme values are another unconfirmed cause; the operator authorized restoring those two original numbers.

## Operational observations

Sale and rental have different fields and archive reasons. Rentals omit Deal Particulars. Sales use **Listing has been cancelled → Mandate expired**. The earlier pilots preserved their source dates. The operator subsequently requested a new date rule: Date listed and On market since use the relist day in South Africa, expiry is six calendar months later, and sale mandate dates follow that interval. Rental occupation dates stay unchanged. Original dates remain in the backup, and interrupted runs retain the date contract recorded before removal.

Hub can accept only part of a photo upload. The app sends one image at a time and verifies the received prefix before continuing. Explicit failure permits up to three attempts for that missing image. An uncertain missing image stops the run; absence alone does not authorize another upload.

The current pilot saved media while the replacement remained unpublished. The app checks that state before proceeding and after saving. Historical guides describe Media **Next** as a publishing action, so its behavior must not be generalized beyond the guarded current workflow.

Windows Chrome requires native HTML clipboard data to retain the source description. The app supplies UTF-8 CF_HTML byte offsets and handles a literal leading markdown heading without converting it to bold. Saved-page comparison, not successful paste alone, establishes fidelity.

## Local validation

- Broad suite: 2,050 checks passed; 83 optional browser checks skipped.
- A later focused suite passed 159 checks covering portal moderation, Windows clipboard, brochure review, interrupted copy recovery, runner journal and Qt worker lifetime changes.
- The Windows portable package is built with PyInstaller. Its offline self-check passes inside the actual executable: Qt window construction, bundled driver-manager availability, dynamic Chrome imports and download-protocol version fallback, with no Hub or user-data access. Development attachment/reload is disabled in the packaged launcher.
- The corrected packaged app completed a further mixed collection: a sale with 20 original photos, followed automatically by a rental with 16 in a separate editor tab. Both photo packs matched the original bytes, order and captured captions. Neither download required manual review, and the app opened the prefilled address review when collection finished. This qualifies mixed collection, not full mixed-batch relisting.
- Download observation attaches a temporary session to the selected editor when Selenium's cached session belongs to another tab, retains the target/context/frame/loader checks, and restores the original connection on cleanup. A focused suite passed 100 checks, with three platform-specific skips. It covers two sequential tabs, a closed earlier tab, wrong-frame events, failed attachment and failed cleanup. The corrected executable's two offline package checks and the two-editor live collection repeat passed.
- The main workflow uses **Start batch** to save the references, open Chrome, wait for manual sign-in/OTP and continue collection automatically. **Resume collection** reuses the same path without recreating a batch. Login observation checks the official Hub origin, authenticated page marker and unique loaded search controls, preserves existing editors and never reads credentials. A focused login/UI/collection suite passed 141 checks. On Windows, Start batch opened Chrome; the initial ten-minute wait expired before testing continued, and Resume collection recognized the completed login and collected both listings without another intervention. The default wait now remains active until sign-in or Stop; all 15 login checks pass, including a late login after an hour. The new unbounded default does not change the collection path already exercised live.
- All committed tests use synthetic data. No real listing captures, photos, credentials, OTPs or browser profiles belong in this repository.
- Unexpected browser messages now remain visible instead of being dismissed by WebDriver. They stop the operation with guidance to inspect Chrome and verify the existing outcome, without exposing alert text or retrying. The focused browser and live-runner checks passed (56 tests).
- The townhouse capture/copy fix passed 136 focused capture, field-inventory, form-plan and runner checks. Live DOM inspection confirmed the field's paired visible selector and native selection code. A new full collection and complete automated sale remain required.

## Limits still enforced

The writer currently requires empty custom-feature, party, document and note tables and no floorplans. Unsupported or unresolved fields stop before removing the original. This is not universal support for every Hub listing type.

Unknown creation outcomes, unsupported interrupted states and mismatched saved content require investigation. The app does not erase uncertain journal entries or repeat creation to recover. It cannot atomically transact or roll back Hub changes, and a source recheck cannot prevent another user editing the listing afterwards.

Before a broad production release, qualify an automated sale run, a live multi-listing batch, additional partial-upload failures and the excluded related-record paths. Passing synthetic checks does not establish those live outcomes.
