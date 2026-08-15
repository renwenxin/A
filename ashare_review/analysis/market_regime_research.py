"""
市场环境研究框架 — Market Regime Research

研究目标：
  1. 市场状态变量分桶统计（涨停数、跌停数、指数趋势等）
  2. 每个市场环境下 V2 策略的胜率、均收益、盈亏比
  3. Alpha × Market Regime 交互分析（dist_250d、chg_60d 在不同环境下的 IC）

数据来源：
  V2 特征日志 data/v2_features_log.jsonl（含 sh_ma60_up, up_ratio, limit_up_num, limit_down_num）
  和回测交易记录（合并市场状态数据后分析）

原则：
  - 不修改任何评分逻辑
  - 不人为赋权
  - 只输出统计结果
"""
import sys, os, json, argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ── 市场环境变量定义 ──
REGIME_VARIABLES = {
    'limit_up_num': '涨停家数',
    'limit_down_num': '跌停家数',
    'sh_ma60_up': '上证MA60方向 (1=上, 0=下)',
    'up_ratio': '上涨比例',
}

# 分桶方案
REGIME_BUCKETS = {
    'limit_up_num':       [(0, 20), (20, 40), (40, 60), (60, 100), (100, 9999)],
    'limit_down_num':     [(0, 5), (5, 10), (10, 20), (20, 50), (50, 9999)],
    'sh_ma60_up':         [(0, 0.5), (0.5, 1.5)],  # binary: 0 or 1
    'up_ratio':           [(0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.0)],
}

REGIME_BUCKET_LABELS = {
    'limit_up_num':       ['冰点(0~20)', '低迷(20~40)', '温和(40~60)', '活跃(60~100)', '火爆(100+)'],
    'limit_down_num':     ['健康(0~5)', '正常(5~10)', '分化(10~20)', '弱势(20~50)', '恐慌(50+)'],
    'sh_ma60_up':         ['MA60向下', 'MA60向上'],
    'up_ratio':           ['普跌(<30%)', '分化(30~50%)', '温和(50~70%)', '普涨(>70%)'],
}


@dataclass
class RegimeAnalysis:
    """单一市场变量分析结果"""
    variable: str
    bucket_results: List[Dict] = field(default_factory=list)
    overall_stats: Dict = field(default_factory=dict)


@dataclass
class AlphaRegimeInteraction:
    """Alpha × 市场环境交互分析"""
    alpha_name: str
    regime_variable: str
    ic_by_regime: List[Dict] = field(default_factory=list)  # [{regime, ic}]
    bucket_returns: List[Dict] = field(default_factory=list)  # [{regime, bucket, avg_ret, count}]


# ═══════════════════════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════════════════════

def load_v2_features(features_path: str = None) -> pd.DataFrame:
    """加载 V2 特征日志（JSONL），包含市场状态字段。"""
    if features_path is None:
        features_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'data', 'v2_features_log.jsonl'
        )
    if not os.path.exists(features_path):
        raise FileNotFoundError(f"V2 features log not found: {features_path}")

    records = []
    with open(features_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    df = pd.DataFrame(records)

    # 确保市场状态字段存在
    required = ['limit_up_num', 'limit_down_num', 'sh_ma60_up', 'up_ratio']
    for col in required:
        if col not in df.columns:
            df[col] = np.nan

    return df


def load_backtest_trades(excel_path: str = None) -> pd.DataFrame:
    """加载回测交易数据（含市场状态列）。"""
    if excel_path is None:
        excel_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'data', 'start_breakout_backtest_250d.xlsx'
        )
    if not os.path.exists(excel_path):
        return pd.DataFrame()

    raw = pd.read_excel(excel_path, sheet_name='逐日记录(固定)', header=None)
    data_rows = []
    for i in range(4, len(raw)):
        code = raw.iloc[i, 2]
        if pd.notna(code):
            try:
                row = {
                    'signal_date': str(raw.iloc[i, 0])[:10] if pd.notna(raw.iloc[i, 0]) else '',
                    'code': str(code).zfill(6),
                    'score': float(raw.iloc[i, 3]) if pd.notna(raw.iloc[i, 3]) else 0,
                    'tier': str(raw.iloc[i, 4]) if pd.notna(raw.iloc[i, 4]) else '',
                    'net_ret': float(raw.iloc[i, 14]) if pd.notna(raw.iloc[i, 14]) else 0,
                    'is_win': float(raw.iloc[i, 14]) > 0 if pd.notna(raw.iloc[i, 14]) else False,
                }
                # 市场状态列 (Col 16~19)
                for col_idx, col_name in [(16, 'limit_up_num'), (17, 'limit_down_num'),
                                          (18, 'sh_ma60_up'), (19, 'up_ratio')]:
                    v = raw.iloc[i, col_idx]
                    if pd.notna(v):
                        try:
                            row[col_name] = float(v)
                        except (ValueError, TypeError):
                            row[col_name] = v
                data_rows.append(row)
            except (ValueError, TypeError):
                pass
    return pd.DataFrame(data_rows)


# ═══════════════════════════════════════════════════════════════════════════════
# 市场环境分桶统计
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_regime_variable(
    df: pd.DataFrame,
    variable: str,
    target: str = 'net_ret',
    buckets: List[Tuple[float, float]] = None,
    labels: List[str] = None,
) -> RegimeAnalysis:
    """对单个市场变量做分桶统计。

    Args:
        df: 交易记录 DataFrame（含 variable 和 target 列）
        variable: 市场变量名（如 limit_up_num）
        target: 目标收益列
        buckets: 分桶区间 [(lo, hi), ...]
        labels: 桶标签

    Returns:
        RegimeAnalysis 对象
    """
    if variable not in df.columns:
        return RegimeAnalysis(variable=variable)

    clean = df[df[variable].notna()]
    if len(clean) == 0:
        return RegimeAnalysis(variable=variable)

    if buckets is None:
        buckets = REGIME_BUCKETS.get(variable, [(0, 999)])
    if labels is None:
        labels = REGIME_BUCKET_LABELS.get(variable, [f'Bucket {i}' for i in range(len(buckets))])

    results = []
    for (lo, hi), label in zip(buckets, labels):
        sub = clean[(clean[variable] >= lo) & (clean[variable] < hi)]
        if len(sub) < 5:
            continue
        rets = sub[target]
        wins = (rets > 0).sum()
        big_wins = (rets > 10).sum()
        big_losses = (rets < -5).sum()
        results.append({
            'regime': label,
            'range': f'{lo}~{hi}',
            'count': len(sub),
            'win_rate': round(wins / len(sub) * 100, 1),
            'avg_ret': round(rets.mean(), 2),
            'median_ret': round(rets.median(), 2),
            'big_win_prob': round(big_wins / len(sub) * 100, 1),
            'big_loss_prob': round(big_losses / len(sub) * 100, 1),
            'pl_ratio': round(
                abs(rets[rets > 0].mean() / rets[rets <= 0].mean()), 2
            ) if len(rets[rets > 0]) and len(rets[rets <= 0]) and rets[rets <= 0].mean() != 0 else 0,
        })

    overall_ret = clean[target].mean()
    overall_wr = (clean[target] > 0).mean() * 100

    return RegimeAnalysis(
        variable=variable,
        bucket_results=results,
        overall_stats={
            'total': len(clean),
            'avg_ret': round(overall_ret, 2),
            'win_rate': round(overall_wr, 1),
        }
    )


def analyze_all_regimes(
    df: pd.DataFrame,
    variables: List[str] = None,
    target: str = 'net_ret',
) -> List[RegimeAnalysis]:
    """批量分析所有市场变量。"""
    if variables is None:
        variables = list(REGIME_VARIABLES.keys())
    results = []
    for var in variables:
        ra = analyze_regime_variable(df, var, target=target)
        if ra.bucket_results:
            results.append(ra)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Alpha × Market Regime 交互分析
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_alpha_regime_interaction(
    df: pd.DataFrame,
    alpha_column: str,
    regime_variable: str,
    target: str = 'net_ret',
    regime_buckets: List[Tuple[float, float]] = None,
    regime_labels: List[str] = None,
) -> AlphaRegimeInteraction:
    """分析单 Alpha 在不同市场环境下的表现。

    对每个市场状态桶，计算：
      - 该 Alpha 在该环境下的 IC
      - 该 Alpha 在该环境下的分层收益（top bucket avg_ret vs bottom bucket avg_ret）
    """
    if alpha_column not in df.columns or regime_variable not in df.columns:
        return AlphaRegimeInteraction(alpha_name=alpha_column, regime_variable=regime_variable)

    clean = df[df[alpha_column].notna() & df[regime_variable].notna()]
    if len(clean) < 20:
        return AlphaRegimeInteraction(alpha_name=alpha_column, regime_variable=regime_variable)

    if regime_buckets is None:
        regime_buckets = REGIME_BUCKETS.get(regime_variable, [(0, 999)])
    if regime_labels is None:
        regime_labels = REGIME_BUCKET_LABELS.get(regime_variable, [f'B{i}' for i in range(len(regime_buckets))])

    ic_by_regime = []
    bucket_returns = []

    for (lo, hi), rlabel in zip(regime_buckets, regime_labels):
        sub = clean[(clean[regime_variable] >= lo) & (clean[regime_variable] < hi)]
        if len(sub) < 15:
            continue

        # IC
        fv = pd.to_numeric(sub[alpha_column], errors='coerce')
        tv = pd.to_numeric(sub[target], errors='coerce')
        mask = fv.notna() & tv.notna()
        ic = fv[mask].corr(tv[mask]) if mask.sum() >= 10 else None

        ic_by_regime.append({
            'regime': rlabel,
            'n': len(sub),
            'ic': round(ic, 3) if ic is not None else None,
        })

        # 分两层：Alpha 高 vs 低
        try:
            med = fv[mask].median()
            high_mask = mask & (fv >= med)
            low_mask = mask & (fv < med)
            high_ret = tv[high_mask].mean()
            low_ret = tv[low_mask].mean()
            bucket_returns.append({
                'regime': rlabel,
                'n': mask.sum(),
                'alpha_high_avg_ret': round(high_ret, 2),
                'alpha_low_avg_ret': round(low_ret, 2),
                'spread': round(high_ret - low_ret, 2),
            })
        except Exception:
            pass

    return AlphaRegimeInteraction(
        alpha_name=alpha_column,
        regime_variable=regime_variable,
        ic_by_regime=ic_by_regime,
        bucket_returns=bucket_returns,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 交易频率分析 — 每日信号数量 vs 收益
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_trade_frequency(
    df: pd.DataFrame,
    target: str = 'net_ret',
    n_buckets: int = 5,
) -> RegimeAnalysis:
    """按每日交易数量分桶，分析交易频率对收益的影响。

    每日信号数量反映市场情绪：冰点期信号少，高潮期信号多。
    """
    if 'signal_date' not in df.columns:
        return RegimeAnalysis(variable='daily_trade_count')

    # 计算每天的交易数量
    daily_counts = df.groupby('signal_date').size().reset_index(name='trade_count')
    df_with_counts = df.merge(daily_counts, on='signal_date', how='left')

    if 'trade_count' not in df_with_counts.columns:
        return RegimeAnalysis(variable='daily_trade_count')

    counts = df_with_counts['trade_count']
    if counts.nunique() < 2:
        return RegimeAnalysis(variable='daily_trade_count')

    # 自动分桶（等宽分桶）
    from ashare_review.analysis.factor_research import _compute_bucket_stats

    try:
        buckets, bins = pd.qcut(counts, q=n_buckets, retbins=True, duplicates='drop')
    except Exception:
        buckets, bins = pd.cut(counts, bins=n_buckets, retbins=True, duplicates='drop')

    labels = [f'{bins[i]:.0f}~{bins[i+1]:.0f}只' for i in range(len(bins) - 1)]
    bounds = [(bins[i], bins[i + 1]) for i in range(len(bins) - 1)]

    # 转换为整数索引
    bucket_idx = pd.Series(buckets.cat.codes, index=df_with_counts.index)
    bucket_idx = bucket_idx.where(df_with_counts['trade_count'].notna(), -1)

    # 使用 factor_research 的桶统计
    from ashare_review.analysis.factor_research import _compute_bucket_stats as _cbs
    results = _cbs(
        df_with_counts['trade_count'],
        df_with_counts[target],
        bucket_idx, labels, bounds,
    )

    bucket_results = []
    for r in results:
        bucket_results.append({
            'regime': r.bucket_label,
            'count': r.count,
            'win_rate': r.win_rate,
            'avg_ret': r.avg_ret,
            'median_ret': r.median_ret,
            'big_win_prob': r.big_win_prob,
            'big_loss_prob': r.big_loss_prob,
        })

    return RegimeAnalysis(
        variable='daily_trade_count',
        bucket_results=bucket_results,
        overall_stats={
            'total': len(df_with_counts),
            'avg_ret': round(df_with_counts[target].mean(), 2),
            'win_rate': round((df_with_counts[target] > 0).mean() * 100, 1),
        }
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 滚动窗口稳定性 — Alpha 衰减监控
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RollingStability:
    """滚动窗口稳定性分析"""
    factor_name: str
    window_days: int
    windows: List[Dict] = field(default_factory=list)  # [{end_date, ic, win_rate, avg_ret, count}]
    ic_trend: str = ''   # 'stable' | 'decaying' | 'oscillating'


def analyze_rolling_stability(
    df: pd.DataFrame,
    factor_name: str = 'score',
    target: str = 'net_ret',
    window_days: int = 250,
    step: int = 20,
) -> RollingStability:
    """滚动窗口分析 Alpha 稳定性。

    每 step 天滑动一次，计算当前窗口的 IC / 胜率 / 均收益。
    用于观察 Alpha 是否随时间衰减。
    """
    if 'signal_date' not in df.columns:
        return RollingStability(factor_name=factor_name, window_days=window_days)

    df_sorted = df.sort_values('signal_date').reset_index(drop=True)
    if len(df_sorted) < window_days:
        return RollingStability(factor_name=factor_name, window_days=window_days)

    windows = []
    # 提取因子值
    try:
        fv = pd.to_numeric(df_sorted[factor_name], errors='coerce')
    except Exception:
        fv = pd.Series(np.nan, index=df_sorted.index)

    tv = pd.to_numeric(df_sorted[target], errors='coerce')

    max_start = len(df_sorted) - window_days
    for start in range(0, max_start + 1, step):
        end = start + window_days
        fv_w = fv.iloc[start:end]
        tv_w = tv.iloc[start:end]
        mask = fv_w.notna() & tv_w.notna()

        if mask.sum() < 30:
            continue

        # IC
        ic = fv_w[mask].corr(tv_w[mask])

        # 收益统计
        rets = tv_w[mask]
        wins = (rets > 0).sum()
        wr = wins / len(rets) * 100

        windows.append({
            'end_date': str(df_sorted['signal_date'].iloc[end - 1])[:10],
            'start_date': str(df_sorted['signal_date'].iloc[start])[:10],
            'n': mask.sum(),
            'ic': round(ic, 3),
            'win_rate': round(wr, 1),
            'avg_ret': round(rets.mean(), 2),
        })

    # 判断趋势
    if len(windows) >= 4:
        ics = [w['ic'] for w in windows if w.get('ic') is not None]
        if len(ics) >= 4:
            # 简单线性回归斜率为负？
            half = len(ics) // 2
            first_half = sum(ics[:half]) / half
            second_half = sum(ics[half:]) / half
            if second_half < first_half - 0.05 and ics[-1] < ics[0]:
                trend = 'decaying'
            elif abs(second_half - first_half) < 0.02:
                trend = 'oscillating'
            else:
                trend = 'stable'
        else:
            trend = 'insufficient'
    else:
        trend = 'insufficient'

    return RollingStability(
        factor_name=factor_name,
        window_days=window_days,
        windows=windows,
        ic_trend=trend,
    )


def print_rolling_report(stability: RollingStability) -> None:
    """打印滚动窗口稳定性报告。"""
    if not stability.windows:
        print(f'\n  Rolling stability for {stability.factor_name}: insufficient data')
        return

    width = 80
    print()
    print('═' * width)
    print(f'  Rolling Stability: {stability.factor_name} (window={stability.window_days}d)')
    print(f'  IC Trend: {stability.ic_trend.upper()}')
    print('═' * width)

    hdr = f'  {"Window":<24s} {"N":>5s} {"IC":>7s} {"Win%":>6s} {"AvgRet":>7s}'
    sep = f'  {"─"*24} {"─"*5} {"─"*7} {"─"*6} {"─"*7}'
    print(hdr)
    print(sep)

    for w in stability.windows[::max(1, len(stability.windows) // 12)]:  # 最多显示12行
        ic_str = f'{w["ic"]:+.3f}' if w.get('ic') is not None else 'N/A'
        print(f'  {w["start_date"]} ~ {w["end_date"]:<14s} {w["n"]:>5d} {ic_str:>7s} '
              f'{w["win_rate"]:>5.1f}% {w["avg_ret"]:>+6.2f}%')

    print('─' * width)
    if stability.ic_trend == 'decaying':
        print(f'  ⚠ WARNING: IC trending DOWN — monitor closely for Alpha decay')
    elif stability.ic_trend == 'stable':
        print(f'  ✅ IC STABLE — Alpha consistent across time')
    elif stability.ic_trend == 'oscillating':
        print(f'  ~ IC oscillating — factor works intermittently')
    print('═' * width)


# ═══════════════════════════════════════════════════════════════════════════════
# 终端输出
# ═══════════════════════════════════════════════════════════════════════════════

def print_regime_report(analyses: List[RegimeAnalysis]) -> None:
    """打印市场环境分桶统计报告。"""
    if not analyses:
        print('  No regime data available.')
        return

    for ra in analyses:
        if not ra.bucket_results:
            continue

        width = 90
        print()
        print('═' * width)
        print(f'  Market Regime: {REGIME_VARIABLES.get(ra.variable, ra.variable)} ({ra.variable})')
        print(f'  Overall: N={ra.overall_stats.get("total", 0)}, '
              f'AvgRet={ra.overall_stats.get("avg_ret", 0)}%, '
              f'WinRate={ra.overall_stats.get("win_rate", 0)}%')
        print('═' * width)

        hdr = (f'  {"Regime":<20s} {"N":>5s} {"Win%":>6s} {"AvgRet":>7s} '
               f'{"Median":>7s} {">10%":>6s} {"<-5%":>6s} {"盈亏比":>6s}')
        sep = f'  {"─"*20} {"─"*5} {"─"*6} {"─"*7} {"─"*7} {"─"*6} {"─"*6} {"─"*6}'
        print(hdr)
        print(sep)

        for b in ra.bucket_results:
            print(f'  {b["regime"]:<20s} {b["count"]:>5d} {b["win_rate"]:>5.1f}% '
                  f'{b["avg_ret"]:>+6.2f}% {b["median_ret"]:>+6.2f}% '
                  f'{b["big_win_prob"]:>5.1f}% {b["big_loss_prob"]:>5.1f}% '
                  f'{b["pl_ratio"]:>5.2f}')

        print('═' * width)


def print_interaction_report(interactions: List[AlphaRegimeInteraction]) -> None:
    """打印 Alpha × Regime 交互分析报告。"""
    for ia in interactions:
        if not ia.ic_by_regime:
            continue

        print()
        print('═' * 80)
        print(f'  Alpha × Regime: {ia.alpha_name} × {REGIME_VARIABLES.get(ia.regime_variable, ia.regime_variable)}')
        print('═' * 80)

        hdr = f'  {"Regime":<20s} {"N":>5s} {"IC":>7s} {"HighAvgRet":>10s} {"LowAvgRet":>9s} {"Spread":>7s}'
        print(hdr)
        print(f'  {"─"*20} {"─"*5} {"─"*7} {"─"*10} {"─"*9} {"─"*7}')

        ic_map = {r['regime']: r['ic'] for r in ia.ic_by_regime}
        ret_map = {r['regime']: r for r in ia.bucket_returns}

        for r in ia.ic_by_regime:
            ic_str = f'{r["ic"]:+.3f}' if r['ic'] is not None else 'N/A'
            bret = ret_map.get(r['regime'], {})
            high = bret.get('alpha_high_avg_ret', 0)
            low = bret.get('alpha_low_avg_ret', 0)
            spread = bret.get('spread', 0)
            print(f'  {r["regime"]:<20s} {r["n"]:>5d} {ic_str:>7s} '
                  f'{high:>+9.2f}% {low:>+8.2f}% {spread:>+6.2f}%')

        # 整体 IC 对比
        ics = [r['ic'] for r in ia.ic_by_regime if r['ic'] is not None]
        if len(ics) >= 1:
            print(f'  IC range: {min(ics):+.3f} ~ {max(ics):+.3f} '
                  f'(delta: {max(ics) - min(ics):+.3f})')
        print('═' * 80)


# ═══════════════════════════════════════════════════════════════════════════════
# 图表输出
# ═══════════════════════════════════════════════════════════════════════════════

def _setup_matplotlib_chinese():
    if not HAS_MPL:
        return False
    chinese_fonts = ['Microsoft YaHei', 'SimHei', 'PingFang SC']
    import matplotlib.font_manager as fm
    available = {f.name for f in fm.fontManager.ttflist}
    for font in chinese_fonts:
        if font in available:
            plt.rcParams['font.sans-serif'] = [font, 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False
            return True
    plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    return False


def plot_regime_bars(analyses: List[RegimeAnalysis], output_dir: str = None) -> None:
    """为每个市场变量绘制分桶均收益柱状图。"""
    if not HAS_MPL:
        return
    _setup_matplotlib_chinese()

    out_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parent.parent / 'data'
    out_dir.mkdir(parents=True, exist_ok=True)

    for ra in analyses:
        if len(ra.bucket_results) < 2:
            continue

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

        labels = [b['regime'] for b in ra.bucket_results]
        avg_rets = [b['avg_ret'] for b in ra.bucket_results]
        big_wins = [b['big_win_prob'] for b in ra.bucket_results]
        counts = [b['count'] for b in ra.bucket_results]

        # Panel 1: 均收益
        colors = ['#2ecc71' if r >= 0 else '#e74c3c' for r in avg_rets]
        bars = ax1.bar(range(len(labels)), avg_rets, color=colors, alpha=0.85)
        for i, (bar, c) in enumerate(zip(bars, counts)):
            ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                     f'n={c}', ha='center', va='bottom' if bar.get_height() >= 0 else 'top', fontsize=7)
        ax1.axhline(y=ra.overall_stats.get('avg_ret', 0), color='#f39c12', linestyle='--',
                    linewidth=1, label=f"Overall {ra.overall_stats.get('avg_ret', 0)}%")
        ax1.set_xticks(range(len(labels)))
        ax1.set_xticklabels(labels, rotation=30, ha='right', fontsize=8)
        ax1.set_ylabel('Avg Return (%)')
        ax1.set_title(f'{REGIME_VARIABLES.get(ra.variable, ra.variable)} — Avg Return')
        ax1.legend(fontsize=7)
        ax1.grid(axis='y', alpha=0.3)

        # Panel 2: 大赚/大亏概率
        big_losses = [b['big_loss_prob'] for b in ra.bucket_results]
        ax2.plot(range(len(labels)), big_wins, 'o-', color='#3498db', linewidth=2, markersize=6, label='Big Win >10%')
        ax2.plot(range(len(labels)), big_losses, 's-', color='#e74c3c', linewidth=2, markersize=6, label='Big Loss <-5%')
        ax2.set_xticks(range(len(labels)))
        ax2.set_xticklabels(labels, rotation=30, ha='right', fontsize=8)
        ax2.set_ylabel('Probability (%)')
        ax2.set_title(f'{REGIME_VARIABLES.get(ra.variable, ra.variable)} — Big Win/Loss')
        ax2.legend(fontsize=7)
        ax2.grid(alpha=0.3)

        plt.tight_layout()
        fpath = out_dir / f'regime_{ra.variable}.png'
        fig.savefig(str(fpath), dpi=150, bbox_inches='tight', facecolor='white')
        print(f'  Chart saved: {fpath}')
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description='Market Regime Research')
    ap.add_argument('--input', '-i', help='交易记录文件 (默认从 Excel 加载)')
    ap.add_argument('--features', '-f', help='V2 特征日志路径')
    ap.add_argument('--charts', '-c', action='store_true', help='生成图表')
    ap.add_argument('--output-dir', '-o', default=None, help='图表输出目录')
    args = ap.parse_args()

    # 加载数据
    if args.input:
        df = load_backtest_trades(args.input)
        print(f'Loaded {len(df)} trades from {args.input}')
    else:
        df = load_backtest_trades()
        if len(df) == 0:
            print('No backtest data found. Loading V2 features log...')
            df = load_v2_features(args.features)
        print(f'Loaded {len(df)} records')

    # 如果有 features 日志，合并市场状态
    features_df = load_v2_features(args.features) if args.features else pd.DataFrame()
    if not features_df.empty and 'signal_date' in df.columns and 'signal_date' in features_df.columns:
        # 合并市场状态
        daily_regime = features_df.groupby('signal_date').agg({
            'limit_up_num': 'first', 'limit_down_num': 'first',
            'sh_ma60_up': 'first', 'up_ratio': 'first',
        }).reset_index()
        df = df.merge(daily_regime, on='signal_date', how='left')
        print(f'Merged market regime data: {len(df)} trades')

    # 检查是否有市场变量
    has_regime = any(v in df.columns for v in REGIME_VARIABLES)
    if not has_regime:
        print('\nNo market regime data found. Can only analyze what\'s in the data.')
        print(f'Available columns: {[c for c in df.columns if c not in ["code", "tier"]]}')
        return

    print()
    print('=' * 70)
    print('  Market Regime Research')
    print('  V2 Frozen — No scoring changes')
    print('=' * 70)

    # 市场环境分桶
    print(f'\n{"─"*70}')
    print('  Part 1: Market Regime Bucketing')
    print(f'{"─"*70}')
    regimes = analyze_all_regimes(df, target='net_ret')
    print_regime_report(regimes)

    if args.charts:
        plot_regime_bars(regimes, output_dir=args.output_dir)

    # Alpha × Regime 交互（如果数据中有 Alpha 因子）
    alphas = [c for c in ['dist_250d', 'chg_60d'] if c in df.columns]
    if alphas:
        print(f'\n{"─"*70}')
        print('  Part 2: Alpha × Market Regime Interaction')
        print(f'{"─"*70}')
        interactions = []
        for alpha in alphas:
            for var in REGIME_VARIABLES:
                ia = analyze_alpha_regime_interaction(df, alpha, var)
                if ia.ic_by_regime:
                    interactions.append(ia)
        print_interaction_report(interactions)


if __name__ == '__main__':
    main()
