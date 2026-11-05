"""
利用者別のコスト（プロファイル）

リンクごとに「起点→終点（fwd）」「終点→起点（bwd）」の 2 方向で
  time_s : 所要時間（秒）= 距離 ÷ 歩行速度 ＋ 待ち時間（エレベーター・信号）
  cost   : 探索コスト = time_s × 係数（避けたい属性ほど大きい）。通れない向きは inf
を作る。勾配と一方通行に向きがあるので、2 方向を別々に持つ。

係数・速度・待ち時間は仮の値（README「前提」）。根拠のある値に置き換える前提で、1 か所にまとめておく。
不明（99）と空欄は「通れるが避ける」とし、経路に含まれたら警告を出す（guidance.py）。
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from api import codes as C

ELEVATOR_WAIT_S = 30.0
SIGNAL_WAIT_S = 20.0


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    speed_mps: float
    description: str


PROFILES: dict[str, Profile] = {
    p.key: p
    for p in [
        Profile('walk', '一般', 1.3, '階段・エスカレーターも使う。距離と信号待ちが少ない経路'),
        Profile(
            'wheelchair',
            '車いす',
            1.0,
            '階段・エスカレーター・5cmを超える段差・8%を超える勾配・幅1m未満・車いす非対応のエレベーターを通らない',
        ),
        Profile('stroller', 'ベビーカー', 1.1, '階段・エスカレーター・10cmを超える段差・18%を超える勾配を通らない'),
        Profile(
            'vision', '視覚障害', 1.0, '誘導用ブロックのある道・音響信号のある横断歩道を優先し、標示のない横断を避ける'
        ),
    ]
}


@dataclass
class LinkCosts:
    cost_fwd: np.ndarray
    cost_bwd: np.ndarray
    time_fwd: np.ndarray
    time_bwd: np.ndarray
    unknown: np.ndarray  # このプロファイルで判断に使う属性が不明なリンク


def _unknown(*cols: np.ndarray) -> np.ndarray:
    m = np.zeros(len(cols[0]), dtype=bool)
    for c in cols:
        m |= (c == C.UNKNOWN) | (c == C.MISSING)
    return m


def _slope(code: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """勾配コード → (区分 0〜3、起点→終点で上りか)。不明は区分 0・上りなし"""
    cls = np.zeros(len(code), dtype=np.int8)
    up = np.zeros(len(code), dtype=bool)
    for k, (c, u) in C.SLOPE_CLASS.items():
        m = code == k
        cls[m] = c
        if u:
            up[m] = True
    return cls, up


def compute(links: pd.DataFrame, profile: str) -> LinkCosts:
    p = PROFILES[profile]
    n = len(links)
    a = {c: links[c].to_numpy() for c in C.LINK_CODE_COLUMNS}
    dist = links['distance'].to_numpy().astype(np.float64)
    rt, st = a['route_type'], a['rt_struct']

    # ---- 所要時間（向きに依らない）
    t = dist / p.speed_mps
    t = t + np.where(rt == C.RT_ELEVATOR, ELEVATOR_WAIT_S, 0.0)
    signalled = (st == C.RT_CROSSWALK) & np.isin(a['tfc_signal'], (2, 3, 4))
    t = t + np.where(signalled, SIGNAL_WAIT_S, 0.0)

    # ---- 係数（向きに依らない部分）と、向きごとの係数
    f = np.ones(n)
    f_fwd = np.ones(n)
    f_bwd = np.ones(n)
    block = np.zeros(n, dtype=bool)  # 両方向とも通れない
    block_fwd = np.zeros(n, dtype=bool)
    block_bwd = np.zeros(n, dtype=bool)

    # 全プロファイル共通: 一方通行（動く歩道・エスカレーターなど）、通行制限
    block_bwd |= a['direction'] == C.DIR_FORWARD
    block_fwd |= a['direction'] == C.DIR_BACKWARD
    f *= np.where(np.isin(a['tfc_restr'], (2, 3)), 1.5, 1.0)
    f *= np.where(rt == C.RT_RAILWAY_CROSSING, 1.5, 1.0)

    slope_cls, uphill_fwd = _slope(a['vtcl_slope'])
    lev, width, elev = a['lev_diff'], a['width'], a['elevator']

    if profile == 'walk':
        unknown = np.zeros(n, dtype=bool)

    elif profile == 'wheelchair':
        block |= np.isin(rt, (C.RT_STAIRS, C.RT_ESCALATOR))
        block |= np.isin(lev, (4, 5))
        block |= width == 1
        block |= slope_cls >= 2
        block |= (rt == C.RT_ELEVATOR) & np.isin(elev, (2, 4))
        f *= np.where(lev == 3, 1.5, 1.0)
        f *= np.where(width == 2, 1.2, 1.0)
        # 5〜8% は上りをより避ける（向きで係数を変える）
        f_fwd *= np.where(slope_cls == 1, np.where(uphill_fwd, 2.0, 1.5), 1.0)
        f_bwd *= np.where(slope_cls == 1, np.where(uphill_fwd, 1.5, 2.0), 1.0)
        unknown = _unknown(lev, width, a['vtcl_slope'], rt) | ((rt == C.RT_ELEVATOR) & _unknown(elev))

    elif profile == 'stroller':
        block |= np.isin(rt, (C.RT_STAIRS, C.RT_ESCALATOR))
        block |= lev == 5
        block |= slope_cls >= 3
        f *= np.where(lev == 4, 2.0, 1.0)
        f *= np.where(width == 1, 1.5, 1.0)
        f_fwd *= np.where((slope_cls == 2) & uphill_fwd, 1.5, 1.0)
        f_bwd *= np.where((slope_cls == 2) & ~uphill_fwd, 1.5, 1.0)
        unknown = _unknown(lev, a['vtcl_slope'], rt)

    elif profile == 'vision':
        brail = a['brail_tile']
        f *= np.where(brail == C.BRAIL_YES, 1.0, 1.5)
        f *= np.where(signalled & (a['tfc_s_type'] == 1), 2.0, 1.0)
        f *= np.where(st == C.RT_CROSSING, 3.0, 1.0)
        f *= np.where(rt == C.RT_ESCALATOR, 1.5, 1.0)
        unknown = _unknown(brail) | (signalled & _unknown(a['tfc_s_type']))

    else:
        raise KeyError(profile)

    # 不明な属性は「通れるが避ける」
    f *= np.where(unknown, 2.0, 1.0)

    cost_fwd = np.where(block | block_fwd, np.inf, t * f * f_fwd)
    cost_bwd = np.where(block | block_bwd, np.inf, t * f * f_bwd)
    return LinkCosts(cost_fwd=cost_fwd, cost_bwd=cost_bwd, time_fwd=t, time_bwd=t.copy(), unknown=unknown)
