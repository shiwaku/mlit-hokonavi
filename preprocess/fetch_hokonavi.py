"""
ほこナビDP（歩行空間ナビゲーションデータプラットフォーム）のカタログと歩行空間ネットワークデータを取得する

カタログは CKAN（https://ckan.hokonavi.go.jp）の API から取る。データセット名が nwd_ で始まるものが
歩行空間ネットワークデータ（link.csv / node.csv / *.geojson）。リンクの形状は 2 点の直線なので CSV だけ取る。

  uv run python preprocess/fetch_hokonavi.py                      # カタログ ＋ nwd_* 全地区の CSV
  uv run python preprocess/fetch_hokonavi.py --dataset nwd_shinjuku
  uv run python preprocess/fetch_hokonavi.py --list               # 取得済みカタログの一覧だけ

出力: input/catalog/package_search.json、input/hokonavi/<データセット名>/{link,node}.csv
既にあるファイルは取り直さない（--force で取り直す）。
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CKAN = 'https://ckan.hokonavi.go.jp/api/3/action/package_search?rows=1000'
CATALOG = BASE / 'input' / 'catalog' / 'package_search.json'
RAW = BASE / 'input' / 'hokonavi'
NETWORK_PREFIX = 'nwd_'
FILES = ('link.csv', 'node.csv')


def fetch_catalog(force: bool) -> list[dict]:
    if force or not CATALOG.exists():
        CATALOG.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(CKAN, CATALOG)
    body = json.loads(CATALOG.read_text(encoding='utf-8'))
    return body['result']['results']


def network_datasets(packages: list[dict]) -> list[dict]:
    return [p for p in packages if p['name'].startswith(NETWORK_PREFIX)]


def download(pkg: dict, force: bool) -> list[Path]:
    out = RAW / pkg['name']
    out.mkdir(parents=True, exist_ok=True)
    got = []
    for r in pkg['resources']:
        fname = r['url'].rsplit('/', 1)[-1]
        if fname not in FILES:
            continue
        path = out / fname
        if force or not path.exists():
            urllib.request.urlretrieve(r['url'], path)
        got.append(path)
    missing = set(FILES) - {p.name for p in got}
    if missing:
        print(f'  ⚠ {pkg["name"]}: {sorted(missing)} がカタログにありません', file=sys.stderr)
    return got


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dataset', action='append', help='取得するデータセット名（複数可）。省略時は nwd_* 全部')
    ap.add_argument('--list', action='store_true', help='一覧を出すだけでダウンロードしない')
    ap.add_argument('--force', action='store_true', help='既にあるファイルも取り直す')
    args = ap.parse_args()

    pkgs = network_datasets(fetch_catalog(args.force))
    if args.dataset:
        unknown = set(args.dataset) - {p['name'] for p in pkgs}
        if unknown:
            sys.exit(f'カタログにないデータセット: {sorted(unknown)}')
        pkgs = [p for p in pkgs if p['name'] in args.dataset]

    for p in pkgs:
        print(f'{p["name"]:40} {p["title"]}  ({p["metadata_modified"][:10]})')
        if not args.list:
            download(p, args.force)
    print(f'{len(pkgs)} データセット', file=sys.stderr)


if __name__ == '__main__':
    main()
