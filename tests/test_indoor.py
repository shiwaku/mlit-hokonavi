"""preprocess/indoor.py（屋内地図オープンデータの読み方と、屋外へのつなぎ方）"""

import shapefile

from api import codes as C
from preprocess import build_network as B
from preprocess import indoor as I
from tests.test_build_network import write_dataset

# 屋外: A(0,0)-B(100m,0)。屋内: P は A から 1 m（地上）、Q は B から 1 m だが地下、R・S はエレベーター
DEG_M = 1 / 110_950
INDOOR_NODES = {
    # node_id: (緯度, 経度, ordinal, in_out)
    'P': (35.690 + DEG_M, 139.700, 0.0, 2),
    'P2': (35.690 + 1.5 * DEG_M, 139.700, 0.0, 3),
    'Q': (35.690 + DEG_M, 139.701, -1.0, 3),
    'R': (35.690 + 5 * DEG_M, 139.700, 0.0, 3),
    'S': (35.690 + 5 * DEG_M, 139.700, -1.0, 3),
}
INDOOR_LINKS = [
    # link_id, start, end, route_type, vtcl_slope, lev_diff, elevator
    ('I1', 'P', 'P2', '6', '1', '1', '1'),  # 短い階段（P と P2 はどちらも A から 2 m 以内）
    ('I2', 'P2', 'R', '1', '2', '1', '1'),
    ('E1', 'R', 'S', '4', '1', '1', '2'),  # かご（バリアフリー対応なし）
    ('E2', 'S', 'Q', '4', '1', '1', '3'),  # 同じエレベーターの乗り場（車いす対応）→ 食い違い
]


def write_indoor(root, name):
    d = root / name / 'nw'
    d.mkdir(parents=True)
    with shapefile.Writer(str(d / 'X_node'), shapeType=shapefile.POINT, encoding='utf-8') as w:
        for f in ('node_id', 'in_out'):
            w.field(f, 'C', 40)
        w.field('ordinal', 'N', 6, 1)
        for nid, (lat, lon, o, io) in INDOOR_NODES.items():
            w.point(lon, lat)
            w.record(node_id=nid, in_out=str(io), ordinal=o)
    with shapefile.Writer(str(d / 'X_link'), shapeType=shapefile.POLYLINE, encoding='utf-8') as w:
        for f in ('link_id', 'start_id', 'end_id', 'route_type', 'vtcl_slope', 'lev_diff', 'elevator', 'direction'):
            w.field(f, 'C', 40)
        w.field('distance', 'N', 8, 1)
        for lid, s, e, rt, sl, lev, el in INDOOR_LINKS:
            a, b = INDOOR_NODES[s], INDOOR_NODES[e]
            w.line([[[a[1], a[0]], [b[1], b[0]]]])
            dist = 1.0 if rt != '4' else 0.0
            w.record(lid, s, e, rt, sl, lev, el, '1', dist)


def test_load_reads_old_codes(tmp_path, monkeypatch):
    write_indoor(tmp_path, 'indoor')
    monkeypatch.setattr(I, 'RAW', tmp_path)
    links, nodes, report = I.load('indoor')
    by = links.set_index('link_id')
    # 旧 1（2cm 以下・5% 以下）は 2024 年版の 2、旧 2（より大きい）は不明
    assert by.loc['I1', 'lev_diff'] == 2 and by.loc['I2', 'lev_diff'] == 2
    assert by.loc['I2', 'vtcl_slope'] == C.UNKNOWN
    assert by.loc['I1', 'tfc_restr'] == C.MISSING
    # 1 基のエレベーターで種別が 2 と 3 に食い違う → 不明
    assert by.loc['E1', 'elevator'] == C.UNKNOWN and by.loc['E2', 'elevator'] == C.UNKNOWN
    assert report['elevators'] == {'count': 1, 'mixed_to_unknown': 1}
    assert nodes.set_index('node_id').loc['S', 'floor'] == -1.0


def test_attach_merges_same_floor_one_to_one(tmp_path, monkeypatch):
    write_dataset(tmp_path, 'nwd_out', [('L1', 'A', 'B', 1)])
    write_indoor(tmp_path, 'indoor')
    monkeypatch.setattr(B, 'RAW', tmp_path)
    monkeypatch.setattr(B, 'source_of', lambda d: None)
    monkeypatch.setattr(I, 'RAW', tmp_path)
    links, nodes, report = B.build(['nwd_out'], ['indoor'])
    st = report['indoor'][0]
    # P だけが A に寄る（P2 も 2 m 以内だが、A は 1 対 1 で P が取る）。Q は B の 1 m 先だが地下なのでつながない
    assert st['merged_nodes'] == 1
    ids = set(nodes['node_id'])
    assert 'P' not in ids and {'P2', 'Q', 'R', 'S'} <= ids
    by = links.set_index('link_id')
    # 階段 I1 は消えずに A-P2 を結ぶ
    assert (by.loc['I1', 'start_id'], by.loc['I1', 'end_id']) == ('A', 'P2')
    assert report['components']['count'] == 1
    assert [s for s in report['sources'] if s] == []
