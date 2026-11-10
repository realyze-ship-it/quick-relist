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


def test_editor_inspection_is_explicitly_partial():
    driver = Driver("https://hub.remax.co.za/app/listings/edit/example/media")
    result = ReadOnlyHub(driver).inspect_current_editor_page()
    assert result["complete"] is False
    assert result["fields"][0]["value"] == "example"
    assert len(driver.calls) == 1


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


def test_search_only_fills_and_submits_unique_observed_controls():
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
            self.field = Control(placeholder="Search fusion ref, listing ID or address")
            self.button = Control(text="Get listings")
        def find_elements(self, by, query):
            return [self.field] if query.startswith("//input") else [self.button]
    driver = SearchDriver()
    result = ReadOnlyHub(driver).search_reference("rxcu12345")
    assert result["searched"] is True
    assert driver.field.events == ["clear", ("type", "RXCU12345")]
    assert driver.button.events == ["click"]
    assert "not verify" in result["warning"]
