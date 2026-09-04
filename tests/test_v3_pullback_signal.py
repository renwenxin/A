"""启动突破 V3 — 回踩低吸信号 单元测试

背景: 用户教学 —— 三条件同时满足才上车:
  1. 板块有中军: 板块内有成交额>20亿且趋势向上的票
  2. 个股在回踩: 股价在 5/10/20 日线附近(乖离<3%)
  3. 缩量企稳:   量<前5日均量60% 且出现下影线/底分型
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk_latest(closes, volumes, opens=None, highs=None, lows=None):
    n = len(closes)
    opens = opens or [closes[i] for i in range(n)]
    highs = highs or [max(opens[i], closes[i]) * 1.01 for i in range(n)]
    lows = lows or [min(opens[i], closes[i]) * 0.99 for i in range(n)]
    vol_ma5_prev = sum(volumes[-6:-1]) / 5.0 if n >= 6 else 0
    def _ma(p):
        return sum(closes[-p:]) / p if n >= p else None
    return {
        'close': closes[-1],
        'prev_close': closes[-2] if n >= 2 else closes[-1],
        'vol': volumes[-1],
        'mavol180': sum(volumes[-180:]) / min(len(volumes), 180),
        'ma5': _ma(5), 'ma10': _ma(10), 'ma20': _ma(20),
        'ma13': _ma(13), 'ma34': _ma(34), 'ma55': _ma(55), 'ma89': _ma(89),
        'vol_ma5_prev': round(vol_ma5_prev, 0),
        'tail': [
            {'o': opens[-3], 'h': highs[-3], 'l': lows[-3], 'c': closes[-3]},
            {'o': opens[-2], 'h': highs[-2], 'l': lows[-2], 'c': closes[-2]},
            {'o': opens[-1], 'h': highs[-1], 'l': lows[-1], 'c': closes[-1]},
        ] if n >= 3 else [],
    }


def test_pullback_metrics_dist_min():
    """回踩指标: 距 5/10/20 均线最近者乖离。"""
    from ashare_review.tools.sim_portfolio import pullback_metrics
    # MA5=10.01 / MA10=10.005 / MA20=10.0 → 距最近均线(MA20) 0.5%（<3 命中回踩）
    closes = [10.0] * 20 + [10.05]
    latest = _mk_latest(closes, volumes=[100] * 21)
    m = pullback_metrics(latest)
    assert m['dist_min_pct'] < 3.0
    # close=11.0，距所有均线 > 5% → 非回踩
    closes2 = [10.0] * 20 + [11.0]
    m2 = pullback_metrics(_mk_latest(closes2, volumes=[100] * 21))
    assert m2['dist_min_pct'] > 5.0


def test_sector_zhongjun_requires_20yi_and_up():
    """板块中军: 板块内存在 成交额≥20亿 且 当日涨幅≥5% 的票。"""
    from ashare_review.tools.sim_portfolio import sector_has_zhongjun
    members = ['600001', '600002']
    rows = {
        '600001': {'chg': 3.0, 'amount': 30_000_000_000},   # 涨幅不足
        '600002': {'chg': 6.5, 'amount': 500_000_000},     # 金额不足(5亿<20亿)
    }
    assert sector_has_zhongjun(members, rows) is False
    rows2 = {'600001': {'chg': 6.0, 'amount': 30_000_000_000}}
    assert sector_has_zhongjun(members, rows2) is True


def test_zhongjun_sector_of_code_industry_primary_concept_fallback():
    """板块归属: 有行业用行业；行业缺失时用概念命中算中军。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio, zhongjun_sector_of
    imap = {'600001': '半导体', '600002': '半导体'}
    cmap = {'AI算力': {'members': {'600003': 1, '600004': 1}},
            '半导体': {'members': {'600001': 1, '600002': 1}}}
    # spot: 600002 是半导体中军(≥20亿+大阳)
    rows = {'600002': {'chg': 6.0, 'amount': 30_000_000_000}}
    # 600001 有行业 → 用行业组判定 → True
    assert zhongjun_sector_of('600001', imap, cmap, rows) is True
    # 600003 无行业 → 概念 'AI算力' 无中军 → False
    assert zhongjun_sector_of('600003', imap, cmap, rows) is False
    # 600004 无行业 → 概念 'AI算力' 无中军（但若同概念 600003 放量也 false 保持）
    rows2 = {'600003': {'chg': 6.0, 'amount': 30_000_000_000}}
    assert zhongjun_sector_of('600004', imap, cmap, rows2) is True
    # 数据缺失: spot 空 → None（放行）
    assert zhongjun_sector_of('600001', imap, cmap, {}) is None


def test_zhongjun_cache_zero_amount_snapshot_treats_as_missing():
    """降级快照(成交额全0) → 视同数据缺失，不判 False、不写盘（fail-open）。"""
    import pandas as pd
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    from ashare_review.data.akshare_fetcher import AkshareFetcher

    p = Vol180SimPortfolio()
    p._imap = {'600001': '半导体'}
    p._cmap = {'半导体': {'members': {'600001': 1}}}
    fake = pd.DataFrame([
        {'代码': '600001', '涨跌幅': 6.0, '成交额': 0},   # 回退快照: 成交额全 0
    ])
    orig = AkshareFetcher.get_spot_df
    AkshareFetcher.get_spot_df = lambda self: fake
    try:
        out = p._zhongjun_cache('2099-01-01', ['600001'])
    finally:
        AkshareFetcher.get_spot_df = orig
    assert out == {}          # 无判定结果 → 调用方按 None(数据缺失) 放行


def test_pullback_signal_all_three_conditions():
    """回踩低吸: 三条件 AND（回踩+缩量企稳+中军）命中。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio, pullback_signal_pass
    latest = {
        'close': 10.05,
        'ma5': 10.4, 'ma10': 10.3, 'ma20': 10.0,   # 距 MA20 0.5%
        'vol': 40, 'vol_ma5_prev': 100,             # 量=前5日均量40%
        'tail': [
            {'o': 10.2, 'h': 10.4, 'l': 10.0, 'c': 10.1},
            {'o': 10.1, 'h': 10.2, 'l': 10.0, 'c': 10.05},
            {'o': 10.06, 'h': 10.2, 'l': 9.8, 'c': 10.05},  # 下影 0.26≥实体0.01*0.5
        ],
    }
    sig = pullback_signal_pass(latest, zhongjun=True)
    assert sig is not None and sig['zhongjun'] is True
    # 无中军 → 不命中
    assert pullback_signal_pass(latest, zhongjun=False) is None
    # 放量 → 不命中
    latest2 = dict(latest, vol=90)
    assert pullback_signal_pass(latest2, zhongjun=True) is None


def test_pullback_signal_zhongjun_missing_data_allows():
    """中军数据缺失(spot 空) → 不拦截（与 daily_pool 对无行业数据放行一致）。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import pullback_signal_pass
    latest = {
        'close': 10.05, 'ma5': 10.4, 'ma10': 10.3, 'ma20': 10.0,
        'vol': 40, 'vol_ma5_prev': 100,
        'tail': [
            {'o': 10.2, 'h': 10.4, 'l': 10.0, 'c': 10.1},
            {'o': 10.1, 'h': 10.2, 'l': 10.0, 'c': 10.05},
            {'o': 10.06, 'h': 10.2, 'l': 9.8, 'c': 10.05},
        ],
    }
    sig = pullback_signal_pass(latest, zhongjun=None)   # None=数据缺失
    assert sig is not None and sig['zhongjun'] is None
