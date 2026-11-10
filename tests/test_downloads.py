"""Synthetic local recovery tests: no browser, Hub or real listing data."""
import json
import os
from pathlib import Path

import pytest

from quick_relist.downloads import (
    DownloadReviewError, MARKER_NAME, download_lock, finish_download_review, inspect_download,
)


@pytest.fixture
def attempt(tmp_path):
    root = tmp_path / "downloads"
    root.mkdir(mode=0o700)
    folder = root / ("download-" + "1" * 32)
    folder.mkdir(mode=0o700)
    record = {"version": 1, "directory": folder.name, "state": "attempted"}
    (root / MARKER_NAME).write_text(json.dumps(record))
    return root, folder


def finish(root, review):
    return finish_download_review(root, review["review_token"], browsers_closed=True, files_reviewed=True)


def test_review_preserves_empty_attempt_and_does_not_imply_success(attempt):
    root, folder = attempt
    review = inspect_download(root)
    assert review["files"] == [] and review["directory"] == str(folder)
    assert review["complete"] is False and review["attribution_verified"] is False
    assert (root / MARKER_NAME).exists()
    result = finish(root, review)
    assert not (root / MARKER_NAME).exists() and folder.exists()
    receipt = json.loads(Path(result["receipt_path"]).read_text())
    assert receipt["state"] == "reviewed_files_preserved"
    assert receipt["attempt"]["directory"] == folder.name
    assert receipt["operator_confirmed_browsers_closed"] is True
    assert receipt["complete"] is False and receipt["attribution_verified"] is False
    if os.name != "nt":
        assert Path(result["receipt_path"]).stat().st_mode & 0o077 == 0


def test_zip_partial_and_other_files_are_preserved_byte_for_byte(attempt):
    root, folder = attempt
    entries = {"one.zip": b"synthetic zip candidate", "two.zip.crdownload": b"incomplete", "other.txt": b"unknown"}
    for name, data in entries.items():
        (folder / name).write_bytes(data)
    review = inspect_download(root)
    assert {item["kind"] for item in review["files"]} == {"partial", "zip_candidate", "other"}
    finish(root, review)
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == entries


@pytest.mark.parametrize("closed,reviewed", [(False, True), (True, False), (False, False), (1, True)])
def test_both_explicit_attestations_required(attempt, closed, reviewed):
    root, _ = attempt
    token = inspect_download(root)["review_token"]
    with pytest.raises(DownloadReviewError, match="Close every"):
        finish_download_review(root, token, browsers_closed=closed, files_reviewed=reviewed)
    assert (root / MARKER_NAME).exists()
    assert not list(root.glob("download-reviewed-*"))


@pytest.mark.parametrize("change", ["grow", "new", "replace_marker", "remove", "same_size"])
def test_changed_files_or_intention_keep_pause(attempt, change):
    root, folder = attempt
    source = folder / "one.zip"
    source.write_bytes(b"original")
    review = inspect_download(root)
    if change == "grow": source.write_bytes(b"longer original")
    elif change == "same_size": source.write_bytes(b"modified")
    elif change == "new": (folder / "late.zip").write_bytes(b"late")
    elif change == "remove": source.unlink()
    elif change == "replace_marker":
        record = json.loads((root / MARKER_NAME).read_text())
        record["extra"] = "synthetic changed record"
        (root / MARKER_NAME).write_text(json.dumps(record))
    with pytest.raises(DownloadReviewError, match="changed"):
        finish(root, review)
    assert (root / MARKER_NAME).exists()


@pytest.mark.parametrize("directory", ["../escape", "/tmp", "download-abc", "..", "download-" + "g" * 32])
def test_marker_cannot_redirect_review_outside_generated_folder(attempt, directory):
    root, _ = attempt
    (root / MARKER_NAME).write_text(json.dumps({"version": 1, "directory": directory, "state": "attempted"}))
    with pytest.raises(DownloadReviewError, match="invalid"):
        inspect_download(root)
    assert (root / MARKER_NAME).exists()


@pytest.mark.parametrize("target", ["marker", "file", "folder"])
def test_links_never_followed(attempt, tmp_path, target):
    root, folder = attempt
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret").write_text("SYNTHETIC_NOT_FOR_REVIEW")
    if target == "marker":
        (root / MARKER_NAME).unlink()
        (root / MARKER_NAME).symlink_to(external / "secret")
    elif target == "file":
        (folder / "candidate.zip").symlink_to(external / "secret")
    else:
        folder.rmdir()
        folder.symlink_to(external, target_is_directory=True)
    with pytest.raises(DownloadReviewError) as caught:
        inspect_download(root)
    assert "SYNTHETIC_NOT_FOR_REVIEW" not in str(caught.value)
    assert (root / MARKER_NAME).exists() or (root / MARKER_NAME).is_symlink()


def test_overlarge_or_malformed_marker_keeps_pause(attempt):
    root, _ = attempt
    for raw in ("X" * 4097, "{bad json", "[]", "{}"):
        (root / MARKER_NAME).write_text(raw)
        with pytest.raises(DownloadReviewError): inspect_download(root)
        assert (root / MARKER_NAME).exists()


def test_second_reset_does_not_touch_reviewed_files(attempt):
    root, folder = attempt
    review = inspect_download(root)
    first = finish(root, review)
    with pytest.raises(DownloadReviewError): finish(root, review)
    assert list(root.glob("download-reviewed-*")) == [Path(first["receipt_path"]).parent]
    assert folder.exists()


def test_concurrent_attempt_or_review_blocks_reset(attempt):
    root, _ = attempt
    review = inspect_download(root)
    with download_lock(root):
        with pytest.raises(DownloadReviewError, match="another download/review"):
            finish(root, review)
    assert (root / MARKER_NAME).exists()


def test_receipt_failure_keeps_original_marker(attempt, monkeypatch):
    from quick_relist import downloads
    root, _ = attempt
    review = inspect_download(root)
    def fail(_): raise OSError("SYNTHETIC_PRIVATE_PATH")
    monkeypatch.setattr(downloads, "_private_file", fail)
    with pytest.raises(DownloadReviewError) as caught: finish(root, review)
    assert "SYNTHETIC_PRIVATE_PATH" not in str(caught.value)
    assert (root / MARKER_NAME).exists()


def test_new_file_during_receipt_write_keeps_pause(attempt, monkeypatch):
    from quick_relist import downloads
    root, folder = attempt
    review = inspect_download(root)
    original = downloads._sync_directory
    def change(path):
        original(path)
        (folder / "late.zip").write_bytes(b"late synthetic bytes")
    monkeypatch.setattr(downloads, "_sync_directory", change)
    with pytest.raises(DownloadReviewError, match="changed during"):
        finish(root, review)
    assert (root / MARKER_NAME).exists()
    assert (folder / "late.zip").exists()


def test_download_review_survives_module_restart(attempt):
    root, folder = attempt
    (folder / "candidate.zip").write_bytes(b"synthetic")
    first = inspect_download(root)
    # Use a fresh import in another process, as on application restart.
    import subprocess, sys
    result = subprocess.run([sys.executable, "-c", "from quick_relist.downloads import inspect_download; import sys; print(inspect_download(sys.argv[1])['review_token'])", str(root)], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == first["review_token"]


def test_receipt_uses_fresh_protected_directory_even_with_existing_root(attempt, monkeypatch):
    from quick_relist import downloads
    root, _ = attempt
    review = inspect_download(root)
    created = []
    original = downloads._private_directory
    def protected(path):
        created.append(path)
        original(path)
    monkeypatch.setattr(downloads, "_private_directory", protected)
    result = finish(root, review)
    receipt = Path(result["receipt_path"])
    assert receipt.name == "review.json"
    assert receipt.parent in created and receipt.parent.parent == root
    if os.name != "nt":
        assert receipt.parent.stat().st_mode & 0o077 == 0


def test_new_attempt_after_review_uses_new_folder_and_keeps_previous_files(tmp_path):
    import zipfile
    from quick_relist.hub_readonly import ReadOnlyHub
    pytest.importorskip("selenium")
    class Button:
        text = "Download photo pack"
        clicks = 0
        def __init__(self, driver): self.driver = driver
        def is_displayed(self): return True
        def is_enabled(self): return True
        def get_attribute(self, key): return "button" if key == "type" else None
        def click(self):
            self.clicks += 1
            with zipfile.ZipFile(self.driver.folder / "synthetic.zip", "w") as archive:
                archive.writestr("synthetic.jpg", b"synthetic image")
    class Driver:
        current_url = "https://hub.remax.co.za/app/listings/edit/123/media"
        current_window_handle = "synthetic"
        window_handles = ["synthetic"]
        def __init__(self): self.button = Button(self)
        def execute_script(self, *args): return {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
        def find_elements(self, *args): return [self.button]
        def execute_cdp_cmd(self, method, params): self.folder = Path(params["downloadPath"])
        def quit(self): pass
    root = tmp_path / "downloads"
    first_driver = Driver()
    hub = ReadOnlyHub(first_driver)
    hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "synthetic"}
    first = hub.download_photo_pack("RXCU12345", "123", root, timeout=1, poll_interval=.01)
    first_bytes = Path(first["candidate_path"]).read_bytes()
    hub.close()
    finish(root, inspect_download(root))
    assert first_driver.button.clicks == 1
    second_driver = Driver()
    new_hub = ReadOnlyHub(second_driver)
    new_hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "synthetic"}
    second = new_hub.download_photo_pack("RXCU12345", "123", root, timeout=1, poll_interval=.01)
    assert second_driver.button.clicks == 1
    assert first["directory"] != second["directory"]
    assert Path(first["candidate_path"]).read_bytes() == first_bytes
    assert (root / MARKER_NAME).exists()  # New request has its own unresolved pause.
    assert second["attribution_verified"] is False
