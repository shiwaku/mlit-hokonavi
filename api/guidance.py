"""
経路（Piece の列）→ 案内（ステップ）と LineString

ステップの区切り:
  - 通り方の種類（kind）が変わるところ（歩道 → 横断歩道 → エレベーター …）
  - 同じ種類が続いても、右・左に曲がる（60° 以上）ところ
  - ただし MIN_STEP_M 未満の歩く区間は、直前の歩く区間にまとめる
各ステップの先頭で、直前の進行方向からの曲がり方（直進・右・左…）を案内する。
"""

import math

import numpy as np

from api import codes as C
from api.graph import SNAP_RADIUS_M, PedestrianGraph, Piece
from api.profiles import PROFILES

MIN_STEP_M = 5.0

KIND_LABEL = {
    'walk': '歩道',
    'shared': '道路',
    'crosswalk': '横断歩道',
    'crossing': '道路の横断',
    'underpass': '地下通路',
    'footbridge': '歩道橋',
    'indoor': '施設内通路',
    'elevator': 'エレベーター',
    'escalator': 'エスカレーター',
    'stairs': '階段',
    'moving_walk': '動く歩道',
    'railway_crossing': '踏切',
    'slope': 'スロープ',
}
ROUTE_TYPE_KIND = {
    C.RT_ELEVATOR: 'elevator',
    C.RT_ESCALATOR: 'escalator',
    C.RT_STAIRS: 'stairs',
    C.RT_MOVING_WALK: 'moving_walk',
    C.RT_RAILWAY_CROSSING: 'railway_crossing',
    C.RT_SLOPE: 'slope',
}
RT_STRUCT_KIND = {
    C.RT_SHARED: 'shared',
    C.RT_CROSSWALK: 'crosswalk',
    C.RT_CROSSING: 'crossing',
    C.RT_UNDERPASS: 'underpass',
    C.RT_FOOTBRIDGE: 'footbridge',
    C.RT_INDOOR: 'indoor',
}
SIGNAL_TEXT = {1: '信号なし', 2: '歩車分離式信号', 3: '押しボタン式信号', 4: '信号あり'}
# 曲がって区切る種類（横断歩道などは 1 本で 1 ステップなので区切らない）
SPLIT_ON_TURN = {'walk', 'shared', 'underpass', 'indoor', 'footbridge', 'slope'}


def link_kind(route_type: int, rt_struct: int) -> str:
    return ROUTE_TYPE_KIND.get(route_type) or RT_STRUCT_KIND.get(rt_struct, 'walk')


def floor_label(f: float | None) -> str:
    if f is None or (isinstance(f, float) and math.isnan(f)):
        return '不明な階'
    if f == 0:
        return '地上'
    return f'{f:g}階' if f > 0 else f'地下{-f:g}階'


def bearing(a: tuple[float, float], b: tuple[float, float], lat0: float) -> float:
    dx = (b[0] - a[0]) * math.cos(math.radians(lat0))
    dy = b[1] - a[1]
    return math.degrees(math.atan2(dx, dy)) % 360


def turn_of(prev: float | None, cur: float) -> tuple[str, float]:
    if prev is None:
        return 'start', 0.0
    d = (cur - prev + 540) % 360 - 180  # -180〜180、正が右
    a = abs(d)
    if a < 20:
        return 'straight', d
    if a > 150:
        return 'uturn', d
    side = 'right' if d > 0 else 'left'
    return (f'slight_{side}' if a < 60 else side), d


TURN_TEXT = {
    'start': '',
    'straight': 'まっすぐ',
    'slight_right': '右斜め前へ',
    'slight_left': '左斜め前へ',
    'right': '右に曲がり',
    'left': '左に曲がり',
    'uturn': '引き返し',
}


def _instruction(step: dict) -> str:
    kind, turn, dist = step['kind'], step['turn'], round(step['dist_m'])
    head = TURN_TEXT[turn]
    head = f'{head}、' if head else ''
    f0, f1 = step['floor_from'], step['floor_to']
    updown = '上る' if (f1 or 0) > (f0 or 0) else '下る' if (f1 or 0) < (f0 or 0) else step.get('updown') or '進む'
    match kind:
        case 'elevator':
            return f'エレベーターで{floor_label(f1)}へ'
        case 'escalator':
            return f'{head}エスカレーターを{updown}'
        case 'stairs':
            return f'{head}階段を{updown}（{dist}m）'
        case 'crosswalk':
            sig = step.get('signal')
            return f'{head}横断歩道を渡る（{dist}m{"・" + sig if sig else ""}）'
        case 'crossing':
            return f'{head}道路を横断する（横断歩道なし・{dist}m）'
        case 'railway_crossing':
            return f'{head}踏切を渡る（{dist}m）'
        case _:
            return f'{head}{KIND_LABEL[kind]}を{dist}m進む'


def build(g: PedestrianGraph, result: dict, profile: str) -> dict:
    pieces: list[Piece] = result['pieces']
    L = g.links
    lc = g.costs[profile]
    dist_col = L['distance'].to_numpy()
    rt, st = L['route_type'].to_numpy(), L['rt_struct'].to_numpy()
    fs, fe = L['floor_start'].to_numpy(), L['floor_end'].to_numpy()
    slope = L['vtcl_slope'].to_numpy()
    sig, sig_t = L['tfc_signal'].to_numpy(), L['tfc_s_type'].to_numpy()

    coords: list[tuple[float, float]] = []
    steps: list[dict] = []
    prev_bearing = None
    total_d = total_t = 0.0
    unknown_links = 0
    counts = {'stairs': 0, 'elevator': 0, 'escalator': 0, 'crosswalk': 0, 'crossing': 0}

    for p in pieces:
        li = p.link
        frac = abs(p.b - p.a)
        d = float(dist_col[li]) * frac
        t = float((lc.time_fwd if p.forward else lc.time_bwd)[li]) * frac
        a, b = g.piece_coords(p)
        brg = bearing(a, b, g.lat0) if a != b else prev_bearing
        kind = link_kind(int(rt[li]), int(st[li]))
        f_from, f_to = (fs[li], fe[li]) if p.forward else (fe[li], fs[li])
        f_from = None if np.isnan(f_from) else float(f_from)
        f_to = None if np.isnan(f_to) else float(f_to)
        unknown_links += int(lc.unknown[li])

        turn, _ = turn_of(prev_bearing, brg) if brg is not None else ('straight', 0.0)
        last = steps[-1] if steps else None
        same = (
            last is not None
            and last['kind'] == kind
            and (kind not in SPLIT_ON_TURN or turn in ('straight', 'slight_right', 'slight_left'))
            and kind not in ('crosswalk', 'crossing')
        )  # 横断は 1 本ずつ案内する。エレベーターは乗り通しで 1 つ
        # 「歩道を 3m」「施設内通路を 1m」のような細切れは、直前の歩く区間にまとめる（種類は直前のものを残す）
        if last is not None and kind in SPLIT_ON_TURN and last['kind'] in SPLIT_ON_TURN and d < MIN_STEP_M:
            same = True
        if not coords:
            coords.append(a)
        if coords[-1] != b:
            coords.append(b)
        if same:
            last['dist_m'] += d
            last['time_s'] += t
            last['floor_to'] = f_to
            last['coord_end'] = len(coords) - 1
            last['link_ids'].append(str(L['link_id'].iat[li]))
        else:
            if kind in counts:
                counts[kind] += 1
            step = {
                'kind': kind,
                'label': KIND_LABEL[kind],
                'turn': turn,
                'dist_m': d,
                'time_s': t,
                'floor_from': f_from,
                'floor_to': f_to,
                'coord_start': max(len(coords) - 2, 0),
                'coord_end': len(coords) - 1,
                'link_ids': [str(L['link_id'].iat[li])],
            }
            if kind == 'crosswalk' and sig[li] in SIGNAL_TEXT:
                s = SIGNAL_TEXT[int(sig[li])]
                if sig[li] != 1 and sig_t[li] in C.TFC_S_TYPE:
                    s += '・' + C.TFC_S_TYPE[int(sig_t[li])]
                step['signal'] = s
            if kind in ('stairs', 'escalator', 'slope') and f_from == f_to and slope[li] in C.SLOPE_CLASS:
                up = C.SLOPE_CLASS[int(slope[li])][1]
                if up is not None:
                    step['updown'] = '上る' if up == p.forward else '下る'
            steps.append(step)
        total_d += d
        total_t += t
        if brg is not None:
            prev_bearing = brg

    for s in steps:
        s['dist_m'] = round(s['dist_m'], 1)
        s['time_s'] = round(s['time_s'])
        s['instruction'] = _instruction(s)
    if steps:
        steps.append(
            {
                'kind': 'arrive',
                'label': '到着',
                'turn': 'straight',
                'dist_m': 0.0,
                'time_s': 0,
                'floor_from': steps[-1]['floor_to'],
                'floor_to': steps[-1]['floor_to'],
                'coord_start': len(coords) - 1,
                'coord_end': len(coords) - 1,
                'link_ids': [],
                'instruction': '目的地に到着',
            }
        )

    warnings = []
    for key, name in (('origin', '出発地'), ('dest', '目的地')):
        s = (result.get('snap') or {}).get(key)
        if s is not None and s.dist_m > SNAP_RADIUS_M:
            warnings.append(
                f'{name}の近くに{PROFILES[profile].label}で通れる道（ほかの道とつながるもの）がないため、'
                f'{s.dist_m:.0f}m離れた道から案内します'
            )
    if unknown_links:
        warnings.append(
            f'{PROFILES[profile].label}の通行判断に使う属性が「不明」の区間を {unknown_links} 本含みます。'
            '現地の状況を確かめてください'
        )
    return {
        'geometry': {'type': 'LineString', 'coordinates': [[round(x, 7), round(y, 7)] for x, y in coords]},
        'summary': {'dist_m': round(total_d, 1), 'time_s': round(total_t), 'link_count': len(pieces), **counts},
        'steps': steps,
        'warnings': warnings,
    }
