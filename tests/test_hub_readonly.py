import pytest
from quick_relist.hub_readonly import ReadOnlyHub


class Driver:
    def __init__(self, url):
        self.current_url = url
        self.calls = []
        self.current_window_handle = "one"
        self.window_handles = ["one"]
        self.switch_to = self
    def window(self, handle):
        self.current_window_handle = handle
    def execute_script(self, script):
        self.calls.append(script)
        return [{"name": "synthetic", "value": "example"}]
    def quit(self):
        self.calls.append("quit")
    def set_page_load_timeout(self, seconds):
        self.page_load_timeout = seconds


def test_editor_inspection_uses_filtered_partial_collector(monkeypatch):
    driver = Driver("https://hub.remax.co.za/app/listings/edit/example/media")
    class Collector:
        def __init__(self, passed): assert passed is driver
        def capture_current_page(self): return {"complete": False, "pages": []}
    monkeypatch.setattr("quick_relist.capture.PartialHubCapture", Collector)
    result = ReadOnlyHub(driver).inspect_current_editor_page()
    assert result == {"complete": False, "pages": []}


@pytest.mark.parametrize("url", [
    "https://evil.example/app/listings/edit/example",
    "https://hub.remax.co.za.evil.example/app/listings/edit/example",
    "http://hub.remax.co.za/app/listings/edit/example",
    "https://hub.remax.co.za/app/listings/archive/example",
    "https://hub.remax.co.za/app/listings/add/sale",
])
def test_refuses_other_origins_and_pages(url):
    driver = Driver(url)
    with pytest.raises(ValueError):
        ReadOnlyHub(driver).inspect_current_editor_page()
    assert not driver.calls


def test_multiple_editor_tabs_are_not_guessed():
    class Tabs(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings")
            self.urls = {"one": "https://hub.remax.co.za/app/listings/edit/a",
                         "two": "https://hub.remax.co.za/app/listings/edit/b"}
            self.window_handles = ["one", "two"]
        def window(self, handle):
            self.current_window_handle = handle
            self.current_url = self.urls[handle]
    driver = Tabs()
    with pytest.raises(ValueError, match="exactly one"):
        ReadOnlyHub(driver).inspect_current_editor_page()
    assert driver.current_window_handle == "one"
    assert not driver.calls


@pytest.mark.parametrize("prompt", ["Search fusion ref, listing ID or address", "Enter fusion ref, listing ID or address"])
@pytest.mark.parametrize('menu_open', [False, True])
def test_search_only_fills_and_submits_unique_observed_controls(prompt, menu_open):
    pytest.importorskip("selenium")
    from selenium.webdriver.common.keys import Keys
    class Menu:
        opened = menu_open
        def is_displayed(self): return self.opened
        def send_keys(self, key):
            assert key == Keys.ESCAPE
            self.opened = False
        def click(self): raise AssertionError('No menu action may be activated')
    menu = Menu()
    class Control:
        def __init__(self, text="", placeholder=""):
            self.text, self.placeholder, self.events = text, placeholder, []
        def is_displayed(self): return True
        def is_enabled(self): return True
        def get_attribute(self, key): return self.placeholder if key == "placeholder" else None
        def clear(self): self.events.append("clear")
        def send_keys(self, *values):
            assert not menu.opened
            self.events.append(('type', values))
            self.value=values[-2]
        def get_property(self, key): return self.value
        def click(self): self.events.append("click")
    class SearchDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings")
            self.field = Control(placeholder=prompt)
            self.button = Control(text="Get listings")
        def find_elements(self, by, query):
            if query == '[role="listbox"]': return [menu] if menu.opened else []
            if query.startswith("//input"): return [self.field]
            if query == '//button': return [self.button]
            raise AssertionError(query)
    driver = SearchDriver()
    result = ReadOnlyHub(driver).search_reference("rxcu12345")
    assert result["searched"] is True
    from selenium.webdriver.common.keys import Keys
    assert driver.field.events == [('type', (Keys.CONTROL, 'a', Keys.NULL, 'RXCU-12345', Keys.TAB))]
    assert driver.button.events == ["click"]
    assert "not verify" in result["warning"]


def test_editor_reference_requires_both_matching_headings_and_numeric_route():
    class IdentityDriver(Driver):
        def execute_script(self, script, reference):
            assert reference == "RXCU12345"
            assert "querySelectorAll" in script
            return {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
    driver = IdentityDriver("https://hub.remax.co.za/app/listings/edit/123/media")
    result = ReadOnlyHub(driver).verify_editor_reference("rxcu12345", "123")
    assert result["editor_reference_matches"] is True
    assert result["account_verified"] is False and result["row_link_verified"] is False
    with pytest.raises(ValueError): ReadOnlyHub(driver).verify_editor_reference("RXCU12345", "456")
    with pytest.raises(ValueError): ReadOnlyHub(driver).verify_editor_reference("RXCU12345", "fake")


@pytest.mark.parametrize("result", [
    {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": False},
    {"outer_count": 2, "inner_count": 1, "outer_matches": True, "inner_matches": True},
    {"outer_count": 1, "inner_count": 0, "outer_matches": True, "inner_matches": True},
])
def test_editor_reference_refuses_ambiguous_or_mismatched_headings(result):
    class IdentityDriver(Driver):
        def execute_script(self, script, reference): return result
    with pytest.raises(ValueError):
        ReadOnlyHub(IdentityDriver("https://hub.remax.co.za/app/listings/edit/123")).verify_editor_reference("RXCU12345", "123")


@pytest.mark.parametrize("button_text", ["Download photo pack", "DOWNLOAD PHOTO PACK", " DOWNLOAD \n PHOTO PACK "])
def test_photo_pack_download_is_single_click_private_and_partial(tmp_path, button_text):
    import zipfile
    pytest.importorskip("selenium")
    class Button:
        text = button_text
        def __init__(self, driver): self.driver, self.clicks = driver, 0
        def is_displayed(self): return True
        def is_enabled(self): return True
        def get_attribute(self, key): return "button" if key == "type" else None
        def click(self):
            self.clicks += 1
            with zipfile.ZipFile(self.driver.download_path / "synthetic.zip", "w") as z:
                z.writestr("synthetic.jpg", b"synthetic")
    class DownloadDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings/edit/123/media")
            self.button = Button(self)
        def execute_script(self, script, ref):
            return {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
        def find_elements(self, by, name): return [self.button]
        def execute_cdp_cmd(self, method, settings):
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": self.current_window_handle, "type": "page", "browserContextId": "synthetic-context"}}
            from pathlib import Path
            assert method == "Browser.setDownloadBehavior"
            self.download_path = Path(settings["downloadPath"])
    driver = DownloadDriver()
    hub = ReadOnlyHub(driver)
    hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "one"}
    result = hub.download_photo_pack("RXCU12345", "123", tmp_path / "downloads", timeout=1, poll_interval=.01)
    assert driver.button.clicks == 1
    assert result["complete"] is False and result["candidate_path"].endswith("synthetic.zip")
    assert zipfile.is_zipfile(result["candidate_path"])
    if __import__("os").name != "nt":
        assert __import__("pathlib").Path(result["directory"]).stat().st_mode & 0o077 == 0


def test_photo_pack_uncertain_click_is_never_retried(tmp_path):
    pytest.importorskip("selenium")
    from quick_relist.hub_readonly import DownloadOutcomeUnknown
    class Button:
        text = "Download photo pack"
        clicks = 0
        def is_displayed(self): return True
        def is_enabled(self): return True
        def get_attribute(self, key): return "button" if key == "type" else None
        def click(self):
            self.clicks += 1
            raise RuntimeError("synthetic timeout after click")
    class DownloadDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings/edit/123/media")
            self.button = Button()
        def execute_script(self, script, ref): return {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
        def find_elements(self, by, name): return [self.button]
        def execute_cdp_cmd(self, method, settings):
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": self.current_window_handle, "type": "page", "browserContextId": "synthetic-context"}}
    driver = DownloadDriver()
    hub = ReadOnlyHub(driver)
    hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "one"}
    with pytest.raises(DownloadOutcomeUnknown) as caught:
        hub.download_photo_pack("RXCU12345", "123", tmp_path / "downloads", timeout=.1, poll_interval=.01)
    assert driver.button.clicks == 1
    assert __import__("pathlib").Path(caught.value.directory).is_dir()


def test_opens_only_the_exact_result_editor_and_verifies_reference():
    pytest.importorskip("selenium")
    class Element:
        def __init__(self, text="", children=None, href=None, action=None):
            self.text, self.children, self.href, self.action, self.clicks = text, children or {}, href, action, 0
        def is_displayed(self): return True
        def is_enabled(self): return True
        def find_elements(self, by, value): return self.children.get(value, [])
        def get_attribute(self, key):
            return {"href": self.href, "aria-controls": "menu-1", "aria-expanded": "true", "id": "menu-1"}.get(key)
        def click(self):
            self.clicks += 1
            if self.action: self.action()
    class EditorDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings")
            self.paragraph = Element("Ref: RXCU-12345")
            self.menu_open = False
            self.combo = Element(action=lambda: setattr(self, "menu_open", True))
            self.row = Element(children={"p": [self.paragraph], '[role="combobox"]': [self.combo]})
            self.edit = Element("Edit Listing", href="https://hub.remax.co.za/app/listings/edit/123", action=self.open_tab)
            self.archive = Element("Archive Listing", href="https://hub.remax.co.za/app/listings/archive/123")
            self.option = Element(children={"a": [self.edit, self.archive]})
            self.menu = Element(children={'[role="option"]': [self.option]})
        def open_tab(self): self.window_handles.append("two")
        def window(self, handle):
            self.current_window_handle = handle
            self.current_url = "https://hub.remax.co.za/app/listings/edit/123" if handle == "two" else "https://hub.remax.co.za/app/listings"
        def find_elements(self, by, value):
            return [self.row] if value == '[role="row"]' else ([self.menu] if self.menu_open and value == '[role="listbox"]' else [])
        def execute_script(self, script, *args):
            from quick_relist.hub_readonly import SOURCE_CONTEXT_SCRIPT
            if script == "return document.documentElement;": return self.row
            if script == SOURCE_CONTEXT_SCRIPT:
                return args == (self.row, self.row, "RXCU12345")
            if "row.isConnected" in script:
                return args == (self.row, self.combo, self.menu, self.edit, "RXCU12345")
            assert args == ("RXCU12345",)
            return {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
    driver = EditorDriver()
    result = ReadOnlyHub(driver).open_listing_editor("rxcu-12345", timeout=.2, poll_interval=.01)
    assert result["row_link_verified"] and result["editor_reference_matches"]
    assert result["account_verified"] is False and result["listing_id"] == "123"
    assert driver.combo.clicks == driver.edit.clicks == 1
    assert driver.archive.clicks == 0


def test_duplicate_matching_rows_stop_before_select():
    pytest.importorskip("selenium")
    class P:
        text = "Ref: RXCU-12345"
        def is_displayed(self): return True
    class Row:
        def is_displayed(self): return True
        def find_elements(self, by, value): return [P()] if value == "p" else []
    class DuplicateDriver(Driver):
        def find_elements(self, by, value): return [Row(), Row()]
    driver = DuplicateDriver("https://hub.remax.co.za/app/listings")
    with pytest.raises(ValueError, match="unique exact"):
        ReadOnlyHub(driver).open_listing_editor("RXCU12345", timeout=.1, poll_interval=.01)


def test_download_requires_row_binding_before_any_click_or_directory(tmp_path):
    pytest.importorskip("selenium")
    driver = Driver("https://hub.remax.co.za/app/listings/edit/123/media")
    with pytest.raises(ValueError, match="exact matching editor"):
        ReadOnlyHub(driver).download_photo_pack("RXCU12345", "123", tmp_path / "downloads", timeout=.1, poll_interval=.01)
    assert not (tmp_path / "downloads").exists() and not driver.calls


@pytest.mark.parametrize("mode", ["duplicate_buttons", "cdp_failure", "wrong_route"])
def test_download_refuses_unsafe_preconditions_before_click(tmp_path, mode):
    pytest.importorskip("selenium")
    class Button:
        text = "Download photo pack"
        clicks = 0
        def is_displayed(self): return True
        def is_enabled(self): return True
        def get_attribute(self, key): return "button"
        def click(self): self.clicks += 1
    class GuardDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings/edit/123" + ("" if mode == "wrong_route" else "/media"))
            self.button = Button()
        def execute_script(self, script, ref): return {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
        def find_elements(self, by, name): return [self.button, self.button] if mode == "duplicate_buttons" else [self.button]
        def execute_cdp_cmd(self, method, settings):
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": self.current_window_handle, "type": "page", "browserContextId": "synthetic-context"}}
            if mode == "cdp_failure": raise RuntimeError("synthetic CDP unavailable")
    driver = GuardDriver()
    hub = ReadOnlyHub(driver)
    hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "one"}
    with pytest.raises((ValueError, RuntimeError)):
        hub.download_photo_pack("RXCU12345", "123", tmp_path / "downloads", timeout=.1, poll_interval=.01)
    assert driver.button.clicks == 0


def test_incomplete_download_is_preserved_and_not_retried(tmp_path):
    pytest.importorskip("selenium")
    from quick_relist.hub_readonly import DownloadOutcomeUnknown
    class Button:
        text = "Download photo pack"
        clicks = 0
        def __init__(self, driver): self.driver = driver
        def is_displayed(self): return True
        def is_enabled(self): return True
        def get_attribute(self, key): return "button"
        def click(self):
            self.clicks += 1
            (self.driver.path / "synthetic.zip.crdownload").write_bytes(b"partial")
    class PartialDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings/edit/123/media")
            self.button = Button(self)
        def execute_script(self, script, ref): return {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
        def find_elements(self, by, name): return [self.button]
        def execute_cdp_cmd(self, method, settings):
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": self.current_window_handle, "type": "page", "browserContextId": "synthetic-context"}}
            from pathlib import Path
            self.path = Path(settings["downloadPath"])
    driver = PartialDriver()
    hub = ReadOnlyHub(driver)
    hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "one"}
    with pytest.raises(DownloadOutcomeUnknown) as caught:
        hub.download_photo_pack("RXCU12345", "123", tmp_path / "downloads", timeout=.05, poll_interval=.01)
    from pathlib import Path
    assert (Path(caught.value.directory) / "synthetic.zip.crdownload").read_bytes() == b"partial"
    assert driver.button.clicks == 1


def test_previous_attempt_marker_blocks_retry_across_new_hub_session(tmp_path):
    import json
    pytest.importorskip("selenium")
    from quick_relist.hub_readonly import PreviousDownloadExists
    root = tmp_path / "downloads"
    root.mkdir(mode=0o700)
    marker = root / "download-review-required.json"
    marker.write_text(json.dumps({"version": 1, "directory": "previous", "state": "attempted"}))
    class Button:
        text = "Download photo pack"
        clicks = 0
        def is_displayed(self): return True
        def is_enabled(self): return True
        def get_attribute(self, key): return "button"
        def click(self): self.clicks += 1
    class ExistingDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings/edit/123/media")
            self.button = Button()
        def execute_script(self, script, ref): return {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
        def find_elements(self, by, name): return [self.button]
    driver = ExistingDriver()
    hub = ReadOnlyHub(driver)
    hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "one"}
    with pytest.raises(PreviousDownloadExists):
        hub.download_photo_pack("RXCU12345", "123", root, timeout=.1, poll_interval=.01)
    assert driver.button.clicks == 0


def test_unrelated_menu_cannot_supply_editor_link():
    pytest.importorskip("selenium")
    class Paragraph:
        text = "Ref: RXCU-12345"
        def is_displayed(self): return True
    class Combo:
        clicks = 0
        def is_displayed(self): return True
        def is_enabled(self): return True
        def click(self): self.clicks += 1
        def get_attribute(self, key): return {"aria-controls": "correct-menu", "aria-expanded": "true"}.get(key)
    class Row:
        def __init__(self, combo): self.combo = combo
        def is_displayed(self): return True
        def find_elements(self, by, value): return [Paragraph()] if value == "p" else [self.combo]
    class StaleMenu:
        def is_displayed(self): return True
        def get_attribute(self, key): return "unrelated-menu"
        def find_elements(self, by, value): pytest.fail("unrelated menu must never be inspected")
    class StaleDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings")
            self.combo, self.menu = Combo(), StaleMenu()
            self.row = Row(self.combo)
        def find_elements(self, by, value):
            if value == '[role="row"]': return [self.row]
            if value == '[role="listbox"]': return [self.menu] if self.combo.clicks else []
            return []
    driver = StaleDriver()
    with pytest.raises(ValueError, match="unique Edit Listing"):
        ReadOnlyHub(driver).open_listing_editor("RXCU12345", timeout=.05, poll_interval=.01)
    assert driver.combo.clicks == 1 and driver.window_handles == ["one"]


def test_failed_browser_close_keeps_handle_and_bindings():
    class BrokenClose(Driver):
        def quit(self): raise RuntimeError("SYNTHETIC_PRIVATE_DRIVER_DETAILS")
    driver = BrokenClose("https://hub.remax.co.za/app/listings")
    hub = ReadOnlyHub(driver)
    hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "one"}
    with pytest.raises(RuntimeError, match="could not be confirmed closed") as caught:
        hub.close()
    assert "SYNTHETIC_PRIVATE" not in str(caught.value)
    assert hub.driver is driver and hub.editor_bindings


def test_successful_browser_close_clears_stale_bindings():
    driver = Driver("https://hub.remax.co.za/app/listings")
    hub = ReadOnlyHub(driver)
    hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "one"}
    hub.close()
    assert hub.driver is None and hub.editor_bindings == {}
    assert driver.calls == ["quit"]


@pytest.mark.parametrize("mode,verified", [("matched", True), ("cleanup_failed", False), ("missing_event", False), ("wrong_size", False)])
def test_completed_guid_download_is_assigned_only_with_matching_event_evidence(tmp_path, monkeypatch, mode, verified):
    import zipfile
    from pathlib import Path
    from quick_relist import photo_downloads
    from quick_relist.photo_downloads import DownloadCompletion
    pytest.importorskip("selenium")
    guid = "12345678-1234-1234-1234-123456789abc"
    class Button:
        text = "DOWNLOAD PHOTO PACK"
        clicks = 0
        def __init__(self, driver): self.driver = driver
        def is_displayed(self): return True
        def is_enabled(self): return True
        def get_attribute(self, key): return "button" if key == "type" else None
        def click(self):
            self.clicks += 1
            with zipfile.ZipFile(self.driver.folder / guid, "w") as archive:
                archive.writestr("photos/01.jpg", b"\xff\xd8\xffsynthetic\xff\xd9")
    class EventDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings/edit/123/media")
            self.button = Button(self)
        def execute_script(self, *args): return {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
        def find_elements(self, *args): return [self.button]
    class Events:
        supported = True
        failed = False
        def __init__(self, driver, directory, ref, **options):
            self.driver, self.directory = driver, directory
            driver.folder = directory
            assert ref == "RXCU12345"
        def __enter__(self): return self
        def __exit__(self, *args): self.failed = mode == "cleanup_failed"
        def arm(self): return True
        def poll(self):
            path = self.directory / guid
            if mode == "missing_event" or not path.exists(): return None
            return DownloadCompletion(guid, path.stat().st_size + (1 if mode == "wrong_size" else 0))
    monkeypatch.setattr(photo_downloads, "PhotoDownloadEvents", Events)
    driver = EventDriver()
    hub = ReadOnlyHub(driver)
    hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "one"}
    result = hub.download_photo_pack("RXCU12345", "123", tmp_path / "downloads", timeout=.08, poll_interval=.01)
    assert result["attribution_verified"] is verified
    assert Path(result["candidate_path"]).is_file()
    assert result["complete"] is False and driver.button.clicks == 1
    assert (tmp_path / "downloads" / "download-review-required.json").exists()
    if verified:
        assert result["candidate_path"].endswith(".zip")
        assert result["download_evidence"]["rxcu"] == "RXCU12345"
        assert result["download_evidence"]["account_verified"] is False
    else:
        assert "download_evidence" not in result


def test_fallback_refuses_unverified_download_context_before_click(tmp_path):
    pytest.importorskip("selenium")
    class Button:
        text = "DOWNLOAD PHOTO PACK"
        clicks = 0
        def is_displayed(self): return True
        def is_enabled(self): return True
        def get_attribute(self, key): return "button"
        def click(self): self.clicks += 1
    class ContextDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings/edit/123/media")
            self.button = Button()
        def execute_script(self, *args): return {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
        def find_elements(self, *args): return [self.button]
        def execute_cdp_cmd(self, method, params):
            assert method == "Target.getTargetInfo"
            return {"targetInfo": {"type": "page", "targetId": "wrong-tab", "browserContextId": "context"}}
    driver = ContextDriver()
    hub = ReadOnlyHub(driver)
    hub.editor_bindings["RXCU12345"] = {"listing_id": "123", "handle": "one"}
    with pytest.raises(RuntimeError, match="no click"):
        hub.download_photo_pack("RXCU12345", "123", tmp_path / "downloads", timeout=.1, poll_interval=.01)
    assert driver.button.clicks == 0


@pytest.mark.parametrize("replacement_size", ["same", "different"])
def test_completed_guid_replaced_during_final_identity_probe_is_not_attributed(tmp_path, replacement_size):
    import zipfile
    from types import SimpleNamespace
    from quick_relist.hub_readonly import DownloadOutcomeUnknown
    from quick_relist.photo_downloads import DownloadCompletion

    guid = "12345678-1234-1234-1234-123456789abc"
    source = tmp_path / guid
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("synthetic.txt", b"first")
    completed_size = source.stat().st_size
    url = "https://hub.remax.co.za/app/listings/edit/123/media"
    driver = SimpleNamespace(current_window_handle="one", window_handles=["one"], current_url=url)
    hub = ReadOnlyHub(driver)
    replacement = tmp_path.parent / (tmp_path.name + "-replacement.zip")
    with zipfile.ZipFile(replacement, "w") as archive:
        archive.writestr("synthetic.txt", b"other" if replacement_size == "same" else b"different synthetic bytes")
    if replacement_size == "same":
        assert replacement.stat().st_size == completed_size

    def replace_after_validation(*args):
        replacement.replace(source)

    hub.verify_editor_reference = replace_after_validation
    completion = DownloadCompletion(guid, completed_size)
    events = SimpleNamespace(poll=lambda: completion, failed=False)
    with pytest.raises(DownloadOutcomeUnknown):
        hub._wait_photo_pack(tmp_path, "RXCU12345", "123", "one", ("one",), url, events, 1, .01)
    assert source.is_file()
    assert not (tmp_path / ("photo-pack-" + guid + ".zip")).exists()


def test_open_uses_new_private_disposable_profile_and_cleans_it_only_after_quit(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from selenium import webdriver
    from quick_relist import hub_readonly
    launched = []
    class ManagedDriver(Driver):
        def get(self, url): self.current_url = url
    def chrome(*, options):
        launched.append(options)
        return ManagedDriver("about:blank")
    monkeypatch.setattr(webdriver, "Chrome", chrome)
    monkeypatch.setattr(hub_readonly.tempfile, "gettempdir", lambda: str(tmp_path))
    hub = ReadOnlyHub()
    hub.open()
    assert hub.driver.page_load_timeout == 30
    assert launched[0].page_load_strategy == 'eager'
    assert launched[0].unhandled_prompt_behavior == 'ignore'
    profile = hub._profile_path
    assert profile.is_dir() and profile.parent == tmp_path
    assert "--incognito" not in launched[0].arguments
    assert "--user-data-dir=" + str(profile) in launched[0].arguments
    assert launched[0].experimental_options["prefs"]["profile.password_manager_enabled"] is False
    assert hub._managed_default_driver is hub.driver
    if os.name != "nt": assert profile.stat().st_mode & 0o077 == 0
    (profile / "synthetic-session").write_bytes(b"synthetic")
    hub.close()
    assert not profile.exists() and hub._profile_path is None


def test_failed_quit_preserves_disposable_profile_for_manual_recovery(tmp_path, monkeypatch):
    from selenium import webdriver
    from quick_relist import hub_readonly
    class ManagedDriver(Driver):
        def get(self, url): self.current_url = url
        def quit(self): raise RuntimeError("synthetic uncertain close")
    monkeypatch.setattr(webdriver, "Chrome", lambda **kw: ManagedDriver("about:blank"))
    monkeypatch.setattr(hub_readonly.tempfile, "gettempdir", lambda: str(tmp_path))
    hub = ReadOnlyHub()
    hub.open()
    profile = hub._profile_path
    with pytest.raises(RuntimeError, match="could not be confirmed closed"):
        hub.close()
    assert profile.exists() and hub.driver is not None


@pytest.mark.parametrize("failure", ["permission", "missing_child"])
def test_profile_cleanup_failure_retains_owned_path_and_can_retry(tmp_path, monkeypatch, failure):
    from quick_relist import hub_readonly
    profile = tmp_path / "synthetic-profile"
    profile.mkdir(mode=0o700)
    retained = profile / "synthetic-session"
    retained.write_bytes(b"synthetic")
    info = profile.stat()
    hub = ReadOnlyHub(Driver("about:blank"))
    hub._profile_path = profile
    hub._profile_identity = (info.st_dev, info.st_ino)
    real_remove = hub_readonly.shutil.rmtree

    def fail_remove(path):
        if failure == "missing_child":
            raise FileNotFoundError(2, "Synthetic child disappeared", str(profile / "missing-child"))
        raise PermissionError("Synthetic locked file")

    monkeypatch.setattr(hub_readonly.shutil, "rmtree", fail_remove)
    with pytest.raises(RuntimeError, match="could not be removed"):
        hub.close()
    assert hub.driver is None and hub._browser_closed
    assert hub._profile_path == profile and retained.is_file()
    monkeypatch.setattr(hub_readonly.shutil, "rmtree", real_remove)
    hub.close()
    assert not profile.exists() and hub._profile_path is None


def test_profile_cleanup_refuses_replaced_directory(tmp_path):
    profile = tmp_path / "synthetic-profile"
    profile.mkdir(mode=0o700)
    info = profile.stat()
    hub = ReadOnlyHub(Driver("about:blank"))
    hub._profile_path = profile
    hub._profile_identity = (info.st_dev, info.st_ino)
    profile.rename(tmp_path / "original-profile")
    profile.mkdir(mode=0o700)
    retained = profile / "unrelated-synthetic-file"
    retained.write_bytes(b"synthetic")
    with pytest.raises(RuntimeError, match="could not be removed"):
        hub.close()
    assert retained.is_file()
    assert hub._profile_path == profile
