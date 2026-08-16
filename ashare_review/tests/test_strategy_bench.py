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
