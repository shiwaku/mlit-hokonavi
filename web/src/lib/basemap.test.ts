import { describe, expect, it } from 'vitest';
import { darkenColor, darkenValue, parseColor } from './basemap.ts';

describe('parseColor', () => {
	it('rgb / rgba / 16 進（3・4・6・8 桁）を読む', () => {
		expect(parseColor('rgb(255, 0, 10)')).toEqual([255, 0, 10, 1]);
		expect(parseColor('rgba(1,2,3,0.5)')).toEqual([1, 2, 3, 0.5]);
		expect(parseColor('#fff')).toEqual([255, 255, 255, 1]);
		expect(parseColor('#00000080')?.[3]).toBeCloseTo(0.502, 3);
		expect(parseColor('red')).toBeNull();
		expect(parseColor('Noto Sans')).toBeNull();
	});
});

describe('darkenColor', () => {
	it('白は暗く、黒は明るくなる（明度の反転・上下限あり）', () => {
		expect(darkenColor('#ffffff')).toBe('rgba(13,13,13,1)');
		expect(darkenColor('rgb(0,0,0)')).toBe('rgba(230,230,230,1)');
	});
	it('透明度と色相を保つ', () => {
		const [r, g, b, a] = parseColor(darkenColor('rgba(205,220,240,0.4)'))!;
		expect(a).toBe(0.4);
		expect(b).toBeGreaterThan(r); // 青みのまま
		expect(r + g + b).toBeLessThan(205 + 220 + 240);
	});
	it('色でない文字列はそのまま', () => {
		expect(darkenColor('transparent')).toBe('transparent');
	});
});

describe('darkenValue', () => {
	it('式の中の色だけを変える（演算子・数値・プロパティ名はそのまま）', () => {
		const expr = ['step', ['zoom'], 'rgba(255,255,255,1)', 4, '#cddcf0'];
		const out = darkenValue(expr) as unknown[];
		expect(out[0]).toBe('step');
		expect(out[1]).toEqual(['zoom']);
		expect(out[2]).toBe('rgba(13,13,13,1)');
		expect(out[3]).toBe(4);
		expect(String(out[4]).startsWith('rgba(')).toBe(true);
	});
});
