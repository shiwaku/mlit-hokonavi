// 紹介動画の録画（ダークモード・1920x1080）。録画の道具は promo-kit.ts。
// 地図は開発サーバーだけが出す window.__map で操作するので、先に `pnpm dev` を立ち上げておく。
//
//   pnpm promo                 → promo/hokonavi-promo-dark.mp4
//   環境変数は promo-kit.ts を参照
import { resolve } from 'node:path';
import { caption, clickAt, clickButton, glide, overlay, px, record, routeSummary, sleep, title, URL } from './promo-kit.ts';

const OUT = resolve(process.argv[2] ?? 'promo');

const SHINJUKU = { from: [139.6949, 35.68806], to: [139.70054, 35.65998] } as const;
const TAKAMATSU = { from: [134.04792, 34.35137], to: [134.05204, 34.33068] } as const;

record(OUT, 'hokonavi-promo-dark.mp4', async (page, mark) => {
	await page.goto(URL);
	await page.waitForFunction(() => (window as unknown as { __map?: unknown }).__map && document.body.innerText.includes('7,921リンク'));
	await page.waitForFunction(() => (window as unknown as { __map: { areTilesLoaded(): boolean } }).__map.areTilesLoaded());
	await overlay(page);
	await sleep(1500);

	// ---- タイトル
	mark();
	await title(page, { tag: 'ほこナビDP × 歩行者経路探索', h1: '歩行者経路探索', p: ['車いす・ベビーカー・視覚障害の方に', 'やさしい道を、ブラウザだけで探す'] });
	await sleep(3800);
	await title(page, null);
	await sleep(900);

	// ---- 新宿・渋谷：経路
	await caption(page, '出発地と目的地を、地図でクリックするだけ', '東京都 新宿駅〜渋谷駅（歩行空間ネットワークデータ 7,921 本）');
	const o = await px(page, SHINJUKU.from), d = await px(page, SHINJUKU.to);
	await clickAt(page, o.x, o.y);
	await sleep(700);
	await clickAt(page, d.x, d.y);
	await sleep(1800);
	await caption(page, `一般：${await routeSummary(page, OUT)}`, '階段や地下通路も使う最短の道');
	await sleep(3200);

	await clickButton(page, '車いす');
	await sleep(900);
	await caption(page, `車いす：${await routeSummary(page, OUT)}`, '階段・エスカレーター・5cm を超える段差・急な勾配を避ける');
	await sleep(3600);

	await clickButton(page, 'ベビーカー');
	await sleep(900);
	await caption(page, `ベビーカー：${await routeSummary(page, OUT)}`, '階段・10cm を超える段差を避ける');
	await sleep(3000);

	await clickButton(page, '視覚障害');
	await sleep(900);
	await caption(page, `視覚障害：${await routeSummary(page, OUT)}`, '誘導用ブロック・音響信号のある道を優先');
	await sleep(3200);

	// ---- 案内（ステップにマウスを乗せると区間が光る）
	await clickButton(page, '車いす');
	await sleep(600);
	await caption(page, '曲がり角・横断歩道・エレベーターごとの案内', 'マウスを乗せた区間を地図で強調');
	const items = page.locator('aside ol li button');
	const n = Math.min(await items.count(), 7);
	for (let i = 0; i < n; i++) {
		const box = (await items.nth(i).boundingBox())!;
		await glide(page, box.x + 120, box.y + box.height / 2, 14);
		await sleep(650);
	}
	await sleep(600);

	// ---- 全地区
	await caption(page, null);
	await clickButton(page, '全地区を表示');
	await sleep(1200);
	await caption(page, '全国 21 エリアを収録', '国土交通省 ほこナビDP の歩行空間ネットワークデータ全 28 地区');
	await sleep(3000);

	// ---- 高松へ
	const areas = await page.evaluate(() => fetch(new URL('data/areas.json', document.baseURI)).then((r) => r.json()));
	const tk = areas.areas.find((a: { case: string }) => a.case === 'takamatsu');
	const c = await px(page, [(tk.bbox[0] + tk.bbox[2]) / 2, (tk.bbox[1] + tk.bbox[3]) / 2]);
	await clickAt(page, c.x, c.y);
	await sleep(400);
	await caption(page, '香川県高松市へ', '地区を選ぶと、その地区のデータだけを読み込む');
	await page.waitForFunction(() => document.body.innerText.includes('2,720リンク'));
	await sleep(2200);
	await clickButton(page, '一般');
	const to = await px(page, TAKAMATSU.from), td = await px(page, TAKAMATSU.to);
	await clickAt(page, to.x, to.y);
	await sleep(600);
	await clickAt(page, td.x, td.y);
	await sleep(1500);
	await caption(page, `高松港 → 栗林公園方面：${await routeSummary(page, OUT)}`, '探索は数ミリ秒。サーバーは使わない');
	await sleep(3600);

	// ---- 締め
	await caption(page, null);
	await title(page, {
		tag: 'GitHub Pages で公開中',
		h1: 'shiwaku.github.io/mlit-hokonavi',
		p: ['出典：国土交通省「歩行空間ネットワークデータ」（ほこナビDP）', '公共データ利用規約（第1.0版）に基づき加工して作成']
	});
	await sleep(4500);
}).catch((e) => {
	console.error(e);
	process.exit(1);
});
