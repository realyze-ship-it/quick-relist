from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from quick_relist.form_copy import CopyStopped
from quick_relist.relist_dates import apply_relist_dates, date_policy, relist_today, six_months_after


def particulars(mode):
    dates={'date_listed':'2098-06-14','on_market_since':'2098-05-01','expiry_date':'2099-01-14'}
    dates.update({'mandate_signed_date':'2098-05-15','mandate_expiry':'2099-01-14'} if mode=='sale'
                 else {'occupation_date':'2099-12-01'})
    return {'mode':mode,'controls':[{'key':k,'kind':'date','value':v} for k,v in dates.items()]
            +[{'key':'rental_price' if mode=='rent' else 'selling_price','kind':'text','value':'5000'}]}


@pytest.mark.parametrize('mode',['sale','rent'])
def test_new_run_changes_only_the_requested_dates_and_preserves_source(mode):
    source=particulars(mode)
    untouched=deepcopy(source)
    plan={'particulars':deepcopy(source)}
    policy=apply_relist_dates(plan,None,date(2099,11,13))
    values={c['key']:c['value'] for c in plan['particulars']['controls']}
    assert values['date_listed']==values['on_market_since']=='2099-11-13'
    assert values['expiry_date']=='2100-05-13'
    if mode=='sale':
        assert values['mandate_signed_date']==values['date_listed']
        assert values['mandate_expiry']==values['expiry_date']
    else:
        assert values['occupation_date']=='2099-12-01'
    assert source==untouched
    assert plan['particulars']['controls'][-1]==source['controls'][-1]
    assert policy['original_dates']['date_listed']=='2098-06-14'


@pytest.mark.parametrize('start,expiry',[
    ('2099-08-31','2100-02-28'),('2099-10-31','2100-04-30'),
    ('2099-12-31','2100-06-30'),('2095-08-31','2096-02-29'),
    ('2096-02-29','2096-08-29'),('2099-11-13','2100-05-13')])
def test_six_calendar_months_not_180_days(start,expiry):
    assert six_months_after(date.fromisoformat(start)).isoformat()==expiry


def test_local_day_is_south_africa_even_before_utc_midnight():
    assert relist_today(datetime(2099,11,12,22,30,tzinfo=timezone.utc))==date(2099,11,13)
    assert relist_today(datetime(2099,11,12,21,30,tzinfo=timezone.utc))==date(2099,11,12)


def test_resume_reuses_the_recorded_plan_and_legacy_runs_keep_their_original_dates():
    source=particulars('rent')
    policy=date_policy(source,date(2099,11,13))
    old={'events':[{'kind':'date_policy','data':policy}]}
    plan={'particulars':deepcopy(source)}
    assert apply_relist_dates(plan,old,date(2099,11,20))==policy
    legacy={'particulars':deepcopy(source)}
    assert apply_relist_dates(legacy,{'events':[]},date(2099,11,20)) is None
    assert legacy['particulars']==source


def test_changed_source_or_corrupt_recorded_plan_is_not_silently_recalculated():
    source=particulars('rent')
    policy=date_policy(source,date(2099,11,13))
    source['controls'][0]['value']='2098-07-14'
    with pytest.raises(CopyStopped,match='no longer matches'):
        apply_relist_dates({'particulars':source},{'events':[{'kind':'date_policy','data':policy}]},date(2099,11,20))


def test_missing_occupation_date_is_never_invented():
    source=particulars('rent')
    source['controls']=[c for c in source['controls'] if c['key']!='occupation_date']
    with pytest.raises(CopyStopped,match='incomplete'):
        date_policy(source,date(2099,11,13))
