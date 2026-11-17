"""Sequential, read-only collection from the real Hub UI into private storage."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import time

from .core import normalize_rxcu
from .matched_capture import MatchedHubCapture
from .original_media import download_originals, observed_originals, verify_pack_bytes
from .media import import_media_zip


class CollectionStopped(RuntimeError):
    pass


class ListingNotFound(CollectionStopped):
    pass


def latest_collection(drafts, job_id):
    for row in getattr(drafts, 'iter_captures', drafts.list_captures)(job_id):
        if row['capture'].get('schema_version') == 'hub-collected-listing/v1':
            return row
    return None


def required_pages(capture):
    """Rental editors omit Deal Particulars; never manufacture that page."""
    types = [f.get('selected_options', []) for p in capture.get('pages', [])
             if p.get('page_key') == 'details' for f in p.get('fields', [])
             if f.get('name') == 'listing_type_id']
    codes = [options[0].get('value') for options in types if len(options) == 1]
    if codes == ['2']:
        return {'details', 'media', 'portals'}
    if codes == ['1']:
        return {'details', 'deal_particulars', 'media', 'portals'}
    raise CollectionStopped('The source listing type is missing or ambiguous.')


def _wait_search(hub, reference, cancelled, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled():
            raise CollectionStopped('Collection stopped. Saved information has been kept.')
        found = []
        for row in hub.driver.find_elements('css selector', '[role="row"]'):
            if row.is_displayed():
                refs = [normalize_rxcu(m.group(1)) for p in row.find_elements('tag name', 'p')
                        if p.is_displayed() and (m := re.fullmatch(r'Ref:\s*(RXCU-?[0-9]{3,20})', p.text.strip(), re.I))]
                if refs == [reference]:
                    found.append(row)
        if len(found) > 1:
            raise CollectionStopped('Hub shows more than one exact matching listing. Review the results.')
        if len(found) == 1:
            return
        time.sleep(.2)
    raise ListingNotFound('The reference did not appear in Hub. Check the listing filters and your sign-in.')


def collect_listing(hub, drafts, job, media_root, *, cancelled=lambda: False, progress=lambda message: None):
    started = time.monotonic()
    reference, job_id = normalize_rxcu(job['rxcu']), job['id']
    if hub.driver is None:
        raise CollectionStopped('Open Hub and sign in first.')
    try:
        handles = hub.driver.window_handles
    except Exception:
        raise CollectionStopped('The app’s Hub browser connection closed. Restart Quick Relist, sign in and keep its RE/MAX window open while collection runs.') from None
    if not handles:
        raise CollectionStopped('The app’s Hub browser window was closed. Restart Quick Relist and leave the new RE/MAX window open after sign-in.')
    progress(f'{reference}: finding the listing')
    binding = hub.editor_bindings.get(reference)
    if binding and binding['handle'] in hub.driver.window_handles:
        if hub.driver.current_window_handle != binding['handle']:
            hub.driver.switch_to.window(binding['handle'])
        hub.verify_editor_reference(reference, binding['listing_id'])
    else:
        try:
            hub.search_reference(reference, all_listing_types=True)
        except ValueError as exc:
            raise CollectionStopped(str(exc)) from None
        _wait_search(hub, reference, cancelled)
        hub.open_listing_editor(reference, timeout=30)
        binding = hub.editor_bindings[reference]
    progress(f'{reference}: copying details, descriptions, room options and captions')
    reader = MatchedHubCapture(hub, reference, timeout=30, cancelled=cancelled,
                               include_room_options=True, include_media_captions=True,
                               include_general_features=True, include_suburb_selection=True)
    from .portal_ui import read_portals
    media_backup = None
    portals = None

    def collect_page(page, url):
        nonlocal media_backup, portals
        if page['page_key'] == 'media':
            media_backup = _backup_media(hub, drafts, job_id, page, media_root,
                                        guard=lambda: reader._identity(url), cancelled=cancelled, progress=progress)
        elif page['page_key'] == 'portals':
            portals = read_portals(hub.driver, guard=lambda: reader._identity(url), timeout=45)

    try:
        capture = reader.capture(on_page=collect_page, start_on_details=True)
    except Exception:
        drafts.save_capture(job_id, reader.get_result())
        raise
    capture_id = drafts.save_capture(job_id, capture)
    if (capture.get('stop_reason') or {p['page_key'] for p in capture['pages']} != required_pages(capture)
            or media_backup is None or portals is None):
        raise CollectionStopped(capture.get('stop_reason') or 'Some Hub pages are missing. Saved information has been kept.')
    originals_id, media_id, verified, attributed = media_backup
    photo_count = len(verified['media_manifest'])
    result = {'schema_version': 'hub-collected-listing/v1', 'complete': False,
              'rxcu': reference, 'listing_id': binding['listing_id'],
              'source_capture_id': capture_id, 'originals_capture_id': originals_id,
              'media_capture_id': media_id, 'media_count': photo_count,
              'source': deepcopy(capture), 'media': verified,
              'portal_observation': portals, 'collection_seconds': round(time.monotonic() - started, 2),
              'status': 'ready_for_address_review', 'live_ready': False,
              'remaining': ['Verify the copied fields in an unpublished listing before enabling live relisting.']}
    if not attributed:
        result['download_needs_review'] = True
    result_id = drafts.save_capture(job_id, result)
    progress(f'{reference}: {photo_count} original images saved and matched to the photo pack')
    return {'id': result_id, 'source_capture_id': capture_id, 'capture': result}


def _backup_media(hub, drafts, job_id, page, media_root, *, guard, cancelled, progress):
    from .capture import read_gallery_media
    reference = page['matched_source']['rxcu']
    listing_id = page['matched_source']['listing_id']
    inventory = observed_originals(page['media'], listing_id)
    captions = page.get('photo_captions', [])
    if [c['id'] for c in captions] != [i['id'] for i in inventory] or not all(c.get('close_verified') is True for c in captions):
        raise CollectionStopped('Some photo captions could not be verified. Saved information has been kept.')
    progress(f'{reference}: downloading {len(inventory)} original images')
    originals = download_originals(page['media'], listing_id, Path(media_root), cancelled=cancelled)
    originals_id = drafts.save_capture(job_id, originals)
    guard()
    progress(f'{reference}: checking the photo pack against the originals')
    root = Path(media_root).parent / 'downloads'
    downloaded = hub.download_photo_pack(reference, listing_id, root)
    imported = import_media_zip(downloaded['candidate_path'], media_root)
    drafts.save_capture(job_id, imported)
    verified = verify_pack_bytes(originals, imported)
    for item, caption in zip(verified['media_manifest'], captions, strict=True):
        item['caption'] = caption['caption']
    verified['captions_observed'] = True
    media_id = drafts.save_capture(job_id, verified)
    attributed = downloaded.get('attribution_verified') is True
    if attributed:
        from .downloads import finish_verified_download
        finish_verified_download(root, downloaded, verified)
    current = read_gallery_media(hub.driver, listing_id, guard=guard)
    if observed_originals(current, listing_id) != inventory:
        raise CollectionStopped('The photos changed during collection. Review Hub before trying again.')
    return originals_id, media_id, verified, attributed


def collect_batch(hub, drafts, jobs, media_root, *, cancelled=lambda: False, progress=lambda message: None,
                  after_collection=None):
    # Development builds refresh handlers between idle UI operations so fixes
    # do not discard the operator's signed-in browser. This performs no Hub
    # action; ordinary installed builds never reload modules.
    import os
    if os.environ.get('QUICK_RELIST_DEVELOPMENT') == '1':
        import importlib, sys
        for name in ('hub_readonly', 'relist_plan', 'live_relist_ui', 'relist_runner'):
            module = sys.modules.get('quick_relist.' + name)
            if module is not None:
                importlib.reload(module)
                if name == 'hub_readonly' and getattr(hub, '_pending_editor', None) is None:
                    from types import MethodType
                    hub.search_reference = MethodType(module.ReadOnlyHub.search_reference, hub)
                    hub._select_all_listing_types = MethodType(module.ReadOnlyHub._select_all_listing_types, hub)
                    hub.close = MethodType(module.ReadOnlyHub.close, hub)
    collected = []
    for job in jobs:
        if cancelled():
            break
        if job.get('state') != 'queued':
            continue
        saved = latest_collection(drafts, job['id'])
        if saved:
            if saved['capture'].get('download_needs_review'):
                from .downloads import reviewed_archive_receipt
                media = saved['capture'].get('media', {})
                receipt = (reviewed_archive_receipt(Path(media_root).parent / 'downloads',
                           media.get('pack_archive_sha256')) if media_root is not None
                           and media.get('source_bytes_verified') is True
                           and media.get('listing_id') == saved['capture'].get('listing_id') else None)
                if receipt is None:
                    raise CollectionStopped('Review the previous photo download before continuing this batch.')
                reviewed = deepcopy(saved['capture'])
                reviewed['download_needs_review'] = False
                reviewed['download_review_receipt'] = receipt
                reviewed['download_review_method'] = 'operator_review_and_original_byte_match'
                # Preserve the original observation and all source/address IDs.
                saved = drafts.get_capture(drafts.save_capture(job['id'], reviewed))
            collected.append(saved)
            if after_collection is not None:
                after_collection(job, saved['capture'], freshly_collected=False)
            continue
        result = collect_listing(hub, drafts, job, media_root, cancelled=cancelled, progress=progress)
        collected.append(result)
        if result['capture'].get('download_needs_review'):
            raise CollectionStopped('The listing and its original photos were saved. Review the completed download before continuing the batch.')
        if after_collection is not None:
            after_collection(job, result['capture'], freshly_collected=True)
    return collected
