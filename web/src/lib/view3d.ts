// 3D 表示：ネットワークと経路を階の高さに持ち上げて描く（deck.gl の PathLayer を MapLibre に重ねる）
//
// データには高さ（m）が無く、階（floor）しか無い。表示用に「1 階分 = FLOOR_M m」と決めて、
// 階 × FLOOR_M × 強調倍率 を高さにする（地下はマイナス）。実際の階高・標高とは関係ない。
// 階は仕様どおり「屋外の地表 0・建物の 1 階 1・地下 1 階 -1」なので、建物の 1 階は地表より 1 段上に描かれる。
//
// 発光（グロー）は探索した経路だけ（太いにじみ 2 層＋明るい芯）。ネットワークは光らせない
// （リンクが 1 万本規模あり、光らせると経路と競合し、アクセス区分の色も混ざって読みにくくなる）。
// ダークでは加算合成（重なるほど明るい）、ライトでは普通の半透明（加算すると白く飛ぶ）。
import { MapView } from '@deck.gl/core';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { PathLayer } from '@deck.gl/layers';
import type { Access, Network, ProfileKey, Route } from './api.ts';
import type { Theme } from './theme.ts';

/** 1 階分の高さ（表示用の仮の値） */
export const FLOOR_M = 4;

type RGBA = [number, number, number, number];
type Path3 = [number, number, number][];

function rgba(hex: string, alpha = 255): RGBA {
	const n = parseInt(hex.slice(1), 16);
	return [(n >> 16) & 255, (n >> 8) & 255, n & 255, alpha];
}

/** 白に寄せる（発光の芯） */
const lighten = ([r, g, b, a]: RGBA, t: number): RGBA => [r + (255 - r) * t, g + (255 - g) * t, b + (255 - b) * t, a];
const withAlpha = ([r, g, b]: RGBA, a: number): RGBA => [r, g, b, a];

export interface View3dOptions {
	network: Network | null;
	route: Route | null;
	profile: ProfileKey;
	viewFloor: 'all' | number;
	/** 強調倍率（1 階分の高さに掛ける） */
	exaggeration: number;
	theme: Theme;
	/** アクセス区分ごとの色（0 は通れる） */
	accessColor: readonly string[];
	routeColor: string;
	casingColor: string;
}

interface Seg {
	path: Path3;
	acc: Access;
	on: boolean;
}

/** 地図に重ねる deck.gl のオーバーレイ（線は地図の上に描く。地下の線も地面に隠れない）。
 * 既定の奥行きの範囲（far 面）は地面のすぐ先までなので、地下を深く描くと切れる。far 面を遠くに置く。
 * id が 'mapbox' のビューを渡すと、オーバーレイはそれを地図のビューとして使う（型では views を渡せない） */
export function createOverlay(): MapboxOverlay {
	const views = new MapView({ id: 'mapbox', farZMultiplier: 10 }) as unknown as undefined;
	return new MapboxOverlay({ interleaved: false, layers: [], views });
}

// 加算合成。奥行きは書かない（にじみ同士で隠し合わないように）
const ADDITIVE = {
	blend: true,
	blendColorOperation: 'add',
	blendColorSrcFactor: 'src-alpha',
	blendColorDstFactor: 'one',
	blendAlphaOperation: 'add',
	blendAlphaSrcFactor: 'one',
	blendAlphaDstFactor: 'one',
	depthWriteEnabled: false
} as const;

export function layers(o: View3dOptions) {
	const z = (f: number | null) => (f ?? 0) * FLOOR_M * o.exaggeration;
	const segs: Seg[] = (o.network?.features ?? []).map((f) => {
		const p = f.properties;
		const [a, b] = f.geometry.coordinates;
		return {
			path: [
				[a[0], a[1], z(p.floor)],
				[b[0], b[1], z(p.floor_end)]
			],
			acc: p[`acc_${o.profile}`],
			on: o.viewFloor === 'all' || p.floor === o.viewFloor || p.floor_end === o.viewFloor
		};
	});
	const dark = o.theme === 'dark';
	const lit = segs.filter((s) => s.on);
	const base = (s: Seg) => rgba(o.accessColor[s.acc]);
	const width = { widthUnits: 'pixels' as const, capRounded: true, jointRounded: true };
	const glowParams = dark ? ADDITIVE : { depthWriteEnabled: false };
	const triggers = [o.profile, o.viewFloor, o.theme, o.exaggeration];
	const net = (id: string, data: Seg[], w: number, color: (s: Seg) => RGBA) =>
		new PathLayer<Seg>({
			id,
			data,
			getPath: (s) => s.path,
			getColor: color,
			getWidth: w,
			...width,
			updateTriggers: { getColor: triggers, getPath: triggers }
		});

	// 経路：上の階のネットワークに隠れないよう、奥行きに関係なく手前に描く
	const route: Path3 = (o.route?.floor_path ?? []).map(([x, y, f]) => [x, y, z(f)]);
	const rc = rgba(o.routeColor);
	const routeLayer = (id: string, w: number, color: RGBA, glow = false) =>
		new PathLayer<{ path: Path3 }>({
			id,
			data: route.length > 1 ? [{ path: route }] : [],
			getPath: (d) => d.path,
			getColor: color,
			getWidth: w,
			...width,
			parameters: { ...(glow ? glowParams : {}), depthCompare: 'always' as const },
			updateTriggers: { getPath: triggers, getColor: [o.theme] }
		});

	return [
		// 表示しない階は、発光させずに薄く
		net('network-3d-dim', segs.filter((s) => !s.on), 1.2, (s) => withAlpha(base(s), dark ? 40 : 55)),
		net('network-3d', lit, 1.3, (s) => withAlpha(base(s), dark ? 150 : 190)),
		// 経路：強い発光
		routeLayer('route-3d-glow-wide', 40, withAlpha(rc, dark ? 60 : 40), true),
		routeLayer('route-3d-glow', 18, withAlpha(rc, dark ? 150 : 90), true),
		routeLayer('route-3d-casing', 9, rgba(o.casingColor, dark ? 200 : 255)),
		routeLayer('route-3d', 7, rc),
		routeLayer('route-3d-core', 2, lighten(rc, 0.7))
	];
}
