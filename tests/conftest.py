"""
テスト用の小さなネットワーク（実データに依存しない）

  A ──歩道 100m── B ──階段 100m── C
  │                              │
  歩道 100m                      歩道 100m
  │                              │
  D ──エレベーター(地上→1階)── E ──歩道(1階→地上の扱いで 0 階)──┘

B→C は階段で最短。車いす・ベビーカーは A→D→E→C（エレベーター）を回る。
F ─動く歩道（起点→終点のみ）─ G は一方通行の確認用で、他とは G-C の歩道でつながる。
"""

import numpy as np
import pandas as pd
import pytest
import shapely

from api import codes as C
from api.graph import PedestrianGraph

LAT0, LON0 = 35.69, 139.70
DLAT = 100 / 110_950  # 100 m
DLON = 100 / (110_950 * np.cos(np.radians(LAT0)))

NODES = {  # id: (x 100m 単位, y 100m 単位, floor)
    'A': (0, 1, 0.0),
    'B': (1, 1, 0.0),
    'C': (2, 1, 0.0),
    'D': (0, 0, 0.0),
    'E': (1, 0, 1.0),
    'E2': (1.0001, 0, 0.0),
    'F': (3, 1, 0.0),
    'G': (4, 1, 0.0),
}


def link(lid, s, e, dist, **kw):
    base = {c: 1 for c in C.LINK_CODE_COLUMNS}
    base.update(width=4, vtcl_slope=1, lev_diff=1, brail_tile=2)
    base.update(kw)
    return {'link_id': lid, 'start_id': s, 'end_id': e, 'distance': float(dist), **base}


LINKS = [
    link('AB', 'A', 'B', 100),
    link('BC', 'B', 'C', 100, route_type=C.RT_STAIRS),
    link('AD', 'A', 'D', 100),
    link('DE', 'D', 'E', 10, route_type=C.RT_ELEVATOR, elevator=3),
    link('EE2', 'E', 'E2', 10, route_type=C.RT_ELEVATOR, elevator=3),
    link('E2C', 'E2', 'C', 141),
    link('CF', 'C', 'F', 100),
    link('FG', 'F', 'G', 100, route_type=C.RT_MOVING_WALK, direction=C.DIR_FORWARD),
]


def make_frames(overrides: dict[str, dict] | None = None):
    nodes = pd.DataFrame(
        [
            {'node_id': k, 'lat': LAT0 + y * DLAT, 'lon': LON0 + x * DLON, 'floor': f, 'elevation': np.nan, 'in_out': 1}
            for k, (x, y, f) in NODES.items()
        ]
    )
    rows = []
    for r in LINKS:
        r = {**r, **(overrides or {}).get(r['link_id'], {})}
        rows.append(r)
    links = pd.DataFrame(rows)
    for c in C.LINK_CODE_COLUMNS:
        links[c] = links[c].astype('int16')
    pos = nodes.set_index('node_id')
    s, e = pos.loc[links['start_id']], pos.loc[links['end_id']]
    links['geometry'] = list(
        shapely.linestrings(
            np.stack([np.column_stack([s['lon'], s['lat']]), np.column_stack([e['lon'], e['lat']])], axis=1)
        )
    )
    links['floor_start'] = s['floor'].to_numpy().astype('float32')
    links['floor_end'] = e['floor'].to_numpy().astype('float32')
    return links, nodes


def latlon(node: str, dx: float = 0.0, dy: float = 0.0) -> tuple[float, float]:
    x, y, _ = NODES[node]
    return LAT0 + (y + dy) * DLAT, LON0 + (x + dx) * DLON


@pytest.fixture
def graph() -> PedestrianGraph:
    return PedestrianGraph.from_frames(*make_frames())
