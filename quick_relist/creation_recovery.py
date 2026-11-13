"""Read-only reconciliation of a Save whose result needs verification."""
from .core import ValidationError, normalize_rxcu
from .capture import parse_editor_url
from .run_store import RunNeedsReview


def can_check_creation(run):
    return bool(run and not any(e['kind'] == 'manual_recovery' for e in run.get('events', []))
                and {a['action']: a['state'] for a in run.get('actions', [])} ==
                {'remove_portals': 'done', 'archive': 'done', 'create': 'uncertain'})


def existing_identity(reference, editor_url, binding):
    try:
        reference = normalize_rxcu(reference)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    ident = parse_editor_url(editor_url.strip())['listing_id']
    if reference == normalize_rxcu(binding['reference']) or ident == binding['listing_id']:
        raise ValueError('Enter the replacement, not the original listing.')
    return reference, ident


def check_existing_replacement(drafts, runs, provider, job, reference, listing_id, *, cancelled=lambda: False):
    from .collection import latest_collection
    from .relist_plan import build_relist_plan
    from .brochure_review import apply_review
    from .relist_dates import apply_relist_dates, relist_today
    run = runs.get(job['id'])
    if not can_check_creation(run):
        raise RunNeedsReview('Only an uncertain Save with completed removal and archiving can be checked here.')
    saved = latest_collection(drafts, job['id'])
    if not saved:
        raise RunNeedsReview('The original capture is unavailable.')
    data = saved['capture']
    reviews = drafts.list_addresses(data['source_capture_id'])
    binding = {'source_capture_id': data['source_capture_id'],
               'address_revision_id': reviews[0]['id'] if reviews else None,
               'reference': data['rxcu'], 'listing_id': data['listing_id']}
    if binding != run['binding']:
        raise RunNeedsReview('The source or address review changed after this run started.')
    runs.assert_source_available(job['id'], data['rxcu'], data['listing_id'])
    plan = build_relist_plan(data, reviews[0])
    apply_review(plan, drafts, job['id'], data)
    apply_relist_dates(plan, run, relist_today())
    if cancelled():
        raise RunNeedsReview('The existing-copy check was stopped.')
    receipt = provider.check_created_copy(data, plan, reference, listing_id)
    if cancelled():
        raise RunNeedsReview('The existing-copy check was stopped before recording its result.')
    runs.reconcile_created_copy(run['id'], receipt)
    return receipt


def recovery_dialog(binding, parent=None):
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout
    dialog = QDialog(parent)
    dialog.setWindowTitle('Check existing replacement')
    dialog.resize(600, 260)
    layout = QVBoxLayout(dialog)
    message = QLabel('Enter the saved replacement found in Hub. The app will check that the original is archived and removed, and that this unpublished copy matches the reviewed details. This check does not save or publish a listing.')
    message.setWordWrap(True)
    layout.addWidget(message)
    form = QFormLayout()
    reference, url = QLineEdit(), QLineEdit()
    reference.setAccessibleName('Replacement RXCU')
    url.setAccessibleName('Replacement Hub edit URL')
    form.addRow('Replacement RXCU', reference)
    form.addRow('Replacement Hub edit URL', url)
    layout.addLayout(form)
    error = QLabel()
    error.setWordWrap(True)
    layout.addWidget(error)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    check = buttons.addButton('Check existing copy', QDialogButtonBox.ButtonRole.AcceptRole)
    layout.addWidget(buttons)
    def values():
        return existing_identity(reference.text(), url.text(), binding)
    def validate():
        try:
            values()
            error.setText('')
            check.setEnabled(True)
        except ValueError:
            error.setText('Enter a different RXCU and its official Hub edit URL.')
            check.setEnabled(False)
    reference.textChanged.connect(validate)
    url.textChanged.connect(validate)
    validate()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return dialog, values
