// 経路・ネットワークの型。FastAPI（docs/api-design.md）のレスポンスと同じ形で、
// ブラウザ内の探索（routing/guidance.ts）もこの形で返す
import type * as GeoJSON from 'geojson';

export type ProfileKey = 'walk' | 'wheelchair' | 'stroller' | 'vision';

export interface Profile {
	key: ProfileKey;
	label: string;
	speed_mps: number;
	description: string;
}

/** 0: 通れる、1: 属性が不明、2: 片方向のみ、3: 通れない */
export type Access = 0 | 1 | 2 | 3;

export interface LinkProps {
	link_id: string;
	kind: string;
	label: string;
	distance: number;
	floor: number | null;
	floor_end: number | null;
	width: string | null;
	slope: string | null;
	lev_diff: string | null;
	signal: string | null;
	brail_tile: string | null;
	elevator: string | null;
	roof: string | null;
	direction: number;
	acc_walk: Access;
	acc_wheelchair: Access;
	acc_stroller: Access;
	acc_vision: Access;
}

export type Network = GeoJSON.FeatureCollection<GeoJSON.LineString, LinkProps>;

export interface Step {
	kind: string;
	label: string;
	turn: string;
	dist_m: number;
	time_s: number;
	floor_from: number | null;
	floor_to: number | null;
	coord_start: number;
	coord_end: number;
	link_ids: string[];
	instruction: string;
	signal?: string;
	/** 階段・エスカレーター・スロープで、階が変わらないときの上り下り（勾配から） */
	updown?: string;
}

export interface SnapPoint {
	lat: number;
	lon: number;
	dist_m: number;
}

export interface Route {
	profile: ProfileKey;
	snap: { origin: SnapPoint | null; dest: SnapPoint | null };
	unreachable_reason: string | null;
	geometry: GeoJSON.LineString | null;
	/** 3D 表示用の [経度, 緯度, 階]。地点か階が変わるたびに 1 点（エレベーターの昇降も点になる） */
	floor_path: [number, number, number | null][] | null;
	summary: {
		dist_m: number;
		time_s: number;
		link_count: number;
		stairs: number;
		elevator: number;
		escalator: number;
		crosswalk: number;
		crossing: number;
	} | null;
	steps: Step[];
	warnings: string[];
	elapsed_ms: number;
}

export function formatDuration(s: number): string {
	const m = Math.round(s / 60);
	return m < 1 ? '1分未満' : `${m}分`;
}
