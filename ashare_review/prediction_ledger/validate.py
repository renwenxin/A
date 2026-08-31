"""预测台账 — 命中判定引擎（纯函数，无 IO）

actual 枚举:
  picks:   zt=涨停  up3=涨≥3%  up=收涨  flat=-3%~0  down=<-3%
  cycle:   up=涨停家数增≥10%  down=减≥10%  flat=其他
  auction: high=平均高开≥1.5%  low=≤-0.5%  flat=其他
direction 枚举: cycle=up|flat|down  auction=high|flat|low  picks=无
"""
from typing import Optional


def grade_pick(today_chg: float, is_zt: bool = False) -> str:
    """精选标的次日实际表现分级。涨停优先，其余按涨幅区间。"""
    if is_zt:
        return 'zt'
    if today_chg >= 3.0:
        return 'up3'
    if 0.0 <= today_chg < 3.0:
        return 'up'
    if -3.0 <= today_chg < 0.0:
        return 'flat'
    return 'down'


def grade_cycle(today_zt: int, next_zt: int) -> Optional[str]:
    """情绪周期次日实际方向：r=次日涨停家数/当日涨停家数。"""
    if today_zt <= 0:
        return None
    r = next_zt / today_zt
    if r >= 1.1:
        return 'up'
    if r <= 0.9:
        return 'down'
    return 'flat'


def grade_auction(avg_gap: float) -> str:
    """竞价预期次日实际：当日涨停池次日平均高开幅度(%)。"""
    if avg_gap >= 1.5:
        return 'high'
    if avg_gap <= -0.5:
        return 'low'
    return 'flat'


def hit_for(pred_type: str, direction: Optional[str], actual: Optional[str]) -> Optional[int]:
    """判定命中：返回 1/0，无法判定返回 None。"""
    if not actual:
        return None
    if pred_type == 'picks':
        return 1 if actual in ('zt', 'up3') else 0
    if not direction:
        return None
    return 1 if direction == actual else 0


def hit_for_auction_verdict(verdict: str, actual: Optional[str]) -> Optional[int]:
    """竞价判断命中：抢筹/达标看涨、观望看跌。无法判定返回 None。

    抢筹 = 强信号 → 需涨停/涨≥3%；达标 = 可参与 → 收涨即可；
    观望 = 规避 → 不涨（震荡/大跌）才算规避正确。
    """
    if not actual:
        return None
    if verdict == '抢筹':
        return 1 if actual in ('zt', 'up3') else 0
    if verdict == '达标':
        return 1 if actual in ('up', 'up3', 'zt') else 0
    if verdict == '观望':
        return 1 if actual in ('flat', 'down') else 0
    return None
