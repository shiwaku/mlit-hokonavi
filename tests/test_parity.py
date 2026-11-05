"""
Python（api/）とブラウザ（web/src/lib/routing/）の探索結果を揃えるためのフィクスチャ

合成ネットワーク（conftest）の静的 JSON と、いくつかの経路の Python での結果を
web/src/lib/routing/__fixtures__/parity.json に書き出す。web 側の vitest が同じ問い合わせを TS で解き、一致を確かめる。

  uv run python -m tests.test_parity --write              # フィクスチャを作り直す（探索・案内を変えたら）
  uv run python -m tests.test_parity --real shinjuku      # 実データでも比べる（git 管理外の network/ に書く）

pytest はフィクスチャが今のコードの出力と同じか（作り直し忘れがないか）だけを見る。
"""

import argparse
import json
import random
import sys
from pathlib import Path

from api import guidance
from api.export import static_graph
from api.graph import PedestrianGraph
from api.main import _snap_json
from tests.conftest import latlon, make_frames

BASE = Path(__file__).resolve().parent.parent
FIXTURE = BASE / 'web' / 'src' / 'lib' / 'routing' / '__fixtures__' / 'parity.json'
PROFILES = ('walk', 'wheelchair', 'stroller', 'vision')


def route_body(g: PedestrianGraph, q: dict) -> dict:
    """API の /route と同じ形（elapsed_ms を除く）"""
    r = g.route(
        q['from_lat'], q['from_lon'], q['to_lat'], q['to_lon'], q['profile'], q.get('from_floor'), q.get('to_floor')
    )
    snap = r['snap'] or {}
    body = {
        'profile': q['profile'],
        'snap': {'origin': _snap_json(snap.get('origin')), 'dest': _snap_json(snap.get('dest'))},
        'unreachable_reason': r['unreachable_reason'],
        'geometry': None,
        'summary': None,
        'steps': [],
        'warnings': [],
    }
    if r['unreachable_reason'] is None:
        body.update(guidance.build(g, r, q['profile']))
    return body


def q(a, b, profile, **kw):
    (alat, alon), (blat, blon) = a, b
    return {'from_lat': alat, 'from_lon': alon, 'to_lat': blat, 'to_lon': blon, 'profile': profile, **kw}


def synthetic_cases() -> list[dict]:
    variants = {'base': {}, 'no_accessible_elevator': {'DE': {'elevator': 2}}, 'slope': {'AB': {'vtcl_slope': 3}}}
    pairs = [
        (latlon('A', dx=0.5), latlon('C', dx=0.5), {}),
        (latlon('C', dx=0.5), latlon('A', dx=0.5), {}),
        (latlon('A', dx=0.2), latlon('A', dx=0.7), {}),
        (latlon('F', dx=0.1), latlon('G', dx=-0.1), {}),
        (latlon('G', dx=-0.1), latlon('F', dx=0.1), {}),
        (latlon('D', dy=0.3), latlon('B', dy=-0.2), {}),
        ((35.0, 139.0), latlon('A'), {}),
        (latlon('A', dx=0.5), latlon('C', dx=0.5), {'from_floor': 0.0, 'to_floor': 0.0}),
    ]
    out = []
    for name, ov in variants.items():
        g = PedestrianGraph.from_frames(*make_frames(ov), case=name)
        cases = []
        for a, b, kw in pairs:
            for p in PROFILES:
                query = q(a, b, p, **kw)
                cases.append({'query': query, 'expected': route_body(g, query)})
        out.append({'name': name, 'graph': static_graph(g), 'cases': cases})
    return out


def real_cases(case: str, n: int = 200, seed: int = 1) -> dict:
    """実データで、ノード付近の点の組をランダムに取って比べる"""
    g = PedestrianGraph(case)
    rng = random.Random(seed)
    lat, lon = g.nodes['lat'].to_numpy(), g.nodes['lon'].to_numpy()
    cases = []
    for _ in range(n):
        i, j = rng.randrange(g.n_nodes), rng.randrange(g.n_nodes)
        jitter = lambda: rng.uniform(-0.0002, 0.0002)  # noqa: E731  約 20 m
        query = q((lat[i] + jitter(), lon[i] + jitter()), (lat[j] + jitter(), lon[j] + jitter()), rng.choice(PROFILES))
        cases.append({'query': query, 'expected': route_body(g, query)})
    return {'name': case, 'graph': static_graph(g), 'cases': cases}


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, allow_nan=False) + '\n'


def test_parity_fixture_is_up_to_date():
    assert FIXTURE.exists(), 'uv run python -m tests.test_parity --write で作ってください'
    assert json.loads(FIXTURE.read_text(encoding='utf-8')) == json.loads(dumps(synthetic_cases())), (
        '探索・案内の結果が変わりました。uv run python -m tests.test_parity --write で作り直してください'
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--real', help='network/<case>/parity.json に実データでの比較用を書く')
    args = ap.parse_args()
    if args.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(dumps(synthetic_cases()), encoding='utf-8')
        print(FIXTURE, file=sys.stderr)
    if args.real:
        out = BASE / 'network' / args.real / 'parity.json'
        out.write_text(json.dumps([real_cases(args.real)], ensure_ascii=False, allow_nan=False), encoding='utf-8')
        print(out, file=sys.stderr)


if __name__ == '__main__':
    main()
