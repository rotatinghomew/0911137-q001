import copy
import json
import unittest

from assortment_forecast.application import ROOT
from assortment_forecast.domain import run


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((ROOT / 'fixtures/data.json').read_text())
        self.data = {key: [] for key in self.fixture['tables']}

    def test_fixture_is_deterministic_and_not_mutated(self):
        original = copy.deepcopy(self.fixture['tables'])
        options = {'cutoff': self.fixture['meta']['default_cutoff'], 'group': ''}
        first = run(self.fixture['tables'], options)
        second = run(self.fixture['tables'], options)
        self.assertEqual(first, second)
        self.assertEqual(original, self.fixture['tables'])
        self.assertTrue(first['rows'])
        json.dumps(first, allow_nan=False)

    def sample(self):
        self.data['membership'] = [dict(product='p', category='old', start=0, end=4, known_at=0), dict(product='p', category='new', start=4, end=None, known_at=5)]
        self.data['sales'] = [dict(product='p', region='甲', week=1, known_at=1, observed=True, quantity=0), dict(product='p', region='甲', week=2, known_at=2, observed=False, quantity=None), dict(product='p', region='甲', week=3, known_at=6, observed=True, quantity=12)]

    def test_unknown_zero_and_late_sales(self):
        self.sample()
        result = run(self.data, {'cutoff': 3, 'group': ''})
        self.assertEqual(result['summary'], {'销量': 0, '有效观测': 1, '未知观测': 1})
        self.assertEqual(result['rows'][0]['average'], 0)
        self.assertEqual(result['rows'][0]['category'], 'old')
        later = run(self.data, {'cutoff': 6, 'group': ''})
        self.assertEqual(later['rows'][0]['total'], 12)
        self.assertEqual(later['rows'][0]['category'], 'new')

    def test_group_exclusion(self):
        self.sample()
        self.assertEqual(run(self.data, {'cutoff': 6, 'group': '乙'})['rows'], [])

    def test_negative_observation_is_invalid(self):
        self.sample()
        self.data['sales'][0]['quantity'] = -1
        with self.assertRaises(ValueError):
            run(self.data, {'cutoff': 3, 'group': ''})
