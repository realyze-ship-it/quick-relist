"""Verify a rate corrected by the operator on one existing Hub sale."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def money(value):
    return Decimal(value.replace(',', '').strip())


def cents(value):
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def reviewed_deal(source, saved, review):
    """Return expected saved values only if the correction explains every change."""
    wanted = deepcopy(source)
    fields = {f['name']: f for f in wanted['fields']}
    observed = {f['name']: f['value'] for f in saved['fields']}
    try:
        amount = money(review['amount'])
        if amount <= 0 or money(observed['total_commission_value']) != amount:
            return None
        if money(observed['total_commission_percentage']) != money(review['rate']):
            return None
        for key in ('total_commission_value', 'total_commission_percentage'):
            fields[key]['value'] = observed[key]
        def rows(page):
            table, = [t for t in page['tables'] if t['rows'] and t['rows'][0][0] == 'Agent']
            return table['rows']
        original, actual = rows(wanted), rows(saved)
        if len(original) != len(actual) or original[0] != actual[0]:
            return None
        by_name = {r[0]: r for r in actual[1:-1]}
        totals = [Decimal(0)] * 4
        precise = [Decimal(0)] * 4
        for row in original[1:-1]:
            other = by_name[row[0]]
            if len(other) != 9 or len(row) != 9 or row[:4] != other[:4] or row[8] != other[8]:
                return None
            old_gross = money(row[4])
            if old_gross <= 0:
                return None
            gross = amount * money(row[3]) / 100
            office = gross * money(row[5]) / old_gross
            brand = gross * money(row[6]) / old_gross
            exact = [gross, office, brand, gross - office - brand]
            values = list(map(money, other[4:8]))
            if values[:3] != [cents(x) for x in exact[:3]]:
                return None
            if values[3] not in (cents(exact[3]), values[0] - values[1] - values[2]):
                return None
            row[4:8] = other[4:8]
            totals = [a + b for a, b in zip(totals, values)]
            precise = [a + b for a, b in zip(precise, exact)]
        last = actual[-1]
        if len(last) != 7 or last[:2] != original[-1][:2] or totals[0] != amount:
            return None
        if any(money(v) not in (total, cents(raw)) for v, total, raw in zip(last[2:6], totals, precise)):
            return None
        original[-1][2:6] = last[2:6]
    except (ArithmeticError, AttributeError, IndexError, KeyError, TypeError, ValueError, InvalidOperation):
        return None
    return wanted
