from copy import deepcopy
import os
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
