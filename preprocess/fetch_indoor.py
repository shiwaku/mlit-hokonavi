"""
屋内地図オープンデータ（G空間情報センター）の歩行者ネットワーク（nw/*_node・*_link の Shapefile）を取得する

国土交通省の屋内地図オープンデータは、国土地理院「階層別屋内地理空間情報データ仕様書（案）」に基づく
屋内地図と、それに沿った歩行者ネットワークを含む。ネットワークの属性は「歩行空間ネットワークデータ等
整備仕様案」（2018 年 3 月）の項目に準じる（階は floor でなく ordinal）。経路探索には nw/ だけを使う。

  uv run python preprocess/fetch_indoor.py            # INDOOR の全データセット
  uv run python preprocess/fetch_indoor.py --force    # 取り直す

出力: input/indoor/<データセット名>/package.json（カタログ）、nw/*.{shp,shx,dbf,prj}
G空間情報センターの案内では、ダウンロードはユーザー登録・ログインのあとに行うこととされている。
環境変数 GEOSPATIAL_API_TOKEN に CKAN の API トークンがあれば、それを付けて取る。
"""

import argparse
import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CKAN = 'https://www.geospatial.jp/ckan'
OUT = BASE / 'input' / 'indoor'

# データセット名 → ネットワークを含む Shapefile のリソース名に含まれる語。
# 新宿は「Shapefile」と「統合版（Shapefile）」の 2 つがあり、統合版の方がリンクが 8 本多い（2,549 本）
INDOOR = {
    'mlit-indoor-shinjuku-r2': '統合版（Shapefile）',
    'mlit-indoor-tokyo-r2': '（Shapefile）',
    'mlit-indoor-shin-yokohama': '（Shapefile）',
}
NW_EXT = ('.shp', '.shx', '.dbf', '.prj')


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'hokonavi-route-search'})
    token = os.environ.get('GEOSPATIAL_API_TOKEN')
    if token and url.startswith(CKAN):
        req.add_header('Authorization', token)
    with urllib.request.urlopen(req) as r:
        return r.read()


def _zip_name(i: zipfile.ZipInfo) -> str:
    """Shift_JIS のファイル名（UTF-8 フラグなし）を読めるようにする"""
    return i.filename if i.flag_bits & 0x800 else i.filename.encode('cp437').decode('cp932')


def fetch(name: str, keyword: str, force: bool) -> Path:
    out = OUT / name
    if not force and any((out / 'nw').glob('*.shp')):
        return out
    pkg = json.loads(_get(f'{CKAN}/api/3/action/package_show?id={name}'))['result']
    out.mkdir(parents=True, exist_ok=True)
    (out / 'package.json').write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding='utf-8')
    res = [r for r in pkg['resources'] if r['name'].endswith(keyword) and 'Shapefile' in r['name']]
    if len(res) != 1:
        sys.exit(f'{name}: 「{keyword}」のリソースが {len(res)} 件あります')
    with zipfile.ZipFile(io.BytesIO(_get(res[0]['url']))) as z:
        files = [i for i in z.infolist() if '/nw/' in _zip_name(i) and _zip_name(i).lower().endswith(NW_EXT)]
        if not files:
            sys.exit(f'{name}: Shapefile に nw/（歩行者ネットワーク）がありません')
        (out / 'nw').mkdir(exist_ok=True)
        for i in files:
            (out / 'nw' / _zip_name(i).rsplit('/', 1)[-1]).write_bytes(z.read(i))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--force', action='store_true', help='既にあるファイルも取り直す')
    args = ap.parse_args()
    for name, kw in INDOOR.items():
        out = fetch(name, kw, args.force)
        print(f'{name:28} {sorted(p.name for p in (out / "nw").glob("*.shp"))}', file=sys.stderr)


if __name__ == '__main__':
    main()
