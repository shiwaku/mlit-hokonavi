"""
整備したネットワーク → 画面（GitHub Pages 版）が読む静的ファイル

  uv run python preprocess/export_static.py --case shinjuku

出力（web/static/data/）:
  areas.json              地区の一覧（画面が最初に読む）
  <case>/graph.json       探索用（利用者別のコスト・連結成分・案内用の属性）。形式は api/export.py
  <case>/network.geojson  表示用（API の /network と同じ）

web/static/data/ は生成物なので git に入れない。GitHub Pages へのデプロイ時に作り直す
（.github/workflows/pages.yml）。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.export import network_geojson, static_graph  # noqa: E402
from api.graph import PedestrianGraph  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'web' / 'static' / 'data'


def dump(obj: dict, path: Path) -> int:
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    return path.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--case', action='append', required=True, help='network/<case>/ の名前（複数可。先頭が既定の地区）')
    ap.add_argument('--out', type=Path, default=OUT)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    areas = []
    for case in args.case:
        g = PedestrianGraph(case)
        d = args.out / case
        d.mkdir(parents=True, exist_ok=True)
        size_g = dump(static_graph(g), d / 'graph.json')
        size_n = dump(network_geojson(g), d / 'network.geojson')
        title = (g.source or {}).get('title', case)
        areas.append({'case': case, 'title': title, 'links': g.n_links})
        print(f'{case}: graph.json {size_g / 1e6:.2f} MB / network.geojson {size_n / 1e6:.2f} MB', file=sys.stderr)
    dump({'default': args.case[0], 'areas': areas}, args.out / 'areas.json')


if __name__ == '__main__':
    main()
