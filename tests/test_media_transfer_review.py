"""Independent offline adversarial review; no browser, server, or user media."""
from dataclasses import replace
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from quick_relist.hub_form_writer import TransferRejected
from quick_relist.hub_media_writer import bind_synthetic_media_for_tests
from media_transfer_fixture import fixture_request
from test_hub_media_writer import Driver


class MediaTransferReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.request = fixture_request(self.tmp.name)
        self.nonce = secrets.token_hex(32)
        self.driver = Driver(self.request, self.nonce)
        self.writer = bind_synthetic_media_for_tests(self.driver, self.request, nonce=self.nonce)

    def test_duplicate_valid_receipt_is_rejected(self):
        self.writer.transfer()
        self.driver.state['receipts'].append(dict(self.driver.state['receipts'][0]))
        self.assertEqual(self.writer.observe().code, 'modeled_receipt_mismatch')
        self.assertEqual(self.driver.sends, 2)

    def test_timeout_after_send_never_retries_or_leaks_error(self):
        with patch.object(self.driver, 'execute_async_script', side_effect=TimeoutError('private filename.png')):
            report = self.writer.transfer()
        self.assertEqual(report.status, 'partial_or_uncertain')
        self.assertTrue(report.upload_state_uncertain)
        self.assertNotIn('private', str(report))
        self.assertEqual(self.driver.sends, 1)
        self.assertEqual(self.writer.transfer().code, 'fixture_transfer_already_attempted')
        self.writer.observe()
        self.assertEqual(self.driver.sends, 1)

    def test_wrong_request_marker_blocks_before_local_reads(self):
        self.driver.state['request'] = '0' * 64
        with patch('quick_relist.hub_media_writer._local_file', side_effect=AssertionError('must not read')):
            self.assertEqual(self.writer.transfer().code, 'fixture_identity_changed')
        self.assertEqual(self.driver.sends, 0)

    def test_extra_selected_file_blocks_before_hashing(self):
        self.writer.transfer()
        self.driver.state['files']['floorplan'].append(dict(self.driver.state['files']['floorplan'][0]))
        self.driver.async_reads = 0
        self.assertEqual(self.writer.observe().code, 'selected_files_mismatch')
        self.assertEqual(self.driver.async_reads, 0)

    def test_symlink_parent_rejected_before_send(self):
        link = Path(self.tmp.name) / 'alias'
        try:
            link.symlink_to(self.tmp.name, target_is_directory=True)
        except OSError:
            self.skipTest('Symlinks unavailable')
        files = tuple(replace(f, local_path=str(link / Path(f.local_path).name)) for f in self.request.files)
        request = replace(self.request, files=files)
        nonce = secrets.token_hex(32)
        driver = Driver(request, nonce)
        writer = bind_synthetic_media_for_tests(driver, request, nonce=nonce)
        self.assertEqual(writer.transfer().code, 'local_file_unsafe')
        self.assertEqual(driver.sends, 0)

    def test_path_control_and_webdriver_key_characters_rejected(self):
        for character in ('\n', '\r', '\x00', '\ue007'):
            with self.subTest(character=repr(character)):
                with self.assertRaises(TransferRejected):
                    replace(self.request.files[0], local_path=str(Path(self.tmp.name) / ('a' + character + '.png')))


if __name__ == '__main__':
    unittest.main()
