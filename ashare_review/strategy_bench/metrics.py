"""策略验证台 — 统一绩效指标（纯函数）

输入：归一化逐笔交易 [{entry_date, exit_date, return_pct}]
指标口径见 specs/2026-08-16-strategy-bench-design.md §4。
夏普为交易级近似：mean(r)/std(r) × √(年化交易次数)，口径一致可横评。
"""
import math
from datetime import datetime
from typing import Dict, List, Optional

from ..utils.calendar import TradingCalendar


def _ymd_to_date(s: str):
    try:
        return datetime.strptime(s, '%Y%m%d').date()
    except (ValueError, TypeError):
        return None


def build_equity_curve(trades: List[Dict]) -> List[list]:
    """按 exit_date 排序的累计收益序列：[[exit_date, 累计收益%], ...]"""
    ordered = sorted(trades, key=lambda t: str(t.get('exit_date', '')))
    cum = 1.0
    curve = []
    for t in ordered:
        cum *= 1 + float(t.get('return_pct', 0.0)) / 100.0
        curve.append([str(t.get('exit_date', '')), round((cum - 1) * 100, 4)])
    return curve


def compute_metrics(trades: List[Dict], calendar: Optional[TradingCalendar] = None) -> Dict:
    """统一指标。0 笔/无法计算 → 对应字段 None。"""
    if not trades:
        return {'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': None,
                'avg_win': None, 'avg_loss': None, 'profit_loss_ratio': None,
                'profit_factor': None, 'total_return': None, 'annual_return': None,
                'max_drawdown': None, 'sharpe': None}

    n = len(trades)
    wins = [t for t in trades if t['return_pct'] > 0]
    losses = [t for t in trades if t['return_pct'] <= 0]
    win_rate = len(wins) / n * 100
    avg_win = sum(t['return_pct'] for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(abs(t['return_pct']) for t in losses) / len(losses) if losses else 0.0
    pl_ratio = round(avg_win / avg_loss, 4) if avg_loss > 0 else None
    total_profit = sum(t['return_pct'] for t in wins)
    total_loss = sum(abs(t['return_pct']) for t in losses)
    profit_factor = round(total_profit / total_loss, 4) if total_loss > 0 else None

    total_return = 1.0
    for t in trades:
        total_return *= 1 + t['return_pct'] / 100.0
    total_return_pct = round((total_return - 1) * 100, 4)

    curve = build_equity_curve(trades)
    peak = -math.inf
    max_drawdown = 0.0
    for _, v in curve:
        peak = max(peak, v)
        max_drawdown = min(max_drawdown, v - peak)

    annual_return = None
    sharpe = None
    if calendar:
        starts = [_ymd_to_date(t.get('entry_date')) for t in trades]
        ends = [_ymd_to_date(t.get('exit_date')) for t in trades]
        starts = [d for d in starts if d]
        ends = [d for d in ends if d]
        if starts and ends:
            span = calendar.trading_days_between(min(starts), max(ends))
            if span and span > 0:
                annual_return = round((total_return ** (252.0 / span) - 1) * 100, 4)
                trades_per_year = n * 252.0 / span
                rets = [t['return_pct'] for t in trades]
                mean_r = sum(rets) / n
                std_r = math.sqrt(sum((r - mean_r) ** 2 for r in rets) / n)
                if std_r > 0:
                    sharpe = round(mean_r / std_r * math.sqrt(trades_per_year), 4)

    return {'total_trades': n, 'wins': len(wins), 'losses': len(losses),
            'win_rate': round(win_rate, 2), 'avg_win': round(avg_win, 4),
            'avg_loss': round(avg_loss, 4), 'profit_loss_ratio': pl_ratio,
            'profit_factor': profit_factor, 'total_return': total_return_pct,
            'annual_return': annual_return, 'max_drawdown': round(max_drawdown, 4),
            'sharpe': sharpe}
