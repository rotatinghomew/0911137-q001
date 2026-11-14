import copy
import json
import unittest

from assortment_forecast.application import ROOT
from assortment_forecast.forecasting import (
    MODEL_ORDER,
    aggregate_sigma,
    build,
    predict,
    visible,
)


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((ROOT / 'fixtures/data.json').read_text())
        self.tables = self.fixture['tables']

    def test_deterministic_and_input_not_mutated(self):
        original = copy.deepcopy(self.tables)
        options = {'cutoff': 24, 'group': '', 'horizon': 4}
        first = build(self.tables, options)
        second = build(self.tables, options)
        self.assertEqual(first, second)
        self.assertEqual(original, self.tables)
        json.dumps(first, allow_nan=False)

    def test_visible_records_respect_cutoff_and_known_at(self):
        series, missing = visible(self.tables, 24, '')
        observed_total = sum(len(values) for values in series.values())
        missing_total = sum(len(weeks) for weeks in missing.values())
        expected = sum(1 for row in self.tables['sales'] if row['observed'] and row['quantity'] is not None and row['week'] <= 24 and row['known_at'] <= 24)
        self.assertEqual(observed_total, expected)
        self.assertEqual(missing_total, sum(1 for row in self.tables['sales'] if not row['observed'] and row['week'] <= 24 and row['known_at'] <= 24))
        # 晚于时点才获知的记录不能提前参与预测。
        self.assertIn(9, series[('p1', '华东')])
        late, _ = visible(self.tables, 9, '')
        self.assertNotIn(9, late[('p1', '华东')])

    def test_aggregate_point_forecasts_are_additive(self):
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4})
        for level in [*result['levels']['categories'], *result['levels']['regions'], result['levels']['total']]:
            members = [
                row for row in result['rows']
                if row['status'] in {'active', 'cold_start'} and (
                    level['level'] == '全部'
                    or (level['level'] == '品类' and row['category'] == level['label'])
                    or (level['level'] == '区域' and row['region'] == level['label'])
                )
            ]
            for index in range(result['horizon']):
                self.assertAlmostEqual(sum(row['forecast'][index] for row in members), level['forecast'][index], places=9)

    def test_aggregate_interval_is_not_endpoint_sum(self):
        """逐序列区间端点相加会明显宽于按相关性合成的区间。"""
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4})
        total = result['levels']['total']
        for index in range(result['horizon']):
            members = [row for row in result['rows'] if row['status'] in {'active', 'cold_start'}]
            endpoint_width = sum(row['high'][index] - row['low'][index] for row in members)
            synthetic_width = total['high'][index] - total['low'][index]
            self.assertLess(synthetic_width, endpoint_width)
            self.assertGreaterEqual(total['correlation'], -0.2)
            self.assertLessEqual(total['correlation'], 1.0)

    def test_aggregate_sigma_matches_correlation_formula(self):
        sigmas = [2.0, 3.0, 5.0]
        self.assertAlmostEqual(aggregate_sigma(sigmas, 0.0), 6.164414002968976, places=9)
        self.assertAlmostEqual(aggregate_sigma(sigmas, 1.0), 10.0, places=9)
        self.assertGreaterEqual(aggregate_sigma(sigmas, -0.2), 0.0)

    def test_stopped_product_is_not_forecast(self):
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4})
        stopped = [row for row in result['rows'] if row['product'] == 'p9']
        self.assertTrue(stopped)
        for row in stopped:
            self.assertEqual(row['status'], 'inactive')
            self.assertEqual(row['forecast'], [None] * 4)
            self.assertEqual(row['low'], [None] * 4)
        self.assertIn('停售', ' '.join(result['notes']))

    def test_missing_record_and_actual_zero_are_distinct(self):
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4})
        rows = {(row['product'], row['region']): row for row in result['rows']}
        parked = rows[('p9', '华东')]
        self.assertTrue(parked['zero_weeks'])
        self.assertIn(21, parked['zero_weeks'])
        self.assertIn('zero_weeks', parked)
        self.assertNotIn(30, parked['zero_weeks'])
        # 缺记录周不进入缓冲区，也绝不作为零值观测。
        self.assertIn(14, parked['missing_weeks'])
        self.assertNotIn(14, parked['zero_weeks'])
        self.assertIn('缺记录', ' '.join(result['notes']))

    def test_known_at_hides_future_membership(self):
        # p3 的 c1 归属为 [1,17)，c2 归属 start=17 但 known_at=18：
        # 时点 17 当天既没有已生效旧归属，也还看不到新归属，应显式记为“未归类”而不是猜测。
        cutoff = 17
        result = build(self.tables, {'cutoff': cutoff, 'group': '', 'horizon': 2})
        product_rows = [row for row in result['rows'] if row['product'] == 'p3']
        self.assertTrue(product_rows)
        for row in product_rows:
            self.assertIsNone(row['category'])
        self.assertTrue(any('缺少可见品类归属' in note for note in result['notes']))
        # 时点 16 仍按当时可见的旧归属 c1 计算。
        earlier = build(self.tables, {'cutoff': 16, 'group': '', 'horizon': 2})
        for row in [row for row in earlier['rows'] if row['product'] == 'p3']:
            self.assertEqual(row['category'], 'c1')
        # 新归属获知后，层级按 c2 解释，并说明历史树变化。
        later = build(self.tables, {'cutoff': 18, 'group': '', 'horizon': 2})
        later_rows = [row for row in later['rows'] if row['product'] == 'p3']
        for row in later_rows:
            self.assertEqual(row['category'], 'c2')
        self.assertIn('由 c1 转为 c2', ' '.join(later['notes']))

    def test_backtest_uses_visible_tree_at_each_origin(self):
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4})
        origins = result['validation']['origins']
        self.assertTrue(origins)
        self.assertEqual(origins, list(range(18, 24)))
        self.assertEqual(result['validation']['backtest_horizon'], 4)

    def test_new_product_cold_start_uses_category_peers(self):
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4})
        row = next(row for row in result['rows'] if row['product'] == 'p8' and row['region'] == '华东')
        self.assertEqual(row['status'], 'cold_start')
        self.assertTrue(row['forecast'][0] is not None)
        self.assertTrue(row['interval_basis'])
        self.assertIn('冷启动', ' '.join(result['notes']))
        # 冷启动水平靠近同品类（c3）的在售序列，而不是被当成零或直接外推 2 周。
        peers = [item for item in result['rows'] if item['product'] == 'p7' and item['region'] == '华东']
        self.assertGreater(row['forecast'][0], 0)
        self.assertLess(abs(row['forecast'][0] - peers[0]['forecast'][0]), 10)

    def test_horizon_beyond_backtest_is_flagged(self):
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 7})
        self.assertEqual(result['horizon'], 7)
        self.assertTrue(any('超过回测可验证' in note for note in result['notes']))

    def test_predict_respects_non_negative(self):
        for model in MODEL_ORDER:
            self.assertTrue(all(value >= 0 for value in predict(model, [3, 1, 4, 1, 5, 9], 4)))
        self.assertEqual(predict('naive', [3], 2), [3.0, 3.0])
        self.assertEqual(predict('weighted', [5], 1), [5.0])
        self.assertEqual(predict('mean4', [], 1), [])

    def test_scenario_same_category_keeps_category_total(self):
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4, 'scenario': {'from': 'p7', 'to': 'p8', 'share': 0.5}})
        scenario = result['scenario']
        self.assertTrue(scenario['supported'])
        level = {(item['level'], item['label']): item for item in scenario['levels']}
        self.assertEqual(level[('品类', 'c3')]['delta'], [0.0] * 4)
        self.assertEqual(level[('全部', '全部')]['delta'], [0.0] * 4)
        self.assertEqual(level[('区域', '华东')]['delta'], [0.0] * 4)
        self.assertEqual(level[('区域', '华东')]['support'], '可比较（层内替代）')
        self.assertTrue(scenario['products'])
        detail = next(item for item in scenario['products'] if item['region'] == '华东')
        self.assertLess(detail['source']['scenario'][0], detail['source']['forecast'][0])
        self.assertGreater(detail['target']['scenario'][0], detail['target']['forecast'][0])

    def test_scenario_cross_category_moves_level_totals(self):
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4, 'scenario': {'from': 'p1', 'to': 'p3', 'share': 1}})
        scenario = result['scenario']
        level = {(item['level'], item['label']): item for item in scenario['levels']}
        self.assertLess(level[('品类', 'c1')]['delta'][0], 0)
        self.assertGreater(level[('品类', 'c2')]['delta'][0], 0)
        self.assertAlmostEqual(level[('全部', '全部')]['delta'][0], 0.0, places=9)
        self.assertEqual(level[('品类', 'c1')]['support'], '可比较（仅移出）')
        self.assertEqual(level[('品类', 'c2')]['support'], '可比较（新增来源）')

    def test_stopped_source_is_counterfactual_and_directional(self):
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4, 'scenario': {'from': 'p9', 'to': 'p8', 'share': 0.4}})
        scenario = result['scenario']
        self.assertTrue(scenario['supported'])
        self.assertEqual(scenario['source_basis'], '停售前反事实')
        self.assertTrue(any('反事实' in item['support'] for item in scenario['levels']))
        self.assertTrue(any('反事实' in note for note in scenario['notes']))

    def test_future_launch_scenario_is_directional(self):
        result = build(self.tables, {'cutoff': 20, 'group': '', 'horizon': 4, 'scenario': {'from': 'p7', 'to': 'p8', 'share': 0.5}})
        scenario = result['scenario']
        self.assertEqual(scenario['target_status'], 'not_launched')
        self.assertTrue(any('未上市' in item['support'] for item in scenario['levels'] if item['level'] == '品类'))
        self.assertTrue(any('尚未上市' in note for note in scenario['notes']))

    def test_scenario_validation_errors(self):
        for scenario in [{'from': 'missing', 'to': 'p8', 'share': 1}, {'from': 'p7', 'to': 'p7', 'share': 1}, {'from': 'p7', 'to': 'p8', 'share': 0}, {'from': 'p7', 'to': 'p8', 'share': 1.2}]:
            with self.assertRaises(ValueError):
                build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4, 'scenario': scenario})

    def test_empty_cutoff_is_not_an_error(self):
        result = build(self.tables, {'cutoff': 0, 'group': '', 'horizon': 4})
        self.assertEqual(result['levels']['total']['forecast'], [0.0] * 4)
        self.assertTrue(result['notes'])
        for row in result['rows']:
            self.assertEqual(row['status'], 'not_launched')

    def test_selected_models_are_from_known_set(self):
        result = build(self.tables, {'cutoff': 24, 'group': '', 'horizon': 4})
        for row in result['rows']:
            for model in row['models']:
                self.assertTrue(model)
        counts = result['validation']['selected_models']
        self.assertEqual(set(counts), {'近8周均值', '近12周指数平滑', '近4周均值', '最近一周', '近8周线性趋势'})
        self.assertTrue(any(counts.values()))


if __name__ == '__main__':
    unittest.main()
