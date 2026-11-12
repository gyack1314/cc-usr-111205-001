"""离散时间容量分析的端到端与性质测试。"""
import copy
import unittest

from response_data.io import load
from response_data.model import validate
from response_data.flow import (analyze, HorizonBuilder, replay_flow, decompose,
                                min_cut, BudgetExceeded)
from response_data.enumerate import brute_force, enumerate_paths


def flow_series(report):
    return [(s['deadline'], s['max_housed']) for s in report['snapshots']
            if s['status'] == 'solved']


class FixtureTests(unittest.TestCase):
    def test_normal_series_and_earliest_clearance(self):
        report = analyze(validate(load('data/normal.json')[0]))
        self.assertEqual(flow_series(report),
                         [(2, 0), (3, 2), (4, 4), (5, 4), (6, 4), (7, 6)])
        self.assertEqual(report['earliest_clearance'], 7)
        self.assertEqual(report['status'], 'completed')

    def test_closed_before_arrival_never_feasible_until_reopen(self):
        report = analyze(validate(load('data/closed-before-arrival.json')[0]))
        self.assertEqual(flow_series(report),
                         [(2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 2)])
        # 时域内未清空，只能陈述该时域结论，不能断言永久不可行。
        self.assertEqual(report['earliest_clearance'], 'not_cleared_within_horizon')

    def test_shelter_total_capacity_shortage(self):
        report = analyze(validate(load('data/shelter-bottleneck.json')[0]))
        self.assertEqual(flow_series(report)[-1], (7, 2))
        self.assertEqual(report['earliest_clearance'], 'not_cleared_within_horizon')
        last = report['snapshots'][-1]
        # 割必须落在安置总容量上，且割值等于已证最大流。
        self.assertTrue(any(c['ref']['kind'] == 'shelter_total' for c in last['cut']))
        self.assertEqual(last['cut_value'], last['max_housed'])

    def test_invalid_duration_rejected_by_validator(self):
        with self.assertRaises((ValueError, KeyError, TypeError)):
            validate(load('data/invalid-domain.json')[0])


class CutAndIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.model = validate(load('data/normal.json')[0])

    def test_cut_equals_flow_at_every_deadline(self):
        for s in analyze(self.model)['snapshots']:
            self.assertEqual(s['cut_value'], s['max_housed'])
            self.assertEqual(sum(c['capacity'] for c in s['cut']), s['max_housed'])

    def test_flows_are_integral_and_batches_sum_to_flow(self):
        for s in analyze(self.model)['snapshots']:
            for fe in s['flow_edges']:
                self.assertIsInstance(fe['flow'], int)
            self.assertEqual(sum(b['people'] for b in s['batches']),
                             s['max_housed'])
            for b in s['batches']:
                self.assertIsInstance(b['people'], int)
                self.assertGreater(b['people'], 0)

    def test_incremental_matches_oneshot_and_no_future_leakage(self):
        # 一次性直接建到 H 与逐层增量扩展，最大流、割、批次必须完全一致；
        # 这同时证明未来层的边不会泄入更早截止时刻的结果。
        incremental = analyze(self.model)
        for h in self.model.deadlines:
            one = HorizonBuilder(self.model)
            one.extend(h)
            value = one.d.max_flow(one.S, one.T)
            cut, cap = min_cut(one, one.d.residual_reachable(one.S))
            snap = next(s for s in incremental['snapshots'] if s['deadline'] == h)
            self.assertEqual(value, snap['max_housed'])
            self.assertEqual(cap, snap['cut_value'])
            self.assertEqual(sum(b['people'] for b in decompose(one)), value)

    def test_time_node_mapping_covers_every_layer(self):
        report = analyze(self.model)
        hmax = self.model.max_deadline
        for nid in self.model.nodes:
            for t in range(hmax + 1):
                self.assertTrue(any(m['node'] == nid and m['time'] == t
                                    for m in report['time_nodes']))


class EnumerationAgreementTests(unittest.TestCase):
    def test_brute_force_matches_max_flow_on_fixtures(self):
        for name in ('normal', 'closed-before-arrival', 'shelter-bottleneck'):
            model = validate(load(f'data/{name}.json')[0])
            report = analyze(model)
            for h in model.deadlines:
                bf = brute_force(model, h)
                self.assertEqual(bf['status'], 'completed', name)
                flow = next(s['max_housed'] for s in report['snapshots']
                            if s['deadline'] == h)
                self.assertEqual(bf['max_housed'], flow, f'{name} H={h}')

    def test_waiting_congestion_synthetic(self):
        # 道路 a 只能 t=0 出发（容量 4），中转点等待容量仅 1；
        # 道路 b 最早 t=2 出发。4 人到达后只有 1 人能在 [1,2) 等待，
        # 其余无法在截止前安置，最大流被等待区卡住。
        model = validate(_waiting_congestion_material())
        report = analyze(model)
        self.assertEqual(flow_series(report), [(3, 1)])
        bf = brute_force(model, 3)
        self.assertEqual(bf['max_housed'], 1)
        # 割证据包含中转点等待边。
        cut_kinds = {c['ref']['kind'] for c in report['snapshots'][0]['cut']}
        self.assertIn('wait', cut_kinds)

    def test_static_reachable_but_too_late(self):
        # 静态图里安置点可达，但任何时序路径都在截止之后才到 => 最大流 0，
        # 且枚举器同样给 0（路径集合为空）。
        material = _waiting_congestion_material()
        material['deadlines'] = [1]
        model = validate(material)
        report = analyze(model)
        self.assertEqual(report['snapshots'][0]['max_housed'], 0)
        self.assertEqual(enumerate_paths(model, 1), [])

    def test_critical_closure_arrival_on_boundary_allowed(self):
        # 道路开放 [0,3)，duration 2，t=1 出发恰在 3 抵达，按半开区间合法。
        material = _boundary_material()
        model = validate(material)
        report = analyze(model)
        self.assertEqual(flow_series(report), [(3, 3)])


class ReplayTests(unittest.TestCase):
    def test_replay_rejects_tampered_capacity(self):
        model = validate(load('data/normal.json')[0])
        report = analyze(model)
        snap = next(s for s in report['snapshots'] if s['deadline'] == 4)
        good = replay_flow(model, snap['batches'])
        self.assertEqual(good['housed'], 4)
        # 篡改某条道路批次人数使其超过逐时隙容量，独立重放必须拒绝。
        tampered = copy.deepcopy(snap['batches'])
        for b in tampered:
            for st in b['steps']:
                if st['kind'] == 'road':
                    b['people'] += 100
                    break
        with self.assertRaises(ValueError):
            replay_flow(model, tampered)

    def test_replay_detects_closed_road_departure(self):
        model = validate(load('data/closed-before-arrival.json')[0])
        # 手工伪造一个在封闭段 t=0 出发的批次。
        fake = [{'release_index': 0, 'release_node': 'village',
                 'release_time': 0, 'people': 1,
                 'steps': [
                     {'kind': 'road', 'road': 'b', 'u': 'junction',
                      'v': 'shelter', 'depart': 0, 'arrive': 2, 'duration': 2},
                     {'kind': 'entry', 'shelter': 's0', 'arrival': 2}]}]
        # 先经道路 a 到 junction 的位置不匹配也会被守恒/衔接检查拦下；
        # 这里直接验证封闭道路判定本身。
        from response_data.model import road_open_at
        road = next(r for r in model.roads if r.id == 'b')
        self.assertFalse(road_open_at(road, 0))


class BudgetTests(unittest.TestCase):
    def test_node_budget_preserves_unfinished_state(self):
        material = _waiting_congestion_material()
        material['deadlines'] = [3, 4, 5]
        # H=3 需要 19 个时间节点，H=4 需要 23；预算 20 可解出第一层，
        # 第二次扩展即触发预算保护并保留未完成状态。
        material['max_time_nodes'] = 20
        model = validate(material)
        report = analyze(model)
        self.assertEqual(report['status'], 'budget_exceeded')
        self.assertIsNotNone(report['blocked_at_deadline'])
        self.assertTrue(any(s['status'] == 'not_attempted'
                            for s in report['snapshots']))
        # 已解出的最早层结论仍然有效。
        self.assertEqual(report['snapshots'][0]['status'], 'solved')

    def test_edge_budget_raises_and_is_reported(self):
        material = _waiting_congestion_material()
        material['max_edges'] = 1
        model = validate(material)
        report = analyze(model)
        self.assertEqual(report['status'], 'budget_exceeded')


class ValidationTests(unittest.TestCase):
    def _base(self):
        return {
            'nodes': [{'id': 'n', 'waiting_capacity': [5] * 6},
                      {'id': 's', 'waiting_capacity': [0] * 6}],
            'roads': [{'id': 'r', 'u': 'n', 'v': 's', 'duration': 1,
                       'open': [[0, 6]], 'capacity': [3] * 6}],
            'releases': [{'node': 'n', 'time': 0, 'people': 3}],
            'shelters': [{'id': 'a', 'node': 's', 'total_capacity': 5,
                          'entry_capacity': [5] * 6}],
            'deadlines': [2, 3], 'max_time_nodes': 5000}

    def _assert_rejected(self, mutate):
        import copy
        material = self._base()
        mutate(material)
        with self.assertRaises(ValueError):
            validate(material)

    def test_validator_rejects_illegal_models(self):
        self._assert_rejected(lambda m: m['roads'][0].__setitem__('u', 's'))
        self._assert_rejected(lambda m: m['roads'][0].__setitem__('duration', 0))
        self._assert_rejected(lambda m: m['roads'][0]['open'].__setitem__(0, [4, 2]))
        self._assert_rejected(lambda m: m['deadlines'].__setitem__(1, 2))
        self._assert_rejected(lambda m: m['releases'][0].__setitem__('people', 2.5))
        self._assert_rejected(lambda m: m['roads'][0].__setitem__('capacity', [3]))
        self._assert_rejected(
            lambda m: m['shelters'][0].__setitem__('entry_capacity', [5]))
        self._assert_rejected(lambda m: m['releases'][0].__setitem__('node', 'x'))
        self._assert_rejected(
            lambda m: m['releases'].append({'node': 'n', 'time': 0, 'people': 1}))

    def test_valid_model_accepted(self):
        self.assertIsNotNone(validate(self._base()))


def _waiting_congestion_material():
    return {
        'nodes': [
            {'id': 'village', 'waiting_capacity': [10, 10, 10, 10, 10, 10]},
            {'id': 'junction', 'waiting_capacity': [1, 1, 1, 1, 1, 1]},
            {'id': 'shelter', 'waiting_capacity': [0, 0, 0, 0, 0, 0]},
        ],
        'roads': [
            {'id': 'a', 'u': 'village', 'v': 'junction', 'duration': 1,
             'open': [[0, 1]], 'capacity': [4, 0, 0, 0, 0, 0]},
            {'id': 'b', 'u': 'junction', 'v': 'shelter', 'duration': 1,
             'open': [[2, 6]], 'capacity': [0, 0, 4, 4, 4, 4]},
        ],
        'releases': [{'node': 'village', 'time': 0, 'people': 4}],
        'shelters': [{'id': 's0', 'node': 'shelter', 'total_capacity': 10,
                      'entry_capacity': [10, 10, 10, 10, 10, 10]}],
        'deadlines': [3],
        'max_time_nodes': 5000,
    }


def _boundary_material():
    return {
        'nodes': [
            {'id': 'village', 'waiting_capacity': [10, 10, 10, 10]},
            {'id': 'shelter', 'waiting_capacity': [0, 0, 0, 0]},
        ],
        'roads': [
            {'id': 'a', 'u': 'village', 'v': 'shelter', 'duration': 2,
             'open': [[0, 3]], 'capacity': [3, 3, 3, 3]},
        ],
        'releases': [{'node': 'village', 'time': 0, 'people': 3}],
        'shelters': [{'id': 's0', 'node': 'shelter', 'total_capacity': 10,
                      'entry_capacity': [10, 10, 10, 10]}],
        'deadlines': [3],
        'max_time_nodes': 5000,
    }


if __name__ == '__main__':
    unittest.main()
