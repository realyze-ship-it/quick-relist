"""Read actual Hub portal columns; pending moderation is never a failed click."""
import re
import time

from .form_copy import CopyStopped

# Live Hub controls verified to open a question before submitting a withdrawal.
TWO_STEP_UNPUBLISH = frozenset(('p24', 'pp', 'global'))

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
 if(![headers.length,headers.length-1].includes(portalIds.length))return null;
 return {headers, ids:portalIds,
  idHosts:ids[0].map(c=>c.querySelector('a[href]')?.hostname?.toLowerCase() || ''),
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
    ids = list(raw['ids'])
    hosts = list(raw.get('idHosts', [''] * len(ids)))
    if len(ids) == len(PORTALS):
        # Hub removes the website's ID cell entirely when it disables that feed.
        # The other portal IDs move left, while their action buttons stay put.
        website_buttons = {b['text'].casefold() for b in raw['columns'][1]['buttons'] if b['enabled']}
        if not result['published'] and website_buttons == {'publish to re/max website'}:
            ids.insert(1, '')
            hosts.insert(1, '')
        else:
            ids.append(None)
            hosts.append('')
    if len(ids) != len(PORTALS) + 1 or len(hosts) != len(ids):
        raise CopyStopped('The portal ID row does not match the portal headings.')
    linked_feeds = {'www.property24.com':'p24', 'www.privateproperty.co.za':'pp', 'global.remax.com':'global'}
    for index, (header, (key, publish, republish)) in enumerate(PORTALS.items(), 1):
        ident = ids[index]
        if hosts[index] in linked_feeds and linked_feeds[hosts[index]] != key:
            raise CopyStopped('A portal ID link does not match its portal column.')
        column = raw['columns'][index]
        labels = {b['text'].casefold() for b in column['buttons'] if b['enabled']}
        # Hub can move moderation from the action cell into the ID row after
        # a reload while leaving Publish enabled. Neither shape permits retry.
        pending = bool(re.search(r'in moderation|publish request sent|publication pending',
                                 column['text'] + ' ' + (ident or ''), re.I))
        valid_id = isinstance(ident, str) and bool(re.fullmatch(r'[A-Za-z0-9-]{3,80}', ident))
        active = valid_id and republish.casefold() in labels and any(s.startswith('unpublish ') for s in labels)
        result['portals'][key] = {'id': ident if valid_id else None,
            'state': 'pending' if pending else 'published' if active else 'unpublished' if ident == ''
                     and not any(s.startswith(('unpublish ', 'republish ')) for s in labels) else 'unknown',
            'can_publish': publish.casefold() in labels and not pending,
            'can_republish': republish.casefold() in labels and not pending}
        if ((ident or '').strip().casefold() == 'in moderation'
                or re.match(r'^in moderation\b', column['text'].strip(), re.I)):
            result['portals'][key]['pending_reason'] = 'moderation'
        if ident is None:
            result['portals'][key]['id_cell_omitted'] = True
    return result


def only_moderation_pending(portals):
    waiting = [p for p in portals.values() if p.get('state') != 'published']
    return bool(waiting) and all(p.get('state') == 'pending' and p.get('pending_reason') == 'moderation'
                                 for p in waiting)


def portal_snapshot(driver):
    """A single observation so callers can poll for a dialog while Hub is loading."""
    raw = driver.execute_script(READ_PORTALS)
    return normalize_portals(raw) if raw else None


def read_portals(driver, *, guard=lambda: None, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        guard()
        state = portal_snapshot(driver)
        guard()
        if state is not None:
            return state
        time.sleep(.2)
    raise CopyStopped('Hub is still checking publication. No publish or removal click was retried.')


def matches_unpublish_dialog(text, destination):
    labels = {
        'p24': ('property24', 'p24'), 'pp': ('privateproperty', 'pp'),
        'website': ('remaxwebsite', 'remaxsouthafricawebsite', 'remaxcoza', 'thewebsite', 'website'),
        'global': ('remaxglobal',), 'iol': ('iolproperty',),
        'immoafrica': ('immoafrica',), 'flow': ('flowliving',),
    }
    normalized = ' '.join(text.split()).casefold()
    question = re.fullmatch(r'unpublish are you sure you want to unpublish from ([a-z0-9 ./]+)\?\s*(?:no\s*yes|yes\s*no)', normalized)
    return bool(question and re.sub(r'[ ./]', '', question[1]) in labels[destination])


def active_destinations(state):
    if any(p['state'] in ('pending', 'unknown') for p in state['portals'].values()):
        raise CopyStopped('The original has a pending or unclear portal status. Wait before relisting it.')
    # Private JSON is stored with sorted keys; dictionary insertion order is
    # not a change in portal selection. Keep the observed UI order stable.
    active = [key for key, *_ in PORTALS.values() if state['portals'][key]['state']=='published']
    if not state['published'] or not active:
        raise CopyStopped('This source is not a confirmed active listing. Review it before relisting.')
    return active


def remaining_destinations(state, plan):
    """Only original, positively identified publications can still be withdrawn."""
    remaining = []
    for key, portal in state['portals'].items():
        if portal['state'] == 'unpublished':
            continue
        # Hub can omit the unused trailing ID cell when the website is offline.
        if (key == 'flow' and key not in plan['destinations'] and portal['state'] == 'unknown'
                and portal.get('id_cell_omitted') and portal['can_publish'] and not portal['can_republish']
                and not state['published']):
            continue
        if (portal['state'] != 'published' or key not in plan['destinations']
                or portal['id'] != plan['source_portal_ids'][key]):
            raise CopyStopped(f'The {key} portal status or ID changed. Check the original in Hub.')
        remaining.append(key)
    return remaining
