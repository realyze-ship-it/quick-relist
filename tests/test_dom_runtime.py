r"""Execute the real DOM extractor against synthetic, offline HTML using jsdom.

Optional test dependency only; nothing is added to the app's dependencies. Run:

    npm install --prefix /tmp/quick-relist-dom-runtime \
        --cache /tmp/quick-relist-npm-cache --ignore-scripts --no-audit --no-fund \
        --registry=https://registry.npmjs.org/ jsdom@26.1.0
    NODE_PATH=/tmp/quick-relist-dom-runtime/node_modules \
        python -m pytest tests/test_dom_runtime.py -q

The suite skips explicitly if Node/jsdom is unavailable. jsdom implements DOM
semantics, NOT browser layout. getClientRects is a small, documented visibility
simulation below. These tests cannot verify CSS geometry, the real Hub UI,
browser integration, asynchronous app state, or complete listing capture.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable

import pytest

from quick_relist.capture import READ_PAGE_SCRIPT


BASE = "https://hub.remax.co.za/app/listings/edit/synthetic-123"

# Page scripts and resource loading are disabled. Only our extractor is run.
# jsdom has no layout; the rectangle shim models display:none / hidden and
# closed dialogs. Visibility is still checked by the production script using
# getComputedStyle. aria-hidden deliberately does not affect the rectangle:
# it does not hide a real element geometrically either. The runner awaits
# jsdom's actual load event before extracting; document.readyState is not mocked.
NODE_RUNNER = r"""
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM(input.html, {
  url: input.url,
  pretendToBeVisual: true,
  runScripts: 'outside-only'
});
const w = dom.window;
Object.defineProperty(w.Element.prototype, 'getClientRects', {
  value: function() {
    for (let el = this; el; el = el.parentElement) {
      if (el.hasAttribute('hidden') || w.getComputedStyle(el).display === 'none' ||
          (el.tagName === 'DIALOG' && !el.hasAttribute('open'))) return [];
    }
    return [{x: 0, y: 0, width: 100, height: 20, top: 0, right: 100,
             bottom: 20, left: 0}];
  }
});
for (const update of input.updates || []) {
  const el = w.document.getElementById(update.id);
  if (!el) throw new Error('Missing synthetic fixture control');
  for (const [key, value] of Object.entries(update)) if (key !== 'id') el[key] = value;
}
const denied = () => { throw new Error('Extractor attempted a prohibited side effect or secret read'); };
for (const key of ['localStorage', 'sessionStorage']) {
  Object.defineProperty(w, key, {get: denied});
}
Object.defineProperty(w.document, 'cookie', {get: denied, set: denied});
for (const key of ['fetch', 'XMLHttpRequest', 'WebSocket', 'open']) w[key] = denied;
w.HTMLElement.prototype.click = denied;
w.HTMLFormElement.prototype.submit = denied;
w.HTMLFormElement.prototype.requestSubmit = denied;
const signature = () => JSON.stringify({
  html: w.document.documentElement.outerHTML,
  values: Array.from(w.document.querySelectorAll('input,select,textarea'))
    .map(el => [el.id, el.value, el.checked, el.disabled, el.readOnly]),
  href: w.location.href
});
(async () => {
  if (w.document.readyState !== 'complete') {
    await new Promise((resolve, reject) => {
      const onLoad = () => { clearTimeout(timer); resolve(); };
      const timer = setTimeout(() => {
        w.removeEventListener('load', onLoad);
        reject(new Error('Synthetic document did not finish loading'));
      }, 5000);
      w.addEventListener('load', onLoad, {once: true});
    });
  }
  if (w.document.readyState !== 'complete') throw new Error('Synthetic document is not ready');
  const before = signature();
  const result = w.Function(input.script)(...(input.args || []));
  if (signature() !== before) throw new Error('Extractor changed the synthetic DOM or control state');
  process.stdout.write(JSON.stringify(result));
})().catch(error => {
  process.stderr.write(String(error.stack || error));
  process.exitCode = 1;
}).finally(() => dom.window.close());
"""


@pytest.fixture(scope="module")
def extract() -> Callable[..., dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Optional synthetic DOM runtime unavailable: install Node and jsdom 26.1.0")
    probe = subprocess.run(
        [node, "-e", "require.resolve('jsdom')"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if probe.returncode:
        if "MODULE_NOT_FOUND" in probe.stderr:
            pytest.skip("Optional jsdom unavailable; see test_dom_runtime.py for the /tmp-only setup")
        pytest.fail(f"Node/jsdom availability check failed: {probe.stderr}")

    def run(html: str, *, updates: list[dict] | None = None) -> dict:
        completed = subprocess.run(
            [node, "-e", NODE_RUNNER],
            input=json.dumps({"html": html, "url": BASE,
                              "script": READ_PAGE_SCRIPT, "updates": updates or []}),
            capture_output=True, text=True, timeout=30, check=False,
        )
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result["href"] == BASE
        return result

    return run


def test_extractor_runs_after_real_document_load(extract):
    from quick_relist.capture import _validate_observation

    result = extract('<main><table><tr><td>Synthetic ready document</td></tr></table></main>')
    assert result["ready_state"] == "complete"
    # Validate the untouched extraction, including the production readiness guard.
    _validate_observation(result, BASE)


def test_visible_fields_read_live_values_labels_and_control_state(extract):
    result = extract("""
        <main><fieldset><legend>Property details</legend>
          <label for="title">Marketing title</label><input id="title" value="Old text">
          <label>Notes <textarea id="notes">Initial notes</textarea></label>
          <span id="choice-title">Property type</span>
          <select id="choice" aria-labelledby="choice-title">
            <option value="house">House</option><option value="flat">Flat</option>
          </select>
          <input id="check" type="checkbox" aria-label="Pool" value="yes">
          <input id="radio" type="radio" aria-label="Exclusive mandate" checked>
          <input id="locked" aria-label="Reference" value="SYNTHETIC" readonly disabled>
        </fieldset></main>
    """, updates=[{"id": "title", "value": "Current edited title"},
                  {"id": "notes", "value": "Current edited notes"},
                  {"id": "choice", "value": "flat"}, {"id": "check", "checked": True}])
    fields = {field["id"]: field for field in result["fields"]}
    assert fields["title"]["value"] == "Current edited title"
    assert fields["title"]["label"] == "Marketing title"
    assert fields["notes"]["value"] == "Current edited notes"
    assert fields["notes"]["label"] == "Notes"
    assert fields["choice"]["label"] == "Property type"
    assert fields["choice"]["selected_options"] == [{"value": "flat", "text": "Flat"}]
    assert fields["choice"]["option_count"] == 2
    assert fields["check"]["checked"] is True
    assert fields["radio"]["checked"] is True
    assert fields["locked"]["disabled"] is True
    assert fields["locked"]["read_only"] is True
    assert all(field["section"] == "Property details" for field in fields.values())
    assert [field["dom_order"] for field in fields.values()] == list(range(6))


@pytest.mark.parametrize("wrapper", [
    '<div hidden>{content}</div>',
    '<div style="display:none">{content}</div>',
    '<div style="visibility:hidden">{content}</div>',
    '<div style="visibility:collapse">{content}</div>',
    '<div aria-hidden="true">{content}</div>',
    '<dialog>{content}</dialog>',
])
def test_hidden_controls_text_media_and_rich_text_are_omitted(extract, wrapper):
    concealed = wrapper.format(content="""
        <input id="concealed" value="SYNTHETIC_CONCEALED_VALUE">
        <p>SYNTHETIC_CONCEALED_TEXT</p>
        <div contenteditable="true">SYNTHETIC_CONCEALED_RICH</div>
        <img src="https://example.invalid/concealed.png" alt="SYNTHETIC_CONCEALED_MEDIA">
    """)
    result = extract('<main><section><h2>Visible section</h2><input id="shown" value="Visible">'
                     + concealed + '</section></main>')
    assert [field["id"] for field in result["fields"]] == ["shown"]
    assert "SYNTHETIC_CONCEALED" not in json.dumps(result)
    assert result["rich_text"] == []
    assert result["media"] == []


@pytest.mark.parametrize("context", [
    '<section id="auth_token">{content}</section>',
    '<section name="credential">{content}</section>',
    '<section aria-label="One time code">{content}</section>',
    '<span id="sensitive-heading">Card number</span><section aria-labelledby="sensitive-heading">{content}</section>',
])
def test_sensitive_ancestor_context_is_not_captured(extract, context):
    content = """
      <input id="generic-field" value="SYNTHETIC_SENSITIVE_FIELD">
      <p>SYNTHETIC_SENSITIVE_TEXT</p>
      <div contenteditable="true">SYNTHETIC_SENSITIVE_RICH</div>
      <img src="https://example.invalid/file.png" alt="SYNTHETIC_SENSITIVE_MEDIA">
      <button>SYNTHETIC_SENSITIVE_BUTTON</button>
      <div role="dialog" aria-label="Dialog">SYNTHETIC_SENSITIVE_DIALOG</div>
    """
    result = extract('<main><input id="ordinary" value="Ordinary">'
                     + context.format(content=content) + '</main>')
    assert [field["id"] for field in result["fields"]] == ["ordinary"]
    assert "SYNTHETIC_SENSITIVE" not in json.dumps(result)
    assert result["dialogs"] == []
    assert result["discovery"] == []


def test_sensitive_field_types_and_all_label_forms_are_omitted(extract):
    result = extract("""
      <main>
        <input id="safe" value="Ordinary">
        <input type="hidden" value="SYNTHETIC_SENSITIVE_HIDDEN">
        <input type="password" value="SYNTHETIC_SENSITIVE_PASSWORD">
        <input type="file"><input type="submit" value="SYNTHETIC_SENSITIVE_SUBMIT">
        <input id="generic1" autocomplete="one-time-code" value="SYNTHETIC_SENSITIVE_OTP">
        <input id="generic2" aria-label="Password" value="SYNTHETIC_SENSITIVE_ARIA">
        <span id="label3">PIN</span><input id="generic3" aria-labelledby="label3" value="SYNTHETIC_SENSITIVE_LABELLEDBY">
        <label for="generic4">Card security code</label><input id="generic4" value="SYNTHETIC_SENSITIVE_FOR_LABEL">
        <label>Token<input id="generic5" value="SYNTHETIC_SENSITIVE_NESTED_LABEL"></label>
      </main>
    """)
    assert [field["id"] for field in result["fields"]] == ["safe"]
    assert "SYNTHETIC_SENSITIVE" not in json.dumps(result)


def test_url_queries_fragments_credentials_and_unsafe_schemes_are_redacted(extract):
    result = extract("""
      <main><section><h2>Media</h2>
        <input id="tour" value="https://example.invalid/tour?key=SYNTHETIC_QUERY#SYNTHETIC_FRAGMENT">
        <textarea id="description">Visit https://example.invalid/page?q=SYNTHETIC_TEXT_QUERY#SYNTHETIC_TEXT_FRAGMENT then /local?x=SYNTHETIC_RELATIVE_QUERY#SYNTHETIC_RELATIVE_FRAGMENT</textarea>
        <p>www.example.invalid/page?x=SYNTHETIC_WWW_QUERY#SYNTHETIC_WWW_FRAGMENT</p>
        <input id="credential-url" value="https://SYNTHETIC_USER:SYNTHETIC_PASSWORD@example.invalid/file">
        <input id="unsafe-url" value="javascript:SYNTHETIC_JAVASCRIPT">
        <input id="data-url" value="data:text/plain,SYNTHETIC_DATA">
        <img src="https://example.invalid/photo.jpg?x=SYNTHETIC_IMAGE_QUERY#SYNTHETIC_IMAGE_FRAGMENT" alt="Front view">
        <a href="/app/listings/edit/synthetic-123/media?x=SYNTHETIC_LINK_QUERY">Unsafe query</a>
        <a href="/app/listings/edit/synthetic-123/portals#SYNTHETIC_LINK_FRAGMENT">Unsafe fragment</a>
        <a href="/app/listings/edit/synthetic-123/deal_particulars">Deal particulars</a>
      </section></main>
    """)
    fields = {field["id"]: field for field in result["fields"]}
    assert fields["tour"]["value"] == "https://example.invalid/tour"
    assert fields["description"]["value"] == "Visit https://example.invalid/page then /local"
    assert fields["unsafe-url"]["value"] == "[URL omitted]"
    assert fields["data-url"]["value"] == "[URL omitted]"
    assert result["media"][0]["source_ref"] == {
        "url": "https://example.invalid/photo.jpg", "query_omitted": True, "fragment_omitted": True,
    }
    assert result["editor_links"] == [{"url": BASE + "/deal_particulars", "text": "Deal particulars"}]
    assert "SYNTHETIC_" not in json.dumps(result)
    assert "url_credentials_and_queries_omitted" in result["limitations"]
    assert "unsafe_editor_links_omitted" in result["limitations"]


def test_only_already_open_visible_dialogs_are_read_without_clicking(extract):
    result = extract("""
      <main><input id="main-field" value="Main">
        <button aria-expanded="false" aria-controls="rooms">Room options</button>
      </main>
      <dialog open aria-labelledby="room-title"><h2 id="room-title">Room options</h2>
        <label for="room-count">Bedrooms</label><input id="room-count" value="3">
        <div contenteditable="true"><p>Sunny <strong>rooms</strong></p></div>
      </dialog>
      <dialog aria-label="Closed"><input value="SYNTHETIC_CLOSED_DIALOG"></dialog>
      <div role="dialog" aria-label="Hidden" style="display:none">SYNTHETIC_HIDDEN_DIALOG</div>
    """)
    assert [field["id"] for field in result["fields"]] == ["main-field"]
    assert len(result["dialogs"]) == 1
    dialog = result["dialogs"][0]
    assert dialog["title"] == "Room options"
    assert dialog["fields"][0]["label"] == "Bedrooms"
    assert dialog["fields"][0]["value"] == "3"
    assert dialog["rich_text"][0]["sanitized_html"] == "<p>Sunny <strong>rooms</strong></p>"
    assert result["discovery"][0]["inspected"] is False
    assert result["discovery"][0]["expanded"] == "false"
    assert "SYNTHETIC_" not in json.dumps(result)


@pytest.mark.parametrize("control", [
    '<input id="tour" type="url" value="tour?key=SYNTHETIC_QUERY#SYNTHETIC_FRAGMENT">',
    '<input id="tour_url" value="tour?key=SYNTHETIC_QUERY#SYNTHETIC_FRAGMENT">',
])
def test_url_designated_fields_redact_relative_queries_and_fragments(extract, control):
    result = extract('<main>' + control + '</main>')
    assert len(result["fields"]) == 1
    assert "SYNTHETIC_" not in json.dumps(result)
    assert "?" not in result["fields"][0]["value"]
    assert "#" not in result["fields"][0]["value"]
    assert "url_credentials_and_queries_omitted" in result["limitations"]


def test_rich_text_is_sanitized_and_nested_editor_is_not_duplicated(extract):
    result = extract("""
      <main><section><h2>Description</h2>
        <div id="rich" contenteditable="true" aria-label="Listing description">
          <p style="color:red" onclick="throw Error('must not run')">Bright <strong>home</strong> &amp; garden<br>Two &lt; three</p>
          <div class="rdw-editor-main"><em>Nested editor content</em></div>
          <a href="https://example.invalid/private?x=SYNTHETIC_ATTRIBUTE_QUERY">Tour</a>
          <p>https://example.invalid/visible?x=SYNTHETIC_VISIBLE_QUERY#SYNTHETIC_VISIBLE_FRAGMENT</p>
          <span style="display:none">SYNTHETIC_HIDDEN_RICH</span>
          <span aria-label="secret">SYNTHETIC_SECRET_RICH</span>
          <script>SYNTHETIC_SCRIPT_TEXT</script><style>SYNTHETIC_STYLE_TEXT</style>
          <iframe src="https://example.invalid/frame">SYNTHETIC_IFRAME_TEXT</iframe>
          <input value="SYNTHETIC_INPUT_VALUE">
          <img src="https://example.invalid/a.jpg" onerror="SYNTHETIC_HANDLER">
        </div>
      </section></main>
    """)
    assert len(result["rich_text"]) == 1
    rich = result["rich_text"][0]
    assert rich["label"] == "Listing description"
    assert rich["format_fidelity_verified"] is False
    html = rich["sanitized_html"]
    assert "<strong>home</strong> &amp; garden<br>Two &lt; three" in html
    assert "<em>Nested editor content</em>" in html
    assert "https://example.invalid/visible" in html
    assert "SYNTHETIC_" not in html
    assert not any(tag in html for tag in ("<a", "<img", "<input", "<iframe", "<script", "<style"))
    assert not any(attribute in html for attribute in ("onclick", "onerror", "style=", "class="))
    assert "SYNTHETIC_HIDDEN_RICH" not in rich["text"]
    assert "SYNTHETIC_SECRET_RICH" not in rich["text"]


def test_main_scope_excludes_unrelated_account_controls(extract):
    result = extract("""
      <header><input id="account-search" value="SYNTHETIC_ACCOUNT_VALUE"></header>
      <main><input id="listing-field" value="Listing value"></main>
      <footer><p>SYNTHETIC_FOOTER_TEXT</p></footer>
    """)
    assert [field["id"] for field in result["fields"]] == ["listing-field"]
    assert "SYNTHETIC_" not in json.dumps(result)
    assert "main_content_scope_unverified" not in result["limitations"]


def test_body_fallback_discloses_unverified_scope(extract):
    result = extract('<section><h1>Editor</h1><input id="listing-field" value="Synthetic listing"></section>')
    assert [field["id"] for field in result["fields"]] == ["listing-field"]
    assert "main_content_scope_unverified" in result["limitations"]


def test_editor_identity_ignores_hidden_heading_descendants():
    from quick_relist.hub_readonly import IDENTITY_SCRIPT
    if shutil.which("node") is None:
        pytest.skip("Optional Node/jsdom unavailable")
    probe = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("Optional Node/jsdom unavailable")
    html = """<h2><span style='display:none'>Edit listing - RXCU-12345 (</span>Edit listing - RXCU-99999 (Synthetic)</h2>
    <main><h4><span aria-hidden='true'>Edit listing - RXCU-12345 (</span>Edit listing - RXCU-99999 (Synthetic)</h4></main>"""
    run = subprocess.run(["node", "-e", NODE_RUNNER], input=json.dumps({"html": html, "url": BASE, "script": IDENTITY_SCRIPT, "args": ["RXCU12345"]}), capture_output=True, text=True, timeout=10, check=True)
    result = json.loads(run.stdout)
    assert result["outer_matches"] is False and result["inner_matches"] is False
    assert result["outer_count"] == result["inner_count"] == 1


def test_editor_identity_accepts_visible_hyphenated_reference():
    from quick_relist.hub_readonly import IDENTITY_SCRIPT
    if shutil.which("node") is None:
        pytest.skip("Optional Node/jsdom unavailable")
    probe = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("Optional Node/jsdom unavailable")
    html = "<h2>Edit listing - <span>RXCU-12345</span> (Synthetic)</h2><main><h4>Edit listing - RXCU-12345 (Synthetic)</h4></main>"
    run = subprocess.run(["node", "-e", NODE_RUNNER], input=json.dumps({"html": html, "url": BASE, "script": IDENTITY_SCRIPT, "args": ["RXCU12345"]}), capture_output=True, text=True, timeout=10, check=True)
    result = json.loads(run.stdout)
    assert result["outer_matches"] is True and result["inner_matches"] is True


@pytest.mark.parametrize("wrapper", ["style='opacity:0'", "aria-hidden='true'", "style='visibility:hidden'"])
def test_editor_identity_rejects_hidden_ancestor(wrapper):
    from quick_relist.hub_readonly import IDENTITY_SCRIPT
    if shutil.which("node") is None: pytest.skip("Optional Node/jsdom unavailable")
    probe = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, text=True)
    if probe.returncode: pytest.skip("Optional Node/jsdom unavailable")
    html = f"<section {wrapper}><h2>Edit listing - RXCU-12345 (Synthetic)</h2><main><h4>Edit listing - RXCU-12345 (Synthetic)</h4></main></section>"
    run = subprocess.run(["node", "-e", NODE_RUNNER], input=json.dumps({"html": html, "url": BASE, "script": IDENTITY_SCRIPT, "args": ["RXCU12345"]}), capture_output=True, text=True, timeout=10, check=True)
    result = json.loads(run.stdout)
    assert result["outer_matches"] is False and result["inner_matches"] is False


def test_only_exact_observed_country_selector_can_include_hidden_code(extract):
    html = """<main><div><div role='combobox' id='mui-component-select-country_id'>Synthetic country</div>
      <input id='country_id' name='country_id' aria-hidden='true' value='ZZ'></div>
      <input id='secret' name='secret' aria-hidden='true' value='SYNTHETIC_SECRET'></main>"""
    result = extract(html)
    matches = [f for f in result['fields'] if f.get('name') == 'country_id']
    assert len(matches) == 1
    assert matches[0]['value'] == 'Synthetic country'
    assert matches[0]['selected_options'] == [{'value': 'ZZ', 'text': 'Synthetic country'}]
    assert matches[0]['observed_hidden_country_code'] is True
    assert 'SYNTHETIC_SECRET' not in json.dumps(result)
    assert 'country_option_resolution_unverified' in result['limitations']


@pytest.mark.parametrize('code', ['SYNTHETIC_SECRET', '123', ''])
def test_country_hidden_input_does_not_admit_arbitrary_values(extract, code):
    html = f"""<main><div><div role='combobox' id='mui-component-select-country_id'>Synthetic country</div>
      <input id='country_id' name='country_id' aria-hidden='true' value='{code}'></div></main>"""
    result = extract(html)
    assert not [f for f in result['fields'] if f.get('name') == 'country_id']
    assert 'country_selector_unresolved' in result['limitations']


def test_country_exception_never_reads_password_control(extract):
    html = """<main><div><div role='combobox' id='mui-component-select-country_id'>Synthetic country</div>
      <input type='password' id='country_id' name='country_id' aria-hidden='true' value='ZZ'></div></main>"""
    result = extract(html)
    assert not [f for f in result['fields'] if f.get('name') == 'country_id']
    assert 'country_selector_unresolved' in result['limitations']


def test_row_binding_rejects_changed_reference_and_unrelated_link():
    from quick_relist.hub_readonly import ROW_BINDING_SCRIPT
    if shutil.which("node") is None: pytest.skip("Optional Node/jsdom unavailable")
    probe = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, text=True)
    if probe.returncode: pytest.skip("Optional Node/jsdom unavailable")
    # IDs passed only to this synthetic harness; production passes WebElements.
    runner = NODE_RUNNER.replace("const result = w.Function(input.script)(...(input.args || []));",
        "const args = input.ids.map(id => w.document.getElementById(id)); const result = w.Function(input.script)(...args, input.ref);")
    html = "<div id='row'><p>Ref: RXCU-99999</p><div id='combo'></div></div><ul id='menu'><li><a id='link'>Edit Listing</a></li></ul>"
    run = subprocess.run(["node", "-e", runner], input=json.dumps({"html": html, "url": BASE, "script": ROW_BINDING_SCRIPT, "ids": ["row", "combo", "menu", "link"], "ref": "RXCU12345"}), capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(run.stdout) is False
    html = html.replace("RXCU-99999", "RXCU-12345")
    run = subprocess.run(["node", "-e", runner], input=json.dumps({"html": html, "url": BASE, "script": ROW_BINDING_SCRIPT, "ids": ["row", "combo", "menu", "link"], "ref": "RXCU12345"}), capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(run.stdout) is True
    html = html.replace("<li><a id='link'>Edit Listing</a></li>", "</ul><a id='link'>Edit Listing</a><ul>")
    run = subprocess.run(["node", "-e", runner], input=json.dumps({"html": html, "url": BASE, "script": ROW_BINDING_SCRIPT, "ids": ["row", "combo", "menu", "link"], "ref": "RXCU12345"}), capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(run.stdout) is False


def test_observed_business_combos_preserve_only_allowlisted_structurally_bound_codes(extract):
    result = extract('''<main>
      <label id="property-label">Property type</label>
      <div><div role="combobox" id="mui-component-select-property_type_id" aria-labelledby="property-label">Apartment</div><input id="property_type_id" name="property_type_id" aria-hidden="true" tabindex="-1" value="42"></div>
      <div><div role="combobox" id="mui-component-select-office_id" aria-disabled="true">Synthetic office</div><input id="office_id" name="office_id" aria-hidden="true" disabled value="7"></div>
      <div><div role="combobox" id="mui-component-select-unknown">Unlisted selector</div><input id="unknown" name="unknown" aria-hidden="true" value="SYNTHETIC_HIDDEN_SECRET"></div>
      <div><div role="combobox" id="mui-component-select-mandate_type_id">Exclusive</div><span>Not adjacent</span><input id="mandate_type_id" name="mandate_type_id" aria-hidden="true" value="SYNTHETIC_UNBOUND_CODE"></div>
    </main>''')
    fields = {item['name']: item for item in result['fields']}
    assert fields['property_type_id']['selected_options'] == [{'value': '42', 'text': 'Apartment'}]
    assert fields['property_type_id']['label'] == 'Property type'
    assert fields['office_id']['disabled'] is True
    assert fields['office_id']['observed_business_selection_code'] is True
    assert 'unknown' not in fields and 'mandate_type_id' not in fields
    assert 'SYNTHETIC_HIDDEN_SECRET' not in json.dumps(result)
    assert 'SYNTHETIC_UNBOUND_CODE' not in json.dumps(result)
    assert 'business_selector_unresolved' in result['limitations']


def test_observed_media_cards_capture_original_refs_and_global_positions_without_classifying(extract):
    result = extract('''<main><div>
      <div><h6>Listing images</h6><input type="file"></div>
      <div><h6>Floor plan images</h6><input type="file"></div>
      <div><div role="button" aria-roledescription="sortable"><figure>
        <img alt="preview" id="image-123" pos="0" src="https://images.example.invalid/thumb.jpg" data-full-src="https://images.example.invalid/listings/999/original/photo.jpg?token=SYNTHETIC_QUERY">
      </figure></div><div role="button" aria-roledescription="sortable"><figure>
        <img alt="preview" id="image-456" pos="1" src="https://images.example.invalid/thumb2.jpg" data-full-src="https://images.example.invalid/listings/999/original/second.png">
      </figure></div></div>
    </div></main>''')
    first, second = result['media']
    assert first['observed_asset_id'] == '123' and first['observed_position'] == 0
    assert second['observed_position'] == 1
    assert first['original_source_ref'] == {'url': 'https://images.example.invalid/listings/999/original/photo.jpg', 'query_omitted': True, 'fragment_omitted': False}
    assert first['kind_hint'] == second['kind_hint'] == 'img'
    assert first['original_bytes_verified'] is False and first['order_verified'] is False
    assert 'SYNTHETIC_QUERY' not in json.dumps(result)


def test_unrecognized_image_metadata_does_not_claim_original_source(extract):
    result = extract('''<main><img id="other" alt="preview" pos="0" src="https://example.invalid/thumb.jpg" data-full-src="https://example.invalid/private.jpg"></main>''')
    assert result['media'][0]['original_source_ref'] is None
    assert result['media'][0]['observed_asset_id'] is None


def test_allowlisted_business_combo_hidden_by_ancestor_opacity_is_omitted(extract):
    result = extract('''<main><div style="opacity:0"><div role="combobox" id="mui-component-select-office_id">Hidden office</div><input id="office_id" name="office_id" aria-hidden="true" value="SYNTHETIC_HIDDEN_CODE"></div></main>''')
    assert not result['fields']
    assert 'SYNTHETIC_HIDDEN_CODE' not in json.dumps(result)


@pytest.mark.parametrize("hidden_on", ["ancestor", "combobox"])
def test_allowlisted_selector_requires_nontransparent_visible_combobox(extract, hidden_on):
    wrapper_style = ' style="opacity:0"' if hidden_on == "ancestor" else ''
    combo_style = ' style="opacity:0"' if hidden_on == "combobox" else ''
    result = extract(f'''<main><div{wrapper_style}>
        <div role="combobox" id="mui-component-select-office_id"{combo_style}>SYNTHETIC_CONCEALED_OFFICE</div>
        <input id="office_id" name="office_id" aria-hidden="true" type="text" value="123">
        </div></main>''')
    assert not result["fields"]
    assert "SYNTHETIC_CONCEALED_OFFICE" not in json.dumps(result)


def test_observed_room_dialog_preserves_repeated_ids_by_room_scope(extract):
    result = extract('''
      <main aria-hidden="true"><input id="floor_area" value="SYNTHETIC_HIDDEN_MAIN"></main>
      <div role="dialog" aria-labelledby="title"><h2 id="title">Set bedrooms options</h2>
      <div class="MuiDialogContent-root">
        <div><h6>Bedroom 1</h6><label>Carpets<input id="has_carpets" name="has_carpets" type="checkbox" checked></label>
          <textarea name="description">First synthetic room</textarea>
          <textarea aria-hidden="true" readonly tabindex="-1">SYNTHETIC_HIDDEN_MEASURE</textarea></div>
        <div><h6>Bedroom 2</h6><label>Carpets<input id="has_carpets" name="has_carpets" type="checkbox"></label>
          <textarea name="description">Second synthetic room</textarea></div>
      </div></div>''')
    groups = result['dialogs'][0]['room_groups']
    assert [g['title'] for g in groups] == ['Bedroom 1', 'Bedroom 2']
    assert [g['index'] for g in groups] == [1, 2]
    assert [g['fields'][0]['checked'] for g in groups] == [True, False]
    assert [g['fields'][1]['value'] for g in groups] == ['First synthetic room', 'Second synthetic room']
    assert all(g['mapping_verified'] is False for g in groups)
    assert 'SYNTHETIC_HIDDEN' not in json.dumps(result)


def test_unknown_or_ambiguous_room_group_structure_is_not_guessed(extract):
    result = extract('''<main><div role="dialog" aria-label="Set bathrooms options">
      <div class="MuiDialogContent-root"><div><h6>Bathroom 1</h6><input name="description" value="First"></div>
        <div><h6>Bathroom 1</h6><input name="description" value="Duplicate"></div>
        <div><h6>Bathroom pending</h6><input value="Unnumbered"></div></div>
      </div><div role="dialog" aria-label="Unknown room options"><div class="MuiDialogContent-root">
        <div><h6>Bedroom 1</h6><input value="Unverified structure"></div></div></div></main>''')
    assert len(result['dialogs'][0]['room_groups']) == 1
    assert result['dialogs'][1]['room_groups'] == []
    assert 'room_group_structure_unresolved' in result['limitations']
    # Unmapped fields remain in the raw dialog; only semantic grouping is refused.
    assert any(f['value'] == 'Duplicate' for f in result['dialogs'][0]['fields'])


@pytest.mark.parametrize('title,room', [
    ('Set bathrooms options', 'Bathroom'), ('Set kitchens options', 'Kitchen'),
    ('Set parkings options', 'Parking'), ('Set lounges options', 'Lounge'),
])
def test_verified_room_categories_keep_independent_option_names(extract, title, room):
    result = extract(f'''<main><div role="dialog" aria-label="{title}"><div class="MuiDialogContent-root">
      <div><h6>{room} 1</h6><label>Specific open plan<input type="checkbox" name="is_open_plan" checked></label>
      <label>General open plan<input type="checkbox" name="has_open_plan"></label>
      <textarea name="description">Synthetic description</textarea></div></div></div></main>''')
    group = result['dialogs'][0]['room_groups'][0]
    assert group['title'] == room + ' 1'
    assert [(f['name'], f['checked']) for f in group['fields'][:2]] == [('is_open_plan', True), ('has_open_plan', False)]
