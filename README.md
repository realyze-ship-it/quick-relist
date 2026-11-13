# Quick Relist

A Windows app for collecting Hub listings, reviewing each address, and processing a batch one listing at a time.

**Pilot status:** the Windows app has processed an authorized rental through collection, reviewed address, portal removal, withdrawal, creation, all 13 original photos, saved-content comparison and confirmed publication to all four original portals. Portal completion is recorded only when Hub returns the destination identifiers. An authorized sale replacement was also completed manually through Hub and recorded locally; the automated sale path still needs a live qualification run. See [live validation](LIVE_TEST_READINESS.md) for the evidence and remaining limits.

## Workflow

1. Enter one or more RXCU references and choose **Create batch**.
2. Choose **Open Hub for sign-in**, complete the login and OTP in the separate RE/MAX Chrome window, and leave that window open.
3. Choose **Collect batch and photos**. The app reads the matching listing, descriptions, agents, supported room options, original photos and portal destinations. It processes references sequentially and checks the photo pack against the downloaded originals.
4. Review the prefilled **Verify address** form for each listing, make any corrections and save the review. This saves a local review only.
5. **Relist reviewed batch** first checks the collected data and shows the reviewed references and addresses for confirmation. For each supported listing, it prepares and checks an unsaved form, rechecks the source, removes the original from its portals, archives sales as **Listing has been cancelled → Mandate expired** and rentals as **Listing has been withdrawn**, saves the replacement, copies and checks its content, then publishes to the original destinations.
6. A submitted portal request remains pending until Hub returns its publication identifier. **Check publication** reads the existing requests without sending them again.

Keep the app's Chrome window open while processing. Switching to another ordinary browser does not transfer the app's signed-in session. Login and OTP are entered directly in Hub; the app does not request them.

If an original brochure summary exceeds Hub's current 250-character limit, the app asks for a reviewed shorter summary before relisting. It preserves the original backup and full main description.

## Current coverage and limits

- Sale and rental use their own prices and status controls. On the operator's requested date rule, new runs set **Date listed** and **On market since** to the South African relist day and **Listing expiry** six calendar months later. Sale **Mandate signed date** follows Date listed and **Mandate expiry** follows Listing expiry. Rental **Occupation date** and rental period stay exactly as collected. If the target month has fewer days, expiry uses its last day.
- The source backup retains every original date. The replacement's date plan is recorded before removal and checked after saving. An interrupted run keeps that recorded plan on resume; a run started by an older build retains its original date contract.
- The collector includes the observed lifestyle and General Property Features checkboxes, primary and secondary agents, rich description, photo captions, and ten room categories. Every nonzero supported room must have its dialog captured before relisting.
- The current writer requires empty custom-feature, party, document and note tables. Listings with related records or floorplans stop before the original is removed. Unsupported or missing fields also stop the run.
- Photos are uploaded individually and compared with the saved originals before sending the next image. This transferred all 13 images in the rental pilot. Only an explicit failed-upload result permits retrying that missing photo, with at most three attempts. Unknown missing uploads, unexpected counts or changed order stop the copy for review.
- A private journal records intent before each significant action. Uncertain actions are not blindly repeated. Fresh reads can reconcile portal removal, an unapplied archive, a received photo and a saved description. An interrupted copy resumes the recorded replacement only after confirming its identity, unpublished state and received photo content/order. It never creates another replacement to recover a copy. Unknown creation outcomes and unsupported recovery states still require investigation. **Check publication** reads already-submitted requests.
- Collection waits for the listing-type menu to appear and confirms **All** is selected before searching a mixed batch. If browser download events are unavailable, collection pauses for explicit local review. After that review closes all app browsers and records a sole completed ZIP's digest, the next collection can resume an already saved source only when that digest matches its original-byte-verified photo pack. The original observation remains unchanged; unrelated, partial or unreviewed downloads cannot release it.
- Hub UI actions are not atomic transactions. Source rechecks cannot prevent another user from changing a listing immediately afterwards. Removal, archive and replacement cannot be automatically rolled back.

The real runner lives in `relist_runner.py` and `live_relist_ui.py`. The older synthetic mutation engine and localhost-only transfer components remain separate; their passing tests do not establish live Hub behavior. Historical design and test notes describe those components, not the qualification status of the new runner.

## Run locally

Requires Python 3.11+, Chrome and a compatible driver. In a virtual environment:

```text
python -m pip install -e ".[browser,test]"
python -m quick_relist
```

For local checks, run `python -m pytest`. DOM fixture tests additionally require Node and jsdom 26.1.0 on `NODE_PATH`. Tests use synthetic data only. The development-only `QUICK_RELIST_DEVELOPMENT=1` launch option reloads collection and relisting handlers when collection is invoked between idle attempts, without discarding the operator's browser login. Reloading sends no Hub actions; packaged builds must not set this flag.

Private batches, captured listing data, reviews, photos and run records stay under `%LOCALAPPDATA%\QuickRelist`. They are not encrypted by this app. Keep them, browser profiles, credentials, OTPs and screenshots out of GitHub.

## Windows package

The portable package starts with `QuickRelist.exe`. Keep its `_internal` folder beside the executable. Chrome and network access are required; Python is bundled. The packaged launcher disables development browser attachment and code reload. Existing local batches and reviews remain available.

To build on Windows after installing the browser dependencies:

```powershell
python -m pip install pyinstaller==6.22.3
.\packaging\build_windows.ps1
```

This produces `dist\QuickRelist\QuickRelist.exe`. It is a portable pilot build, not a signed installer. Remaining release work includes automated sale qualification, a live multi-listing batch, broader interrupted-operation recovery and support for currently excluded related records/floorplans.

The build isolates DLL discovery from unrelated tools on PATH and includes Selenium's dynamic modules and generated download-protocol version directories. `QuickRelist.exe --check-package <report.json>` checks the actual Chrome exports, download-event imports and protocol-version fallback, an offline Qt window and the included driver manager without opening Hub or saved user data. `--check-browser-tools <report.json>` additionally checks local driver discovery, its version-only process and connection configuration without starting Chrome. These checks do not replace live Hub tests. Startup failures are written privately to `%LOCALAPPDATA%\QuickRelist\diagnostics\startup-error.txt`; read-only Hub errors retain message-free local error locations and a diagnostic reference.
