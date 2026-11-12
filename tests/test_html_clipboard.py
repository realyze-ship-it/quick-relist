from quick_relist.html_clipboard import windows_html


def test_windows_fragment_offsets_count_utf8_bytes_and_preserve_formatting():
    fragment='<p>**Résumé**<br><br>€ &amp; facts.</p>'
    value=windows_html(fragment)
    header=value[:value.index(b'<html>')].decode('ascii')
    fields=dict(line.split(':',1) for line in header.splitlines())
    assert value[int(fields['StartFragment']):int(fields['EndFragment'])].decode('utf-8')==fragment
    context=value[int(fields['StartHTML']):int(fields['EndHTML'])]
    assert context.startswith(b'<html><body><!--StartFragment-->')
    assert context.endswith(b'<!--EndFragment--></body></html>')
