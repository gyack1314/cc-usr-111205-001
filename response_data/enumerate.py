"""小规模实例的独立枚举核对。

与 :mod:`response_data.flow` 的最大流求解器完全分开实现：

1. 在 (节点, 时刻) 状态空间上 DFS，枚举所有遵守释放时刻、道路开放区间、
   等待容量非零、且在截止时刻前抵达安置点的完整时序路径；
2. 对路径集合做带逐资源容量约束的整数分配 DFS，求可安置人数最大值。

只适用于小演练；路径数或搜索分支超过预算时保留未完成状态，返回当前已证
下界，不伪造最优性。
"""
from .model import road_open_at


def enumerate_paths(model, deadline):
    """枚举 deadline（含）前可从任一释放点走到安置点的全部时序路径。"""
    m = model
    paths = []

    def walk(node, t, steps, waits, roads):
        if node in m.shelter_nodes:
            paths.append({'end_node': node, 'arrival': t, 'steps': list(steps),
                          'waits': list(waits), 'roads': list(roads)})
            return
        if t >= deadline:
            return
        # 等待：占用 [t,t+1)，容量为 0 的时隙不允许停留。
        if m.waiting[node][t] > 0:
            steps.append({'kind': 'wait', 'node': node, 'interval': [t, t + 1]})
            waits.append((node, t))
            walk(node, t + 1, steps, waits, roads)
            waits.pop()
            steps.pop()
        # 道路：整条 [t,t+duration) 被开放段覆盖，且抵达不晚于截止时刻。
        for r in m.node_roads.get(node, ()):
            if t + r.duration <= deadline and road_open_at(r, t):
                steps.append({'kind': 'road', 'road': r.id, 'u': r.u, 'v': r.v,
                              'depart': t, 'arrive': t + r.duration,
                              'duration': r.duration})
                roads.append((r.id, t))
                walk(r.v, t + r.duration, steps, waits, roads)
                roads.pop()
                steps.pop()

    for rel in m.releases:
        if rel.time <= deadline:
            walk(rel.node, rel.time,
                 [{'kind': 'release', 'release_index': rel.index,
                   'node': rel.node, 'time': rel.time}], [], [])
    return paths


def brute_force(model, deadline, max_paths=200, max_branches=200000):
    """枚举 + 整数分配，返回与最大流口径一致的独立核对结果。"""
    m = model
    raw_paths = enumerate_paths(m, deadline)
    if len(raw_paths) > max_paths:
        return {'deadline': deadline, 'status': 'budget_exceeded',
                'reason': f'路径数 {len(raw_paths)} 超过 {max_paths}',
                'path_count': len(raw_paths), 'best_lower_bound': 0}

    release_by_idx = {r.index: r for r in m.releases}
    shelter_at_node = {s.node: s for s in m.shelters}
    shelter_by_id = {s.id: s for s in m.shelters}

    # 每条候选路径按释放来源分组，并展开其占用的全部资源键。
    groups = {}
    for p in raw_paths:
        idx = p['steps'][0]['release_index']
        sid = shelter_at_node[p['end_node']].id
        resources = [('release', idx)]
        resources += [('wait',) + w for w in p['waits']]
        resources += [('road',) + x for x in p['roads']]
        resources.append(('entry', sid, p['arrival']))
        resources.append(('total', sid))
        groups.setdefault(idx, []).append({'path': p, 'resources': resources})

    def resource_cap(key):
        # 容量一律直接从原始 model 取数，不经过时间展开图。
        kind = key[0]
        if kind == 'release':
            return release_by_idx[key[1]].people
        if kind == 'wait':
            return m.waiting[key[1]][key[2]]
        if kind == 'road':
            return next(r for r in m.roads if r.id == key[1]).capacity[key[2]]
        if kind == 'entry':
            return shelter_by_id[key[1]].entry_capacity[key[2]]
        if kind == 'total':
            return shelter_by_id[key[1]].total_capacity
        raise KeyError(kind)

    order = sorted(groups)
    best = 0
    best_alloc = None
    branches = 0
    stopped = False

    # 所有可能被剩余来源占用的资源键，用于计算资源侧乐观上界。
    remaining_resources = set()
    for i in order:
        for cand in groups[i]:
            remaining_resources.update(cand['resources'])

    def upper_bound(gi, used):
        # 两个独立乐观上界取小：
        # 1) 剩余来源尚未分配的总人数；
        # 2) 剩余相关资源的剩余容量之和（每条路径至少占一个单位资源，
        #    释放/入口/总容量都会被计入，故不会低估）。
        people_room = sum(release_by_idx[i].people for i in order[gi:])
        resource_room = sum(max(0, resource_cap(k) - used.get(k, 0))
                            for k in remaining_resources)
        return min(people_room, resource_room)

    def assign_source(gi, used, housed, alloc):
        """对第 gi 个释放来源，在它的候选路径上分配可安置人数。"""
        nonlocal best, best_alloc, branches, stopped
        if stopped:
            return
        branches += 1
        if branches > max_branches:
            stopped = True
            return
        if housed + upper_bound(gi, used) <= best:
            return
        if gi == len(order):
            if housed > best:
                best = housed
                best_alloc = [dict(x) for x in alloc]
            return

        idx = order[gi]
        cands = groups[idx]
        supply = release_by_idx[idx].people

        def choose(pi, remain, housed_here):
            # 在第 pi 条路径上分配 x（0..该路径可用容量），余下人员继续在
            # 后续路径上安排；所有路径都试过仍剩余的人留在源侧，不算转移。
            # housed_here 累计本来源已安置人数，归约下一来源时并入总 housed。
            if stopped:
                return
            if pi == len(cands):
                assign_source(gi + 1, used, housed + housed_here, alloc)
                return
            cand = cands[pi]
            cap = min(remain, min((resource_cap(k) - used.get(k, 0)
                                   for k in cand['resources']), default=remain))
            for x in range(cap, -1, -1):
                if x:
                    for k in cand['resources']:
                        used[k] = used.get(k, 0) + x
                    alloc.append({'release_index': idx, 'people': x,
                                  'path': cand['path']})
                choose(pi + 1, remain - x, housed_here + x)
                if x:
                    alloc.pop()
                    for k in cand['resources']:
                        used[k] -= x
                if stopped:
                    return

        choose(0, supply, 0)

    assign_source(0, {}, 0, [])
    if stopped:
        return {'deadline': deadline, 'status': 'budget_exceeded',
                'reason': f'分配搜索分支超过 {max_branches}',
                'path_count': len(raw_paths), 'best_lower_bound': best}
    return {'deadline': deadline, 'status': 'completed', 'path_count': len(raw_paths),
            'max_housed': best, 'allocation': best_alloc or []}
