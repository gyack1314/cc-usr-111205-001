"""离散时间容量模型的严格校验与索引化视图。

只接受整数时隙：释放时刻、道路时长、开放区间端点、各类容量均为整数。
校验通过后返回 :class:`Model`，供时间展开图构建器反复使用；校验失败抛
``ValueError``，不做任何静默填补。
"""
from dataclasses import dataclass, field
from .io import unique, integer


@dataclass
class Road:
    id: str
    u: str
    v: str
    duration: int
    open: list
    capacity: list


@dataclass
class Shelter:
    id: str
    node: str
    total_capacity: int
    entry_capacity: list


@dataclass
class Release:
    index: int
    node: str
    time: int
    people: int


@dataclass
class Model:
    nodes: list
    waiting: dict
    roads: list
    shelters: list
    releases: list
    deadlines: list
    max_time_nodes: int
    max_edges: object
    shelter_nodes: set = field(default_factory=set)
    node_roads: dict = field(default_factory=dict)

    @property
    def total_released(self):
        return sum(r.people for r in self.releases)

    @property
    def max_deadline(self):
        return max(self.deadlines)


def validate(v):
    if not isinstance(v, dict):
        raise ValueError('顶层资料必须为对象')
    for key in ('nodes', 'roads', 'releases', 'shelters', 'deadlines', 'max_time_nodes'):
        if key not in v:
            raise ValueError(f'缺少字段 {key}')

    node_ids = unique(v['nodes'], 'id')
    waiting = {}
    for n in v['nodes']:
        w = n.get('waiting_capacity')
        if not isinstance(w, list) or not w:
            raise ValueError('waiting_capacity 必须是非空整数数组')
        waiting[n['id']] = [integer(x) for x in w]

    roads = []
    node_roads = {nid: [] for nid in node_ids}
    unique(v['roads'], 'id')
    for r in v['roads']:
        if r['u'] not in node_ids or r['v'] not in node_ids:
            raise ValueError('道路节点未知')
        duration = integer(r['duration'], 1)
        opens = []
        if not isinstance(r.get('open'), list) or not r['open']:
            raise ValueError('开放区间必须是非空数组')
        for pair in r['open']:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError('开放区间必须是 [a,b) 二元组')
            a, b = integer(pair[0]), integer(pair[1])
            if a > b:
                raise ValueError('开放区间逆序')
            opens.append((a, b))
        cap = r.get('capacity')
        if not isinstance(cap, list) or not cap:
            raise ValueError('道路容量必须是非空整数数组')
        cap = [integer(x) for x in cap]
        road = Road(r['id'], r['u'], r['v'], duration, opens, cap)
        roads.append(road)
        node_roads[r['u']].append(road)

    unique(v['shelters'], 'id')
    shelters = []
    shelter_nodes = set()
    for s in v['shelters']:
        if s['node'] not in node_ids:
            raise ValueError('安置节点未知')
        total = integer(s['total_capacity'])
        ec = s.get('entry_capacity')
        if not isinstance(ec, list) or not ec:
            raise ValueError('安置入口容量必须是非空整数数组')
        shelters.append(Shelter(s['id'], s['node'], total, [integer(x) for x in ec]))
        shelter_nodes.add(s['node'])
    # 契约：安置节点没有外出道路，正式接收后不再回路。
    for nid in shelter_nodes:
        if node_roads[nid]:
            raise ValueError('安置节点不得存在外出道路')

    releases = []
    seen_release = set()
    for i, r in enumerate(v['releases']):
        if r['node'] not in node_ids:
            raise ValueError('出发点未知')
        t = integer(r['time'])
        people = integer(r['people'], 1)
        key = (r['node'], t)
        if key in seen_release:
            raise ValueError('同一节点同一时刻不得重复声明释放')
        seen_release.add(key)
        releases.append(Release(i, r['node'], t, people))

    deadlines = v['deadlines']
    if not isinstance(deadlines, list) or not deadlines:
        raise ValueError('deadlines 必须是非空整数数组')
    h_prev = -1
    for h in deadlines:
        h = integer(h)
        if h <= h_prev:
            raise ValueError('deadlines 必须严格递增且非负')
        h_prev = h
    hmax = h_prev

    # 数组长度必须覆盖最大候选时域里会被索引到的每个时隙。
    for nid, w in waiting.items():
        if len(w) < hmax:
            raise ValueError(f'节点 {nid} 的 waiting_capacity 未覆盖到时隙 {hmax - 1}')
    for r in roads:
        if len(r.capacity) < max(1, hmax):
            raise ValueError(f'道路 {r.id} 的逐时隙容量未覆盖出发时隙 {max(0, hmax - 1)}')
    for s in shelters:
        if len(s.entry_capacity) < hmax + 1:
            raise ValueError(f'安置点 {s.id} 的入口容量未覆盖到时刻 {hmax}')

    budget = integer(v['max_time_nodes'], 1)
    max_edges = v.get('max_edges')
    if max_edges is not None:
        max_edges = integer(max_edges, 1)

    return Model(list(node_ids), waiting, roads, shelters, releases, list(deadlines),
                 budget, max_edges, shelter_nodes, node_roads)


def road_open_at(road, depart):
    """道路在 depart 出发时，整条 [depart, depart+duration) 是否被某开放段覆盖。

    半开区间 [a,b)：抵达恰好等于 b 合法，因此条件是 a<=depart 且
    depart+duration<=b；封闭时刻之后出发必然失败。
    """
    end = depart + road.duration
    return any(a <= depart and end <= b for a, b in road.open)
