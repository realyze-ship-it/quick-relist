"""Synthetic caption geometry only; no Hub data or controller exemptions."""


def positioning_html(*, placement='clean', overlay='none', child='text', size='normal'):
    positions = {'clean': 'top:180px', 'bottom': 'top:calc(100vh - 36px)',
                 'below': 'top:1500px', 'fixed': 'position:fixed;top:calc(100vh - 36px)'}
    contents = {'text': 'Set bedrooms options', 'span': '<span>Set bedrooms options</span>',
                'button': '<button type="button">Set bedrooms options</button>',
                'empty_button': 'Set bedrooms options<button type="button"></button>',
                'empty_span': 'Set bedrooms options<span></span>',
                'nested_empty_button': 'Set bedrooms options<span><button type="button"></button></span>'}
    sizes = {'normal': '', 'tiny': 'display:inline-block;width:.25px;height:.25px;overflow:hidden',
             'zero': 'display:inline-block;width:0;height:0;overflow:hidden',
             'multiline': 'display:inline;font-size:16px'}
    help_style = {'none': 'display:none',
                  'help': 'left:22px;bottom:0;width:156px;height:56px',
                  'full': 'inset:0;width:100vw;height:100vh'}
    return f'''<style>
html,body{{margin:0;padding:0;scroll-behavior:smooth}} main{{min-height:2800px}}
#feature{{position:absolute;left:30px;{positions[placement]}}}
#opener{{font:12px sans-serif;{sizes[size]}}}
h6{{margin:0}} #help{{position:fixed;border:0;opacity:0;z-index:2147483000;{help_style[overlay]}}}
</style><h2>Edit listing - RXCU-12345 (Synthetic)</h2>
<main><h4>Edit listing - RXCU-12345 (Synthetic)</h4><div id="feature">
<div class="flex alignItems_flexend"><h6>Bedrooms</h6><div class="MuiFormControl-root"><div class="MuiInputBase-root">
<input id="feature-bedroom-input" type="text" value="2"></div></div></div>
<div><span id="opener" class="MuiTypography-caption">{contents[child]}</span></div></div></main>
<iframe id="help" title="Synthetic transparent Help footprint"></iframe>'''
