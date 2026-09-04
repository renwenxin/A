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
