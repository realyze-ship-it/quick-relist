"""Explicit operator receipts for work finished in Hub after a stopped run.

These receipts do not turn a failed action into an automated success.
"""
import re

from .core import normalize_rxcu
from .capture import parse_editor_url


CONFIRMATIONS = ('source_removed_and_archived', 'reviewed_content_and_photos', 'portal_status_checked')
PORTAL_NAMES = {'website': 'RE/MAX website', 'p24': 'Property24', 'pp': 'Private Property',
                'global': 'RE/MAX Global', 'iol': 'IOL Property', 'immoafrica': 'ImmoAfrica',
                'flow': 'Flow Living'}


def recovery_receipt(binding, reference, editor_url, destinations, portals, confirmations, notes=''):
    reference = normalize_rxcu(reference)
    target_id = parse_editor_url(editor_url.strip())['listing_id']
    if reference == normalize_rxcu(binding['reference']) or target_id == binding['listing_id']:
        raise ValueError('The replacement must be different from the original.')
    if (not destinations or len(set(destinations)) != len(destinations)
            or set(destinations) - PORTAL_NAMES.keys() or set(portals) != set(destinations)):
        raise ValueError('Record every original portal destination.')
    if set(confirmations) != set(CONFIRMATIONS) or any(v is not True for v in confirmations.values()):
        raise ValueError('Check the original, replacement content and portal statuses in Hub first.')
    for portal in portals.values():
        if portal.get('state') == 'published':
            if not isinstance(portal.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9-]{3,80}', portal['id']):
                raise ValueError('Enter the confirmed portal reference, or choose Waiting for portal.')
        elif portal.get('state') != 'pending' or portal.get('id') is not None:
            raise ValueError('Choose a confirmed or waiting status for every original portal.')
    if not isinstance(notes, str) or len(notes) > 3000:
        raise ValueError('Keep the recovery note under 3,000 characters.')
    complete = all(p['state'] == 'published' for p in portals.values())
    return {'state': 'manual_completed' if complete else 'manual_publication_pending',
            'mode': 'manual_recovery', 'target_reference': reference, 'target_listing_id': target_id,
            'original_archived': True, 'content_checked_by': 'operator_in_hub',
            'confirmations': dict(confirmations), 'notes': notes,
            'publication': {'complete': complete, 'portals': portals}}


def recovery_dialog(reference, destinations, parent=None):
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                                  QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout)
    dialog = QDialog(parent)
    dialog.setWindowTitle('Record Hub recovery · ' + reference)
    dialog.resize(660, 630)
    layout = QVBoxLayout(dialog)
    explanation = QLabel('Use this only after completing the replacement directly in Hub. '
        'It records your checks locally and sends no Hub changes. The earlier failed attempt stays in the activity record.')
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    form = QFormLayout()
    target = QLineEdit()
    target.setAccessibleName('Replacement RXCU')
    link = QLineEdit()
    link.setAccessibleName('Replacement Hub editor link')
    form.addRow('Replacement RXCU', target)
    form.addRow('Replacement Hub editor link', link)
    portal_widgets = {}
    for key in destinations:
        status, ident = QComboBox(), QLineEdit()
        status.addItems(['Waiting for portal', 'Confirmed in Hub'])
        status.setAccessibleName(PORTAL_NAMES[key] + ' status')
        ident.setAccessibleName(PORTAL_NAMES[key] + ' reference')
        ident.setPlaceholderText('Portal reference, when confirmed')
        form.addRow(PORTAL_NAMES[key], status)
        form.addRow('Portal reference', ident)
        portal_widgets[key] = status, ident
    layout.addLayout(form)
    checks = {}
    labels = ['The original is removed from its portals and archived.',
              'The replacement matches the reviewed address, dates, details, descriptions and every photo.',
              'I checked the listed portal statuses in Hub.']
    for key, label in zip(CONFIRMATIONS, labels):
        check = QCheckBox(label)
        checks[key] = check
        layout.addWidget(check)
    notes = QPlainTextEdit()
    notes.setAccessibleName('Recovery notes')
    notes.setPlaceholderText('Optional notes, including any separately reviewed corrections')
    notes.setMaximumHeight(70)
    layout.addWidget(notes)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    save = buttons.addButton('Record Hub recovery', QDialogButtonBox.ButtonRole.AcceptRole)
    save.setEnabled(False)
    for check in checks.values():
        check.toggled.connect(lambda _checked: save.setEnabled(all(c.isChecked() for c in checks.values())))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    def values():
        return {'reference': target.text(), 'editor_url': link.text(), 'destinations': destinations,
                'portals': {k: {'state': 'published' if s.currentIndex() else 'pending',
                                'id': i.text().strip() if s.currentIndex() else None}
                            for k, (s, i) in portal_widgets.items()},
                'confirmations': {k: c.isChecked() for k, c in checks.items()}, 'notes': notes.toPlainText()}
    return dialog, values
