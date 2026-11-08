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
	import { attributionHtml, findRoute, getAreas, loadArea, type Area } from '#lib/data.ts';
	import { floorLabel } from '#lib/routing/guidance.ts';
	import type { AreaInfo } from '#lib/routing/types.ts';
	import { applyBasemapTheme, basemapPaintTable, OVERLAY } from '#lib/basemap.ts';
	import { applyTheme, initialTheme, type Theme } from '#lib/theme.ts';
	import { createOverlay, FLOOR_M, layers as layers3d, pickNetwork } from '#lib/view3d.ts';

	// 地図の色。アクセス区分（0 通れる / 1 不明 / 2 片方向のみ / 3 通れない）。0 はテーマの色
	const ACCESS_COLOR = ['#0d9488', '#f59e0b', '#8b5cf6', '#ef4444'];
	const accessColors = (t: Theme) => [OVERLAY[t].network, ...ACCESS_COLOR.slice(1)];
	const ROUTE_COLOR = '#2563eb';
	const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };

	let mapEl: HTMLDivElement;
	let map: maplibregl.Map | undefined;
	// 探索はブラウザ内（GitHub Pages にはサーバーが無い）。area.router が graph.json から作った探索グラフ
	let area = $state.raw<Area | null>(null);
	// 全地区の一覧（名前・範囲）。データは選んだ地区のものだけを読む
	let areas = $state.raw<AreaInfo[]>([]);
	let areaCase = $state('');
	let loadingArea = $state(false);
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
	// ライト / ダーク。app.html が描画前に <html data-theme> を決めているので、それに合わせて始める
	let theme = $state<Theme>('light');
	let paintTable: ReturnType<typeof basemapPaintTable> = [];
	let ready = $state(false); // 地図のレイヤーを足し終えたか（map 自体は $state にしない）
	// 3D 表示（階の高さに持ち上げて描く）。高さは「1 階分 × 強調倍率」の仮の値（#lib/view3d.ts）
	let view3d = $state(true); // 既定は 3D
	let exaggeration = $state(2);
	let overlay: ReturnType<typeof createOverlay> | undefined;
	// URL の hash（#ズーム/緯度/経度/向き/傾き）で開いたときは、その視点を保つ（最初の地区に寄せたり、3D で傾けたりしない）
	let keepHashView = false;
	// 3D のときは、地図の 2D の線（ネットワーク・経路・ステップ）を隠して deck.gl の線に任せる。
	// ネットワークは非表示にすると出典表示（ソースの attribution）まで消えるので、何も描かない filter で隠す
	const LAYERS_2D = ['route-glow', 'route-casing', 'route', 'step-line', 'step-point'];
	const HIDE_ALL: maplibregl.FilterSpecification = ['==', ['get', 'link_id'], ''];

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

	const inBbox = (b: AreaInfo['bbox'], [x, y]: [number, number], pad = 0) =>
		x >= b[0] - pad && x <= b[2] + pad && y >= b[1] - pad && y <= b[3] + pad;

	function onMapClick(e: MapMouseEvent) {
		const p: [number, number] = [e.lngLat.lng, e.lngLat.lat];
		if (loadingArea) return;
		// 地区の点をクリックしたら、その地区へ（縮小時は点のほうが地区の範囲より大きい）
		const hit = map?.getLayer('areas-point')
			? map.queryRenderedFeatures(
					[
						[e.point.x - 6, e.point.y - 6],
						[e.point.x + 6, e.point.y + 6]
					],
					{ layers: ['areas-point'] }
				)[0]
			: undefined;
		const hitCase = hit?.properties?.case as string | undefined;
		if (hitCase && hitCase !== areaCase) return void selectArea(hitCase);
		// 今の地区の外で、ほかの地区の範囲内をクリックしたら、その地区に切り替える（今の地区には約 300 m の余裕）
		const cur = areas.find((a) => a.case === areaCase);
		if (!cur || !inBbox(cur.bbox, p, 0.003)) {
			const other = areas.find((a) => a.case !== areaCase && inBbox(a.bbox, p));
			if (other) return void selectArea(other.case);
		}
		// 階：3D で線をクリックしたらその線の階（位置も持ち上げた線の上）。
		// 線から外れていれば、表示する階を 1 つに絞っているときはその階、それ以外は指定なし
		const hit3d = view3d && overlay ? pickNetwork(overlay, e.point.x, e.point.y, { exaggeration, viewFloor }) : null;
		const at = hit3d?.lngLat ?? p;
		const floor = hit3d ? hit3d.floor : viewFloor === 'all' ? null : viewFloor;
		if (!origin) [origin, fromFloor] = [at, floor];
		else [dest, toFloor] = [at, floor];
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
		// area: 地区を切り替えるとネットワークのレイヤーを作り直すので、そのたびに塗り直す
		if (!ready || !map || !area) return;
		const acc: ExpressionSpecification = ['get', `acc_${profile}`];
		const base = OVERLAY[theme].network;
		const color: ExpressionSpecification = ['match', acc, 1, ACCESS_COLOR[1], 2, ACCESS_COLOR[2], 3, ACCESS_COLOR[3], base];
		map.setPaintProperty('network', 'line-color', color);
		map.setPaintProperty('network', 'line-dasharray', ['case', ['==', acc, 3], ['literal', [1, 1]], ['literal', [1, 0]]]);
		const onFloor: ExpressionSpecification | boolean =
			viewFloor === 'all'
				? true
				: ['any', ['==', ['get', 'floor'], viewFloor], ['==', ['get', 'floor_end'], viewFloor]];
		map.setPaintProperty('network', 'line-opacity', ['case', onFloor, 0.95, 0.12]);
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
				.map(([k, v]) => `<tr><th class="pr-2 text-left font-normal text-slate-500 dark:text-slate-400">${k}</th><td>${v}</td></tr>`)
				.join('') +
			'</table>'
		);
	}

	/** ネットワークのソースとレイヤーを地区ごとに作り直す（出典表示が地区ごとに違うため） */
	function showNetwork(m: maplibregl.Map, a: Area | null) {
		if (m.getLayer('network')) m.removeLayer('network');
		if (m.getSource('network')) m.removeSource('network');
		m.addSource('network', {
			type: 'geojson',
			data: a?.network ?? EMPTY,
			attribution: a ? attributionHtml(a.sources) : undefined
		});
		m.addLayer(
			{
				id: 'network',
				type: 'line',
				source: 'network',
				paint: { 'line-color': ACCESS_COLOR[0], 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 1, 14, 1.8, 18, 4] }
			},
			'route-glow'
		);
	}

	// 3D 表示：オン・オフで視点を傾け、2D の線と deck.gl の線を入れ替える
	$effect(() => {
		if (!ready || !map) return;
		const on = view3d;
		for (const id of LAYERS_2D) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'none' : 'visible');
		if (map.getLayer('network')) map.setFilter('network', on ? HIDE_ALL : null);
		if (on && !overlay) {
			overlay = createOverlay();
			map.addControl(overlay);
			if (!keepHashView) map.easeTo({ pitch: 60, duration: 600 });
			keepHashView = false;
		} else if (!on && overlay) {
			map.removeControl(overlay);
			overlay = undefined;
			map.easeTo({ pitch: 0, bearing: 0, duration: 600 });
		}
	});

	$effect(() => {
		if (!ready || !map || !view3d || !overlay) return;
		const o = OVERLAY[theme];
		overlay.setProps({
			layers: layers3d({
				network: area?.network ?? null,
				route,
				profile,
				viewFloor,
				exaggeration,
				theme,
				accessColor: accessColors(theme),
				routeColor: o.route,
				casingColor: o.casing
			})
		});
	});

	// テーマ：背景地図の色と、自前のレイヤーの縁取り・薄い線の色を塗り替える（スタイルは差し替えない）
	$effect(() => {
		if (!ready || !map) return;
		const o = OVERLAY[theme];
		applyBasemapTheme(map, paintTable, theme);
		map.setPaintProperty('route-casing', 'line-color', o.casing);
		map.setPaintProperty('route', 'line-color', o.route);
		map.setPaintProperty('route-glow', 'line-color', o.route);
		map.setPaintProperty('snap', 'line-color', o.snap);
		map.setPaintProperty('areas-point', 'circle-color', o.areaPoint);
		map.setPaintProperty('areas-point', 'circle-stroke-color', o.halo);
		map.setPaintProperty('areas-outline', 'line-color', o.areaPoint);
		map.setPaintProperty('step-point', 'circle-stroke-color', o.halo);
	});

	function toggleTheme() {
		theme = theme === 'dark' ? 'light' : 'dark';
		applyTheme(theme);
	}

	/** 全地区が入るまで引く（青い点をクリックすると、その地区に切り替わる） */
	function showAllAreas() {
		if (!map || !areas.length) return;
		const b = new maplibregl.LngLatBounds();
		for (const a of areas) b.extend([a.bbox[0], a.bbox[1]]).extend([a.bbox[2], a.bbox[3]]);
		map.fitBounds(b, { padding: 60, duration: 600 });
	}

	async function selectArea(c: string, fit = true) {
		if (!map || loadingArea) return;
		loadingArea = true;
		loadError = null;
		try {
			const a = await loadArea(c);
			clearAll();
			fromFloor = toFloor = null;
			viewFloor = 'all';
			floors = a.floors;
			area = a;
			areaCase = c;
			showNetwork(map, a);
			if (view3d) map.setFilter('network', HIDE_ALL);
			const info = areas.find((x) => x.case === c);
			if (fit && info) map.fitBounds(info.bbox, { padding: 40, duration: 600 });
		} catch (e) {
			loadError = (e as Error).message;
		} finally {
			loadingArea = false;
		}
	}

	onMount(async () => {
		theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : initialTheme();
		maplibregl.setWorkerUrl(workerUrl);
		keepHashView = /^#[\d.]+\/-?[\d.]+\/-?[\d.]+/.test(location.hash);
		maplibregl.addProtocol('pmtiles', protocol.tile);
		map = new maplibregl.Map({
			container: mapEl,
			// GitHub Pages ではサイトが /<リポジトリ名>/ の下に置かれるので、静的ファイルは asset() で解決する
			style: new URL(asset('styles/pale.json'), location.href).href,
			center: [139.7, 35.69],
			zoom: 15,
			maxPitch: 85,
			// 視点を URL の hash に書く（共有・再読み込みで同じ視点に戻る）
			hash: true,
			attributionControl: { compact: true }
		});
		// load は 1 回しか来ない。データを待っている間に来ても取りこぼさないよう、先に待ち受ける
		const mapLoaded = new Promise((r) => map!.once('load', r));
		// 紹介動画の録画（web/scripts/promo.ts）が地図を操作するための入口。開発サーバーのときだけ置く
		if (import.meta.env.DEV) {
			const w = window as unknown as Record<string, unknown>;
			w.__map = map;
			// 3D の線（階の高さに持ち上げた位置）の画面上の位置。録画（web/scripts/demo-shinjuku.ts）が地下の線をクリックするのに使う。
			// deck.gl のビューポートはオーバーレイの内部（_deck）からしか取れないので、開発用に限る
			w.__project3d = (lon: number, lat: number, floor: number): [number, number] | null => {
				const deck = (overlay as unknown as { _deck?: { getViewports(): { project(p: number[]): number[] }[] } })?._deck;
				const vp = deck?.getViewports()[0];
				if (!vp) return null;
				const [x, y] = vp.project([lon, lat, floor * FLOOR_M * exaggeration]);
				return [x, y];
			};
		}
		map.addControl(new maplibregl.NavigationControl(), 'top-right');
		map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');

		let first = '';
		try {
			const list = await getAreas();
			areas = list.areas;
			first = list.default;
		} catch (e) {
			loadError = (e as Error).message;
		}
		await mapLoaded;
		const m = map;
		// 背景スタイルの色を控える（自前のレイヤーを足す前に。ダークの値もここで作る）
		paintTable = basemapPaintTable(m);

		// 全地区の範囲（枠）と位置（点）。縮小しても地区が見つかるように
		m.addSource('areas', {
			type: 'geojson',
			data: {
				type: 'FeatureCollection',
				features: areas.flatMap((a) => {
					const [w, s, e, n] = a.bbox;
					const props = { case: a.case, title: a.title };
					return [
						{ type: 'Feature', properties: props, geometry: { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] } },
						{ type: 'Feature', properties: props, geometry: { type: 'Point', coordinates: [(w + e) / 2, (s + n) / 2] } }
					] as GeoJSON.Feature[];
				})
			}
		});
		m.addLayer({
			id: 'areas-outline',
			type: 'line',
			source: 'areas',
			filter: ['==', ['geometry-type'], 'Polygon'],
			paint: { 'line-color': '#2563eb', 'line-width': 1.5, 'line-dasharray': [3, 2], 'line-opacity': 0.6 }
		});
		m.addLayer({
			id: 'areas-point',
			type: 'circle',
			source: 'areas',
			maxzoom: 13,
			filter: ['==', ['geometry-type'], 'Point'],
			paint: { 'circle-color': '#2563eb', 'circle-radius': 7, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 }
		});
		m.addSource('route', { type: 'geojson', data: EMPTY });
		m.addSource('snap', { type: 'geojson', data: EMPTY });
		m.addSource('step', { type: 'geojson', data: EMPTY });
		// 経路の発光：太くぼかした線を下に敷く
		m.addLayer({
			id: 'route-glow',
			type: 'line',
			source: 'route',
			layout: { 'line-cap': 'round', 'line-join': 'round' },
			paint: {
				'line-color': ROUTE_COLOR,
				'line-width': ['interpolate', ['linear'], ['zoom'], 14, 18, 18, 44],
				'line-blur': ['interpolate', ['linear'], ['zoom'], 14, 10, 18, 24],
				'line-opacity': 0.55
			}
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
		// 地区の点：名前を出す。クリックは onMapClick がその地区に切り替える
		const areaPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
		m.on('mousemove', 'areas-point', (e: maplibregl.MapLayerMouseEvent) => {
			const f = e.features?.[0];
			if (!f) return;
			m.getCanvas().style.cursor = 'pointer';
			const el = document.createElement('span');
			el.className = 'text-xs';
			el.textContent = String(f.properties?.title ?? '');
			areaPopup.setLngLat(e.lngLat).setDOMContent(el).addTo(m);
		});
		m.on('mouseleave', 'areas-point', () => {
			m.getCanvas().style.cursor = '';
			areaPopup.remove();
		});
		m.on('click', onMapClick);
		ready = true;
		if (keepHashView) {
			// hash の視点の中心を含む地区を開く（無ければ既定の地区。視点は動かさない）
			const c = m.getCenter();
			first = areas.find((a) => inBbox(a.bbox, [c.lng, c.lat], 0.003))?.case ?? first;
			if (first) await selectArea(first, false);
		} else if (first) {
			const info = areas.find((x) => x.case === first);
			if (info) m.fitBounds(info.bbox, { padding: 40, duration: 0 });
			await selectArea(first, false);
		}
	});

	onDestroy(() => {
		map?.remove();
		maplibregl.removeProtocol('pmtiles');
	});

	const currentProfile = $derived(profiles.find((p) => p.key === profile));
</script>

<div class="flex h-dvh w-full flex-col md:flex-row">
	<aside class="order-2 flex max-h-[50dvh] w-full flex-col overflow-hidden border-slate-200 bg-white md:order-1 md:max-h-none md:w-96 md:border-r dark:border-slate-700 dark:bg-slate-900">
		<header class="border-b border-slate-200 px-4 py-3 dark:border-slate-700">
			<div class="flex items-center justify-between">
				<h1 class="text-base font-bold text-slate-800 dark:text-slate-100">歩行者経路探索</h1>
				<button
					type="button"
					class="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
					onclick={toggleTheme}
					aria-label={theme === 'dark' ? 'ライトモードにする' : 'ダークモードにする'}
					title={theme === 'dark' ? 'ライトモードにする' : 'ダークモードにする'}
				>
					{theme === 'dark' ? '☀️ ライト' : '🌙 ダーク'}
				</button>
			</div>
			<p class="text-xs text-slate-500 dark:text-slate-400">
				ほこナビDP の歩行空間ネットワークデータ（{areas.length || '–'}地区）
			</p>
		</header>

		<div class="space-y-3 overflow-y-auto px-4 py-3">
			<div class="block">
				<span class="mb-1 flex items-center justify-between text-xs font-semibold text-slate-600 dark:text-slate-300">
					地区
					<button type="button" class="rounded border border-slate-300 px-1.5 py-0.5 font-normal hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800" onclick={showAllAreas}>全地区を表示</button>
				</span>
				<select
					class="w-full rounded border border-slate-300 px-2 py-1 text-sm dark:border-slate-600"
					aria-label="地区"
					value={areaCase}
					disabled={loadingArea}
					onchange={(e) => selectArea(e.currentTarget.value)}
				>
					{#each areas as a (a.case)}
						<option value={a.case}>{a.title}（{a.links.toLocaleString()}リンク）</option>
					{/each}
				</select>
				<span class="mt-0.5 block text-[10px] text-slate-400 dark:text-slate-500">
					{#if loadingArea}読み込み中…{:else if area}{area.links.toLocaleString()}リンク・{areas.find((a) => a.case === areaCase)?.data_updated ?? '–'} 更新。地図の青い枠・点をクリックしても切り替わります{/if}
				</span>
			</div>

			{#if loadError}
				<p class="rounded bg-red-50 p-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-200">データを読めません: {loadError}</p>
			{/if}

			<fieldset>
				<legend class="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">利用者</legend>
				<div class="grid grid-cols-4 gap-1">
					{#each profiles as p (p.key)}
						<button
							type="button"
							class="rounded border px-1 py-1.5 text-sm {profile === p.key
								? 'border-blue-600 bg-blue-600 text-white'
								: 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800'}"
							onclick={() => ((profile = p.key), search())}>{p.label}</button
						>
					{/each}
				</div>
				{#if currentProfile}
					<p class="mt-1 text-xs text-slate-500 dark:text-slate-400">{currentProfile.description}</p>
				{/if}
			</fieldset>

			<div class="grid grid-cols-[auto_1fr_auto] items-center gap-x-2 gap-y-1 text-sm">
				<span class="grid h-5 w-5 place-items-center rounded-full bg-green-600 text-[10px] font-bold text-white">発</span>
				<span class="truncate text-slate-700 dark:text-slate-200">{origin ? `${origin[1].toFixed(5)}, ${origin[0].toFixed(5)}` : '地図をクリック'}</span>
				<select class="rounded border border-slate-300 px-1 py-0.5 text-xs dark:border-slate-600" bind:value={fromFloor} onchange={search} aria-label="出発地の階">
					<option value={null}>階の指定なし</option>
					{#each floors as f (f)}<option value={f}>{floorLabel(f)}</option>{/each}
				</select>
				<span class="grid h-5 w-5 place-items-center rounded-full bg-red-600 text-[10px] font-bold text-white">着</span>
				<span class="truncate text-slate-700 dark:text-slate-200">{dest ? `${dest[1].toFixed(5)}, ${dest[0].toFixed(5)}` : origin ? '地図をクリック' : '–'}</span>
				<select class="rounded border border-slate-300 px-1 py-0.5 text-xs dark:border-slate-600" bind:value={toFloor} onchange={search} aria-label="目的地の階">
					<option value={null}>階の指定なし</option>
					{#each floors as f (f)}<option value={f}>{floorLabel(f)}</option>{/each}
				</select>
			</div>
			<div class="flex gap-2">
				<button type="button" class="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-40 dark:border-slate-600 dark:hover:bg-slate-800" disabled={!origin || !dest} onclick={swap}>入れ替え</button>
				<button type="button" class="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800" onclick={clearAll}>クリア</button>
			</div>

			{#if error}
				<p class="rounded bg-red-50 p-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-200">{error}</p>
			{/if}

			{#if route?.unreachable_reason}
				<p class="rounded bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">{route.unreachable_reason}</p>
			{:else if route?.summary}
				{@const s = route.summary}
				<section class="rounded border border-slate-200 p-3 dark:border-slate-700">
					<div class="flex items-baseline gap-3">
						<span class="text-2xl font-bold text-slate-800 dark:text-slate-100">{formatDuration(s.time_s)}</span>
						<span class="text-sm text-slate-600 dark:text-slate-300">{Math.round(s.dist_m).toLocaleString()} m</span>
						<span class="ml-auto text-[10px] text-slate-400 dark:text-slate-500">{route.elapsed_ms} ms</span>
					</div>
					<p class="mt-1 text-xs text-slate-600 dark:text-slate-300">
						階段 {s.stairs}・エレベーター {s.elevator}・エスカレーター {s.escalator}・横断歩道 {s.crosswalk}・横断 {s.crossing}
					</p>
					{#each route.warnings as w (w)}
						<p class="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">{w}</p>
					{/each}
				</section>

				<ol class="divide-y divide-slate-100 dark:divide-slate-800">
					{#each route.steps as st, i (i)}
						<li>
							<button
								type="button"
								class="flex w-full gap-2 px-1 py-2 text-left text-sm {activeStep === i ? 'bg-orange-50 dark:bg-orange-950' : 'hover:bg-slate-50 dark:hover:bg-slate-800'}"
								onmouseenter={() => (activeStep = i)}
								onclick={() => focusStep(i)}
							>
								<span class="w-5 shrink-0 text-right text-xs text-slate-400 dark:text-slate-500">{i + 1}</span>
								<span class="flex-1 text-slate-800 dark:text-slate-100">{st.instruction}</span>
								{#if st.floor_from != null && st.floor_from !== 0}
									<span class="shrink-0 text-xs text-slate-500 dark:text-slate-400">{floorLabel(st.floor_from)}</span>
								{/if}
							</button>
						</li>
					{/each}
				</ol>
			{/if}

			<fieldset class="border-t border-slate-200 pt-3 dark:border-slate-700">
				<legend class="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">表示する階</legend>
				<div class="mb-2 flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
					<label class="flex items-center gap-1"><input type="checkbox" bind:checked={view3d} />3D で表示</label>
					{#if view3d}
						<label class="flex flex-1 items-center gap-1"
							>高さ ×{exaggeration}<input class="flex-1" type="range" min="1" max="5" step="1" bind:value={exaggeration} aria-label="高さの強調" /></label
						>
					{/if}
				</div>
				<select class="w-full rounded border border-slate-300 px-2 py-1 text-sm dark:border-slate-600" bind:value={viewFloor}>
					<option value="all">すべて</option>
					{#each floors as f (f)}<option value={f}>{floorLabel(f)}</option>{/each}
				</select>
				<ul class="mt-2 grid grid-cols-2 gap-1 text-xs text-slate-600 dark:text-slate-300">
					{#each ['通れる', '属性が不明', '片方向のみ', '通れない'] as label, i (label)}
						<li class="flex items-center gap-1">
							<span class="inline-block h-1 w-5 rounded" style="background:{accessColors(theme)[i]}"></span>{label}
						</li>
					{/each}
				</ul>
				<p class="mt-1 text-[10px] text-slate-400 dark:text-slate-500">ネットワークの色は、選んだ利用者で通れるかどうか</p>
				{#if view3d}
					<p class="mt-1 text-[10px] text-slate-400 dark:text-slate-500">
						3D の高さは階 × 4 m × 強調倍率（表示用の仮の値。データに高さは無い）。右ドラッグ・Ctrl＋ドラッグで傾き・向きを変えられます
					</p>
				{/if}
			</fieldset>
		</div>
	</aside>

	<div class="relative order-1 min-h-0 flex-1 md:order-2" bind:this={mapEl}></div>
</div>
