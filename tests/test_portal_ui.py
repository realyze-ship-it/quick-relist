from copy import deepcopy
import pytest
from quick_relist.portal_ui import PORTALS, normalize_portals, active_destinations
from quick_relist.form_copy import CopyStopped


def raw():
    return {'headers':['Listing Detail',*PORTALS], 'ids':['Portals IDs','123456','7654321','T123456','12345-6','','',''],
        'published':'Is published? Yes','busy':False,
        'columns':[{'text':'Publish to all portals Remove from all portals','buttons':[]}] + [
            {'text':republish, 'buttons':[{'text':republish,'enabled':True},{'text':'Unpublish from '+key,'enabled':True}]}
            if index<4 else {'text':'Unpublish','buttons':[]} for index,(key,publish,republish) in enumerate(PORTALS.values())]}


def test_only_portal_ids_with_actual_active_controls_are_counted():
    state = normalize_portals(raw())
    assert active_destinations(state) == ['website','p24','pp','global']
    assert state['portals']['iol']['state']=='unpublished'


def test_saved_json_key_order_does_not_change_portal_selection():
    import json
    fresh = normalize_portals(raw())
    saved = json.loads(json.dumps(fresh, sort_keys=True))
    assert active_destinations(saved) == active_destinations(fresh)


def test_moderation_with_a_publish_button_cannot_trigger_a_second_publish():
    data=raw()
    data['ids'][3]=''
    data['columns'][3]={'text':'In moderation Publish to PP','buttons':[{'text':'Publish to PP','enabled':True}]}
    state=normalize_portals(data)
    assert state['portals']['pp']['state']=='pending'
    assert not state['portals']['pp']['can_publish']
    with pytest.raises(CopyStopped,match='pending'):
        active_destinations(state)


def test_checking_status_is_not_mistaken_for_unpublished():
    data=raw();data['columns'][2]['text']='Checking publish status...'
    assert normalize_portals(data) is None


def test_moderation_in_id_row_after_reload_does_not_allow_republication():
    data=raw()
    data['ids'][3]='In moderation'
    data['columns'][3]={'text':'Publish to PP','buttons':[{'text':'Publish to PP','enabled':True}]}
    portal=normalize_portals(data)['portals']['pp']
    assert portal['state']=='pending'
    assert portal['id'] is None
    assert not portal['can_publish']
    assert not portal['can_republish']


def test_placeholder_id_does_not_count_as_published():
    data=raw();data['ids'][2]='P24 ID'
    state=normalize_portals(data)
    assert state['portals']['p24']['state']=='unknown'


def test_missing_final_cell_does_not_hide_confirmed_removal_or_invent_a_flow_status():
    data=raw()
    data.update(published='Is published? No', ids=['Portals IDs','','','','','','',None])
    data['columns'][1]={'text':'Publish to RE/MAX website','buttons':[{'text':'Publish to RE/MAX website','enabled':True}]}
    state=normalize_portals(data)
    assert not state['published']
    assert all(state['portals'][d]['state']=='unpublished' for d in ('website','p24','pp','global'))
    assert state['portals']['flow']['state']=='unknown'
    assert state['portals']['website']['can_publish']
