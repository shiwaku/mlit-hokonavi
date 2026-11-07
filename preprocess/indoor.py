"""
屋内地図オープンデータの歩行者ネットワーク（input/indoor/<データセット>/nw/）を読み、屋外のネットワークにつなぐ

【読み方】属性は「歩行空間ネットワークデータ等整備仕様案」（2018 年 3 月）の項目で、多くのコードは
2024 年 7 月版と同じ。違うのは次の 2 つで、2024 年版のコードに読み替える（api/codes.py）:
  - 縦断勾配 vtcl_slope  旧 1: 5% 以下 → 2（0〜5%）。旧 2・3: 5% より大きい（上り・下り）→ 99（不明）
  - 段差 lev_diff        旧 1: 2cm 以下 → 2（2cm 以下）。旧 2: 2cm より大きい → 99（不明）
  「5% より大きい」「2cm より大きい」は車いすで通れるか（8%・5cm）を決められないので不明とし、
  ほかの不明と同じく「通れるが避ける」「経路に含まれたら警告」にする。
エレベーターは乗り場とかごを結ぶ数本のリンクで表され、1 基の中で種別（elevator）が食い違うことが多い
（新宿は 23 基中 18 基、東京は 79 基中 47 基。階をまたぐリンクだけ「2: バリアフリー対応なし」で、乗り場側は
「3: 車いす使用者対応」など）。どちらが正しいかはデータから決められないので、エレベーターのリンクで
つながる 1 基の中で種別が食い違うときは、その 1 基をすべて 99（不明）にする。
階は ordinal（屋外の地表が 0、建物の 1 階が 1、地下 1 階が -1）。歩行空間ネットワークデータの floor と
同じ数え方なので、そのまま floor に入れる。エレベーターの distance は 0（待ち時間だけがかかる）。
リンクの形状は屋外と同じく起点ノードと終点ノードを結ぶ直線にする（中間点を持つリンクは 15% ほど）。

【つなぎ方】屋内と屋外のネットワークは ID を共有しない。座標と階で次のようにつなぐ:
  1. 屋内のノードと、MERGE_M 以内にある**階が同じ**屋外のノードを、近い組から順に 1 対 1 で
     同じノードとみなす（屋内のノードを屋外のノードに置き換える。座標は屋外のものを使う）
  2. 置き換えた結果、屋外に同じ 2 点を結ぶリンクがある屋内のリンクは落とす（同じ通路が両方にある）
1 対 1 にするのは、近い屋内ノード 2 つを同じ屋外ノードに寄せると、そのあいだの短い階段などが
消えてしまうため。階は値が同じものだけをつなぐ。仕様では建物の 1 階は 1・屋外の地表は 0 だが、
東京駅には 0 と 1 を結ぶエレベーター・階段があり、1 と 0 を同じ高さとはみなせない。
座標だけでつなぐと階の違う通路を誤ってつなぐおそれがある（README 前提⑨）ので、階が違う組はつながない。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import shapefile
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from api.codes import LINK_CODE_COLUMNS, MISSING, RT_ELEVATOR, UNKNOWN

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / 'input' / 'indoor'
# 1 m でも 3 地区とも屋内のリンクの 99% 以上が屋外とつながる。2 m で新宿 186・東京 165・新横浜 16 ノードを寄せる
MERGE_M = 2.0
# 屋内地図オープンデータ → それを足す地区（network/<case>/）
INDOOR_CASE = {
    'mlit-indoor-shinjuku-r2': 'shibuya_sendagaya_shinjuku',
    'mlit-indoor-tokyo-r2': 'tokyo',
    'mlit-indoor-shin-yokohama': 'shin-yokohama',
}
M_PER_DEG_LAT = 110_950.0

# 2018 年 3 月版 → 2024 年 7 月版のコード（ここに無いコードはそのまま）
OLD_CODES = {
    'vtcl_slope': {1: 2, 2: UNKNOWN, 3: UNKNOWN},
    'lev_diff': {1: 2, 2: UNKNOWN},
}


def _read(path: Path) -> pd.DataFrame:
    r = shapefile.Reader(str(path), encoding='utf-8')
    fields = [f[0] for f in r.fields[1:]]
    df = pd.DataFrame([list(rec) for rec in r.records()], columns=fields)
    df['_xy'] = [s.points[0] if s.points else (np.nan, np.nan) for s in r.shapes()]
    return df


def _code(s: pd.Series) -> np.ndarray:
    return pd.to_numeric(s.astype(str).str.strip().replace('', None), errors='coerce').fillna(MISSING).to_numpy()


def source_of(dataset: str) -> dict | None:
    """G空間情報センターのカタログから、名前・発行者・利用規約・公開日を引く（画面の出典表示）"""
    p = RAW / dataset / 'package.json'
    if not p.exists():
        return None
    pkg = json.loads(p.read_text(encoding='utf-8'))
    terms = next((r['url'] for r in pkg['resources'] if r['name'] == '利用規約'), None)
    shp = [r for r in pkg['resources'] if 'Shapefile' in r['name']]
    created = max((r.get('created') or '' for r in shp), default='')
    return {
        'dataset': dataset,
        'title': pkg['title'],
        'publisher': '国土交通省',
        'license': '利用規約（政府標準利用規約 第2.0版に準拠）',
        'license_url': terms,
        'data_updated': created[:10].replace('-', '/') or None,
        'url': f'https://www.geospatial.jp/ckan/dataset/{dataset}',
    }


def load(dataset: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """1 データセットの nw/ を build_network.load と同じ列にする"""
    d = RAW / dataset / 'nw'
    node_shp = next(p for p in d.glob('*.shp') if p.stem.lower().endswith('_node'))
    link_shp = next(p for p in d.glob('*.shp') if p.stem.lower().endswith('_link'))
    nr, lr = _read(node_shp), _read(link_shp)
    report: dict = {'dataset': dataset, 'source': source_of(dataset), 'input': {'links': len(lr), 'nodes': len(nr)}}

    xy = np.array(nr['_xy'].tolist(), dtype=float)
    nodes = pd.DataFrame(
        {
            'node_id': nr['node_id'].astype(str),
            # 属性の lat / lon は丸めてあることがあるので、点の座標を使う（JGD2011 緯度経度）
            'lat': xy[:, 1],
            'lon': xy[:, 0],
            'floor': pd.to_numeric(nr['ordinal'], errors='coerce').astype('float32'),
            'elevation': np.full(len(nr), np.nan, dtype='float32'),
            'in_out': _code(nr['in_out']).astype('int16'),
        }
    )
    dup_nodes = nodes['node_id'].duplicated()
    nodes = nodes[~dup_nodes].reset_index(drop=True)

    links = pd.DataFrame(
        {
            'link_id': lr['link_id'].astype(str),
            'start_id': lr['start_id'].astype(str),
            'end_id': lr['end_id'].astype(str),
            'distance': pd.to_numeric(lr['distance'], errors='coerce').fillna(0).astype('float32'),
        }
    )
    for c in LINK_CODE_COLUMNS:
        v = _code(lr[c]) if c in lr else np.full(len(lr), MISSING)
        if c in OLD_CODES:
            v = pd.Series(v).replace(OLD_CODES[c]).to_numpy()
        links[c] = v.astype('int16')
    for c in ('rank', 'start_time', 'end_time', 'start_date', 'end_date', 'no_serv_d', 'st_name'):
        links[c] = ''

    known = set(nodes['node_id'])
    checks = {
        'duplicate_link_id': links['link_id'].duplicated().to_numpy(),
        'dangling_node': ~(links['start_id'].isin(known) & links['end_id'].isin(known)).to_numpy(),
        'self_loop': (links['start_id'] == links['end_id']).to_numpy(),
    }
    drop = np.zeros(len(links), dtype=bool)
    report['dropped'] = {'duplicate_node_id': int(dup_nodes.sum())}
    for name, m in checks.items():
        report['dropped'][name] = int((m & ~drop).sum())
        drop |= m
    links = links[~drop].reset_index(drop=True)
    links['elevator'], report['elevators'] = unify_elevators(links)
    links['dataset'] = dataset
    nodes['dataset'] = dataset
    return links, nodes, report


def unify_elevators(links: pd.DataFrame) -> tuple[np.ndarray, dict]:
    """エレベーター（route_type 4）のリンクでつながる 1 基の中で種別が食い違えば、その 1 基を 99（不明）にする"""
    code = links['elevator'].to_numpy().copy()
    el = np.flatnonzero(links['route_type'].to_numpy() == RT_ELEVATOR)
    ids = pd.Index(pd.unique(pd.concat([links['start_id'].iloc[el], links['end_id'].iloc[el]])))
    i, j = ids.get_indexer(links['start_id'].iloc[el]), ids.get_indexer(links['end_id'].iloc[el])
    n, comp = connected_components(coo_matrix((np.ones(len(el)), (i, j)), shape=(len(ids),) * 2), directed=False)
    groups = pd.Series(code[el]).groupby(comp[i]).agg(lambda s: len({v for v in s if v not in (UNKNOWN, MISSING)}))
    mixed = groups.index[groups > 1]
    code[el[np.isin(comp[i], mixed)]] = UNKNOWN
    return code, {'count': int(n), 'mixed_to_unknown': len(mixed)}


def _xy_m(lon: np.ndarray, lat: np.ndarray, lat0: float) -> np.ndarray:
    return np.column_stack([lon * M_PER_DEG_LAT * np.cos(np.radians(lat0)), lat * M_PER_DEG_LAT])


def match_nodes(in_xy: np.ndarray, f_in: np.ndarray, out_xy: np.ndarray, f_out: np.ndarray, r: float) -> np.ndarray:
    """屋内ノードごとに、同じとみなす屋外ノードの行番号（無ければ -1）。r 以内・同じ階・近い組から 1 対 1"""
    pairs = cKDTree(in_xy).query_ball_tree(cKDTree(out_xy), r)
    cand = [(float(np.linalg.norm(in_xy[i] - out_xy[o])), i, o) for i, os in enumerate(pairs) for o in os]
    target = np.full(len(in_xy), -1)
    taken: set[int] = set()
    # 距離が同じなら行番号の小さい方（結果を毎回同じにするため）
    for _, i, o in sorted(c for c in cand if f_in[c[1]] == f_out[c[2]]):
        if target[i] < 0 and o not in taken:
            target[i] = o
            taken.add(o)
    return target


def attach(
    links: pd.DataFrame,
    nodes: pd.DataFrame,
    in_links: pd.DataFrame,
    in_nodes: pd.DataFrame,
    merge_m: float = MERGE_M,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """屋内のネットワークを屋外のネットワークに足す（つなぎ方はモジュールの説明）"""
    lat0 = float(nodes['lat'].mean())
    out_xy = _xy_m(nodes['lon'].to_numpy(), nodes['lat'].to_numpy(), lat0)
    in_xy = _xy_m(in_nodes['lon'].to_numpy(), in_nodes['lat'].to_numpy(), lat0)
    io_in = in_nodes['in_out'].to_numpy()
    target = match_nodes(in_xy, in_nodes['floor'].to_numpy(), out_xy, nodes['floor'].to_numpy(), merge_m)

    merged = target >= 0
    ren = dict(zip(in_nodes['node_id'][merged], nodes['node_id'].to_numpy()[target[merged]], strict=True))
    il = in_links.copy()
    il['start_id'] = il['start_id'].replace(ren)
    il['end_id'] = il['end_id'].replace(ren)

    # 1 対 1 で寄せるので、起点と終点が同じになるリンクはできない
    def pairs(df: pd.DataFrame) -> pd.Series:
        return pd.Series([tuple(sorted(p)) for p in zip(df['start_id'], df['end_id'], strict=True)])

    dup = pairs(il).isin(set(pairs(links))).to_numpy()
    il = il[~dup]
    in_kept = in_nodes[~merged]

    stats = {
        'merge_m': merge_m,
        'merged_nodes': int(merged.sum()),
        'merged_by_in_out': {int(k): int(v) for k, v in pd.Series(io_in[merged]).value_counts().sort_index().items()},
        'dropped_same_as_outdoor': int(dup.sum()),
        'links_added': len(il),
        'nodes_added': len(in_kept),
    }
    return (
        pd.concat([links, il], ignore_index=True),
        pd.concat([nodes, in_kept], ignore_index=True),
        stats,
    )
