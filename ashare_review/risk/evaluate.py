"""风控规则 — 开仓判定纯函数"""
import math
from typing import Dict, List


def evaluate(config: dict, state: dict, regime: str) -> Dict:
    """开仓判定。state = {positions, opened_today, total_value, history_peak, breaker_tripped}

    返回 {can_open, blocked_reasons[], suggested_size_pct, regime_scale, drawdown_pct,
          breaker_tripped}
    """
    blocked: List[str] = []
    peak = float(state.get('history_peak', 0) or 0)
    total = float(state.get('total_value', 0) or 0)
    if peak > 0 and math.isfinite(total):
        drawdown_pct = (peak - total) / peak * 100
    elif not math.isfinite(total):
        drawdown_pct = float('nan')
    else:
        drawdown_pct = 0.0
    if math.isnan(drawdown_pct):
        drawdown_pct = 0.0
        blocked.append('组合净值数据异常，暂停开仓')

    breaker = float(config.get('drawdown_breaker_pct', 8.0))
    recover = float(config.get('drawdown_recover_pct', 4.0))
    tripped = bool(state.get('breaker_tripped', False))
    if tripped:
        if drawdown_pct >= recover:
            blocked.append(f'组合回撤 {drawdown_pct:.1f}% 仍高于恢复线 {recover:.1f}%（熔断中）')
        else:
            tripped = False
    elif drawdown_pct >= breaker:
        blocked.append(f'组合回撤 {drawdown_pct:.1f}% ≥ 熔断线 {breaker:.1f}%')
        tripped = True

    scale = float(config.get('regime_scale', {}).get(regime, 1.0))
    if scale <= 0:
        blocked.append(f'行情「{regime}」禁止开新仓')

    max_pos = int(config.get('max_positions', 10))
    if int(state.get('positions', 0)) >= max_pos:
        blocked.append(f'持仓数已达上限 {max_pos} 只')

    max_new = int(config.get('max_new_per_day', 3))
    if int(state.get('opened_today', 0)) >= max_new:
        blocked.append(f'今日已新开 {max_new} 只')

    return {
        'can_open': len(blocked) == 0,
        'blocked_reasons': blocked,
        'suggested_size_pct': round(min(float(config.get('per_position_pct', 10.0)) * scale, 100.0), 1),
        'regime_scale': round(scale, 2),
        'drawdown_pct': round(drawdown_pct, 2),
        'breaker_tripped': tripped,
    }


def stop_loss_pct(config: dict) -> float:
    """卖出点读取的止损线（负值 %）。"""
    return float(config.get('stop_loss_pct', -6.0))
