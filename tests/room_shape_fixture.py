"""Synthetic bounded room shapes; no listing data or browser automation."""
from html import escape

SECTIONS = {
    'Bedroom': ('Bedroom options', 'Room options', 'Floor options'),
    'Bathroom': ('Bathroom options', 'Room options', 'Floor options'),
    'Kitchen': ('Kitchen options', 'Room options', 'Floor options'),
    'Parking': ('Parking options', 'Room options', 'Floor options'),
    'Lounge': ('Room options', 'Floor options'),
}
TITLES = {name: 'Set ' + name.lower() + 's options' for name in SECTIONS}


def checkbox(name, number, checked=False, value=' 007 '):
    return f'''<label class="MuiFormControlLabel-root MuiFormControlLabel-labelPlacementEnd">
<span class="MuiButtonBase-root MuiCheckbox-root PrivateSwitchBase-root">
<input type="checkbox" id="{name}" name="{name}" class="PrivateSwitchBase-input" value="{escape(value)}"{' checked' if checked else ''}>
<svg aria-hidden="true" focusable="false"><path d="M0 0h1v1z"></path></svg></span>
<span class="MuiTypography-root MuiTypography-body1 MuiFormControlLabel-label">Synthetic {name} {number}</span></label>'''


def room(number, category):
    keys = ['has_built_in_cupboards', 'has_open_plan', 'has_carpets']
    sections = ''.join(f'''<h6>{title}</h6><div class="MuiGrid-root MuiGrid-container MuiGrid-direction-xs-row">
<div class="MuiGrid-root MuiGrid-direction-xs-row MuiGrid-grid-xs-12 MuiGrid-grid-sm-4">
{checkbox(keys[i], number, checked=(number == i + 1))}</div></div><br>'''
        for i, title in enumerate(SECTIONS[category]))
    return f'''<div data-fixture="room-{number}" style="border:1px solid #ddd;padding:10px;border-radius:5px;position:relative">
<h6>{category} {number}</h6><button type="button">Synthetic remove control</button><br>{sections}
<div class="MuiFormControl-root MuiFormControl-fullWidth MuiTextField-root">
<label for="room-description-{number}">Synthetic description {number}</label>
<div class="MuiInputBase-root MuiOutlinedInput-root MuiInputBase-multiline">
<textarea rows="5" id="room-description-{number}" name="description">  Synthetic room {number}\n  Second line  </textarea>
<textarea aria-hidden="true" style="visibility:hidden;position:absolute" readonly tabindex="-1">SYNTHETIC_MEASURE_ONLY</textarea>
<fieldset aria-hidden="true"><legend></legend></fieldset></div></div></div>'''


def fixture_html(category='Bedroom', *, rooms=2):
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic room fixture</title>
<style>.PrivateSwitchBase-input{opacity:0;position:absolute;display:block;visibility:visible;width:42px;height:24px}
.MuiFormControlLabel-root,.MuiCheckbox-root,.MuiFormControlLabel-label{display:inline-block}</style>
<main><h1>Synthetic editor</h1><input id="feature-bedroom-input" value="2"></main>
<div role="dialog" aria-label="''' + TITLES[category] + '"><h2>' + TITLES[category] + '''</h2>
<div class="MuiDialogContent-root">''' + ''.join(room(i, category) + '<br>' for i in range(1, rooms + 1)) + '''</div>
<div class="MuiDialogActions-root"><button type="button">Add option</button><button type="button">Done</button></div></div>
<div class="MuiModal-hidden" aria-hidden="true" style="visibility:hidden"><div role="dialog" aria-label="Set bedrooms options">
<div class="MuiDialogContent-root"><div><h6>Bedroom 99</h6><input value="SYNTHETIC_HIDDEN_MODAL"></div></div></div></div></html>'''


TITLE_SETUP = '''for (const block of document.querySelectorAll('[data-fixture^=room-]')) {
 const title=block.firstElementChild, [noun, number]=title.textContent.split(' ');
 title.replaceChildren(document.createTextNode(noun), document.createTextNode(number));
}'''
