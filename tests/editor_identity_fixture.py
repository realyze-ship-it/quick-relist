"""Generated identity-only fixtures: no real listing text, browser or network."""

TITLE = "Edit listing - RXCU-12345 (Synthetic)"
WRONG = "Edit listing - RXCU-99999 (Synthetic)"
MATCH = {"outer_count": 1, "inner_count": 1, "outer_matches": True, "inner_matches": True}
NO_OUTER = {**MATCH, "outer_count": 0, "outer_matches": False}
NO_INNER = {**MATCH, "inner_count": 0, "inner_matches": False}
WRONG_OUTER = {**MATCH, "outer_matches": False}
WRONG_INNER = {**MATCH, "inner_matches": False}
DUPLICATE_OUTER = {**MATCH, "outer_count": 2, "outer_matches": False}
DUPLICATE_INNER = {**MATCH, "inner_count": 2, "inner_matches": False}


def heading(tag, text=TITLE, attrs=""):
    return f"<{tag} {attrs}>{text}</{tag}>"


def page(outer=None, inner=None, *, header_attrs="", main_attrs="", extra=""):
    if outer is None:
        outer = heading("h2", attrs="style='opacity:0'")
    if inner is None:
        inner = heading("h4")
    # jsdom does not always expose CSS initial computed opacity/display values.
    # Set browser-equivalent initial values explicitly, without hiding missing
    # style-read cases (those are independently fault-injected in the runner).
    defaults = "<style>*{opacity:1}html,body,header,main{display:block;visibility:visible}</style>"
    return defaults + f"<header {header_attrs}>{outer}</header><main {main_attrs}>{inner}</main>{extra}"


def identity_cases():
    transparent = heading("h2", attrs="style='opacity:0'")
    wrong_transparent = heading("h2", WRONG, "style='opacity:0'")
    cases = {
        "visible_pair": (page(outer=heading("h2")), MATCH),
        "qualified_transparent": (page(), MATCH),
        "visible_wrong_beats_fallback": (page(outer=heading("h2", WRONG) + transparent), WRONG_OUTER),
        "visible_duplicates_beat_fallback": (page(outer=heading("h2") * 2 + transparent), DUPLICATE_OUTER),
        "legacy_visible_pair_ignores_unused_transparent": (page(outer=heading("h2") + wrong_transparent), MATCH),
        "transparent_wrong": (page(outer=wrong_transparent), WRONG_OUTER),
        "transparent_duplicates": (page(outer=transparent * 2), DUPLICATE_OUTER),
        "transparent_mixed_references": (page(outer=transparent + wrong_transparent), DUPLICATE_OUTER),
        "transparent_nested_span": (page(outer=heading("h2", f"<span>{TITLE}</span>", "style='opacity:0'")), NO_OUTER),
        "transparent_hidden_descendant": (page(outer=heading("h2", f"<span hidden>{TITLE}</span>", "style='opacity:0'")), NO_OUTER),
        "transparent_self_hidden": (page(outer=heading("h2", attrs="hidden style='display:block;opacity:0'")), NO_OUTER),
        "transparent_self_display_none": (page(outer=heading("h2", attrs="style='display:none;opacity:0'")), NO_OUTER),
        "transparent_self_visibility_hidden": (page(outer=heading("h2", attrs="style='visibility:hidden;opacity:0'")), NO_OUTER),
        "transparent_self_inline": (page(outer=heading("h2", attrs="style='display:inline;opacity:0'")), NO_OUTER),
        "transparent_self_display_contents": (page(outer=heading("h2", attrs="style='display:contents;opacity:0'")), NO_OUTER),
        "transparent_zero_area": (page(outer=heading("h2", attrs="style='opacity:0;width:0;height:0;overflow:hidden'")), NO_OUTER),
        "transparent_zero_width": (page(outer=heading("h2", attrs="style='opacity:0;width:0;height:20px;overflow:hidden'")), NO_OUTER),
        "transparent_zero_height": (page(outer=heading("h2", attrs="style='opacity:0;width:100px;height:0;overflow:hidden'")), NO_OUTER),
        "ordinary_fractional_opacity_stays_visible": (page(outer=heading("h2", attrs="style='opacity:.5'")), MATCH),
        "transparent_wrong_raw_whitespace": (page(outer=heading("h2", TITLE.replace("Edit listing", "Edit\u00a0listing"), "style='opacity:0'")), NO_OUTER),
        "transparent_h2_moved_inside_main": (page(outer="", inner=transparent + heading("h4")), NO_OUTER),
        "h4_moved_outside_main": (page(inner="", extra=heading("h4")), NO_INNER),
        "h4_duplicate_other_main": (page(extra="<main>" + heading("h4") + "</main>"), DUPLICATE_INNER),
        "missing_main_h4": (page(inner=""), NO_INNER),
        "wrong_main_h4": (page(inner=heading("h4", WRONG)), WRONG_INNER),
        "duplicate_main_h4": (page(inner=heading("h4") * 2), DUPLICATE_INNER),
        "transparent_main_h4": (page(inner=heading("h4", attrs="style='opacity:0'")), NO_INNER),
        "hidden_main_h4": (page(inner=heading("h4", attrs="hidden")), NO_INNER),
        "zero_area_main_h4": (page(inner=heading("h4", attrs="style='width:0;height:0;overflow:hidden'")), NO_INNER),
        "zero_width_main_h4": (page(inner=heading("h4", attrs="style='width:0;height:20px;overflow:hidden'")), NO_INNER),
        "zero_height_main_h4": (page(inner=heading("h4", attrs="style='width:100px;height:0;overflow:hidden'")), NO_INNER),
        "wrong_zero_area_main_h4_stays_conflict": (page(inner=heading("h4", WRONG, "style='width:0;height:0;overflow:hidden'")), WRONG_INNER),
        "duplicate_zero_area_main_h4_stays_conflict": (page(inner=heading("h4") + heading("h4", attrs="style='width:0;height:0;overflow:hidden'")), DUPLICATE_INNER),
        "legacy_visible_pair_zero_area_h4_unchanged": (page(outer=heading("h2"), inner=heading("h4", attrs="style='width:0;height:0;overflow:hidden'")), MATCH),
        "nested_visible_main_h4": (page(inner=heading("h4", "Edit listing - <span>RXCU-12345</span> (Synthetic)")), NO_INNER),
        "zero_area_reference_descendant": (page(inner=heading("h4", "Edit listing - <span style='display:inline-block;width:0;height:0;overflow:hidden'>RXCU-12345</span> (Synthetic)")), NO_INNER),
        "nested_wrong_main_h4_stays_conflict": (page(inner=heading("h4", f"<span>{WRONG}</span>")), WRONG_INNER),
        "duplicate_nested_h4_stays_conflict": (page(inner=heading("h4") + heading("h4", f"<span>{TITLE}</span>")), DUPLICATE_INNER),
        "legacy_visible_pair_nested_h4_unchanged": (page(outer=heading("h2"), inner=heading("h4", "Edit listing - <span>RXCU-12345</span> (Synthetic)")), MATCH),
        "legacy_visible_pair_zero_area_reference_descendant_unchanged": (page(outer=heading("h2"), inner=heading("h4", "Edit listing - <span style='display:inline-block;width:0;height:0;overflow:hidden'>RXCU-12345</span> (Synthetic)")), MATCH),
        "hidden_h4_ancestor": (page(main_attrs="style='opacity:0'"), NO_INNER),
        "hidden_expected_h4_descendant_cannot_match": (page(inner=heading("h4", f"<span hidden>{TITLE}</span>{WRONG}")), WRONG_INNER),
        "hidden_wrong_h4_descendant_still_unqualified_shape": (page(inner=heading("h4", f"<span hidden>{WRONG}</span>{TITLE}")), NO_INNER),
    }
    for value in ("true", "false", "", "unexpected"):
        cases["transparent_self_aria_" + (value or "empty")] = (
            page(outer=heading("h2", attrs=f"style='opacity:0' aria-hidden='{value}'")), NO_OUTER)
    for name, attrs in (("opacity", "style='opacity:0'"), ("display", "style='display:none'"),
                        ("visibility", "style='visibility:hidden'"), ("collapse", "style='visibility:collapse'"),
                        ("hidden", "hidden"), ("aria", "aria-hidden='true'")):
        cases["transparent_ancestor_" + name] = (page(header_attrs=attrs), NO_OUTER)
    return cases


def identity_style_faults():
    """Unreadable computed results, not real CSS defaults or production writes."""
    faults = {name: {"tag": "HEADER", "property": prop, "value": value, "expected": NO_OUTER}
              for name, prop, value in (
        ("missing_opacity", "opacity", ""), ("whitespace_opacity", "opacity", " "),
        ("invalid_opacity", "opacity", "invalid"), ("infinite_opacity", "opacity", "Infinity"),
        ("out_of_range_opacity", "opacity", "2"), ("negative_opacity", "opacity", "-1"),
        ("missing_display", "display", ""), ("missing_visibility", "visibility", ""),
    )}
    for tag in ("H4", "MAIN"):
        for name, prop, value in (("opacity", "opacity", ""), ("display", "display", ""),
                                  ("visibility", "visibility", ""), ("invalid_opacity", "opacity", "invalid")):
            faults[f"{tag.lower()}_missing_{name}"] = {
                "tag": tag, "property": prop, "value": value, "expected": NO_INNER}
    faults["wrong_h4_missing_opacity"] = {"tag": "H4", "property": "opacity", "value": "",
        "expected": WRONG_INNER, "html": page(inner=heading("h4", WRONG))}
    faults["duplicate_h4_missing_opacity"] = {"tag": "H4", "property": "opacity", "value": "",
        "expected": DUPLICATE_INNER, "html": page(inner=heading("h4") * 2)}
    return faults


# JSDOM has no layout. Geometry is deliberately simulated for the few cases
# requiring an absent/zero-area box; the same fixtures also run in opt-in Chrome.
IDENTITY_RUNNER = r"""
const {JSDOM} = require('jsdom');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const results = {};
for (const [name, html] of Object.entries(input.cases)) {
  const dom = new JSDOM(html, {url:'https://hub.remax.co.za/app/listings/edit/123', runScripts:'outside-only'});
  const w = dom.window;
  const styleFault = (input.style_faults || {})[name];
  if (styleFault) {
    const computed = w.getComputedStyle.bind(w);
    w.getComputedStyle = e => {
      const style = computed(e);
      return e.tagName === styleFault.tag ? new Proxy(style, {get:(target, key) =>
        key === styleFault.property ? styleFault.value : Reflect.get(target, key)}) : style;
    };
  }
  Object.defineProperty(w.Element.prototype, 'getClientRects', {value:function() {
    for (let e = this; e; e = e.parentElement) if (e.hidden || w.getComputedStyle(e).display === 'none') return [];
    const style = w.getComputedStyle(this);
    if (style.display === 'contents') return [];
    const width = parseFloat(style.width), height = parseFloat(style.height);
    return [{width:Number.isFinite(width) ? width : 100, height:Number.isFinite(height) ? height : 20}];
  }});
  const denied = () => { throw new Error('Unexpected mutation, network or storage access'); };
  for (const key of ['localStorage', 'sessionStorage']) Object.defineProperty(w, key, {get:denied});
  Object.defineProperty(w.document, 'cookie', {get:denied, set:denied});
  for (const key of ['fetch', 'XMLHttpRequest', 'WebSocket', 'open']) w[key] = denied;
  w.HTMLElement.prototype.click = denied;
  w.HTMLFormElement.prototype.submit = denied;
  w.HTMLFormElement.prototype.requestSubmit = denied;
  const before = w.document.documentElement.outerHTML, url = w.location.href;
  results[name] = w.Function(input.script)('RXCU12345');
  if (before !== w.document.documentElement.outerHTML || url !== w.location.href) throw new Error('DOM or route changed');
  dom.window.close();
}
process.stdout.write(JSON.stringify(results));
"""
