"""Encode the documented Windows HTML clipboard fragment format."""


def windows_html(fragment):
    template = ('Version:1.0\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\n'
                'StartFragment:{:010d}\r\nEndFragment:{:010d}\r\n')
    prefix = b'<html><body><!--StartFragment-->'
    suffix = b'<!--EndFragment--></body></html>'
    content = fragment.encode('utf-8')
    start = len(template.format(0,0,0,0).encode('ascii'))
    first = start + len(prefix)
    last = first + len(content)
    header = template.format(start,last+len(suffix),first,last).encode('ascii')
    return header + prefix + content + suffix + b'\0'
