"""启动突破 V3 — 情绪周期闸门 + 小票加分 单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _p(monkeypatch, gate):
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    monkeypatch.setattr(p, '_get_market_state',
                        lambda trade_date=None: {'is_bull': True, 'sh_close': 3000, 'sh_ma60': 2900})
    monkeypatch.setattr(p, '_emotion_gate', lambda: gate)
    return p


def _cand(code, mcap=None):
    c = {'code': code, 'close': 10.2, 'top_line': 10.0, 'dist_pct': 2.0,
         'prev_close': 9.9, 'vol': 100, 'mavol180': 50, 'vol_ratio': 2.0,
         'limit_count': 12, 'fib_state': 'ok', 'ma13': 9.8}
    if mcap is not None:
        c['float_mcap_yi'] = mcap
    return c


def test_gate_blocks_on_ebb():
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    p = _p(monkeypatch, {'block': True, 'best': False, 'raise_bar': False,
                         'regime': '退潮下跌', 'emotion': '低迷'})
    assert p._scan_buy_signals([_cand('600001')], mode='v3') == []


def test_gate_best_adds_score_and_raise_bar_threshold():
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    # 最佳档 +5 分
    p = _p(monkeypatch, {'block': False, 'best': True, 'raise_bar': False,
                         'regime': '强势趋势', 'emotion': '活跃'})
    sigs = p._scan_buy_signals([_cand('600001')], mode='v3')
    assert sigs and sigs[0]['score'] >= 65
    # 修复档：门槛 75，低分被剔除
    p2 = _p(monkeypatch, {'block': False, 'best': False, 'raise_bar': True,
                          'regime': '震荡观望', 'emotion': '低迷'})
    low = _cand('600002')
    low['vol_ratio'] = 1.3          # 基础分 70 < 75 → 被修复档门槛拦掉
    assert p2._scan_buy_signals([low], mode='v3') == []


def test_small_cap_bonus():
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    g = {'block': False, 'best': False, 'raise_bar': False, 'regime': '震荡观望', 'emotion': '温和'}
    p = _p(monkeypatch, g)
    s_small = p._scan_buy_signals([_cand('600001', mcap=50.0)], mode='v3')[0]['score']
    p2 = _p(monkeypatch, g)
    s_big = p2._scan_buy_signals([_cand('600002', mcap=200.0)], mode='v3')[0]['score']
    assert s_small == s_big + 8
