"""
歩行者経路探索 API（FastAPI）

  uv run uvicorn api.main:app --port 8000

探索するネットワークは環境変数 HOKONAVI_CASE（既定 shibuya_sendagaya_shinjuku）で選ぶ。network/<case>/ に
preprocess/build_network.py の出力が要る。仕様は docs/api-design.md。
"""

import os
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from api import export, guidance
from api.graph import PedestrianGraph, Snap
from api.profiles import PROFILES

ProfileKey = Literal['walk', 'wheelchair', 'stroller', 'vision']

state: dict = {'graph': None, 'error': None, 'started_at': time.time(), 'network': None}


def load_graph(case: str | None = None, net_dir: str | None = None) -> None:
    try:
        state['graph'] = PedestrianGraph(
            case or os.environ.get('HOKONAVI_CASE', 'shibuya_sendagaya_shinjuku'),
            net_dir or os.environ.get('HOKONAVI_NETWORK_DIR', 'network'),
        )
        state['network'] = None
        state['error'] = None
    except Exception as e:  # 起動失敗を /health で見えるようにする
        state['graph'] = None
        state['error'] = repr(e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if state['graph'] is None:  # テストでは先に差し込む
        load_graph()
    yield


app = FastAPI(
    title='Hokonavi Route Search API',
    version='0.1',
    lifespan=lifespan,
    description='歩行空間ネットワークデータによる歩行者経路探索。仕様は docs/api-design.md',
)
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=5)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173'], allow_methods=['GET'], allow_headers=['*'])


def graph() -> PedestrianGraph:
    g = state['graph']
    if g is None:
        raise HTTPException(503, f'ネットワークを読み込めていません: {state["error"]}')
    return g


@app.get('/health')
def health():
    g = state['graph']
    body = {
        'status': 'ok' if g else 'error',
        'graph_loaded': g is not None,
        'uptime_seconds': round(time.time() - state['started_at'], 1),
    }
    if g:
        body.update(g.health())
    if state['error']:
        body['error'] = state['error']
    return body


@app.get('/profiles')
def profiles():
    return [
        {'key': p.key, 'label': p.label, 'speed_mps': p.speed_mps, 'description': p.description}
        for p in PROFILES.values()
    ]


@app.get('/network')
def network():
    """表示用のネットワーク（GeoJSON）。acc_<プロファイル> は 0: 通れる、1: 属性が不明、2: 片方向のみ、3: 通れない"""
    g = graph()
    if state['network'] is None:
        state['network'] = export.network_geojson(g)
    return state['network']


def _snap_json(s: Snap | None) -> dict | None:
    if s is None:
        return None
    return {'lat': round(s.lat, 7), 'lon': round(s.lon, 7), 'dist_m': round(s.dist_m, 1)}


@app.get('/route')
def route(
    from_lat: float = Query(ge=20, le=46),
    from_lon: float = Query(ge=122, le=154),
    to_lat: float = Query(ge=20, le=46),
    to_lon: float = Query(ge=122, le=154),
    profile: ProfileKey = 'walk',
    from_floor: float | None = Query(None, description='出発地を寄せる階。省略時はすべての階から最寄り'),
    to_floor: float | None = Query(None, description='目的地を寄せる階'),
):
    """2 地点間の歩行者経路。到達不能は 200 で geometry=null・unreachable_reason に理由"""
    g = graph()
    r = g.route(from_lat, from_lon, to_lat, to_lon, profile, from_floor, to_floor)
    snap = r['snap'] or {}
    body = {
        'profile': profile,
        'snap': {'origin': _snap_json(snap.get('origin')), 'dest': _snap_json(snap.get('dest'))},
        'unreachable_reason': r['unreachable_reason'],
        'geometry': None,
        'floor_path': None,
        'summary': None,
        'steps': [],
        'warnings': [],
    }
    if r['unreachable_reason'] is None:
        body.update(guidance.build(g, r, profile))
    body['elapsed_ms'] = round(r['elapsed_ms'], 1)
    return body
