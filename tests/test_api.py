"""API の結合テスト（全エンドポイントの正常系と主な異常系）。グラフは conftest の合成ネットワークを差し込む"""

import pytest
from fastapi.testclient import TestClient

from api import main
from api.graph import PedestrianGraph
from tests.conftest import latlon, make_frames


@pytest.fixture
def client():
    main.state.update(graph=PedestrianGraph.from_frames(*make_frames()), error=None, network=None)
    with TestClient(main.app) as c:
        yield c
    main.state.update(graph=None, network=None)


def params(a, b, **kw):
    (alat, alon), (blat, blon) = a, b
    return {'from_lat': alat, 'from_lon': alon, 'to_lat': blat, 'to_lon': blon, **kw}


def test_health(client):
    body = client.get('/health').json()
    assert body['status'] == 'ok' and body['links'] == 8 and body['floors'] == [0.0, 1.0]


def test_health_when_not_loaded():
    main.state.update(graph=None, error='FileNotFoundError()', network=None)
    main.state['graph'] = None
    c = TestClient(main.app)  # lifespan を走らせない（走らせると実データを読みに行く）
    body = c.get('/health').json()
    assert body['status'] == 'error' and 'FileNotFoundError' in body['error']
    r = c.get('/route', params=params(latlon('A'), latlon('C')))
    assert r.status_code == 503
    assert c.get('/network').status_code == 503
    main.state.update(error=None)


def test_profiles(client):
    keys = [p['key'] for p in client.get('/profiles').json()]
    assert keys == ['walk', 'wheelchair', 'stroller', 'vision']


def test_network(client):
    fc = client.get('/network').json()
    assert fc['type'] == 'FeatureCollection' and len(fc['features']) == 8
    by_id = {f['properties']['link_id']: f['properties'] for f in fc['features']}
    assert by_id['BC']['kind'] == 'stairs'
    assert by_id['BC']['acc_wheelchair'] == 3 and by_id['BC']['acc_walk'] == 0
    assert by_id['FG']['acc_walk'] == 2  # 一方通行


def test_route_ok(client):
    r = client.get('/route', params=params(latlon('A', dx=0.5), latlon('C', dx=0.5), profile='wheelchair'))
    assert r.status_code == 200
    body = r.json()
    assert body['unreachable_reason'] is None
    assert body['geometry']['type'] == 'LineString' and len(body['geometry']['coordinates']) >= 2
    assert body['summary']['elevator'] == 1 and body['summary']['stairs'] == 0
    assert body['steps'][-1]['kind'] == 'arrive'
    assert body['snap']['origin']['dist_m'] < 1


def test_route_unreachable_is_200(client):
    r = client.get('/route', params=params(latlon('G', dx=-0.1), latlon('F', dx=0.1)))
    assert r.status_code == 200
    body = r.json()
    assert body['geometry'] is None and body['unreachable_reason']


def test_route_far_from_network(client):
    body = client.get('/route', params=params((35.0, 139.0), (35.0, 139.001))).json()
    assert body['geometry'] is None and '出発地' in body['unreachable_reason']


@pytest.mark.parametrize(
    'bad',
    [
        {'profile': 'car'},
        {'from_lat': 10.0},
        {'to_lon': 'x'},
    ],
)
def test_route_validation(client, bad):
    p = params(latlon('A'), latlon('C'))
    p.update(bad)
    assert client.get('/route', params=p).status_code == 422


def test_route_missing_param(client):
    assert client.get('/route', params={'from_lat': 35.69}).status_code == 422
