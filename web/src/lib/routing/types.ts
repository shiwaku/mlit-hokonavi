// 静的 JSON（preprocess/export_static.py・api/export.py の static_graph）の型
import type { ProfileKey } from '../api.ts';

/** api/export.py の STATIC_FORMAT と揃える */
export const FORMAT = 2;

export interface ProfileData {
	label: string;
	speed_mps: number;
	description: string;
	/** 起点→終点 / 終点→起点のコスト。-1 は通れない */
	cost_fwd: number[];
	cost_bwd: number[];
	/** 所要時間（秒）。向きに依らない */
	time: number[];
	/** 1: 通行判断に使う属性が不明 */
	unknown: number[];
	/** ノードごとの連結成分（通れるリンクだけの無向グラフ） */
	component: number[];
}

export interface StaticGraph {
	format: number;
	case: string;
	/** 地区の名前（まとめた地区は「／」でつなぐ） */
	title: string;
	/** 元の地区（ほこナビDP のデータセット）。公共データ利用規約の出典表示に使う */
	sources: Source[];
	floors: number[];
	lat0: number;
	snap: { radii_m: number[]; candidates: number };
	kind_label: Record<string, string>;
	nodes: { lon: number[]; lat: number[] };
	links: {
		id: string[];
		u: number[];
		v: number[];
		distance: number[];
		floor_start: (number | null)[];
		floor_end: (number | null)[];
		snappable: number[];
		kind: string[];
		signal: (string | null)[];
		/** 1: 起点→終点が上り、-1: 下り、0: 勾配なし・不明 */
		uphill: number[];
	};
	profiles: Record<ProfileKey, ProfileData>;
}

export interface Source {
	dataset: string;
	title: string;
	publisher: string | null;
	license: string;
	license_url: string;
	data_updated: string | null;
	url: string;
}

export interface AreaInfo {
	case: string;
	title: string;
	links: number;
	/** [西, 南, 東, 北] */
	bbox: [number, number, number, number];
	data_updated: string | null;
}

export interface AreaList {
	default: string;
	areas: AreaInfo[];
}
