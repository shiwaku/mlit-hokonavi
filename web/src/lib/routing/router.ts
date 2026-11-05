// ブラウザ内の経路探索。api/graph.py の PedestrianGraph.route と同じ手順（説明はそちら）。
// 通れるか・コストは前処理で決まっている（graph.json）。ここではスナップと Dijkstra だけ行う。
import type { ProfileKey } from '../api.ts';
import type { StaticGraph } from './types.ts';

const M_PER_DEG_LAT = 110_950.0;
/** api/graph.py の FRAC_EPS。リンクの端ちょうどに寄せたときの誤差（1e-16 など）を端とみなす */
const FRAC_EPS = 1e-9;
const snapFrac = (t: number) => (t < FRAC_EPS ? 0 : t > 1 - FRAC_EPS ? 1 : t);

export interface Snap {
	link: number;
	frac: number;
	lat: number;
	lon: number;
	dist_m: number;
	component: number;
}

/** リンクの一部（向き付き）。frac は起点からの割合で、a → b の向きに進む */
export interface Piece {
	link: number;
	a: number;
	b: number;
}

export const isForward = (p: Piece) => p.b >= p.a;

export interface RouteResult {
	pieces: Piece[];
	snap: { origin: Snap; dest: Snap } | null;
	unreachable_reason: string | null;
	cost: number | null;
	elapsed_ms: number;
}

/** 最小ヒープ（コスト, ノード） */
class Heap {
	private c: number[] = [];
	private n: number[] = [];
	get size() {
		return this.c.length;
	}
	push(cost: number, node: number) {
		const c = this.c, n = this.n;
		let i = c.length;
		c.push(cost);
		n.push(node);
		while (i > 0) {
			const p = (i - 1) >> 1;
			if (c[p] <= cost) break;
			c[i] = c[p];
			n[i] = n[p];
			i = p;
		}
		c[i] = cost;
		n[i] = node;
	}
	pop(): [number, number] {
		const c = this.c, n = this.n;
		const top: [number, number] = [c[0], n[0]];
		const lc = c.pop()!, ln = n.pop()!;
		if (c.length) {
			let i = 0;
			for (;;) {
				const l = 2 * i + 1;
				if (l >= c.length) break;
				const r = l + 1;
				const m = r < c.length && c[r] < c[l] ? r : l;
				if (c[m] >= lc) break;
				c[i] = c[m];
				n[i] = n[m];
				i = m;
			}
			c[i] = lc;
			n[i] = ln;
		}
		return top;
	}
}

/** 隣接リスト（CSR）。エッジごとに行き先・コスト・リンク・向き */
interface Adjacency {
	ptr: Int32Array;
	to: Int32Array;
	cost: Float64Array;
	link: Int32Array;
	fwd: Uint8Array;
}

export class Router {
	readonly g: StaticGraph;
	readonly nLinks: number;
	readonly nNodes: number;
	private kx: number;
	private ax: Float64Array; // リンク起点・終点の平面座標（m）
	private ay: Float64Array;
	private bx: Float64Array;
	private by: Float64Array;
	private adj = new Map<ProfileKey, Adjacency>();

	constructor(g: StaticGraph) {
		this.g = g;
		this.nLinks = g.links.u.length;
		this.nNodes = g.nodes.lon.length;
		this.kx = M_PER_DEG_LAT * Math.cos((g.lat0 * Math.PI) / 180);
		const L = g.links, N = g.nodes;
		this.ax = new Float64Array(this.nLinks);
		this.ay = new Float64Array(this.nLinks);
		this.bx = new Float64Array(this.nLinks);
		this.by = new Float64Array(this.nLinks);
		for (let i = 0; i < this.nLinks; i++) {
			this.ax[i] = N.lon[L.u[i]] * this.kx;
			this.ay[i] = N.lat[L.u[i]] * M_PER_DEG_LAT;
			this.bx[i] = N.lon[L.v[i]] * this.kx;
			this.by[i] = N.lat[L.v[i]] * M_PER_DEG_LAT;
		}
	}

	private adjacency(profile: ProfileKey): Adjacency {
		const hit = this.adj.get(profile);
		if (hit) return hit;
		const P = this.g.profiles[profile];
		const { u, v } = this.g.links;
		const deg = new Int32Array(this.nNodes + 1);
		for (let i = 0; i < this.nLinks; i++) {
			if (P.cost_fwd[i] >= 0) deg[u[i] + 1]++;
			if (P.cost_bwd[i] >= 0) deg[v[i] + 1]++;
		}
		for (let i = 0; i < this.nNodes; i++) deg[i + 1] += deg[i];
		const m = deg[this.nNodes];
		const a: Adjacency = {
			ptr: deg,
			to: new Int32Array(m),
			cost: new Float64Array(m),
			link: new Int32Array(m),
			fwd: new Uint8Array(m)
		};
		const fill = deg.slice(0, this.nNodes);
		for (let i = 0; i < this.nLinks; i++) {
			if (P.cost_fwd[i] >= 0) {
				const k = fill[u[i]]++;
				a.to[k] = v[i];
				a.cost[k] = P.cost_fwd[i];
				a.link[k] = i;
				a.fwd[k] = 1;
			}
			if (P.cost_bwd[i] >= 0) {
				const k = fill[v[i]]++;
				a.to[k] = u[i];
				a.cost[k] = P.cost_bwd[i];
				a.link[k] = i;
				a.fwd[k] = 0;
			}
		}
		this.adj.set(profile, a);
		return a;
	}

	pointOn(link: number, frac: number): [number, number] {
		const { u, v } = this.g.links, N = this.g.nodes;
		return [
			N.lon[u[link]] + (N.lon[v[link]] - N.lon[u[link]]) * frac,
			N.lat[u[link]] + (N.lat[v[link]] - N.lat[u[link]]) * frac
		];
	}

	/** 半径内の候補を、連結成分ごとに最寄り 1 本ずつ・近い順に（api/graph.py の snap_candidates） */
	snapCandidates(lat: number, lon: number, profile: ProfileKey, floor: number | null, radius: number): Snap[] {
		const P = this.g.profiles[profile];
		const L = this.g.links;
		const px = lon * this.kx, py = lat * M_PER_DEG_LAT;
		const cand: { i: number; d: number; t: number }[] = [];
		for (let i = 0; i < this.nLinks; i++) {
			if (!L.snappable[i] || (P.cost_fwd[i] < 0 && P.cost_bwd[i] < 0)) continue;
			if (floor != null && L.floor_start[i] !== floor) continue;
			const dx = this.bx[i] - this.ax[i], dy = this.by[i] - this.ay[i];
			const len2 = dx * dx + dy * dy;
			let t = len2 > 0 ? ((px - this.ax[i]) * dx + (py - this.ay[i]) * dy) / len2 : 0;
			t = snapFrac(Math.min(1, Math.max(0, t)));
			const d = Math.hypot(this.ax[i] + t * dx - px, this.ay[i] + t * dy - py);
			if (d <= radius) cand.push({ i, d, t });
		}
		// 同じ距離はリンクの行番号順（api/graph.py と揃える）
		cand.sort((p, q) => Math.round(p.d * 1e6) - Math.round(q.d * 1e6) || p.i - q.i);
		const out: Snap[] = [];
		const seen = new Set<number>();
		for (const c of cand) {
			if (out.length >= this.g.snap.candidates) break;
			const comp = P.component[L.u[c.i]];
			if (seen.has(comp)) continue;
			seen.add(comp);
			const [x, y] = this.pointOn(c.i, c.t);
			out.push({ link: c.i, frac: c.t, lat: y, lon: x, dist_m: c.d, component: comp });
		}
		return out;
	}

	route(
		oLat: number,
		oLon: number,
		dLat: number,
		dLon: number,
		profile: ProfileKey,
		oFloor: number | null = null,
		dFloor: number | null = null
	): RouteResult {
		const t0 = performance.now();
		const P = this.g.profiles[profile];
		if (!P) throw new Error(`unknown profile: ${profile}`);
		const res: RouteResult = { pieces: [], snap: null, unreachable_reason: null, cost: null, elapsed_ms: 0 };
		const done = (reason: string | null = null) => {
			res.unreachable_reason = reason;
			res.elapsed_ms = performance.now() - t0;
			return res;
		};
		const label = P.label;

		let pairs: [Snap, Snap][] = [];
		let oc: Snap[] = [], dc: Snap[] = [];
		const radii = this.g.snap.radii_m;
		for (let r = 0; r < radii.length; r++) {
			oc = this.snapCandidates(oLat, oLon, profile, oFloor, radii[r]);
			dc = this.snapCandidates(dLat, dLon, profile, dFloor, radii[r]);
			if (r === 0 && (!oc.length || !dc.length)) {
				const which = !oc.length ? '出発地' : '目的地';
				return done(`${which}の${radii[r].toFixed(0)}m以内に、${label}で通れる歩行空間ネットワークがありません`);
			}
			pairs = [];
			for (const o of oc) for (const d of dc) if (o.component === d.component) pairs.push([o, d]);
			if (pairs.length) break;
		}
		if (!pairs.length) {
			res.snap = { origin: oc[0], dest: dc[0] };
			return done(`出発地と目的地が、${label}で通れる道ではつながっていません（データ上の別のネットワーク）`);
		}
		let [o, d] = pairs[0];
		for (const p of pairs) if (p[0].dist_m + p[1].dist_m < o.dist_m + d.dist_m) [o, d] = p;
		res.snap = { origin: o, dest: d };

		// 仮ノード S（出発地）・T（目的地）とそのエッジ
		const { u, v } = this.g.links;
		const S = this.nNodes, T = this.nNodes + 1;
		type Extra = { from: number; to: number; cost: number; piece: Piece };
		const extra: Extra[] = [];
		const cf = P.cost_fwd, cb = P.cost_bwd;
		if (cb[o.link] >= 0) extra.push({ from: S, to: u[o.link], cost: cb[o.link] * o.frac, piece: { link: o.link, a: o.frac, b: 0 } });
		if (cf[o.link] >= 0) extra.push({ from: S, to: v[o.link], cost: cf[o.link] * (1 - o.frac), piece: { link: o.link, a: o.frac, b: 1 } });
		if (cf[d.link] >= 0) extra.push({ from: u[d.link], to: T, cost: cf[d.link] * d.frac, piece: { link: d.link, a: 0, b: d.frac } });
		if (cb[d.link] >= 0) extra.push({ from: v[d.link], to: T, cost: cb[d.link] * (1 - d.frac), piece: { link: d.link, a: 1, b: d.frac } });
		if (o.link === d.link) {
			const c = d.frac >= o.frac ? cf[o.link] : cb[o.link];
			if (c >= 0) extra.push({ from: S, to: T, cost: c * Math.abs(d.frac - o.frac), piece: { link: o.link, a: o.frac, b: d.frac } });
		}
		const extraFrom = new Map<number, number[]>();
		extra.forEach((e, k) => extraFrom.set(e.from, [...(extraFrom.get(e.from) ?? []), k]));

		// Dijkstra。pred には「どのエッジで来たか」（>= 0 は隣接リストの添字、< 0 は extra の -1-k）
		const A = this.adjacency(profile);
		const n = this.nNodes + 2;
		const dist = new Float64Array(n).fill(Infinity);
		const pred = new Int32Array(n).fill(0x7fffffff);
		const predNode = new Int32Array(n).fill(-1);
		const heap = new Heap();
		dist[S] = 0;
		heap.push(0, S);
		while (heap.size) {
			const [c, x] = heap.pop();
			if (c > dist[x]) continue;
			if (x === T) break;
			const relax = (y: number, w: number, e: number) => {
				const nc = c + w;
				if (nc < dist[y]) {
					dist[y] = nc;
					pred[y] = e;
					predNode[y] = x;
					heap.push(nc, y);
				}
			};
			if (x < this.nNodes) for (let k = A.ptr[x]; k < A.ptr[x + 1]; k++) relax(A.to[k], A.cost[k], k);
			for (const k of extraFrom.get(x) ?? []) relax(extra[k].to, extra[k].cost, -1 - k);
		}
		if (!Number.isFinite(dist[T])) {
			return done(`${label}で通れる経路が見つかりません（一方通行・通れない区間で行き止まり）`);
		}

		const pieces: Piece[] = [];
		for (let x = T; x !== S; x = predNode[x]) {
			const e = pred[x];
			if (e < 0) pieces.push(extra[-1 - e].piece);
			else pieces.push(A.fwd[e] ? { link: A.link[e], a: 0, b: 1 } : { link: A.link[e], a: 1, b: 0 });
		}
		pieces.reverse();
		const nonEmpty = pieces.filter((p) => Math.abs(p.a - p.b) > FRAC_EPS);
		res.pieces = nonEmpty.length ? nonEmpty : pieces.slice(0, 1);
		res.cost = dist[T];
		return done();
	}
}
