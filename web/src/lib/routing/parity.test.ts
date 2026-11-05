// Python（api/）と同じ問い合わせで同じ結果になるか。フィクスチャは tests/test_parity.py が書く
import { existsSync, readdirSync, readFileSync } from 'node:fs';
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
	/** cost: Python での探索コスト（到達不能は null） */
	cases: { query: Query; expected: Omit<Route, 'elapsed_ms'>; cost: number | null }[];
}

function solveWithCost(router: Router, q: Query): [Omit<Route, 'elapsed_ms'>, number | null] {
	const r = router.route(q.from_lat, q.from_lon, q.to_lat, q.to_lon, q.profile, q.from_floor ?? null, q.to_floor ?? null);
	const { elapsed_ms: _e, ...body } = buildRoute(router, r, q.profile);
	return [body, r.cost];
}
const solve = (router: Router, q: Query) => solveWithCost(router, q)[0];

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

// 実データ（git 管理外）。uv run python -m tests.test_parity --real-all（または --real <case>）で作った地区だけ走る
const netDir = fileURLToPath(new URL('../../../../network/', import.meta.url));
const realFiles = existsSync(netDir)
	? readdirSync(netDir)
			.map((c) => `${netDir}${c}/parity.json`)
			.filter((f) => existsSync(f))
	: [];
/**
 * 同じコストの経路が 2 本以上あると（並行する歩道など）、どちらを選ぶかは Dijkstra の処理順で決まり、
 * scipy と揃えられない。探索のコストと出発地・目的地の寄せ先が同じなら「同点の別経路」として数える
 */
function isTie(got: Omit<Route, 'elapsed_ms'>, gotCost: number | null, exp: Omit<Route, 'elapsed_ms'>, expCost: number | null) {
	if (gotCost == null || expCost == null) return false;
	return Math.abs(gotCost - expCost) <= 1e-6 * Math.max(1, expCost) && isDeepStrictEqual(got.snap, exp.snap);
}

describe.skipIf(!realFiles.length)('実データ', () => {
	for (const suite of realFiles.flatMap(load)) {
		it(`${suite.name}: ${suite.cases.length} 件の経路が Python と一致する`, () => {
			const router = new Router(suite.graph);
			const diffs: string[] = [];
			let ties = 0;
			for (const [i, c] of suite.cases.entries()) {
				const [got, cost] = solveWithCost(router, c.query);
				if (isDeepStrictEqual(got, c.expected)) continue;
				if (isTie(got, cost, c.expected, c.cost)) ties++;
				else diffs.push(`#${i} ${c.query.profile}: ${got.summary?.dist_m} vs ${c.expected.summary?.dist_m} / ${got.unreachable_reason} vs ${c.expected.unreachable_reason}`);
			}
			expect(diffs).toEqual([]);
			// 同点は例外的なもの。増えたら手順のずれを疑う
			expect(ties).toBeLessThanOrEqual(suite.cases.length * 0.02);
		});
	}
});
