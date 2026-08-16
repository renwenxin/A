"""策略验证台单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Task 1: 统一指标 ----------

def _trades():
    return [
        {'entry_date': '20260810', 'exit_date': '20260811', 'return_pct': 10.0},
        {'entry_date': '20260811', 'exit_date': '20260812', 'return_pct': -5.0},
        {'entry_date': '20260812', 'exit_date': '20260814', 'return_pct': 6.0},
    ]


def test_metrics_basic():
    from ashare_review.strategy_bench.metrics import compute_metrics
    m = compute_metrics(_trades())
    assert m['total_trades'] == 3
    assert m['wins'] == 2 and m['losses'] == 1
    assert round(m['win_rate'], 1) == 66.7
    assert m['avg_win'] == 8.0 and m['avg_loss'] == 5.0
    assert m['profit_loss_ratio'] == 1.6
    assert m['profit_factor'] == 3.2
    assert round(m['total_return'], 2) == 10.77   # 1.1*0.95*1.06-1


def test_metrics_equity_curve():
    from ashare_review.strategy_bench.metrics import build_equity_curve
    curve = build_equity_curve(_trades())
    # 按 exit_date 排序累乘：10% → 1.1*0.95=4.5% → 1.045*1.06=10.77%
    assert curve == [['20260811', 10.0], ['20260812', 4.5], ['20260814', 10.77]]


def test_metrics_max_drawdown():
    from ashare_review.strategy_bench.metrics import compute_metrics
    trades = [
        {'entry_date': '20260810', 'exit_date': '20260811', 'return_pct': 20.0},
        {'entry_date': '20260811', 'exit_date': '20260812', 'return_pct': -10.0},
        {'entry_date': '20260812', 'exit_date': '20260813', 'return_pct': -5.0},
    ]
    m = compute_metrics(trades)
    # 曲线: 20 → 1.2*0.9-1=8 → 1.08*0.95-1=2.6；峰值 20，谷值 2.6 → mdd=-17.4
    assert round(m['max_drawdown'], 2) == -17.4


def test_metrics_empty_and_edge():
    from ashare_review.strategy_bench.metrics import compute_metrics
    m = compute_metrics([])
    assert m['total_trades'] == 0
    assert m['win_rate'] is None and m['annual_return'] is None
    # 全部同收益 → std=0 → sharpe None
    same = [{'entry_date': '20260810', 'exit_date': '20260811', 'return_pct': 5.0},
            {'entry_date': '20260811', 'exit_date': '20260812', 'return_pct': 5.0}]
    m2 = compute_metrics(same)
    assert m2['sharpe'] is None
    # 单笔
    one = [{'entry_date': '20260810', 'exit_date': '20260811', 'return_pct': 3.0}]
    m3 = compute_metrics(one)
    assert m3['total_trades'] == 1 and m3['win_rate'] == 100.0


def test_metrics_annual_and_sharpe_with_calendar():
    from ashare_review.strategy_bench.metrics import compute_metrics
    from ashare_review.utils.calendar import TradingCalendar
    cal = TradingCalendar()
    # 2026-08-10(周一) ~ 2026-08-14(周五) = 5 个交易日
    trades = _trades()
    m = compute_metrics(trades, calendar=cal)
    # 跨度 5 交易日：年化 = 1.1077^(252/5)-1
    assert m['annual_return'] is not None
    # 夏普 = mean/std * sqrt(3*252/5)；mean=3.667, std=6.342 → 0.578*12.296 ≈ 7.11
    assert round(m['sharpe'], 2) == 7.11


# ---------- Task 2: 快照存储 ----------

def _snapshot_metrics_a():
    return {'annual_return': 12.3, 'max_drawdown': -18.2, 'sharpe': 1.2,
            'win_rate': 55.0, 'profit_loss_ratio': 1.8, 'profit_factor': 2.0,
            'total_return': 40.0, 'total_trades': 50}


def _snapshot_metrics_b():
    return {'annual_return': 15.1, 'max_drawdown': -15.5, 'sharpe': 1.5,
            'win_rate': 58.0, 'profit_loss_ratio': 2.0, 'profit_factor': 2.3,
            'total_return': 48.0, 'total_trades': 52}


def test_store_snapshot_crud(tmp_path):
    from ashare_review.strategy_bench.store import BenchStore
    store = BenchStore(str(tmp_path / 't.db'))
    sid = store.upsert_snapshot('v3', {'lookback_days': 60}, 'abc1234',
                                _snapshot_metrics_a(), [['20260811', 10.0]], 50)
    assert sid > 0
    s = store.get_snapshot(sid)
    assert s['strategy_id'] == 'v3'
    assert s['params'] == {'lookback_days': 60}
    assert s['git_sha'] == 'abc1234'
    assert s['metrics']['win_rate'] == 55.0
    assert s['equity_curve'] == [['20260811', 10.0]]
    assert s['trades_count'] == 50


def test_store_list_and_latest(tmp_path):
    from ashare_review.strategy_bench.store import BenchStore
    store = BenchStore(str(tmp_path / 't.db'))
    store.upsert_snapshot('v3', {}, 'a', _snapshot_metrics_a(), [], 1)
    store.upsert_snapshot('one_two', {}, 'b', _snapshot_metrics_b(), [], 2)
    store.upsert_snapshot('v3', {}, 'c', _snapshot_metrics_a(), [], 3)
    lst = store.list_snapshots(strategy_id='v3')
    assert len(lst) == 2
    assert lst[0]['git_sha'] == 'c'          # 倒序
    assert len(store.list_snapshots()) == 3
    latest = store.latest_snapshot('v3')
    assert latest['git_sha'] == 'c'
    assert store.latest_snapshot('ice') is None


def test_store_compare(tmp_path):
    from ashare_review.strategy_bench.store import BenchStore
    store = BenchStore(str(tmp_path / 't.db'))
    id_a = store.upsert_snapshot('v3', {'lookback_days': 60}, 'a',
                                 _snapshot_metrics_a(), [], 50)
    id_b = store.upsert_snapshot('v3', {'lookback_days': 120}, 'b',
                                 _snapshot_metrics_b(), [], 52)
    cmp = store.compare(id_a, id_b)
    assert cmp['a']['id'] == id_a and cmp['b']['id'] == id_b
    by_key = {m['key']: m for m in cmp['metrics']}
    assert by_key['annual_return']['a'] == 12.3
    assert by_key['annual_return']['b'] == 15.1
    assert round(by_key['annual_return']['delta'], 1) == 2.8
    assert by_key['annual_return']['better'] == 'b'
    # 最大回撤 -15.5 > -18.2（更浅）→ better=b
    assert by_key['max_drawdown']['better'] == 'b'
    # total_trades 无 better
    assert by_key['total_trades']['better'] is None

# ---------- Task 3: 基类 + 注册表 + 归一化 ----------

def test_normalize_v3_style_trades():
    """v3/zt/ice 共用归一化：buy_date/sell_date('%Y-%m-%d') + net_ret(%)"""
    from ashare_review.strategy_bench.adapters.base import normalize_v3_style_trades
    raw = [
        {'buy_date': '2026-08-10', 'sell_date': '2026-08-14', 'net_ret': 8.5, 'code': '600001'},
        {'buy_date': '2026-08-11', 'sell_date': '2026-08-12', 'net_ret': -3.2},
    ]
    trades = normalize_v3_style_trades(raw)
    assert trades == [
        {'entry_date': '20260810', 'exit_date': '20260814', 'return_pct': 8.5},
        {'entry_date': '20260811', 'exit_date': '20260812', 'return_pct': -3.2},
    ]


def test_normalize_one_two_trades():
    """one_two：entry_date/exit_date(可能带'-') + return_pct"""
    from ashare_review.strategy_bench.adapters.base import normalize_one_two_trades
    raw = [
        {'entry_date': '2026-08-10', 'exit_date': '2026-08-11', 'return_pct': 6.0, 'result': 'win'},
        {'entry_date': '20260812', 'exit_date': '20260812', 'return_pct': -4.0, 'result': 'loss'},
    ]
    trades = normalize_one_two_trades(raw)
    assert trades[0] == {'entry_date': '20260810', 'exit_date': '20260811', 'return_pct': 6.0}
    assert trades[1]['return_pct'] == -4.0


def test_normalize_tail_signals():
    """尾盘：信号行 → trade_date 入场，open_ret(%) 为收益，exit=次日"""
    from ashare_review.strategy_bench.adapters.base import normalize_tail_signals
    import pandas as pd
    from ashare_review.utils.calendar import TradingCalendar
    cal = TradingCalendar()
    sig = pd.DataFrame([
        {'trade_date': '2026-08-10', 'open_ret': 2.5, 'signal': '超跌'},
        {'trade_date': '2026-08-11', 'open_ret': -1.0, 'signal': '平台突破'},
    ])
    trades = normalize_tail_signals(sig, 'open_ret', cal)
    assert len(trades) == 2
    assert trades[0]['entry_date'] == '20260810'
    assert trades[0]['return_pct'] == 2.5
    assert trades[0]['exit_date'] == '20260811'   # 2026-08-10 的下一个交易日


def test_normalize_none_and_nan_guards():
    """None/NaN 字段：跳过脏数据不崩溃"""
    from ashare_review.strategy_bench.adapters.base import (
        normalize_v3_style_trades, normalize_one_two_trades)
    import math
    # v3 风格：net_ret=None / NaN / buy_date=None → 全部跳过；正常行保留
    raw = [
        {'buy_date': '2026-08-10', 'sell_date': '2026-08-14', 'net_ret': None},
        {'buy_date': None, 'sell_date': '2026-08-14', 'net_ret': 5.0},
        {'buy_date': '2026-08-11', 'sell_date': '2026-08-12', 'net_ret': float('nan')},
        {'buy_date': '2026-08-12', 'sell_date': '2026-08-13', 'net_ret': 3.0},
    ]
    trades = normalize_v3_style_trades(raw)
    assert len(trades) == 1
    assert trades[0]['return_pct'] == 3.0
    # one_two：return_pct=None 跳过
    raw2 = [
        {'entry_date': '2026-08-10', 'exit_date': '2026-08-11', 'return_pct': None},
        {'entry_date': '20260812', 'exit_date': '20260812', 'return_pct': -4.0},
    ]
    trades2 = normalize_one_two_trades(raw2)
    assert len(trades2) == 1 and trades2[0]['return_pct'] == -4.0


# ---------- Task 4: 5 个适配器 ----------

def _make_adapter(id_):
    from ashare_review.strategy_bench.adapters.registry import get_adapter
    return get_adapter(id_)


def test_v3_adapter_normalize():
    from ashare_review.strategy_bench.adapters.v3 import V3Adapter
    a = V3Adapter()
    trades = a.normalize({'trades': [
        {'buy_date': '2026-08-10', 'sell_date': '2026-08-14', 'net_ret': 8.5},
        {'buy_date': '2026-08-11', 'sell_date': '2026-08-12', 'net_ret': -3.2},
    ]})
    assert trades[0]['entry_date'] == '20260810' and trades[0]['return_pct'] == 8.5
    assert a.strategy_id == 'v3' and a.name == '启动突破V3'
    assert a.param_schema[0]['name'] == 'lookback_days'


def test_one_two_adapter_normalize():
    from ashare_review.strategy_bench.adapters.one_two import OneTwoAdapter
    a = OneTwoAdapter()
    trades = a.normalize({'valid_trades': [
        {'entry_date': '2026-08-10', 'exit_date': '2026-08-11', 'return_pct': 6.0},
        {'entry_date': '20260812', 'exit_date': '20260812', 'return_pct': -4.0},
    ]})
    assert trades[0]['entry_date'] == '20260810' and trades[0]['return_pct'] == 6.0
    assert trades[1]['return_pct'] == -4.0


def test_ice_adapter_normalize():
    from ashare_review.strategy_bench.adapters.ice import IceAdapter
    a = IceAdapter()
    trades = a.normalize([
        {'buy_date': '2026-08-10', 'sell_date': '2026-08-14', 'net_ret': 5.0},
    ])
    assert trades[0]['entry_date'] == '20260810' and trades[0]['return_pct'] == 5.0


def test_zt_replica_adapter_normalize():
    from ashare_review.strategy_bench.adapters.zt_replica import ZTReplicaAdapter
    a = ZTReplicaAdapter()
    trades = a.normalize({'trades': [
        {'buy_date': '2026-08-10', 'sell_date': '2026-08-14', 'net_ret': 3.3},
    ]})
    assert trades[0]['return_pct'] == 3.3


def test_registry_completeness():
    from ashare_review.strategy_bench.adapters.registry import list_adapters, get_adapter
    ids = [a.strategy_id for a in list_adapters()]
    assert set(ids) == {'v3', 'one_two', 'ice', 'tail', 'zt_replica'}
    a = get_adapter('v3')
    assert a.name == '启动突破V3'
    assert get_adapter('nope') is None
    for adapter in list_adapters():
        for p in adapter.param_schema:
            assert {'name', 'label', 'type', 'default'} <= set(p), adapter.strategy_id


def test_adapters_params_schema_values():
    for sid, expect in [
        ('v3', ['lookback_days', 'max_positions']),
        ('one_two', ['lookback_days', 'top_n', 'min_score']),
        ('ice', ['lookback_days']),
        ('tail', ['days', 'limit']),
        ('zt_replica', ['lookback_days', 'only_double_cannon']),
    ]:
        a = _make_adapter(sid)
        names = [p['name'] for p in a.param_schema]
        assert set(expect) <= set(names), sid
        for p in a.param_schema:
            assert p['type'] in ('int', 'float', 'bool'), (sid, p['name'])
            assert 'default' in p


# ---------- Task 5: 编排层 ----------

def test_run_backtest_with_mocked_adapter(tmp_path, monkeypatch):
    from ashare_review.strategy_bench import service as bench_service
    from ashare_review.strategy_bench.store import BenchStore

    monkeypatch.setattr(bench_service, 'DB_PATH', str(tmp_path / 't.db'))

    class FakeAdapter:
        strategy_id = 'v3'
        name = '启动突破V3'
        param_schema = []
        def run(self, params, tdx=None, ak=None):
            return [{'entry_date': '20260810', 'exit_date': '20260814', 'return_pct': 8.5},
                    {'entry_date': '20260811', 'exit_date': '20260812', 'return_pct': -3.2}]

    monkeypatch.setattr(bench_service, 'get_adapter', lambda sid: FakeAdapter())
    snap_id = bench_service.run_backtest('v3', {'lookback_days': 60})
    assert snap_id > 0
    store = BenchStore(str(tmp_path / 't.db'))
    s = store.get_snapshot(snap_id)
    assert s['strategy_id'] == 'v3'
    assert s['metrics']['total_trades'] == 2
    assert s['metrics']['win_rate'] == 50.0
    assert len(s['equity_curve']) == 2
    assert s['trades_count'] == 2


def test_run_backtest_bad_strategy(tmp_path, monkeypatch):
    from ashare_review.strategy_bench import service as bench_service
    monkeypatch.setattr(bench_service, 'DB_PATH', str(tmp_path / 't.db'))
    monkeypatch.setattr(bench_service, 'get_adapter', lambda sid: None)
    assert bench_service.run_backtest('nope', {}) == 0


def test_job_lifecycle(tmp_path, monkeypatch):
    import threading
    from ashare_review.strategy_bench import service as bench_service
    monkeypatch.setattr(bench_service, 'DB_PATH', str(tmp_path / 't.db'))

    class FakeAdapter:
        strategy_id = 'v3'
        def run(self, params, tdx=None, ak=None):
            return [{'entry_date': '20260810', 'exit_date': '20260814', 'return_pct': 1.0}]

    monkeypatch.setattr(bench_service, 'get_adapter', lambda sid: FakeAdapter())
    job_id = bench_service.start_job('v3', {'lookback_days': 60})
    # 轮询直到结束
    import time
    for _ in range(100):
        st = bench_service.get_job(job_id)
        if st['status'] in ('done', 'error'):
            break
        time.sleep(0.05)
    assert st['status'] == 'done'
    assert st['snapshot_id'] > 0

