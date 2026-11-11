"""Independent composition invariants. Synthetic data; no Hub access."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.request import urlopen

from quick_relist.draft_pipeline import DraftPipelineRequest, DraftPipeline, validate_pipeline_state
from quick_relist.hub_draft_pipeline import HubDraftPipeline
from quick_relist.hub_form_writer import TransferRejected, _json, _digest
from quick_relist.media_metadata import MetadataStore
from draft_pipeline_fixture import fixture_request, Service, OfflineProvider, serve


def complete_history(request):
    """Independent authoritative-history model, not browser qualification."""
    state = deepcopy(Service(request).state())
    metadata = None
    for ordinal in range(request.count):
        previous = state['revision']
        command = request.command(ordinal, previous)
        revision = 'review-stage-' + str(ordinal + 1)
        if ordinal < len(request.forms):
            page = request.forms[ordinal].context.page
            state['fields'][page] = request.desired_fields[page]
        elif ordinal == len(request.forms):
            upload = request.upload_at(previous)
            state['upload_receipts'] = request.expected_upload_receipts(upload)
            files = list(upload.files)
            state['cards'] = request.initial_cards((files[2], files[0], files[1]))
            metadata = request.metadata(revision, state['upload_receipts'], state['cards'], upload)
        else:
            index = ordinal - len(request.forms) - 1
            inner = metadata.command(index, previous, state['cards'])
            state['cards'] = metadata.after(state['cards'], index)
            state['metadata_receipts'].append({'operation_id': inner['operation_id'],
                'command_hash': _digest(inner), 'request_hash': metadata.fingerprint,
                'ordinal': index, 'status': 'applied', 'terminal': True,
                'previous_revision': previous, 'revision': revision,
                'before_hash': inner['before_hash'], 'after_hash': inner['after_hash']})
        snapshot = deepcopy({k: state[k] for k in ('fields', 'cards', 'upload_receipts')})
        state['snapshots'].append(snapshot)
        state['receipts'].append({'command': command, 'command_hash': _digest(command),
                                  'revision': revision, 'state_hash': _digest(snapshot)})
        state['revision'] = revision
    return state


class DraftPipelineReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.request = fixture_request(self.temp.name)

    def test_same_service_chain_derives_metadata_from_actual_upload_snapshot(self):
        state = complete_history(self.request)
        receipts, metadata = validate_pipeline_state(self.request, state)
        self.assertEqual(len(receipts), self.request.count)
        self.assertEqual(metadata.baseline, state['snapshots'][len(self.request.forms)]['cards'])
        self.assertEqual(metadata.context.expected_draft_revision,
                         receipts[len(self.request.forms)]['revision'])
        self.assertEqual(_json(state['cards']), _json(metadata.desired))
        self.assertEqual([c['kind'] for c in state['cards']], ['photo', 'floorplan', 'photo'])

    def test_source_account_capture_and_initial_revision_mismatch_rejected(self):
        for key, value in (('account_id', 'other-account'), ('agency_id', 'other-agency'),
                           ('source_capture_sha256', 'd' * 64), ('draft_id', '333333'),
                           ('expected_source_revision', 'other-source-revision'),
                           ('expected_draft_revision', 'other-draft-revision')):
            with self.subTest(key=key):
                form = self.request.forms[0]
                changed = replace(form, context=replace(form.context, **{key: value}))
                with self.assertRaises(TransferRejected):
                    replace(self.request, forms=(changed, *self.request.forms[1:]))

    def test_global_text_address_approval_has_exact_page_projections(self):
        approval = {'street_name': '  Approved replacement street  '}
        form = self.request.forms[0]
        details = replace(form, context=replace(form.context, approved_address_sha256=_digest(approval)),
                          approved_address_json=_json(approval))
        upload = replace(self.request.upload,
                         context=replace(self.request.context, approved_address_sha256=_digest(approval)))
        request = DraftPipelineRequest((details, *self.request.forms[1:]), upload, _json(approval))
        self.assertEqual(request.desired_fields['details']['street_name'], approval['street_name'])
        self.assertEqual(request.forms[0].fields[0].source, form.fields[0].source)
        self.assertEqual([f.approved_address_json for f in request.forms[1:]], ['{}', '{}'])
        validate_pipeline_state(request, complete_history(request))
        with self.assertRaises(TransferRejected):
            replace(request, forms=self.request.forms)
        with self.assertRaises(TransferRejected):
            replace(request, approved_address_json=_json({'suburb': 'Unqualified selection'}))
        with self.assertRaises(TransferRejected):
            replace(request, approved_address_json=_json(approval | {'street_number': '99'}))

    def test_saved_field_or_media_changes_fail_final_comparison(self):
        original = complete_history(self.request)
        mutations = (
            lambda s: s.update(draft_state='published'),
            lambda s: s.update(draft_state=None),
            lambda s: s['fields']['details'].update(street_name='Changed after copy'),
            lambda s: s['fields']['details'].update(unrequested='Unexpected field'),
            lambda s: s['cards'][0].update(caption='Changed caption'),
            lambda s: s['cards'][0].update(cover=1),
            lambda s: s['cards'][0].update(position=False),
            lambda s: s['cards'].reverse(),
            lambda s: s['upload_receipts'][0].update(size=True),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                state = deepcopy(original); mutate(state)
                with self.assertRaises(TransferRejected):
                    validate_pipeline_state(self.request, state)

    def test_receipt_and_earlier_snapshot_changes_fail_replay(self):
        original = complete_history(self.request)
        mutations = (
            lambda s: s['receipts'][0]['command'].update(ordinal=False),
            lambda s: s['receipts'][1].update(revision=self.request.context.expected_draft_revision),
            lambda s: s['snapshots'][0]['fields']['details'].update(street_name='Unrelated saved value'),
            lambda s: s['metadata_receipts'][0].update(terminal=1),
            lambda s: s['metadata_receipts'][0].update(request_hash='0' * 64),
            lambda s: s['context'].update(source_id='444444'),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                state = deepcopy(original); mutate(state)
                with self.assertRaises(TransferRejected):
                    validate_pipeline_state(self.request, state)

    def test_unknown_source_media_metadata_is_not_silently_dropped(self):
        first = self.request.upload.files[0]
        bad = replace(first, metadata_json=_json(json.loads(first.metadata_json) | {'rotation': 0}))
        upload = replace(self.request.upload, files=(bad, *self.request.upload.files[1:]))
        with self.assertRaises(TransferRejected):
            replace(self.request, upload=upload)

    def test_public_live_facade_never_accesses_browser(self):
        class Trap:
            def __getattribute__(self, name):
                raise AssertionError('Browser must not be accessed')
        report = HubDraftPipeline(Trap()).transfer(object())
        self.assertEqual(report.status, 'blocked')
        self.assertFalse(report.live_supported)
        self.assertFalse(report.full_fidelity)
        self.assertFalse(report.allocation_verified)
        self.assertFalse(report.hub_persistence_verified)
        self.assertIsNone(report.saved)

    def test_missing_or_swapped_enrolled_inner_journal_never_recreated(self):
        for fault in ('missing', 'swapped'):
            with self.subTest(fault=fault):
                service = Service(self.request)
                provider = OfflineProvider(service)
                store = MetadataStore(Path(self.temp.name) / fault / 'pipeline.sqlite')
                try:
                    flow = DraftPipeline(store, self.request, provider)
                    for index in range(len(self.request.forms) + 1):
                        self.assertEqual(flow.advance_for_testing().completed_stages, index + 1)
                    provider.fail_before = len(self.request.forms) + 1
                    self.assertEqual(flow.advance_for_testing().status, 'uncertain')
                    self.assertEqual(store.conn.execute('SELECT count(*) FROM metadata_bindings').fetchone()[0], 1)
                    # Deliberately corrupt only this synthetic private test DB.
                    if fault == 'missing':
                        store.conn.execute('DROP TRIGGER immutable_metadata_binding_delete')
                        store.conn.execute('DELETE FROM metadata_bindings')
                    else:
                        store.conn.execute('DROP TRIGGER immutable_metadata_binding_update')
                        store.conn.execute("UPDATE metadata_bindings SET request_json='{}'")
                    store.conn.commit()
                    self.assertEqual(flow.reconcile_for_testing().status, 'blocked')
                    self.assertEqual(flow.advance_for_testing().status, 'blocked')
                    self.assertEqual(service.metadata_posts, 0)
                    if fault == 'missing':
                        self.assertEqual(store.conn.execute('SELECT count(*) FROM metadata_bindings').fetchone()[0], 0)
                finally:
                    store.close()

    def test_every_handler_http_route_is_read_only_and_reachable_in_order(self):
        with serve(self.request) as (origin, service):
            store = MetadataStore(Path(self.temp.name) / 'http-smoke' / 'pipeline.sqlite')
            try:
                flow = DraftPipeline(store, self.request, OfflineProvider(service))
                for ordinal in range(self.request.count):
                    index = (ordinal if ordinal < len(self.request.forms)
                             else len(self.request.forms) if ordinal == len(self.request.forms)
                             else len(self.request.forms) + 1)
                    before = _json(service.state())
                    with urlopen(origin + service.path(index), timeout=3) as response:
                        self.assertEqual(response.status, 200)
                        html = response.read().decode('utf-8')
                    expected = ('quick-relist-synthetic-form' if index < len(self.request.forms)
                                else 'synthetic-media' if index == len(self.request.forms)
                                else 'synthetic-metadata')
                    self.assertIn(expected, html)
                    self.assertEqual(_json(service.state()), before)
                    self.assertEqual(flow.advance_for_testing().completed_stages, ordinal + 1)
                before = _json(service.state())
                with urlopen(origin + service.state_path, timeout=3) as response:
                    self.assertIn('pipeline-state', response.read().decode('utf-8'))
                self.assertEqual(_json(service.state()), before)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
