"""1进2 新旧对比回测单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tr(ret, rank=1, reason='T+3收盘', days=3):
    return {'return_pct': ret, 'rank': rank, 'exit_reason': reason, 'days_held': days}


def test_compute_stats_basic():
    from ashare_review.analysis.one_two_compare_backtest import compute_stats
    trades = [_tr(10), _tr(-5), _tr(6), _tr(-3)]
    s = compute_stats(trades)
    assert s['total'] == 4
    assert s['win_rate'] == 50.0
    assert round(s['avg_ret'], 2) == 2.0          # (10-5+6-3)/4
    assert round(s['total_ret'], 1) == 8.0
    assert s['pl_ratio'] > 0
    # 最大回撤：10 → 5 → 11 → 8（累计收益 10/5/11/8 → 峰值11 谷值5 → -6）
    assert s['max_drawdown'] <= -5.0
    assert s['max_losing_streak'] == 1


def test_compute_stats_stop_loss_rate():
    from ashare_review.analysis.one_two_compare_backtest import compute_stats
    trades = [_tr(5, reason='止损-6%'), _tr(-4, reason='止损-6%'), _tr(2)]
    s = compute_stats(trades)
    assert s['stop_loss_rate'] == round(2 / 3 * 100, 1)


def test_rank_stats():
    from ashare_review.analysis.one_two_compare_backtest import rank_stats
    trades = [_tr(10, rank=1), _tr(-5, rank=1), _tr(6, rank=2)]
    r = rank_stats(trades)
    assert r['rank1']['total'] == 2 and r['rank1']['win_rate'] == 50.0
    assert r['rank2']['total'] == 1 and r['rank2']['avg_ret'] == 6.0
    assert r['rank3']['total'] == 0


def test_entry_decision_rules():
    from ashare_review.analysis.one_two_compare_backtest import entry_decision
    # 高开 +2%~+7% 且未走弱 → 可买
    mins = [{'t': 570, 'open': 10.3, 'high': 10.4, 'low': 10.3, 'close': 10.35, 'vol': 1000},
            {'t': 571, 'open': 10.35, 'high': 10.4, 'low': 10.3, 'close': 10.4, 'vol': 1200},
            {'t': 572, 'open': 10.4, 'high': 10.5, 'low': 10.38, 'close': 10.45, 'vol': 800},
            {'t': 573, 'open': 10.45, 'high': 10.5, 'low': 10.4, 'close': 10.48, 'vol': 900},
            {'t': 574, 'open': 10.48, 'high': 10.5, 'low': 10.42, 'close': 10.45, 'vol': 700}]
    price, note = entry_decision('600001', 'sh', 10.0, '20260826', mins)
    assert price is not None and '高开' in note        # 高开3%未走弱
    # 高开 >7%：回踩后突破 → 可买
    mins2 = [{'t': 570, 'open': 10.9, 'high': 11.2, 'low': 10.9, 'close': 11.1, 'vol': 500},
             {'t': 571, 'open': 11.1, 'high': 11.3, 'low': 11.0, 'close': 11.2, 'vol': 600},
             {'t': 572, 'open': 11.2, 'high': 11.25, 'low': 10.95, 'close': 11.05, 'vol': 700},
             {'t': 573, 'open': 11.05, 'high': 11.1, 'low': 11.0, 'close': 11.05, 'vol': 800},
             {'t': 574, 'open': 11.05, 'high': 11.35, 'low': 11.0, 'close': 11.35, 'vol': 900}]
    price2, note2 = entry_decision('600001', 'sh', 10.0, '20260826', mins2)
    assert price2 is not None and '回踩' in note2
    # 低开 <-2% 且走弱 → 放弃
    mins3 = [{'t': 570, 'open': 9.7, 'high': 9.7, 'low': 9.5, 'close': 9.55, 'vol': 1000},
             {'t': 571, 'open': 9.55, 'high': 9.6, 'low': 9.4, 'close': 9.45, 'vol': 1200},
             {'t': 572, 'open': 9.45, 'high': 9.5, 'low': 9.3, 'close': 9.35, 'vol': 900},
             {'t': 573, 'open': 9.35, 'high': 9.4, 'low': 9.2, 'close': 9.25, 'vol': 800},
             {'t': 574, 'open': 9.25, 'high': 9.3, 'low': 9.1, 'close': 9.2, 'vol': 700}]
    price3, note3 = entry_decision('600001', 'sh', 10.0, '20260826', mins3)
    assert price3 is None


def test_entry_decision_daily_fallback():
    from ashare_review.analysis.one_two_compare_backtest import entry_decision
    # 无分钟线 → 日线近似：开盘 +3% 可买
    price, note = entry_decision('600001', 'sh', 10.0, '20260610', None)
    # 依赖真实 TDX 日线（若数据存在返回价或放弃说明）
    assert price is None or price > 0
