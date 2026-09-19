"""回测引擎与实盘引擎 V3 卖出阈值一致（防口径裂）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_stop_loss_and_hold_days_aligned():
    from ashare_review.tools import sim_portfolio as sp
    from ashare_review.analysis import v3_backtest as bt3
    from ashare_review.analysis import v4_backtest as bt4
    assert bt3.V3_STOP_LOSS_PCT == sp.V3_STOP_LOSS_PCT == -8.0
    assert bt4.V3_STOP_LOSS_PCT == sp.V3_STOP_LOSS_PCT == -8.0
    assert bt3.V3_MAX_HOLD_DAYS == sp.V3_MAX_HOLD_DAYS == 20
    assert bt4.V3_MAX_HOLD_DAYS == sp.V3_MAX_HOLD_DAYS == 20
