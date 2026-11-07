"""
歩行空間ネットワークデータ（link.csv / node.csv）→ 探索用のリンク・ノード parquet

  uv run python preprocess/build_network.py --all                               # 全地区（重なる地区はまとめる）
  uv run python preprocess/build_network.py --dataset nwd_shinjuku              # 1 地区。case 名 = shinjuku
  uv run python preprocess/build_network.py --dataset nwd_tokyo --dataset nwd_chiyoda_chuo --case tokyo

【重なる地区のまとめ方】
ほこナビDP の地区には、同じ ID のノード・リンクを共有するものがある（2026-11 時点）:
  - 渋谷・千駄ヶ谷・新宿（2021）は、新宿駅周辺（2018）・渋谷南部（2020）・千駄ヶ谷駅周辺（2019）・
    国立競技場周辺（2021）を丸ごと含む
  - 東京駅周辺は千代田区・中央区周辺を含む（後者にしか無いリンクが 2 本）
  - 有明テニスの森 と 国際展示場、お台場海浜公園 と 東京テレポート は境界のノードを共有してつながる
--all はノード ID を共有する地区を 1 つのネットワーク（case）にまとめる。重複は ID で解き、
**リンクは行ごと最も新しいデータのものを採る**。同じ link_id でも新しい版では起点・終点のノード ID が
振り直されていることがある（新宿で 95 本）ので、属性ごとに混ぜると接続が壊れる。ノードは ID の和集合
（同じ ID の座標・階は一致することを確かめた）。

出力（network/<case>/）:
  links.parquet   コード列は int16（空欄は -1）、geometry は WKB の LineString（起点ノード → 終点ノード）。
                  dataset 列に元の地区
  nodes.parquet   node_id, lat, lon, floor, elevation, in_out, dataset
  report.json     検査結果（下記）。datasets・sources に元の地区、title に地区の名前
--all はさらに network/areas.json に地区の一覧を書く（preprocess/export_static.py が読む）

検査して落とすもの:
  - 起点・終点のノードが node.csv に無いリンク
  - 起点と終点が同じノードのリンク
  - 同じ link_id の 2 本目以降
検査して記録だけするもの:
  - distance と 2 点間の測地線長の差（±max(2 m, 20%) を超えるもの）
  - 連結成分の数と大きさ
  - node.csv の接続リンク ID（link*_id）と link.csv の食い違い

【形状について】2024 年 7 月版仕様のリンクは中間点を持たず、GeoJSON 版も全リンクが 2 点。
したがって形状はノード座標から作れば足り、GeoJSON は読まない。
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.codes import LINK_CODE_COLUMNS, MISSING  # noqa: E402
from preprocess import indoor as I  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / 'input' / 'hokonavi'
CATALOG = BASE / 'input' / 'catalog' / 'package_search.json'
NET = BASE / 'network'

LINK_TEXT_COLUMNS = ('rank', 'start_time', 'end_time', 'start_date', 'end_date', 'no_serv_d', 'st_name')
DIST_TOL_M, DIST_TOL_RATIO = 2.0, 0.2
# 地区の名前に入れる目安。まとめた地区のうち、リンクのこの割合以上を出している地区の名前を並べる
TITLE_MIN_SHARE = 0.1


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6_371_008.8
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dlmb = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def read_csv(path: Path) -> pd.DataFrame:
    # ID は 32 桁の英数字。数値に化けないよう全部文字列で読み、必要な列だけ型を付ける
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding='utf-8-sig')


def to_code(s: pd.Series) -> np.ndarray:
    return pd.to_numeric(s.replace('', None), errors='coerce').fillna(MISSING).astype('int16').to_numpy()


def source_of(dataset: str) -> dict | None:
    """カタログからデータセットの名前・ライセンス・最終更新日を引く（画面の出典表示に使う）"""
    if not CATALOG.exists():
        return None
    pkgs = json.loads(CATALOG.read_text(encoding='utf-8'))['result']['results']
    p = next((x for x in pkgs if x['name'] == dataset), None)
    if p is None:
        return None
    m = re.search(r'最終更新日[:：]\s*([0-9/]+)', p.get('notes') or '')
    return {
        'dataset': dataset,
        'title': p['title'],
        'publisher': (p.get('organization') or {}).get('title'),
        'license': p['license_title'],
        'license_url': p['license_url'],
        'data_updated': m.group(1) if m else None,
        'url': f'https://ckan.hokonavi.go.jp/dataset/{p["name"]}',
    }


def load(dataset: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """1 地区の CSV を読み、型を付けて壊れたリンクを落とす（形状・連結成分は finalize）"""
    src = RAW / dataset
    links_raw, nodes_raw = read_csv(src / 'link.csv'), read_csv(src / 'node.csv')
    report: dict = {
        'dataset': dataset,
        'source': source_of(dataset),
        'input': {'links': len(links_raw), 'nodes': len(nodes_raw)},
        'dropped': {},
    }

    # ---- ノード
    nodes = pd.DataFrame(
        {
            'node_id': nodes_raw['node_id'],
            'lat': pd.to_numeric(nodes_raw['lat']),
            'lon': pd.to_numeric(nodes_raw['lon']),
            'floor': pd.to_numeric(nodes_raw['floor'].replace('', None)).astype('float32'),
            'elevation': pd.to_numeric(nodes_raw['elevation'].replace('', None)).astype('float32'),
            'in_out': to_code(nodes_raw['in_out']),
        }
    )
    dup_nodes = nodes['node_id'].duplicated()
    report['dropped']['duplicate_node_id'] = int(dup_nodes.sum())
    nodes = nodes[~dup_nodes].reset_index(drop=True)

    # ---- リンク
    links = pd.DataFrame(
        {
            'link_id': links_raw['link_id'],
            'start_id': links_raw['start_id'],
            'end_id': links_raw['end_id'],
            'distance': pd.to_numeric(links_raw['distance']).astype('float32'),
        }
    )
    for c in LINK_CODE_COLUMNS:
        links[c] = to_code(links_raw[c]) if c in links_raw else np.full(len(links), MISSING, dtype='int16')
    for c in LINK_TEXT_COLUMNS:
        links[c] = links_raw[c] if c in links_raw else ''

    known = set(nodes['node_id'])
    checks = {
        'duplicate_link_id': links['link_id'].duplicated().to_numpy(),
        'dangling_node': ~(links['start_id'].isin(known) & links['end_id'].isin(known)).to_numpy(),
        'self_loop': (links['start_id'] == links['end_id']).to_numpy(),
    }
    drop = np.zeros(len(links), dtype=bool)
    for name, m in checks.items():
        report['dropped'][name] = int((m & ~drop).sum())
        drop |= m
    links = links[~drop].reset_index(drop=True)
    links['dataset'] = dataset
    nodes['dataset'] = dataset

    # ---- node.csv の接続リンク ID との食い違い（記録だけ）
    ref_cols = [c for c in nodes_raw.columns if c.startswith('link') and c.endswith('_id')]
    refs = nodes_raw.melt(id_vars='node_id', value_vars=ref_cols, value_name='link_id')
    refs = refs[refs['link_id'] != '']
    actual = pd.concat(
        [
            links[['start_id', 'link_id']].rename(columns={'start_id': 'node_id'}),
            links[['end_id', 'link_id']].rename(columns={'end_id': 'node_id'}),
        ]
    )
    ref_set = set(zip(refs['node_id'], refs['link_id'], strict=True))
    act_set = set(zip(actual['node_id'], actual['link_id'], strict=True))
    report['node_link_refs'] = {
        'listed_but_not_connected': len(ref_set - act_set),
        'connected_but_not_listed': len(act_set - ref_set),
    }
    return links, nodes, report


def finalize(links: pd.DataFrame, nodes: pd.DataFrame, report: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """形状・階・距離の検査・連結成分を足す（1 地区でも、まとめた地区でも同じ）"""
    links = links.reset_index(drop=True)
    nodes = nodes.reset_index(drop=True)

    # ---- 形状と測地線長
    pos = nodes.set_index('node_id')
    s, e = pos.loc[links['start_id']], pos.loc[links['end_id']]
    coords = np.stack([np.column_stack([s['lon'], s['lat']]), np.column_stack([e['lon'], e['lat']])], axis=1)
    links['geometry'] = shapely.to_wkb(shapely.linestrings(coords))
    links['floor_start'] = s['floor'].to_numpy()
    links['floor_end'] = e['floor'].to_numpy()
    geod = haversine_m(s['lat'].to_numpy(), s['lon'].to_numpy(), e['lat'].to_numpy(), e['lon'].to_numpy())
    diff = np.abs(links['distance'].to_numpy() - geod)
    off = diff > np.maximum(DIST_TOL_M, DIST_TOL_RATIO * geod)
    # エレベーターなど上下移動のリンクは平面距離が 0 に近いので、差が出て当然。数は分けて記録する
    vertical = (links['floor_start'] != links['floor_end']).to_numpy()
    report['distance_mismatch'] = {
        'count': int((off & ~vertical).sum()),
        'vertical_links': int((off & vertical).sum()),
        'examples': links.loc[off & ~vertical, 'link_id'].head(5).tolist(),
    }

    # ---- 連結成分（無向）
    idx = pd.Index(nodes['node_id'])
    i, j = idx.get_indexer(links['start_id']), idx.get_indexer(links['end_id'])
    n = len(nodes)
    g = coo_matrix((np.ones(len(links)), (i, j)), shape=(n, n))
    _, comp = connected_components(g, directed=False)
    used = np.zeros(n, dtype=bool)
    used[i] = used[j] = True
    sizes = np.bincount(comp[used])
    sizes = np.sort(sizes[sizes > 0])[::-1]
    report['components'] = {
        'count': int(len(sizes)),
        'largest_nodes': int(sizes[0]),
        'largest_ratio': round(float(sizes[0] / used.sum()), 4),
        'sizes_top10': sizes[:10].tolist(),
    }
    report['isolated_nodes'] = int((~used).sum())
    report['output'] = {
        'links': len(links),
        'nodes': len(nodes),
        'total_km': round(float(links['distance'].sum()) / 1000, 2),
    }
    return links, nodes, report


def _updated(report: dict) -> str:
    """最終更新日（YYYY/MM/DD）。不明は最も古い扱い"""
    return (report.get('source') or {}).get('data_updated') or '0000/00/00'


def build(datasets: list[str], indoor: list[str] = ()) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """1 つ以上の地区を 1 つのネットワークにする。重複はリンク・ノードとも最も新しい地区のものを採る。
    indoor に屋内地図オープンデータ（input/indoor/）を挙げると、座標と階で屋外につないで足す（preprocess/indoor.py）"""
    loaded = sorted((load(d) for d in datasets), key=lambda x: _updated(x[2]), reverse=True)
    links = pd.concat([x[0] for x in loaded], ignore_index=True).drop_duplicates('link_id', keep='first')
    nodes = pd.concat([x[1] for x in loaded], ignore_index=True).drop_duplicates('node_id', keep='first')
    per = []
    for _, _, r in loaded:
        used = int((links['dataset'] == r['dataset']).sum())
        per.append({**{k: r[k] for k in ('dataset', 'input', 'dropped', 'node_link_refs')}, 'links_used': used})
    used_ds = {p['dataset'] for p in per if p['links_used'] > 0}
    report: dict = {
        'datasets': [r['dataset'] for _, _, r in loaded],
        # 出典に挙げるのはリンクを 1 本でも採った地区だけ（新しい版に丸ごと含まれる古い地区は使っていない）
        'sources': [r['source'] for _, _, r in loaded if r['source'] and r['dataset'] in used_ds],
        'per_dataset': per,
    }
    # リンクは行ごと新しい版を採ったので、どのリンクも端点はノードにあるはず（念のため確かめる）
    known = set(nodes['node_id'])
    missing = ~(links['start_id'].isin(known) & links['end_id'].isin(known))
    if missing.any():
        raise ValueError(f'まとめた結果、端点のノードが無いリンクが {int(missing.sum())} 本あります')
    # 古い版にしか無いノード（新しい版で ID が振り直されたもの）は、どのリンクからも使われない。落とす
    used = set(links['start_id']) | set(links['end_id'])
    unused = ~nodes['node_id'].isin(used)
    report['dropped_unused_nodes'] = int(unused.sum())
    nodes = nodes[~unused]
    report['indoor'] = []
    for d in indoor:
        il, inn, r = I.load(d)
        links, nodes, st = I.attach(links, nodes, il, inn)
        report['indoor'].append({**{k: r[k] for k in ('dataset', 'input', 'dropped')}, **st})
        if r['source']:
            report['sources'].append(r['source'])
    return finalize(links, nodes, report)


def clusters(datasets: list[str]) -> list[list[str]]:
    """ノード ID を共有する地区どうしをまとめる（Union-Find）。各グループは大きい地区から、グループは大きい順"""
    parent = {d: d for d in datasets}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[str, str] = {}
    size: dict[str, int] = {}
    for d in datasets:
        ids = read_csv(RAW / d / 'node.csv')['node_id']
        size[d] = len(ids)
        for i in ids:
            if i in owner:
                parent[find(d)] = find(owner[i])
            else:
                owner[i] = d
    groups: dict[str, list[str]] = {}
    for d in datasets:
        groups.setdefault(find(d), []).append(d)
    out = [sorted(g, key=lambda d: -size[d]) for g in groups.values()]
    return sorted(out, key=lambda g: -sum(size[d] for d in g))


def short_title(title: str) -> str:
    """「歩行空間ネットワークデータ（新宿駅周辺）」→「新宿駅周辺」"""
    m = re.search(r'（(.*)）\s*$', title)
    return m.group(1) if m else title


def area_title(report: dict) -> str:
    """まとめた地区の名前。リンクの TITLE_MIN_SHARE 以上を出している地区の名前を「／」でつなぐ"""
    titles = {d: short_title(s['title']) for d in report['datasets'] if (s := source_of(d))}
    total = sum(p['links_used'] for p in report['per_dataset'])
    names = [
        titles.get(p['dataset'], p['dataset'])
        for p in report['per_dataset']
        if p['links_used'] >= TITLE_MIN_SHARE * total
    ]
    return '／'.join(dict.fromkeys(names))


def write(case: str, links: pd.DataFrame, nodes: pd.DataFrame, report: dict) -> None:
    out = NET / case
    out.mkdir(parents=True, exist_ok=True)
    links.to_parquet(out / 'links.parquet', index=False)
    nodes.to_parquet(out / 'nodes.parquet', index=False)
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dataset', action='append', help='input/hokonavi/ 以下のデータセット名（複数でまとめる）')
    ap.add_argument('--all', action='store_true', help='input/hokonavi/nwd_* を全部。重なる地区はまとめる')
    ap.add_argument('--case', help='出力名（network/<case>/）。省略時は最も大きい地区の名前から nwd_ を外したもの')
    args = ap.parse_args()
    if bool(args.all) == bool(args.dataset):
        ap.error('--all か --dataset のどちらかを指定してください')

    if args.all:
        groups = clusters(sorted(p.name for p in RAW.glob('nwd_*') if p.is_dir()))
    else:
        groups = [args.dataset]
    areas = []
    for g in groups:
        case = (None if args.all else args.case) or g[0].removeprefix('nwd_')
        # 屋内地図オープンデータは、取得済み（preprocess/fetch_indoor.py）のものだけ足す
        indoor = [d for d, c in I.INDOOR_CASE.items() if c == case and (I.RAW / d / 'nw').is_dir()]
        links, nodes, report = build(g, indoor)
        report['title'] = area_title(report)
        write(case, links, nodes, report)
        lon, lat = nodes['lon'], nodes['lat']
        areas.append(
            {
                'case': case,
                'title': report['title'],
                'datasets': report['datasets'],
                'links': len(links),
                'bbox': [round(float(x), 6) for x in (lon.min(), lat.min(), lon.max(), lat.max())],
            }
        )
        c = report['components']
        print(
            f'{case:28} {len(g)} 地区 → リンク {len(links):6,} ノード {len(nodes):6,} '
            f'成分 {c["count"]:3}  {report["title"]}',
            file=sys.stderr,
        )
        for r in report['indoor']:
            print(
                f'{"":28} ＋ {r["dataset"]}: リンク {r["links_added"]:,} を追加'
                f'（{r["merged_nodes"]} ノードで屋外とつなぐ）',
                file=sys.stderr,
            )
    if args.all:
        (NET / 'areas.json').write_text(json.dumps(areas, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
