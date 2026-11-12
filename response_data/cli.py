"""演练资料命令行：原始摘要、时间容量分析、独立重放与枚举核对。"""
import argparse
import json
from .io import load
from .domain import summarize
from .model import validate
from .flow import analyze, replay_flow
from .enumerate import brute_force


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False))


def cmd_inspect(a):
    v, digest = load(a.file)
    _print({'source_sha256': digest, 'summary': summarize(v)})


def cmd_analyze(a):
    v, digest = load(a.file)
    model = validate(v)
    report = analyze(model)
    if a.compact:
        for s in report['snapshots']:
            s.pop('flow_edges', None)
            s.pop('batches', None)
            s.pop('cut', None)
    _print({'source_sha256': digest, 'analysis': report})


def cmd_replay(a):
    v, digest = load(a.file)
    model = validate(v)
    report = analyze(model)
    snap = next((s for s in report['snapshots']
                 if s['status'] == 'solved' and s['deadline'] == a.deadline), None)
    if snap is None:
        valid = [s['deadline'] for s in report['snapshots'] if s['status'] == 'solved']
        raise SystemExit(f'截止时刻 {a.deadline} 不在已解候选 {valid} 中')
    _print({'source_sha256': digest, 'deadline': a.deadline,
            'replay': replay_flow(model, snap['batches'])})


def cmd_enumerate(a):
    v, digest = load(a.file)
    model = validate(v)
    results = [brute_force(model, h, a.max_paths, a.max_branches)
               for h in model.deadlines]
    _print({'source_sha256': digest, 'enumeration': results})


def main():
    p = argparse.ArgumentParser(prog='response_data')
    sub = p.add_subparsers(dest='command', required=True)

    pi = sub.add_parser('inspect', help='原始资料概况')
    pi.add_argument('file')
    pi.set_defaults(func=cmd_inspect)

    pa = sub.add_parser('analyze', help='逐候选截止时间的离散容量最大流分析')
    pa.add_argument('file')
    pa.add_argument('--compact', action='store_true',
                    help='只输出数值结论与状态，省略割边/流边/批次明细')
    pa.set_defaults(func=cmd_analyze)

    pr = sub.add_parser('replay', help='对指定截止时间的批次做独立容量重放')
    pr.add_argument('file')
    pr.add_argument('deadline', type=int)
    pr.set_defaults(func=cmd_replay)

    pe = sub.add_parser('enumerate', help='小规模枚举独立核对最大流结论')
    pe.add_argument('file')
    pe.add_argument('--max-paths', type=int, default=200)
    pe.add_argument('--max-branches', type=int, default=200000)
    pe.set_defaults(func=cmd_enumerate)

    a = p.parse_args()
    a.func(a)


if __name__ == '__main__':
    main()
