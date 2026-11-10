"""Synthetic events only: never starts Chrome or contacts Hub."""
from types import SimpleNamespace as NS

import pytest

from quick_relist.photo_downloads import DownloadCompletion, PhotoDownloadEvents

GUID = "12345678-1234-1234-1234-123456789abc"


class WillBegin:
    pass


class Progress:
    pass


class Connection:
    def __init__(self):
        self.callbacks = {}
        self.behaviors = []
        self.target = NS(target_id="tab", type_="page", browser_context_id="incognito")
        self.frame = NS(id_="frame", loader_id="loader", parent_id=None)

    def execute(self, command):
        name, params = command
        if name == "target":
            return self.target
        if name == "frame":
            return NS(frame=self.frame)
        self.behaviors.append(params)

    def add_callback(self, event, callback):
        self.callbacks[event] = callback
        return id(callback)

    def remove_callback(self, event, callback_id):
        assert id(self.callbacks[event]) == callback_id
        del self.callbacks[event]

    def begin(self, **kwargs):
        values = dict(frame_id="frame", guid=GUID, url="https://invalid.example/private",
                      suggested_filename="RXCU-12345 Synthetic address.zip")
        values.update(kwargs)
        self.callbacks[WillBegin](NS(**values))

    def progress(self, **kwargs):
        values = dict(guid=GUID, total_bytes=20, received_bytes=20, state="completed",
                      file_path="/untrusted/private/path.zip")
        values.update(kwargs)
        self.callbacks[Progress](NS(**values))


class Driver:
    current_window_handle = "tab"

    def __init__(self):
        self.connection = Connection()
        self.info = dict(targetId="tab", type="page", browserContextId="incognito")
        self.devtools = NS(
            target=NS(get_target_info=lambda: ("target", {})),
            page=NS(get_frame_tree=lambda: ("frame", {})),
            browser=NS(DownloadWillBegin=WillBegin, DownloadProgress=Progress,
                       set_download_behavior=lambda behavior, **kw: ("behavior", dict(behavior=behavior, **kw))))

    def start_devtools(self):
        return self.devtools, self.connection

    def execute_cdp_cmd(self, name, params):
        assert name == "Target.getTargetInfo" and params == {}
        return {"targetInfo": self.info}


@pytest.fixture
def driver():
    return Driver()


def test_scoped_behavior_completion_and_cleanup(driver, tmp_path):
    connection = driver.connection
    with PhotoDownloadEvents(driver, tmp_path, "RXCU12345") as events:
        assert events.supported and events.arm()
        assert connection.behaviors == [dict(behavior="allowAndName", browser_context_id="incognito",
                                            download_path=str(tmp_path), events_enabled=True)]
        assert events.poll() is None
        connection.begin()
        connection.progress(state="inProgress", received_bytes=5)
        assert events.poll() is None
        connection.progress()
        result = events.poll()
        assert result == DownloadCompletion(GUID, 20)
        assert "private" not in repr(vars(events))
        assert "address" not in repr(vars(events))
    assert not connection.callbacks
    assert connection.behaviors[-1]["behavior"] == "allow"
    assert connection.behaviors[-1]["browser_context_id"] == "incognito"
    assert events.poll() is None


@pytest.mark.parametrize("changes", [
    {"frame_id": "other"}, {"guid": "../../evil"}, {"guid": ""},
    {"suggested_filename": "RXCU123456.zip"}, {"suggested_filename": "RXCU12345evil.zip"},
    {"suggested_filename": "RXCU12345.zip.exe"}, {"suggested_filename": "RXCU12345/evil.zip"},
    {"suggested_filename": "RXCU12345\\evil.zip"}, {"suggested_filename": "RXCU12345\n.zip"},
    {"suggested_filename": "RXCU12345:evil.zip"}, {"suggested_filename": None},
])
def test_bad_start_never_attributes(driver, tmp_path, changes):
    with PhotoDownloadEvents(driver, tmp_path, "RXCU12345") as events:
        assert events.arm()
        driver.connection.begin(**changes)
        driver.connection.progress()
        assert events.failed and events.poll() is None


@pytest.mark.parametrize("changes", [
    {"guid": "other"}, {"state": "canceled"}, {"state": "unknown"},
    {"total_bytes": 0, "received_bytes": 0}, {"received_bytes": 19},
    {"received_bytes": 21}, {"total_bytes": float("nan")},
    {"received_bytes": float("inf")}, {"received_bytes": -1},
    {"received_bytes": True}, {"received_bytes": 1.5},
])
def test_bad_completion_never_attributes(driver, tmp_path, changes):
    with PhotoDownloadEvents(driver, tmp_path, "RXCU12345") as events:
        assert events.arm()
        driver.connection.begin()
        driver.connection.progress(**changes)
        assert events.failed and events.poll() is None


@pytest.mark.parametrize("scenario", ["prearm_begin", "prearm_progress", "progress_first", "duplicate", "unrelated", "regression"])
def test_ambiguous_event_order_fails_closed(driver, tmp_path, scenario):
    with PhotoDownloadEvents(driver, tmp_path, "RXCU12345") as events:
        c = driver.connection
        if scenario == "prearm_begin":
            c.begin()
        elif scenario == "prearm_progress":
            c.progress()
        else:
            assert events.arm()
            if scenario == "progress_first":
                c.progress()
            else:
                c.begin()
                if scenario == "duplicate":
                    c.begin()
                elif scenario == "unrelated":
                    c.begin(guid="abcdefab-1234-1234-1234-123456789abc")
                else:
                    c.progress(state="inProgress", received_bytes=10)
                    c.progress(state="inProgress", received_bytes=5)
        assert events.failed and events.poll() is None


@pytest.mark.parametrize("change", ["target", "context", "frame", "loader", "window"])
def test_changed_binding_after_click_fails_closed(driver, tmp_path, change):
    with PhotoDownloadEvents(driver, tmp_path, "RXCU12345") as events:
        assert events.arm()
        driver.connection.begin()
        driver.connection.progress()
        if change == "target":
            driver.connection.target.target_id = "other"
        elif change == "context":
            driver.connection.target.browser_context_id = "other"
        elif change == "window":
            driver.current_window_handle = "other"
        else:
            setattr(driver.connection.frame, "id_" if change == "frame" else "loader_id", "other")
        assert events.poll() is None and events.failed


@pytest.mark.parametrize("change", ["unsupported", "context", "cached_target", "directory", "setup_error"])
def test_unsupported_before_click_is_cleaned_up(driver, tmp_path, change):
    if change == "unsupported":
        driver.start_devtools = None
    elif change == "context":
        driver.connection.target.browser_context_id = None
    elif change == "cached_target":
        driver.connection.target.target_id = "old-tab"
    elif change == "directory":
        (tmp_path / "old.zip").write_bytes(b"old")
    else:
        def fail(*args, **kwargs):
            raise RuntimeError("private URL must not be retained")
        driver.devtools.browser.set_download_behavior = fail
    with PhotoDownloadEvents(driver, tmp_path, "RXCU12345") as events:
        assert not events.supported and not events.arm()
        assert events.poll() is None
    assert not driver.connection.callbacks


def test_directory_changed_before_arm(driver, tmp_path):
    with PhotoDownloadEvents(driver, tmp_path, "RXCU12345") as events:
        (tmp_path / GUID).write_bytes(b"preexisting")
        assert not events.arm()
        assert events.poll() is None


def test_second_arm_and_reentry_rejected(driver, tmp_path):
    with PhotoDownloadEvents(driver, tmp_path, "RXCU12345") as events:
        assert events.arm() and not events.arm()
    with pytest.raises(RuntimeError):
        events.__enter__()


def test_exception_does_not_leak_callbacks_or_swallow_click_error(driver, tmp_path):
    with pytest.raises(RuntimeError, match="click outcome unknown"):
        with PhotoDownloadEvents(driver, tmp_path, "RXCU12345") as events:
            assert events.arm()
            raise RuntimeError("click outcome unknown")
    assert not driver.connection.callbacks


def test_generated_selenium_contract_without_browser(driver, tmp_path):
    browser = pytest.importorskip("selenium.webdriver.common.devtools.v156.browser")
    page = pytest.importorskip("selenium.webdriver.common.devtools.v156.page")
    with PhotoDownloadEvents(driver, tmp_path, "RXCU12345") as events:
        assert events.arm()
        events._begin(browser.DownloadWillBegin(
            frame_id=page.FrameId("frame"), guid=GUID,
            url="https://invalid.example/private", suggested_filename="RXCU12345.zip"))
        events._progress(browser.DownloadProgress(
            guid=GUID, total_bytes=20.0, received_bytes=20.0, state="completed",
            file_path="/not-trusted"))
        assert events.poll() == DownloadCompletion(GUID, 20)
    command = next(browser.set_download_behavior(
        "allowAndName", browser_context_id=browser.BrowserContextID("incognito"),
        download_path=str(tmp_path), events_enabled=True))
    assert command["params"]["browserContextId"] == "incognito"
    assert command["params"]["eventsEnabled"] is True


def test_explicit_managed_default_context_omits_unregistered_context_selector(tmp_path):
    driver = Driver()
    with PhotoDownloadEvents(driver, tmp_path, "RXCU12345", use_default_context=True) as events:
        assert events.supported and events.arm()
        driver.connection.begin()
        driver.connection.progress()
        assert events.poll() == DownloadCompletion(GUID, 20)
        assert driver.connection.behaviors[0]['browser_context_id'] is None
    assert driver.connection.behaviors[-1]['browser_context_id'] is None
    assert not events.failed
