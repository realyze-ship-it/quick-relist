"""Optional CDP evidence for one download; never imports or certifies a photo pack.

Use a fresh private directory. Enter, check ``supported``, call ``arm()`` just
before the single authorized click, then poll. Unsupported setup permits a
pre-click filesystem-only fallback. No result permits an automatic click retry.
Selenium dispatches callbacks on threads: ambiguous ordering fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from threading import RLock

from .core import normalize_rxcu

_GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_FILENAME = re.compile(r"(RXCU-?[0-9]{3,20})(?=$|[ _.-])", re.I)


@dataclass(frozen=True)
class DownloadCompletion:
    guid: str
    received_bytes: int


class PhotoDownloadEvents:
    """Fail-closed, single-use event observer for the currently selected page.

    ``poll()`` returns GUID and byte count only. The caller must independently
    validate the regular file at directory/GUID, its size and ZIP safety, and
    recheck the listing identity before association. Raw event URLs, suggested
    filenames and event file paths are never stored or returned here.
    """

    def __init__(self, driver, directory, rxcu, *, use_default_context=False):
        self.driver = driver
        self.directory = Path(directory)
        self.reference = normalize_rxcu(rxcu)
        if type(use_default_context) is not bool:
            raise ValueError("Default-context mode must be explicit")
        # Only the caller that created a dedicated default browser session may
        # select this mode. Never infer it from a failed context lookup.
        self._use_default_context = use_default_context
        self._behavior_context = None
        self.supported = False
        self.failed = False
        self._armed = False
        self._closed = False
        self._entered = False
        self._guid = None
        self._completion = None
        self._received = 0
        self._lock = RLock()
        self._callbacks = []
        self._connection = None
        self._context = None
        self._behavior_set = False
        self._observer_session = None
        self._previous_session = None

    def _select_current_target(self):
        """Attach a temporary session when Selenium caches an earlier tab.

        The selected WebDriver target remains authoritative. Never switch the
        browser to match a stale event session, or relax the frame checks.
        """
        current = self.driver.execute_cdp_cmd("Target.getTargetInfo", {})["targetInfo"]
        handle = self.driver.current_window_handle
        if current.get("type") != "page" or current.get("targetId") != handle:
            raise ValueError("The selected download target cannot be identified")
        try:
            cached = self._connection.execute(self._devtools.target.get_target_info())
            if str(cached.target_id) == handle:
                return
        except Exception:
            pass  # The cached page may have closed; verify a fresh session below.
        self._previous_session = self._connection.session_id
        self._connection.session_id = None
        try:
            session = self._connection.execute(self._devtools.target.attach_to_target(
                self._devtools.target.TargetID(handle), flatten=True))
            if not session:
                raise ValueError("The selected download target could not be attached")
            self._observer_session = session
            self._connection.session_id = session
        except Exception:
            self._connection.session_id = self._previous_session
            raise

    def _binding(self):
        # Both transports must refer to the selected page, not start_devtools'
        # potentially cached session from an earlier tab.
        current = self.driver.execute_cdp_cmd("Target.getTargetInfo", {})["targetInfo"]
        target = self._connection.execute(self._devtools.target.get_target_info())
        tree = self._connection.execute(self._devtools.page.get_frame_tree())
        frame = tree.frame
        context = target.browser_context_id
        if (target.type_ != "page" or current.get("type") != "page"
                or str(target.target_id) != current.get("targetId")
                or str(target.target_id) != self.driver.current_window_handle
                or (not self._use_default_context and not context)
                or str(context or "") != str(current.get("browserContextId") or "")
                or not frame.id_ or not frame.loader_id or frame.parent_id is not None):
            raise ValueError("Download target cannot be bound")
        return (str(target.target_id), str(context or ""), str(frame.id_), str(frame.loader_id)), context

    def __enter__(self):
        if self._entered:
            raise RuntimeError("Download observer is single-use")
        self._entered = True
        try:
            if (not self.directory.is_absolute() or self.directory.is_symlink()
                    or not self.directory.is_dir() or any(self.directory.iterdir())):
                raise ValueError("A fresh download directory is required")
            self._devtools, self._connection = self.driver.start_devtools()
            if not callable(getattr(self._connection, "remove_callback", None)):
                raise ValueError("Callback cleanup unavailable")
            self._select_current_target()
            browser = self._devtools.browser
            self._binding_value, self._context = self._binding()
            self._behavior_context = None if self._use_default_context else self._context
            for event, callback in ((browser.DownloadWillBegin, self._begin),
                                    (browser.DownloadProgress, self._progress)):
                callback_id = self._connection.add_callback(event, callback)
                self._callbacks.append((event, callback_id))
            # Mark before execute: an exception may follow successful execution.
            self._behavior_set = True
            self._connection.execute(browser.set_download_behavior(
                "allowAndName", browser_context_id=self._behavior_context,
                download_path=str(self.directory), events_enabled=True))
            self.supported = True
        except Exception:
            # Do not retain exception messages: browser errors may contain URLs.
            self._cleanup()
        return self

    def arm(self):
        """Return whether observation is armed; call immediately before clicking."""
        with self._lock:
            if not self.supported or self.failed or self._closed or self._armed:
                self.failed = True
                return False
            try:
                if self._binding()[0] != self._binding_value or any(self.directory.iterdir()):
                    raise ValueError("Download binding changed")
                self._armed = True
                return True
            except Exception:
                self.failed = True
                return False

    def _begin(self, event):
        with self._lock:
            try:
                name = event.suggested_filename
                match = _FILENAME.match(name) if isinstance(name, str) else None
                if (self._closed or not self._armed or self._guid is not None
                        or not isinstance(event.guid, str) or not _GUID.fullmatch(event.guid)
                        or str(event.frame_id) != self._binding_value[2]
                        or not match or normalize_rxcu(match[1]) != self.reference
                        or len(name) > 255 or not name.lower().endswith(".zip")
                        or any(ord(c) < 32 or ord(c) == 127 or c in '/\\:<>"|?*' for c in name)):
                    raise ValueError("Ambiguous download")
                self._guid = event.guid
            except Exception:
                self.failed = True

    def _progress(self, event):
        with self._lock:
            try:
                total, received = event.total_bytes, event.received_bytes
                if (self._closed or not self._armed or self._guid is None
                        or event.guid != self._guid or self._completion is not None
                        or event.state not in ("inProgress", "completed")
                        or any(isinstance(n, bool) or not isinstance(n, (int, float))
                               or not math.isfinite(n) or n < 0 or int(n) != n
                               or n > 2**53 for n in (total, received))
                        or received < self._received or (total > 0 and received > total)):
                    raise ValueError("Ambiguous download progress")
                self._received = int(received)
                if event.state == "completed":
                    if total <= 0 or received != total:
                        raise ValueError("Incomplete download")
                    self._completion = DownloadCompletion(self._guid, int(received))
            except Exception:
                self.failed = True

    def poll(self):
        """Return completion only while all observed evidence remains consistent."""
        with self._lock:
            if not self.supported or self.failed or self._closed or not self._armed:
                return None
            try:
                if self._binding()[0] != self._binding_value:
                    raise ValueError("Download binding changed")
            except Exception:
                self.failed = True
                return None
            return self._completion

    def _cleanup(self):
        # No browser shutdown: start_devtools returns a driver-owned connection.
        if self._behavior_set:
            try:
                self._connection.execute(self._devtools.browser.set_download_behavior(
                    "allow", browser_context_id=self._behavior_context,
                    download_path=str(self.directory), events_enabled=False))
            except Exception:
                self.failed = True
            self._behavior_set = False
        for event, callback_id in self._callbacks:
            try:
                self._connection.remove_callback(event, callback_id)
            except Exception:
                self.failed = True
        self._callbacks.clear()
        if self._observer_session is not None:
            try:
                self._connection.session_id = None
                self._connection.execute(self._devtools.target.detach_from_target(
                    session_id=self._observer_session))
            except Exception:
                self.failed = True
            finally:
                self._connection.session_id = self._previous_session
                self._observer_session = None

    def __exit__(self, exc_type, exc, traceback):
        with self._lock:
            self._closed = True
        self._cleanup()
