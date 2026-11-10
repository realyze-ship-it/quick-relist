"""Private, local review of photo-pack attempts; never attributes or retries one.

A review preserves all files and the original intention. Clearing the pause is
an explicit operator decision after closing every Quick Relist browser. Local
file metadata detects ordinary concurrent changes, not malicious same-user edits.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from .media import _prepare_root, _private_directory, _private_file

MARKER_NAME = "download-review-required.json"


class DownloadReviewError(RuntimeError):
    """Messages are safe to show; do not include file contents or exceptions."""


def _regular(path: Path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise DownloadReviewError("A download record or file is linked or not a regular file. The pause was kept.")
    return info


@contextmanager
def download_lock(root: Path):
    """Nonblocking process lock shared by download attempts and review/reset."""
    _prepare_root(root)
    handle = None
    try:
        path = root / ".download-operation.lock"
        if path.exists() or path.is_symlink():
            _regular(path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags, 0o600)
        handle = os.fdopen(fd, "r+b")
        opened, named = os.fstat(handle.fileno()), _regular(path)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise DownloadReviewError("The download lock changed. The pause was kept.")
        if os.name == "nt":
            import msvcrt
            if not opened.st_size:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except DownloadReviewError:
        raise
    except OSError:
        raise DownloadReviewError("The download folder is unavailable or another download/review is running. Try again after it finishes.") from None
    finally:
        if handle is not None:
            handle.close()


def _stamp(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _read_review(root: Path):
    marker = root / MARKER_NAME
    before = _regular(marker)
    if not 0 < before.st_size <= 4096:
        raise DownloadReviewError("The download record is invalid. The pause was kept; preserve the folder for support.")
    fd = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as stream:
        if _stamp(os.fstat(stream.fileno())) != _stamp(before):
            raise DownloadReviewError("The download record changed. Review it again.")
        raw = stream.read(4097)
        if _stamp(os.fstat(stream.fileno())) != _stamp(before) or _stamp(marker.lstat()) != _stamp(before):
            raise DownloadReviewError("The download record changed. Review it again.")
    try:
        record = json.loads(raw)
        if (not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1
                or record.get("state") != "attempted" or not isinstance(record.get("directory"), str)
                or not re.fullmatch(r"download-[a-f0-9]{32}", record["directory"])):
            raise ValueError
    except (ValueError, UnicodeError):
        raise DownloadReviewError("The download record is invalid. The pause was kept; preserve the folder for support.") from None
    directory = root / record["directory"]
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise DownloadReviewError("The original download folder is unavailable or linked. The pause was kept.")
    files = []
    for path in directory.iterdir():
        if len(files) >= 100:
            raise DownloadReviewError("The download folder has too many entries for review. The pause was kept.")
        details = _regular(path)
        name = path.name
        kind = "partial" if name.lower().endswith(".crdownload") else "zip_candidate" if path.suffix.lower() == ".zip" else "other"
        files.append({"name": name, "size": details.st_size, "kind": kind, "stamp": _stamp(details)})
    files.sort(key=lambda entry: entry["name"])
    material = {"marker_sha256": hashlib.sha256(raw).hexdigest(), "marker_stamp": _stamp(before),
                "directory": record["directory"], "directory_stamp": _stamp(info), "files": files}
    token = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    # No file is imported, renamed, deleted, opened, or assigned to an RXCU.
    return {"review_token": token, "directory": str(directory), "files": files,
            "attempt": record, "complete": False, "attribution_verified": False}


def inspect_download(download_root):
    root = Path(os.path.abspath(download_root))
    try:
        with download_lock(root):
            return _read_review(root)
    except DownloadReviewError:
        raise
    except (OSError, ValueError):
        raise DownloadReviewError("The previous download could not be reviewed safely. The pause was kept.") from None


def finish_download_review(download_root, review_token, *, browsers_closed=False, files_reviewed=False):
    """Preserve a receipt, then lift only the local download pause.

    The caller must close its controlled browser successfully and obtain explicit
    confirmation that older/orphaned Quick Relist browsers are closed and files
    reviewed. This cannot verify process state in another app instance. A future
    attempt always uses a new folder and requires a new click; no automatic retry.
    """
    if browsers_closed is not True or files_reviewed is not True:
        raise DownloadReviewError("Close every Quick Relist browser and confirm that you reviewed the saved files first.")
    if not isinstance(review_token, str) or not re.fullmatch(r"[a-f0-9]{64}", review_token):
        raise DownloadReviewError("A current download review is required.")
    root = Path(os.path.abspath(download_root))
    try:
        with download_lock(root):
            current = _read_review(root)
            if current["review_token"] != review_token:
                raise DownloadReviewError("The download files or record changed after review. Review them again; the pause was kept.")
            # A fresh protected directory supplies an owner-only Windows DACL
            # even when the existing downloads root has inherited permissions.
            receipt_directory = root / ("download-reviewed-" + uuid4().hex)
            _private_directory(receipt_directory)
            receipt = receipt_directory / "review.json"
            with _private_file(receipt) as stream:
                stream.write(json.dumps({**current, "reviewed_at": datetime.now(timezone.utc).isoformat(),
                                         "operator_confirmed_browsers_closed": True,
                                         "operator_confirmed_files_reviewed": True,
                                         "state": "reviewed_files_preserved"}, indent=2, ensure_ascii=True).encode())
                stream.flush()
                os.fsync(stream.fileno())
            _sync_directory(receipt_directory)
            _sync_directory(root)
            # Recheck after writing the receipt. Any changed bytes keep the pause.
            if _read_review(root)["review_token"] != review_token:
                raise DownloadReviewError("The download changed during review. The pause was kept.")
            (root / MARKER_NAME).unlink()
            _sync_directory(root)
            return {"receipt_path": str(receipt), "directory": current["directory"], "files_preserved": True,
                    "complete": False, "attribution_verified": False}
    except DownloadReviewError:
        raise
    except (OSError, ValueError):
        raise DownloadReviewError("The review could not finish. Refresh to check the pause status; files were not removed. Do not automatically retry a download.") from None


def _sync_directory(root):
    if os.name != "nt":
        descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
