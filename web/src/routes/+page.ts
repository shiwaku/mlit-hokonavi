// MapLibre は window を前提にするので、このページは SSR しない
export const ssr = false;
// GitHub Pages 用に index.html を書き出す（中身は空のシェルで、描画はブラウザで行う）
export const prerender = true;
