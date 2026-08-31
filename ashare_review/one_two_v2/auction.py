# ashare_review/one_two_v2/auction.py
"""今日1进2 — 次日竞价确认（视频竞价爆量公式 + 战法联动）"""
from typing import Dict, Optional

LEVEL_LABEL = {'extreme': '🔴 爆量极强', 'high': '🟠 爆量', 'mid': '🟡 放量', 'low': '⚪ 平淡'}


def grade_auction_ratio(auction_amount: float, free_mcap_yi: float,
                        thresholds: dict) -> Dict:
    """竞价爆量比 = 竞价成交额 / 自由流通市值 ×100。返回 {level, ratio, label}

    auction_amount 单位万，free_mcap_yi 单位亿 → mcap_yi*1e4 万。
    """
    if free_mcap_yi <= 0:
        return {'level': 'low', 'ratio': 0.0, 'label': LEVEL_LABEL['low']}
    ratio = auction_amount / (free_mcap_yi * 1e4) * 100
    if ratio >= thresholds.get('auction_ratio_high', 10.0):
        level = 'extreme'
    elif ratio >= thresholds.get('auction_ratio_mid', 5.0):
        level = 'high'
    elif ratio >= thresholds.get('auction_ratio_low', 3.0):
        level = 'mid'
    else:
        level = 'low'
    return {'level': level, 'ratio': round(ratio, 2), 'label': LEVEL_LABEL[level]}


def check_trigger(tactic: str, open_change_pct: float = 0.0,
                  auction_volume: float = 0.0, preclose_volume: float = 0.0,
                  prev_high: float = 0.0, gap_price: Optional[float] = None,
                  ratio: float = 0.0, thresholds: Optional[dict] = None) -> Dict:
    """按战法判定买点是否触发。返回 {triggered, note}"""
    if tactic == 'weak_strong':
        if open_change_pct >= 3.0 and auction_volume > preclose_volume * 0.5:
            return {'triggered': True, 'note': '✅ 弱转强买点（高开≥3%+竞价放量）'}
        return {'triggered': False, 'note': '⏳ 弱转强未确认'}
    if tactic == 'graph':
        if gap_price and prev_high and gap_price > prev_high:
            return {'triggered': True, 'note': '✅ 图形突破买点（跳空过前高）'}
        return {'triggered': False, 'note': '⏳ 未跳空过压力位'}
    low = (thresholds or {}).get('auction_ratio_low', 3.0)
    if ratio >= low:
        return {'triggered': True, 'note': f'✅ 竞价爆量（{ratio:.1f}%）'}
    return {'triggered': False, 'note': '⏳ 竞价量能不足'}
