"""The prefilled Verify address form, backed by immutable source observations."""
from copy import deepcopy
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                              QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget)
from .address import propose_address_fields

LABELS = {'country': 'Country', 'suburb': 'Suburb', 'street_number': 'Street number',
          'street_name': 'Street name', 'street_type': 'Street type', 'estate_name': 'Estate name',
          'erf_number': 'Erf number', 'ss_unit_number': 'Sectional scheme unit number',
          'ss_number': 'Scheme number', 'ss_name': 'Scheme name',
          'id_registration_number': 'ID / registration number', 'door_number': 'Door number',
          'latitude': 'Latitude', 'longitude': 'Longitude', 'show_address_on_web': 'Show address on web'}


class VerifyAddressDialog(QDialog):
    def __init__(self, reference, capture, revision=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Verify address — {reference}')
        self.resize(660, 740)
        self.proposal = propose_address_fields(capture)
        self.widgets = {}
        self.original = {}
        layout = QVBoxLayout(self)
        title = QLabel(f'Verify address · {reference}')
        title.setStyleSheet('font-size:22px;font-weight:600;')
        layout.addWidget(title)
        message = QLabel('These values came from the original listing. Keep them or make your changes below. Saving this review does not change Hub.')
        message.setWordWrap(True)
        layout.addWidget(message)
        if self.proposal['missing'] or self.proposal['ambiguous']:
            warning = QLabel('Some address fields could not be read. They must be checked before creating the new listing.')
            warning.setWordWrap(True)
            layout.addWidget(warning)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        form.setSpacing(13)
        for key, label in LABELS.items():
            item = self.proposal['fields'].get(key)
            if not item:
                continue
            path, original = item['path'], item['field']
            self.original[path] = deepcopy(original)
            previous = (revision or {}).get('edited', {}).get(path, original)
            if (revision or {}).get('original', {}).get(path, original) != original:
                previous = original
            if original.get('type') == 'checkbox':
                widget = QCheckBox()
                widget.setChecked(previous['checked'])
            else:
                widget = QLineEdit(str(previous.get('value') or ''))
                if key == 'country':
                    # Keep the observed country identity. Country changes need
                    # Hub's full country/suburb selection flow, not a typed ID.
                    widget.setReadOnly(True)
                if key == 'id_registration_number':
                    widget.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
            widget.setAccessibleName(label)
            self.widgets[path] = widget
            form.addRow(label, widget)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save = buttons.addButton('Save address review', QDialogButtonBox.ButtonRole.AcceptRole)
        save.setEnabled(bool(self.original))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def edited_address(self):
        result = deepcopy(self.original)
        for path, widget in self.widgets.items():
            if isinstance(widget, QCheckBox):
                result[path]['checked'] = widget.isChecked()
            else:
                result[path]['value'] = widget.text()
        return result
