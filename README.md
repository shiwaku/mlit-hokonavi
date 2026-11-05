# Hokonavi Route Search — 歩行空間ネットワークデータによる歩行者経路探索

国土交通省の[歩行空間ナビゲーションデータプラットフォーム（ほこナビDP）](https://www.hokonavi.go.jp/)で公開されている
**歩行空間ネットワークデータ**を使い、利用者（一般・車いす・ベビーカー・視覚障害）ごとに経路を探して案内する。

**公開版: https://shiwaku.github.io/mlit-hokonavi/**（GitHub Pages・サーバーなし。探索はブラウザ内）

構成は [shiwaku/ksj-route-search-api](https://github.com/shiwaku/ksj-route-search-api) にそろえている（preprocess / api / web）。
**現在は新宿駅周辺（`nwd_shinjuku`）の 1 地区だけ**。全地区への展開は「今後」を参照。

### 2 つの動かし方

| | 公開版（GitHub Pages） | API 版（ローカル・ほかのアプリから呼ぶ） |
|---|---|---|
| 探索する場所 | **ブラウザ**（`web/src/lib/routing/`・TypeScript） | **FastAPI**（`api/`・Python） |
| データ | 前処理で書き出した静的 JSON（`web/static/data/`） | `network/<case>/*.parquet` |
| 通れるか・コスト | どちらも **`api/profiles.py` の結果**（静的 JSON に配列で書き出す） | 同左 |

判断（通れるか・コスト）は Python の 1 か所だけに置き、TypeScript に移したのはスナップ・Dijkstra・案内文だけ。
両者が**同じ結果を返すことをテストで確かめている**（合成ネットワークの全ケース、新宿の実データでランダムな 200 経路が完全一致・`tests/test_parity.py`）。

---

## 分析上の前提

| # | 前提 | 理由 |
|---|---|---|
| ① | ネットワークは**ほこナビDP の公開データをそのまま使う**（2024 年 7 月版の整備仕様） | 整備済みの属性（段差・勾配・幅員・階段・エレベーター・信号）を経路のコストに直接使える |
| ② | リンクの形状は**起点ノードと終点ノードを結ぶ直線** | 2024 年 7 月版のリンクは中間点を持たない（GeoJSON 版も全リンク 2 点） |
| ③ | **有向グラフ**で探す。1 リンクにつき 起点→終点 / 終点→起点 の 2 エッジ | 一方通行（動く歩道・エスカレーターなど）と勾配の上り下りに向きがある |
| ④ | 出発地・目的地は**最寄りのリンク上の点**に寄せる（半径 50 m）。同じ連結成分に乗らなければ 100・200 m まで広げ、50 m を超えたら警告を出す | データは連結成分が分かれている（新宿で 14、車いすで階段を除くと 150）。デッキ上など階段でしか出られない場所に寄せると到達不能になる |
| ⑤ | 所要時間 = 距離 ÷ 歩行速度 ＋ 待ち時間（エレベーター 30 秒・信号のある横断歩道 20 秒） | 速度と待ち時間は**仮の値**（`api/profiles.py`）。根拠のある値に置き換える前提 |
| ⑥ | 属性が「不明（99）」・空欄の区間は**通れるが避ける**（コスト 2 倍）。経路に含まれたら警告する | 不明を通行止めにすると、ほとんどの経路が切れる |
| ⑦ | 階（`floor`）は**データの値をそのまま**表示する（0 = 地上、負 = 地下） | 仕様では屋外の地上部分が 0。建物の何階かとの対応はデータからは決まらない |
| ⑧ | 供用時間（`start_time` など）と通行制限曜日は**未対応** | 新宿のデータはすべて空欄 |

### 利用者ごとの通れない区間・避ける区間

| | 通れない | 避ける（コストの係数） |
|---|---|---|
| 一般 | 一方通行の逆走 | — |
| 車いす | 階段・エスカレーター・段差 5 cm 超・勾配 8 % 超・幅 1 m 未満・車いす非対応のエレベーター | 段差 2〜5 cm ×1.5、幅 1〜2 m ×1.2、勾配 5〜8 % 上り ×2.0・下り ×1.5 |
| ベビーカー | 階段・エスカレーター・段差 10 cm 超・勾配 18 % 超 | 段差 5〜10 cm ×2.0、幅 1 m 未満 ×1.5、勾配 8〜18 % の上り ×1.5 |
| 視覚障害 | 一方通行の逆走 | 誘導用ブロックなし ×1.5、音響設備のない信号 ×2.0、横断歩道のない横断 ×3.0、エスカレーター ×1.5 |

全員に共通: 通り抜けが好ましくない通路・有料の通路 ×1.5、踏切 ×1.5。

---

## 実測値（新宿駅周辺・2026-11-05）

| 項目 | 値 |
|---|---|
| リンク / ノード | 1,866 / 1,720（総延長 27.8 km） |
| 欠損・重複・距離の食い違い | なし（`network/shinjuku/report.json`） |
| 連結成分 | 14（最大成分がノードの 82 %） |
| 階 | 地下 1・地下 0.5・地上・0.5・1 |
| API の起動（グラフ読込） | 0.04 秒 |
| 経路探索 1 回 | 2〜4 ms（西新宿 → 新宿三丁目 約 1.7 km。Python・ブラウザとも） |
| 公開版のデータ | graph.json 0.61 MB・network.geojson 0.95 MB（gzip で 86 KB・59 KB） |

---

## 構成

```
.
├── preprocess/   データ取得・ネットワーク整備・静的ファイルの書き出し
├── api/          FastAPI（graph.py 探索グラフ / profiles.py 利用者別コスト / guidance.py 案内文 / export.py 書き出し / main.py）
├── web/          SvelteKit + Tailwind + MapLibre（地理院 最適化ベクトルタイル 淡色）。src/lib/routing/ がブラウザ内の探索
├── tests/        pytest（合成ネットワークでの単体テスト・API の結合テスト・ブラウザ版との一致用フィクスチャ）
├── .github/      CI（ci.yml）と GitHub Pages へのデプロイ（pages.yml）
├── docs/         API 設計書（api-design.md）
├── input/        ほこナビDP の原データ（gitignored）
└── network/      整備したネットワーク（gitignored）
```

### preprocess

| スクリプト | 役割 |
|---|---|
| `fetch_hokonavi.py` | ほこナビDP の CKAN からカタログを取り、`nwd_*`（歩行空間ネットワークデータ）の `link.csv` / `node.csv` を `input/hokonavi/` に落とす |
| `build_network.py` | CSV → `network/<case>/links.parquet`・`nodes.parquet`。欠損・重複・自己ループを落とし、距離の食い違い・連結成分・接続リンク ID の食い違いを `report.json` に記録。出典（データセット名・ライセンス・更新日）も書く |
| `export_static.py` | ネットワーク → 公開版が読む `web/static/data/`（`areas.json`・`<case>/graph.json`・`<case>/network.geojson`） |

### api

| ファイル | 役割 |
|---|---|
| `codes.py` | 整備仕様のコード表 |
| `profiles.py` | 利用者別のコスト（向き付き）。係数・速度・待ち時間はここに集める |
| `graph.py` | 探索グラフ（scipy CSR・有向）。リンク上の点へのスナップ（shapely STRtree）、仮ノードを足して Dijkstra |
| `guidance.py` | 経路 → ステップ（歩道・横断歩道・階段・エレベーター…と曲がり方）と LineString |
| `export.py` | 表示用 GeoJSON（`/network`）と、ブラウザ用の静的 JSON（利用者別のコスト・連結成分・案内用の属性） |
| `main.py` | `/health` `/profiles` `/network` `/route` |

### web/src/lib/routing（ブラウザ内の探索）

| ファイル | 役割 |
|---|---|
| `router.ts` | `api/graph.py` と同じスナップ（成分ごとに最寄り・半径を広げる）と Dijkstra（二分ヒープ） |
| `guidance.ts` | `api/guidance.py` と同じ区切り方・文言・丸め方（Python の偶数丸めも再現） |
| `parity.test.ts` | Python と同じ結果か（vitest） |

---

## 使い方

```bash
uv sync

# データを取ってネットワークを作る（新宿駅周辺）
uv run python preprocess/fetch_hokonavi.py --dataset nwd_shinjuku
uv run python preprocess/build_network.py --dataset nwd_shinjuku       # → network/shinjuku/

# 画面（公開版と同じくブラウザ内で探索）→ http://localhost:5173
uv run python preprocess/export_static.py --case shinjuku             # → web/static/data/
cd web && pnpm install && pnpm dev

# API 版（http://127.0.0.1:8000/docs に Swagger）
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000              # 別の地区は HOKONAVI_CASE=tokyo など
```

### GitHub Pages へのデプロイ

`main` に push すると `.github/workflows/pages.yml` が、ほこナビDP からデータを取り → 整備 → 静的ファイルを書き出し →
`BASE_PATH=/<リポジトリ名>` で画面をビルドして公開する。公開する地区はワークフローの `DATASETS`（先頭が既定）。
リポジトリの Settings → Pages の Source は「GitHub Actions」にしておく。

取得できるデータセットの一覧は `uv run python preprocess/fetch_hokonavi.py --list`。

### テスト・lint

```bash
uv run ruff format --check . && uv run ruff check . && uv run pytest -q
cd web && pnpm check && pnpm test && pnpm build

# 探索・案内を変えたら、ブラウザ版との一致用フィクスチャを作り直す（pytest が作り直し忘れを検出する）
uv run python -m tests.test_parity --write
# 実データでも比べる（network/shinjuku/parity.json を書き、pnpm test が読む）
uv run python -m tests.test_parity --real shinjuku
```

CI（`.github/workflows/ci.yml`）でも同じものを回す。テストは合成ネットワークだけを使うので、実データは要らない。

---

## 今後（全地区への展開）

- **全 28 地区をまとめる**。`fetch_hokonavi.py` は全地区を取れる。重なる地区（新宿駅周辺・国立競技場周辺・渋谷〜千駄ヶ谷〜新宿 など）の**同じ ID・同じ場所のノードとリンクの重複**を解く必要がある
- **表示を PMTiles に**する。今は GeoJSON を丸ごと読む（新宿で 1,866 本なら十分。全地区 5.6 万本では重い）。探索用の graph.json も地区ごとに分けて、表示範囲に応じて読む
- 速度・待ち時間・係数を根拠のある値にする。供用時間・曜日に対応する
- バリアフリー施設データ（`facility_*`）と、都営大江戸線の駅構内データ（`station_oedo_*`・限定公開）の取り込み

---

## データの出典

- 歩行空間ネットワークデータ：国土交通省（[ほこナビDP](https://ckan.hokonavi.go.jp/)）、[公共データ利用規約（第1.0版）](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)。本リポジトリはこれを加工して利用する。データ自体はリポジトリに含めない
- 整備仕様：国土交通省「[歩行空間ネットワークデータ整備仕様（2024年7月）](https://www.mlit.go.jp/sogoseisaku/soukou/sogoseisaku_soukou_tk_000056.html)」
- 背景地図：[国土地理院 最適化ベクトルタイル](https://github.com/gsi-cyberjapan/optimal_bvmap)（淡色地図風スタイル）
