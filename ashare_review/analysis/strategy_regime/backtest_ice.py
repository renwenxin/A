"""冰点抄底 — 独立回测入口

用法:
    python -m ashare_review.analysis.strategy_regime.backtest_ice [--use-cache] [--rebuild-state]
"""
import os
import argparse
from datetime import date

from ...data.tdx_reader import TdxReader
from . import market_state as ms
from . import export as ex
from . import ice_backtest as ice
from . import causal_universe as cu

START = date(2025, 8, 8)
END = date(2026, 8, 7)
PERIOD = '2025-08 ~ 2026-08'
REGIME_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'strategy_regime')
STATE_CACHE = os.path.join(REGIME_DIR, 'market_state.csv')
UNIVERSE_CACHE = os.path.join(REGIME_DIR, 'causal_universe.json')
TRADE_CACHE = os.path.join(REGIME_DIR, 'ice_trades.json')
OUT = os.path.join(REGIME_DIR, '冰点抄底_回测_202508-202608.xlsx')


def main():
    ap = argparse.ArgumentParser(description='冰点抄底独立回测')
    ap.add_argument('--use-cache', action='store_true', help='复用已缓存交易（默认每次真实重跑）')
    ap.add_argument('--rebuild-state', action='store_true', help='重建市场状态缓存')
    ap.add_argument('--static-pool', action='store_true',
                    help='用静态 limit_up_pool.json（默认用因果候选池）')
    args = ap.parse_args()

    print('=' * 60)
    print('  冰点抄底 — 独立回测')
    print(f'  区间: {PERIOD}')
    print('=' * 60)

    state = ms.load_state(START, END, STATE_CACHE, rebuild=args.rebuild_state)
    print(f'[1/4] 行情分类完成: {len(state)} 天')

    uni = None
    if not args.static_pool:
        print('[2/4] 构建因果候选池...')
        uni = cu.CausalUniverse(TdxReader(), START, END, cache_path=UNIVERSE_CACHE)
        print(f'  ever-eligible: {len(uni.codes)} 只')

    print('[3/4] 运行 冰点抄底 回测 ...')
    bt = ice.IceBottomBacktest(TdxReader())
    trades = bt.run(state, START, END, causal_universe=uni)
    print(f'  冰点 交易: {len(trades)} 笔')

    print('[4/4] 导出 xlsx ...')
    ex.tag_trades(trades, ex._regime_map(state))
    # 冰点抄底信号本质是"冰点超跌"行情
    for t in trades:
        t['regime'] = '冰点超跌'
    ex.export_strategy_xlsx('冰点抄底', trades, state, OUT, period=PERIOD)

    st = ex.strat_stats(trades)
    print(f'\n===== 冰点抄底 结果 =====')
    print(f'  交易 {st["n"]} · 胜率 {st["win_rate"]}% · 笔均 {st["avg_ret"]:+.2f}% · 盈亏比 {st["pf"]}')
    print(f'  → {OUT}')


if __name__ == '__main__':
    main()
