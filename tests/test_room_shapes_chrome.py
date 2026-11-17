"""Opt-in native localhost checks of the exact room extraction script.

Existing explicit driver only, disposable synthetic profile, no installation or
Hub access. The production source URL guard must continue rejecting localhost.
Without QUICK_RELIST_CHROME_TESTS=1 the class skips before browser/server setup.
"""
import json
import unittest
from quick_relist.capture import READ_PAGE_SCRIPT, CaptureSafetyError, parse_editor_url
from room_shape_fixture import SECTIONS, TITLE_SETUP, fixture_html
import test_editor_recheck_chrome as native_fixture


class RoomShapesChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native_fixture.EditorIdentityChromeTests.setUpClass.__func__(cls)

    def read_fixture(self, *, category='Bedroom', rooms=2, setup='', denied=None):
        with native_fixture.serve_synthetic_html(fixture_html(category, rooms=rooms)) as url:
            self.driver.get(url)
            with self.assertRaises(CaptureSafetyError):
                parse_editor_url(url)
            self.driver.execute_script(TITLE_SETUP + setup)
            self.driver.execute_script('''
              const denied=()=>{throw new Error('Unexpected side effect or excluded value read');};
              for(const key of ['fetch','XMLHttpRequest','WebSocket','open']) window[key]=denied;
              for(const key of ['localStorage','sessionStorage']) Object.defineProperty(window,key,{get:denied,configurable:true});
              Object.defineProperty(document,'cookie',{get:denied,set:denied,configurable:true});
              HTMLElement.prototype.click=denied;
              HTMLFormElement.prototype.submit=denied;HTMLFormElement.prototype.requestSubmit=denied;
              if(arguments[0]){
                const controls=[...document.querySelectorAll(arguments[0])];
                if(!controls.length) throw new Error('Missing synthetic excluded control');
                for(const control of controls) Object.defineProperty(control,'value',{get:denied,set:denied});
              }
            ''', denied)
            signature = '''const ig=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').get;
              const tg=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').get;
              return [document.documentElement.outerHTML,location.href,[...document.querySelectorAll('input,textarea')]
                .map(e=>[e.id,(e.tagName==='INPUT'?ig:tg).call(e),e.checked,e.indeterminate,e.disabled,e.readOnly])];'''
            before = self.driver.execute_script(signature)
            result = self.driver.execute_script(READ_PAGE_SCRIPT)
            self.assertEqual(self.driver.execute_script(signature), before)
            self.assertEqual(result['href'], url)
            self.assertEqual(result['ready_state'], 'complete')
            return result

    def unresolved(self, raw):
        self.assertEqual(raw['dialogs'][0]['room_groups'], [])
        self.assertIn('room_group_structure_unresolved', raw['limitations'])

    def test_five_category_layouts_preserve_repeated_ids_and_raw_checkbox_state(self):
        for category in SECTIONS:
            with self.subTest(category=category):
                raw = self.read_fixture(category=category, setup='''
                  const fields=[...document.querySelectorAll('input[name=has_built_in_cupboards]')];
                  fields[0].checked=false;fields[1].checked=true;fields[0].value=' 0007 ';fields[1].value='007';
                  fields[0].disabled=true;fields[0].readOnly=true;fields[0].setAttribute('data-indeterminate','false');
                ''')
                self.assertEqual(len(raw['dialogs']), 1)
                groups = raw['dialogs'][0]['room_groups']
                self.assertEqual([g['title'] for g in groups], [category+' 1',category+' 2'])
                self.assertEqual([len(g['fields']) for g in groups], [len(SECTIONS[category])+1]*2)
                fields = [g['fields'][0] for g in groups]
                self.assertEqual([f['id'] for f in fields], ['has_built_in_cupboards']*2)
                self.assertEqual([f['label'] for f in fields], ['Synthetic has_built_in_cupboards 1','Synthetic has_built_in_cupboards 2'])
                self.assertEqual([f['checked'] for f in fields], [False,True])
                self.assertEqual([f['value'] for f in fields], [' 0007 ','007'])
                self.assertTrue(fields[0]['disabled'] and fields[0]['read_only'])
                self.assertTrue(all(f['observed_room_checkbox_proxy'] for f in fields))
                self.assertFalse(any(g['mapping_verified'] for g in groups))
                self.assertEqual(groups[0]['fields'][-1]['value'], '  Synthetic room 1\n  Second line  ')
                self.assertFalse(raw['limitations'])
                self.assertNotIn('SYNTHETIC_MEASURE_ONLY', json.dumps(raw))
                self.assertNotIn('SYNTHETIC_HIDDEN_MODAL', json.dumps(raw))

    def test_missing_or_retagged_subsection_headings_cannot_fall_back_to_legacy(self):
        for operation in ['h.remove()', "const p=document.createElement('p');p.textContent=h.textContent;h.replaceWith(p)"]:
            with self.subTest(operation=operation):
                raw = self.read_fixture(rooms=1, setup="for(const h of [...document.querySelector('[data-fixture=room-1]').querySelectorAll('h6')].slice(1)){"+operation+'}',
                    denied='input.PrivateSwitchBase-input')
                self.unresolved(raw)

    def test_unknown_roles_and_editable_subtrees_are_unresolved(self):
        for mutation in ["block.setAttribute('role','checkbox')",
            "block.children[1].insertAdjacentHTML('beforeend','<div role=switch aria-checked=mixed>Unknown switch</div>')",
            "block.children[1].insertAdjacentHTML('beforeend','<div contenteditable=plaintext-only>Unknown editor</div>')"]:
            with self.subTest(mutation=mutation):
                self.unresolved(self.read_fixture(rooms=1, setup="const block=document.querySelector('[data-fixture=room-1]');"+mutation,
                    denied='input.PrivateSwitchBase-input'))

    def test_mixed_states_are_not_flat_booleans_even_on_early_structural_failure(self):
        for state in ['input.indeterminate=true',"input.setAttribute('data-indeterminate','true')",
                      "input.parentElement.parentElement.lastElementChild.setAttribute('aria-checked','mixed')"]:
            for extra in ['',"document.querySelector('.MuiDialogContent-root').insertAdjacentHTML('beforeend','<div class=MuiDialogContent-root></div>');"]:
                with self.subTest(state=state,extra=extra):
                    raw = self.read_fixture(rooms=1, setup=extra+"const input=document.querySelector('input.PrivateSwitchBase-input');input.style.opacity='1';input.value='SYNTHETIC_PRIVATE_STATE';"+state,
                        denied='input.PrivateSwitchBase-input')
                    self.unresolved(raw)
                    self.assertNotIn('SYNTHETIC_PRIVATE_STATE', json.dumps(raw))
                    self.assertFalse(any(f['name']=='has_built_in_cupboards' for f in raw['dialogs'][0]['fields']))

    def test_duplicate_identifiers_and_ancestor_refusal_never_accept_partial_groups(self):
        for mutation in ["const input=document.querySelector('input[name=has_open_plan]');input.id=input.name='has_built_in_cupboards';",
            "document.querySelector('.MuiDialogContent-root').setAttribute('role','group')",
            "document.querySelector('[role=dialog]').setAttribute('aria-checked','mixed')"]:
            with self.subTest(mutation=mutation):
                self.unresolved(self.read_fixture(rooms=1, setup=mutation, denied='input.PrivateSwitchBase-input'))

    def test_hidden_and_credential_values_remain_unread(self):
        raw = self.read_fixture(rooms=1, setup='''
          document.querySelector('main').insertAdjacentHTML('beforeend','<input id=private-hidden type=hidden value=SYNTHETIC_PRIVATE_HIDDEN>');
          const input=document.querySelector('input.PrivateSwitchBase-input');input.id=input.name='credential_token';input.value='SYNTHETIC_PRIVATE_CREDENTIAL';
        ''', denied='#private-hidden,input.PrivateSwitchBase-input')
        self.unresolved(raw)
        self.assertNotIn('SYNTHETIC_PRIVATE_', json.dumps(raw))


if __name__ == '__main__':
    unittest.main()
