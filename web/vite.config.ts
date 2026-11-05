import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Runes 記法を強制
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// GitHub Pages に置く静的サイト（SPA）。未知のパスは 404.html（+page.ts で ssr = false）
			adapter: adapter({ fallback: '404.html' }),
			// GitHub Pages ではサイトが /<リポジトリ名>/ の下になる。デプロイ時に BASE_PATH=/mlit-hokonavi を渡す
			paths: { base: (process.env.BASE_PATH ?? '') as '' | `/${string}` }
		})
	]
});
