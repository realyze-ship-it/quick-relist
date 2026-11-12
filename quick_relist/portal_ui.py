"""Read actual Hub portal columns; pending moderation is never a failed click."""
import re
import time

from .form_copy import CopyStopped

PORTALS = {
    'Publish to website': ('website', 'Publish to RE/MAX website', 'Republish to website'),
    'Property 24': ('p24', 'Publish to P24', 'Republish to P24'),
    'Private Property': ('pp', 'Publish to PP', 'Republish to PP'),
    'RE/MAX Global': ('global', 'Publish to RE/MAX Global', 'Republish to RE/MAX Global'),
    'IOL Property': ('iol', 'Publish to IOL Property', 'Republish to IOL Property'),
    'ImmoAfrica': ('immoafrica', 'Publish to ImmoAfrica', 'Republish to ImmoAfrica'),
    'Flow Living': ('flow', 'Publish to Flow Living', 'Republish to Flow Living'),
}

READ_PORTALS = r'''
return (() => {
 const visible=e=>!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
 const tables=[...document.querySelectorAll('main table')].filter(t=>
   t.querySelector('thead th')?.textContent.trim()==='Listing Detail');
 if(tables.length!==1)return null;
 const table=tables[0], headers=[...table.querySelectorAll('thead th')].map(e=>e.textContent.trim());
 const rows=[...table.querySelectorAll('tbody tr')].map(r=>[...r.children].filter(e=>e.tagName==='TD'));
 const ids=rows.filter(r=>r[0]?.textContent.trim()==='Portals IDs');
 const actions=rows.filter(r=>r[0]?.textContent.includes('Remove from all portals'));
 const published=rows.filter(r=>r[0]?.textContent.includes('Is published?'));
 if(ids.length!==1 || actions.length!==1 || published.length!==1 || actions[0].length!==headers.length)return null;
 const portalIds=ids[0].map(e=>e.textContent.trim());
 // Hub omits Flow Living's final ID cell on the unpublished screen.
 // Preserve this as unknown, rather than inventing a blank/removed ID.
 if(portalIds.length===headers.length-1 && headers.at(-1)==='Flow Living')portalIds.push(null);
 if(portalIds.length!==headers.length)return null;
 return {headers, ids:portalIds,
  published:published[0][0].textContent.replace(/\s+/g,' ').trim(),
  columns:actions[0].map(c=>({text:c.textContent.replace(/\s+/g,' ').trim(),
    buttons:[...c.querySelectorAll('button')].filter(visible).map(b=>({text:b.textContent.replace(/\s+/g,' ').trim(),enabled:!b.disabled}))})),
  busy:[...document.querySelectorAll('main [role="progressbar"],main [aria-busy="true"]')].some(visible)};
})();
'''


def normalize_portals(raw):
    if not raw or raw.get('headers') != ['Listing Detail', *PORTALS]:
        raise CopyStopped('The portal table layout changed. Check Hub before continuing.')
    if raw['busy'] or any(re.search(r'checking|please wait|loading', c['text'], re.I) for c in raw['columns']):
        return None
    if raw['published'] not in ('Is published? Yes', 'Is published? No'):
        return None
    result = {'published': raw['published'].endswith('Yes'), 'portals': {}}
    for index, (header, (key, publish, republish)) in enumerate(PORTALS.items(), 1):
        ident = raw['ids'][index]
        column = raw['columns'][index]
        labels = {b['text'].casefold() for b in column['buttons'] if b['enabled']}
        # Hub can move moderation from the action cell into the ID row after
        # a reload while leaving Publish enabled. Neither shape permits retry.
        pending = bool(re.search(r'in moderation|publish request sent|publication pending',
                                 column['text'] + ' ' + (ident or ''), re.I))
        valid_id = isinstance(ident, str) and bool(re.fullmatch(r'[A-Za-z0-9-]{3,80}', ident))
        active = valid_id and republish.casefold() in labels and any(s.startswith('unpublish ') for s in labels)
        result['portals'][key] = {'id': ident if valid_id else None,
            'state': 'pending' if pending else 'published' if active else 'unpublished' if ident == '' else 'unknown',
            'can_publish': publish.casefold() in labels and not pending,
            'can_republish': republish.casefold() in labels and not pending}
    return result


def read_portals(driver, *, guard=lambda: None, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        guard()
        raw = driver.execute_script(READ_PORTALS)
        guard()
        if raw:
            state = normalize_portals(raw)
            if state is not None:
                return state
        time.sleep(.2)
    raise CopyStopped('Hub is still checking publication. No publish or removal click was retried.')


def active_destinations(state):
    if any(p['state'] in ('pending', 'unknown') for p in state['portals'].values()):
        raise CopyStopped('The original has a pending or unclear portal status. Wait before relisting it.')
    # Private JSON is stored with sorted keys; dictionary insertion order is
    # not a change in portal selection. Keep the observed UI order stable.
    active = [key for key, *_ in PORTALS.values() if state['portals'][key]['state']=='published']
    if not state['published'] or not active:
        raise CopyStopped('This source is not a confirmed active listing. Review it before relisting.')
    return active
