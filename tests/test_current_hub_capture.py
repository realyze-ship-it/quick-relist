"""Current Hub control shapes with generated, non-private values."""
import pytest

from quick_relist.business_fields import LIFESTYLE_FIELDS, GENERAL_FIELDS
from quick_relist.source_fields import extract_source_fields
from test_business_checked_capture import proxy
from test_dom_runtime import extract
from test_source_fields import capture, page, entries


def test_lifestyle_checked_and_unchecked_values_reach_copy_inventory(extract):
    html = '<main>' + ''.join(proxy(key, label, checked=i % 2 == 0)
                             for i, (key, label) in enumerate(LIFESTYLE_FIELDS.items())) + '</main>'
    observed = extract(html, url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    mapped = entries(extract_source_fields(capture(page(fields=observed['fields'],
                     page_path='/app/listings/edit/123456/particulars'))))
    for i, key in enumerate(LIFESTYLE_FIELDS):
        assert mapped[key]['value'] is (i % 2 == 0)
        assert mapped[key]['status'] == 'observed'


def test_expanded_general_features_preserve_all_checked_and_unchecked_states(extract):
    html = '<main>' + ''.join(proxy(key, label, checked=i % 3 == 0)
                             for i, (key, label) in enumerate(GENERAL_FIELDS.items())) + '</main>'
    observed = extract(html, url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    mapped = entries(extract_source_fields(capture(page(fields=observed['fields'],
                     page_path='/app/listings/edit/123456/particulars'))))
    assert len(GENERAL_FIELDS) == 94
    for i, key in enumerate(GENERAL_FIELDS):
        assert mapped[key]['value'] is (i % 3 == 0)


def tiptap(label='Listing description', copies=1):
    return '''<main><div class="MuiGrid-root">
      <div class="MuiFormControl-root">''' + label + '''</div>
      <div class="MuiFormControl-root"><div><div><div class="html-editor">
      <div class="html-editor__toolbar">Normal Size Font</div><div><div>''' + ''.join(
        '<div class="tiptap ProseMirror" role="textbox" contenteditable="true">'
        '<p>Synthetic description</p><p><strong>Second paragraph</strong></p></div>'
        for _ in range(copies)) + '</div></div></div></div></div></div></div></main>'


def test_current_tiptap_description_is_copied_without_toolbar_text(extract):
    observed = extract(tiptap(), url='https://hub.remax.co.za/app/listings/edit/123456/media')
    mapped = entries(extract_source_fields(capture(page('media', [], rich_text=observed['rich_text']))))
    value = mapped['rich_text_description']
    assert value['value'] == 'Synthetic description Second paragraph'
    assert value['sanitized_html'] == '<p>Synthetic description</p><p><strong>Second paragraph</strong></p>'
    assert value['observations'][0]['field']['observed_description_label'] is True


@pytest.mark.parametrize('html', [tiptap('Other description'), tiptap(copies=2)])
def test_ambiguous_or_unrelated_tiptap_label_is_not_assumed(extract, html):
    observed = extract(html, url='https://hub.remax.co.za/app/listings/edit/123456/media')
    assert all(not item.get('observed_description_label') for item in observed['rich_text'])


def test_primary_agent_state_is_preserved_with_its_own_row_name(extract):
    html = '''<main><table><thead><tr><th>Is primary agent?</th><th>Name</th><th>Action</th></tr></thead>
      <tbody><tr><td><span class="MuiCheckbox-root"><input type="checkbox" class="PrivateSwitchBase-input"
      style="opacity:0;position:absolute" checked></span></td><td>Synthetic Agent One</td><td><button>Remove</button></td></tr>
      <tr><td><span class="MuiCheckbox-root"><input type="checkbox" class="PrivateSwitchBase-input"
      style="opacity:0;position:absolute"></span></td><td>Synthetic Agent Two</td><td><button>Remove</button></td></tr></tbody></table></main>'''
    observed = extract(html, url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    assert observed['tables'][0]['observed_agents'] == [
        {'name': 'Synthetic Agent One', 'primary': True}, {'name': 'Synthetic Agent Two', 'primary': False}]
    unrelated = extract(html.replace('Is primary agent?', 'Unrelated permission'),
                        url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    assert 'observed_agents' not in unrelated['tables'][0]


def test_rental_occupation_date_is_read_without_inventing_a_value(extract):
    from segmented_date_fixtures import picker_html
    observed = extract('<main>' + picker_html(5, raw='2040-12-03') + '</main>',
                       url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    dates = [field for field in observed['fields'] if field.get('name') == 'occupation_date']
    assert len(dates) == 1
    assert dates[0]['value'] == '2040-12-03'


def test_rental_status_uses_its_own_radio_group(extract):
    html = '''<main><div role="radiogroup" aria-label="rental_state_id">
    <label class="MuiFormControlLabel-root"><span class="MuiRadio-root">
    <input name="rental_state_id" type="radio" value="2" checked class="PrivateSwitchBase-input"
    style="opacity:0;position:absolute"></span><span>Available Immediately</span></label></div></main>'''
    observed = extract(html, url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    assert observed['fields'][0]['checked'] is True
    assert observed['fields'][0]['label'] == 'Available Immediately'


def test_rental_period_with_space_is_not_lost(extract):
    html = '''<main><div><div role="combobox" id="mui-component-select-rental_price_period">Per month</div><input id="rental_price_period" name="rental_price_period" aria-hidden="true" value="per month"></div></main>'''
    observed = extract(html, url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    assert observed['fields'][0]['selected_options'] == [{'value':'per month','text':'Per month'}]


def test_townhouse_subtype_reaches_the_copy_inventory(extract):
    html = '''<main><div><div role="combobox" id="mui-component-select-townhouse_descriptive_property_type">New Development</div><input id="townhouse_descriptive_property_type" name="townhouse_descriptive_property_type" aria-hidden="true" value="NewDevelopment"></div></main>'''
    observed = extract(html, url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    field = observed['fields'][0]
    assert field['selected_options'] == [{'value':'NewDevelopment','text':'New Development'}]
    mapped = entries(extract_source_fields(capture(page(fields=observed['fields']))))
    assert mapped['townhouse_descriptive_property_type']['status'] == 'observed'


@pytest.mark.parametrize('name,hidden,blocked', [('synthetic_new_type',False,True),
                                              ('synthetic_new_type',True,False), ('listing',False,False)])
def test_unknown_visible_business_selector_blocks_instead_of_disappearing(extract,name,hidden,blocked):
    html = f'<main><div {"hidden" if hidden else ""}><div role="combobox" id="mui-component-select-{name}">Synthetic choice</div><input name="{name}" aria-hidden="true" value="DO_NOT_CAPTURE"></div></main>'
    observed = extract(html, url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    assert ('unsupported_business_selector' in observed['limitations']) is blocked
    assert all(f.get('value') != 'DO_NOT_CAPTURE' for f in observed['fields'])


def test_nameless_bank_mandate_is_bound_to_its_visible_label(extract):
    html = '''<main><div class="MuiFormControl-root"><label>Bank mandate</label><div><div role="combobox">None</div><input class="MuiSelect-nativeInput" aria-hidden="true" value="0"></div></div></main>'''
    observed = extract(html, url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    bank = observed['fields'][0]
    assert bank['name'] == '' and bank['id'] == ''
    assert bank['observed_label_bound_selector'] == 'Bank mandate'
    assert bank['selected_options'] == [{'value':'0','text':'None'}]
    unrelated = extract(html.replace('Bank mandate','Other selector'), url='https://hub.remax.co.za/app/listings/edit/123456/particulars')
    assert unrelated['fields'] == []
