"""Production read-only scripts on optional offline Node/jsdom fixtures.

No packages are installed. Node syntax checks run independently of jsdom.
jsdom's rectangle shim is not a real browser/layout qualification.
"""
import json
import shutil
import subprocess
import unittest

from quick_relist.hub_readonly import IDENTITY_SCRIPT, SOURCE_CONTEXT_SCRIPT


RUNNER = r"""
const {JSDOM} = require('jsdom');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const dom = new JSDOM(input.html, {url:'https://hub.remax.co.za/app/listings', runScripts:'outside-only'});
const w = dom.window;
Object.defineProperty(w.Element.prototype, 'getClientRects', {value:function() {
  for (let el=this; el; el=el.parentElement) {
    if (el.hidden || w.getComputedStyle(el).display === 'none') return [];
  }
  return [{width:100, height:20}];
}});
const args = [w.document.documentElement, w.document.getElementById('row'), 'RXCU12345'];
if (input.setup) w.Function(input.setup)();
const denied = () => { throw new Error('Unexpected mutation, network or hidden-state access'); };
for (const key of ['localStorage', 'sessionStorage']) Object.defineProperty(w, key, {get:denied});
Object.defineProperty(w.document, 'cookie', {get:denied, set:denied});
for (const key of ['fetch', 'XMLHttpRequest', 'WebSocket', 'open']) w[key] = denied;
w.HTMLElement.prototype.click = denied;
w.HTMLFormElement.prototype.submit = denied;
w.HTMLFormElement.prototype.requestSubmit = denied;
const before = w.document.documentElement.outerHTML;
const href = w.location.href;
const result = w.Function(input.script)(...args);
if (before !== w.document.documentElement.outerHTML || href !== w.location.href) throw new Error('DOM or route changed');
process.stdout.write(JSON.stringify(result));
dom.window.close();
"""


class EditorScriptSyntaxTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is unavailable")
    def test_source_and_identity_scripts_parse_with_node(self):
        for script in (SOURCE_CONTEXT_SCRIPT, IDENTITY_SCRIPT):
            with self.subTest(script="source" if script == SOURCE_CONTEXT_SCRIPT else "identity"):
                result = subprocess.run(["node", "-e", "new Function(require('node:fs').readFileSync(0, 'utf8'));"],
                                        input=script, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is unavailable")
    def test_production_source_script_checks_ancestor_css_but_allows_aria_hiding(self):
        # Deliberately keep row rectangles nonempty under opacity:0, as browsers
        # do. This small DOM double tests the production ancestor traversal; it
        # does not substitute for jsdom or real browser/layout qualification.
        runner = r"""
const vm = require('node:vm');
const script = require('node:fs').readFileSync(0, 'utf8');
const scenarios = [{aria:true}, {aria:true, opacity:'0'}, {hidden:true},
  {display:'none'}, {visibility:'hidden'}, {visibility:'collapse'}, {}];
const results = scenarios.map(scenario => {
  const document = {documentElement:{}};
  const element = (parentElement, attributes={}, style={}) => ({parentElement, attributes, style,
    isConnected:true, ownerDocument:document, getClientRects:() => [{width:100, height:20}],
    hasAttribute(name) { return Object.hasOwn(this.attributes, name); },
    getAttribute(name) { return this.attributes[name] || null; }});
  const ancestor = element(null, {...(scenario.aria ? {'aria-hidden':'true'} : {}),
    ...(scenario.hidden ? {hidden:''} : {})}, scenario);
  const row = element(ancestor), paragraph = element(row);
  row.querySelectorAll = selector => { if (selector !== 'p') throw Error('Unexpected selector'); return [paragraph]; };
  document.querySelectorAll = selector => { if (selector !== '[role="row"]') throw Error('Unexpected selector'); return [row]; };
  document.createTreeWalker = () => { let seen=false; return {nextNode:() => {
    if (seen) return null; seen=true; return {parentElement:paragraph, nodeValue:'Ref: RXCU-12345'};
  }}; };
  const getComputedStyle = element => ({display:'block', visibility:'visible', opacity:'1', ...element.style});
  return vm.runInNewContext('(function(){' + script + '})', {document, getComputedStyle, NodeFilter:{SHOW_TEXT:4}})
    (document.documentElement, row, 'RXCU12345');
});
process.stdout.write(JSON.stringify(results));
"""
        result = subprocess.run(["node", "-e", runner], input=SOURCE_CONTEXT_SCRIPT,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [True, False, False, False, False, False, True])


class EditorSourceDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("Optional Node/jsdom unavailable; no DOM runtime tests ran")
        probe = subprocess.run(["node", "-e", "require.resolve('jsdom')"], text=True, capture_output=True, timeout=10)
        if probe.returncode:
            raise unittest.SkipTest("Optional jsdom unavailable; no DOM runtime tests ran")

    def read(self, *, inner="Ref: RXCU-12345", extra="", setup="", ancestor="aria-hidden='true'"):
        html = f"<div {ancestor}><div role='row' id='row'><p>{inner}</p></div>{extra}</div>"
        result = subprocess.run(["node", "-e", RUNNER],
                                input=json.dumps({"html": html, "setup": setup, "script": SOURCE_CONTEXT_SCRIPT}),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_menu_is_not_required_and_ancestor_accessibility_hiding_is_allowed(self):
        self.assertTrue(self.read())
        self.assertTrue(self.read(ancestor=""))

    def test_changed_reference_and_duplicate_rows_are_rejected(self):
        self.assertFalse(self.read(inner="Ref: RXCU-99999"))
        self.assertFalse(self.read(extra="<div role='row'><p>Ref: RXCU-12345</p></div>"))
        self.assertTrue(self.read(extra="<div role='row'><p>Ref: RXCU-99999</p></div>"))

    def test_hidden_reference_cannot_supply_identity(self):
        self.assertFalse(self.read(inner="<span hidden>Ref: RXCU-12345</span>"))
        self.assertFalse(self.read(setup="document.getElementById('row').setAttribute('aria-hidden', 'true');"))
        self.assertTrue(self.read(extra="<div role='row' hidden><p>Ref: RXCU-12345</p></div>"))
        self.assertFalse(self.read(ancestor="aria-hidden='true' style='opacity:0'"))
        self.assertFalse(self.read(ancestor="style='visibility:hidden'"))

    def test_detached_or_replaced_source_row_is_rejected(self):
        self.assertFalse(self.read(setup="document.getElementById('row').remove();"))
        self.assertFalse(self.read(setup="const row=document.getElementById('row'); row.replaceWith(row.cloneNode(true));"))

    def test_replaced_source_document_root_is_rejected(self):
        self.assertFalse(self.read(setup="const root=document.documentElement; root.replaceWith(root.cloneNode(true));"))


if __name__ == "__main__":
    unittest.main()
