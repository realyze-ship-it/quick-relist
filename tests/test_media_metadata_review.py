"""Independent synthetic request/metadata fidelity regressions. No Hub access."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from quick_relist.hub_form_writer import TransferContext, TransferRejected, _json, _digest
from quick_relist.hub_media_writer import MediaFile, MediaTransferRequest, empty_baseline
from quick_relist.media_metadata import MetadataRequest, MetadataStore, MetadataWorkflow


def inputs():
    context = TransferContext('review-op', 'review-account', 'review-agency',
                              '111111', 'RXCU111111', 'source-r1', 'a' * 64,
                              'b' * 64, '222222', 'before-upload', 'media')
    files = []
    captions = ('  Exact leading and trailing spaces  ', '\nTwo lines\n\n', '')
    for i, kind in enumerate(('photo', 'floorplan', 'photo')):
        asset_id = f'review-asset-{i}'
        sha = hashlib.sha256(asset_id.encode()).hexdigest()
        evidence = {'asset_id': asset_id, 'source_id': context.source_id,
                    'source_revision': context.expected_source_revision,
                    'source_capture_sha256': context.source_capture_sha256,
                    'kind': kind, 'position': i, 'sha256': sha}
        files.append(MediaFile(asset_id, kind, i, 20 + i, sha,
                               str(Path(tempfile.gettempdir()).resolve() / f'{asset_id}.png'),
                               _json(evidence), _json({'caption': captions[i], 'cover': i == 1})))
    transfer = MediaTransferRequest(context, 'review-parent', 'c' * 64,
                                    tuple(files), _json(empty_baseline()))
    receipts = [{'media_id': f.media_id, 'upload_id': 'synthetic-upload-' + f.media_id,
                 'sha256': f.sha256, 'size': f.size, 'kind': f.kind,
                 'source_position': f.position, 'request': transfer.fingerprint} for f in files]
    baseline = [{k: receipt[k] for k in ('media_id', 'upload_id', 'sha256', 'size', 'kind')}
                | {'position': index, 'caption': 'Old caption', 'cover': False}
                for index, receipt in enumerate((receipts[0], receipts[2], receipts[1]))]
    return transfer, replace(context, expected_draft_revision='after-upload-r1'), receipts, baseline


class ReviewProvider:
    """Small independent model; no browser or real persistence claim."""
    def __init__(self, request):
        self.request, self.cards = request, request.baseline
        self.revision = request.context.expected_draft_revision
        self.receipts, self.calls = [], []
        self.epoch = 'e' * 64
        self.crash_after = False
        self.reuse_initial_revision = False

    def binding(self):
        return {'epoch': self.epoch, 'origin': 'http://127.0.0.1:23456/synthetic-only',
                'request_hash': self.request.fingerprint}

    def inspect(self):
        return deepcopy({'request_hash': self.request.fingerprint,
                         'context': asdict(self.request.context), 'revision': self.revision,
                         'cards': self.cards, 'receipts': self.receipts})

    def preflight(self, command, cards):
        assert _json(cards) == _json(self.cards)
        assert command['expected_revision'] == self.revision

    def perform(self, command, cards):
        self.preflight(command, cards)
        self.calls.append(deepcopy(command))
        self.cards = self.request.after(self.cards, command['ordinal'])
        self.revision = (self.request.context.expected_draft_revision if self.reuse_initial_revision
                         else f'review-revision-{len(self.calls)}')
        self.receipts.append({'operation_id': command['operation_id'],
            'command_hash': _digest(command), 'request_hash': self.request.fingerprint,
            'ordinal': command['ordinal'], 'status': 'applied', 'terminal': True,
            'previous_revision': command['expected_revision'], 'revision': self.revision,
            'before_hash': command['before_hash'], 'after_hash': command['after_hash']})
        if self.crash_after:
            raise KeyboardInterrupt('Synthetic interruption, never log raw provider errors')


class MediaMetadataReviewTests(unittest.TestCase):
    def test_exact_source_captions_cover_and_cross_kind_order(self):
        request = MetadataRequest.from_transfer(*inputs())
        current = request.baseline
        for ordinal, _ in enumerate(request.stages()):
            current = request.after(current, ordinal)
        self.assertEqual(_json(current), _json(request.desired))
        self.assertEqual([c['kind'] for c in current], ['photo', 'floorplan', 'photo'])
        self.assertEqual(current[0]['caption'], '  Exact leading and trailing spaces  ')
        self.assertEqual(current[1]['caption'], '\nTwo lines\n\n')
        self.assertEqual(current[2]['caption'], '')
        self.assertEqual([c['cover'] for c in current], [False, True, False])
        self.assertEqual(request.baseline[1]['kind'], 'photo')

    def test_unknown_metadata_never_discarded_even_when_neutral(self):
        transfer, context, receipts, baseline = inputs()
        for extra in ({'rotation': 0}, {'tags': []}, {'future_field': None}):
            with self.subTest(extra=extra):
                changed = list(transfer.files)
                changed[0] = replace(changed[0], metadata_json=_json(json.loads(changed[0].metadata_json) | extra))
                candidate = replace(transfer, files=tuple(changed))
                bound_receipts = [r | {'request': candidate.fingerprint} for r in receipts]
                with self.assertRaises(TransferRejected):
                    MetadataRequest.from_transfer(candidate, context, bound_receipts, baseline)

    def test_receipt_types_and_bytes_must_match(self):
        original = inputs()
        for key, value in (('source_position', False), ('size', True), ('sha256', '0' * 64),
                           ('kind', 'floorplan'), ('request', '0' * 64)):
            with self.subTest(key=key):
                args = list(deepcopy(original))
                args[2][0][key] = value
                with self.assertRaises(TransferRejected):
                    MetadataRequest.from_transfer(*args)

    def test_metadata_context_cannot_change_source_or_draft(self):
        transfer, context, receipts, baseline = inputs()
        for field, value in (('source_id', '333333'), ('draft_id', '444444'),
                             ('account_id', 'other-account'), ('source_capture_sha256', 'd' * 64)):
            with self.subTest(field=field):
                with self.assertRaises(TransferRejected):
                    MetadataRequest.from_transfer(transfer, replace(context, **{field: value}), receipts, baseline)

    def test_no_cover_is_explicit_and_missing_cover_is_not_false(self):
        transfer, context, receipts, baseline = inputs()
        files = tuple(replace(f, metadata_json=_json(json.loads(f.metadata_json) | {'cover': False})) for f in transfer.files)
        candidate = replace(transfer, files=files)
        bound = [r | {'request': candidate.fingerprint} for r in receipts]
        request = MetadataRequest.from_transfer(candidate, context, bound, baseline)
        self.assertIsNone(request.stages()[-2]['media_id'])
        for value in ({'caption': ''}, {'caption': '', 'cover': 0}, {'caption': '', 'cover': None}):
            with self.subTest(value=value):
                broken = replace(candidate, files=(replace(files[0], metadata_json=_json(value)), *files[1:]))
                with self.assertRaises(TransferRejected):
                    MetadataRequest.from_transfer(broken, context,
                        [r | {'request': broken.fingerprint} for r in receipts], baseline)

    def test_unroundtrippable_caption_is_rejected_not_normalized(self):
        transfer, context, receipts, baseline = inputs()
        for caption in ('a\r\nb', 'a\rb', 'a\x00b', 'a\x7fb', 'a\x85b', 'a\ue007b'):
            with self.subTest(caption=repr(caption)):
                first = replace(transfer.files[0], metadata_json=_json({'caption': caption, 'cover': False}))
                candidate = replace(transfer, files=(first, *transfer.files[1:]))
                with self.assertRaises(TransferRejected):
                    MetadataRequest.from_transfer(candidate, context,
                        [r | {'request': candidate.fingerprint} for r in receipts], baseline)

    def test_baseline_identity_position_and_bool_types_fail_closed(self):
        original = inputs()
        for key, value in (('position', False), ('cover', 0), ('upload_id', 'unrelated'),
                           ('sha256', '0' * 64), ('kind', 'floorplan')):
            with self.subTest(key=key):
                args = list(deepcopy(original)); args[3][0][key] = value
                with self.assertRaises(TransferRejected):
                    MetadataRequest.from_transfer(*args)

    def test_default_execution_never_reads_provider_or_writes_intent(self):
        class Trap:
            def __getattribute__(self, name):
                raise AssertionError('Provider must not be touched')
        store = MetadataStore(':memory:')
        try:
            workflow = MetadataWorkflow(store, MetadataRequest.from_transfer(*inputs()), Trap())
            report = workflow.execute()
            self.assertEqual(report.status, 'blocked')
            self.assertFalse(report.live_supported)
            self.assertFalse(report.hub_persistence_verified)
            self.assertFalse(report.full_fidelity)
            self.assertIsNone(report.saved)
            self.assertEqual(store.conn.execute('SELECT count(*) FROM metadata_events').fetchone()[0], 0)
        finally:
            store.close()

    def test_pending_recovery_never_advances_next_stage_in_same_call(self):
        request = MetadataRequest.from_transfer(*inputs())
        provider, store = ReviewProvider(request), MetadataStore(':memory:')
        try:
            workflow = MetadataWorkflow(store, request, provider)
            provider.crash_after = True
            self.assertEqual(workflow.advance_for_testing().status, 'uncertain')
            provider.crash_after = False
            self.assertIn(workflow.advance_for_testing().status, ('uncertain', 'blocked'))
            self.assertEqual(len(provider.calls), 1)
            recovered = workflow.reconcile_for_testing()
            self.assertEqual(recovered.completed_stages, 1)
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(workflow.advance_for_testing().completed_stages, 2)
            self.assertEqual(len(provider.calls), 2)
        finally:
            store.close()

    def test_lost_local_journal_does_not_restart_progressed_server(self):
        request = MetadataRequest.from_transfer(*inputs())
        provider = ReviewProvider(request)
        first, missing = MetadataStore(':memory:'), MetadataStore(':memory:')
        try:
            self.assertEqual(MetadataWorkflow(first, request, provider).advance_for_testing().completed_stages, 1)
            self.assertEqual(MetadataWorkflow(missing, request, provider).advance_for_testing().status, 'blocked')
            self.assertEqual(len(provider.calls), 1)
        finally:
            first.close(); missing.close()

    def test_changed_server_epoch_cannot_reconcile_old_intent(self):
        request = MetadataRequest.from_transfer(*inputs())
        provider, store = ReviewProvider(request), MetadataStore(':memory:')
        try:
            workflow = MetadataWorkflow(store, request, provider)
            provider.crash_after = True
            self.assertEqual(workflow.advance_for_testing().status, 'uncertain')
            provider.epoch = 'f' * 64
            self.assertEqual(workflow.reconcile_for_testing().status, 'blocked')
            self.assertEqual(workflow.advance_for_testing().status, 'blocked')
            self.assertEqual(len(provider.calls), 1)
        finally:
            store.close()

    def test_repeated_revision_token_is_not_an_accepted_chain(self):
        request = MetadataRequest.from_transfer(*inputs())
        provider, store = ReviewProvider(request), MetadataStore(':memory:')
        try:
            workflow = MetadataWorkflow(store, request, provider)
            self.assertEqual(workflow.advance_for_testing().completed_stages, 1)
            provider.reuse_initial_revision = True
            self.assertEqual(workflow.advance_for_testing().status, 'uncertain')
            self.assertEqual(workflow.reconcile_for_testing().status, 'blocked')
            self.assertEqual(len(provider.calls), 2)
        finally:
            store.close()

    def test_modeled_existing_key_rejects_altered_submitted_payload(self):
        from media_metadata_fixture import Model
        request = MetadataRequest.from_transfer(*inputs())
        model = Model(request)
        command = model.command()
        first = deepcopy(model.apply(command, command['payload']))
        saved = _json(model.cards)
        with self.assertRaises(TransferRejected):
            model.apply(command, command['payload'] | {'caption': 'Different submitted caption'})
        self.assertEqual(_json(model.cards), saved)
        self.assertEqual(model.receipts, [first])


if __name__ == '__main__':
    unittest.main()
