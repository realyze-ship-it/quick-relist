import json

from quick_relist.diagnostics import record_error


def test_diagnostics_record_causes_but_never_messages_or_values(tmp_path):
    try:
        try:
            raise OSError(6, 'private URL, OTP and listing contents')
        except OSError as exc:
            raise RuntimeError('another private message') from exc
    except RuntimeError as exc:
        reference = record_error(tmp_path / 'diagnostics', 'open_browser', exc)
    text = (tmp_path / 'diagnostics' / ('error-' + reference + '.json')).read_text()
    record = json.loads(text)
    assert record['stage'] == 'open_browser'
    assert [e['exception_type'] for e in record['errors']] == ['RuntimeError', 'OSError']
    assert record['errors'][1]['errno'] == 6
    assert 'private' not in text.replace('test_private_diagnostics.py', '')
    assert str(tmp_path) not in text


def test_diagnostic_failure_does_not_replace_original_error(tmp_path):
    destination = tmp_path / 'not-a-directory'
    destination.write_text('existing file')
    assert record_error(destination, 'open_browser', ValueError('private')) is None
    assert destination.read_text() == 'existing file'
