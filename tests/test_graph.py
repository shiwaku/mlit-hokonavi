import numpy as np
import pandas as pd
import pytest

from api import codes as C
from api import guidance
from api.graph import PedestrianGraph
from api.profiles import compute
from tests.conftest import latlon, make_frames


def links_of(g, r) -> list[str]:
    ids = [str(g.links['link_id'].iat[p.link]) for p in r['pieces']]
    return [x for i, x in enumerate(ids) if i == 0 or x != ids[i - 1]]


def route(g, a, b, profile='walk', **kw):
    (alat, alon), (blat, blon) = a, b
    return g.route(alat, alon, blat, blon, profile, **kw)


# ---------------------------------------------------------------- プロファイル
def test_walk_uses_stairs(graph):
    r = route(graph, latlon('A', dx=0.5), latlon('C', dx=0.5))
    assert r['unreachable_reason'] is None
    assert links_of(graph, r) == ['AB', 'BC', 'CF']


@pytest.mark.parametrize('profile', ['wheelchair', 'stroller'])
def test_step_free_profiles_take_elevator(graph, profile):
    r = route(graph, latlon('A', dx=0.5), latlon('C', dx=0.5), profile)
    assert r['unreachable_reason'] is None
    assert 'BC' not in links_of(graph, r)
    assert {'DE', 'EE2'} <= set(links_of(graph, r))


def test_wheelchair_without_accessible_elevator_snaps_farther_and_warns():
    # 車いす非対応のエレベーターでは A 側と C 側がつながらない。半径を広げ、出発地を C 側の道（106m）に寄せて警告を出す
    g = PedestrianGraph.from_frames(*make_frames({'DE': {'elevator': 2}}))
    r = route(g, latlon('A', dx=0.5), latlon('C', dx=0.5), 'wheelchair')
    assert r['unreachable_reason'] is None
    assert links_of(g, r) == ['E2C', 'CF']
    assert r['snap']['origin'].dist_m > 50 and r['snap']['dest'].dist_m < 1
    out = guidance.build(g, r, 'wheelchair')
    assert [w for w in out['warnings'] if '離れた道' in w] == [out['warnings'][0]]
    assert '出発地' in out['warnings'][0]


def test_one_way_link_is_not_used_backwards(graph):
    fwd = route(graph, latlon('F', dx=0.1), latlon('G', dx=-0.1))
    assert fwd['unreachable_reason'] is None
    assert links_of(graph, fwd) == ['FG']
    back = route(graph, latlon('G', dx=-0.1), latlon('F', dx=0.1))
    assert back['unreachable_reason'] is not None


def test_slope_cost_depends_on_direction():
    links, _ = make_frames({'AB': {'vtcl_slope': 3}})  # 5〜8%・終点が高い（A→B が上り）
    lc = compute(links, 'wheelchair')
    i = int(np.flatnonzero(links['link_id'] == 'AB')[0])
    assert lc.cost_fwd[i] > lc.cost_bwd[i]
    assert lc.time_fwd[i] == pytest.approx(100 / 1.0)


@pytest.mark.parametrize('code,blocked', [(3, False), (4, True), (5, True)])
def test_wheelchair_level_difference(code, blocked):
    links, _ = make_frames({'AB': {'lev_diff': code}})
    lc = compute(links, 'wheelchair')
    i = int(np.flatnonzero(links['link_id'] == 'AB')[0])
    assert np.isinf(lc.cost_fwd[i]) == blocked


def test_unknown_attribute_is_flagged_not_blocked():
    links, _ = make_frames({'AB': {'lev_diff': C.UNKNOWN}})
    lc = compute(links, 'wheelchair')
    i = int(np.flatnonzero(links['link_id'] == 'AB')[0])
    assert lc.unknown[i] and np.isfinite(lc.cost_fwd[i])


# ---------------------------------------------------------------- スナップ
def test_route_on_same_link_is_direct(graph):
    r = route(graph, latlon('A', dx=0.2), latlon('A', dx=0.7))
    assert len(r['pieces']) == 1
    p = r['pieces'][0]
    assert p.forward and p.a == pytest.approx(0.2, abs=0.01) and p.b == pytest.approx(0.7, abs=0.01)


def test_snap_respects_floor(graph):
    lat, lon = latlon('E')
    assert {s.link for s in graph.snap_candidates(lat, lon, 'walk', floor=1.0)} == set()
    assert graph.snap_candidates(lat, lon, 'walk', floor=0.0)


def test_far_from_network():
    g = PedestrianGraph.from_frames(*make_frames())
    r = g.route(35.0, 139.0, 35.0, 139.001, 'walk')
    assert '出発地' in r['unreachable_reason']


# ---------------------------------------------------------------- 案内
def test_guidance_steps(graph):
    r = route(graph, latlon('A', dx=0.5), latlon('C', dx=0.5), 'wheelchair')
    out = guidance.build(graph, r, 'wheelchair')
    kinds = [s['kind'] for s in out['steps']]
    assert kinds[0] == 'walk' and kinds[-1] == 'arrive'
    assert 'elevator' in kinds
    elev = next(s for s in out['steps'] if s['kind'] == 'elevator')
    assert elev['instruction'] == 'エレベーターで地上へ'  # D(地上) → E(1階) → E2(地上) を 1 ステップにまとめる
    assert out['summary']['elevator'] == 1
    coords = out['geometry']['coordinates']
    assert all(0 <= s['coord_start'] <= s['coord_end'] < len(coords) for s in out['steps'])
    total = sum(s['dist_m'] for s in out['steps'])
    assert total == pytest.approx(out['summary']['dist_m'], abs=1.0)


def test_turn_of():
    assert guidance.turn_of(None, 90)[0] == 'start'
    assert guidance.turn_of(0, 5)[0] == 'straight'
    assert guidance.turn_of(0, 90)[0] == 'right'
    assert guidance.turn_of(0, 270)[0] == 'left'
    assert guidance.turn_of(0, 40)[0] == 'slight_right'
    assert guidance.turn_of(0, 180)[0] == 'uturn'


def test_floor_label():
    assert guidance.floor_label(0.0) == '地上'
    assert guidance.floor_label(2.0) == '2階'
    assert guidance.floor_label(-1.0) == '地下1階'
    assert guidance.floor_label(float('nan')) == '不明な階'


def test_frames_have_expected_columns():
    links, nodes = make_frames()
    assert isinstance(links, pd.DataFrame) and {'floor_start', 'floor_end', 'geometry'} <= set(links.columns)
