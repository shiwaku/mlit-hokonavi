"""
歩行者ネットワークの探索グラフ（in-memory）

ksj-route-search-api と同じく、グラフはプロセスに常駐させ scipy の Dijkstra で探す。
歩行空間ネットワークは一方通行（direction）と勾配の向きがあるので**有向**で持ち、
リンク 1 本につき fwd（起点→終点）と bwd（終点→起点）の 2 エッジを、プロファイルごとに作る。

【スナップ】出発地・目的地は最寄りの**リンク上の点**に寄せ、その点を仮ノードとしてグラフに足す。
ノードに寄せると、長いリンクの途中をクリックしたときに行き過ぎて戻る経路になるため。
  - 候補は半径 SNAP_RADIUS_M 以内のリンク。エレベーター・エスカレーター・階をまたぐリンク、
    そのプロファイルで両方向とも通れないリンクには寄せない
  - 階（floor）を指定したら、その階のリンクだけに寄せる
  - データは連結成分が複数に分かれている（新宿で 14 成分・車いすでは階段を除くので 150）。
    候補は成分ごとに最寄り 1 本。出発地と目的地が同じ成分に乗る候補の組のうち、
    スナップ距離の和が最小のものを選ぶ。無ければ半径を SNAP_RADII_M（100・200 m）に広げて探し直し、
    それでも同じ成分にならなければ到達不能として理由を返す
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from api import codes as C
from api.profiles import PROFILES, LinkCosts, compute

BASE = Path(__file__).resolve().parent.parent
SNAP_RADIUS_M = 50.0
# 出発地・目的地が同じ連結成分に乗らないときに広げていく半径
SNAP_RADII_M = (SNAP_RADIUS_M, 100.0, 200.0)
SNAP_CANDIDATES = 8
M_PER_DEG_LAT = 110_950.0
# リンクの端ちょうどに寄せたときの割合は、計算の誤差で 0 / 1 にならないことがある（1e-16 など）。
# ブラウザ版（web/src/lib/routing/router.ts）と結果を揃えるため、この幅は端とみなす
FRAC_EPS = 1e-9


def snap_frac(t: float) -> float:
    return 0.0 if t < FRAC_EPS else 1.0 if t > 1 - FRAC_EPS else t


@dataclass
class Snap:
    link: int  # リンクの行番号
    frac: float  # 起点からの割合 0〜1
    lat: float
    lon: float
    dist_m: float  # クリック位置からの距離
    component: int


@dataclass
class Piece:
    """経路を構成するリンクの一部（向き付き）。frac は起点からの割合で、a → b の向きに進む"""

    link: int
    a: float
    b: float

    @property
    def forward(self) -> bool:
        return self.b >= self.a


class PedestrianGraph:
    def __init__(self, case: str, net_dir: str | Path = 'network'):
        t0 = time.perf_counter()
        d = Path(net_dir) if Path(net_dir).is_absolute() else BASE / net_dir
        d = d / case
        self.case = case
        links = pd.read_parquet(d / 'links.parquet')
        nodes = pd.read_parquet(d / 'nodes.parquet')
        self._init(links, nodes)
        report = d / 'report.json'
        self.source = json.loads(report.read_text(encoding='utf-8')).get('source') if report.exists() else None
        self.load_seconds = time.perf_counter() - t0

    @classmethod
    def from_frames(cls, links: pd.DataFrame, nodes: pd.DataFrame, case: str = 'test') -> PedestrianGraph:
        """テスト用。parquet を介さずに作る（links.geometry は WKB でも shapely でもよい）"""
        g = cls.__new__(cls)
        g.case = case
        g.source = None
        g._init(links, nodes)
        g.load_seconds = 0.0
        return g

    def _init(self, links: pd.DataFrame, nodes: pd.DataFrame) -> None:
        self.links = links.reset_index(drop=True)
        self.nodes = nodes.reset_index(drop=True)
        idx = pd.Index(self.nodes['node_id'])
        self.u = idx.get_indexer(self.links['start_id']).astype(np.int32)
        self.v = idx.get_indexer(self.links['end_id']).astype(np.int32)
        if (self.u < 0).any() or (self.v < 0).any():
            raise ValueError('ノードに無い start_id / end_id があります（preprocess/build_network.py を通したか）')
        self.n_nodes = len(self.nodes)
        self.n_links = len(self.links)

        geom = self.links['geometry'].to_numpy()
        self.geoms = shapely.from_wkb(geom) if isinstance(geom[0], bytes) else geom
        self.lat0 = float(self.nodes['lat'].mean())
        self._kx = M_PER_DEG_LAT * np.cos(np.radians(self.lat0))
        self._geoms_m = shapely.transform(self.geoms, self._to_m)
        self._tree = shapely.STRtree(self._geoms_m)

        rt = self.links['route_type'].to_numpy()
        fs, fe = self.links['floor_start'].to_numpy(), self.links['floor_end'].to_numpy()
        self._snappable = ~np.isin(rt, (C.RT_ELEVATOR, C.RT_ESCALATOR)) & ((fs == fe) | (np.isnan(fs) & np.isnan(fe)))
        self.floors = sorted({float(x) for x in np.concatenate([fs, fe]) if not np.isnan(x)})

        self.costs: dict[str, LinkCosts] = {k: compute(self.links, k) for k in PROFILES}
        # プロファイルごとの連結成分（通れるリンクだけの無向グラフ）。有向の到達可能性は Dijkstra で判定する
        self.components: dict[str, np.ndarray] = {}
        for k, lc in self.costs.items():
            ok = np.isfinite(lc.cost_fwd) | np.isfinite(lc.cost_bwd)
            g = csr_matrix((np.ones(ok.sum()), (self.u[ok], self.v[ok])), shape=(self.n_nodes, self.n_nodes))
            _, self.components[k] = connected_components(g, directed=False)

    # ------------------------------------------------------------------ 座標
    def _to_m(self, xy: np.ndarray) -> np.ndarray:
        return np.column_stack([xy[:, 0] * self._kx, xy[:, 1] * M_PER_DEG_LAT])

    def _point_on(self, link: int, frac: float) -> tuple[float, float]:
        p = shapely.line_interpolate_point(self.geoms[link], frac, normalized=True)
        return float(p.x), float(p.y)

    # ------------------------------------------------------------------ スナップ
    def snap_candidates(
        self, lat: float, lon: float, profile: str, floor: float | None = None, radius: float = SNAP_RADIUS_M
    ) -> list[Snap]:
        """半径内の候補を、連結成分ごとに最寄り 1 本ずつ・近い順に返す。
        近い 8 本だけを取ると、デッキの上（階段でしか降りられない小さな成分）のリンクで埋まり、
        少し離れた地上の歩道が候補に入らないことがあった（新宿・都庁前）"""
        pt = shapely.Point(lon * self._kx, lat * M_PER_DEG_LAT)
        cand = self._tree.query(pt.buffer(radius))
        if len(cand) == 0:
            return []
        lc = self.costs[profile]
        ok = self._snappable[cand] & (np.isfinite(lc.cost_fwd[cand]) | np.isfinite(lc.cost_bwd[cand]))
        if floor is not None:
            ok &= self.links['floor_start'].to_numpy()[cand] == floor
        cand = cand[ok]
        if len(cand) == 0:
            return []
        dist = shapely.distance(self._geoms_m[cand], pt)
        # buffer との交差は外接矩形で拾うので、半径の外のものも混じる
        comp = self.components[profile]
        out: list[Snap] = []
        seen: set[int] = set()
        # 同じ距離（交差点の真横など）はリンクの行番号順。ブラウザ版と並びを揃えるため、距離は 1e-6 m で丸めて比べる
        for k in np.lexsort((cand, np.round(dist, 6))):
            if dist[k] > radius or len(out) >= SNAP_CANDIDATES:
                break
            li = int(cand[k])
            c = int(comp[self.u[li]])
            if c in seen:
                continue
            seen.add(c)
            frac = snap_frac(float(shapely.line_locate_point(self._geoms_m[li], pt, normalized=True)))
            x, y = self._point_on(li, frac)
            out.append(Snap(li, frac, y, x, float(dist[k]), c))
        return out

    # ------------------------------------------------------------------ 探索
    def _edges(self, profile: str):
        lc = self.costs[profile]
        rows = np.concatenate([self.u, self.v])
        cols = np.concatenate([self.v, self.u])
        cost = np.concatenate([lc.cost_fwd, lc.cost_bwd])
        link = np.concatenate([np.arange(self.n_links), np.arange(self.n_links)])
        ok = np.isfinite(cost)
        return rows[ok], cols[ok], cost[ok], link[ok]

    def route(
        self,
        o_lat: float,
        o_lon: float,
        d_lat: float,
        d_lon: float,
        profile: str = 'walk',
        o_floor: float | None = None,
        d_floor: float | None = None,
    ) -> dict:
        """2 地点間の経路。返り値は pieces（Piece の列）・snap・unreachable_reason など。表示用の整形は guidance.py"""
        t0 = time.perf_counter()
        if profile not in PROFILES:
            raise KeyError(profile)
        res: dict = {'pieces': [], 'snap': None, 'unreachable_reason': None, 'cost': None}

        def done(reason: str | None = None) -> dict:
            res['unreachable_reason'] = reason
            res['elapsed_ms'] = (time.perf_counter() - t0) * 1000
            return res

        label = PROFILES[profile].label
        # 最初の半径に何も無ければ「ネットワークの外」。候補はあるが同じ成分に乗らなければ、半径を広げて探し直す
        pairs: list[tuple[Snap, Snap]] = []
        for i, radius in enumerate(SNAP_RADII_M):
            oc = self.snap_candidates(o_lat, o_lon, profile, o_floor, radius)
            dc = self.snap_candidates(d_lat, d_lon, profile, d_floor, radius)
            if i == 0 and (not oc or not dc):
                which = '出発地' if not oc else '目的地'
                return done(f'{which}の{radius:.0f}m以内に、{label}で通れる歩行空間ネットワークがありません')
            pairs = [(o, d) for o in oc for d in dc if o.component == d.component]
            if pairs:
                break
        if not pairs:
            res['snap'] = {'origin': oc[0], 'dest': dc[0]}
            return done(f'出発地と目的地が、{label}で通れる道ではつながっていません（データ上の別のネットワーク）')
        o, d = min(pairs, key=lambda p: p[0].dist_m + p[1].dist_m)
        res['snap'] = {'origin': o, 'dest': d}

        lc = self.costs[profile]
        rows, cols, cost, link = self._edges(profile)
        S, T = self.n_nodes, self.n_nodes + 1  # 仮ノード
        extra = []  # (from, to, cost, Piece)
        # 出発地 → リンク両端
        if np.isfinite(lc.cost_bwd[o.link]):
            extra.append((S, int(self.u[o.link]), lc.cost_bwd[o.link] * o.frac, Piece(o.link, o.frac, 0.0)))
        if np.isfinite(lc.cost_fwd[o.link]):
            extra.append((S, int(self.v[o.link]), lc.cost_fwd[o.link] * (1 - o.frac), Piece(o.link, o.frac, 1.0)))
        # リンク両端 → 目的地
        if np.isfinite(lc.cost_fwd[d.link]):
            extra.append((int(self.u[d.link]), T, lc.cost_fwd[d.link] * d.frac, Piece(d.link, 0.0, d.frac)))
        if np.isfinite(lc.cost_bwd[d.link]):
            extra.append((int(self.v[d.link]), T, lc.cost_bwd[d.link] * (1 - d.frac), Piece(d.link, 1.0, d.frac)))
        # 同じリンク上なら直行も候補
        if o.link == d.link:
            c = lc.cost_fwd[o.link] if d.frac >= o.frac else lc.cost_bwd[o.link]
            if np.isfinite(c):
                extra.append((S, T, c * abs(d.frac - o.frac), Piece(o.link, o.frac, d.frac)))

        n = self.n_nodes + 2
        e_rows = np.concatenate([rows, [e[0] for e in extra]]).astype(np.int32)
        e_cols = np.concatenate([cols, [e[1] for e in extra]]).astype(np.int32)
        # scipy は 0 の重みをエッジ無しとみなすので、極小値を足す
        e_cost = np.concatenate([cost, [e[2] for e in extra]]) + 1e-9
        e_id = np.concatenate([link, -1 - np.arange(len(extra))])  # 負の値は extra の添字
        # 並行エッジ（同じ from→to）は csr_matrix が合計してしまうので、最小のものだけ残す
        order = np.lexsort((e_cost, e_cols, e_rows))
        keep = np.ones(len(order), dtype=bool)
        r_s, c_s = e_rows[order], e_cols[order]
        keep[1:] = (r_s[1:] != r_s[:-1]) | (c_s[1:] != c_s[:-1])
        order = order[keep]
        G = csr_matrix((e_cost[order], (e_rows[order], e_cols[order])), shape=(n, n))
        edge_of = {(int(e_rows[i]), int(e_cols[i])): int(e_id[i]) for i in order}

        dist, pred = dijkstra(G, directed=True, indices=S, return_predecessors=True)
        if not np.isfinite(dist[T]):
            return done(f'{label}で通れる経路が見つかりません（一方通行・通れない区間で行き止まり）')

        path = [T]
        while path[-1] != S:
            path.append(int(pred[path[-1]]))
        path.reverse()
        pieces = []
        for a, b in zip(path[:-1], path[1:], strict=True):
            eid = edge_of[(a, b)]
            if eid < 0:
                pieces.append(extra[-1 - eid][3])
            else:
                pieces.append(Piece(eid, 0.0, 1.0) if a == self.u[eid] else Piece(eid, 1.0, 0.0))
        # 長さ 0 の端（リンクの端点ちょうどにスナップ）は落とす
        res['pieces'] = [p for p in pieces if abs(p.a - p.b) > FRAC_EPS] or pieces[:1]
        res['cost'] = float(dist[T])
        return done()

    def piece_coords(self, p: Piece) -> list[tuple[float, float]]:
        a = self._point_on(p.link, p.a)
        b = self._point_on(p.link, p.b)
        return [a, b]

    def health(self) -> dict:
        return {
            'case': self.case,
            'source': self.source,
            'links': self.n_links,
            'nodes': self.n_nodes,
            'floors': self.floors,
            'components': {
                k: int(len(np.unique(c[np.concatenate([self.u, self.v])]))) for k, c in self.components.items()
            },
            'load_seconds': round(self.load_seconds, 3),
        }
