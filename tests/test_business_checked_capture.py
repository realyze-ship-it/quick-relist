"""Actual read-only extractor against synthetic MUI checked-control proxies.

These fixtures exercise the existing observed names/label relationships. They
are not evidence that a new collector has run against the current live Hub DOM.
"""
import pytest

from test_dom_runtime import extract


LABELS = {'poa': 'Price on application', 'featured': 'Featured',
          'address_hidden': 'Show address on web', 'transfer_cost': 'No Transfer Duty Applicable'}


def proxy(name='poa', caption=None, *, typ='checkbox', value='on', checked=False,
          input_extra='', label_extra='', parent_class=None):
    caption = LABELS.get(name, name) if caption is None else caption
    cls = parent_class or ('MuiCheckbox-root' if typ == 'checkbox' else 'MuiRadio-root')
    return f'''<label class="MuiFormControlLabel-root" {label_extra}>
      <span class="MuiButtonBase-root {cls}"><input type="{typ}" name="{name}"
      class="PrivateSwitchBase-input" value="{value}" {'checked' if checked else ''}
      style="opacity:0;position:absolute;display:block" {input_extra}>
      <svg aria-hidden="true"></svg></span><span>{caption}</span></label>'''


@pytest.mark.parametrize('name', LABELS)
@pytest.mark.parametrize('checked', [False, True])
@pytest.mark.parametrize('suffix', ['', '/particulars'])
def test_named_visible_proxies_retain_raw_checked_state(extract, name, checked, suffix):
    result = extract('<main>' + proxy(name, checked=checked) + '</main>',
                     url='https://hub.remax.co.za/app/listings/edit/synthetic-123' + suffix)
    fields = result['fields']
    assert len(fields) == 1
    assert (fields[0]['name'], fields[0]['label'], fields[0]['checked']) == (name, LABELS[name], checked)
    assert fields[0]['value'] == 'on'
    assert fields[0]['observed_business_checked_proxy'] is True
    # In particular, Show address on web must not invert address_hidden.
    assert type(fields[0]['checked']) is bool


def test_sale_state_members_retain_codes_labels_and_false_states(extract):
    captions = ['New', 'For Sale', 'Price Reduced', 'Offer Made', 'Sold']
    html = ''.join(proxy('sale_state_id', label, typ='radio', value=str(i + 1), checked=i == 1)
                   for i, label in enumerate(captions))
    result = extract('<main><div role="radiogroup" aria-label="sale_state_id">' + html + '</div></main>')
    assert [(f['value'], f['checked'], f['label']) for f in result['fields']] == [
        (str(i + 1), i == 1, label) for i, label in enumerate(captions)]
    assert all(f['observed_business_checked_proxy'] for f in result['fields'])


@pytest.mark.parametrize('overrides', [
    {'name': 'unrelated', 'caption': 'Price on application'},
    {'caption': 'Unexpected label'}, {'typ': 'hidden'},
    {'input_extra': 'hidden'}, {'input_extra': 'aria-hidden="true"'},
    {'input_extra': 'aria-checked="true"'}, {'input_extra': 'data-indeterminate="true"'},
    {'input_extra': 'aria-label="access token"'},
    {'label_extra': 'for="another-control"'}, {'label_extra': 'hidden'},
    {'label_extra': 'style="opacity:0"'}, {'parent_class': 'UnrelatedProxy'},
])
def test_hidden_unrelated_or_ambiguous_controls_are_not_promoted(extract, overrides):
    result = extract('<main>' + proxy(**overrides) + '</main>')
    assert result['fields'] == []


@pytest.mark.parametrize('wrapper', ['<div id="access_token">{}</div>', '<div role="dialog">{}</div>'])
def test_sensitive_ancestors_and_room_dialogs_do_not_use_business_exception(extract, wrapper):
    result = extract('<main>' + wrapper.format(proxy()) + '</main>')
    assert result['fields'] == []
    assert not any(f.get('observed_business_checked_proxy') for d in result['dialogs'] for f in d['fields'])


def test_radio_requires_its_own_named_group(extract):
    result = extract('<main>' + proxy('sale_state_id', 'New', typ='radio', value='1') + '</main>')
    assert result['fields'] == []


def test_ordinary_visible_checkbox_still_uses_generic_capture(extract):
    result = extract('<main><label>Ordinary<input type="checkbox" name="other" checked></label></main>')
    assert result['fields'][0]['checked'] is True
    assert 'observed_business_checked_proxy' not in result['fields'][0]


@pytest.mark.parametrize('path', ['/app/listings/add/sale',
    '/app/listings/edit/123/media', '/app/listings/edit/123/deal_particulars'])
def test_business_exception_is_limited_to_existing_details_route(extract, path):
    result = extract('<main>' + proxy() + '</main>', url='https://hub.remax.co.za' + path)
    assert result['fields'] == []
