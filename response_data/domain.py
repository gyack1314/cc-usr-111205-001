"""核对道路、人数与开放区间的原始字段。"""
from .io import unique,integer

def summarize(v):
    nodes=unique(v['nodes'],'id');unique(v['roads'],'id');unique(v['shelters'],'id')
    for r in v['roads']:
        if r['u'] not in nodes or r['v'] not in nodes:raise ValueError('道路节点未知')
        integer(r['duration'],1)
        for a,b in r['open']:
            if integer(a)>integer(b):raise ValueError('开放区间逆序')
        for cap in r['capacity']:integer(cap)
    people=0
    for r in v['releases']:
        if r['node'] not in nodes:raise ValueError('出发点未知')
        integer(r['time']);people+=integer(r['people'])
    for s in v['shelters']:
        if s['node'] not in nodes:raise ValueError('安置节点未知')
        integer(s['total_capacity'])
    return {'nodes':len(nodes),'roads':len(v['roads']),'released_people':people,'shelter_total_capacity':sum(s['total_capacity'] for s in v['shelters'])}
