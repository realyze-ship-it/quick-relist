from copy import deepcopy

import pytest

from quick_relist import collection_refresh as refresh
from quick_relist.collection import latest_collection
from quick_relist.drafts import DraftStore
from quick_relist.form_copy import CopyStopped


@pytest.mark.parametrize('change', ['subtype_only', 'other_listing', 'old_address_changed', 'media_changed', 'unknown_field'])
def test_refresh_keeps_the_existing_review_and_media_only_for_the_same_house(monkeypatch, change):
    drafts = DraftStore(':memory:')
    job = 'test-job'
    street = {'name':'street_number', 'type':'text', 'value':'10'}
    fields = [street, {'name':'property_type_id','value':'House'}]
    page = {'page_key':'details','limitations':['unsupported_business_selector'],
            'fields':fields, 'tables':[], 'dialogs':[], 'rich_text':[], 'media':[]}
    source = {'complete':False,'pages':[page],
              'matched_source':{'rxcu':'RXCU1000','listing_id':'1000','all_accepted_observations_guarded':True}}
    source_id = drafts.save_capture(job, source)
    path = 'pages/0/fields/0'
    original = {path: street}
    review_id = drafts.save_address(source_id, original, {path:dict(street,value='12A')})
    data = {'schema_version':'hub-collected-listing/v1','complete':False,'source':source,
            'source_capture_id':source_id,'rxcu':'RXCU1000','listing_id':'1000','media_capture_id':'same-photos'}
    drafts.save_capture(job, data)
    assert refresh.needs_house_refresh(data)
    current = deepcopy(source)
    current['pages'][0]['limitations'] = []
    current['pages'][0]['fields'].insert(0, {'name':'house_descriptive_property_type','value':'Double Storey'})
    if change == 'other_listing':
        current['matched_source']['listing_id'] = '999'
    elif change == 'old_address_changed':
        current['pages'][0]['fields'][1]['value'] = '11'
    elif change == 'media_changed':
        current['pages'][0]['media'] = [{'observed_asset_id':'new'}]
    elif change == 'unknown_field':
        current['pages'][0]['limitations'] = ['unsupported_business_selector']
    def preflight(data, review):
        if data['source']['pages'][0]['limitations']:
            raise CopyStopped('Incomplete capture')
        assert review['edited']['pages/0/fields/1']['value'] == '12A'
    monkeypatch.setattr(refresh, 'build_relist_plan', preflight)
    if change == 'subtype_only':
        refresh.refresh_house_collection(drafts, job, data, drafts.get_address(review_id), current)
        saved = latest_collection(drafts, job)['capture']
        assert saved['media_capture_id'] == 'same-photos'
        assert not refresh.needs_house_refresh(saved)
        assert drafts.list_addresses(saved['source_capture_id'])[0]['edited']['pages/0/fields/1']['value'] == '12A'
    else:
        with pytest.raises(CopyStopped):
            refresh.refresh_house_collection(drafts, job, data, drafts.get_address(review_id), current)
        assert latest_collection(drafts, job)['capture'] == data
