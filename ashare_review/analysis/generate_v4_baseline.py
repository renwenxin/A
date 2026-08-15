"""V4 回测基线缓存生成脚本

运行 V3/V4 突破战法全周期回测，按牛熊市分割统计，生成缓存 JSON。
用于 v4_monitor.html 中第五节「V4 回测基线参考值」数据的动态加载。

用法:
    python -m ashare_review.analysis.generate_v4_baseline
    python -m ashare_review.analysis.generate_v4_baseline --start 2022-01-01 --end 2026-07-31
    python -m ashare_review.analysis.generate_v4_baseline --output custom_path.json

输出: ashare_review/data/v4_baseline_cache.json
"""
import sys, os, json, argparse
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ashare_review.analysis.v4_backtest import V3Backtest

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
DEFAULT_CACHE_PATH = os.path.join(DATA_DIR, 'v4_baseline_cache.json')


def compute_baseline(start_date: date, end_date: date) -> dict:
    """运行 V3/V4 回测，返回完整的基线指标数据。"""
    print(f'\n{"="*60}')
    print(f'  V4 回测基线缓存生成')
    print(f'  区间: {start_date} ~ {end_date}')
    print(f'{"="*60}\n')

    bt = V3Backtest()
    results = bt.run(start_date=start_date, end_date=end_date)

    if not results or not results.get('trades'):
        print('[ERROR] 回测未产生交易记录')
        return {'error': 'no_trades', 'start_date': str(start_date), 'end_date': str(end_date)}

    trades = results['trades']
    market_cycle = results.get('market_cycle_stats', {})

    # ── 全周期聚合指标 ──
    total = len(trades)
    wins = sum(1 for t in trades if t['is_win'])
    losses = total - wins
    win_rate = wins / total * 100 if total > 0 else 0
    net_rets = [t['net_ret'] for t in trades]
    avg_ret = sum(net_rets) / total if total > 0 else 0
    avg_win = sum(r for r in net_rets if r > 0) / max(wins, 1) if wins > 0 else 0
    avg_loss = sum(r for r in net_rets if r <= 0) / max(losses, 1) if losses > 0 else 0
    profit_factor = abs(sum(r for r in net_rets if r > 0) / min(sum(r for r in net_rets if r <= 0), -0.01)) if losses > 0 else 999
    avg_hold_days = sum(t['days_held'] for t in trades) / total if total > 0 else 0
    cumulative_return = results.get('cumulative_return', 0)
    max_drawdown = results.get('max_drawdown', 0)

    # 平均同时持仓
    daily_log = results.get('daily_log', [])
    avg_positions = sum(dl['holdings'] for dl in daily_log) / max(len(daily_log), 1) if daily_log else 0

    full_cycle_data = market_cycle.get('full_cycle', {})

    # ── 构建基线 JSON ──
    baseline = {
        'meta': {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'backtest_period': f'{results["start_date"]} ~ {results["end_date"]}',
            'initial_capital': results.get('initial_capital', 1000000),
            'final_value': results.get('final_value', 0),
            'total_trading_days': len(daily_log),
        },
        'full_cycle': {
            'total_trades': total,
            'win_rate': round(win_rate, 1),
            'avg_net_return': round(avg_ret, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'profit_factor': round(profit_factor, 2),
            'cumulative_return': round(cumulative_return, 1),
            'annualized_return': full_cycle_data.get('annualized_return', 0),
            'max_drawdown': round(max_drawdown, 2),
            'sharpe_ratio': full_cycle_data.get('sharpe_ratio', 0),
            'sh_above_ma20_pct': full_cycle_data.get('sh_above_ma20_pct', 0),
            'avg_hold_days': round(avg_hold_days, 1),
            'avg_positions': round(avg_positions, 1),
        },
        'bear_2022_2024': market_cycle.get('bear_2022_2024', {}),
        'bull_2025_2026': market_cycle.get('bull_2025_2026', {}),
    }

    return baseline


def main():
    parser = argparse.ArgumentParser(description='V4 回测基线缓存生成')
    parser.add_argument('--start', type=str, default='2022-01-01',
                        help='开始日期 YYYY-MM-DD（默认 2022-01-01）')
    parser.add_argument('--end', type=str, default='2026-07-31',
                        help='结束日期 YYYY-MM-DD（默认 2026-07-31）')
    parser.add_argument('--output', type=str, default=None,
                        help='输出路径（默认 ashare_review/data/v4_baseline_cache.json）')
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, '%Y-%m-%d').date()
    end_date = datetime.strptime(args.end, '%Y-%m-%d').date()
    output_path = args.output or DEFAULT_CACHE_PATH

    baseline = compute_baseline(start_date, end_date)

    if 'error' in baseline:
        print(f'\n[ERROR] 基线生成失败: {baseline["error"]}')
        return 1

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)

    print(f'\n[OK] V4 基线缓存已保存: {output_path}')
    print(f'  全周期: {baseline["full_cycle"]["total_trades"]}笔 '
          f'胜率{baseline["full_cycle"]["win_rate"]}% '
          f'累计{baseline["full_cycle"]["cumulative_return"]:+.1f}%')
    bear = baseline.get('bear_2022_2024', {})
    bull = baseline.get('bull_2025_2026', {})
    if bear:
        print(f'  熊市: {bear.get("trades", 0)}笔 '
              f'胜率{bear.get("win_rate", 0):.1f}% '
              f'均盈{bear.get("avg_win", 0):+.2f}% 均亏{bear.get("avg_loss", 0):+.2f}%')
    if bull:
        print(f'  牛市: {bull.get("trades", 0)}笔 '
              f'胜率{bull.get("win_rate", 0):.1f}% '
              f'均盈{bull.get("avg_win", 0):+.2f}% 均亏{bull.get("avg_loss", 0):+.2f}%')

    return 0


if __name__ == '__main__':
    sys.exit(main())
