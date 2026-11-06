// 紹介動画の録画（ダークモード・1920x1080）。Playwright で画面を操作して録画（webm）し、ffmpeg で MP4 にする。
// 字幕・カーソル・タイトルはページに差し込む一時的な要素で、アプリには入っていない。
// 地図は開発サーバーだけが出す window.__map で操作するので、先に `pnpm dev` を立ち上げておく。
//
//   pnpm promo                 → promo/hokonavi-promo-dark.mp4
//   PROMO_URL   録画するページ（既定 http://localhost:5173/）
//   CHROME_PATH Chromium の実行ファイル（既定は Playwright が入れたもの）
//   FFMPEG      ffmpeg の実行ファイル（既定 ffmpeg）
import { chromium, type Page } from 'playwright-core';
import { mkdirSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { join, resolve } from 'node:path';

const URL = process.env.PROMO_URL ?? 'http://localhost:5173/';
const OUT = resolve(process.argv[2] ?? 'promo');
const CHROME = process.env.CHROME_PATH || undefined;
const FFMPEG = process.env.FFMPEG ?? 'ffmpeg';
const W = 1920, H = 1080;

const SHINJUKU = { from: [139.6949, 35.68806], to: [139.70054, 35.65998] } as const;
const TAKAMATSU = { from: [134.04792, 34.35137], to: [134.05204, 34.33068] } as const;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function overlay(page: Page) {
	await page.evaluate(() => {
		const css = document.createElement('style');
		css.textContent = `
		#promo-cursor{position:fixed;z-index:9999;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;
			background:rgba(255,255,255,.9);box-shadow:0 0 0 3px rgba(59,130,246,.7),0 2px 10px rgba(0,0,0,.6);pointer-events:none;
			transition:transform .12s}
		#promo-cursor.down{transform:scale(.7)}
		#promo-cap{position:fixed;z-index:9998;left:calc(384px + (100vw - 384px)/2);top:28px;transform:translateX(-50%);
			padding:14px 28px;border-radius:14px;background:rgba(15,23,42,.82);color:#f1f5f9;backdrop-filter:blur(6px);
			font:600 30px/1.4 "Noto Sans JP","Yu Gothic UI","Meiryo",sans-serif;letter-spacing:.02em;white-space:nowrap;
			box-shadow:0 10px 30px rgba(0,0,0,.5);border:1px solid rgba(148,163,184,.25);opacity:0;transition:opacity .45s;pointer-events:none}
		#promo-cap small{display:block;font-size:20px;font-weight:400;color:#cbd5e1}
		#promo-title{position:fixed;inset:0;z-index:9997;display:flex;flex-direction:column;align-items:center;justify-content:center;
			gap:18px;background:rgba(2,6,23,.72);backdrop-filter:blur(10px);color:#f8fafc;text-align:center;opacity:0;transition:opacity .8s;pointer-events:none;
			font-family:"Noto Sans JP","Yu Gothic UI","Meiryo",sans-serif}
		#promo-title h1{font-size:76px;font-weight:800;margin:0;letter-spacing:.04em}
		#promo-title p{font-size:30px;margin:0;color:#cbd5e1;line-height:1.6}
		#promo-title .tag{font-size:24px;color:#93c5fd;letter-spacing:.06em}`;
		document.head.append(css);
		const cur = Object.assign(document.createElement('div'), { id: 'promo-cursor' });
		const cap = Object.assign(document.createElement('div'), { id: 'promo-cap' });
		const title = Object.assign(document.createElement('div'), { id: 'promo-title' });
		document.body.append(cur, cap, title);
		cur.style.left = '1200px';
		cur.style.top = '540px';
		addEventListener('mousemove', (e) => ((cur.style.left = `${e.clientX}px`), (cur.style.top = `${e.clientY}px`)), true);
		addEventListener('mousedown', () => cur.classList.add('down'), true);
		addEventListener('mouseup', () => cur.classList.remove('down'), true);
	});
}

async function caption(page: Page, main: string | null, sub = '') {
	await page.evaluate(
		([m, s]) => {
			const cap = document.getElementById('promo-cap')!;
			if (!m) return void (cap.style.opacity = '0');
			cap.innerHTML = '';
			cap.append(m);
			if (s) {
				const sm = document.createElement('small');
				sm.textContent = s;
				cap.append(sm);
			}
			cap.style.opacity = '1';
		},
		[main, sub] as const
	);
}

async function title(page: Page, lines: { h1?: string; p?: string[]; tag?: string } | null) {
	await page.evaluate((l) => {
		const t = document.getElementById('promo-title')!;
		if (!l) return void (t.style.opacity = '0');
		t.innerHTML = '';
		if (l.tag) t.append(Object.assign(document.createElement('div'), { className: 'tag', textContent: l.tag }));
		if (l.h1) t.append(Object.assign(document.createElement('h1'), { textContent: l.h1 }));
		for (const p of l.p ?? []) t.append(Object.assign(document.createElement('p'), { textContent: p }));
		t.style.opacity = '1';
	}, lines);
}

/** 経緯度 → 画面上の位置（地図の要素の位置を足す） */
async function px(page: Page, lonlat: readonly [number, number]) {
	return page.evaluate((ll) => {
		const m = (window as unknown as { __map: { project(ll: [number, number]): { x: number; y: number }; getContainer(): HTMLElement } }).__map;
		const p = m.project(ll as [number, number]);
		const r = m.getContainer().getBoundingClientRect();
		return { x: r.left + p.x, y: r.top + p.y };
	}, lonlat as [number, number]);
}

async function glide(page: Page, x: number, y: number, steps = 40) {
	await page.mouse.move(x, y, { steps });
}

async function clickAt(page: Page, x: number, y: number) {
	await glide(page, x, y);
	await sleep(250);
	await page.mouse.down();
	await sleep(90);
	await page.mouse.up();
}

async function clickButton(page: Page, name: string) {
	const b = page.getByRole('button', { name, exact: true });
	const box = (await b.boundingBox())!;
	await clickAt(page, box.x + box.width / 2, box.y + box.height / 2);
}

async function routeSummary(page: Page) {
	try {
		await page.locator('aside section').first().waitFor({ timeout: 4000 });
	} catch (e) {
		await page.screenshot({ path: join(OUT, 'debug.png') });
		console.error('ASIDE:', (await page.locator('aside').innerText()).slice(0, 600));
		throw e;
	}
	const t = await page.locator('aside section').first().innerText();
	const [time, dist] = t.split('\n').map((s) => s.trim()).filter(Boolean);
	return `${time}・${dist}`;
}

async function main() {
	mkdirSync(OUT, { recursive: true });
	const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--use-angle=d3d11', '--enable-gpu-rasterization', '--ignore-gpu-blocklist'] });
	const context = await browser.newContext({
		viewport: { width: W, height: H },
		colorScheme: 'dark',
		locale: 'ja-JP',
		recordVideo: { dir: join(OUT, 'raw'), size: { width: W, height: H } }
	});
	const t0 = Date.now();
	await context.addInitScript(() => localStorage.setItem('hokonavi-theme', 'dark'));
	const page = await context.newPage();
	await page.goto(URL);
	await page.waitForFunction(() => (window as unknown as { __map?: unknown }).__map && document.body.innerText.includes('7,921リンク'));
	await page.waitForFunction(() => (window as unknown as { __map: { areTilesLoaded(): boolean } }).__map.areTilesLoaded());
	await overlay(page);
	await sleep(1500);

	// ---- タイトル
	const tStart = Date.now() - t0;
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
	await caption(page, `一般：${await routeSummary(page)}`, '階段や地下通路も使う最短の道');
	await sleep(3200);

	await clickButton(page, '車いす');
	await sleep(900);
	await caption(page, `車いす：${await routeSummary(page)}`, '階段・エスカレーター・5cm を超える段差・急な勾配を避ける');
	await sleep(3600);

	await clickButton(page, 'ベビーカー');
	await sleep(900);
	await caption(page, `ベビーカー：${await routeSummary(page)}`, '階段・10cm を超える段差を避ける');
	await sleep(3000);

	await clickButton(page, '視覚障害');
	await sleep(900);
	await caption(page, `視覚障害：${await routeSummary(page)}`, '誘導用ブロック・音響信号のある道を優先');
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
	await caption(page, `高松港 → 栗林公園方面：${await routeSummary(page)}`, '探索は数ミリ秒。サーバーは使わない');
	await sleep(3600);

	// ---- 締め
	await caption(page, null);
	await title(page, {
		tag: 'GitHub Pages で公開中',
		h1: 'shiwaku.github.io/mlit-hokonavi',
		p: ['出典：国土交通省「歩行空間ネットワークデータ」（ほこナビDP）', '公共データ利用規約（第1.0版）に基づき加工して作成']
	});
	await sleep(4500);
	const tEnd = Date.now() - t0;

	const video = page.video();
	await context.close();
	await browser.close();
	const raw = await video!.path();

	// 読み込み待ちの頭を切り、タイトルから締めまでを H.264 / 30 fps にする
	const mp4 = join(OUT, 'hokonavi-promo-dark.mp4');
	const r = spawnSync(
		FFMPEG,
		['-y', '-ss', String(tStart / 1000), '-to', String(tEnd / 1000), '-i', raw,
			'-c:v', 'libx264', '-crf', '18', '-preset', 'slow', '-pix_fmt', 'yuv420p', '-r', '30', '-an', '-movflags', '+faststart', mp4],
		{ stdio: 'inherit' }
	);
	if (r.status !== 0) throw new Error(`ffmpeg に失敗しました（録画は ${raw} に残っています）`);
	rmSync(join(OUT, 'raw'), { recursive: true, force: true });
	console.log(mp4);
}

main().catch((e) => {
	console.error(e);
	process.exit(1);
});
