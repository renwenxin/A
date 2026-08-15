"""1进2 接力 — 独立回测入口

用法:
    python -m ashare_review.analysis.strategy_regime.backtest_one_two [--use-cache] [--rebuild-state]
"""
import os
import argparse
from datetime import date

from ...data.tdx_reader import TdxReader
from . import market_state as ms
from . import export as ex
from . import one_two_backtest as otb

START = date(2025, 8, 8)
END = date(2026, 8, 7)
PERIOD = '2025-08 ~ 2026-08'
REGIME_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'strategy_regime')
STATE_CACHE = os.path.join(REGIME_DIR, 'market_state.csv')
TRADE_CACHE = os.path.join(REGIME_DIR, 'one_two_trades.json')
OUT = os.path.join(REGIME_DIR, '1进2接力_回测_202508-202608.xlsx')


def main():
    ap = argparse.ArgumentParser(description='1进2 接力独立回测')
    ap.add_argument('--use-cache', action='store_true', help='复用已缓存交易（默认每次真实重跑）')
    ap.add_argument('--rebuild-state', action='store_true', help='重建市场状态缓存')
    args = ap.parse_args()

    print('=' * 60)
    print('  1进2 接力 — 独立回测')
    print(f'  区间: {PERIOD}')
    print('=' * 60)

    state = ms.load_state(START, END, STATE_CACHE, rebuild=args.rebuild_state)
    print(f'[1/3] 行情分类完成: {len(state)} 天')

    print('[2/3] 运行 1进2 回测 ...')
    if args.use_cache and os.path.exists(TRADE_CACHE):
        import json
        with open(TRADE_CACHE, 'r', encoding='utf-8') as f:
            trades = json.load(f)
        print(f'  (使用缓存) 1进2 交易: {len(trades)} 笔')
    else:
        bt = otb.OneTwoBacktest(TdxReader())
        trades = bt.run(START, END)
        print(f'  1进2 交易: {len(trades)} 笔（含被竞价过滤 {sum(1 for t in trades if t.get("skipped_gap"))} 条）')

    print('[3/3] 导出 xlsx ...')
    ex.tag_trades(trades, ex._regime_map(state))
    ex.export_strategy_xlsx('1进2', trades, state, OUT, period=PERIOD)

    st = ex.strat_stats(trades)
    print(f'\n===== 1进2 接力 结果 =====')
    print(f'  交易 {st["n"]}（跳过 {st["skipped"]}）· 胜率 {st["win_rate"]}% · 笔均 {st["avg_ret"]:+.2f}% · 盈亏比 {st["pf"]}')
    print(f'  → {OUT}')


if __name__ == '__main__':
    main()
