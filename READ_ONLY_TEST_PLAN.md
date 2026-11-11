# Windows read-only test

This tests preparation and review, **not relisting**. Removal, archiving,
creation and publishing are unavailable. Use only a listing you are allowed to
inspect. Do not change its fields or portal state during this test.

## Start and select the source

1. Start the qualified read-only test build. Keep its private data directory
   separate from any other build or existing workspace.
2. In **Connection & safety**, choose **Open Hub for sign-in**. Sign in yourself
   in the new Chrome window. The app does not need your password or OTP.
3. Create a saved batch with the chosen RXCU and select that job.
4. Choose **Find RXCU in Hub (read only)**, then **Open exact matching editor**.
   Confirm the reference shown in the browser matches the selected local job.
   Stop if there is no unique match, the account is wrong, or the page is not
   the expected listing. Do not work around a blocked identity check.

### If opening times out but the editor appears

In builds with **Recheck opened editor (read only)**, keep the original selected
job, results page and newly opened browser tab unchanged. Once the editor has
loaded, press **Recheck opened editor (read only)**. It reads the existing tabs;
it does not click Edit Listing again, navigate to a URL or open another browser.
Missing headings may remain pending, and you may explicitly recheck again.
Only a successful recheck enables matched capture for this opening.

This is limited to the current app/browser session and an opening the app
already initiated from a verified exact row and numeric editor link. It cannot
adopt a manually opened editor or recover an opening from a prior app session.
A changed driver, results document/row, tab set, editor route or visible
conflicting heading blocks recovery. Do not repair a conflicting tab and retry
to override the check. Close all dedicated Quick Relist browser windows and
restart normally if the app directs you to do so. There is no force/reset path.
Another search or editor opening stays unavailable while an intent is unresolved.
Only conflicts observed during these guarded reads are detected. The app does
not record full navigation history and cannot prove that a tab never changed
between reads. An initially blank tab can remain pending until the expected
route loads; returning to a blank route after that route was observed blocks
the intent rather than being treated as initial loading.

**Save current page (incomplete)** remains available for an explicitly approved,
unverified partial observation. It does not establish a matched binding. Do not
change editor routes while expecting the outstanding opening to remain
recheckable. Recheck is a usability safeguard around an uncertain opening, not
a diagnosis of the original timeout or a native-crash fix. Account ownership,
complete source coverage, original media and live relisting remain unverified.

### Qualified transparent duplicate title

The observed editor can show its main H4 title while keeping the matching
outer H2 title transparent. A qualified build may use that outer title only
as consistency evidence. The main H4 must still be fully visible and exactly
match the selected reference, with the original row/link, numeric route,
browser session, tabs and local job guards unchanged. This is not proof of
two visible titles, account ownership or complete source capture.

The exception is limited to one H2 outside MAIN with direct text and no child
elements or hidden/aria-hidden attributes, block/visible computed style,
self opacity exactly zero and positive-area rectangles. Every ancestor must
have readable display/visibility and a finite positive computed opacity.
The authoritative main H4 must also be the observed direct-text leaf with no
element children. Nested elements cannot supply its reference, even if they
appear visible. It needs positive-area rectangles and readable,
visible styles with finite positive opacity on itself and its ancestors.
Wrong or duplicate visible H4 identities remain contradictions even when
their geometry or style evidence is unqualified; they are not filtered away.
Hidden or otherwise unqualified ancestors, wrong or duplicate eligible
titles, missing/hidden main titles and changed bindings cannot approve a
match. The normal visible-title branch is unchanged; a transparent fallback
never overrides wrong or duplicate visible outer-title evidence.

Do not change page styles or fields to satisfy these checks. A pending or
blocked result remains unverified. Saved captures are still partial, and no
listing mutation or live readiness is enabled by either title presentation.

## Capture and check

5. Choose **Capture matched editor tabs**. Read the confirmation. Supported
   existing room-option dialogs may open and close with Escape. Do not type,
   navigate, switch tabs or operate another browser window while capture runs.
6. Open **Review partial observations** and **Review copied listing fields**.
   Check the source reference, values, room grouping and capture gaps. Missing,
   ambiguous and unresolved values are expected to remain visible. A successful
   collection is still incomplete and does not approve a relist.
7. Use **Prepare tentative address** if you want to try local address review.
   Confirm that the browser listing stays unchanged. This draft is not an
   approval to change an address or archive a listing.
8. If capture stops or a dialog remains open, stop the test and record the
   message. Do not click Done, Save, Add, remove, archive or publish to recover.
   Keep the partial observations for review and close the app's browser when
   you have finished inspecting the outcome.

## Optional photo-pack check

Only do this if a fresh download is wanted and authorized. An existing pack
does not need another download just to repeat the test.

1. With capture finished, manually select the browser's **Media** tab.
2. Choose **Download photo pack** once. Let the attempt finish. Check whether
   the app assigns a verified byte receipt or asks for manual review.
3. A timeout or uncertain result is not proof that no download happened.
   Inspect the app's download folder and browser history before any further
   request. **Review previous download** preserves the files and does not retry.
4. Do not treat the number of ZIP entries as proof of image order, captions,
   original resolution, cover selection, floorplan identity or completeness.

## Saved replacement comparison

In builds that include **Compare with a saved replacement**, open it from the
original capture review. Explicitly choose a different saved listing job and
then its exact capture. The comparison reads local records only. It does not
create a replacement. Equal observations do not establish a complete copy;
changed values are not approved. Review gaps and chronology as well as the
field table.

## Useful test feedback

Report the build identifier, step, visible error text and whether the expected
page or dialog remained open. Do not post property records, screenshots,
photos, downloads, session files or local databases in a public issue or PR.
Keep them private. No successful step in this plan qualifies live relisting.

## Optional offline developer qualification

`tests/test_editor_recheck_chrome.py` exercises the exact read-only source and
heading scripts on generated localhost HTML in a disposable Chrome profile.
It covers real DOM visibility and reference semantics without visiting Hub or
weakening controller origin checks. It does not qualify the entire controller,
Hub timing, the Windows app, account ownership or complete source capture.

With an existing compatible Chrome/driver and Selenium already installed, set
`QUICK_RELIST_CHROME_TESTS=1` and `QUICK_RELIST_CHROMEDRIVER` to the existing
driver executable, then run:

`python -m unittest discover -s tests -p test_editor_recheck_chrome.py -v`

`QUICK_RELIST_CHROME_BINARY` may select an existing Chrome binary. The tests
never install packages or download a driver. Without explicit opt-in, the class
skips before creating a local server, browser profile or browser.
