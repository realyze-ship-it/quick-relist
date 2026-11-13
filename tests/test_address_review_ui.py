from copy import deepcopy
import os
import pytest
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from quick_relist.address_review_ui import VerifyAddressDialog
from quick_relist.drafts import DraftStore


def test_prefilled_address_changes_are_saved_separately_from_original():
    app = QApplication.instance() or QApplication([])
    capture = {'complete': False, 'pages': [{'page_key': 'details', 'fields': [
        {'id': 'street_number', 'name': 'street_number', 'type': 'text', 'value': '24'},
        {'id': 'scheme_name', 'name': 'scheme_name', 'type': 'text', 'value': 'Synthetic Court'},
        {'type': 'text', 'label': 'Suburb', 'value': 'Synthetic Suburb'}]}]}
    before = deepcopy(capture)
    drafts = DraftStore(':memory:')
    capture_id = drafts.save_capture('synthetic-job', capture)
    dialog = VerifyAddressDialog('RXCU1000', capture)
    field = dialog.widgets['pages/0/fields/0']
    assert field.text() == '24'
    field.setText('24A')
    revision_id = drafts.save_address(capture_id, dialog.original, dialog.edited_address())
    assert drafts.get_address(revision_id)['edited']['pages/0/fields/0']['value'] == '24A'
    assert drafts.get_capture(capture_id)['capture'] == before
    assert capture == before
    reopened = VerifyAddressDialog('RXCU1000', capture, drafts.get_address(revision_id))
    assert reopened.widgets['pages/0/fields/0'].text() == '24A'
    reopened.close()
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize('erf,scheme', [('58', '712/2021'), ('', ''), ('RE/58', 'SS712')])
def test_registered_numbers_survive_old_reviews_and_remain_read_only(erf, scheme):
    app = QApplication.instance() or QApplication([])
    names = ['lightstone_erf_number', 'scheme_number', 'street_number',
             'lightstone_sectional_scheme_unit_number', 'door_number']
    values = [erf, scheme, '24', '7', '7']
    capture = {'complete': False, 'pages': [{'page_key': 'details', 'fields': [
        {'id': name, 'name': name, 'type': 'text', 'value': value}
        for name, value in zip(names, values)]}]}
    before = deepcopy(capture)
    initial = VerifyAddressDialog('RXCU1000', capture)
    revision = {'original': deepcopy(initial.original), 'edited': deepcopy(initial.original)}
    revision['edited']['pages/0/fields/0']['value'] = '5X'
    revision['edited']['pages/0/fields/1']['value'] = '7X2'
    revision['edited']['pages/0/fields/2']['value'] = '24A'
    dialog = VerifyAddressDialog('RXCU1000', capture, revision)
    for index, value in enumerate((erf, scheme)):
        widget = dialog.widgets[f'pages/0/fields/{index}']
        assert widget.isReadOnly()
        assert widget.text() == value
        # Serialization also preserves the source if code changes a widget.
        widget.setText('unexpected override')
    assert dialog.widgets['pages/0/fields/2'].text() == '24A'
    for index, value in ((3, '7X'), (4, '9')):
        widget = dialog.widgets[f'pages/0/fields/{index}']
        assert not widget.isReadOnly()
        widget.setText(value)
    edited = dialog.edited_address()
    assert [edited[f'pages/0/fields/{i}']['value'] for i in range(5)] == [erf, scheme, '24A', '7X', '9']
    assert capture == before
    assert revision['edited']['pages/0/fields/0']['value'] == '5X'
    initial.close()
    dialog.close()
    app.processEvents()
