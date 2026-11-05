"""
歩行空間ネットワークデータ整備仕様（国土交通省 2024年7月）のコード表

出典: https://www.mlit.go.jp/sogoseisaku/soukou/content/001757259.pdf
表3.6（リンク）・表3.8（ノード）。99 はどの項目も「不明」。
前処理（preprocess/build_network.py）は空欄を MISSING（-1）として整数列に入れる。
"""

UNKNOWN = 99
MISSING = -1

# 経路の構造 rt_struct
RT_STRUCT = {
    1: '車道と歩道の物理的な分離あり',
    2: '車道と歩道の物理的な分離なし',
    3: '横断歩道',
    4: '横断歩道の路面標示の無い道路の横断部',
    5: '地下通路',
    6: '歩道橋',
    7: '施設内通路',
    8: 'その他の経路の構造',
}
RT_SIDEWALK, RT_SHARED, RT_CROSSWALK, RT_CROSSING, RT_UNDERPASS, RT_FOOTBRIDGE, RT_INDOOR, RT_OTHER = range(1, 9)

# 経路の種別 route_type
ROUTE_TYPE = {
    1: '対応する属性情報なし',
    2: '動く歩道',
    3: '踏切',
    4: 'エレベーター',
    5: 'エスカレーター',
    6: '階段',
    7: 'スロープ',
}
RT_NONE, RT_MOVING_WALK, RT_RAILWAY_CROSSING, RT_ELEVATOR, RT_ESCALATOR, RT_STAIRS, RT_SLOPE = range(1, 8)

# 方向性 direction
DIR_BOTH, DIR_FORWARD, DIR_BACKWARD = 1, 2, 3  # 2: 起点→終点のみ、3: 終点→起点のみ

# 幅員 width
WIDTH = {1: '1.0m未満', 2: '1.0m以上2.0m未満', 3: '2.0m以上3.0m未満', 4: '3.0m以上'}

# 縦断勾配 vtcl_slope。3/5/7 は起点より終点が高い（起点→終点が上り）、4/6/8 は起点より終点が低い
VTCL_SLOPE = {
    1: '0%',
    2: '0%より大きく5%以下',
    3: '5%より大きく8%以下（終点が高い）',
    4: '5%より大きく8%以下（終点が低い）',
    5: '8%より大きく18%以下（終点が高い）',
    6: '8%より大きく18%以下（終点が低い）',
    7: '18%より大きい（終点が高い）',
    8: '18%より大きい（終点が低い）',
}
# 勾配コード → (区分, 起点→終点で上りか)。区分 0: 5%以下、1: 5〜8%、2: 8〜18%、3: 18%超
SLOPE_CLASS = {
    1: (0, None),
    2: (0, None),
    3: (1, True),
    4: (1, False),
    5: (2, True),
    6: (2, False),
    7: (3, True),
    8: (3, False),
}

# 段差 lev_diff
LEV_DIFF = {1: '0cm', 2: '2cm以下', 3: '2cmより大きく5cm以下', 4: '5cmより大きく10cm以下', 5: '10cmより大きい'}

# 歩行者用信号機の有無 tfc_signal
TFC_SIGNAL = {1: 'なし', 2: '歩車分離式', 3: '押しボタン式', 4: 'その他の信号機'}
# 歩行者用信号機の種別 tfc_s_type
TFC_S_TYPE = {1: '音響設備なし', 2: '音響設備あり（押しボタンなし）', 3: '音響設備あり（押しボタンあり）'}

# 視覚障害者誘導用ブロック等 brail_tile
BRAIL_NONE, BRAIL_YES = 1, 2

# エレベーターの種別 elevator
ELEVATOR = {
    1: 'なし',
    2: 'あり（バリアフリー対応なし）',
    3: 'あり（車椅子使用者対応）',
    4: 'あり（視覚障害者対応）',
    5: 'あり（車椅子使用者・視覚障害者対応）',
}

# 通行制限 tfc_restr
TFC_RESTR = {1: '自由に通行できる', 2: '通り抜けが好ましくない通路', 3: '料金の支払いが必要'}

# ノードの施設内外区分 in_out
IN_OUT = {1: '施設外', 2: '施設内外の境界', 3: '施設内'}

# リンクの整数コード列（前処理で int16 にそろえる）
LINK_CODE_COLUMNS = (
    'rt_struct',
    'route_type',
    'direction',
    'width',
    'vtcl_slope',
    'lev_diff',
    'tfc_signal',
    'tfc_s_type',
    'brail_tile',
    'elevator',
    'roof',
    'tfc_restr',
)
