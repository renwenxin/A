"""V2 Baseline 跨时间窗口验证

保持所有策略参数不变，只改变回测时间范围。
验证 V2 Baseline 在不同市场环境下的泛化能力。

原则：
  - 不修改任何策略参数（板块过滤、V2≥60、回踩确认、MA10卖出）
  - 只输出统计，不用于调参
  - 多窗口批量回测，生成汇总报告
"""
import sys, os, json, argparse
from datetime import date, datetime
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ashare_review.analysis.start_breakout_backtest import StartBreakoutBacktest

# ── 指标计算 ──────────────────────────────────────────────

def calc_metrics(df: pd.DataFrame, label: str = '') -> dict:
    """计算一组交易的绩效指标。"""
    if df.empty or len(df) == 0:
        return {'period': label, 'trades': 0}

    rets = df['net_ret'].dropna()
    n = len(rets)
    if n == 0:
        return {'period': label, 'trades': 0}

    wins = rets[rets > 0]
    losses = rets[rets < 0]
    n_wins = len(wins)
    n_losses = len(losses)

    # 复利累计收益 + 最大回撤
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rets:
        equity *= (1 + r / 100)
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        max_dd = max(max_dd, dd)

    cum_ret = (equity - 1) * 100

    # 年化（交易日约 250）
    years = n / 250
    annual_ret = ((1 + cum_ret / 100) ** (1 / years) - 1) * 100 if years > 0 else 0

    # Sharpe（简化，假设无风险利率 0）
    std = rets.std()
    sharpe = (rets.mean() / std * np.sqrt(250)) if std > 0 else 0

    avg_win = wins.mean() if n_wins > 0 else 0
    avg_loss = losses.mean() if n_losses > 0 else 0
    pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    big_wins = (rets > 10).sum()
    big_losses = (rets < -5).sum()

    return {
        'period': label,
        'trades': n,
        'trading_days': df['signal_date'].nunique() if 'signal_date' in df.columns else 0,
        'win_rate': round(n_wins / n * 100, 1),
        'avg_ret': round(rets.mean(), 2),
        'median_ret': round(rets.median(), 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'cum_ret': round(cum_ret, 2),
        'annual_ret': round(annual_ret, 2),
        'max_dd': round(max_dd, 2),
        'pl_ratio': round(pl_ratio, 2),
        'sharpe': round(sharpe, 2),
        'big_win_pct': round(big_wins / n * 100, 1),
        'big_loss_pct': round(big_losses / n * 100, 1),
        'max_win': round(rets.max(), 2),
        'max_loss': round(rets.min(), 2),
    }


def evaluate_tier_split(df: pd.DataFrame, label: str = '') -> dict:
    """V2≥60 vs <60 对比。"""
    passed = df[df['score'] >= 60] if 'score' in df.columns else pd.DataFrame()
    failed = df[(df['score'] < 60) & (df['score'] > 0)] if 'score' in df.columns else pd.DataFrame()
    return {
        'passed': calc_metrics(passed, f'{label} V2≥60'),
        'failed': calc_metrics(failed, f'{label} V2<60'),
    }


def print_metrics(m: dict, header: str = ''):
    """打印一行指标。"""
    if m.get('trades', 0) == 0:
        print(f'  {m.get("period", header):<20s} {"无交易":>10s}')
        return
    label = header or m.get('period', '')
    print(f'  {label:<20s} {m["trades"]:>5d} {m["trading_days"]:>5d} '
          f'{m["win_rate"]:>5.1f}% {m["avg_ret"]:>+6.2f}% {m["median_ret"]:>+6.2f}% '
          f'{m["cum_ret"]:>+8.2f}% {m["max_dd"]:>6.2f}% {m["pl_ratio"]:>5.2f} '
          f'{m["sharpe"]:>5.2f} {m["big_win_pct"]:>5.1f}%')


# ── 回测引擎（冻结 V2 参数） ──────────────────────────────

def run_validation_backtest(
    start_date: date,
    end_date: date,
    lookback: int = 250,
    use_2024_cache: bool = False,
) -> pd.DataFrame:
    """在指定时间区间运行 V2 回测，保持所有参数不变。

    Args:
        start_date: 回测起始日期（用于指定数据范围）
        end_date: 回测结束日期
        lookback: 回测往前看的天数（从 end_date 往前数）
        use_2024_cache: 使用 2024 年缓存

    Returns:
        固定持有模式下的交易 DataFrame
    """
    bt = StartBreakoutBacktest(skip_sector_filter=not use_2024_cache)
    # 用固定持有模式 + MA10 退出
    df_fixed, _, df_ma10, _, _, ddf, _, ss = bt.run(
        lookback=lookback,
        exit_mode='all',
        hold_days=5,
        end_date=end_date,
    )

    # 确保 signal_date 在指定范围内
    if not df_ma10.empty and 'signal_date' in df_ma10.columns:
        ds = df_ma10['signal_date'].astype(str)
        date_start = start_date.strftime('%Y-%m-%d')
        date_end = end_date.strftime('%Y-%m-%d')
        df_ma10 = df_ma10[(ds >= date_start) & (ds <= date_end)].copy()

    return df_ma10


# ── 多窗口批量回测 ────────────────────────────────────────

def build_windows() -> list:
    """构建跨时间窗口。每个窗口约 250 个交易日（约 1 年），
    滑动步长约 60 个交易日（约 3 个月）。
    """
    windows = []
    for end_year in range(2023, 2026):
        for end_month in [3, 6, 9, 12]:
            try:
                e = date(end_year, end_month, 15)
                # 往前 250 个交易日 ≈ 1 年
                import calendar
                s = date(end_year - 1, end_month, 15)
                windows.append((s, e))
            except ValueError:
                continue
    # 最后加一个包含最新数据的窗口
    windows.append((date(2025, 6, 1), date(2026, 6, 30)))
    return windows


# ══════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description='V2 Baseline 跨时间窗口验证')
    ap.add_argument('--start', help='起始日期 YYYY-MM-DD')
    ap.add_argument('--end', default='2026-07-10', help='结束日期 YYYY-MM-DD (default: 2026-07-10)')
    ap.add_argument('--lookback', type=int, default=300, help='回测天数 (default: 300)')
    ap.add_argument('--batch', action='store_true', help='批量运行多个时间窗口')
    ap.add_argument('--xlsx', help='输出 Excel 文件路径（如桌面路径）')
    ap.add_argument('--2024', action='store_true', help='使用 2024 年缓存（需要先运行 build_2024_cache.py）')
    args = ap.parse_args()

    width = 130

    if args.batch:
        # ── 批量模式 ──
        windows = build_windows()
        print(f'\n{"=" * width}')
        print(f'  V2 Baseline 批量时间窗口验证')
        print(f'  {len(windows)} 个窗口，策略参数完全不变')
        print(f'{"=" * width}')

        all_results = []
        for s, e in windows:
            print(f'\n  回测 {s} ~ {e} ... ', end='', flush=True)
            try:
                df = run_validation_backtest(s, e, lookback=args.lookback)
                m = calc_metrics(df, label=f'{s}~{e}')
                all_results.append(m)
                print(f'{m["trades"]} 笔')
            except Exception as ex:
                print(f'失败: {ex}')
                continue

        # 汇总表
        print(f'\n{"=" * width}')
        print(f'  {"窗口":<28s} {"笔数":>5s} {"日":>4s} {"胜率":>6s} {"均收益":>7s} {"中位":>7s} '
              f'{"累计":>9s} {"回撤":>7s} {"盈亏比":>6s} {"Sharpe":>6s} {"大赚":>6s}')
        print(f'  {"─" * 28} {"─" * 5} {"─" * 4} {"─" * 6} {"─" * 7} {"─" * 7} '
              f'{"─" * 9} {"─" * 7} {"─" * 6} {"─" * 6} {"─" * 6}')
        for m in all_results:
            print_metrics(m)

        # 稳定性：各窗口间标准差
        if len(all_results) >= 3:
            print(f'\n  稳定性分析 (n={len(all_results)} 个窗口):')
            rets_list = [m['avg_ret'] for m in all_results if m['trades'] > 0]
            cum_list = [m['cum_ret'] for m in all_results if m['trades'] > 0]
            if rets_list:
                print(f'    均收益: {np.mean(rets_list):+.2f}% ± {np.std(rets_list):.2f}%')
            if cum_list:
                print(f'    累计收益: {np.mean(cum_list):+.2f}% ± {np.std(cum_list):.2f}%')
            win_rates = [m['win_rate'] for m in all_results if m['trades'] > 0]
            if win_rates:
                print(f'    胜率: {np.mean(win_rates):.1f}% ± {np.std(win_rates):.1f}%')
            sharpes = [m['sharpe'] for m in all_results if m['trades'] > 0]
            if sharpes:
                print(f'    Sharpe: {np.mean(sharpes):.2f} ± {np.std(sharpes):.2f}')

        print(f'{"=" * width}')

    else:
        # ── 单窗口模式 ──
        start = datetime.strptime(args.start, '%Y-%m-%d').date() if args.start else date(2024, 1, 1)
        end = datetime.strptime(args.end, '%Y-%m-%d').date() if args.end else date(2026, 7, 10)

        print(f'\n{"=" * width}')
        print(f'  V2 Baseline 验证回测: {start} ~ {end}')
        print(f'  ├ V2≥60 资格过滤')
        print(f'  ├ 回踩确认 T+1~T+5')
        print(f'  └ MA10 跌破卖出')
        print(f'  ⚠ 所有参数冻结，不修改')
        print(f'{"=" * width}')

        df = run_validation_backtest(start, end, lookback=args.lookback)

        if df.empty:
            print('\n  无交易。')
            return

        # ── 整体表现 ──
        overall = calc_metrics(df, label='整体')
        print(f'\n  {"指标":<20s} {"值":>10s}')
        print(f'  {"─" * 32}')
        for k, v in overall.items():
            if k not in ('period', 'avg_win', 'avg_loss', 'max_win', 'max_loss'):
                print(f'  {k:<20s} {str(v):>10s}')

        # ── V2≥60 vs <60 ──
        if 'score' in df.columns:
            print(f'\n  V2 分档对比:')
            tier_split = evaluate_tier_split(df)
            print(f'  {"分组":<20s} {"笔数":>5s} {"日":>4s} {"胜率":>6s} {"均收益":>7s} '
                  f'{"中位":>7s} {"累计":>9s} {"回撤":>7s} {"盈亏比":>6s} {"Sharpe":>6s}')
            print(f'  {"─" * 90}')
            print_metrics(tier_split['passed'])
            print_metrics(tier_split['failed'])

        # ── 按 tier 分层 ──
        if 'tier' in df.columns:
            print(f'\n  Tier 分层:')
            for t in ['S', 'A', 'B', 'C', 'D']:
                sub = df[df['tier'] == t]
                if len(sub):
                    print_metrics(calc_metrics(sub, label=f'  {t}'))

        # ── 按行业分布 ──
        if 'industry' in df.columns:
            print(f'\n  行业分布 (Top 10):')
            ind_counts = df['industry'].value_counts().head(10)
            for ind, cnt in ind_counts.items():
                sub = df[df['industry'] == ind]
                m = calc_metrics(sub, label=f'  {ind}')
                print(f'  {ind:<16s} {cnt:>4d}笔  wr={m["win_rate"]:.1f}%  avg={m["avg_ret"]:+.2f}%  cum={m["cum_ret"]:+.2f}%')

        # ── 按回踩天数 ──
        if 'pullback_days' in df.columns:
            print(f'\n  回踩天数分布:')
            for d in range(1, 6):
                sub = df[df['pullback_days'] == d]
                if len(sub):
                    m = calc_metrics(sub, label=f'  T+{d}')
                    print(f'  T+{d:<4d} {m["trades"]:>4d}笔  wr={m["win_rate"]:.1f}%  avg={m["avg_ret"]:+.2f}%')

        print(f'\n{"=" * width}')
        print(f'  注：以上为 V2 Baseline 验证，所有参数与 250 天基准回测一致')
        print(f'{"=" * width}')


if __name__ == '__main__':
    main()
