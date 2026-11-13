"""Private, append-only *partial* observations and tentative address drafts.

This store is separate from the relist engine. It cannot approve a listing or
turn an incomplete observation into a verified source snapshot. Records may
contain personal data; keep this database in the per-user data directory.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import stat
import threading
from uuid import uuid4


def _encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Observation must contain finite JSON data") from exc


def _digest(encoded):
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class DraftStore:
    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.RLock()
        if self.path != ":memory:":
            directory = Path(self.path).parent
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            # A private directory keeps SQLite's subsequent path-based reopen
            # from racing another local user. Never follow an existing symlink.
            parent_info = directory.lstat()
            if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
                    or getattr(parent_info, "st_file_attributes", 0) & 0x400):
                raise ValueError("Choose a private local data directory")
            if os.name != "nt" and (parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o077):
                raise ValueError("The local data directory must be accessible only to you")
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                if Path(self.path).is_symlink():
                    raise ValueError("The local data file must not be a symbolic link")
                fd = os.open(self.path, flags, 0o600)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        raise ValueError("The local data file must be a regular file")
                    if os.name != "nt":
                        if info.st_uid != os.getuid() or info.st_nlink != 1:
                            raise ValueError("The local data file must be owned by you and not linked elsewhere")
                        os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
            except OSError:
                raise ValueError("The private local data file could not be opened safely") from None
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS captures (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, captured_at TEXT NOT NULL,
            observation_json TEXT NOT NULL, observation_sha256 TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS addresses (
            id TEXT PRIMARY KEY, capture_id TEXT NOT NULL REFERENCES captures(id),
            saved_at TEXT NOT NULL, original_json TEXT NOT NULL, edited_json TEXT NOT NULL,
            original_sha256 TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS captures_no_replace BEFORE INSERT ON captures
        WHEN EXISTS (SELECT 1 FROM captures WHERE id=NEW.id) BEGIN
          SELECT RAISE(ABORT, 'Observations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS addresses_no_replace BEFORE INSERT ON addresses
        WHEN EXISTS (SELECT 1 FROM addresses WHERE id=NEW.id) BEGIN
          SELECT RAISE(ABORT, 'Address drafts are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS captures_no_update BEFORE UPDATE ON captures BEGIN
          SELECT RAISE(ABORT, 'Observations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS captures_no_delete BEFORE DELETE ON captures BEGIN
          SELECT RAISE(ABORT, 'Observations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS addresses_no_update BEFORE UPDATE ON addresses BEGIN
          SELECT RAISE(ABORT, 'Address drafts are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS addresses_no_delete BEFORE DELETE ON addresses BEGIN
          SELECT RAISE(ABORT, 'Address drafts are immutable'); END;
        """)
        self.conn.commit()

    def close(self):
        with self._lock:
            self.conn.close()

    def save_capture(self, job_id, capture):
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("A local job is required")
        if not isinstance(capture, dict) or capture.get("complete") is not False:
            raise ValueError("Only explicitly incomplete observations may be saved here")
        encoded = _encoded(capture)
        ident = uuid4().hex
        with self._lock, self.conn:
            self.conn.execute("INSERT INTO captures VALUES(?,?,?,?,?)", (ident, job_id, datetime.now(timezone.utc).isoformat(), encoded, _digest(encoded)))
        return ident

    def get_capture(self, capture_id):
        with self._lock:
            row = self.conn.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
        if row is None:
            raise ValueError("Partial observation not found")
        if _digest(row["observation_json"]) != row["observation_sha256"]:
            raise ValueError("Partial observation integrity failed")
        return {"id": row["id"], "job_id": row["job_id"], "captured_at": row["captured_at"],
                "capture": json.loads(row["observation_json"]), "sha256": row["observation_sha256"]}

    def list_captures(self, job_id):
        with self._lock:
            ids = [row[0] for row in self.conn.execute("SELECT id FROM captures WHERE job_id=? ORDER BY rowid DESC", (job_id,))]
        return [self.get_capture(ident) for ident in ids]

    def save_address(self, capture_id, original, edited):
        capture = self.get_capture(capture_id)["capture"]
        if not isinstance(original, dict) or not original or not isinstance(edited, dict) or set(original) != set(edited):
            raise ValueError("Original and tentative address must have the same fields")
        for path, observed in original.items():
            if not isinstance(path, str) or not re.fullmatch(r"pages/\d+/(?:dialogs/\d+/)?fields/\d+", path):
                raise ValueError("Address fields must reference captured controls")
            try:
                item = capture
                for component in path.split("/"):
                    item = item[int(component)] if isinstance(item, list) else item[component]
            except (KeyError, IndexError, TypeError, ValueError):
                raise ValueError("Address field is absent from the observation") from None
            changed = edited[path]
            if not isinstance(item, dict) or _encoded(observed) != _encoded(item) or not isinstance(changed, dict) or set(changed) != set(item):
                raise ValueError("Original address must match the observed control")
            if any(_encoded(changed[key]) != _encoded(item[key]) for key in item if key not in {"value", "checked"}):
                raise ValueError("Control metadata must remain unchanged")
            if "value" in item and _encoded(changed["value"]) != _encoded(item["value"]):
                if not isinstance(changed["value"], str) or not (item["value"] is None or isinstance(item["value"], str)):
                    raise ValueError("Only text values may be edited")
            if "checked" in item and _encoded(changed["checked"]) != _encoded(item["checked"]):
                if not isinstance(item["checked"], bool) or not isinstance(changed["checked"], bool):
                    raise ValueError("Only captured checkbox states may be edited")
        original_encoded, edited_encoded = _encoded(original), _encoded(edited)
        ident = uuid4().hex
        with self._lock, self.conn:
            self.conn.execute("INSERT INTO addresses VALUES(?,?,?,?,?,?)", (ident, capture_id, datetime.now(timezone.utc).isoformat(), original_encoded, edited_encoded, _digest(original_encoded)))
        return ident

    def get_address(self, revision_id):
        with self._lock:
            row = self.conn.execute("SELECT * FROM addresses WHERE id=?", (revision_id,)).fetchone()
        if row is None:
            raise ValueError("Address draft not found")
        if _digest(row["original_json"]) != row["original_sha256"]:
            raise ValueError("Original address integrity failed")
        return {"id": row["id"], "capture_id": row["capture_id"], "saved_at": row["saved_at"],
                "original": json.loads(row["original_json"]), "edited": json.loads(row["edited_json"]),
                "original_sha256": row["original_sha256"], "approved": False}

    def list_addresses(self, capture_id):
        with self._lock:
            ids = [row[0] for row in self.conn.execute("SELECT id FROM addresses WHERE capture_id=? ORDER BY rowid DESC", (capture_id,))]
        return [self.get_address(ident) for ident in ids]
