"""Drive the production withdrawal controls against a disposable local Hub-shaped page."""
import os
from types import SimpleNamespace

import pytest

from quick_relist import live_relist_ui as live
from quick_relist.portal_ui import PORTALS
from test_editor_recheck_chrome import serve_synthetic_html


@pytest.mark.skipif(os.environ.get('QUICK_RELIST_CHROME_TESTS') != '1', reason='Native Chrome check is opt-in')
def test_all_portal_controls_wait_for_an_exact_dialog_or_a_direct_removal(tmp_path, monkeypatch):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()
    options.binary_location = os.environ['QUICK_RELIST_CHROME_BINARY']
    for argument in ('--headless=new','--no-first-run','--disable-dev-shm-usage','--no-sandbox'):
        options.add_argument(argument)
    service = Service(os.environ['QUICK_RELIST_CHROMEDRIVER'])
    names = ['RE/MAX website','Property24','Private Property','RemaxGlobal','IOLProperty','ImmoAfrica','FlowLiving']
    columns = [[h,*v,name] for (h,v),name in zip(PORTALS.items(), names, strict=True)]
    ids = {key:str(1000+i) for i, (key, _, _) in enumerate(PORTALS.values())}
    html = '<main><h4>RXCU1000</h4><button role="tab" aria-selected="true">Portals</button><table></table></main>'
    with serve_synthetic_html(html) as url, webdriver.Chrome(service=service, options=options) as driver:
        driver.get(url)
        driver.execute_script("""
            const cols=arguments[0], ids=arguments[1], main=document.querySelector('main');
            window.clicks=[];
            function render(){
              const row=(label,cells)=>'<tr><td>'+label+'</td>'+cells.map(x=>'<td>'+x+'</td>').join('')+'</tr>';
              const cell=([h,k,pub,rep])=>ids[k]
                ? '<button>'+rep+'</button><button data-portal="'+k+'">'+pub.replace('Publish to','Unpublish from')+'</button>'
                : '<button>'+pub+'</button>';
              const idCells=cols.filter(c=>c[1]!=='website'||ids.website).map(c=>ids[c[1]]||'');
              document.querySelector('table').innerHTML='<thead><tr><th>Listing Detail</th>'+
                cols.map(c=>'<th>'+c[0]+'</th>').join('')+'</tr></thead><tbody>'+row('Portals IDs',idCells)+
                row('Is published? '+(ids.website?'Yes':'No'),[])+row('<button>Remove from all portals</button>',cols.map(cell))+'</tbody>';
              for(const btn of document.querySelectorAll('[data-portal]')) btn.onclick=()=>{
                const key=btn.dataset.portal;
                window.clicks.push('open '+key);
                if(key==='website'){delete ids[key];render();return}
                setTimeout(()=>{
                  const dialog=document.createElement('div');
                  dialog.setAttribute('role','dialog');
                  const name=cols.find(c=>c[1]===key)[4];
                  dialog.innerHTML='<h2>Unpublish</h2><p>Are you sure you want to unpublish from '+name+'?</p><button>No</button><button>Yes</button>';
                  main.setAttribute('aria-hidden','true');
                  document.body.append(dialog);
                  dialog.lastElementChild.onclick=()=>{
                    window.clicks.push('yes '+key);
                    setTimeout(()=>{delete ids[key];dialog.remove();main.removeAttribute('aria-hidden');render()},350);
                  };
                },100);
              };
            }
            render();
        """, columns, ids)
        target = {'handle':driver.current_window_handle, 'listing_id':'1000', 'reference':'RXCU1000'}
        def fixture_route(value):
            assert value == url
            return {'listing_id':'1000','page_key':'portals'}
        monkeypatch.setattr(live, 'parse_editor_url', fixture_route)
        hub = SimpleNamespace(driver=driver, has_unresolved_editor_opening=lambda:False)
        def verify(reference, listing_id):
            assert reference == 'RXCU1000' and listing_id == '1000'
            assert driver.find_element('css selector','main').get_attribute('aria-hidden') != 'true'
        hub.verify_editor_reference = verify
        ui = live.HubRelistUI(hub, tmp_path/'media', clipboard=None)
        ui.source = target
        stages = []
        def record(key, portal_id, stage='open'):
            assert portal_id == ids[key]
            stages.append((stage,key))
        receipt = ui.unpublish({'source_reference':'RXCU1000','source_listing_id':'1000',
            'source_portal_ids':ids,'destinations':list(ids)}, record)
        expected = [key for key in ids if key != 'website']
        assert driver.execute_script('return window.clicks') == [
            action+' '+key for key in expected for action in ('open','yes')] + ['open website']
        assert stages == [(stage,key) for key in expected for stage in ('open','prompt','yes')] + [('open','website')]
        assert receipt['observed']['published'] is False
        assert all(p['state']=='unpublished' for p in receipt['observed']['portals'].values())
