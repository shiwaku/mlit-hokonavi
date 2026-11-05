<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import * as maplibregl from 'maplibre-gl';
	import type { ExpressionSpecification, GeoJSONSource, MapMouseEvent } from 'maplibre-gl';
	import type * as GeoJSON from 'geojson';
	import { Protocol } from 'pmtiles';
	// MapLibre v6 は既定で import.meta.url からの相対パスで Worker を読むが、Vite でまとめると
	// その場所にファイルが無くなり「Worker failed to load」になる。Worker（依存なしの 1 ファイル）を
	// アセットとして出力させ、その URL を渡す
	import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
	import { asset } from '$app/paths';
	import { formatDuration, type LinkProps, type ProfileKey, type Route } from '#lib/api.ts';
	import { findRoute, getAreas, loadArea, type Area } from '#lib/data.ts';
	import { floorLabel } from '#lib/routing/guidance.ts';

	// 地図の色。アクセス区分（0 通れる / 1 不明 / 2 片方向のみ / 3 通れない）
	const ACCESS_COLOR = ['#64748b', '#f59e0b', '#8b5cf6', '#ef4444'];
	const ROUTE_COLOR = '#2563eb';
	const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };

	let mapEl: HTMLDivElement;
	let map: maplibregl.Map | undefined;
	// 探索はブラウザ内（GitHub Pages にはサーバーが無い）。area.router が graph.json から作った探索グラフ
	let area = $state.raw<Area | null>(null);
	let loadError = $state<string | null>(null);
	const profiles = $derived(area?.profiles ?? []);
	let profile = $state<ProfileKey>('walk');
	let floors = $state<number[]>([]);
	let viewFloor = $state<'all' | number>('all');
	let origin = $state<[number, number] | null>(null);
	let dest = $state<[number, number] | null>(null);
	let fromFloor = $state<number | null>(null);
	let toFloor = $state<number | null>(null);
	let route = $state<Route | null>(null);
	let error = $state<string | null>(null);
	let activeStep = $state<number | null>(null);
	let ready = $state(false); // 地図のレイヤーを足し終えたか（map 自体は $state にしない）

	let originMarker: maplibregl.Marker | undefined;
	let destMarker: maplibregl.Marker | undefined;
	const protocol = new Protocol();

	function marker(color: string, label: string, onMove: (p: [number, number]) => void) {
		const el = document.createElement('div');
		el.className =
			'grid h-7 w-7 place-items-center rounded-full border-2 border-white text-xs font-bold text-white shadow';
		el.style.background = color;
		el.textContent = label;
		const m = new maplibregl.Marker({ element: el, draggable: true });
		m.on('dragend', () => {
			const ll = m.getLngLat();
			onMove([ll.lng, ll.lat]);
		});
		return m;
	}

	function setSource(id: string, data: GeoJSON.FeatureCollection | GeoJSON.Feature) {
		(map?.getSource(id) as GeoJSONSource | undefined)?.setData(data);
	}

	function search() {
		if (!origin || !dest || !area) return;
		error = null;
		activeStep = null;
		try {
			route = findRoute(area, { from: origin, to: dest, profile, fromFloor, toFloor });
		} catch (e) {
			error = (e as Error).message;
			route = null;
		}
	}

	function clearAll() {
		origin = dest = null;
		route = null;
		error = null;
		activeStep = null;
	}

	function swap() {
		[origin, dest] = [dest, origin];
		[fromFloor, toFloor] = [toFloor, fromFloor];
		search();
	}

	function onMapClick(e: MapMouseEvent) {
		const p: [number, number] = [e.lngLat.lng, e.lngLat.lat];
		if (!origin) origin = p;
		else dest = p;
		search();
	}

	// ---- 地図の状態を Svelte の状態に合わせる
	$effect(() => {
		if (!ready || !map) return;
		if (origin) {
			originMarker ??= marker('#16a34a', '発', (p) => ((origin = p), search()));
			originMarker.setLngLat(origin).addTo(map);
		} else originMarker?.remove();
		if (dest) {
			destMarker ??= marker('#dc2626', '着', (p) => ((dest = p), search()));
			destMarker.setLngLat(dest).addTo(map);
		} else destMarker?.remove();
	});

	$effect(() => {
		if (!ready || !map) return;
		const acc: ExpressionSpecification = ['get', `acc_${profile}`];
		map.setPaintProperty('network', 'line-color', ['match', acc, 1, ACCESS_COLOR[1], 2, ACCESS_COLOR[2], 3, ACCESS_COLOR[3], ACCESS_COLOR[0]]);
		map.setPaintProperty('network', 'line-dasharray', ['case', ['==', acc, 3], ['literal', [1, 1]], ['literal', [1, 0]]]);
		const onFloor: ExpressionSpecification | boolean =
			viewFloor === 'all'
				? true
				: ['any', ['==', ['get', 'floor'], viewFloor], ['==', ['get', 'floor_end'], viewFloor]];
		map.setPaintProperty('network', 'line-opacity', ['case', onFloor, 0.9, 0.12]);
	});

	$effect(() => {
		if (!ready || !map) return;
		const r = route;
		setSource('route', r?.geometry ? { type: 'Feature', geometry: r.geometry, properties: {} } : EMPTY);
		const snaps: GeoJSON.Feature[] = [];
		for (const [click, s] of [
			[origin, r?.snap.origin],
			[dest, r?.snap.dest]
		] as const) {
			if (click && s)
				snaps.push({
					type: 'Feature',
					properties: {},
					geometry: { type: 'LineString', coordinates: [click, [s.lon, s.lat]] }
				});
		}
		setSource('snap', { type: 'FeatureCollection', features: snaps });
	});

	$effect(() => {
		if (!ready || !map) return;
		const r = route;
		const i = activeStep;
		if (!r?.geometry || i == null) return setSource('step', EMPTY);
		const s = r.steps[i];
		const coords = r.geometry.coordinates.slice(s.coord_start, s.coord_end + 1);
		setSource('step', {
			type: 'Feature',
			properties: {},
			geometry: coords.length > 1 ? { type: 'LineString', coordinates: coords } : { type: 'Point', coordinates: coords[0] }
		});
	});

	function focusStep(i: number) {
		activeStep = i;
		const r = route;
		if (!map || !r?.geometry) return;
		const s = r.steps[i];
		const coords = r.geometry.coordinates.slice(s.coord_start, s.coord_end + 1) as [number, number][];
		const b = coords.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]));
		map.fitBounds(b, { padding: 120, maxZoom: 19, duration: 400 });
	}

	function linkPopupHtml(p: LinkProps): string {
		const rows: [string, string | null][] = [
			['種類', p.label],
			['距離', `${p.distance} m`],
			['階', p.floor === p.floor_end ? floorLabel(p.floor) : `${floorLabel(p.floor)} → ${floorLabel(p.floor_end)}`],
			['幅員', p.width],
			['縦断勾配', p.slope],
			['段差', p.lev_diff],
			['信号', p.signal],
			['誘導用ブロック', p.brail_tile],
			['エレベーター', p.kind === 'elevator' ? p.elevator : null],
			['方向', p.direction === 1 ? null : p.direction === 2 ? '起点→終点のみ' : p.direction === 3 ? '終点→起点のみ' : '不明']
		];
		return (
			'<table class="text-xs">' +
			rows
				.filter(([, v]) => v != null)
				.map(([k, v]) => `<tr><th class="pr-2 text-left font-normal text-slate-500">${k}</th><td>${v}</td></tr>`)
				.join('') +
			'</table>'
		);
	}

	onMount(async () => {
		maplibregl.setWorkerUrl(workerUrl);
		maplibregl.addProtocol('pmtiles', protocol.tile);
		map = new maplibregl.Map({
			container: mapEl,
			// GitHub Pages ではサイトが /<リポジトリ名>/ の下に置かれるので、静的ファイルは asset() で解決する
			style: new URL(asset('styles/pale.json'), location.href).href,
			center: [139.7, 35.69],
			zoom: 15,
			attributionControl: { compact: true }
		});
		// load は 1 回しか来ない。データを待っている間に来ても取りこぼさないよう、先に待ち受ける
		const mapLoaded = new Promise((r) => map!.once('load', r));
		map.addControl(new maplibregl.NavigationControl(), 'top-right');
		map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');

		let a: Area | null = null;
		try {
			const list = await getAreas();
			a = await loadArea(list.default);
		} catch (e) {
			loadError = (e as Error).message;
		}
		area = a;
		floors = a?.floors ?? [];
		const net = a?.network ?? null;
		await mapLoaded;
		const m = map;

		const src = a?.source;
		m.addSource('network', {
			type: 'geojson',
			data: net ?? EMPTY,
			// 公共データ利用規約（第1.0版）の出典表示。加工している旨も書く
			attribution: src
				? `出典：${src.publisher ?? ''}「<a href="${src.url}" target="_blank" rel="noopener">${src.title}</a>」（<a href="${src.license_url}" target="_blank" rel="noopener">${src.license}</a>）を加工して作成`
				: undefined
		});
		m.addSource('route', { type: 'geojson', data: EMPTY });
		m.addSource('snap', { type: 'geojson', data: EMPTY });
		m.addSource('step', { type: 'geojson', data: EMPTY });
		m.addLayer({
			id: 'network',
			type: 'line',
			source: 'network',
			paint: { 'line-color': ACCESS_COLOR[0], 'line-width': ['interpolate', ['linear'], ['zoom'], 14, 1, 18, 4] }
		});
		m.addLayer({
			id: 'route-casing',
			type: 'line',
			source: 'route',
			layout: { 'line-cap': 'round', 'line-join': 'round' },
			paint: { 'line-color': '#fff', 'line-width': ['interpolate', ['linear'], ['zoom'], 14, 6, 18, 14] }
		});
		m.addLayer({
			id: 'route',
			type: 'line',
			source: 'route',
			layout: { 'line-cap': 'round', 'line-join': 'round' },
			paint: { 'line-color': ROUTE_COLOR, 'line-width': ['interpolate', ['linear'], ['zoom'], 14, 3, 18, 9] }
		});
		m.addLayer({
			id: 'snap',
			type: 'line',
			source: 'snap',
			paint: { 'line-color': '#334155', 'line-width': 1.5, 'line-dasharray': [2, 2] }
		});
		m.addLayer({
			id: 'step-line',
			type: 'line',
			source: 'step',
			filter: ['==', ['geometry-type'], 'LineString'],
			layout: { 'line-cap': 'round' },
			paint: { 'line-color': '#f97316', 'line-width': ['interpolate', ['linear'], ['zoom'], 14, 5, 18, 12] }
		});
		m.addLayer({
			id: 'step-point',
			type: 'circle',
			source: 'step',
			filter: ['==', ['geometry-type'], 'Point'],
			paint: { 'circle-color': '#f97316', 'circle-radius': 8, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 }
		});

		if (net && net.features.length) {
			const b = new maplibregl.LngLatBounds();
			for (const f of net.features) for (const c of f.geometry.coordinates) b.extend(c as [number, number]);
			m.fitBounds(b, { padding: 40, duration: 0 });
		}

		const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, maxWidth: '280px' });
		m.on('mousemove', 'network', (e: maplibregl.MapLayerMouseEvent) => {
			const f = e.features?.[0];
			if (!f) return;
			m.getCanvas().style.cursor = 'pointer';
			popup.setLngLat(e.lngLat).setHTML(linkPopupHtml(f.properties as LinkProps)).addTo(m);
		});
		m.on('mouseleave', 'network', () => {
			m.getCanvas().style.cursor = '';
			popup.remove();
		});
		m.on('click', onMapClick);
		ready = true;
	});

	onDestroy(() => {
		map?.remove();
		maplibregl.removeProtocol('pmtiles');
	});

	const currentProfile = $derived(profiles.find((p) => p.key === profile));
</script>

<div class="flex h-dvh w-full flex-col md:flex-row">
	<aside class="order-2 flex max-h-[50dvh] w-full flex-col overflow-hidden border-slate-200 bg-white md:order-1 md:max-h-none md:w-96 md:border-r">
		<header class="border-b border-slate-200 px-4 py-3">
			<h1 class="text-base font-bold text-slate-800">歩行者経路探索</h1>
			<p class="text-xs text-slate-500">
				{area?.source?.title ?? '歩行空間ネットワークデータ'}（{area?.links.toLocaleString() ?? '–'}リンク{area?.source?.data_updated
					? `・${area.source.data_updated} 更新`
					: ''}）
			</p>
		</header>

		<div class="space-y-3 overflow-y-auto px-4 py-3">
			{#if loadError}
				<p class="rounded bg-red-50 p-2 text-sm text-red-700">データを読めません: {loadError}</p>
			{/if}

			<fieldset>
				<legend class="mb-1 text-xs font-semibold text-slate-600">利用者</legend>
				<div class="grid grid-cols-4 gap-1">
					{#each profiles as p (p.key)}
						<button
							type="button"
							class="rounded border px-1 py-1.5 text-sm {profile === p.key
								? 'border-blue-600 bg-blue-600 text-white'
								: 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50'}"
							onclick={() => ((profile = p.key), search())}>{p.label}</button
						>
					{/each}
				</div>
				{#if currentProfile}
					<p class="mt-1 text-xs text-slate-500">{currentProfile.description}</p>
				{/if}
			</fieldset>

			<div class="grid grid-cols-[auto_1fr_auto] items-center gap-x-2 gap-y-1 text-sm">
				<span class="grid h-5 w-5 place-items-center rounded-full bg-green-600 text-[10px] font-bold text-white">発</span>
				<span class="truncate text-slate-700">{origin ? `${origin[1].toFixed(5)}, ${origin[0].toFixed(5)}` : '地図をクリック'}</span>
				<select class="rounded border border-slate-300 px-1 py-0.5 text-xs" bind:value={fromFloor} onchange={search} aria-label="出発地の階">
					<option value={null}>階の指定なし</option>
					{#each floors as f (f)}<option value={f}>{floorLabel(f)}</option>{/each}
				</select>
				<span class="grid h-5 w-5 place-items-center rounded-full bg-red-600 text-[10px] font-bold text-white">着</span>
				<span class="truncate text-slate-700">{dest ? `${dest[1].toFixed(5)}, ${dest[0].toFixed(5)}` : origin ? '地図をクリック' : '–'}</span>
				<select class="rounded border border-slate-300 px-1 py-0.5 text-xs" bind:value={toFloor} onchange={search} aria-label="目的地の階">
					<option value={null}>階の指定なし</option>
					{#each floors as f (f)}<option value={f}>{floorLabel(f)}</option>{/each}
				</select>
			</div>
			<div class="flex gap-2">
				<button type="button" class="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-40" disabled={!origin || !dest} onclick={swap}>入れ替え</button>
				<button type="button" class="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50" onclick={clearAll}>クリア</button>
			</div>

			{#if error}
				<p class="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>
			{/if}

			{#if route?.unreachable_reason}
				<p class="rounded bg-amber-50 p-2 text-sm text-amber-800">{route.unreachable_reason}</p>
			{:else if route?.summary}
				{@const s = route.summary}
				<section class="rounded border border-slate-200 p-3">
					<div class="flex items-baseline gap-3">
						<span class="text-2xl font-bold text-slate-800">{formatDuration(s.time_s)}</span>
						<span class="text-sm text-slate-600">{Math.round(s.dist_m).toLocaleString()} m</span>
						<span class="ml-auto text-[10px] text-slate-400">{route.elapsed_ms} ms</span>
					</div>
					<p class="mt-1 text-xs text-slate-600">
						階段 {s.stairs}・エレベーター {s.elevator}・エスカレーター {s.escalator}・横断歩道 {s.crosswalk}・横断 {s.crossing}
					</p>
					{#each route.warnings as w (w)}
						<p class="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-800">{w}</p>
					{/each}
				</section>

				<ol class="divide-y divide-slate-100">
					{#each route.steps as st, i (i)}
						<li>
							<button
								type="button"
								class="flex w-full gap-2 px-1 py-2 text-left text-sm {activeStep === i ? 'bg-orange-50' : 'hover:bg-slate-50'}"
								onmouseenter={() => (activeStep = i)}
								onclick={() => focusStep(i)}
							>
								<span class="w-5 shrink-0 text-right text-xs text-slate-400">{i + 1}</span>
								<span class="flex-1 text-slate-800">{st.instruction}</span>
								{#if st.floor_from != null && st.floor_from !== 0}
									<span class="shrink-0 text-xs text-slate-500">{floorLabel(st.floor_from)}</span>
								{/if}
							</button>
						</li>
					{/each}
				</ol>
			{/if}

			<fieldset class="border-t border-slate-200 pt-3">
				<legend class="mb-1 text-xs font-semibold text-slate-600">表示する階</legend>
				<select class="w-full rounded border border-slate-300 px-2 py-1 text-sm" bind:value={viewFloor}>
					<option value="all">すべて</option>
					{#each floors as f (f)}<option value={f}>{floorLabel(f)}</option>{/each}
				</select>
				<ul class="mt-2 grid grid-cols-2 gap-1 text-xs text-slate-600">
					{#each ['通れる', '属性が不明', '片方向のみ', '通れない'] as label, i (label)}
						<li class="flex items-center gap-1">
							<span class="inline-block h-1 w-5 rounded" style="background:{ACCESS_COLOR[i]}"></span>{label}
						</li>
					{/each}
				</ul>
				<p class="mt-1 text-[10px] text-slate-400">ネットワークの色は、選んだ利用者で通れるかどうか</p>
			</fieldset>
		</div>
	</aside>

	<div class="relative order-1 min-h-0 flex-1 md:order-2" bind:this={mapEl}></div>
</div>
