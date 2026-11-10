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
def test_search_only_fills_and_submits_unique_observed_controls(prompt):
    pytest.importorskip("selenium")
    class Control:
        def __init__(self, text="", placeholder=""):
            self.text, self.placeholder, self.events = text, placeholder, []
        def is_displayed(self): return True
        def is_enabled(self): return True
        def get_attribute(self, key): return self.placeholder if key == "placeholder" else None
        def clear(self): self.events.append("clear")
        def send_keys(self, value): self.events.append(("type", value))
        def click(self): self.events.append("click")
    class SearchDriver(Driver):
        def __init__(self):
            super().__init__("https://hub.remax.co.za/app/listings")
            self.field = Control(placeholder=prompt)
            self.button = Control(text="Get listings")
        def find_elements(self, by, query):
            return [self.field] if query.startswith("//input") else [self.button]
    driver = SearchDriver()
    result = ReadOnlyHub(driver).search_reference("rxcu12345")
    assert result["searched"] is True
    assert driver.field.events == ["clear", ("type", "RXCU-12345")]
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


def test_photo_pack_download_is_single_click_private_and_partial(tmp_path):
    import zipfile
    pytest.importorskip("selenium")
    class Button:
        text = "Download photo pack"
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
        def execute_cdp_cmd(self, method, settings): pass
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
