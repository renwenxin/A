"""今日1进2（视频方法论版）单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Task 1: 权重 ----------

def test_default_weights_complete():
    from ashare_review.one_two_v2.weights import DEFAULT_WEIGHTS, DIMENSIONS
    assert set(DEFAULT_WEIGHTS['dimensions'].keys()) == set(DIMENSIONS)
    assert len(DIMENSIONS) == 8
    for k in ('auction_ratio_high', 'auction_ratio_mid', 'auction_ratio_low',
              'volume_health_pct', 'cap_max', 'price_max'):
        assert k in DEFAULT_WEIGHTS['thresholds']
    assert DEFAULT_WEIGHTS['thresholds']['auction_ratio_high'] == 10.0
    assert DEFAULT_WEIGHTS['thresholds']['volume_health_pct'] == 80.0


def test_validate_weights():
    from ashare_review.one_two_v2.weights import validate_weights, DEFAULT_WEIGHTS
    assert validate_weights(DEFAULT_WEIGHTS) == []
    bad = dict(DEFAULT_WEIGHTS)
    bad['dimensions'] = dict(bad['dimensions'], quality=60)
    assert any('quality' in e for e in validate_weights(bad))
    bad2 = dict(DEFAULT_WEIGHTS)
    bad2['thresholds'] = dict(bad2['thresholds'], auction_ratio_high=-1)
    assert any('auction_ratio_high' in e for e in validate_weights(bad2))
    bad3 = {'dimensions': {}, 'thresholds': {}}
    assert validate_weights(bad3) != []


def test_weight_store(tmp_path):
    from ashare_review.one_two_v2.weights import WeightStore
    path = str(tmp_path / 'w.json')
    s = WeightStore(path)
    assert s.get()['dimensions']['quality'] == 30
    s.set({'dimensions': {'quality': 40}, 'thresholds': {'auction_ratio_low': 2.0}})
    s2 = WeightStore(path)
    w = s2.get()
    assert w['dimensions']['quality'] == 40
    assert w['thresholds']['auction_ratio_low'] == 2.0
    bad = tmp_path / 'bad.json'
    bad.write_text('{broken', encoding='utf-8')
    assert WeightStore(str(bad)).get()['dimensions']['quality'] == 30
