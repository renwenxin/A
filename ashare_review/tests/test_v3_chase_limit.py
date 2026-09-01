"""启动突破 V3 追高上限 — 单元测试

背景: 用户反馈 V3 选出的买点票"已突破压力位 >10%"(追高)。
根因: _scan_buy_signals 对已突破(dist_pct>0)的票无突破幅度上限,
      赤天化 break_pct 45% 也入选买点。
教学规则(逻辑哥视频 + 页面模板):
  - 视频: "八个点以下才做" · "突破压力位>10%的追高不做"
  - 模板/V4 口径: 10cm ≤6% / 20cm ≤8% / 30cm ≤30%
修复: screen_candidates + _scan_buy_signals + v3_backtest + V3 screener
      增加追高上限过滤; 压力位下方蓄势(0~10%)的票保留。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- 1. 追高上限按板块 ----------

def test_chase_limit_pct_by_board():
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    assert Vol180SimPortfolio._chase_limit_pct('600001') == 6.0    # 10cm 主板
    assert Vol180SimPortfolio._chase_limit_pct('000001') == 6.0    # 10cm 主板
    assert Vol180SimPortfolio._chase_limit_pct('300001') == 8.0    # 20cm 创业板
    assert Vol180SimPortfolio._chase_limit_pct('688001') == 8.0    # 20cm 科创板
    assert Vol180SimPortfolio._chase_limit_pct('830001') == 30.0   # 30cm 北交所


def test_backtest_chase_limit_matches_sim():
    """回测引擎与实盘引擎追高上限一致（口径统一）"""
    from ashare_review.analysis.v3_backtest import V3Backtest
    assert V3Backtest._chase_limit_pct('600001') == 6.0
    assert V3Backtest._chase_limit_pct('300001') == 8.0
    assert V3Backtest._chase_limit_pct('830001') == 30.0


# ---------- 2. screen_candidates: 追高排除 / 蓄势保留 ----------

def _portfolio_with_fake_io(monkeypatch, pool, latest_map):
    """构造 Vol180SimPortfolio，monkeypatch 候选池与行情读取。"""
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    monkeypatch.setattr(p, '_get_eligible_pool', lambda: pool)
    monkeypatch.setattr(p, '_read_latest', lambda code: latest_map.get(code))
    return p


def test_screen_candidates_keeps_below_pressure_and_just_broken():
    """压力位下方蓄势(0~-10%)保留; 刚突破(≤6%)保留; 突破>6%排除"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    pool = [
        {'code': '600001', 'pressure': 10.0, 'limit_count': 12, 'name': '蓄势股'},
        {'code': '600002', 'pressure': 10.0, 'limit_count': 12, 'name': '刚突破'},
        {'code': '600003', 'pressure': 10.0, 'limit_count': 12, 'name': '追高股'},
        {'code': '600004', 'pressure': 10.0, 'limit_count': 12, 'name': '下方太远'},
    ]
    latest = {
        '600001': {'close': 9.5, 'vol': 100, 'mavol180': 50},    # -5% 蓄势
        '600002': {'close': 10.5, 'vol': 100, 'mavol180': 50},   # +5% 刚突破
        '600003': {'close': 10.9, 'vol': 100, 'mavol180': 50},   # +9% 追高 → 排除
        '600004': {'close': 8.9, 'vol': 100, 'mavol180': 50},    # -11% 下方太远 → 排除
    }
    p = _portfolio_with_fake_io(monkeypatch, pool, latest)
    cands = p.screen_candidates('2026-09-01')
    codes = {c['code']: c for c in cands}
    assert '600001' in codes and codes['600001']['status'] == 'watching'
    assert '600002' in codes and codes['600002']['status'] == 'breakout'
    assert '600003' not in codes    # 追高 >6% 排除
    assert '600004' not in codes    # 下方超过10% 排除


def test_scan_buy_signals_skips_over_chase():
    """买点信号: 已突破>6%的票不产生买点; 刚突破(≤6%)保留"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    monkeypatch.setattr(p, '_get_market_state', lambda trade_date=None: {'is_bull': True, 'sh_close': 3000, 'sh_ma60': 2900})
    cands = [
        {'code': '600002', 'close': 10.55, 'top_line': 10.0, 'dist_pct': 5.5,  # +5.5% 刚突破(避开死亡区间3-5)
         'vol': 100, 'mavol180': 50, 'vol_ratio': 2.0, 'limit_count': 12},
        {'code': '600003', 'close': 10.9, 'top_line': 10.0, 'dist_pct': 9.0,  # +9% 追高
         'vol': 100, 'mavol180': 50, 'vol_ratio': 2.0, 'limit_count': 12},
        {'code': '600001', 'close': 9.5, 'top_line': 10.0, 'dist_pct': -5.0,  # -5% 蓄势(未突破,不放量不算信号)
         'vol': 100, 'mavol180': 50, 'vol_ratio': 2.0, 'limit_count': 12},
    ]
    sigs = p._scan_buy_signals(cands, mode='v3')
    codes = {s['code'] for s in sigs}
    assert '600002' in codes    # 刚突破保留
    assert '600003' not in codes  # 追高排除
    assert '600001' not in codes  # 未突破(close<top_line)不触发


def test_screen_candidates_gem_limit():
    """20cm 票追高上限 8%: +7% 保留, +9% 排除"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    pool = [{'code': '300001', 'pressure': 10.0, 'limit_count': 12, 'name': '创A'}]
    latest = {
        '300001': {'close': 10.7, 'vol': 100, 'mavol180': 50},   # +7% ≤8% 保留
    }
    p = _portfolio_with_fake_io(monkeypatch, pool, latest)
    cands = p.screen_candidates('2026-09-01')
    assert any(c['code'] == '300001' for c in cands)
    # +9% > 8% 排除
    latest['300001'] = {'close': 10.9, 'vol': 100, 'mavol180': 50}
    p2 = _portfolio_with_fake_io(monkeypatch, pool, latest)
    assert not any(c['code'] == '300001' for c in p2.screen_candidates('2026-09-01'))


# ---------- 3. V3 screener: 真实前高追高排除 ----------

def test_screener_excludes_real_high_chase():
    """V3 筛选器: close 相对前60日最高高点(排除当日)突破>8% → 排除"""
    import pytest, pandas as pd
    from datetime import datetime, timedelta
    from ashare_review.screening.five_indicator import StartBreakoutScreenerV3

    from ashare_review.analysis.indicators import enrich_all

    def make_df(closes, highs):
        n = len(closes)
        dates = [datetime(2026, 6, 1) + timedelta(days=i) for i in range(n)]
        df = pd.DataFrame({
            'trade_date': dates,
            'open': closes, 'high': highs, 'low': [c * 0.98 for c in closes],
            'close': closes, 'volume': [1000] * n,
        })
        return enrich_all(df)   # 模拟 _read_stock（swl/sws/均线等）

    import ashare_review.screening.five_indicator as fi_module
    monkeypatch = pytest.MonkeyPatch()
    # 隔离 SWL 变量（本测试只验证追高排除逻辑）
    monkeypatch.setattr(fi_module, 'calc_swl_sws',
                        lambda df: df.assign(swl=1.0, sws=0.5, swl_control=True))

    s = StartBreakoutScreenerV3(None, None)
    s._get_sector = lambda code: ''
    s._get_name = lambda code: ''
    s._count_limit_ups = lambda code: 15   # 主板活跃
    # 找顶线 mock 在股价上方 ~5.5%（DRAWLINE 外推虚高假象：看似蓄势，实则已突破真实前高）
    s._find_resistance_line = lambda df, idx, lookback=60: float(df['close'].iloc[idx]) * 1.055

    def run_check(close_val, high_prev):
        """构造 df + patch _read_stock → 调 _check_v2"""
        closes = [5.0] * 59 + [close_val]
        highs = [5.0] * 58 + [high_prev, close_val]
        df = make_df(closes, highs)
        monkeypatch.setattr(fi_module, '_read_stock', lambda tdx, code, trade_date=None: df)
        info = {'name': '追高股', 'code': '600001', 'is_zt': False, 'board_type': '7%+',
                'consecutive': 0, 'float_market_cap': 0}
        return s._check_v2('600001', info, '20260901')

    # 前60日高点 10.0，当前 close 10.9 → 已突破真实前高 9% > 8% → 排除
    r = run_check(10.9, 10.0)
    assert r is None  # 已突破真实前高 9% → 排除

    # 当前 close 10.3 → 已突破真实前高 3% ≤ 8% → 保留（刚突破可做）
    r2 = run_check(10.3, 10.0)
    assert r2 is not None and 0 <= r2.detail.get('chase_pct', 99) <= 8.0

    # 当前 close 9.5 → 未突破（下方蓄势）→ 保留
    r3 = run_check(9.5, 10.0)
    assert r3 is not None and r3.detail.get('chase_pct', 99) < 0
