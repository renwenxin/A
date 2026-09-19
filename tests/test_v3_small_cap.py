"""启动突破 V3 — 小票过滤 单元测试（逻辑哥五打法一：只做小票）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_float_mcap_yi():
    from ashare_review.tools.sim_portfolio import float_mcap_yi
    # 流通股本 1e7 手 = 1e9 股；收盘 10 元 → 100 亿
    assert float_mcap_yi(1e7, 10.0) == 100.0
    assert float_mcap_yi(1e7, 5.0) == 50.0
    # 缺数据 → None（调用方放行）
    assert float_mcap_yi(None, 10.0) is None
    assert float_mcap_yi(1e7, None) is None
    assert float_mcap_yi(0, 10.0) is None
    # 非有限值 → None（契约不漏 NaN/inf，未来调用方也安全）
    assert float_mcap_yi(float('nan'), 10.0) is None
    assert float_mcap_yi(1e7, float('inf')) is None


def _portfolio(monkeypatch, pool, latest_map, hands_map=None):
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    monkeypatch.setattr(p, '_get_eligible_pool', lambda: pool)
    monkeypatch.setattr(p, '_read_latest', lambda code: latest_map.get(code))
    monkeypatch.setattr(p, '_capital_hands', lambda code: (hands_map or {}).get(code))
    return p


def test_screen_candidates_small_cap_filter():
    """>100 亿剔除；≤100 亿保留且带 float_mcap_yi；市值未知放行。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    pool = [
        {'code': '600001', 'pressure': 11.0, 'limit_count': 12, 'name': '小票'},
        {'code': '600002', 'pressure': 11.0, 'limit_count': 12, 'name': '大票'},
        {'code': '600003', 'pressure': 11.0, 'limit_count': 12, 'name': '未知'},
    ]
    latest = {
        '600001': {'close': 10.0, 'vol': 100, 'mavol180': 50, 'prev_close': 9.8,
                   'ma13': 9.5, 'ma34': 9.0, 'ma55': 8.5, 'ma89': 8.0},
        '600002': {'close': 10.0, 'vol': 100, 'mavol180': 50, 'prev_close': 9.8,
                   'ma13': 9.5, 'ma34': 9.0, 'ma55': 8.5, 'ma89': 8.0},
        '600003': {'close': 10.0, 'vol': 100, 'mavol180': 50, 'prev_close': 9.8,
                   'ma13': 9.5, 'ma34': 9.0, 'ma55': 8.5, 'ma89': 8.0},
    }
    # 600001: 1e7手×100×10 = 100亿（临界，保留）
    # 600002: 3e7手×100×10 = 300亿（剔除）
    # 600003: 无股本数据 → 放行
    hands = {'600001': 1e7, '600002': 3e7}
    p = _portfolio(monkeypatch, pool, latest, hands)
    cands = p.screen_candidates('2026-09-01')
    by = {c['code']: c for c in cands}
    assert '600001' in by and by['600001']['float_mcap_yi'] == 100.0
    assert '600002' not in by
    assert '600003' in by and by['600003']['float_mcap_yi'] is None
