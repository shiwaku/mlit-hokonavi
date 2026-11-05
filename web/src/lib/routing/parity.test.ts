// Python（api/）と同じ問い合わせで同じ結果になるか。フィクスチャは tests/test_parity.py が書く
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { describe, expect, it } from 'vitest';
import type { ProfileKey, Route } from '../api.ts';
import { buildRoute, pyRound } from './guidance.ts';
import { Router } from './router.ts';
import type { StaticGraph } from './types.ts';

interface Query {
	from_lat: number;
	from_lon: number;
	to_lat: number;
	to_lon: number;
	profile: ProfileKey;
	from_floor?: number;
	to_floor?: number;
}
interface Suite {
	name: string;
	graph: StaticGraph;
	cases: { query: Query; expected: Omit<Route, 'elapsed_ms'> }[];
}

function solve(router: Router, q: Query): Omit<Route, 'elapsed_ms'> {
	const r = router.route(q.from_lat, q.from_lon, q.to_lat, q.to_lon, q.profile, q.from_floor ?? null, q.to_floor ?? null);
	const { elapsed_ms: _e, ...body } = buildRoute(router, r, q.profile);
	return body;
}

function load(path: string): Suite[] {
	return JSON.parse(readFileSync(path, 'utf-8'));
}

describe('pyRound', () => {
	it('Python と同じく .5 は偶数に丸める', () => {
		expect(pyRound(2.5)).toBe(2);
		expect(pyRound(3.5)).toBe(4);
		expect(pyRound(-0.5)).toBe(-0);
		expect(pyRound(1.25, 1)).toBe(1.2);
		expect(pyRound(12.34567, 2)).toBe(12.35);
	});
});

const fixture = fileURLToPath(new URL('./__fixtures__/parity.json', import.meta.url));
for (const suite of load(fixture)) {
	describe(`合成ネットワーク: ${suite.name}`, () => {
		const router = new Router(suite.graph);
		it.each(suite.cases.map((c, i) => [i, c.query.profile, c] as const))('#%i %s', (_i, _p, c) => {
			expect(solve(router, c.query)).toEqual(c.expected);
		});
	});
}

// 実データ（git 管理外）。uv run python -m tests.test_parity --real shinjuku で作ったときだけ走る
const real = fileURLToPath(new URL('../../../../network/shinjuku/parity.json', import.meta.url));
describe.skipIf(!existsSync(real))('実データ: shinjuku', () => {
	const suites = existsSync(real) ? load(real) : [];
	for (const suite of suites) {
		const router = new Router(suite.graph);
		it(`${suite.cases.length} 件の経路が Python と一致する`, () => {
			const diffs: string[] = [];
			for (const [i, c] of suite.cases.entries()) {
				const got = solve(router, c.query);
				if (!isDeepStrictEqual(got, c.expected)) {
					diffs.push(`#${i} ${c.query.profile}: ${got.summary?.dist_m} vs ${c.expected.summary?.dist_m} / ${got.unreachable_reason} vs ${c.expected.unreachable_reason}`);
				}
			}
			expect(diffs).toEqual([]);
		});
	}
});
