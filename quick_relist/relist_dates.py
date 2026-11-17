"""Operator-requested relist dates, separate from the immutable source copy."""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from .form_copy import CopyStopped

RULE = 'relist-six-calendar-months/v1'
SA_TIME = timezone(timedelta(hours=2), 'Africa/Johannesburg')


def relist_today(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(SA_TIME).date()


def six_months_after(day):
    month = day.month + 6
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def date_policy(particulars, day):
    if type(day) is not date or particulars.get('mode') not in ('sale', 'rent'):
        raise CopyStopped('The relist date or listing type is invalid.')
    controls = [c for c in particulars['controls'] if c['kind'] == 'date']
    original = {c['key']: c['value'] for c in controls}
    required = {'date_listed', 'on_market_since', 'expiry_date'}
    required.update(('mandate_signed_date', 'mandate_expiry') if particulars['mode']=='sale'
                    else ('occupation_date',))
    if len(original) != len(controls) or not required.issubset(original):
        raise CopyStopped('The source dates are incomplete or ambiguous.')
    try:
        if any(date.fromisoformat(value).isoformat() != value for key,value in original.items()
               if not (particulars['mode']=='sale' and key in ('mandate_signed_date','mandate_expiry') and value=='')):
            raise ValueError
    except (ValueError, TypeError):
        raise CopyStopped('A source date is invalid.') from None
    replacement = dict(original)
    replacement.update(date_listed=day.isoformat(), on_market_since=day.isoformat(),
                       expiry_date=six_months_after(day).isoformat())
    if particulars['mode'] == 'sale':
        replacement.update(mandate_signed_date=replacement['date_listed'],
                           mandate_expiry=replacement['expiry_date'])
    return {'rule':RULE, 'effective_date':day.isoformat(), 'mode':particulars['mode'],
            'original_dates':original, 'replacement_dates':replacement}


def apply_relist_dates(plan, previous, day):
    events = [e['data'] for e in (previous or {}).get('events', []) if e['kind']=='date_policy']
    if previous and not events:
        # A legacy interrupted run was prepared with its original dates. Never
        # silently change the replacement contract during recovery.
        return None
    if events:
        if any(e != events[0] for e in events):
            raise CopyStopped('The recorded relist dates disagree. Review the existing run.')
        try:
            day = date.fromisoformat(events[0]['effective_date'])
        except (ValueError, TypeError, KeyError):
            raise CopyStopped('The recorded relist date is invalid.') from None
    policy = date_policy(plan['particulars'], day)
    if events and events[0] != policy:
        raise CopyStopped('The recorded date plan no longer matches the source.')
    for control in plan['particulars']['controls']:
        if control['kind'] == 'date':
            control['value'] = policy['replacement_dates'][control['key']]
    plan['date_policy'] = policy
    return policy
