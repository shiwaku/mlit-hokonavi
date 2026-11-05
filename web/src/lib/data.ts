// 静的データ（preprocess/export_static.py が web/static/data/ に書く）の読み込みと、ブラウザ内の探索
import type { Network, Profile, ProfileKey, Route } from './api.ts';
import { buildRoute } from './routing/guidance.ts';
import { Router } from './routing/router.ts';
import { FORMAT, type AreaList, type Source, type StaticGraph } from './routing/types.ts';

export interface Area {
	case: string;
	router: Router;
	network: Network;
	profiles: Profile[];
	title: string;
	sources: Source[];
	floors: number[];
	links: number;
}

// データは生成物で、地区ごとにファイル名が変わる。asset() は static/ にあるファイル名でしか型が通らないので、
// ページの URL（GitHub Pages では /<リポジトリ名>/）からの相対パスで引く。画面は 1 ページだけなのでこれで足りる
async function getJson<T>(path: string): Promise<T> {
	const res = await fetch(new URL(path, document.baseURI));
	if (!res.ok) throw new Error(`${path} を読めません（${res.status}）。preprocess/export_static.py を実行しましたか`);
	return res.json() as Promise<T>;
}

export const getAreas = () => getJson<AreaList>('data/areas.json');

export async function loadArea(c: string): Promise<Area> {
	const [graph, network] = await Promise.all([
		getJson<StaticGraph>(`data/${c}/graph.json`),
		getJson<Network>(`data/${c}/network.geojson`)
	]);
	if (graph.format !== FORMAT) {
		throw new Error(`データの形式が合いません（graph.json ${graph.format} / 画面 ${FORMAT}）。データを作り直してください`);
	}
	const profiles = (Object.keys(graph.profiles) as ProfileKey[]).map((key) => {
		const p = graph.profiles[key];
		return { key, label: p.label, speed_mps: p.speed_mps, description: p.description };
	});
	return {
		case: c,
		router: new Router(graph),
		network,
		profiles,
		title: graph.title,
		sources: graph.sources,
		floors: graph.floors,
		links: graph.links.u.length
	};
}

/** 公共データ利用規約（第1.0版）の出典表示。まとめた地区は元の地区を全部並べ、加工している旨も書く */
export function attributionHtml(sources: Source[]): string {
	if (!sources.length) return '';
	const esc = (t: string) => t.replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
	const titles = sources.map((s) => `「<a href="${s.url}" target="_blank" rel="noopener">${esc(s.title)}</a>」`).join('');
	const pubs = [...new Set(sources.map((s) => s.publisher).filter(Boolean))].join('・');
	const lic = sources[0];
	return `出典：${esc(pubs)}${titles}（<a href="${lic.license_url}" target="_blank" rel="noopener">${esc(lic.license)}</a>）を加工して作成`;
}

export interface RouteQuery {
	from: [number, number]; // [lon, lat]
	to: [number, number];
	profile: ProfileKey;
	fromFloor?: number | null;
	toFloor?: number | null;
}

export function findRoute(area: Area, q: RouteQuery): Route {
	const r = area.router.route(q.from[1], q.from[0], q.to[1], q.to[0], q.profile, q.fromFloor ?? null, q.toFloor ?? null);
	return buildRoute(area.router, r, q.profile);
}
