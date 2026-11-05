"""
整備したネットワーク → 画面（GitHub Pages 版）が読む静的ファイル

  uv run python preprocess/export_static.py --all                 # network/areas.json の全地区
  uv run python preprocess/export_static.py --case shinjuku       # 指定した地区だけ（複数可）

出力（web/static/data/）:
  areas.json              地区の一覧（名前・範囲・リンク数）。画面が最初に読み、地図に地区の位置を出す
  <case>/graph.json       探索用（利用者別のコスト・連結成分・案内用の属性）。形式は api/export.py
  <case>/network.geojson  表示用（API の /network と同じ）

画面は選んだ地区の graph.json・network.geojson だけを読む（全地区を一度には読まない）。
web/static/data/ は生成物なので git に入れない。GitHub Pages へのデプロイ時に作り直す
（.github/workflows/pages.yml）。
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.export import network_geojson, static_graph  # noqa: E402
from api.graph import PedestrianGraph  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
NET = BASE / 'network'
OUT = BASE / 'web' / 'static' / 'data'
# 最初に開く地区（あれば）。無ければリンク数が最も多い地区
DEFAULT_CASE = 'shibuya_sendagaya_shinjuku'


def dump(obj, path: Path) -> int:
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    return path.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--all', action='store_true', help='network/areas.json（build_network.py --all）の全地区')
    ap.add_argument('--case', action='append', help='network/<case>/ の名前（複数可）')
    ap.add_argument('--out', type=Path, default=OUT)
    args = ap.parse_args()
    if bool(args.all) == bool(args.case):
        ap.error('--all か --case のどちらかを指定してください')
    cases = [a['case'] for a in json.loads((NET / 'areas.json').read_text(encoding='utf-8'))] if args.all else args.case

    # 前回の地区が残らないよう作り直す
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    areas = []
    total = 0
    for case in cases:
        g = PedestrianGraph(case)
        d = args.out / case
        d.mkdir()
        size = dump(static_graph(g), d / 'graph.json') + dump(network_geojson(g), d / 'network.geojson')
        total += size
        lon, lat = g.nodes['lon'], g.nodes['lat']
        areas.append(
            {
                'case': case,
                'title': g.title,
                'links': g.n_links,
                'bbox': [round(float(x), 6) for x in (lon.min(), lat.min(), lon.max(), lat.max())],
                'data_updated': max((s.get('data_updated') or '' for s in g.sources), default=None) or None,
            }
        )
        print(f'{case:32} {g.n_links:6,} リンク  {size / 1e6:5.2f} MB', file=sys.stderr)
    areas.sort(key=lambda a: -a['links'])
    default = DEFAULT_CASE if any(a['case'] == DEFAULT_CASE for a in areas) else areas[0]['case']
    dump({'default': default, 'areas': areas}, args.out / 'areas.json')
    print(f'{len(areas)} 地区  計 {total / 1e6:.1f} MB', file=sys.stderr)


if __name__ == '__main__':
    main()
