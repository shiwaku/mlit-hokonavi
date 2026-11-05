// ライト / ダークの切り替え。<html data-theme="…"> で画面（Tailwind の dark:）と地図の配色を切り替える。
// 初回は OS の設定に合わせ、切り替えたらブラウザに覚えておく（保存できない環境でも動く）
export type Theme = 'light' | 'dark';

export const THEME_KEY = 'hokonavi-theme';

function systemPref(): Theme {
	return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function initialTheme(): Theme {
	try {
		const saved = localStorage.getItem(THEME_KEY);
		if (saved === 'light' || saved === 'dark') return saved;
	} catch {
		// プライベートウィンドウなどで読めないときは OS の設定に従う
	}
	return systemPref();
}

export function applyTheme(theme: Theme): void {
	document.documentElement.dataset.theme = theme;
	try {
		localStorage.setItem(THEME_KEY, theme);
	} catch {
		// 保存できなくても、開いている間は切り替わる
	}
}
