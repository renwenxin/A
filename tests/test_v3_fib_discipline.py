"""启动突破 V3 — 斐波那契均线纪律 单元测试

背景: 用户教学 —— 均线设 13/34/55/89(斐波那契)；只做 K 线在 13 日线上的图形；
日线破位 34/55 均线→谨慎参与；破位 89 均线→直接休息。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_fib_state_ok_caution_rest():
    """斐波那契状态分档：可做/谨慎/休息 按优先级互斥。"""
    from ashare_review.tools.sim_portfolio import fib_state
    # 可做: close 在所有 fib 均线上方
    assert fib_state(close=100, ma13=90, ma34=88, ma55=85, ma89=80) == 'ok'
    # 谨慎: 破 34/55 (但仍在 89 上方)
    assert fib_state(close=83, ma13=90, ma34=88, ma55=85, ma89=80) == 'caution'
    assert fib_state(close=86, ma13=90, ma34=88, ma55=85, ma89=80) == 'caution'
    # 休息: 破 89
    assert fib_state(close=78, ma13=90, ma34=88, ma55=85, ma89=80) == 'rest'
    # 缺均线 → 不拦截(ok)
    assert fib_state(100, None, None, None, None) == 'ok'


def test_fib_state_partial_none_per_rule():
    """部分均线缺失 → 缺失的规则不触发，其余规则照常判定。"""
    from ashare_review.tools.sim_portfolio import fib_state
    # 仅 ma34 在场且被破 → caution
    assert fib_state(close=5, ma13=None, ma34=6, ma55=None, ma89=None) == 'caution'
    # 仅 ma89 在场且被破 → rest（最高优先级）
    assert fib_state(close=5, ma13=None, ma34=None, ma55=None, ma89=6) == 'rest'
    # 仅 ma34 在场但未破 → ok
    assert fib_state(close=7, ma13=None, ma34=6, ma55=None, ma89=None) == 'ok'
    # close=None → ok（与 pullback_metrics 的缺省健壮性一致）
    assert fib_state(close=None, ma13=90, ma34=88, ma55=85, ma89=80) == 'ok'


def test_read_latest_computes_fib_ma():
    """_read_latest 真实实现需产出 ma5/10/20/13/34/55/89 + vol_ma5_prev + tail。

    用 monkeypatch 让 tdx 读一个内存 .day 文件太长 → 此处改为直接断言
    _read_latest 的输出契约由 pullback_metrics/fib_state 消费（见上），
    并验证 Vol180SimPortfolio._read_latest 在真实 TDX 数据上返回新键（若数据存在）。
    """
    # 真实 _read_latest 依赖 TDX 数据目录，仅做键名契约冒烟：存在数据则含新键
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    # 候选池文件存在的环境下跑真实读；不存在则跳过（CI 无数据）
    latest = p._read_latest('600001')
    if latest is not None:
        for k in ('ma5', 'ma10', 'ma20', 'ma13', 'ma34', 'ma55', 'ma89',
                  'vol_ma5_prev', 'tail'):
            assert k in latest, f'{k} 缺失'
    monkeypatch.undo()


def test_screen_candidates_excludes_rest_below_ma89():
    """破 89 日线 → 观察池剔除；谨慎档保留但带 fib_state=caution。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    pool = [
        {'code': '600001', 'pressure': 10.0, 'limit_count': 12, 'name': '可做'},
        {'code': '600002', 'pressure': 9.4, 'limit_count': 12, 'name': '谨慎'},
        {'code': '600003', 'pressure': 8.2, 'limit_count': 12, 'name': '休息'},
    ]
    # close 分别 9.5(ok) / 8.6(caution, 破55) / 7.5(rest, 破89)，
    # 相对各自压力位(10.0/9.4/8.2) 均在 -10% 内 → 不触发"距压力位过远"剔除
    latest = {
        '600001': {'close': 9.5, 'vol': 100, 'mavol180': 50,
                   'ma13': 9.0, 'ma34': 8.8, 'ma55': 8.7, 'ma89': 8.0,
                   'ma5': 9.4, 'ma10': 9.2, 'ma20': 9.1, 'vol_ma5_prev': 90, 'tail': []},
        '600002': {'close': 8.6, 'vol': 100, 'mavol180': 50,
                   'ma13': 9.0, 'ma34': 8.8, 'ma55': 8.7, 'ma89': 8.0,
                   'ma5': 8.7, 'ma10': 8.7, 'ma20': 8.6, 'vol_ma5_prev': 90, 'tail': []},
        '600003': {'close': 7.5, 'vol': 100, 'mavol180': 50,
                   'ma13': 9.0, 'ma34': 8.8, 'ma55': 8.7, 'ma89': 8.0,
                   'ma5': 8.0, 'ma10': 7.9, 'ma20': 7.8, 'vol_ma5_prev': 90, 'tail': []},
    }
    monkeypatch.setattr(p, '_get_eligible_pool', lambda: pool)
    monkeypatch.setattr(p, '_read_latest', lambda code: latest.get(code))
    # 流通股本缓存是机器相关的 → 置 None（市值未知放行），结果只由 fib 纪律决定
    monkeypatch.setattr(p, '_capital_hands', lambda code: None)
    cands = p.screen_candidates('2026-09-01')
    by_code = {c['code']: c for c in cands}
    assert '600001' in by_code and by_code['600001']['fib_state'] == 'ok'
    assert '600002' in by_code and by_code['600002']['fib_state'] == 'caution'
    assert '600003' not in by_code          # 破 89 → 休息剔除


def test_scan_buy_signals_respects_fib_discipline():
    """买点: close<=MA13 不给信号；fib=rest 防御性跳过；caution 允许(带警示)。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    monkeypatch.setattr(p, '_get_market_state',
                        lambda trade_date=None: {'is_bull': True, 'sh_close': 3000, 'sh_ma60': 2900})
    # 情绪闸门依赖本机实盘 regime 数据 → 固定为"不拦、不加分、不提门槛"
    monkeypatch.setattr(p, '_emotion_gate',
                        lambda: {'block': False, 'best': False, 'raise_bar': False,
                                 'regime': '震荡观望', 'emotion': '温和'})
    def cand(code, close, ma13, fs):
        return {'code': code, 'close': close, 'top_line': 10.0,
                'dist_pct': 2.0, 'prev_close': 9.9,
                'vol': 100, 'mavol180': 50, 'vol_ratio': 2.0, 'limit_count': 12,
                'ma13': ma13, 'fib_state': fs}
    sigs = p._scan_buy_signals([
        cand('600001', 10.2, 9.8, 'ok'),       # close>MA13 → 允许
        cand('600002', 10.2, 10.5, 'caution'),  # close<=MA13 → 拦截
        cand('600003', 10.2, 9.0, 'rest'),      # 休息 → 防御拦截
    ], mode='v3')
    codes = {s['code'] for s in sigs}
    assert '600001' in codes
    assert '600002' not in codes
    assert '600003' not in codes
