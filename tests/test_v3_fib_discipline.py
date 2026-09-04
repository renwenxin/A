"""启动突破 V3 — 斐波那契均线纪律 单元测试

背景: 用户教学 —— 均线设 13/34/55/89(斐波那契)；只做 K 线在 13 日线上的图形；
日线破位 34/55 均线→谨慎参与；破位 89 均线→直接休息。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk_latest(closes, volumes, opens=None, highs=None, lows=None):
    """构造 _read_latest 的输出结构（含新增字段）。"""
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
