// 録画スクリプト（promo.ts・demo-shinjuku.ts）で共通に使う道具。
// Playwright で画面を操作して録画（webm）し、ffmpeg で MP4 にする。
// 字幕・カーソル・タイトルはページに差し込む一時的な要素で、アプリには入っていない。
// 地図は開発サーバーだけが出す window.__map で操作するので、先に `pnpm dev` を立ち上げておく。
//
//   PROMO_URL   録画するページ（既定 http://localhost:5173/）
//   CHROME_PATH Chromium の実行ファイル（既定は Playwright が入れたもの）
//   FFMPEG      ffmpeg の実行ファイル（既定 ffmpeg）
import { chromium, type Page } from 'playwright-core';
import { mkdirSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';

export const URL = process.env.PROMO_URL ?? 'http://localhost:5173/';
const CHROME = process.env.CHROME_PATH || undefined;
const FFMPEG = process.env.FFMPEG ?? 'ffmpeg';
export const W = 1920, H = 1080;

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function overlay(page: Page) {
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

export async function caption(page: Page, main: string | null, sub = '') {
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

export async function title(page: Page, lines: { h1?: string; p?: string[]; tag?: string } | null) {
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
export async function px(page: Page, lonlat: readonly [number, number]) {
	return page.evaluate((ll) => {
		const m = (window as unknown as { __map: { project(ll: [number, number]): { x: number; y: number }; getContainer(): HTMLElement } }).__map;
		const p = m.project(ll as [number, number]);
		const r = m.getContainer().getBoundingClientRect();
		return { x: r.left + p.x, y: r.top + p.y };
	}, lonlat as [number, number]);
}

export async function glide(page: Page, x: number, y: number, steps = 40) {
	await page.mouse.move(x, y, { steps });
}

export async function clickAt(page: Page, x: number, y: number) {
	await glide(page, x, y);
	await sleep(250);
	await page.mouse.down();
	await sleep(90);
	await page.mouse.up();
}

export async function clickButton(page: Page, name: string) {
	const b = page.getByRole('button', { name, exact: true });
	const box = (await b.boundingBox())!;
	await clickAt(page, box.x + box.width / 2, box.y + box.height / 2);
}

/** 経路の所要時間・距離（例「6分・484 m」）。出なければ画面を debug.png に残して止める */
export async function routeSummary(page: Page, out: string) {
	try {
		await page.locator('aside section').first().waitFor({ timeout: 4000 });
	} catch (e) {
		await page.screenshot({ path: join(out, 'debug.png') });
		console.error('ASIDE:', (await page.locator('aside').innerText()).slice(0, 600));
		throw e;
	}
	const t = await page.locator('aside section').first().innerText();
	const [time, dist] = t.split('\n').map((s) => s.trim()).filter(Boolean);
	return `${time}・${dist}`;
}

/** ダークモード・1920x1080 で録画し、mark() から終わりまでを H.264 / 30 fps の MP4 にする */
export async function record(out: string, name: string, scenes: (page: Page, mark: () => void) => Promise<void>) {
	mkdirSync(out, { recursive: true });
	const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--use-angle=d3d11', '--enable-gpu-rasterization', '--ignore-gpu-blocklist'] });
	const context = await browser.newContext({
		viewport: { width: W, height: H },
		colorScheme: 'dark',
		locale: 'ja-JP',
		recordVideo: { dir: join(out, 'raw'), size: { width: W, height: H } }
	});
	const t0 = Date.now();
	let tStart = 0;
	await context.addInitScript(() => localStorage.setItem('hokonavi-theme', 'dark'));
	const page = await context.newPage();
	await scenes(page, () => (tStart = Date.now() - t0));
	const tEnd = Date.now() - t0;

	const video = page.video();
	await context.close();
	await browser.close();
	const raw = await video!.path();

	// 読み込み待ちの頭を切る
	const mp4 = join(out, name);
	const r = spawnSync(
		FFMPEG,
		['-y', '-ss', String(tStart / 1000), '-to', String(tEnd / 1000), '-i', raw,
			'-c:v', 'libx264', '-crf', '18', '-preset', 'slow', '-pix_fmt', 'yuv420p', '-r', '30', '-an', '-movflags', '+faststart', mp4],
		{ stdio: 'inherit' }
	);
	if (r.status !== 0) throw new Error(`ffmpeg に失敗しました（録画は ${raw} に残っています）`);
	rmSync(join(out, 'raw'), { recursive: true, force: true });
	console.log(mp4);
}
