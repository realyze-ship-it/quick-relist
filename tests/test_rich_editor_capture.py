"""Offline actual-extractor regressions; no private Hub data or mutations.

jsdom covers DOM selection and attribution, not native browser rendering or
current Hub nesting. The shared harness forbids network/storage/action calls.
"""
import json

import pytest

from test_dom_runtime import extract


def test_labelled_editable_inside_presentation_wrappers_is_selected(extract):
    result = extract('''<main><div class="rdw-editor"><p>Toolbar text</p>
      <div class="rdw-editor-main"><div role="textbox" contenteditable="true"
      aria-label="rdw-editor" id="synthetic-editor"><p>A <b>synthetic</b> description</p>
      </div><p>Wrapper-only footer</p></div></div></main>''')
    assert len(result['rich_text']) == 1
    rich = result['rich_text'][0]
    assert rich['id'] == 'synthetic-editor'
    assert rich['label'] == 'rdw-editor'
    assert rich['text'] == 'A synthetic description'
    assert '<b>synthetic</b>' in rich['sanitized_html']
    assert 'Toolbar' not in rich['sanitized_html']
    assert 'footer' not in rich['sanitized_html']
    assert rich['format_fidelity_verified'] is False


def test_sibling_editors_are_separate_not_combined_or_arbitrarily_selected(extract):
    result = extract('''<main><div class="rdw-editor-main">
      <div contenteditable="true" aria-label="rdw-editor">Synthetic first</div>
      <div contenteditable="true" aria-label="rdw-editor">Synthetic second</div>
      </div></main>''')
    assert [r['text'] for r in result['rich_text']] == ['Synthetic first', 'Synthetic second']
    assert [r['label'] for r in result['rich_text']] == ['rdw-editor', 'rdw-editor']


def test_wrapper_label_and_sibling_labels_are_not_borrowed(extract):
    result = extract('''<main><div class="rdw-editor-main" aria-label="rdw-editor">
      <span aria-label="rdw-editor">Synthetic toolbar</span>
      <div contenteditable="true">Synthetic unlabelled text</div></div></main>''')
    assert len(result['rich_text']) == 1
    assert result['rich_text'][0]['label'] == ''
    assert result['rich_text'][0]['text'] == 'Synthetic unlabelled text'


@pytest.mark.parametrize('attributes', ['hidden', 'aria-hidden="true"',
    'style="display:none"', 'style="opacity:0"', 'aria-label="Access token"'])
def test_hidden_or_sensitive_editable_never_falls_back_to_wrapper(extract, attributes):
    result = extract(f'''<main><div class="rdw-editor-main">
      <div contenteditable="true" {attributes}>SYNTHETIC_EXCLUDED</div></div></main>''')
    assert result['rich_text'] == []
    assert 'SYNTHETIC_EXCLUDED' not in json.dumps(result)


def test_secret_ancestor_remains_excluded(extract):
    result = extract('''<main><div id="access_token" class="rdw-editor-main">
      <div contenteditable="true" aria-label="rdw-editor">SYNTHETIC_EXCLUDED</div></div></main>''')
    assert result['rich_text'] == []
    assert 'SYNTHETIC_EXCLUDED' not in json.dumps(result)


def test_wrapper_only_legacy_observation_and_unrelated_content(extract):
    result = extract('''<main><p>Unrelated content</p><div class="rdw-editor">
      <div class="rdw-editor-main"><p>Legacy synthetic text</p></div></div>
      <div role="textbox">Not editable</div></main>''')
    assert len(result['rich_text']) == 1
    assert result['rich_text'][0]['text'] == 'Legacy synthetic text'
    assert result['rich_text'][0]['label'] == ''


def test_nested_editables_remain_one_observation_with_explicit_limitation(extract):
    result = extract('''<main><div contenteditable="true">Outer synthetic
      <div contenteditable="true" aria-label="rdw-editor">Nested synthetic</div>
      </div></main>''')
    assert len(result['rich_text']) == 1
    assert result['rich_text'][0]['label'] == ''
    assert 'rich_text_nested_editables_unresolved' in result['limitations']


@pytest.mark.parametrize('count', [1, 2])
def test_actual_editable_attribution_reaches_inventory_without_guessing(extract, count):
    from quick_relist.source_fields import extract_source_fields
    from test_source_fields import capture, page, entries

    html = '<main><div class="rdw-editor-main">' + ''.join(
        f'<div contenteditable="true" aria-label="rdw-editor">Synthetic {i}</div>'
        for i in range(count)) + '</div></main>'
    observed = extract(html)
    mapped = extract_source_fields(capture(page('media', [], rich_text=observed['rich_text'])))
    entry = entries(mapped)['rich_text_description']
    assert len(entry['observations']) == count
    if count == 1:
        assert entry['status'] == 'unresolved'
        assert entry['value'] == 'Synthetic 0'
        assert entry['issues'] == ['rich_text_format_fidelity_unverified']
    else:
        assert entry['status'] == 'ambiguous'
        assert entry['issues'] == ['multiple_matching_controls']
        assert 'value' not in entry
    assert mapped['live_ready'] is False
