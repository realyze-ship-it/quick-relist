"""Refresh a saved house whose older collector missed the descriptive type."""
from copy import deepcopy

from .address import propose_address_fields
from .form_copy import CopyStopped
from .live_relist_ui import content_signature
from .relist_plan import build_relist_plan


def needs_house_refresh(collection):
    details = next((p for p in collection['source']['pages'] if p['page_key'] == 'details'), {})
    fields = details.get('fields', [])
    return ('unsupported_business_selector' in details.get('limitations', [])
            and any(f.get('name') == 'property_type_id' and f.get('value') == 'House' for f in fields)
            and not any(f.get('name') == 'house_descriptive_property_type' for f in fields))


def refresh_house_collection(drafts, job_id, collection, review, fresh):
    old = collection['source']
    binding = fresh.get('matched_source', {})
    if (binding.get('rxcu') != collection['rxcu'] or binding.get('listing_id') != collection['listing_id']
            or binding.get('all_accepted_observations_guarded') is not True or fresh.get('stop_reason')):
        raise CopyStopped('The refreshed house could not be matched to the saved listing.')
    current = content_signature(fresh)
    fields = current['details']['fields']
    filtered = [f for f in fields if f[0] != 'house_descriptive_property_type']
    if len(filtered) + 1 != len(fields):
        raise CopyStopped('Hub did not identify one house descriptive property type.')
    current['details']['fields'] = filtered
    if current != content_signature(old):
        raise CopyStopped('Other listing details changed since the address review. Collect the listing again before relisting.')

    # React can regenerate control IDs and the new field can shift indexes.
    # Carry reviewed values by their unique address meanings onto fresh paths.
    prior = propose_address_fields(old)['fields']
    now = propose_address_fields(fresh)['fields']
    if (set(prior) != set(now)
            or {v['path']:v['field'] for v in prior.values()} != review['original']
            or set(review['original']) != set(review['edited'])):
        raise CopyStopped('The saved address review could not be matched to the refreshed house.')
    original = {v['path']:v['field'] for v in now.values()}
    edited = deepcopy(original)
    for key, item in now.items():
        old_item = prior[key]
        for attr in ('value', 'checked', 'selected_options', 'selected_suburb_label'):
            if old_item['field'].get(attr) != item['field'].get(attr):
                raise CopyStopped('The original address changed. Review the refreshed address before relisting.')
        for attr in ('value', 'checked'):
            if attr in item['field']:
                edited[item['path']][attr] = review['edited'][old_item['path']][attr]

    updated = dict(collection, source=fresh)
    build_relist_plan(updated, {'original':original, 'edited':edited})
    # Publish the collection pointer last so a partial save leaves the old
    # source, review and verified photo pack usable together.
    updated['source_capture_id'] = drafts.save_capture(job_id, fresh)
    drafts.save_address(updated['source_capture_id'], original, edited)
    drafts.save_capture(job_id, updated)
