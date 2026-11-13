"""Preflight actual collected data before any original is taken off market."""
from copy import deepcopy
from pathlib import Path

from .collection import required_pages
from .form_copy import CopyStopped, particulars_plan, _text
from .portal_ui import active_destinations
from .upload_review import compare_uploaded_images

RELATED = {
    ('Name','Value','Action'): 'No custom features added',
    ('Name','Role','Type','Residency','ID/Passport/Registration number','Email','Mobile','Action'): 'No listing parties have been saved yet',
    ('Document type','Description','File name','Date added','Uploaded by','Action'): 'No documents saved.',
    ('Note','User','Type','Date','Action'): 'No listing notes saved',
}


def commission_plan_check(page, agents):
    if page is None:
        return
    fields={f.get('name'):f.get('value') for f in page['fields']}
    if set(fields)!={'total_commission_value','total_commission_percentage'} or any(not isinstance(v,str) or not v for v in fields.values()):
        raise CopyStopped('The original commission values are incomplete. No figures will be invented.')
    tables=[t for t in page['tables'] if t.get('rows') and t['rows'][0][0]=='Agent']
    if len(tables)!=1:
        raise CopyStopped('The sale commission split table is missing.')
    rows=[r for r in tables[0]['rows'][1:] if r and r[0]!='Totals:']
    if (any(len(r)!=9 or r[1]!='RE/MAX' or r[2]!='listing agent' for r in rows)
            or sorted(r[0] for r in rows)!=sorted(a['name'] for a in agents)):
        raise CopyStopped('This sale has additional commission participants that need a copy handler. The original will stay active.')


def require_empty_related(details):
    tables = details.get('tables', [])
    for header, empty in RELATED.items():
        matches = [t for t in tables if t.get('rows') and tuple(t['rows'][0]) == header]
        if len(matches) != 1:
            raise CopyStopped('Refresh the listing to check custom features, parties, documents and notes.')
        body = matches[0]['rows'][1:]
        if len(body) != 1 or [v.strip() for v in body[0] if v.strip()] != [empty]:
            raise CopyStopped('This listing has related records that need a copy handler. The original will stay active.')


def media_plan(page):
    result = {}
    names = {'virtualtour_url','videotour_url','p24_youtube_video_id','matterport_video_id','eyespy360_url','marketing_header'}
    for field in page['fields']:
        key = field.get('name')
        if key not in names:
            if field.get('label','').strip() == 'Brochure Description':
                key = 'brochure_description'
            elif key == 'word_count_inline':
                continue  # Generator setting, not saved listing description.
            else:
                raise CopyStopped('An unrecognized media field needs review before copying.')
        if key in result:
            raise CopyStopped('A media field is ambiguous.')
        result[key] = _text(field['value'])
    if set(result) != names | {'brochure_description'}:
        raise CopyStopped('Some descriptions or media links were not collected.')
    rich = [r for r in page['rich_text'] if r.get('observed_description_label') is True]
    if len(rich) != 1:
        raise CopyStopped('The formatted description could not be matched.')
    # Hub removes attribute-free inline wrappers on paste. They carry no
    # formatting in the captured HTML subset. Preserve every text character,
    # paragraph and formatting tag while comparing the same representation.
    result['description_html'] = _text(rich[0]['sanitized_html']).replace('<span>', '').replace('</span>', '')
    result['description_text'] = _text(rich[0]['text'])
    return result


def build_relist_plan(collection, review):
    source = collection['source']
    if collection.get('download_needs_review') or source.get('stop_reason'):
        raise CopyStopped('Finish checking the collected listing before relisting.')
    pages = {p['page_key']:p for p in source['pages']}
    if len(pages) != len(source['pages']) or set(pages) != required_pages(source):
        raise CopyStopped('The sale or rental source pages are incomplete.')
    for page in pages.values():
        lossy = ('truncated','omitted','unresolved','unsupported')
        if any(any(word in issue for word in lossy) for issue in page.get('limitations', [])
               if issue != 'date_semantics_unresolved'):
            raise CopyStopped('Some copied data was incomplete or changed during capture. Review it before relisting.')
    particulars = particulars_plan(source, review)
    require_empty_related(pages['details'])
    commission_plan_check(pages.get('deal_particulars'),particulars['agents'])
    # Nonzero rooms must have a full dialog backup, not just a room count.
    from .room_capture import ROOM_OPTIONS
    mapped = {r.count_id:r.opener for r in ROOM_OPTIONS}
    captured = {r['opener'] for r in particulars['rooms']}
    for c in particulars['controls']:
        if c['kind'] == 'count':
            try:
                number=float(c['value'])
                if not 0<=number<=100:
                    raise ValueError
            except (TypeError,ValueError):
                raise CopyStopped('A room count is not supported. Review it before relisting.') from None
            if number>0 and mapped.get(c['key']) not in captured:
                raise CopyStopped('A room has options that were not collected. The original will stay active.')
    if particulars['mode'] == 'sale' and not any(c['key']=='bank_mandate' for c in particulars['controls']):
        raise CopyStopped('Refresh the sale listing to include its Bank mandate selection.')
    media = collection.get('media', {})
    if not all(media.get(k) is True for k in ('source_bytes_verified','display_order_observed','captions_observed')):
        raise CopyStopped('The original photo bytes, order and captions must all be checked first.')
    photos = media.get('media_manifest', [])
    if len(photos) < 3 or any(p.get('kind') != 'photo' or not isinstance(p.get('caption'),str) for p in photos):
        raise CopyStopped('At least three verified photos are required. Floor plans need their separate upload handler.')
    compare_uploaded_images(photos, photos)  # Validates files and recorded hashes now.
    destinations = active_destinations(collection['portal_observation'])
    return {'particulars':particulars, 'media':media_plan(pages['media']),
            'photos':deepcopy(photos), 'destinations':destinations,
            'source_reference':collection['rxcu'], 'source_listing_id':collection['listing_id'],
            'deal':deepcopy(pages.get('deal_particulars'))}
