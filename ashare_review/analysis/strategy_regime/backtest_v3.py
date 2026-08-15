"""启动突破 V3 — 独立回测入口

用法:
    python -m ashare_review.analysis.strategy_regime.backtest_v3 [--use-cache] [--rebuild-state]
"""
import os
import argparse
from datetime import date

from ...data.tdx_reader import TdxReader
from ..v3_backtest import V3Backtest
from . import market_state as ms
from . import export as ex
from . import causal_universe as cu
from . import sector_strength as ss

START = date(2025, 8, 8)
END = date(2026, 8, 7)
PERIOD = '2025-08 ~ 2026-08'
REGIME_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'strategy_regime')
STATE_CACHE = os.path.join(REGIME_DIR, 'market_state.csv')
UNIVERSE_CACHE = os.path.join(REGIME_DIR, 'causal_universe.json')
SECTOR_CACHE = os.path.join(REGIME_DIR, 'sector_partners.json')
OUT = os.path.join(REGIME_DIR, '启动突破V3_回测_202508-202608.xlsx')


def main():
    ap = argparse.ArgumentParser(description='启动突破 V3 独立回测')
    ap.add_argument('--use-cache', action='store_true', help='复用已缓存交易（默认每次真实重跑）')
    ap.add_argument('--rebuild-state', action='store_true', help='重建市场状态缓存')
    ap.add_argument('--static-pool', action='store_true',
                    help='用静态 limit_up_pool.json（默认用因果候选池，无幸存者偏差）')
    args = ap.parse_args()

    print('=' * 60)
    print('  启动突破 V3 — 独立回测')
    print(f'  区间: {PERIOD}')
    print('=' * 60)

    # 市场状态（行情标签用）
    state = ms.load_state(START, END, STATE_CACHE, rebuild=args.rebuild_state)
    print(f'[1/4] 行情分类完成: {len(state)} 天')

    # 行情→日期 映射（供按行情调仓）
    regime_map = {str(r['date']): r['regime'] for _, r in state.iterrows()}
    def regime_of_day(d):
        return regime_map.get(str(d), '震荡观望')

    # 因果候选池（默认）
    uni = None
    ss_obj = None
    if not args.static_pool:
        print('[2/4] 构建因果候选池（近250日涨停≥10，逐日判定，无未来函数）...')
        uni = cu.CausalUniverse(TdxReader(), START, END, cache_path=UNIVERSE_CACHE)
        print(f'  ever-eligible: {len(uni.codes)} 只')
        print('  构建板块共振（共涨停聚类，同伴关系用回测前一年）...')
        ss_obj = ss.SectorStrength(uni, date(2024, 8, 8), date(2025, 8, 8),
                                   min_co=2, cache_path=SECTOR_CACHE)

    # 行情仓位权重（战法: 大盘×个股矩阵）
    REGIME_W = {'强势趋势': 1.0, '题材轮动': 0.7, '震荡观望': 0.3,
                '弱市回调': 0.2, '退潮下跌': 0.0, '冰点超跌': 0.3}

    # ── 版本对比 ──
    print('[3/4] 运行 V3（全行情 / 调仓★ / 集中+轮换 对比）...')
    res0 = V3Backtest().run(START, END, causal_universe=uni)                        # 全行情照做
    res1 = V3Backtest().run(START, END, causal_universe=uni,
                            regime_weights=REGIME_W, regime_of_day=regime_of_day)    # 10仓10%+调仓
    res2 = V3Backtest().run(START, END, causal_universe=uni,
                            regime_weights=REGIME_W, regime_of_day=regime_of_day,
                            max_positions=4, position_pct=0.25, rotation=True)      # 4仓25%+每日轮换 ★推荐
    res3 = V3Backtest().run(START, END, causal_universe=uni,
                            regime_weights=REGIME_W, regime_of_day=regime_of_day,
                            max_positions=3, position_pct=0.33, rotation=True)      # 3仓33%+轮换
    trades = res2['trades']  # 推荐版 = 因果池 + 调仓 + 4仓25% + 每日轮换

    print(f'\n  [全行情10仓]      组合收益 {res0["cumulative_return"]:+.1f}% · 回撤 {res0["max_drawdown"]:.1f}% · {len(res0["trades"])}笔')
    print(f'  [调仓10仓10%]     组合收益 {res1["cumulative_return"]:+.1f}% · 回撤 {res1["max_drawdown"]:.1f}% · {len(res1["trades"])}笔')
    print(f'  [4仓25%+轮换]★    组合收益 {res2["cumulative_return"]:+.1f}% · 回撤 {res2["max_drawdown"]:.1f}% · {len(res2["trades"])}笔')
    print(f'  [3仓33%+轮换]     组合收益 {res3["cumulative_return"]:+.1f}% · 回撤 {res3["max_drawdown"]:.1f}% · {len(res3["trades"])}笔')

    # 行情标签
    print('[4/4] 导出 xlsx ...')
    ex.tag_trades(trades, ex._regime_map(state))
    meta_note = (f'V3 资金模型：100万本金 · 4仓×25% + 每日轮换 + 按行情调仓 + 因果候选池（推荐配置）\n'
                 f'  组合累计收益 {res2["cumulative_return"]}% · 最大回撤 {res2["max_drawdown"]}%\n'
                 f'  对照[全行情10仓]: {res0["cumulative_return"]}% · 回撤 {res0["max_drawdown"]}%\n'
                 f'  对照[调仓10仓10%]: {res1["cumulative_return"]}% · 回撤 {res1["max_drawdown"]}%\n'
                 f'  对照[3仓33%+轮换]: {res3["cumulative_return"]}% · 回撤 {res3["max_drawdown"]}%\n'
                 f'  权重: 强势1.0/题材0.7/震荡0.3/弱市0.2/退潮0.0/冰点0.3')
    notes_extra = [
        ('集中+轮换说明', '4仓×25% + 每日轮换：满仓后若新信号评分 > 最弱持仓强度(信号分×(1+当前收益))，'
             '次日开盘卖出最弱、换入新标的（只做最强，淘汰弱鸡）。'),
        ('', '相比 10仓10%：收益 +46%→+100%（近翻倍），回撤 15.8%→26.7%，风险收益比 2.92→3.73（最优）。'
             '4仓 比 3仓 分散（单票25%控风险），也更贴近战法"≤4只、单票≤35%"的仓位纪律。'),
        ('', '3仓33% 收益+88%但回撤33.6%；回撤熔断(15%/25%)在集中持仓下过度触发(频繁清仓错失反弹)，不建议叠加。'),
        ('回撤修正说明', '此前 47~64% 最大回撤为 bug 虚高（weekday 当交易日，节假日持仓按0估值）。'
             '已改真实交易日历，真实回撤: 10仓16%、4仓轮换27%。'),
        ('板块共振实验结论', '离线无真实板块映射，用共涨停聚类代理。实测当日板块拥挤度对 V3 是反向信号'
             '(涨停潮5+只胜率仅40%，孤立突破最好+3.65%)——追高跟风。真实板块强度需完整板块映射再验证。'),
    ]
    ex.export_strategy_xlsx('V3启动突破', trades, state, OUT,
                            period=PERIOD, meta_note=meta_note, notes_extra=notes_extra)

    st = ex.strat_stats(trades)
    print(f'\n===== 启动突破 V3（推荐: 因果池+调仓+4仓25%+每日轮换）结果 =====')
    print(f'  交易 {st["n"]} · 胜率 {st["win_rate"]}% · 笔均 {st["avg_ret"]:+.2f}% · '
          f'盈亏比 {st["pf"]} · 组合累计 {res2["cumulative_return"]}% · 最大回撤 {res2["max_drawdown"]}%')
    print(f'  → {OUT}')


if __name__ == '__main__':
    main()
