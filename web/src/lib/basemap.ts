// 背景地図（地理院 最適化ベクトルタイル 淡色）のダーク化。
// shiwaku/japan-food-store-master の viewer と同じく、スタイル中の色の明度だけを反転する（色相は保ち、彩度を少し抑える）。
// ただしスタイルは差し替えず、背景レイヤーの色の paint だけを setPaintProperty で塗り替える。
// 差し替えると経路・ネットワークなど自前のレイヤーが消え、貼り直しが要るため
import type { Map as MapLibreMap } from 'maplibre-gl';

type PaintProp = Parameters<MapLibreMap['setPaintProperty']>[1];
type PaintValue = Parameters<MapLibreMap['setPaintProperty']>[2];
import type { Theme } from './theme.ts';

type RGBA = [number, number, number, number];

export function parseColor(str: string): RGBA | null {
	const s = str.trim();
	const rgba = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i.exec(s);
	if (rgba) return [+rgba[1], +rgba[2], +rgba[3], rgba[4] !== undefined ? +rgba[4] : 1];
	const hex = /^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(s);
	if (hex) {
		let h = hex[1];
		if (h.length <= 4) h = [...h].map((c) => c + c).join('');
		const a = h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
		return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), a];
	}
	return null;
}

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
	r /= 255;
	g /= 255;
	b /= 255;
	const max = Math.max(r, g, b),
		min = Math.min(r, g, b);
	const l = (max + min) / 2;
	let h = 0,
		s = 0;
	if (max !== min) {
		const d = max - min;
		s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
		if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
		else if (max === g) h = (b - r) / d + 2;
		else h = (r - g) / d + 4;
		h /= 6;
	}
	return [h, s, l];
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
	if (s === 0) {
		const v = Math.round(l * 255);
		return [v, v, v];
	}
	const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
	const p = 2 * l - q;
	const hue = (t: number) => {
		if (t < 0) t += 1;
		if (t > 1) t -= 1;
		if (t < 1 / 6) return p + (q - p) * 6 * t;
		if (t < 1 / 2) return q;
		if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
		return p;
	};
	return [Math.round(hue(h + 1 / 3) * 255), Math.round(hue(h) * 255), Math.round(hue(h - 1 / 3) * 255)];
}

/** 明度を反転して暗い色にする（色相は保ち、彩度は 85 %）。色でない文字列はそのまま */
export function darkenColor(str: string): string {
	const c = parseColor(str);
	if (!c) return str;
	const [h, s, l] = rgbToHsl(c[0], c[1], c[2]);
	const [r, g, b] = hslToRgb(h, s * 0.85, Math.min(0.9, Math.max(0.05, 1 - l)));
	return `rgba(${r},${g},${b},${c[3]})`;
}

/** paint の値（文字列か式の配列）の中の色だけを、再帰的にダーク化する */
export function darkenValue(v: unknown): unknown {
	if (typeof v === 'string') return parseColor(v) ? darkenColor(v) : v;
	if (Array.isArray(v)) return v.map(darkenValue);
	return v;
}

type PaintTable = { layer: string; prop: PaintProp; light: PaintValue; dark: PaintValue }[];

/** 背景スタイルの色の paint を、ライト・ダーク両方の値で控えておく（読み込んだ直後のスタイルから作る） */
export function basemapPaintTable(map: MapLibreMap): PaintTable {
	const out: PaintTable = [];
	for (const layer of map.getStyle().layers) {
		const paint = (layer as { paint?: Record<string, unknown> }).paint;
		if (!paint) continue;
		for (const [prop, value] of Object.entries(paint)) {
			if (prop.includes('color')) out.push({
					layer: layer.id,
					prop: prop as PaintProp,
					light: value as PaintValue,
					dark: darkenValue(value) as PaintValue
				});
		}
	}
	return out;
}

export function applyBasemapTheme(map: MapLibreMap, table: PaintTable, theme: Theme): void {
	for (const t of table) {
		if (map.getLayer(t.layer)) map.setPaintProperty(t.layer, t.prop, theme === 'dark' ? t.dark : t.light);
	}
}

/** 自前のレイヤー（ネットワーク・経路など）の配色。ダークでは背景から浮くよう明るめ・縁取りは暗く */
export const OVERLAY = {
	light: { network: '#0d9488', route: '#2563eb', casing: '#ffffff', snap: '#334155', areaPoint: '#2563eb', halo: '#ffffff' },
	dark: { network: '#2dd4bf', route: '#60a5fa', casing: '#0b1220', snap: '#cbd5e1', areaPoint: '#60a5fa', halo: '#0b1220' }
} as const;
