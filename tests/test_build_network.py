"""preprocess/build_network.py の地区のまとめ方（ID の重複・新しい版の優先・グループ分け）"""

import csv

import pytest

from preprocess import build_network as B

LINK_HEADER = [
    'link_id',
    'start_id',
    'end_id',
    'distance',
    'rt_struct',
    'route_type',
    'direction',
    'width',
    'vtcl_slope',
    'lev_diff',
    'tfc_signal',
    'tfc_s_type',
    'brail_tile',
    'elevator',
    'roof',
]
NODE_HEADER = ['node_id', 'lat', 'lon', 'floor', 'elevation', 'in_out', 'link1_id', 'link2_id']
# 約 100 m 間隔のノード（同じ ID は同じ座標）
POS = {
    'A': (35.690, 139.700),
    'B': (35.690, 139.701),
    'B2': (35.690, 139.701),
    'C': (35.690, 139.702),
    'D': (35.690, 139.703),
    'X': (34.000, 135.000),
    'Y': (34.000, 135.001),
}
UPDATED = {'nwd_old': '2018/10/29', 'nwd_new': '2021/05/26', 'nwd_next': '2020/01/01', 'nwd_far': '2019/01/01'}


def write_dataset(root, name, links):
    d = root / name
    d.mkdir(parents=True)
    with open(d / 'link.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(LINK_HEADER)
        for lid, s, e, lev in links:
            w.writerow([lid, s, e, 90.0, 1, 1, 1, 4, 1, lev, 1, 1, 2, 1, 1])
    nodes = sorted({n for _, s, e, _ in links for n in (s, e)})
    with open(d / 'node.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(NODE_HEADER)
        for n in nodes:
            lat, lon = POS[n]
            w.writerow([n, lat, lon, 0.0, '', 1, '', ''])


@pytest.fixture
def raw(tmp_path, monkeypatch):
    # old（2018）: A-B-C。new（2021）: 同じリンク ID で B を B2 に振り直し、L2 の段差を直し、C-D を足した版
    write_dataset(tmp_path, 'nwd_old', [('L1', 'A', 'B', 1), ('L2', 'B', 'C', 1)])
    write_dataset(tmp_path, 'nwd_new', [('L1', 'A', 'B2', 1), ('L2', 'B2', 'C', 5), ('L3', 'C', 'D', 1)])
    # next: D を共有してつながる別の地区。far: どことも共有しない
    write_dataset(tmp_path, 'nwd_next', [('M1', 'D', 'C', 1)])
    write_dataset(tmp_path, 'nwd_far', [('F1', 'X', 'Y', 1)])
    monkeypatch.setattr(B, 'RAW', tmp_path)
    monkeypatch.setattr(
        B,
        'source_of',
        lambda d: {
            'dataset': d,
            'title': f'歩行空間ネットワークデータ（{d}）',
            'publisher': '国土交通省',
            'license': 'PDL1.0',
            'license_url': '',
            'data_updated': UPDATED[d],
            'url': '',
        },
    )
    return tmp_path


def test_newest_link_row_wins_and_old_nodes_are_dropped(raw):
    links, nodes, report = B.build(['nwd_old', 'nwd_new'])
    by_id = links.set_index('link_id')
    # L1・L2 は新しい版の行ごと（端点 B2・段差 5）。属性を混ぜると B と B2 で接続が壊れる
    assert by_id.loc['L1', 'end_id'] == 'B2' and by_id.loc['L2', 'start_id'] == 'B2'
    assert by_id.loc['L2', 'lev_diff'] == 5
    assert set(by_id['dataset']) == {'nwd_new'}
    # 古い版にしか無い B はどのリンクからも使われないので落とす
    assert set(nodes['node_id']) == {'A', 'B2', 'C', 'D'}
    assert report['dropped_unused_nodes'] == 1
    assert report['components']['count'] == 1
    assert report['datasets'] == ['nwd_new', 'nwd_old']  # 新しい順
    assert [p['links_used'] for p in report['per_dataset']] == [3, 0]
    # 出典はリンクを採った地区だけ
    assert [x['dataset'] for x in report['sources']] == ['nwd_new']


def test_clusters_group_datasets_sharing_node_ids(raw):
    groups = B.clusters(['nwd_far', 'nwd_new', 'nwd_next', 'nwd_old'])
    assert groups == [['nwd_new', 'nwd_old', 'nwd_next'], ['nwd_far']]


def test_area_title_lists_datasets_that_contribute(raw):
    _, _, report = B.build(['nwd_new', 'nwd_old', 'nwd_next'])
    # old はリンクを 1 本も出していないので名前に入れない。next は 4 本中 1 本（1 割以上）なので入れる
    assert B.area_title(report) == 'nwd_new／nwd_next'


def test_short_title():
    assert B.short_title('歩行空間ネットワークデータ（新宿駅周辺）') == '新宿駅周辺'
    assert B.short_title('歩行空間ネットワークデータ （新横浜地区）') == '新横浜地区'
    assert B.short_title('タイトル') == 'タイトル'
