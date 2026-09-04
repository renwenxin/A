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
    from ashare_review.tools.sim_portfolio import pullback_signal_pass
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


# ---------- 引擎接线: pullback_signal_pass dist=0 / _build_pullback_list / refresh ----------

def _pullback_latest_row(code, close=10.05, vol=40):
    """最新行情行: close 距 MA20 0.5%，缩量 40%，末根带下影线 → 三条件可命中。"""
    return {
        'code': code, 'name': '测' + code, 'close': close, 'vol': vol,
        'top_line': 12.0, 'dist_pct': -16.0, 'vol_ratio': 0.4,
        'limit_count': 15, 'fib_state': 'ok',
        'ma5': 10.4, 'ma10': 10.3, 'ma20': 10.0,   # 距 MA20 0.5%
        'vol_ma5_prev': 100,
        'tail': [
            {'o': 10.2, 'h': 10.4, 'l': 10.0, 'c': 10.1},
            {'o': 10.1, 'h': 10.2, 'l': 10.0, 'c': 10.05},
            {'o': 10.06, 'h': 10.2, 'l': 9.8, 'c': 10.05},  # 下影线
        ],
    }


def test_pullback_signal_dist_zero_allowed():
    """评审跟进(Important): 收盘恰在均线上(dist_min_pct=0.0)是回踩，
    不被旧的 falsy 守卫(not m.get('dist_min_pct'))误杀。"""
    from ashare_review.tools.sim_portfolio import pullback_signal_pass
    latest = {
        'close': 10.0, 'ma5': 10.4, 'ma10': 10.3, 'ma20': 10.0,  # 恰在 MA20 上 → 0%
        'vol': 40, 'vol_ma5_prev': 100,
        'tail': [
            {'o': 10.2, 'h': 10.4, 'l': 10.0, 'c': 10.1},
            {'o': 10.1, 'h': 10.2, 'l': 10.0, 'c': 10.05},
            {'o': 10.06, 'h': 10.2, 'l': 9.8, 'c': 10.0},   # 下影 0.2 ≥ 实体 0.06×0.5
        ],
    }
    sig = pullback_signal_pass(latest, zhongjun=True)
    assert sig is not None
    assert sig['metrics']['dist_min_pct'] == 0.0
    assert sig['reasons'][0].startswith('回踩MA(乖离0.0%')


def test_build_pullback_list_filters_and_sorts():
    """引擎接线: 过滤 rest/无中军/不满足三条件；zhongjun=True 排前；字段透传。"""
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    # zhongjun: 600001 True, 600002 False, 600003 None(数据缺失), 600004 True 但不回踩
    p._zhongjun_cache = lambda trade_date, codes: {
        '600001': True, '600002': False, '600003': None, '600004': True,
    }
    rows = [
        _pullback_latest_row('600003'),                              # None 放行 → 命中
        _pullback_latest_row('600001'),                              # True → 命中
        _pullback_latest_row('600002'),                              # False → 排除
        dict(_pullback_latest_row('600004'), fib_state='rest'),      # 休息 → 排除
        dict(_pullback_latest_row('600005'), vol=90),                # 放量 → 排除
        dict(_pullback_latest_row('600006'), close=11.0),            # 乖离>3% → 排除
    ]
    out = p._build_pullback_list(rows, '2026-09-04')
    codes = [x['code'] for x in out]
    assert codes[0] == '600001'          # True 排前
    assert '600003' in codes             # None(数据缺失) 放行
    assert '600002' not in codes
    assert '600004' not in codes         # rest
    assert '600005' not in codes         # 放量
    assert '600006' not in codes         # 非回踩
    first = out[0]
    for k in ('code', 'name', 'close', 'ma5', 'ma10', 'ma20', 'fib_state',
              'dist_min_pct', 'vol_ratio_5', 'zhongjun',
              'has_lower_shadow', 'has_bottom_fractal', 'reasons'):
        assert k in first
    assert first['zhongjun'] is True and first['has_lower_shadow'] is True


def _empty_state():
    """refresh_daily_status 所需的最小 state（离线、确定性，不依赖磁盘状态文件）。"""
    return {
        'watch': {}, 'ready': {}, 'holding': {}, 'finished': {},
        'pending_sell': {}, 'pullback': [],
        'last_risk': {'breaker_tripped': False},
        'cash': 1_000_000.0, 'initial_capital': 1_000_000.0,
        'portfolio_history': [], 'last_update': '', 'cache_date': '',
        'total_trades': 0, 'total_wins': 0,
        'max_positions': 10, 'per_position_pct': 0.10,
    }


def _fake_latest(code, rest=False):
    """_read_latest 假输出：refresh 需要 close/vol/mavol180/fib均线/缩量均量/tail。

    rest=True 时 ma89 抬高到 close 之上 → fib_state=rest。
    """
    row = _pullback_latest_row(code)
    row.update({
        'prev_close': 10.0,
        'mavol180': 300.0,
        'ma13': 9.9, 'ma34': 9.8, 'ma55': 9.7,
        'ma89': 11.0 if rest else 9.6,      # rest: close 10.05 < 11.0
    })
    return row


def _patch_refresh_io(p, latest_map):
    """monkeypatch refresh_daily_status 的 IO 侧：行情/市场/中军/持久化全部离线。"""
    p._read_latest = lambda code: latest_map.get(code)
    p._get_market_state = lambda trade_date=None: {'is_bull': True, 'sh_close': 3000, 'sh_ma60': 2900}
    p._zhongjun_cache = lambda trade_date, codes: {c: True for c in (codes or [])}
    p._save = lambda: None


def test_refresh_daily_status_merges_fib_and_rebuilds_pullback():
    """引擎接线: refresh Step1 用最新行并入 fib/ma 字段、rest 行剔除、回踩列表重建（离线）。"""
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state = _empty_state()
    p._state['watch'] = {
        # 旧 fib_state=caution → 按最新价重算为 ok；top_line 10.2 → dist -1.5% 保留
        '600001': {'code': '600001', 'name': '测600001', 'top_line': 10.2,
                   'close': 10.5, 'fib_state': 'caution'},
        # 旧 fib_state=ok → 最新价破 MA89(rest) → 剔除
        '600002': {'code': '600002', 'name': '测600002', 'top_line': 10.5,
                   'close': 10.4, 'fib_state': 'ok'},
    }
    _patch_refresh_io(p, {
        '600001': _fake_latest('600001', rest=False),
        '600002': _fake_latest('600002', rest=True),
    })
    p.refresh_daily_status('2026-09-04')
    # rest 行剔除；命中行保留且 fib/均线字段来自最新行（非旧 seed）
    assert '600002' not in p._state['watch']
    assert '600001' in p._state['watch']
    w = p._state['watch']['600001']
    assert w['fib_state'] == 'ok'
    assert w['ma20'] == 10.0 and w['ma89'] == 9.6
    # pullback 重建: 三条件命中 + 中军 True
    pb = p._state['pullback']
    assert pb and pb[0]['code'] == '600001'
    assert pb[0]['zhongjun'] is True
    assert pb[0]['fib_state'] == 'ok'


def test_refresh_daily_status_clears_pullback_when_watch_empties():
    """引擎接线(评审 Minor#4): rest 剔除把观察池清空 → 清掉旧的回踩列表。"""
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state = _empty_state()
    p._state['watch'] = {
        '600002': {'code': '600002', 'name': '测600002', 'top_line': 10.5,
                   'close': 10.4, 'fib_state': 'ok'},
    }
    p._state['pullback'] = [{'code': '600002', 'stale': True}]   # 旧信号，观察池清空应被清掉
    _patch_refresh_io(p, {'600002': _fake_latest('600002', rest=True)})
    p.refresh_daily_status('2026-09-04')
    assert p._state['watch'] == {}
    assert p._state['pullback'] == []


def test_run_daily_rebuilds_watch_and_pullback():
    """引擎接线(评审 Minor): run_daily 观察池行透传 fib/ma/tail 字段；
    pullback 由 watch_list + already_broken 重建（含已突破行，离线）。

    run_daily 从 screen_candidates 取行: watching → 观察池；breakout → already_broken，
    pullback = _build_pullback_list(watch_list + already_broken)。
    """
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state = _empty_state()
    # screen_candidates 假输出:
    #   600001 观察中 + 回踩三条件命中(watching)
    #   600002 已突破(close 10.05 > top 9.9) 但同样回踩命中(breakout) → 应进 pullback 不进观察池
    #   600005 观察中但放量(vol=90) → 三条件不命中
    cand_600001 = dict(_fake_latest('600001'), status='watching')
    cand_600002 = dict(_fake_latest('600002'), top_line=9.9, dist_pct=1.5,
                       status='breakout')
    cand_600005 = dict(_fake_latest('600005'), vol=90, status='watching')
    cands = [cand_600001, cand_600002, cand_600005]
    p.screen_candidates = lambda trade_date: cands
    p._scan_buy_signals = lambda candidates, mode='v1': []     # 本测试只验接线，不测买点
    p._get_name = lambda code: {'600001': '测600001', '600002': '测600002',
                                '600005': '测600005'}.get(code, code)
    _patch_refresh_io(p, {})     # _get_market_state(bull) / _zhongjun_cache(全True) / _save
    p.run_daily('2026-09-04')    # 周五交易日，不走周末提前返回

    # 观察池重建: 只有 watching 行，且 fib/ma/tail 等候选字段原样透传
    assert set(p._state['watch']) == {'600001', '600005'}
    w = p._state['watch']['600001']
    assert w['update_date'] == '2026-09-04'
    for k in ('code', 'close', 'prev_close', 'top_line', 'dist_pct', 'vol',
              'mavol180', 'vol_ratio', 'limit_count', 'fib_state',
              'ma5', 'ma10', 'ma20', 'ma13', 'ma34', 'ma55', 'ma89',
              'vol_ma5_prev', 'tail'):
        assert w[k] == cand_600001[k], f'watch 透传失败: {k}'

    # pullback 重建: watching 命中 + already_broken 命中都进列表；放量行排除
    pb = p._state['pullback']
    assert pb, 'pullback 应被重建（有回踩命中候选）'
    pb_codes = {x['code'] for x in pb}
    assert pb_codes == {'600001', '600002'}
    assert '600002' in pb_codes          # already_broken 行也参与回踩扫描
    assert all(x['zhongjun'] is True for x in pb)
    assert all(x['fib_state'] == 'ok' for x in pb)
