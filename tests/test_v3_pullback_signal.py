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
    # 收盘 ≈ MA20(=10)，距 5/10 远 → dist_min ≈ 0.x%（<3 命中回踩）
    closes = [10.0] * 20 + [10.05]
    latest = _mk_latest(closes, volumes=[100] * 21)
    m = pullback_metrics(latest)
    assert m['dist_min_pct'] < 3.0
    # 收盘离所有均线 > 5% → 非回踩
    closes2 = [10.0] * 20 + [11.0]
    m2 = pullback_metrics(_mk_latest(closes2, volumes=[100] * 21))
    assert m2['dist_min_pct'] > 5.0
