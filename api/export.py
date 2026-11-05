"""
探索グラフ → 表示用 GeoJSON（/network）と、ブラウザで探索するための静的 JSON（GitHub Pages 版）

GitHub Pages ではサーバーが動かないので、画面（web/src/lib/routing/）が自分で探索する。
通れるか・コストはいくらかの判断は Python（profiles.py）だけに置き、結果の配列をそのまま書き出す。
ブラウザ側に移すのはスナップ・Dijkstra・案内文の組み立てだけ。両者が同じ結果になることは
tests/test_parity.py が書き出すフィクスチャで web 側のテスト（vitest）が確かめる。
"""

import math

import numpy as np

from api import codes as C
from api import guidance
from api.graph import SNAP_CANDIDATES, SNAP_RADII_M, PedestrianGraph
from api.profiles import PROFILES

# 静的 JSON の形式の版。配列を足したり意味を変えたら上げる（web/src/lib/routing/types.ts の FORMAT と揃える）
STATIC_FORMAT = 2  # 2: source（1 件）→ sources（まとめた地区は複数）・title


def _decode(table: dict, v: int) -> str | None:
    return None if v == C.MISSING else table.get(v, '不明' if v == C.UNKNOWN else str(v))


def _floor(x: float) -> float | None:
    return None if math.isnan(x) else float(x)


def access_codes(g: PedestrianGraph) -> dict[str, np.ndarray]:
    """0: 通れる、1: 属性が不明、2: 片方向のみ、3: 通れない"""
    acc = {}
    for k, lc in g.costs.items():
        f, b = np.isfinite(lc.cost_fwd), np.isfinite(lc.cost_bwd)
        acc[k] = np.where(~f & ~b, 3, np.where(f ^ b, 2, np.where(lc.unknown, 1, 0)))
    return acc


def network_geojson(g: PedestrianGraph) -> dict:
    """表示用のネットワーク（GeoJSON）。プロパティは docs/api-design.md 1-3"""
    L = g.links
    acc = access_codes(g)
    feats = []
    for i, row in enumerate(L.itertuples(index=False)):
        a, b = g.piece_coords(guidance.Piece(i, 0.0, 1.0))
        kind = guidance.link_kind(int(row.route_type), int(row.rt_struct))
        props = {
            'link_id': row.link_id,
            'kind': kind,
            'label': guidance.KIND_LABEL[kind],
            'distance': round(float(row.distance), 1),
            'floor': _floor(row.floor_start),
            'floor_end': _floor(row.floor_end),
            'width': _decode(C.WIDTH, row.width),
            'slope': _decode(C.VTCL_SLOPE, row.vtcl_slope),
            'lev_diff': _decode(C.LEV_DIFF, row.lev_diff),
            'signal': _decode(C.TFC_SIGNAL, row.tfc_signal),
            'brail_tile': _decode({1: 'なし', 2: 'あり'}, row.brail_tile),
            'elevator': _decode(C.ELEVATOR, row.elevator),
            'roof': _decode({1: 'なし', 2: 'あり'}, row.roof),
            'direction': int(row.direction),
            **{f'acc_{k}': int(v[i]) for k, v in acc.items()},
        }
        coords = [[round(a[0], 7), round(a[1], 7)], [round(b[0], 7), round(b[1], 7)]]
        feats.append(
            {'type': 'Feature', 'id': i, 'geometry': {'type': 'LineString', 'coordinates': coords}, 'properties': props}
        )
    return {'type': 'FeatureCollection', 'features': feats}


def _costs(a: np.ndarray) -> list[float]:
    """inf（通れない）は JSON に書けないので -1 にする。
    値は丸めない（丸めると案内の距離・時間・座標の末尾が Python 版とずれる）"""
    return [-1 if not np.isfinite(x) else float(x) for x in a]


def static_graph(g: PedestrianGraph) -> dict:
    """ブラウザで探索するための静的 JSON。リンク・ノードは列ごとの配列（行番号が添字）"""
    L = g.links
    rt, st = L['route_type'].to_numpy(), L['rt_struct'].to_numpy()
    sig, sig_t = L['tfc_signal'].to_numpy(), L['tfc_s_type'].to_numpy()
    slope = L['vtcl_slope'].to_numpy()

    kind, signal, uphill = [], [], []
    for i in range(g.n_links):
        k = guidance.link_kind(int(rt[i]), int(st[i]))
        kind.append(k)
        s = None
        if k == 'crosswalk' and sig[i] in guidance.SIGNAL_TEXT:
            s = guidance.SIGNAL_TEXT[int(sig[i])]
            if sig[i] != 1 and sig_t[i] in C.TFC_S_TYPE:
                s += '・' + C.TFC_S_TYPE[int(sig_t[i])]
        signal.append(s)
        up = C.SLOPE_CLASS.get(int(slope[i]), (0, None))[1]
        uphill.append(0 if up is None else (1 if up else -1))  # 1: 起点→終点が上り、-1: 下り

    profiles = {}
    for k, p in PROFILES.items():
        lc = g.costs[k]
        profiles[k] = {
            'label': p.label,
            'speed_mps': p.speed_mps,
            'description': p.description,
            'cost_fwd': _costs(lc.cost_fwd),
            'cost_bwd': _costs(lc.cost_bwd),
            'time': [float(x) for x in lc.time_fwd],  # 時間は向きに依らない（profiles.py）
            'unknown': [int(x) for x in lc.unknown],
            'component': [int(x) for x in g.components[k]],
        }

    fs, fe = L['floor_start'].to_numpy(), L['floor_end'].to_numpy()
    return {
        'format': STATIC_FORMAT,
        'case': g.case,
        'title': g.title,
        'sources': g.sources,
        'floors': g.floors,
        'lat0': g.lat0,
        'snap': {'radii_m': list(SNAP_RADII_M), 'candidates': SNAP_CANDIDATES},
        'kind_label': guidance.KIND_LABEL,
        'nodes': {
            'lon': [float(x) for x in g.nodes['lon']],
            'lat': [float(x) for x in g.nodes['lat']],
        },
        'links': {
            'id': L['link_id'].astype(str).tolist(),
            'u': g.u.tolist(),
            'v': g.v.tolist(),
            'distance': [float(x) for x in L['distance']],
            'floor_start': [_floor(x) for x in fs],
            'floor_end': [_floor(x) for x in fe],
            'snappable': [int(x) for x in g._snappable],
            'kind': kind,
            'signal': signal,
            'uphill': uphill,
        },
        'profiles': profiles,
    }
