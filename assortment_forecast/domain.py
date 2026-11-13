from collections import Counter, defaultdict
import math
from statistics import mean, median


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('数值必须为有限实数')
    return value


def selected(value, group):
    return not group or value == group


def latest(rows, key, revision, visible, cutoff):
    result = {}
    for row in rows:
        if row[visible] > cutoff:
            continue
        previous = result.get(row[key])
        if previous is None or row[revision] > previous[revision]:
            result[row[key]] = row
    return list(result.values())


def output(rows, summary, notes):
    return {'rows': rows, 'summary': summary, 'notes': notes}


def run(data, options):
    cutoff, group = options['cutoff'], options['group']
    grouped = defaultdict(list)
    missing = Counter()
    for sale in data['sales']:
        if sale['week'] > cutoff or sale['known_at'] > cutoff or not selected(sale['region'], group):
            continue
        key = (sale['product'], sale['region'])
        if sale['observed'] and sale['quantity'] is not None:
            if number(sale['quantity']) < 0:
                raise ValueError('销量不能为负')
            grouped[key].append(sale)
        else:
            missing[key] += 1
    rows = []
    for product, region in sorted(set(grouped) | set(missing)):
        sales = sorted(grouped[(product, region)], key=lambda row: row['week'])
        quantities = [row['quantity'] for row in sales]
        memberships = [row for row in data['membership'] if row['product'] == product and row['known_at'] <= cutoff and row['start'] <= cutoff and (row['end'] is None or cutoff < row['end'])]
        category = memberships[-1]['category'] if memberships else None
        rows.append(dict(product=product, region=region, category=category, total=sum(quantities), observations=len(quantities), missing=missing[(product, region)], average=mean(quantities) if quantities else None, recent=[{'time': row['week'], 'value': row['quantity']} for row in sales[-12:]]))
    return output(rows, {'销量': sum(row['total'] for row in rows), '有效观测': sum(row['observations'] for row in rows), '未知观测': sum(missing.values())}, ['这里只展示已知销量，不代表未满足需求。'])
