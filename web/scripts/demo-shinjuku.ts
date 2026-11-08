// デモ動画：新宿駅 西口の地下（地下 1 階）から地上に出て、甲州街道を通って東南口へ（ダークモード・1920x1080・3D）。
// 録画の道具は promo-kit.ts。先に `pnpm dev` を立ち上げておく（window.__map・window.__project3d は開発サーバーだけが出す）。
//
//   pnpm demo:shinjuku         → promo/hokonavi-demo-shinjuku.mp4
//
// 西口の地下から「東口」へ 1 回で探すと地下の通路を通って甲州街道を通らないので、目的地は東南口にしている。
// 出発地・目的地は地図上の線の点（3D の線の位置をクリックする）。データが変わって経路が変わったら、点と字幕を見直す。
import type { Page } from 'playwright-core';
import { resolve } from 'node:path';
import { caption, clickAt, glide, overlay, record, routeSummary, sleep, title, URL } from './promo-kit.ts';

const OUT = resolve(process.argv[2] ?? 'promo');

/** [経度, 緯度, 階] */
const FROM = [139.6985023, 35.6904484, -1] as const; // 西口の地下 1 階の通路
const TO = [139.7016953, 35.6891054, 0] as const; // 東南口の前の歩道

type Cam = { center: [number, number]; zoom: number; pitch: number; bearing: number };
const VIEW_WEST: Cam = { center: [139.6990, 35.6901], zoom: 18.3, pitch: 62, bearing: -15 };
const VIEW_EAST: Cam = { center: [139.7012, 35.6891], zoom: 18.1, pitch: 62, bearing: -15 };
const VIEW_ALL: Cam = { center: [139.7001, 35.6895], zoom: 17.55, pitch: 58, bearing: -20 };
// 経路をたどるカメラ（字幕・視点）。向きはほぼ変えずに寄って動かす（回すと方角が分からなくなる）
const TOUR: { cap: string; sub: string; cam: Cam; ms: number }[] = [
	{ cap: '西口の地下 1 階から', sub: '施設内通路を進む', cam: { center: [139.6987, 35.6903], zoom: 19.3, pitch: 62, bearing: -20 }, ms: 2600 },
	{ cap: '階段を上って地上へ', sub: '屋内地図と歩行空間ネットワークを、同じ地点でつないでいる', cam: { center: [139.6988, 35.6901], zoom: 19.5, pitch: 72, bearing: 5 }, ms: 2600 },
	{ cap: '西口の歩道を南へ', sub: '', cam: { center: [139.6990, 35.6893], zoom: 18.6, pitch: 62, bearing: -20 }, ms: 3000 },
	{ cap: '甲州街道の歩道を東へ', sub: '線路の上を渡る', cam: { center: [139.7001, 35.6887], zoom: 18.4, pitch: 62, bearing: -20 }, ms: 3200 },
	{ cap: '東南口に到着', sub: '', cam: { center: [139.7014, 35.6890], zoom: 18.9, pitch: 62, bearing: -20 }, ms: 2800 }
];

const win = (page: Page) => page.evaluate.bind(page);

/** カメラを動かし、止まってタイルを読み終えるまで待つ */
async function ease(page: Page, cam: Cam, duration: number) {
	await win(page)(
		([c, d]) =>
			new Promise<void>((r) => {
				const m = (window as unknown as { __map: import('maplibre-gl').Map }).__map;
				m.once('moveend', () => r());
				m.easeTo({ ...c, duration: d, essential: true });
			}),
		[cam, duration] as const
	);
	await page.waitForFunction(() => (window as unknown as { __map: import('maplibre-gl').Map }).__map.areTilesLoaded(), null, { timeout: 15000 });
}

/** 3D の線（階の高さ）の点の、画面上の位置 */
async function px3d(page: Page, [lon, lat, floor]: readonly [number, number, number]) {
	const p = await win(page)(
		([x, y, f]) => {
			const w = window as unknown as { __project3d(lon: number, lat: number, floor: number): [number, number] | null; __map: import('maplibre-gl').Map };
			const q = w.__project3d(x, y, f);
			const r = w.__map.getContainer().getBoundingClientRect();
			return q && { x: r.left + q[0], y: r.top + q[1] };
		},
		[lon, lat, floor] as const
	);
	if (!p) throw new Error('3D の表示になっていません');
	return p;
}

const floorSelect = (page: Page, label: string) =>
	label === '表示する階' ? page.locator('fieldset:has(legend:text("表示する階")) select') : page.locator(`[aria-label="${label}"]`);

/** 表示する階を選ぶ（カーソルをプルダウンに動かしてから） */
async function showFloor(page: Page, floor: string) {
	const s = floorSelect(page, '表示する階');
	const box = (await s.boundingBox())!;
	await glide(page, box.x + box.width / 2, box.y + box.height / 2, 30);
	await sleep(300);
	await s.selectOption({ label: floor });
	await sleep(700);
}

async function expectFloor(page: Page, label: string, want: string) {
	const got = await floorSelect(page, label).evaluate((s: HTMLSelectElement) => s.selectedOptions[0].textContent);
	if (got !== want) throw new Error(`${label}が「${got}」になりました（期待：${want}）`);
}

record(OUT, 'hokonavi-demo-shinjuku.mp4', async (page, mark) => {
	const c = VIEW_ALL;
	await page.goto(`${URL}#${c.zoom}/${c.center[1]}/${c.center[0]}/${c.bearing}/${c.pitch}`);
	await page.waitForFunction(() => document.body.innerText.includes('10,376リンク'), null, { timeout: 60000 });
	await page.waitForFunction(() => {
		const w = window as unknown as { __map?: import('maplibre-gl').Map; __project3d?(a: number, b: number, c: number): unknown };
		return w.__map?.areTilesLoaded() && w.__project3d?.(139.7, 35.69, 0);
	});
	// 地下と地上の差が見えるよう、高さの強調を上げる（既定 ×2 → ×4。地下 1 階が 16 m 下に描かれる）
	await page.locator('input[type=range][aria-label="高さの強調"]').fill('4');
	await overlay(page);
	await sleep(1500);

	// ---- タイトル
	mark();
	await title(page, {
		tag: 'ほこナビDP × 屋内地図オープンデータ',
		h1: '新宿駅 西口の地下から東南口へ',
		p: ['地下 1 階の通路から階段で地上に出て、甲州街道を歩く', '屋内と屋外の歩行者ネットワークをつないで、階をまたいで探す']
	});
	await sleep(4200);
	await title(page, null);
	await sleep(900);

	// ---- 出発地：地下 1 階の線をクリック
	await caption(page, '表示する階を「地下1階」に', '駅構内・地下街の通路（屋内地図オープンデータ）');
	await showFloor(page, '地下1階');
	await ease(page, VIEW_WEST, 1800);
	await sleep(600);
	await caption(page, '西口の地下の通路をクリック', '3D の線をクリックすると、その階が出発地になる');
	const o = await px3d(page, FROM);
	await clickAt(page, o.x, o.y);
	await sleep(400);
	await expectFloor(page, '出発地の階', '地下1階');
	await sleep(1800);

	// ---- 目的地：地上の線をクリック
	await caption(page, '表示する階を「地上」に', '歩道・横断歩道（歩行空間ネットワークデータ）');
	await showFloor(page, '地上');
	await ease(page, VIEW_EAST, 1800);
	await sleep(600);
	await caption(page, '東南口の前の歩道をクリック', '');
	const d = await px3d(page, TO);
	await clickAt(page, d.x, d.y);
	await sleep(400);
	await expectFloor(page, '目的地の階', '地上');

	// ---- 結果
	await showFloor(page, 'すべて');
	await ease(page, VIEW_ALL, 1800);
	await caption(page, `一般：${await routeSummary(page, OUT)}`, '地下 1 階 → 階段で地上へ → 甲州街道を東へ');
	await sleep(3800);

	// ---- 案内（ステップにマウスを乗せると区間が光る）
	await caption(page, '曲がり角・階段ごとの案内', 'マウスを乗せた区間を地図で強調');
	const items = page.locator('aside ol li button');
	const n = await items.count();
	for (let i = 0; i < n; i++) {
		const box = (await items.nth(i).boundingBox())!;
		await glide(page, box.x + 120, box.y + box.height / 2, 12);
		await sleep(700);
	}
	await glide(page, 1500, 900, 20);
	await sleep(400);

	// ---- 経路をたどる
	for (const t of TOUR) {
		await caption(page, t.cap, t.sub);
		await ease(page, t.cam, t.ms);
		await sleep(900);
	}

	// ---- 締め
	await caption(page, null);
	await ease(page, VIEW_ALL, 2200);
	await sleep(1200);
	await title(page, {
		tag: 'GitHub Pages で公開中',
		h1: 'shiwaku.github.io/mlit-hokonavi',
		p: [
			'出典：国土交通省「歩行空間ネットワークデータ」（ほこナビDP）・「新宿駅周辺屋内地図オープンデータ」',
			'公共データ利用規約（第1.0版）・政府標準利用規約（第2.0版）に準拠して加工して作成'
		]
	});
	await sleep(4500);
}).catch((e) => {
	console.error(e);
	process.exit(1);
});
