from copy import deepcopy
import pytest
from quick_relist.address import _ADDRESS_NAMES, propose_address_fields
from quick_relist.form_copy import particulars_plan, CopyStopped, ParticularsFiller, COUNTS
from quick_relist.business_fields import GENERAL_FIELDS, LIFESTYLE_FIELDS
from segmented_date_fixtures import date_record


def source_and_review():
    def combo(name, value, label):
        return {'name': name, 'id': name, 'type': 'custom-combobox', 'value': label,
                'label': 'Country' if name == 'country_id' else label,
                'observed_business_selection_code': True,
                'selected_options': [{'value': value, 'text': label}]}
    fields = [{'name': n, 'id': n, 'type': 'text', 'value': 'Synthetic' if n == 'street_name' else ''}
              for n in _ADDRESS_NAMES.values() if n != 'country_id']
    fields += [combo('country_id', 'za', 'South Africa'), combo('listing_type_id', '1', 'Sale'),
               combo('property_type_id', '2', 'Apartment / Flat'), combo('office_id', '123', 'Synthetic Office'),
               combo('mandate_type_id', '2', 'Open'),
               {'label': 'Suburb', 'type': 'text', 'value': 'Synthetic, City, Province'},
               {'name': 'address_hidden', 'type': 'checkbox', 'label': 'Show address on web', 'checked': True, 'value': 'on'},
               {'name': 'selling_price', 'type': 'text', 'value': '123456'}]
    fields += [{'name':n,'type':'text','value':'0'} for n in ('floor_area','land_area','rates','levy','special_levies','ensuites')]
    fields += [combo(n,'sqm','m2') for n in ('floor_area_units','land_area_units')]
    fields += [{'id':n,'type':'text','value':'0'} for n in COUNTS]
    fields += [{'name':n,'type':'checkbox','checked':False,'value':''} for n in (*LIFESTYLE_FIELDS,'poa','featured')]
    fields += [{'name':'sale_state_id','type':'radio','value':'1','checked':True}]
    fields += [date_record(i, raw='2099-11-19') for i in range(5)]
    fields += [{'name': name, 'id': name, 'type': 'checkbox', 'label': label, 'value': 'on', 'checked': False}
               for name, label in GENERAL_FIELDS.items()]
    source = {'complete': False, 'pages': [{'page_key': 'details', 'fields': fields,
              'general_features_expanded': True,
              'tables': [{'observed_agents': [{'name': 'Synthetic Agent', 'primary': True}]}]}]}
    proposed = propose_address_fields(source)
    original = {v['path']: v['field'] for v in proposed['fields'].values()}
    return source, {'original': deepcopy(original), 'edited': deepcopy(original)}


def test_only_reviewed_address_changes_and_original_dates_enter_plan():
    source, review = source_and_review()
    before = deepcopy(source)
    path = next(p for p, f in review['edited'].items() if f.get('name') == 'street_number')
    review['edited'][path]['value'] = '24A'
    plan = particulars_plan(source, review)
    values = {c['key']: c['value'] for c in plan['controls']}
    assert values['street_number'] == '24A'
    assert values['mandate_signed_date'] == '2099-11-19'
    assert values['mandate_expiry'] == '2099-11-19'
    assert values['address_hidden'] is True
    assert source == before


def test_stale_address_review_is_rejected_before_any_form_input():
    source, review = source_and_review()
    source['pages'][0]['fields'][0]['value'] = 'changed externally'
    with pytest.raises(CopyStopped, match='does not match'):
        particulars_plan(source, review)


def test_disagreement_between_date_input_and_display_stops_copy():
    source, review = source_and_review()
    next(f for f in source['pages'][0]['fields'] if f.get('name') == 'mandate_expiry')['value'] = '2098-11-19'
    with pytest.raises(CopyStopped, match='date differs'):
        particulars_plan(source, review)


def test_missing_mandate_date_is_not_replaced_with_new_form_default():
    source, review = source_and_review()
    source['pages'][0]['fields'] = [f for f in source['pages'][0]['fields'] if f.get('name') != 'on_market_since']
    with pytest.raises(CopyStopped, match='Required source fields'):
        particulars_plan(source, review)


def test_observed_blank_mandates_are_retained_until_the_explicit_relist_policy_applies():
    from segmented_date_fixtures import empty_date_record
    from quick_relist.relist_dates import apply_relist_dates
    from datetime import date
    source, review = source_and_review()
    fields=source['pages'][0]['fields']
    for index, name in enumerate(('mandate_signed_date','mandate_expiry')):
        position=next(i for i,f in enumerate(fields) if f.get('name')==name)
        fields[position]=empty_date_record(index,false_container=True)
    before=deepcopy(source)
    plan={'particulars':particulars_plan(source,review)}
    assert all(c['value']=='' for c in plan['particulars']['controls'] if c['key'].startswith('mandate_') and c['kind']=='date')
    apply_relist_dates(plan,None,date(2099,11,13))
    values={c['key']:c['value'] for c in plan['particulars']['controls']}
    assert values['mandate_signed_date']=='2099-11-13'
    assert values['mandate_expiry']=='2100-05-13'
    assert source==before


def test_blank_mandate_marker_alone_cannot_hide_contradictory_date_parts():
    source, review = source_and_review()
    field=next(f for f in source['pages'][0]['fields'] if f.get('name')=='mandate_signed_date')
    field['value']=''
    field['date_picker']['empty_observed']=True
    with pytest.raises(CopyStopped,match='date differs'):
        particulars_plan(source,review)


def test_unknown_source_control_stops_before_silent_data_loss():
    source, review = source_and_review()
    source['pages'][0]['fields'].append({'name': 'unmapped_new_feature', 'type': 'text', 'value': 'keep me'})
    with pytest.raises(CopyStopped, match='no copy mapping'):
        particulars_plan(source, review)


def test_existing_listing_is_never_accepted_as_a_new_unsaved_form():
    class Driver:
        current_window_handle = 'synthetic'
        current_url = 'https://hub.remax.co.za/app/listings/edit/123456/particulars'
    with pytest.raises(CopyStopped, match='unsaved'):
        ParticularsFiller(Driver())


def test_opening_menu_can_replace_nodes_without_replaying_input(monkeypatch):
    from selenium.common.exceptions import StaleElementReferenceException
    from types import SimpleNamespace
    from quick_relist import form_copy
    driver = SimpleNamespace(current_window_handle='unsaved',
        current_url='https://hub.remax.co.za/app/listings/add/rent')
    filler = ParticularsFiller(driver)
    observations = iter([StaleElementReferenceException(), ['exact option']])
    def read():
        result = next(observations)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(form_copy.time, 'sleep', lambda seconds: None)
    assert filler.wait(read, 'Missing option') == ['exact option']
    driver.current_url = 'https://hub.remax.co.za/app/listings'
    with pytest.raises(CopyStopped, match='tab changed'):
        filler.wait(read, 'Missing option')


def test_collapsed_features_cannot_be_interpreted_as_all_unchecked():
    source, review = source_and_review()
    source['pages'][0].pop('general_features_expanded')
    with pytest.raises(CopyStopped, match='General Property Features expanded'):
        particulars_plan(source, review)


def test_suburb_autocomplete_keeps_reviewed_city_and_rejects_near_names():
    from quick_relist.form_copy import suburb_matches
    assert suburb_matches('Synthetic Park', 'Synthetic Park, Example City, Province')
    assert not suburb_matches('Synthetic Park, Other City', 'Synthetic Park, Example City, Province')
    assert not suburb_matches('Synthetic Park', 'Synthetic Park Extension, Example City')


def test_rental_plan_preserves_occupation_deposit_and_period_without_sale_dates():
    source, review = source_and_review()
    fields = source['pages'][0]['fields']
    fields[:] = [f for f in fields if f.get('name') not in ('selling_price', 'mandate_type_id', 'mandate_signed_date', 'mandate_expiry')]
    next(f for f in fields if f.get('name')=='sale_state_id')['name']='rental_state_id'
    listing_type = next(f for f in fields if f.get('name') == 'listing_type_id')
    listing_type.update(value='Rent', selected_options=[{'value':'2', 'text':'Rent'}])
    fields.extend([date_record(5, raw='2099-11-19'),
        {'name':'rental_price','type':'text','value':'7,500'},
        {'name':'deposit','type':'text','value':'15,000'},
        {'name':'rental_price_period','type':'custom-combobox','observed_business_selection_code':True,
         'selected_options':[{'value':'per month','text':'Per month'}]}])
    # Removing earlier source controls changes address capture paths.
    proposed = propose_address_fields(source)
    original = {v['path']:v['field'] for v in proposed['fields'].values()}
    plan = particulars_plan(source, {'original':deepcopy(original),'edited':deepcopy(original)})
    values = {c['key']:c['value'] for c in plan['controls']}
    assert plan['mode'] == 'rent'
    assert values['occupation_date'] == '2099-11-19'
    assert values['deposit'] == '15,000' and values['rental_price_period'] == 'per month'
    assert 'mandate_expiry' not in values and 'selling_price' not in values
