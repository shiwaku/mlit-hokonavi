// 経路（Piece の列）→ 案内（ステップ）と LineString。api/guidance.py の build を移したもの。
// 区切り方・文言・丸め方を Python と揃える（tests/test_parity.py のフィクスチャで確かめる）
import type { ProfileKey, Route, Step } from '../api.ts';
import { isForward, type RouteResult, type Router, type Snap } from './router.ts';

const MIN_STEP_M = 5.0;
const SPLIT_ON_TURN = new Set(['walk', 'shared', 'underpass', 'indoor', 'footbridge', 'slope']);
const TURN_TEXT: Record<string, string> = {
	start: '',
	straight: 'まっすぐ',
	slight_right: '右斜め前へ',
	slight_left: '左斜め前へ',
	right: '右に曲がり',
	left: '左に曲がり',
	uturn: '引き返し'
};

/**
 * Python の round(x, digits)。浮動小数点数の正確な 10 進展開で丸め、ちょうど半分のときだけ偶数に寄せる。
 * x * 10^digits を Math.round すると掛け算の誤差で 7 桁目がずれる（139.6994058 / 139.6994059）ので使わない。
 * toFixed は 2 進の値を正確に 10 進展開して丸める（ちょうど半分は大きい方）
 */
export function pyRound(x: number, digits = 0): number {
	if (!Number.isFinite(x) || Math.abs(x) >= 1e21) return x;
	const exact = Math.abs(x).toFixed(Math.min(100, digits + 60));
	const dot = exact.indexOf('.');
	const tail = exact.slice(dot + 1 + digits);
	const isHalf = tail[0] === '5' && /^0*$/.test(tail.slice(1));
	let y = Number(Math.abs(x).toFixed(digits));
	if (isHalf) {
		// toFixed は大きい方に寄せている。最後の桁が奇数なら 1 つ戻す
		const scaled = exact.slice(0, dot).concat(exact.slice(dot + 1, dot + 1 + digits));
		if (Number(scaled.at(-1)) % 2 === 0) y = Number((Number(scaled) / 10 ** digits).toFixed(digits));
	}
	return x < 0 ? -y : y;
}

const mod = (a: number, n: number) => ((a % n) + n) % n;

export function floorLabel(f: number | null | undefined): string {
	if (f == null || Number.isNaN(f)) return '不明な階';
	if (f === 0) return '地上';
	return f > 0 ? `${f}階` : `地下${-f}階`;
}

export function bearing(a: [number, number], b: [number, number], lat0: number): number {
	const dx = (b[0] - a[0]) * Math.cos((lat0 * Math.PI) / 180);
	const dy = b[1] - a[1];
	return mod((Math.atan2(dx, dy) * 180) / Math.PI, 360);
}

export function turnOf(prev: number | null, cur: number): string {
	if (prev == null) return 'start';
	const d = mod(cur - prev + 540, 360) - 180; // -180〜180、正が右
	const a = Math.abs(d);
	if (a < 20) return 'straight';
	if (a > 150) return 'uturn';
	const side = d > 0 ? 'right' : 'left';
	return a < 60 ? `slight_${side}` : side;
}

interface WorkStep extends Step {
	updown?: string;
}

function instruction(s: WorkStep, kindLabel: Record<string, string>): string {
	const dist = pyRound(s.dist_m);
	const head = TURN_TEXT[s.turn] ? `${TURN_TEXT[s.turn]}、` : '';
	const f0 = s.floor_from ?? 0, f1 = s.floor_to ?? 0;
	const updown = f1 > f0 ? '上る' : f1 < f0 ? '下る' : s.updown || '進む';
	switch (s.kind) {
		case 'elevator':
			return `エレベーターで${floorLabel(s.floor_to)}へ`;
		case 'escalator':
			return `${head}エスカレーターを${updown}`;
		case 'stairs':
			return `${head}階段を${updown}（${dist}m）`;
		case 'crosswalk':
			return `${head}横断歩道を渡る（${dist}m${s.signal ? '・' + s.signal : ''}）`;
		case 'crossing':
			return `${head}道路を横断する（横断歩道なし・${dist}m）`;
		case 'railway_crossing':
			return `${head}踏切を渡る（${dist}m）`;
		default:
			return `${head}${kindLabel[s.kind]}を${dist}m進む`;
	}
}

const sameXY = (p: [number, number], q: [number, number]) => p[0] === q[0] && p[1] === q[1];

/** 探索結果 → API の /route と同じ形（web/src/lib/api.ts の Route） */
export function buildRoute(router: Router, r: RouteResult, profile: ProfileKey): Route {
	const G = router.g, L = G.links, P = G.profiles[profile];
	const snapJson = (s: Snap | null) =>
		s ? { lat: pyRound(s.lat, 7), lon: pyRound(s.lon, 7), dist_m: pyRound(s.dist_m, 1) } : null;
	const base: Route = {
		profile,
		snap: { origin: snapJson(r.snap?.origin ?? null), dest: snapJson(r.snap?.dest ?? null) },
		unreachable_reason: r.unreachable_reason,
		geometry: null,
		floor_path: null,
		summary: null,
		steps: [],
		warnings: [],
		elapsed_ms: pyRound(r.elapsed_ms, 1)
	};
	if (r.unreachable_reason != null) return base;

	const coords: [number, number][] = [];
	// 3D 表示用の [経度, 緯度, 階]。coords は同じ地点を 1 点にまとめるのでエレベーターの昇降が消える。
	// こちらは地点か階が変わるたびに点を足す（api/guidance.py と同じ）
	const floorPath: [number, number, number | null][] = [];
	const steps: WorkStep[] = [];
	let prevBearing: number | null = null;
	let totalD = 0, totalT = 0, unknownLinks = 0;
	const counts = { stairs: 0, elevator: 0, escalator: 0, crosswalk: 0, crossing: 0 };

	for (const p of r.pieces) {
		const li = p.link;
		const fwd = isForward(p);
		const frac = Math.abs(p.b - p.a);
		const d = L.distance[li] * frac;
		const t = P.time[li] * frac;
		const a = router.pointOn(li, p.a), b = router.pointOn(li, p.b);
		const brg: number | null = !sameXY(a, b) ? bearing(a, b, G.lat0) : prevBearing;
		const kind = L.kind[li];
		const fFrom = fwd ? L.floor_start[li] : L.floor_end[li];
		const fTo = fwd ? L.floor_end[li] : L.floor_start[li];
		unknownLinks += P.unknown[li];

		const turn = brg != null ? turnOf(prevBearing, brg) : 'straight';
		const last = steps.length ? steps[steps.length - 1] : null;
		let same =
			last != null &&
			last.kind === kind &&
			(!SPLIT_ON_TURN.has(kind) || ['straight', 'slight_right', 'slight_left'].includes(turn)) &&
			kind !== 'crosswalk' &&
			kind !== 'crossing';
		if (last != null && SPLIT_ON_TURN.has(kind) && SPLIT_ON_TURN.has(last.kind) && d < MIN_STEP_M) same = true;
		if (!coords.length) coords.push(a);
		if (!sameXY(coords[coords.length - 1], b)) coords.push(b);
		for (const pt of [[a[0], a[1], fFrom], [b[0], b[1], fTo]] as [number, number, number | null][]) {
			const q = floorPath[floorPath.length - 1];
			if (!q || !sameXY([q[0], q[1]], [pt[0], pt[1]]) || q[2] !== pt[2]) floorPath.push(pt);
		}
		if (same && last) {
			last.dist_m += d;
			last.time_s += t;
			last.floor_to = fTo;
			last.coord_end = coords.length - 1;
			last.link_ids.push(L.id[li]);
		} else {
			if (kind in counts) counts[kind as keyof typeof counts]++;
			const step: WorkStep = {
				kind,
				label: G.kind_label[kind],
				turn,
				dist_m: d,
				time_s: t,
				floor_from: fFrom,
				floor_to: fTo,
				coord_start: Math.max(coords.length - 2, 0),
				coord_end: coords.length - 1,
				link_ids: [L.id[li]],
				instruction: ''
			};
			if (L.signal[li] != null) step.signal = L.signal[li]!;
			if (['stairs', 'escalator', 'slope'].includes(kind) && fFrom === fTo && L.uphill[li] !== 0) {
				step.updown = (L.uphill[li] === 1) === fwd ? '上る' : '下る';
			}
			steps.push(step);
		}
		totalD += d;
		totalT += t;
		if (brg != null) prevBearing = brg;
	}

	const out: Step[] = steps.map((s) => {
		const rest: Step = { ...s };
		rest.dist_m = pyRound(s.dist_m, 1);
		rest.time_s = pyRound(s.time_s);
		rest.instruction = instruction({ ...s, dist_m: rest.dist_m }, G.kind_label);
		return rest;
	});
	if (out.length) {
		const ft = out[out.length - 1].floor_to;
		out.push({
			kind: 'arrive',
			label: '到着',
			turn: 'straight',
			dist_m: 0,
			time_s: 0,
			floor_from: ft,
			floor_to: ft,
			coord_start: coords.length - 1,
			coord_end: coords.length - 1,
			link_ids: [],
			instruction: '目的地に到着'
		});
	}

	const warnings: string[] = [];
	const radius = G.snap.radii_m[0];
	for (const [key, name] of [['origin', '出発地'], ['dest', '目的地']] as const) {
		const s = r.snap?.[key];
		if (s && s.dist_m > radius) {
			warnings.push(
				`${name}の近くに${P.label}で通れる道（ほかの道とつながるもの）がないため、${pyRound(s.dist_m)}m離れた道から案内します`
			);
		}
	}
	if (unknownLinks) {
		warnings.push(`${P.label}の通行判断に使う属性が「不明」の区間を ${unknownLinks} 本含みます。現地の状況を確かめてください`);
	}

	return {
		...base,
		geometry: { type: 'LineString', coordinates: coords.map(([x, y]) => [pyRound(x, 7), pyRound(y, 7)]) },
		floor_path: floorPath.map(([x, y, f]) => [pyRound(x, 7), pyRound(y, 7), f]),
		summary: { dist_m: pyRound(totalD, 1), time_s: pyRound(totalT), link_count: r.pieces.length, ...counts },
		steps: out,
		warnings
	};
}
