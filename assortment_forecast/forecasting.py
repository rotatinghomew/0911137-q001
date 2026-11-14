"""分层补货预测、滚动回测与替代情景。

口径：
- 只用分析时点前已发生（week <= cutoff）且当时已获知（known_at <= cutoff）的销售记录；
- observed=false 是缺记录，不按零销量补；observed=true 且 quantity=0 才是实际零销量；
- 回测在每个历史起点重建当时可见的商品归属、上市与停售状态，不用未来信息解释过去；
- 各层点值由逐序列预测自下而上汇总，因此可加；聚合区间按回测残差相关合成，
  不是把逐序列区间端点相加。
"""

from collections import defaultdict
import math
from statistics import mean, stdev


Z80 = 1.2815515655446004
INTERVAL = 0.8
MIN_HISTORY = 4
BACKTEST_ORIGINS = 6
BACKTEST_HORIZON = 4
MAX_HORIZON = 8
DEFAULT_HORIZON = 4
MODEL_ORDER = ('mean8', 'weighted', 'mean4', 'naive', 'trend8')
MODEL_LABELS = {
    'mean8': '近8周均值',
    'weighted': '近12周指数平滑',
    'mean4': '近4周均值',
    'naive': '最近一周',
    'trend8': '近8周线性趋势',
}
STATUS_LABELS = {
    'active': '在售',
    'cold_start': '冷启动',
    'missing': '缺记录',
    'no_history': '缺历史',
    'inactive': '停售',
    'not_launched': '未上市',
}


def _quantity(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('销量必须为有限非负数')
    return float(value)


def _variance(values):
    return stdev(values) if len(values) >= 2 else None


def _selected(value, group):
    return not group or value == group


def _rmse(values):
    return math.sqrt(mean(value * value for value in values)) if values else None


def _rmse_map(mapping):
    return _rmse([value for origin in mapping.values() for value in origin.values()])


def _status_at(products, product, cutoff):
    """商品主档缺失时按在售处理并由序列自身判断有无历史；不把缺失主档当成停售。"""
    row = next((item for item in products if item['product'] == product), None)
    if row is None:
        return 'active'
    if row.get('launched_at') is not None and row['launched_at'] > cutoff:
        return 'not_launched'
    inactive = row.get('inactive_from')
    if inactive is not None and inactive <= cutoff:
        return 'inactive'
    return 'active'


def _category_at(memberships, product, cutoff):
    """分析时点可见且已生效的归属；known_at 晚于时点的归属不能解释当时历史。"""
    active = [row for row in memberships if row['product'] == product and row['known_at'] <= cutoff and row['start'] <= cutoff and (row['end'] is None or cutoff < row['end'])]
    return max(active, key=lambda row: (row['start'], row['known_at']))['category'] if active else None


def _planned_category(memberships, product, cutoff):
    """已获知、但可能尚未开始的归属，仅用于向前看的情景；不用于解释历史。"""
    active = [row for row in memberships if row['product'] == product and row['known_at'] <= cutoff and (row['end'] is None or cutoff < row['end'])]
    return max(active, key=lambda row: (row['start'], row['known_at']))['category'] if active else None


def visible(data, cutoff, group, end=None, known_cutoff=None):
    """返回时点可见的 observed 序列与缺记录周。

    end 限制已发生的周（回测起点），known_cutoff 限制当时已获知的记录。
    缺记录与零销量分开保存。
    """
    end = cutoff if end is None else end
    known_cutoff = cutoff if known_cutoff is None else known_cutoff
    observed = defaultdict(dict)
    missing = defaultdict(set)
    for sale in data['sales']:
        if sale['week'] < 1 or sale['week'] > end or sale['known_at'] > known_cutoff or not _selected(sale['region'], group):
            continue
        key = (sale['product'], sale['region'])
        if sale['observed'] and sale['quantity'] is not None:
            observed[key][sale['week']] = _quantity(sale['quantity'])
        else:
            missing[key].add(sale['week'])
    return observed, missing


def predict(model, values, horizon):
    if not values or horizon <= 0:
        return []
    if model == 'mean8':
        return [max(0.0, mean(values[-8:]))] * horizon
    if model == 'mean4':
        return [max(0.0, mean(values[-4:]))] * horizon
    if model == 'naive':
        return [max(0.0, values[-1])] * horizon
    if model == 'weighted':
        start = max(0, len(values) - 12)
        value = values[start]
        for item in values[start + 1:]:
            value = 0.35 * item + 0.65 * value
        return [max(0.0, value)] * horizon
    if model == 'trend8':
        window = values[-8:]
        if len(window) < 2:
            return [max(0.0, window[-1])] * horizon
        midpoint = (len(window) - 1) / 2
        denominator = sum((index - midpoint) ** 2 for index in range(len(window)))
        slope = sum((index - midpoint) * value for index, value in enumerate(window)) / denominator
        return [max(0.0, window[-1] + slope * step) for step in range(1, horizon + 1)]
    raise ValueError('未知预测方法')


def _combined(models, values, horizon):
    forecasts = [predict(model, values, horizon) for model in models]
    return [mean(item[index] for item in forecasts) for index in range(horizon)]


def _backtest(data, cutoff, group):
    """滚动起点回测，返回逐序列残差、逐模型上层计划误差与起点目录。"""
    actual, _ = visible(data, cutoff, group)
    residuals = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    plan = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    origin_category = {}
    origins = []
    for origin in range(max(1, cutoff - BACKTEST_ORIGINS), cutoff):
        history, _ = visible(data, cutoff, group, end=origin, known_cutoff=origin)
        eligible = {key: values for key, values in history.items() if _status_at(data['products'], key[0], origin) == 'active' and len(values) >= MIN_HISTORY}
        if not eligible:
            continue
        origins.append(origin)
        categories = {}
        for key in eligible:
            categories[key] = _category_at(data['membership'], key[0], origin)
            origin_category[(origin, key[0])] = categories[key]
        groups = {('全部', '全部'): set(eligible)}
        for category in sorted({value for value in categories.values() if value}):
            groups[('品类', category)] = {key for key, value in categories.items() if value == category}
        for region in sorted({key[1] for key in eligible}):
            groups[('区域', region)] = {key for key in eligible if key[1] == region}
        limit = min(BACKTEST_HORIZON, cutoff - origin)
        cache = {}
        for model in MODEL_ORDER:
            for key, values in eligible.items():
                cache[(model, key)] = predict(model, [values[week] for week in sorted(values)], BACKTEST_HORIZON)
                for step in range(1, limit + 1):
                    week = origin + step
                    if week in actual.get(key, {}):
                        residuals[key][model][origin][step] = actual[key][week] - cache[(model, key)][step - 1]
            for level, keys in groups.items():
                for step in range(1, limit + 1):
                    week = origin + step
                    usable = [key for key in keys if week in actual.get(key, {})]
                    if not usable:
                        continue
                    total = sum(actual[key][week] for key in usable)
                    forecast = sum(cache[(model, key)][step - 1] for key in usable)
                    plan[model][level][origin][step] = total - forecast
    return residuals, plan, origin_category, origins


def _select(residuals, plan, origin_category):
    """逐序列选模型：底层回测误差与所属品类的历史计划误差同时参与。"""
    chosen = {}
    for key, by_model in residuals.items():
        scores = {}
        for model, by_origin in by_model.items():
            bottom = _rmse_map(by_origin)
            if bottom is None:
                continue
            plan_values = []
            for origin in by_origin:
                category = origin_category.get((origin, key[0]))
                entry = plan.get(model, {}).get(('品类', category)) if category else None
                if entry and origin in entry:
                    plan_values.extend(entry[origin].values())
            scores[model] = {'bottom': bottom, 'plan': _rmse(plan_values)}
        if not scores:
            chosen[key] = ['mean8']
            continue
        bottom_min = min(item['bottom'] for item in scores.values())
        plan_min = min((item['plan'] for item in scores.values() if item['plan'] is not None), default=None)
        for item in scores.values():
            bottom = item['bottom'] / bottom_min if bottom_min else item['bottom']
            if plan_min is None:
                item['score'] = bottom
            elif item['plan'] is None:
                item['score'] = 0.7 * bottom + 0.3
            else:
                item['score'] = 0.7 * bottom + 0.3 * (item['plan'] / plan_min)
        ranked = sorted(scores, key=lambda model: (scores[model]['score'], MODEL_ORDER.index(model)))
        limit = scores[ranked[0]]['score'] * 1.05 + 1e-6
        chosen[key] = [model for model in ranked if scores[model]['score'] <= limit][:2]
    return chosen


def _ensemble_residuals(key, models, residuals):
    out = defaultdict(dict)
    origins = sorted({origin for model in models for origin in residuals.get(key, {}).get(model, {})})
    for origin in origins:
        steps = sorted({step for model in models for step in residuals.get(key, {}).get(model, {}).get(origin, {})})
        for step in steps:
            values = [residuals[key][model][origin][step] for model in models if step in residuals.get(key, {}).get(model, {}).get(origin, {})]
            out[origin][step] = mean(values)
    return out


def _ensemble_plan_errors(key, models, residuals, plan, origin_category):
    out = defaultdict(dict)
    origins = sorted({origin for model in models for origin in residuals.get(key, {}).get(model, {})})
    for origin in origins:
        category = origin_category.get((origin, key[0]))
        if not category:
            continue
        steps = sorted({step for model in models for step in plan.get(model, {}).get(('品类', category), {}).get(origin, {})})
        for step in steps:
            values = [plan[model][('品类', category)][origin][step] for model in models if step in plan.get(model, {}).get(('品类', category), {}).get(origin, {})]
            if values:
                out[origin][step] = mean(values)
    return out


def _step_sigma(residuals, step, fallback):
    sample = [origin[step] for origin in residuals.values() if step in origin]
    sigma = _variance(sample)
    if sigma is not None:
        return sigma, '回测残差'
    pooled = _variance([value for origin in residuals.values() for value in origin.values()])
    if pooled is not None:
        # 该步长没有足够回测样本：用合并残差按步长外推，并明确标注不是历史验证值。
        return pooled * math.sqrt(step), '回测残差外推'
    variability = _variance(fallback)
    if variability is not None:
        return variability * math.sqrt(step), '样本波动近似'
    return 0.0, '无残差'


def aggregate_sigma(sigmas, rho):
    """聚合标准差 = 各序列方差 + 按相关系数收缩的交叉项。"""
    total = sum(value * value for value in sigmas)
    cross = sum(sigmas[i] * sigmas[j] for i in range(len(sigmas)) for j in range(i + 1, len(sigmas)))
    return math.sqrt(max(0.0, total + 2.0 * rho * cross))


def _level_rho(keys, chosen, residuals):
    vectors = {}
    for key in keys:
        residuals_for_key = _ensemble_residuals(key, chosen.get(key, ['mean8']), residuals)
        points = [((origin, step), value) for origin, steps in residuals_for_key.items() for step, value in steps.items()]
        if len(points) >= 3:
            vectors[key] = dict(points)
    pairs = []
    ordered = sorted(vectors)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            common = sorted(set(vectors[left]) & set(vectors[right]))
            if len(common) < 3:
                continue
            left_values = [vectors[left][point] for point in common]
            right_values = [vectors[right][point] for point in common]
            left_mean, right_mean = mean(left_values), mean(right_values)
            numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_values, right_values))
            denominator = math.sqrt(sum((a - left_mean) ** 2 for a in left_values) * sum((b - right_mean) ** 2 for b in right_values))
            if denominator:
                pairs.append(numerator / denominator)
    if not pairs:
        return 0.0, 0
    return max(-0.2, min(1.0, mean(pairs))), len(pairs)


def _peers(series, categories, statuses, product, region):
    def usable(key):
        return key[0] != product and statuses.get(key[0]) == 'active' and len(series.get(key, {})) >= MIN_HISTORY
    same = [key for key in series if key[1] == region and categories.get(key) == categories.get((product, region)) and usable(key)]
    if same:
        return same
    wider = [key for key in series if categories.get(key) == categories.get((product, region)) and usable(key)]
    if wider:
        return wider
    return [key for key in series if key[1] == region and usable(key)]


def _cold_start(values, peers, series, chosen, residuals, horizon):
    own = mean(values)
    peer_forecasts = [_combined(chosen.get(peer, ['mean8']), [series[peer][week] for week in sorted(series[peer])], horizon) for peer in peers]
    if peer_forecasts:
        peer = [mean(item[index] for item in peer_forecasts) for index in range(horizon)]
        weight = len(values) / (len(values) + MIN_HISTORY)
        forecast = [weight * own + (1 - weight) * value for value in peer]
    else:
        peer = [own] * horizon
        forecast = [own] * horizon
    sigmas = []
    own_sigma = _variance(values) if len(values) >= 2 else abs(own) * 0.5
    for step in range(1, horizon + 1):
        samples = []
        for peer_key in peers:
            peer_residuals = _ensemble_residuals(peer_key, chosen.get(peer_key, ['mean8']), residuals)
            samples.append(_step_sigma(peer_residuals, step, list(series[peer_key].values()))[0])
        peer_sigma = mean(samples) if samples else 0.0
        own_term = own_sigma / math.sqrt(max(1, len(values))) * math.sqrt(step)
        sigmas.append(math.sqrt(peer_sigma ** 2 + own_term ** 2))
    return forecast, sigmas, len(peers)


def _level_entry(label, level_type, rows, horizon, chosen, residuals):
    members = [row for row in rows if row['status'] in {'active', 'cold_start'} and (level_type == '全部' or (level_type == '品类' and row['category'] == label) or (level_type == '区域' and row['region'] == label))]
    keys = {(row['product'], row['region']) for row in members}
    rho, pairs = _level_rho(keys, chosen, residuals)
    forecast, low, high, sigmas = [], [], [], []
    for index in range(horizon):
        total = sum(row['forecast'][index] for row in members)
        sigma = aggregate_sigma([row['sigma'][index] for row in members], rho)
        forecast.append(total)
        low.append(max(0.0, total - Z80 * sigma))
        high.append(total + Z80 * sigma)
        sigmas.append(sigma)
    valid = [key for key in keys if residuals.get(key)]
    common = None
    ensembles = {}
    for key in valid:
        ensembles[key] = _ensemble_residuals(key, chosen.get(key, ['mean8']), residuals)
        available = {(origin, step) for origin, steps in ensembles[key].items() for step in steps}
        common = available if common is None else common & available
    aggregate = defaultdict(dict)
    for origin, step in sorted(common or ()):
        aggregate[origin][step] = sum(ensembles[key][origin][step] for key in valid)
    points = sum(len(steps) for steps in aggregate.values())
    excluded_rows = [f"{row['name']}／{row['region']}（{row['status_label']}）" for row in rows if row['status'] not in {'active', 'cold_start'} and (level_type == '全部' or (level_type == '品类' and row['category'] == label) or (level_type == '区域' and row['region'] == label))]
    return {
        'label': label,
        'level': level_type,
        'series': len(members),
        'excluded': excluded_rows,
        'forecast': forecast,
        'low': low,
        'high': high,
        'sigma': sigmas,
        'correlation': round(rho, 3),
        'correlation_pairs': pairs,
        'plan_rmse': round(_rmse_map(aggregate), 3) if points else None,
        'plan_points': points,
    }


def _scenario(data, options, rows, levels, series, chosen, residuals, cutoff, horizon):
    supplied = options.get('scenario')
    if not supplied:
        return None
    source, target, share = supplied['from'], supplied['to'], supplied['share']
    try:
        share = float(share)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('替代比例须为数值') from None
    if not 0 < share <= 1:
        raise ValueError('替代比例须大于 0 且不超过 1')
    products = {row['product']: row for row in data['products']}
    if source not in products or target not in products:
        raise ValueError('情景商品不存在')
    if source == target:
        raise ValueError('替代情景需要两个不同商品')
    statuses = {row['product']: _status_at(data['products'], row['product'], cutoff) for row in data['products']}
    if statuses[target] == 'inactive':
        raise ValueError('替代情景的新品已在分析时点停售')
    rows_by_key = {(row['product'], row['region']): row for row in rows}
    regions = sorted({row['region'] for row in rows})
    transfers = {}
    notes = []
    for region in regions:
        row = rows_by_key.get((source, region))
        if row is None:
            continue
        if row['status'] in {'active', 'cold_start'}:
            forecast, sigmas, basis = row['forecast'], row['sigma'], '当前预测'
            removable = True
        elif row['status'] == 'inactive':
            stop = products[source].get('inactive_from')
            past = {week: value for week, value in series.get((source, region), {}).items() if stop is None or week < stop}
            if len(past) < MIN_HISTORY:
                notes.append(f'{source}／{region} 停售前有效历史不足 {MIN_HISTORY} 周，无法量化替代来源。')
                continue
            values = [past[week] for week in sorted(past)]
            forecast = [mean(predict(model, values, horizon)[step] for model in ('mean8', 'weighted')) for step in range(horizon)]
            own_sigma = _variance(values) or 0.0
            sigmas = [own_sigma * math.sqrt(step) for step in range(1, horizon + 1)]
            basis = '停售前反事实'
            removable = False
            distance = (cutoff + 1) - max(past)
            notes.append(f'{source}／{region} 已于时点 {stop} 停售，情景按停售前 {len(past)} 周反事实外推；最后观察距分析时点 {distance} 周。')
        else:
            notes.append(f'{source}／{region} 当前为{row["status_label"]}，没有可转移的需求依据。')
            continue
        transfers[region] = {
            'forecast': forecast,
            'amount': [share * value for value in forecast],
            'sigma': sigmas,
            'basis': basis,
            'removable': removable,
        }
    if not transfers:
        return {
            'from': source,
            'to': target,
            'share': share,
            'source_status': statuses[source],
            'target_status': statuses[target],
            'source_category': _planned_category(data['membership'], source, cutoff),
            'target_category': _planned_category(data['membership'], target, cutoff),
            'transfers': {},
            'transfer_total': [0.0] * horizon,
            'levels': [],
            'supported': False,
            'notes': notes + ['情景没有可量化的转移量。'],
        }

    source_category = _planned_category(data['membership'], source, cutoff)
    target_category = _planned_category(data['membership'], target, cutoff)

    def member(entry, product, region):
        if entry['level'] == '全部':
            return True
        if entry['level'] == '区域':
            return entry['label'] == region
        return _planned_category(data['membership'], product, cutoff) == entry['label']

    target_future = statuses[target] == 'not_launched'
    counterfactual = any(item['basis'] == '停售前反事实' for item in transfers.values())
    out_of_range = horizon > BACKTEST_HORIZON
    compared = []
    for entry in [levels['total'], *levels['categories'], *levels['regions']]:
        delta = [0.0] * horizon
        source_inside = target_inside = False
        for region, transfer in transfers.items():
            source_here = member(entry, source, region)
            target_here = member(entry, target, region)
            source_inside = source_inside or source_here
            target_inside = target_inside or target_here
            for step in range(horizon):
                removed = transfer['amount'][step] if source_here and transfer['removable'] else 0.0
                added = transfer['amount'][step] if target_here else 0.0
                delta[step] += added - removed
        problems = []
        if not source_inside and not target_inside:
            support = '不涉及'
        else:
            if counterfactual:
                problems.append('停售反事实')
            if target_future and target_inside:
                problems.append('新品未上市')
            if out_of_range:
                problems.append('超出回测步长')
            if source_inside and target_inside:
                if not problems:
                    support = '可比较（层内替代）'
                else:
                    support = '方向性：' + '、'.join(problems)
            elif target_inside and not problems:
                support = '可比较（新增来源）'
            elif source_inside and not problems:
                support = '可比较（仅移出）'
            else:
                support = '方向性：' + '、'.join(problems)
        compared.append({
            'level': entry['level'],
            'label': entry['label'],
            'baseline': entry['forecast'],
            'scenario': [value + change for value, change in zip(entry['forecast'], delta)],
            'delta': delta,
            'support': support,
        })

    product_details = []
    for region in regions:
        transfer = transfers.get(region)
        source_row = rows_by_key.get((source, region))
        target_row = rows_by_key.get((target, region))
        source_active = source_row is not None and source_row['status'] in {'active', 'cold_start'}
        detail = {
            'region': region,
            'source': None if not source_active else {
                'forecast': source_row['forecast'],
                'scenario': [value - (share * transfer['forecast'][step] if transfer and transfer['removable'] else 0.0) for step, value in enumerate(source_row['forecast'])],
            },
            'target': None if target_row is None or target_row['forecast'][0] is None else {
                'forecast': target_row['forecast'],
                'scenario': [value + (share * transfer['forecast'][step] if transfer else 0.0) for step, value in enumerate(target_row['forecast'])],
            },
        }
        if detail['source'] or detail['target']:
            product_details.append(detail)

    if counterfactual:
        notes.append('旧品已停售且不在基线预测中；情景增加的是转移量，不代表已经实现的需求。')
    if target_future:
        notes.append(f'{target} 在时点 {cutoff} 尚未上市，转移量沿用旧品水平；新品自身接受度与上市节奏未校准。')
    if statuses[target] == 'cold_start':
        notes.append(f'{target} 历史不足 {MIN_HISTORY} 周，基线为冷启动近似；情景转移量叠加在该近似之上。')
    if source_category == target_category and source_category:
        changed = [item['label'] for item in compared if item['level'] != '全部' and any(abs(value) > 1e-9 for value in item['delta'])]
        where = f'区域层（{ "、".join(changed) }）与单品层' if changed else '仅单品层'
        notes.append(f'旧品与新品同属品类 {source_category}，品类与总层不随替代变化；位移出现在{where}。')
    elif source_category != target_category:
        notes.append(f'旧品属品类 {source_category or "未归类"}，新品属品类 {target_category or "未归类"}；替代会同时改变两个品类层的计划，总层只在转移量可移除时保持。')
    if options.get('group'):
        notes.append(f'情景只覆盖当前业务范围 {options["group"]}。')
    if out_of_range:
        notes.append(f'预测步长 {horizon} 周超过回测可验证的 {BACKTEST_HORIZON} 周，远端只能作方向性比较。')
    return {
        'from': source,
        'to': target,
        'share': share,
        'source_status': statuses[source],
        'target_status': statuses[target],
        'source_basis': '停售前反事实' if counterfactual else '当前预测',
        'source_category': source_category,
        'target_category': target_category,
        'transfers': transfers,
        'transfer_total': [sum(transfer['amount'][index] for transfer in transfers.values()) for index in range(horizon)],
        'levels': compared,
        'products': product_details,
        'supported': True,
        'notes': notes,
    }


def build(data, options):
    cutoff, group = options['cutoff'], options['group']
    horizon = max(1, min(MAX_HORIZON, options.get('horizon', DEFAULT_HORIZON)))
    series, missing = visible(data, cutoff, group)
    residuals, plan, origin_category, origins = _backtest(data, cutoff, group)
    chosen = _select(residuals, plan, origin_category)
    products = sorted({row['product'] for row in data['products']} | {sale['product'] for sale in data['sales']})
    names = {row['product']: row['name'] for row in data['products']}
    regions = sorted({sale['region'] for sale in data['sales'] if _selected(sale['region'], group)})
    statuses = {product: _status_at(data['products'], product, cutoff) for product in products}
    categories = {product: _category_at(data['membership'], product, cutoff) for product in products}
    unknown_products = [product for product in products if product not in names]

    rows = []
    for product in products:
        for region in regions:
            key = (product, region)
            values = series.get(key, {})
            missing_weeks = sorted(missing.get(key, set()))
            state = statuses[product]
            if state != 'active':
                status = state
            elif not values:
                status = 'missing' if missing_weeks else 'no_history'
            elif len(values) < MIN_HISTORY:
                status = 'cold_start'
            else:
                status = 'active'
            prediction = [None] * horizon
            low = [None] * horizon
            high = [None] * horizon
            sigmas = [None] * horizon
            basis = []
            models = chosen.get(key, ['mean8'])
            if status == 'active':
                ordered = [values[week] for week in sorted(values)]
                prediction = _combined(models, ordered, horizon)
                ensemble = _ensemble_residuals(key, models, residuals)
                for step in range(1, horizon + 1):
                    sigma, reason = _step_sigma(ensemble, step, ordered)
                    sigmas[step - 1] = sigma
                    if reason not in basis:
                        basis.append(reason)
            elif status == 'cold_start':
                peers = _peers(series, {key: categories.get(key[0]) for key in series}, statuses, product, region)
                prediction, sigmas, peer_count = _cold_start([values[week] for week in sorted(values)], peers, series, chosen, residuals, horizon)
                basis = [f'冷启动近似（{peer_count} 条品类参照）' if peer_count else '冷启动近似（无参照）']
            if prediction[0] is not None:
                low = [max(0.0, value - Z80 * sigma) for value, sigma in zip(prediction, sigmas)]
                high = [value + Z80 * sigma for value, sigma in zip(prediction, sigmas)]
            ensemble_error = _ensemble_residuals(key, models, residuals) if status == 'active' else {}
            plan_error = _ensemble_plan_errors(key, models, residuals, plan, origin_category) if status == 'active' else {}
            rows.append({
                'product': product,
                'name': names.get(product, product),
                'region': region,
                'category': categories.get(product),
                'status': status,
                'status_label': STATUS_LABELS[status],
                'models': [MODEL_LABELS[model] for model in models] if status == 'active' else [],
                'forecast': prediction,
                'low': low,
                'high': high,
                'sigma': sigmas,
                'interval_basis': basis,
                'observations': len(values),
                'missing_weeks': missing_weeks,
                'zero_weeks': sorted(week for week, value in values.items() if value == 0),
                'validation': {
                    'series_rmse': round(_rmse_map(ensemble_error), 3) if status == 'active' and ensemble_error else None,
                    'plan_rmse': round(_rmse_map(plan_error), 3) if status == 'active' and plan_error else None,
                },
            })

    catalog_products = [
        {
            'product': product,
            'name': names.get(product, product),
            'status': statuses[product],
            'status_label': STATUS_LABELS[statuses[product]],
            'category': categories.get(product),
        }
        for product in products
    ]
    level_rows = {'total': _level_entry('全部', '全部', rows, horizon, chosen, residuals), 'categories': [], 'regions': []}
    for category in sorted({row['category'] for row in rows if row['category']}):
        level_rows['categories'].append(_level_entry(category, '品类', rows, horizon, chosen, residuals))
    for region in regions:
        level_rows['regions'].append(_level_entry(region, '区域', rows, horizon, chosen, residuals))

    scenario = _scenario(data, {**options, 'horizon': horizon}, rows, level_rows, series, chosen, residuals, cutoff, horizon)

    changes = []
    by_product = defaultdict(list)
    for row in data['membership']:
        if row['known_at'] <= cutoff and row['start'] <= cutoff:
            by_product[row['product']].append(row)
    for product, entries in sorted(by_product.items()):
        ordered = sorted(entries, key=lambda row: (row['start'], row['known_at']))
        if len({row['category'] for row in ordered}) > 1:
            changes.append({'product': product, 'from': ordered[0]['category'], 'to': ordered[-1]['category'], 'at': ordered[-1]['start'], 'known_at': ordered[-1]['known_at']})
    uncategorized = [name for product, name in names.items() if categories.get(product) is None]
    inactive = sorted({row['name'] for row in rows if row['status'] == 'inactive'})
    cold = [f"{row['name']}／{row['region']}" for row in rows if row['status'] == 'cold_start']
    zero_rows = [f"{row['name']}／{row['region']}" for row in rows if row['zero_weeks']]

    notes = [
        '预测只使用分析时点前已发生且当时已获知的记录；observed=false 是缺记录，不按零销量补，observed=true 且 quantity=0 才是实际零销量。',
        '各层点值由逐序列预测自下而上汇总，因此品类、区域与总计划可加；80% 区间按回测残差序列相关合成，不是逐序列区间端点相加。',
        f'回测使用最近 {len(origins)} 个起点（{origins[0] if origins else "无"}–{origins[-1] if origins else "无"}），每个起点按当时可见的归属与上市/停售状态计算，最多检验 {BACKTEST_HORIZON} 步。',
    ]
    for change in changes:
        notes.append(f"{change['product']} 于时点 {change['at']} 由 {change['from']} 转为 {change['to']}（时点 {change['known_at']} 起可见）；历史验证按各起点当时可见归属计算，不与当前树混用。")
    if uncategorized:
        notes.append('时点缺少可见品类归属的商品仍计入总层与区域层，但不进入品类层：' + '、'.join(sorted(set(uncategorized))) + '。')
    if unknown_products:
        notes.append('有销售记录但商品主档缺失的商品按序列自身历史处理，不能据此判断停售：' + '、'.join(unknown_products) + '。')
    if inactive:
        notes.append('停售商品不生成未来预测，停售后零销量不代表需求归零：' + '、'.join(inactive) + '。')
    if cold:
        notes.append(f'有效历史不足 {MIN_HISTORY} 周的商品按品类同区域参照做冷启动收缩，区间含小样本修正，单品水平只作方向性参考：' + '、'.join(cold) + '。')
    if horizon > BACKTEST_HORIZON:
        notes.append(f'请求步长 {horizon} 周超过回测可验证的 {BACKTEST_HORIZON} 周，远端区间是残差外推，不能视为历史验证结果。')
    if zero_rows:
        notes.append('实际零销量与缺记录分开统计，含零销量周的序列：' + '、'.join(zero_rows) + '。')
    if scenario:
        notes.extend(scenario['notes'])

    return {
        'horizon': horizon,
        'weeks': list(range(cutoff + 1, cutoff + horizon + 1)),
        'rows': rows,
        'products': catalog_products,
        'levels': level_rows,
        'scenario': scenario,
        'validation': {
            'origins': origins,
            'backtest_horizon': BACKTEST_HORIZON,
            'interval': INTERVAL,
            'min_history': MIN_HISTORY,
            'selected_models': {MODEL_LABELS[model]: sum(1 for models in chosen.values() if model in models) for model in MODEL_ORDER},
        },
        'summary': {
            '在售序列': sum(1 for row in rows if row['status'] == 'active'),
            '冷启动序列': sum(1 for row in rows if row['status'] == 'cold_start'),
            '停售序列': sum(1 for row in rows if row['status'] == 'inactive'),
            '未上市序列': sum(1 for row in rows if row['status'] == 'not_launched'),
            '缺记录序列': sum(1 for row in rows if row['status'] == 'missing'),
            '缺历史序列': sum(1 for row in rows if row['status'] == 'no_history'),
            '缺记录周': sum(len(row['missing_weeks']) for row in rows),
            '零销量周': sum(len(row['zero_weeks']) for row in rows),
        },
        'notes': notes,
    }
