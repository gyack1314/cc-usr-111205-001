"""时间展开图上的整数最大流、最小割证据、流分解与独立重放。

图结构（所有边容量均为整数，Dinic 在整数容量上给出整数流）：

* 每个原节点 n 在每个时刻 t 有一个时间节点 (n,t)；
* 释放 r 在其声明时刻经源点边 S -> (node,time) 注入，人数即容量；
* 等待边 (n,t) -> (n,t+1)，容量 waiting_capacity[t]，对应占用 [t,t+1)；
* 道路边 (u,t) -> (v,t+duration)，仅当整条 [t,t+duration) 被某开放段
  [a,b) 覆盖时建立，容量为该道路出发时隙 t 的容量，抵达恰在 b 合法；
* 安置点 s 在时刻 t 先经入口边 (node,t) -> 中间点，容量 entry_capacity[t]，
  再经总容量边 中间点 -> T，容量 total_capacity。总容量边只建一次，
  因而同一总名额不会在每个时隙被重复使用；
* 只建立到达时刻不超过当前截止 H 的边，未来边无法泄入当前截止结果。
"""
import sys
from .model import road_open_at

sys.setrecursionlimit(100000)


class Edge:
    __slots__ = ('to', 'cap', 'rev', 'meta', 'is_reverse')

    def __init__(self, to, cap, rev, meta, is_reverse=False):
        self.to = to
        self.cap = cap
        self.rev = rev
        self.meta = meta
        self.is_reverse = is_reverse


class Dinic:
    def __init__(self):
        self.g = []
        self.level = []
        self.it = []

    def new_node(self):
        self.g.append([])
        return len(self.g) - 1

    def add_edge(self, fr, to, cap, meta=None):
        # rev 记录对方在其邻接表中的最终下标。时间严格向前，fr != to。
        forward_index = len(self.g[fr])
        backward_index = len(self.g[to])
        f = Edge(to, cap, backward_index, meta)
        r = Edge(fr, 0, forward_index, None, True)
        self.g[fr].append(f)
        self.g[to].append(r)
        return f

    def _bfs(self, s, t):
        self.level = [-1] * len(self.g)
        self.level[s] = 0
        q = [s]
        head = 0
        while head < len(q):
            v = q[head]
            head += 1
            for e in self.g[v]:
                if e.cap > 0 and self.level[e.to] < 0:
                    self.level[e.to] = self.level[v] + 1
                    q.append(e.to)
        return self.level[t] >= 0

    def _dfs(self, v, t, f):
        if v == t:
            return f
        g = self.g
        pushed = 0
        # 标准累计式当前弧 DFS：一次调用内尽量推满 f。只有“推送成功且该边
        # 仍有余量”时才原地重试；下游推不动（got==0）或边已耗尽都推进当前
        # 弧，否则会在多次 max_flow（残量已含反向边）时漏算重路由路径。
        while self.it[v] < len(g[v]) and pushed < f:
            e = g[v][self.it[v]]
            if e.cap > 0 and self.level[e.to] == self.level[v] + 1:
                got = self._dfs(e.to, t, min(f - pushed, e.cap))
                if got:
                    e.cap -= got
                    g[e.to][e.rev].cap += got
                    pushed += got
                    if e.cap > 0:
                        continue
            self.it[v] += 1
        return pushed

    def max_flow(self, s, t):
        flow = 0
        inf = 10 ** 30
        while self._bfs(s, t):
            self.it = [0] * len(self.g)
            while True:
                f = self._dfs(s, t, inf)
                if not f:
                    break
                flow += f
        return flow

    def residual_reachable(self, s):
        seen = {s}
        stack = [s]
        while stack:
            v = stack.pop()
            for e in self.g[v]:
                if e.cap > 0 and e.to not in seen:
                    seen.add(e.to)
                    stack.append(e.to)
        return seen


class BudgetExceeded(Exception):
    """扩展到下一层会超过声明的时间节点或边预算。"""


class HorizonBuilder:
    """逐层扩展的时间展开网络；已求过的流与残量在扩展时原样保留。"""

    def __init__(self, model):
        self.m = model
        self.d = Dinic()
        self.S = self.d.new_node()
        self.T = self.d.new_node()
        self.layer = {}          # (node, t) -> 内部节点号
        self.entry = {}          # (shelter_id, t) -> 中间点
        self.total_node = {}     # shelter_id -> 总容量侧节点
        self.road_ptr = {r.id: 0 for r in model.roads}
        self.H = -1
        self.node_budget_used = 2
        self.edge_budget_used = 0
        for s in model.shelters:
            z = self.d.new_node()
            self.total_node[s.id] = z
            self.node_budget_used += 1
            self._edge(z, self.T, s.total_capacity,
                       {'kind': 'shelter_total', 'shelter': s.id})

    def _edge(self, fr, to, cap, meta):
        self.edge_budget_used += 1
        return self.d.add_edge(fr, to, cap, meta)

    def _layer_nodes_estimate(self, h):
        return len(self.m.nodes) + len(self.m.shelters)

    def _layer_edges_estimate(self, h):
        m = self.m
        n_wait = sum(1 for nid in m.nodes if nid not in m.shelter_nodes) if h >= 1 else 0
        n_release = sum(1 for rel in m.releases if rel.time == h)
        n_shelter = 2 * len(m.shelters)
        n_road = 0
        for r in m.roads:
            t = h - r.duration
            if t >= 0 and road_open_at(r, t):
                n_road += 1
        return n_wait + n_release + n_shelter + n_road

    def extend(self, target_h):
        while self.H < target_h:
            h = self.H + 1
            added_nodes = self._layer_nodes_estimate(h)
            added_edges = self._layer_edges_estimate(h)
            if self.node_budget_used + added_nodes > self.m.max_time_nodes:
                raise BudgetExceeded(f'时间节点预算 {self.m.max_time_nodes} 不足')
            if (self.m.max_edges is not None
                    and self.edge_budget_used + added_edges > self.m.max_edges):
                raise BudgetExceeded(f'边预算 {self.m.max_edges} 不足')

            for nid in self.m.nodes:
                self.layer[(nid, h)] = self.d.new_node()
                self.node_budget_used += 1
            for s in self.m.shelters:
                z = self.d.new_node()
                self.entry[(s.id, h)] = z
                self.node_budget_used += 1

            if h >= 1:
                for nid in self.m.nodes:
                    if nid in self.m.shelter_nodes:
                        continue
                    self._edge(self.layer[(nid, h - 1)], self.layer[(nid, h)],
                               self.m.waiting[nid][h - 1],
                               {'kind': 'wait', 'node': nid,
                                'interval': [h - 1, h]})

            for rel in self.m.releases:
                if rel.time == h:
                    self._edge(self.S, self.layer[(rel.node, h)], rel.people,
                               {'kind': 'release', 'release_index': rel.index,
                                'node': rel.node, 'time': h, 'people': rel.people})

            for s in self.m.shelters:
                self._edge(self.layer[(s.node, h)], self.entry[(s.id, h)],
                           s.entry_capacity[h],
                           {'kind': 'shelter_entry', 'shelter': s.id,
                            'arrival': h})
                self._edge(self.entry[(s.id, h)], self.total_node[s.id],
                           s.total_capacity,
                           {'kind': 'shelter_total_link', 'shelter': s.id,
                            'arrival': h})
            for r in self.m.roads:
                t = h - r.duration
                while t >= self.road_ptr[r.id]:
                    self.road_ptr[r.id] = t + 1
                    if t >= 0 and road_open_at(r, t):
                        if t >= len(r.capacity):
                            raise ValueError(f'道路 {r.id} 容量未覆盖出发时隙 {t}')
                        self._edge(self.layer[(r.u, t)], self.layer[(r.v, h)],
                                   r.capacity[t],
                                   {'kind': 'road', 'road': r.id, 'u': r.u,
                                    'v': r.v, 'depart': t, 'arrive': h,
                                    'duration': r.duration})
                    t -= 1
            self.H = h

    def node_mapping(self):
        return [{'node': nid, 'time': t, 'time_node': idx}
                for (nid, t), idx in sorted(self.layer.items(),
                                            key=lambda kv: (kv[0][1], kv[0][0]))]


def min_cut(builder, reachable):
    """从残量可达集提取 S-T 割，只收集带业务元数据的正向非零容量边。

    零容量边不属于任何最小割（加入只会虚增割容量），显式排除；反向残量边
    以 ``is_reverse`` 标记排除。
    """
    d = builder.d
    cut = []
    capacity_sum = 0
    for v in reachable:
        for e in d.g[v]:
            if e.meta is None or e.is_reverse or e.to in reachable:
                continue
            original = e.cap + d.g[e.to][e.rev].cap
            if original <= 0:
                continue
            cut.append({'from_time_node': v, 'to_time_node': e.to,
                        'capacity': original, 'ref': e.meta})
            capacity_sum += original
    return cut, capacity_sum


def flow_edges(builder):
    """每条正向业务边的当前流量（反向边容量即已推送流量）。"""
    out = []
    for v, adj in enumerate(builder.d.g):
        for e in adj:
            if e.meta is None:
                continue
            used = builder.d.g[e.to][e.rev].cap
            if used:
                out.append({'from_time_node': v, 'to_time_node': e.to,
                            'flow': used, 'capacity': e.cap + used,
                            'ref': e.meta})
    return out


def decompose(builder):
    """把整数流拆成带出发/抵达时刻的路径批次。

    在流量快照上做非破坏性的 DFS：只读取每条边的已推送流量并扣减本地副本，
    绝不修改求解器残量，保证后续时域扩展复用的残量网络不被污染。
    """
    d = builder.d
    # (from_node, edge_index) -> 剩余可拆流量；初始等于当前已推送流量。
    remaining = {}
    for v, adj in enumerate(d.g):
        for i, e in enumerate(adj):
            if e.meta is not None:
                used = d.g[e.to][e.rev].cap
                if used > 0:
                    remaining[(v, i)] = used

    def step_of(meta):
        kind = meta['kind']
        if kind == 'road':
            return {'kind': 'road', 'road': meta['road'], 'u': meta['u'],
                    'v': meta['v'], 'depart': meta['depart'],
                    'arrive': meta['arrive'], 'duration': meta['duration']}
        if kind == 'wait':
            return {'kind': 'wait', 'node': meta['node'],
                    'interval': list(meta['interval'])}
        if kind == 'shelter_entry':
            return {'kind': 'entry', 'shelter': meta['shelter'],
                    'arrival': meta['arrival']}
        if kind in ('shelter_total', 'shelter_total_link'):
            return None
        raise AssertionError(f'意外的边类型 {kind}')

    def follow(v, limit, edge_keys, steps):
        """返回 (抵达汇点时的批次大小, 走过的业务边键, 步骤)。"""
        if v == builder.T:
            return limit, edge_keys, steps
        for i, e in enumerate(d.g[v]):
            key = (v, i)
            if remaining.get(key, 0) <= 0:
                continue
            nxt = min(limit, remaining[key])
            st = step_of(e.meta)
            if st is not None:
                steps.append(st)
            edge_keys.append(key)
            return follow(e.to, nxt, edge_keys, steps)
        raise AssertionError('正流量边未能抵达汇点')

    batches = []
    for i, e in enumerate(d.g[builder.S]):
        key = (builder.S, i)
        f = remaining.get(key, 0)
        while f > 0:
            meta = e.meta
            take, edge_keys, steps = follow(e.to, f, [], [])
            for k in edge_keys:
                remaining[k] -= take
            remaining[key] -= take
            f -= take
            batches.append({'release_index': meta['release_index'],
                            'release_node': meta['node'],
                            'release_time': meta['time'], 'people': take,
                            'steps': steps})
    return batches


def analyze(model):
    """逐候选截止时间求最大已安置人数，并给出割与可复算证据。"""
    builder = HorizonBuilder(model)
    total = model.total_released
    snapshots = []
    status = 'completed'
    blocked_at = None
    cumulative = 0  # 增量 max_flow 返回本次新增量，累计值才是当前截止的总流

    for h in model.deadlines:
        try:
            builder.extend(h)
        except BudgetExceeded as exc:
            status = 'budget_exceeded'
            blocked_at = h
            for later in model.deadlines[model.deadlines.index(h):]:
                snapshots.append({'deadline': later, 'status': 'not_attempted',
                                  'reason': str(exc)})
            break

        added = builder.d.max_flow(builder.S, builder.T)
        cumulative += added
        value = cumulative
        reachable = builder.d.residual_reachable(builder.S)
        cut, cut_cap = min_cut(builder, reachable)
        assert cut_cap == value, '最大流最小割定理自检失败'
        assert builder.T not in reachable
        batches = decompose(builder)
        assert sum(b['people'] for b in batches) == value
        replay = replay_flow(model, batches)
        assert replay['housed'] == value
        snapshots.append({
            'deadline': h,
            'status': 'solved',
            'max_housed': value,
            'total_released': total,
            'cleared': value == total,
            'cut_value': cut_cap,
            'cut': cut,
            'flow_edges': flow_edges(builder),
            'batches': batches,
            'replay': replay,
        })

    earliest = next((s['deadline'] for s in snapshots
                     if s['status'] == 'solved' and s['cleared']), None)
    solved = [s for s in snapshots if s['status'] == 'solved']
    if earliest is not None:
        clearance = earliest
    elif solved and not solved[-1]['cleared']:
        # 仅陈述给定时域内未清空，不断言现实中永久不可行。
        clearance = ('not_cleared_within_horizon' if status == 'completed'
                     else 'unknown_budget')
    else:
        # 第一层即超预算，没有任何已解候选。
        clearance = 'unknown_budget'
    return {
        'total_released': total,
        'shelter_total_capacity': sum(s.total_capacity for s in model.shelters),
        'status': status,
        'blocked_at_deadline': blocked_at,
        'earliest_clearance': clearance,
        'time_nodes': builder.node_mapping(),
        'time_node_count': builder.node_budget_used,
        'edge_count': builder.edge_budget_used,
        'snapshots': snapshots,
    }


def replay_flow(model, batches):
    """只读原始道路/开放/等待/接收容量，独立重放路径批次。

    与求解器内部图完全分离：所有容量约束都由本函数从 model 重新取数核对，
    并额外核对每个时间节点的流量守恒，防止图构造与求解器互相背书。
    """
    release_by_idx = {r.index: r for r in model.releases}
    shelter_by_id = {s.id: s for s in model.shelters}
    road_by_id = {r.id: r for r in model.roads}
    release_used = {}
    wait_use = {}
    road_use = {}
    entry_use = {}
    total_use = {}
    inject = {}
    inflow = {}
    outflow = {}

    def add(table, key, n):
        table[key] = table.get(key, 0) + n

    for b in batches:
        rel = release_by_idx[b['release_index']]
        n = b['people']
        if n <= 0:
            raise ValueError('批次人数必须为正整数')
        if (rel.node, rel.time) != (b['release_node'], b['release_time']):
            raise ValueError('批次释放点元数据不一致')
        add(release_used, rel.index, n)
        add(inject, (rel.node, rel.time), n)
        cur_node, cur_t = rel.node, rel.time
        housed = False
        for st in b['steps']:
            if st['kind'] == 'wait':
                if st['node'] != cur_node or st['interval'] != [cur_t, cur_t + 1]:
                    raise ValueError('等待边与当前位置/时刻不衔接')
                if cur_node in model.shelter_nodes:
                    raise ValueError('安置点接收后不得再等待')
                add(wait_use, (cur_node, cur_t), n)
                # 等待边的守恒记在其起点：[t,t+1) 的占用就是该时间节点的一类
                # 出流；下一时刻节点的入流由随后的道路/继续等待/入口另行记账，
                # 不在此重复计入。
                add(outflow, (cur_node, cur_t), n)
                add(inflow, (cur_node, cur_t + 1), n)
                cur_t += 1
            elif st['kind'] == 'road':
                r = road_by_id[st['road']]
                if (st['u'], st['v']) != (cur_node, r.v) or st['u'] != r.u:
                    raise ValueError('道路批次端点不衔接')
                if st['depart'] != cur_t or st['arrive'] != cur_t + r.duration \
                        or st['duration'] != r.duration:
                    raise ValueError('道路批次时刻与时长不衔接')
                if not road_open_at(r, cur_t):
                    raise ValueError(f'道路 {r.id} 在 {cur_t} 出发时未全程开放')
                add(road_use, (r.id, cur_t), n)
                add(outflow, (cur_node, cur_t), n)
                cur_node, cur_t = r.v, cur_t + r.duration
                add(inflow, (cur_node, cur_t), n)
            elif st['kind'] == 'entry':
                s = shelter_by_id[st['shelter']]
                if s.node != cur_node or st['arrival'] != cur_t:
                    raise ValueError('安置入口与当前位置/时刻不衔接')
                add(entry_use, (s.id, cur_t), n)
                add(total_use, s.id, n)
                add(outflow, (cur_node, cur_t), n)
                housed = True
            else:
                raise ValueError('未知批次步骤类型')
        if not housed:
            raise ValueError('批次未在安置点结束')

    for idx, used in release_used.items():
        if used > release_by_idx[idx].people:
            raise ValueError('重放超出释放人数')
    for (nid, t), used in wait_use.items():
        if used > model.waiting[nid][t]:
            raise ValueError(f'等待容量超载: {nid}[{t},{t + 1})')
    for (rid, t), used in road_use.items():
        r = road_by_id[rid]
        if used > r.capacity[t]:
            raise ValueError(f'道路 {rid} 出发时隙 {t} 容量超载')
        if not road_open_at(r, t):
            raise ValueError(f'道路 {rid} 在封闭后出发')
    for (sid, t), used in entry_use.items():
        if used > shelter_by_id[sid].entry_capacity[t]:
            raise ValueError(f'安置点 {sid} 时刻 {t} 入口容量超载')
    for sid, used in total_use.items():
        if used > shelter_by_id[sid].total_capacity:
            raise ValueError(f'安置点 {sid} 总接收容量超载')

    # 时间节点守恒：注入 + 抵达（道路入流）== 道路出发 + 入口接收 + 等待离开。
    keys = set(inject) | set(inflow) | set(outflow)
    for key in keys:
        if inject.get(key, 0) + inflow.get(key, 0) != outflow.get(key, 0):
            raise ValueError(f'时间节点 {key} 流量守恒不成立')

    return {
        'housed': sum(b['people'] for b in batches),
        'batch_count': len(batches),
        'peak_wait': [{'node': k[0], 'interval': [k[1], k[1] + 1], 'used': v}
                      for k, v in sorted(wait_use.items())],
        'peak_road': [{'road': k[0], 'depart': k[1], 'used': v}
                      for k, v in sorted(road_use.items())],
        'entry_used': [{'shelter': k[0], 'arrival': k[1], 'used': v}
                       for k, v in sorted(entry_use.items())],
        'total_used': [{'shelter': sid, 'used': used,
                        'capacity': shelter_by_id[sid].total_capacity}
                       for sid, used in sorted(total_use.items())],
    }
