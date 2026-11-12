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


def latest_collection(drafts, job_id):
    for row in drafts.list_captures(job_id):
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
    raise CollectionStopped('The reference did not appear in Hub. Check the listing filters and your sign-in.')


def collect_listing(hub, drafts, job, media_root, *, cancelled=lambda: False, progress=lambda message: None):
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
                               include_general_features=True)
    capture = reader.capture()
    capture_id = drafts.save_capture(job_id, capture)
    if capture.get('stop_reason') or {p['page_key'] for p in capture['pages']} != required_pages(capture):
        raise CollectionStopped(capture.get('stop_reason') or 'Some Hub pages are missing. Saved information has been kept.')
    media_page = next(p for p in capture['pages'] if p['page_key'] == 'media')
    inventory = observed_originals(media_page['media'], binding['listing_id'])
    captions = media_page.get('photo_captions', [])
    if [c['id'] for c in captions] != [i['id'] for i in inventory] or not all(c.get('close_verified') is True for c in captions):
        raise CollectionStopped('Some photo captions could not be verified. Saved information has been kept.')
    url = hub.driver.current_url
    from .capture import parse_editor_url
    from .portal_ui import read_portals
    if parse_editor_url(url)['page_key'] != 'portals':
        url = reader._navigate_tab(url, 'portals')
    portals = read_portals(hub.driver, guard=lambda: reader._identity(url), timeout=45)
    if parse_editor_url(url)['page_key'] != 'media':
        url = reader._navigate_tab(url, 'media')
    current = reader._settled_page(url)
    if observed_originals(current['media'], binding['listing_id']) != inventory:
        raise CollectionStopped('The photos changed during collection. Review Hub before trying again.')
    progress(f'{reference}: downloading {len(inventory)} original images')
    originals = download_originals(media_page['media'], binding['listing_id'], Path(media_root), cancelled=cancelled)
    originals_id = drafts.save_capture(job_id, originals)
    reader._identity(url)
    progress(f'{reference}: checking the photo pack against the originals')
    downloaded = hub.download_photo_pack(reference, binding['listing_id'], Path(media_root).parent / 'downloads')
    imported = import_media_zip(downloaded['candidate_path'], media_root)
    drafts.save_capture(job_id, imported)
    verified = verify_pack_bytes(originals, imported)
    for item, caption in zip(verified['media_manifest'], captions):
        item['caption'] = caption['caption']
    verified['captions_observed'] = True
    media_id = drafts.save_capture(job_id, verified)
    result = {'schema_version': 'hub-collected-listing/v1', 'complete': False,
              'rxcu': reference, 'listing_id': binding['listing_id'],
              'source_capture_id': capture_id, 'originals_capture_id': originals_id,
              'media_capture_id': media_id, 'media_count': len(inventory),
              'source': deepcopy(capture), 'media': verified,
              'portal_observation': portals,
              'status': 'ready_for_address_review', 'live_ready': False,
              'remaining': ['Verify the copied fields in an unpublished listing before enabling live relisting.']}
    if downloaded.get('attribution_verified') is True:
        from .downloads import finish_verified_download
        finish_verified_download(Path(media_root).parent / 'downloads', downloaded, verified)
    else:
        result['download_needs_review'] = True
    result_id = drafts.save_capture(job_id, result)
    progress(f'{reference}: {len(inventory)} original images saved; ready for address review')
    return {'id': result_id, 'source_capture_id': capture_id, 'capture': result}


def collect_batch(hub, drafts, jobs, media_root, *, cancelled=lambda: False, progress=lambda message: None):
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
                raise CollectionStopped('Review the previous photo download before continuing this batch.')
            collected.append(saved)
            continue
        result = collect_listing(hub, drafts, job, media_root, cancelled=cancelled, progress=progress)
        collected.append(result)
        if result['capture'].get('download_needs_review'):
            raise CollectionStopped('The listing and its original photos were saved. Review the completed download before continuing the batch.')
    return collected
