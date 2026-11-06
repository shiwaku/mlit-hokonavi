# Hokonavi Route Search — 歩行空間ネットワークデータによる歩行者経路探索

国土交通省の[歩行空間ナビゲーションデータプラットフォーム（ほこナビDP）](https://www.hokonavi.go.jp/)で公開されている
**歩行空間ネットワークデータ**を使い、利用者（一般・車いす・ベビーカー・視覚障害）ごとに経路を探して案内する。

**公開版: https://shiwaku.github.io/mlit-hokonavi/**（GitHub Pages・サーバーなし。探索はブラウザ内）

構成は [shiwaku/ksj-route-search-api](https://github.com/shiwaku/ksj-route-search-api) にそろえている（preprocess / api / web）。
ほこナビDP の歩行空間ネットワークデータ **全 28 地区**を、重なる地区をまとめて **21 地区**として扱う（「地区」）。

### 2 つの動かし方

| | 公開版（GitHub Pages） | API 版（ローカル・ほかのアプリから呼ぶ） |
|---|---|---|
| 探索する場所 | **ブラウザ**（`web/src/lib/routing/`・TypeScript） | **FastAPI**（`api/`・Python） |
| データ | 前処理で書き出した静的 JSON（`web/static/data/`）。選んだ地区の分だけ読む | `network/<case>/*.parquet`（1 プロセス 1 地区） |
| 通れるか・コスト | どちらも **`api/profiles.py` の結果**（静的 JSON に配列で書き出す） | 同左 |

判断（通れるか・コスト）は Python の 1 か所だけに置き、TypeScript に移したのはスナップ・Dijkstra・案内文だけ。
両者が**同じ結果を返すことをテストで確かめている**（`tests/test_parity.py`）。合成ネットワークの全ケースに加え、
全 21 地区でランダムな 200 経路ずつ（計 4,200）を比べ、4,198 件が完全一致・2 件（府中）は探索コストが同じ別経路（同点）。

---

## 地区

ほこナビDP の地区には、同じ ID のノード・リンクを共有するものがある。**ノード ID を共有する地区は 1 つのネットワークにまとめる**
（`preprocess/build_network.py --all`）。

- 渋谷・千駄ヶ谷・新宿（2021）は、新宿駅周辺（2018）・渋谷南部（2020）・千駄ヶ谷駅周辺（2019）・国立競技場周辺（2021）を丸ごと含む
- 東京駅周辺は千代田区・中央区周辺を含む（後者にしか無いリンクが 2 本）
- 有明テニスの森 と 国際展示場、お台場海浜公園 と 東京テレポート は境界のノードを共有してつながる

重複は ID で解き、**リンクは行ごと最も新しいデータのものを採る**。同じ link_id でも新しい版では起点・終点のノード ID が
振り直されていることがある（新宿で 95 本）ので、属性ごとに混ぜると接続が壊れる。古い版にしか無いノード（使われない）は落とす。
出典表示には、リンクを 1 本でも採った地区だけを挙げる。

| 地区 | 元の地区数 | リンク | 連結成分 | 最終更新 |
|---|---:|---:|---:|---|
| 東京都渋谷区・渋谷駅、千駄ヶ谷駅周辺／東京都新宿区・新宿駅周辺 | 5 | 7,921 | 1 | 2021/05/26 |
| 東京駅周辺 | 2 | 4,949 | 3 | 2019/01/23 |
| 赤羽駅周辺 | 1 | 4,302 | 4 | 2026/03/19 |
| 香川県高松市 | 1 | 2,720 | 1 | 2021/05/26 |
| 神奈川県川崎市川崎駅周辺地区 | 1 | 2,672 | 1 | 2019/03/07 |
| 福井県鯖江市 | 1 | 2,492 | 1 | 2021/05/26 |
| 池袋駅周辺 | 1 | 2,196 | 1 | 2019/01/28 |
| 東京都港区・お台場海浜公園周辺／東京都江東区・東京テレポート駅周辺 | 2 | 1,891 | 1 | 2021/05/26 |
| 新横浜地区 | 1 | 1,845 | 1 | 2018/10/29 |
| 東京都府中市 | 1 | 1,555 | 1 | 2021/05/26 |
| 姫路市 | 1 | 1,494 | 1 | 2018/06/20 |
| 東京都江東区・有明テニスの森公園周辺／東京都江東区・国際展示場周辺 | 2 | 1,425 | 1 | 2021/05/26 |
| 会津若松市 | 1 | 1,418 | 1 | 2018/06/20 |
| 港区大門駅周辺 | 1 | 1,322 | 2 | 2019/03/29 |
| 大阪府大東市・住道駅周辺 | 1 | 1,253 | 1 | 2021/05/26 |
| 神奈川県川崎市武蔵小杉駅周辺地区 | 1 | 1,241 | 1 | 2020/03/02 |
| 台東区上野駅周辺 | 1 | 1,233 | 1 | 2019/03/29 |
| 神奈川県川崎市溝口駅周辺地区 | 1 | 675 | 1 | 2022/05/10 |
| 神奈川県川崎市新百合ヶ丘駅周辺地区 | 1 | 566 | 1 | 2020/03/02 |
| 神奈川県川崎市新川崎・鹿島田駅周辺地区 | 1 | 565 | 1 | 2021/02/24 |
| 東京都江東区・新木場駅周辺 | 1 | 562 | 1 | 2021/05/26 |
| **計** | **28** | **44,297** | | |

（2026-11-05 時点のカタログ。地区ごとの検査結果は `network/<case>/report.json`）

画面では、地区を選ぶ（リスト・地図の青い枠や点をクリック）と、その地区のデータだけを読む。地区をまたぐ経路は探さない。

画面はライト / ダークを切り替えられる（右上の 🌙 / ☀️。初回は OS の設定に合わせ、切り替えたらブラウザに覚える）。
ダークの背景地図は [shiwaku/japan-food-store-master の viewer](https://github.com/shiwaku/japan-food-store-master/tree/main/viewer) と同じく、
淡色地図スタイルの色の**明度だけを反転**して作る（色相は保ち、彩度を 85 % に）。ただしスタイルは差し替えず、
背景レイヤーの色を `setPaintProperty` で塗り替える（`web/src/lib/basemap.ts`）。経路などのレイヤーを貼り直さずに済み、表示中の経路も残る。

---

## 分析上の前提

| # | 前提 | 理由 |
|---|---|---|
| ① | ネットワークは**ほこナビDP の公開データをそのまま使う**（2024 年 7 月版の整備仕様） | 整備済みの属性（段差・勾配・幅員・階段・エレベーター・信号）を経路のコストに直接使える |
| ② | リンクの形状は**起点ノードと終点ノードを結ぶ直線** | 2024 年 7 月版のリンクは中間点を持たない（GeoJSON 版も全リンク 2 点） |
| ③ | **有向グラフ**で探す。1 リンクにつき 起点→終点 / 終点→起点 の 2 エッジ | 一方通行（動く歩道・エスカレーターなど）と勾配の上り下りに向きがある |
| ④ | 出発地・目的地は**最寄りのリンク上の点**に寄せる（半径 50 m）。同じ連結成分に乗らなければ 100・200 m まで広げ、50 m を超えたら警告を出す | 車いすなどで通れない区間を除くと連結成分が分かれる。デッキ上など階段でしか出られない場所に寄せると到達不能になる |
| ⑤ | 所要時間 = 距離 ÷ 歩行速度 ＋ 待ち時間（エレベーター 30 秒・信号のある横断歩道 20 秒） | 速度と待ち時間は**仮の値**（`api/profiles.py`）。根拠のある値に置き換える前提 |
| ⑥ | 属性が「不明（99）」・空欄の区間は**通れるが避ける**（コスト 2 倍）。経路に含まれたら警告する | 不明を通行止めにすると、ほとんどの経路が切れる |
| ⑦ | 階（`floor`）は**データの値をそのまま**表示する（0 = 地上、負 = 地下） | 仕様では屋外の地上部分が 0。建物の何階かとの対応はデータからは決まらない |
| ⑧ | 供用時間（`start_time` など）と通行制限曜日は**未対応** | — |
| ⑨ | 重なる地区は**ノード ID の共有**でまとめる。ID が違うが同じ場所にあるノード（境界で 1 m 以内に十数個）はつながない | 共有 ID で十分つながる。座標だけでつなぐと階の違う通路を誤ってつなぐおそれがある |

### 利用者ごとの通れない区間・避ける区間

| | 通れない | 避ける（コストの係数） |
|---|---|---|
| 一般 | 一方通行の逆走 | — |
| 車いす | 階段・エスカレーター・段差 5 cm 超・勾配 8 % 超・幅 1 m 未満・車いす非対応のエレベーター | 段差 2〜5 cm ×1.5、幅 1〜2 m ×1.2、勾配 5〜8 % 上り ×2.0・下り ×1.5 |
| ベビーカー | 階段・エスカレーター・段差 10 cm 超・勾配 18 % 超 | 段差 5〜10 cm ×2.0、幅 1 m 未満 ×1.5、勾配 8〜18 % の上り ×1.5 |
| 視覚障害 | 一方通行の逆走 | 誘導用ブロックなし ×1.5、音響設備のない信号 ×2.0、横断歩道のない横断 ×3.0、エスカレーター ×1.5 |

全員に共通: 通り抜けが好ましくない通路・有料の通路 ×1.5、踏切 ×1.5。

---

## 実測値（2026-11-05）

| 項目 | 値 |
|---|---|
| 地区 / リンク | 21 地区（元 28）/ 44,297 リンク（重複を除く前は 52,169） |
| 検査で落としたリンク | 0（欠損・重複・自己ループなし）。距離の食い違いは赤羽 21 本ほか少数（記録のみ） |
| 経路探索 1 回 | ブラウザ版（Node で計測）中央値 0.2 ms・95% 点 0.7 ms、Python 版 中央値 4 ms・95% 点 8 ms（実データ 4,200 経路 / 400 経路） |
| 公開版のデータ | 21 地区で 36.8 MB。最大の渋谷・千駄ヶ谷・新宿で graph.json 2.6 MB・network.geojson 4.2 MB（gzip で 0.40 MB・0.24 MB） |
| 前処理（取得を除く） | 全地区のまとめ（`build_network.py --all`）と書き出し（`export_static.py --all`）で計 7 秒 |

---

## 構成

```
.
├── preprocess/   データ取得・ネットワーク整備（地区のまとめ）・静的ファイルの書き出し
├── api/          FastAPI（graph.py 探索グラフ / profiles.py 利用者別コスト / guidance.py 案内文 / export.py 書き出し / main.py）
├── web/          SvelteKit + Tailwind + MapLibre（地理院 最適化ベクトルタイル 淡色）。src/lib/routing/ がブラウザ内の探索
├── tests/        pytest（合成ネットワークでの単体テスト・API の結合テスト・地区のまとめ方・ブラウザ版との一致用フィクスチャ）
├── .github/      CI（ci.yml）と GitHub Pages へのデプロイ（pages.yml）
├── docs/         API 設計書（api-design.md）
├── input/        ほこナビDP の原データ（gitignored）
└── network/      整備したネットワーク（gitignored）
```

### preprocess

| スクリプト | 役割 |
|---|---|
| `fetch_hokonavi.py` | ほこナビDP の CKAN からカタログを取り、`nwd_*`（歩行空間ネットワークデータ）の `link.csv` / `node.csv` を `input/hokonavi/` に落とす |
| `build_network.py` | CSV → `network/<case>/links.parquet`・`nodes.parquet`。`--all` は ID を共有する地区をまとめ、`network/areas.json` に一覧を書く。欠損・重複・自己ループを落とし、距離の食い違い・連結成分・接続リンク ID の食い違いを `report.json` に記録。出典（データセット名・ライセンス・更新日）も書く |
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

`web/src/lib/basemap.ts`（背景地図のダーク化）・`theme.ts`（ライト / ダークの切り替えと保存）

---

## 使い方

```bash
uv sync

# データを取ってネットワークを作る（全地区。重なる地区はまとめる）
uv run python preprocess/fetch_hokonavi.py
uv run python preprocess/build_network.py --all                      # → network/<case>/・network/areas.json

# 画面（公開版と同じくブラウザ内で探索）→ http://localhost:5173
uv run python preprocess/export_static.py --all                      # → web/static/data/
cd web && pnpm install && pnpm dev

# API 版（http://127.0.0.1:8000/docs に Swagger）。地区は HOKONAVI_CASE（既定 shibuya_sendagaya_shinjuku）
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000
```

1 地区だけなら `build_network.py --dataset nwd_shinjuku`、`export_static.py --case shinjuku`。
取得できるデータセットの一覧は `uv run python preprocess/fetch_hokonavi.py --list`。

### GitHub Pages へのデプロイ

`main` に push すると `.github/workflows/pages.yml` が、ほこナビDP から全地区のデータを取り → まとめて整備 → 静的ファイルを書き出し →
`BASE_PATH=/<リポジトリ名>` で画面をビルドして公開する。最初に開く地区は `export_static.py` の `DEFAULT_CASE`。
リポジトリの Settings → Pages の Source は「GitHub Actions」にしておく。

### テスト・lint

```bash
uv run ruff format --check . && uv run ruff check . && uv run pytest -q
cd web && pnpm check && pnpm test && pnpm build

# 探索・案内を変えたら、ブラウザ版との一致用フィクスチャを作り直す（pytest が作り直し忘れを検出する）
uv run python -m tests.test_parity --write
# 実データでも比べる（network/<case>/parity.json を書き、pnpm test が読む）
uv run python -m tests.test_parity --real-all
```

CI（`.github/workflows/ci.yml`）でも同じものを回す。テストは合成データだけを使うので、実データは要らない。

### 紹介動画の録画

`web/scripts/promo.ts` が Playwright で画面を操作して録画し、ffmpeg で MP4（ダークモード・1920×1080・約 66 秒・H.264・音声なし）にする。
地図は開発サーバーだけが出す `window.__map` で操作するので、先に `pnpm dev` を立ち上げておく。

```bash
cd web && pnpm dev                       # 別の端末で
pnpm exec playwright-core install chromium   # 初回だけ（CHROME_PATH で手元の Chromium を指してもよい）
pnpm promo                               # → web/promo/hokonavi-promo-dark.mp4（git には入れない）
```

環境変数：`PROMO_URL`（既定 `http://localhost:5173/`）・`CHROME_PATH`・`FFMPEG`（既定 `ffmpeg`）。
字幕の文言・場面の順番・待ち時間は `promo.ts` の `main()` に並んでいる。

---

## 今後

- **表示を PMTiles に**する。今は選んだ地区の GeoJSON を丸ごと読む（最大の地区で gzip 0.24 MB なので今は足りる）
- 地区をまたぐ経路（今は地区ごとに別のネットワーク。隣り合う地区は今のところ無い）
- 速度・待ち時間・係数を根拠のある値にする。供用時間・曜日に対応する
- API 版を複数地区に（今は 1 プロセス 1 地区）
- バリアフリー施設データ（`facility_*`）と、都営大江戸線の駅構内データ（`station_oedo_*`・限定公開）の取り込み

---

## データの出典

- 歩行空間ネットワークデータ：国土交通省（[ほこナビDP](https://ckan.hokonavi.go.jp/)）、[公共データ利用規約（第1.0版）](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)。本リポジトリはこれを加工して利用する。データ自体はリポジトリに含めない。画面では地区ごとに元のデータセット名を出典として表示する
- 整備仕様：国土交通省「[歩行空間ネットワークデータ整備仕様（2024年7月）](https://www.mlit.go.jp/sogoseisaku/soukou/sogoseisaku_soukou_tk_000056.html)」
- 背景地図：[国土地理院 最適化ベクトルタイル](https://github.com/gsi-cyberjapan/optimal_bvmap)（淡色地図風スタイル）
