"""Synthetic fixtures only. Never read a real Hub account or listing."""
from copy import deepcopy
import json
import shutil

import pytest

from quick_relist.capture import (
    CaptureReadError, CaptureSafetyError, EDITOR_PAGES, PartialHubCapture,
    READ_PAGE_SCRIPT, parse_editor_url, verify_capture_integrity,
)

BASE = "https://hub.remax.co.za/app/listings/edit/synthetic-123"


def observation(url=BASE, **updates):
    result = {"href": url, "ready_state": "complete", "fields": [], "rich_text": [],
              "dialogs": [], "sections": [], "lists": [], "tables": [], "media": [],
              "editor_links": [], "discovery": [], "limitations": []}
    result.update(updates)
    return result


def field(name="synthetic_field", value="Synthetic value", **updates):
    result = {"name": name, "id": name, "label": name.replace("_", " "),
              "tag": "input", "type": "text", "value": value, "dom_order": 0,
              "checked": None, "selected_options": None}
    result.update(updates)
    return result


class Driver:
    """Only read and navigation calls exist. Writes cannot pass accidentally."""
    def __init__(self, pages=None, url=BASE):
        self.current_url = url
        self.current_window_handle = "selected"
        self.window_handles = ["selected", "unrelated"]
        self.pages = pages or {url: observation(url)}
        self.calls = []
        self.after_read = None
        self.after_navigation = None
        self.read_error = None

    def execute_script(self, script):
        assert script == READ_PAGE_SCRIPT
        self.calls.append(("read", self.current_url))
        if self.read_error:
            raise self.read_error
        raw = deepcopy(self.pages[self.current_url])
        if self.after_read:
            self.after_read(self)
        return raw

    def get(self, url):
        assert url in self.pages, "Unobserved navigation"
        self.calls.append(("navigate", url))
        self.current_url = url
        if self.after_navigation:
            self.after_navigation(self)


def test_current_page_is_partial_without_snapshot_or_identity_claims():
    result = PartialHubCapture(Driver()).capture_current_page()
    assert result["complete"] is False
    assert result["live_ready"] is False
    assert result["listing_id"] == "synthetic-123"
    assert result["identity"] == {"route_verified": True, "reference_verified": False,
                                  "account_verified": False, "page_binding_verified": False}
    assert result["coverage"]["observed_pages"] == ["details"]
    assert result["coverage"]["missing_pages"] == ["deal_particulars", "media", "portals"]
    assert result["coverage"]["exhaustive"] is False
    assert verify_capture_integrity(result)
    assert {"partial_not_backup", "nested_dialogs_unverified", "related_records_unverified", "media_unverified", "editor_pages_missing"} <= {x["code"] for x in result["blindspots"]}


@pytest.mark.parametrize("url", [
    "http://hub.remax.co.za/app/listings/edit/synthetic-123",
    "https://hub.remax.co.za.evil.example/app/listings/edit/synthetic-123",
    "https://evil.example/app/listings/edit/synthetic-123",
    "https://hub.remax.co.za:444/app/listings/edit/synthetic-123",
    "https://name:password@hub.remax.co.za/app/listings/edit/synthetic-123",
    "https://hub.remax.co.za/app/listings/edit/synthetic-123?token=fake",
    "https://hub.remax.co.za/app/listings/edit/synthetic-123?",
    "https://hub.remax.co.za/app/listings/edit/synthetic-123#secret",
    "https://hub.remax.co.za/app/listings/edit/synthetic-123#",
    "https://hub.remax.co.za/app/listings/edit/synthetic-123/archive",
    "https://hub.remax.co.za/app/listings/archive/synthetic-123",
    "https://hub.remax.co.za/app/listings/edit/synthetic%2F123",
    "https://hub.remax.co.za/app/listings/edit/../synthetic-123",
    "https://hub.remax.co.za\\@evil.example/app/listings/edit/synthetic-123",
    "\nhttps://hub.remax.co.za/app/listings/edit/synthetic-123",
    "https://hub.remax.co.za/app/listings/edit/synthetic-123\n",
    "https://hub.remax.co.za/app/listings",
    "https://hub.remax.co.za/app/listings/add/sale", None, 42,
])
def test_unsafe_routes_refused_without_dom_read(url):
    driver = Driver(url=url)
    with pytest.raises(CaptureSafetyError):
        PartialHubCapture(driver).capture_current_page()
    assert not driver.calls


@pytest.mark.parametrize("suffix,key", [("", "details"), ("/", "details"),
                                          ("/particulars", "details"),
                                          ("/particulars/", "details"),
                                          ("/deal_particulars", "deal_particulars"),
                                          ("/media", "media"), ("/portals", "portals")])
def test_only_observed_route_families_are_accepted(suffix, key):
    parsed = parse_editor_url(BASE + suffix)
    assert parsed["listing_id"] == "synthetic-123"
    assert parsed["page_key"] == key
    assert parsed["page_path"] == "/app/listings/edit/synthetic-123" + suffix


def test_return_to_particulars_remains_same_listing_and_records_real_path():
    from quick_relist.hub_readonly import ReadOnlyHub
    details_url = BASE + "/particulars"
    driver = Driver(pages={BASE: observation(), details_url: observation(details_url)})
    capture = PartialHubCapture(driver)
    capture.capture_current_page()
    driver.current_url = details_url
    result = capture.capture_current_page()
    assert result["listing_id"] == "synthetic-123"
    assert result["pages"][-1]["page_key"] == "details"
    assert result["pages"][-1]["page_path"].endswith("/particulars")
    assert verify_capture_integrity(result)
    assert ReadOnlyHub._is_editor_url(details_url)
    assert not ReadOnlyHub._is_editor_url(details_url + "?listing=another")


def test_wrong_expected_listing_rejected_before_read():
    driver = Driver()
    with pytest.raises(CaptureSafetyError, match="different listing"):
        PartialHubCapture(driver).capture_current_page("another-synthetic")
    assert not driver.calls


@pytest.mark.parametrize("expected", ["../evil", 123, "", "has space"])
def test_invalid_expected_listing_id_refused(expected):
    driver = Driver()
    with pytest.raises(CaptureSafetyError):
        PartialHubCapture(driver).capture_current_page(expected)
    assert not driver.calls


@pytest.mark.parametrize("change", [
    lambda d: setattr(d, "current_window_handle", "unrelated"),
    lambda d: d.window_handles.append("new"),
    lambda d: d.window_handles.remove("unrelated"),
    lambda d: setattr(d, "current_url", BASE + "/media"),
    lambda d: setattr(d, "current_url", BASE.replace("synthetic-123", "different")),
])
def test_tab_or_route_changes_during_read_are_rejected(change):
    driver = Driver()
    driver.after_read = change
    capture = PartialHubCapture(driver)
    with pytest.raises(CaptureSafetyError):
        capture.capture_current_page()
    assert capture.get_result()["pages"] == []
    assert capture.get_result()["status"] == "stopped"
    assert len(driver.calls) == 1


def test_pinned_session_rejects_later_listing_or_tab_switch():
    driver = Driver()
    capture = PartialHubCapture(driver)
    capture.capture_current_page()
    driver.current_url = BASE.replace("synthetic-123", "another")
    with pytest.raises(CaptureSafetyError, match="different listing"):
        capture.capture_current_page()
    assert len(driver.calls) == 1
    assert len(capture.get_result()["pages"]) == 1


def test_supplying_new_expected_id_cannot_repin_existing_session():
    driver = Driver()
    capture = PartialHubCapture(driver)
    capture.capture_current_page()
    driver.current_url = BASE.replace("synthetic-123", "another")
    with pytest.raises(CaptureSafetyError, match="pinned"):
        capture.capture_current_page("another")
    assert len(driver.calls) == 1


def test_browser_errors_do_not_echo_private_values():
    driver = Driver()
    driver.read_error = RuntimeError("synthetic private value https://secret.example/token")
    capture = PartialHubCapture(driver)
    with pytest.raises(CaptureReadError) as caught:
        capture.capture_current_page()
    assert "private value" not in str(caught.value)
    assert "secret.example" not in str(caught.value)
    assert capture.get_result()["pages"] == []


@pytest.mark.parametrize("raw", [None, [], {}, observation(ready_state="loading"),
                                 observation(fields="unexpected"), observation(fields=["bad"]),
                                 observation(limitations=[{}]), observation(fields=[{"value": float("nan")}])])
def test_malformed_or_loading_observation_fails_closed(raw):
    capture = PartialHubCapture(Driver({BASE: raw}))
    with pytest.raises((CaptureReadError, CaptureSafetyError)):
        capture.capture_current_page()
    assert capture.get_result()["pages"] == []


def test_dom_reported_location_must_match_driver_location():
    driver = Driver({BASE: observation(BASE + "/media")})
    with pytest.raises(CaptureSafetyError):
        PartialHubCapture(driver).capture_current_page()


def test_sensitive_controls_are_removed_again_at_python_boundary():
    unsafe = [field("password"), field("access_token"), field("otp"), field("card_number"),
              field("ordinary", type="hidden"), field("ordinary", type="file")]
    raw = observation(fields=[field("street_number", "12")] + unsafe,
                      dialogs=[{"title": "Synthetic room options", "fields": [field("secret_key")], "rich_text": []}])
    result = PartialHubCapture(Driver({BASE: raw})).capture_current_page()
    assert result["pages"][0]["fields"] == [field("street_number", "12")]
    assert result["pages"][0]["dialogs"][0]["fields"] == []
    assert "sensitive_control_omitted" in result["limitations"]


def test_repeated_dialog_observations_preserve_values_order_and_time():
    driver = Driver()
    capture = PartialHubCapture(driver)
    capture.capture_current_page()
    driver.pages[BASE] = observation(dialogs=[{"title": "Synthetic room options", "fields": [field("room_option", "Option A")], "rich_text": []}])
    result = capture.capture_current_page()
    assert len(result["pages"]) == 2
    assert result["pages"][0]["dialogs"] == []
    assert result["pages"][1]["dialogs"][0]["fields"][0]["value"] == "Option A"
    assert [p["observation_number"] for p in result["pages"]] == [1, 2]
    assert all(p["observed_at"] for p in result["pages"])
    assert result["complete"] is False
    assert verify_capture_integrity(result)


def test_media_observations_preserve_order_captions_and_known_field_evidence():
    fields = [field("marketing_header"), field("brochure", label="Brochure Description"),
              field("virtualtour_url"), field("videotour_url"), field("p24_youtube_video_id"),
              field("matterport_video_id"), field("eyespy360_url")]
    media = [{"dom_order": 0, "caption": "Synthetic second photo", "kind_hint": "img"},
             {"dom_order": 1, "caption": "Synthetic first photo", "kind_hint": "img"},
             {"dom_order": 2, "caption": "Synthetic plan", "kind_hint": "floorplan"}]
    raw = observation(BASE + "/media", fields=fields, media=media,
                      rich_text=[{"text": "Synthetic rich description", "sanitized_html": "<p><b>Synthetic</b></p>"}])
    result = PartialHubCapture(Driver({BASE + "/media": raw}, BASE + "/media")).capture_current_page()
    assert result["pages"][0]["media"] == media
    assert all(result["coverage"]["media_fields"].values())
    assert result["coverage"]["media_fields"]["rich_text_description"][0]["mapping_verified"] is False
    assert result["complete"] is False


def test_generic_sections_keep_parties_documents_notes_and_rooms_as_hints():
    raw = observation(sections=[{"title": name, "text": "Synthetic record", "dom_order": i}
                                for i, name in enumerate(("Parties", "Documents", "Notes", "Rooms", "Floor plans"))])
    result = PartialHubCapture(Driver({BASE: raw})).capture_current_page()
    assert all(result["coverage"]["category_hints"].values())
    assert all(x["mapping_verified"] is False for values in result["coverage"]["category_hints"].values() for x in values)


def test_multi_page_capture_uses_only_observed_same_listing_links():
    links = [{"url": BASE + suffix, "text": suffix} for suffix in ("/deal_particulars", "/media", "/portals")]
    unsafe = [{"url": "https://evil.example/app/listings/edit/synthetic-123/media"},
              {"url": BASE.replace("synthetic-123", "another") + "/media"},
              {"url": BASE + "/media?token=fake"}]
    pages = {BASE: observation(editor_links=links + unsafe)}
    pages.update({link["url"]: observation(link["url"], editor_links=[{"url": BASE}]) for link in links})
    driver = Driver(pages)
    result = PartialHubCapture(driver).capture_listing("synthetic-123")
    assert [call[1] for call in driver.calls if call[0] == "navigate"] == [link["url"] for link in links]
    assert result["coverage"]["observed_pages"] == list(EDITOR_PAGES)
    assert result["coverage"]["missing_pages"] == []
    assert result["pages"][0]["rejected_editor_link_count"] == 3
    assert result["complete"] is False
    assert result["live_ready"] is False
    assert result["coverage"]["exhaustive"] is False
    assert driver.current_url == BASE + "/portals"
    assert verify_capture_integrity(result)


def test_missing_editor_links_are_not_guessed():
    driver = Driver()
    result = PartialHubCapture(driver).capture_listing("synthetic-123")
    assert driver.calls == [("read", BASE)]
    assert result["coverage"]["missing_pages"] == ["deal_particulars", "media", "portals"]


def test_open_dialog_is_captured_but_not_closed_or_navigated_away_from():
    raw = observation(dialogs=[{"title": "Synthetic room options", "fields": [field()], "rich_text": []}],
                      editor_links=[{"url": BASE + "/media"}])
    driver = Driver({BASE: raw})
    result = PartialHubCapture(driver).capture_listing("synthetic-123")
    assert driver.calls == [("read", BASE)]
    assert "navigation_stopped_for_open_dialog" in result["limitations"]
    assert verify_capture_integrity(result)


@pytest.mark.parametrize("redirect", [BASE.replace("synthetic-123", "another") + "/media", BASE + "/portals"])
def test_navigation_redirect_stops_without_reading_or_silent_restore(redirect):
    driver = Driver({BASE: observation(editor_links=[{"url": BASE + "/media"}]), BASE + "/media": observation(BASE + "/media")})
    driver.after_navigation = lambda d: setattr(d, "current_url", redirect)
    capture = PartialHubCapture(driver)
    with pytest.raises(CaptureSafetyError):
        capture.capture_listing("synthetic-123")
    assert driver.calls == [("read", BASE), ("navigate", BASE + "/media")]
    assert driver.current_url == redirect
    assert capture.get_result()["status"] == "stopped"
    assert len(capture.get_result()["pages"]) == 1


def test_results_are_detached_and_integrity_detects_accidental_edits():
    capture = PartialHubCapture(Driver({BASE: observation(fields=[field()])}))
    result = capture.capture_current_page()
    result["pages"][0]["fields"][0]["value"] = "Edited synthetic value"
    assert not verify_capture_integrity(result)
    assert capture.get_result()["pages"][0]["fields"][0]["value"] == "Synthetic value"
    assert verify_capture_integrity(capture.get_result())
    result = capture.get_result()
    result["complete"] = True
    assert not verify_capture_integrity(result)


def test_empty_result_is_explicitly_partial():
    result = PartialHubCapture(None).get_result()
    assert result["complete"] is False
    assert result["identity"]["route_verified"] is False
    assert result["coverage"]["missing_pages"] == list(EDITOR_PAGES)
    assert verify_capture_integrity(result)


def test_script_has_no_application_mutations_or_secret_storage_reads():
    # This is a useful regression check, not a substitute for DOM tests/review.
    for forbidden in (".click(", ".submit(", ".dispatchEvent(", ".setAttribute(",
                      "localStorage", "sessionStorage", "document.cookie", "fetch(",
                      "XMLHttpRequest", "window.open(", "innerHTML ="):
        assert forbidden not in READ_PAGE_SCRIPT


@pytest.fixture
def synthetic_browser():
    """Optional real DOM test; all requests are fulfilled from synthetic HTML.

    No browser download, real Hub navigation, credentials or user profile occurs.
    Unit tests remain runnable with only the declared pytest dependency.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    executable = shutil.which("chromium") or shutil.which("google-chrome")
    if not executable:
        pytest.skip("No preinstalled browser for optional synthetic DOM validation")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox"])
        except Exception:
            pytest.skip("Preinstalled browser unavailable for optional synthetic DOM validation")
        context = browser.new_context()
        # Every URL, including image subresources, is synthetic and intercepted.
        html = '''<!doctype html><html><body><nav>Unrelated account menu</nav><main>
        <section><h1>Media</h1>
        <label for="marketing_header">Marketing header</label><input id="marketing_header" name="marketing_header" value="Synthetic marketing">
        <label>Brochure Description<textarea name="brochure">Synthetic brochure</textarea></label>
        <input type="password" value="SHOULD_NOT_APPEAR_PASSWORD">
        <input type="hidden" name="hidden_value" value="SHOULD_NOT_APPEAR_HIDDEN">
        <input name="otp" value="SHOULD_NOT_APPEAR_OTP">
        <input name="access_token" value="SHOULD_NOT_APPEAR_TOKEN">
        <div id="account_secret"><div><p>SHOULD_NOT_APPEAR_ANCESTOR</p><input name="ordinary_nested" value="SHOULD_NOT_APPEAR_NESTED_FIELD"></div></div>
        <div aria-label="Access token"><div><p>SHOULD_NOT_APPEAR_ARIA_ANCESTOR</p></div></div>
        <div aria-hidden="true"><input name="hidden_aria" value="SHOULD_NOT_APPEAR_ARIA_HIDDEN"></div>
        <label for="labelled_secret">Password</label><input id="labelled_secret" value="SHOULD_NOT_APPEAR_LABELLED">
        <p>Open https://example.invalid/path?token=SHOULD_NOT_APPEAR_TEXT#fragment</p>
        <p>Also example.invalid/path?token=SHOULD_NOT_APPEAR_BARE_DOMAIN</p>
        <input type="file" name="document_upload">
        <div class="rdw-editor-main" contenteditable="true"><p>A <b>synthetic</b> description</p><span aria-label="secret"><b>SHOULD_NOT_APPEAR_RICH_ARIA</b></span><a href="https://example.invalid/?token=SHOULD_NOT_APPEAR_HTML">Link text</a><script>var secret='SHOULD_NOT_APPEAR_SCRIPT';</script></div>
        <input name="virtualtour_url" value="https://example.invalid/tour?token=SHOULD_NOT_APPEAR_FIELD"><input name="videotour_url" value="tour?token=SHOULD_NOT_APPEAR_RELATIVE">
        <select name="synthetic_select" multiple><option value="a" selected>Alpha</option><option value="b" selected>Beta</option></select>
        <input name="synthetic_checkbox" type="checkbox" checked>
        <figure><img src="https://example.invalid/picture-a.jpg?token=SHOULD_NOT_APPEAR_URL" alt="Synthetic photo"><figcaption>Synthetic photo caption</figcaption></figure>
        </section><section><h2>Floor plans</h2><figure><img src="https://example.invalid/plan.jpg" alt="Floorplan"><figcaption>Synthetic floorplan caption</figcaption></figure></section>
        <section><h2>Parties</h2><ul><li>Synthetic party</li></ul></section>
        <section><h2>Documents</h2><table><tr><th>File</th></tr><tr><td>Synthetic attachment.pdf</td></tr></table></section>
        <section><h2>Notes</h2><p>Synthetic note only</p></section>
        <a href="/app/listings/edit/synthetic-123">Details</a><a href="/app/listings/edit/synthetic-123/deal_particulars">Deal</a><a href="/app/listings/edit/synthetic-123/portals">Portals</a>
        <button onclick="throw new Error('must not click')">Room Options</button>
        </main><div role="dialog" aria-label="Synthetic bedroom options"><label><input type="checkbox" name="room_option" checked>Built in cupboard</label></div></body></html>'''
        context.route("**/*", lambda route: route.fulfill(status=200, body=html, content_type="text/html"))
        page = context.new_page()
        page.goto(BASE + "/media", wait_until="load")
        yield page
        context.close()
        browser.close()


def test_actual_extractor_reads_synthetic_dom_without_credentials_or_mutation(synthetic_browser):
    page = synthetic_browser
    before = page.content()
    raw = page.evaluate("() => {" + READ_PAGE_SCRIPT + "}")
    assert page.content() == before
    assert raw["href"] == BASE + "/media"
    assert "SHOULD_NOT_APPEAR" not in json.dumps(raw)
    fields = {x["name"]: x for x in raw["fields"]}
    assert fields["marketing_header"]["value"] == "Synthetic marketing"
    assert fields["virtualtour_url"]["value"] == "https://example.invalid/tour"
    assert fields["videotour_url"]["value"] == "tour"
    assert fields["brochure"]["label"].startswith("Brochure Description")
    assert fields["synthetic_select"]["selected_options"] == [{"value": "a", "text": "Alpha"}, {"value": "b", "text": "Beta"}]
    assert fields["synthetic_checkbox"]["checked"] is True
    assert len(raw["rich_text"]) == 1
    assert "<b>synthetic</b>" in raw["rich_text"][0]["sanitized_html"]
    assert raw["dialogs"][0]["fields"][0]["name"] == "room_option"
    assert raw["dialogs"][0]["fields"][0]["checked"] is True
    assert [x["caption"] for x in raw["media"]] == ["Synthetic photo caption", "Synthetic floorplan caption"]
    assert raw["media"][1]["kind_hint"] == "floorplan"
    assert raw["media"][0]["source_ref"]["url"] == "https://example.invalid/picture-a.jpg"
    assert raw["media"][0]["source_ref"]["query_omitted"] is True
    assert "url_credentials_and_queries_omitted" in raw["limitations"]
    assert {x["title"] for x in raw["sections"]} >= {"Parties", "Documents", "Notes", "Floor plans"}
    assert any(x["items"] == ["Synthetic party"] for x in raw["lists"])
    assert any(["Synthetic attachment.pdf"] in x["rows"] for x in raw["tables"])
    assert [x["url"] for x in raw["editor_links"]] == [BASE, BASE + "/deal_particulars", BASE + "/portals"]
    assert any(x["label"] == "Room Options" for x in raw["discovery"])


def test_url_queries_are_redacted_from_plain_field_and_rich_observations():
    raw = observation(fields=[field("virtualtour_url", "https://example.invalid/tour?token=FAKE_SECRET#frag")],
                      sections=[{"title": "Notes", "text": "Open /path?token=FAKE_SECRET and example.invalid/next?token=FAKE_SECRET"}],
                      rich_text=[{"text": "https://user:FAKE_SECRET@example.invalid", "sanitized_html": "<p>https://example.invalid/?token=FAKE_SECRET</p>"}])
    result = PartialHubCapture(Driver({BASE: raw})).capture_current_page()
    assert "FAKE_SECRET" not in json.dumps(result)
    assert result["pages"][0]["fields"][0]["value"] == "https://example.invalid/tour"
    assert "url_credentials_and_queries_omitted" in result["limitations"]


def test_malformed_field_type_is_safe_display_error():
    raw = observation(fields=[field(type={"secret": "synthetic-private"})])
    with pytest.raises(CaptureReadError) as error:
        PartialHubCapture(Driver({BASE: raw})).capture_current_page()
    assert "synthetic-private" not in str(error.value)
