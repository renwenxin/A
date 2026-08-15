"""总筛选回测 — 对历史每日 Top 3 重叠标的做止盈止损回测

逻辑：
1. 遍历历史交易日，每日运行总筛选（4策略取其重叠）
2. 取 Top 3 标的，获取入场/止损/止盈价
3. 向前查看未来N日的最高/最低价
4. 判断先触发止盈还是止损 → 统计胜率

用法：
    python -m ashare_review.analysis.backtest --days 60 --hold 10
"""
import sys, os, json
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ashare_review.data.tdx_reader import TdxReader
from ashare_review.data.akshare_fetcher import AkshareFetcher
from ashare_review.screening.leader import LeaderScreener
from ashare_review.screening.breakout import BreakoutScreener
from ashare_review.screening.sector_divergence import SectorDivergenceScreener
from ashare_review.screening.institution import InstitutionScreener
from ashare_review.analysis.pick_analysis import analyze_pick
from ashare_review.utils.calendar import TradingCalendar


def _is_a_stock(code: str) -> bool:
    """过滤A股（排除B股、港股通等非主流品种）"""
    if not code or len(code) != 6:
        return False
    return (code.startswith(('0', '3', '6', '4', '8')) and
            not code.startswith(('900', '200')))


class BacktestSummary:
    """总筛选回测器"""

    HOLD_DAYS = 10          # 默认持仓天数
    ENTRY_OFFSET = 0.995    # 入场价 = 收盘价 * 0.995（次日开盘附近）

    def __init__(self):
        self.tdx = TdxReader()
        self.ak = AkshareFetcher()
        self.calendar = TradingCalendar()
        self.screeners = {
            'leader': LeaderScreener(self.tdx, self.ak),
            'breakout': BreakoutScreener(self.tdx, self.ak),
            'sector_divergence': SectorDivergenceScreener(self.tdx, self.ak),
            'institution': InstitutionScreener(self.tdx, self.ak),
        }

    def run(self, lookback_days: int = 60, hold_days: int = 10,
            top_n: int = 3, min_score: int = 0,
            min_price: float = 0, max_price: float = 0) -> Dict:
        """主入口：回测最近 N 个交易日

        Parameters
        ----------
        lookback_days : int
            回测天数
        hold_days : int
            持仓天数上限
        top_n : int
            每日取前N个标的
        min_score : int
            最低评分阈值（0=不过滤）
        min_price : float
            最低股价（0=不限），如 10 表示只买 ≥10 元的股票
        max_price : float
            最高股价（0=不限），如 20 表示只买 ≤20 元的股票
        """
        self.HOLD_DAYS = hold_days
        trade_dates = self._get_trade_dates(lookback_days)
        print(f'回测范围: {trade_dates[0]} ~ {trade_dates[-1]} 共{len(trade_dates)}个交易日')

        all_trades = []
        skipped_days = 0

        for i, td in enumerate(trade_dates):
            td_str = td.strftime('%Y%m%d')
            print(f'\r[{i+1}/{len(trade_dates)}] {td_str} ...', end='', flush=True)

            # ---- 大盘环境过滤 ----
            market_ok = self._check_market_env(td_str)
            if not market_ok:
                skipped_days += 1
                continue

            # ---- 1. 运行四个筛选器 ----
            picks = self._run_summary_for_date(td_str)
            if len(picks) < 2:
                skipped_days += 1
                continue

            # ---- 2. 评分过滤 ----
            if min_score > 0:
                picks = [p for p in picks if p.get('avg_score', 0) >= min_score]
                if len(picks) < 1:
                    skipped_days += 1
                    continue

            # ---- 2.5 价格过滤（收盘价 = entry_low / 0.99） ----
            if min_price > 0 or max_price > 0:
                filtered = []
                for p in picks:
                    signal_close = p['entry_low'] / 0.99 if p.get('entry_low', 0) > 0 else 0
                    if signal_close <= 0:
                        continue
                    if min_price > 0 and signal_close < min_price:
                        continue
                    if max_price > 0 and signal_close > max_price:
                        continue
                    filtered.append(p)
                picks = filtered
                if len(picks) < 1:
                    skipped_days += 1
                    continue

            # ---- 3. 取Top N ----
            top_picks = picks[:top_n]

            # ---- 3. 对每个标的模拟交易 ----
            for pick in top_picks:
                trade = self._simulate_trade(
                    pick['code'], pick['name'],
                    td_str, hold_days,
                    pick.get('entry_low'), pick.get('entry_high'),
                    pick.get('stop_loss'), pick.get('target'),
                    pick.get('match_count', 0), pick.get('avg_score', 0),
                )
                if trade:
                    all_trades.append(trade)

        print()
        return self._summarize(all_trades, skipped_days, lookback_days)

    # ------------------------------------------------------------------
    # 大盘环境过滤 — 市场不好时空仓
    # ------------------------------------------------------------------
    _index_cache = None

    def _check_market_env(self, trade_date: str) -> bool:
        """检查当日大盘环境：上证跌幅>1.5%则空仓"""
        if self._index_cache is None:
            try:
                df = self.tdx.read_daily('999999', 'sh')
                self._index_cache = {}
                if 'trade_date' in df.columns:
                    for i in range(1, len(df)):
                        td = df['trade_date'].iloc[i]
                        if isinstance(td, datetime):
                            td = td.date()
                        elif isinstance(td, date):
                            pass
                        else:
                            continue
                        prev = float(df['close'].iloc[i-1])
                        curr = float(df['close'].iloc[i])
                        self._index_cache[td] = (curr - prev) / prev
            except Exception:
                self._index_cache = {}

        try:
            entry_dt = datetime.strptime(trade_date, '%Y%m%d').date()
            chg = self._index_cache.get(entry_dt)
            if chg is not None and chg < -0.015:
                return False
        except Exception:
            pass
        return True

    # ------------------------------------------------------------------
    # 获取交易日列表
    # ------------------------------------------------------------------
    def _get_trade_dates(self, lookback: int) -> List[date]:
        """获取最近N个交易日（不含今天）"""
        d = date.today() - timedelta(days=1)
        dates = []
        while len(dates) < lookback:
            if d.weekday() < 5 and self.calendar.is_trading_day(d):
                dates.append(d)
            d -= timedelta(days=1)
            if len(dates) == 0 and (date.today() - d).days > 365:
                break
        return list(reversed(dates))

    # ------------------------------------------------------------------
    # 运行每日总筛选
    # ------------------------------------------------------------------
    def _run_summary_for_date(self, trade_date: str) -> List[Dict]:
        """对指定日期运行四策略汇总，返回重叠标的列表"""
        all_by_code = {}

        for name, screener in self.screeners.items():
            try:
                # 龙头和板块分歧传入历史日期，用指定日的涨停池数据
                if name in ('sector_divergence', 'leader'):
                    results = screener.screen(trade_date=trade_date)
                else:
                    results = screener.screen()
                for r in results:
                    if not _is_a_stock(r.code):
                        continue
                    if r.code not in all_by_code:
                        all_by_code[r.code] = {
                            'code': r.code, 'name': r.name,
                            'strategies': [], 'scores': {}, 'reasons': {},
                        }
                    all_by_code[r.code]['strategies'].append(name)
                    all_by_code[r.code]['scores'][name] = r.score
                    all_by_code[r.code]['reasons'][name] = r.reasons[:2]
            except Exception:
                continue

        # 找重叠标的
        picks = []
        for code, info in all_by_code.items():
            match_count = len(info['strategies'])
            if match_count >= 2:
                avg_score = sum(info['scores'].values()) / match_count
                # 快速计算止损止盈（不用完整 analyze_pick，回测速度关键）
                # 回测时传入 trade_date 以获取该日期对应的价格
                entry_low, entry_high, stop_loss, target = \
                    self._fast_pick_params(code, info['strategies'][0], trade_date=trade_date)
                picks.append({
                    'code': info['code'],
                    'name': info['name'],
                    'match_count': match_count,
                    'avg_score': round(avg_score),
                    'strategies': info['strategies'],
                    'entry_low': entry_low,
                    'entry_high': entry_high,
                    'stop_loss': stop_loss,
                    'target': target,
                })

        picks.sort(key=lambda x: (x['match_count'], x['avg_score']), reverse=True)
        return picks

    # ------------------------------------------------------------------
    # 快速计算入场/止损/止盈（不回测中不用完整 analyze_pick）
    # ------------------------------------------------------------------
    def _fast_pick_params(self, code: str, strategy: str, trade_date: str = None):
        """快速读取TDX数据计算入场/止损/止盈

        Parameters
        ----------
        trade_date : str, optional
            目标交易日期 YYYYMMDD。传入时只用 <= 该日的数据计算。
        """
        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith('8') or code.startswith('4'):
            market = 'bj'
        try:
            df = self.tdx.read_daily(code, market)
            if len(df) < 20:
                return (0, 0, 0, 0)

            # 如果指定了日期，只用到该日为止的数据
            if trade_date:
                try:
                    target_d = datetime.strptime(trade_date, '%Y%m%d').date()
                except ValueError:
                    target_d = None
                if target_d:
                    df_filtered = df[df['trade_date'].apply(
                        lambda x: (x.date() if hasattr(x, 'date') else x) <= target_d
                    )]
                    if len(df_filtered) >= 20:
                        df = df_filtered

            close = float(df['close'].iloc[-1])
            high_20 = float(df['high'].iloc[-20:].max())
            low_10 = float(df['low'].iloc[-10:].min())

            entry_low = round(close * 0.99, 2)
            entry_high = round(close * 1.02, 2)

            # 止损：动态计算 — 优先用近期支撑，避免固定比例失真
            # 1) 10日最低价（如果距现价 ≤ 8%，用它的 0.98 倍作为止损）
            # 2) 回退到 -5%（确保最大亏损可控）
            atr_stop = round(low_10 * 0.98, 2)
            fixed_stop = round(close * 0.95, 2)
            # 取两者中较高者（更贴近实战支撑，同时不超过 -8% 硬止损）
            stop_loss = max(atr_stop, fixed_stop)
            hard_cap = round(close * 0.92, 2)
            if stop_loss < hard_cap:
                stop_loss = hard_cap  # 极端情况兜底，单笔最多亏8%

            # 止盈：20日高点或8%
            target = round(max(high_20, close * 1.08), 2)
            if target <= close * 1.03:
                target = round(close * 1.08, 2)

            return (entry_low, entry_high, stop_loss, target)
        except Exception:
            return (0, 0, 0, 0)

    # ------------------------------------------------------------------
    # 模拟单笔交易
    # ------------------------------------------------------------------
    def _simulate_trade(self, code: str, name: str,
                         entry_date: str, hold_days: int,
                         entry_low: float, entry_high: float,
                         stop_loss: float, target: float,
                         match_count: int, score: int) -> Optional[Dict]:
        """模拟一笔交易：从入场日开始，看未来N天内先触发止盈还是止损"""
        if stop_loss <= 0 or target <= 0:
            return None

        # 入场价：取入场区间的中值
        entry_price = (entry_low + entry_high) / 2 if entry_low > 0 else 0
        if entry_price <= 0:
            return None

        # 读取未来行情
        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith('8') or code.startswith('4'):
            market = 'bj'
        try:
            df = self.tdx.read_daily(code, market)
            if df.empty or len(df) < 20:
                return None
        except Exception:
            return None

        # 找到入场日在df中的位置 — TDX DataFrame 用整数索引，
        # trade_date 是数据列（可能为 date 对象或字符串）
        try:
            entry_dt = datetime.strptime(entry_date, '%Y%m%d').date()
        except ValueError:
            return None

        # 解析 trade_date 列（datetime 是 date 的子类，需先判断）
        trade_dates = []
        if 'trade_date' in df.columns:
            for i in range(len(df)):
                td = df['trade_date'].iloc[i]
                if isinstance(td, datetime):
                    trade_dates.append(td.date())
                elif isinstance(td, date):
                    trade_dates.append(td)
                elif hasattr(td, 'to_pydatetime'):
                    trade_dates.append(td.to_pydatetime().date())
                elif isinstance(td, str):
                    try:
                        trade_dates.append(datetime.strptime(td[:10], '%Y-%m-%d').date())
                    except ValueError:
                        trade_dates.append(None)
                else:
                    trade_dates.append(None)
        else:
            return None

        # 找入场日索引
        entry_idx = None
        for i, d in enumerate(trade_dates):
            if d == entry_dt:
                entry_idx = i
                break

        if entry_idx is None:
            return None

        # 向前看 hold_days 个交易日
        exit_idx = min(entry_idx + 1 + hold_days, len(df) - 1)
        if exit_idx <= entry_idx + 1:
            return None  # 没有后续数据

        future = df.iloc[entry_idx + 1:exit_idx + 1]
        if future.empty:
            return None

        # 判断触发止盈还是止损
        result = 'timeout'
        exit_price = float(future['close'].iloc[-1])
        exit_date = str(trade_dates[min(exit_idx, len(trade_dates)-1)]
                        if exit_idx < len(trade_dates) else entry_dt)
        days_held = hold_days

        for j in range(len(future)):
            row = future.iloc[j]
            day_open = float(row['open'])
            high = float(row['high'])
            low = float(row['low'])

            # T+1 约束：买入当天(T)不能卖，future[0] 才是 T+1（首个可卖日）
            # 止损：日内最低价触及止损线 → 以当天开盘价或止损价中较差者成交
            # （跳空低开时开盘价 < 止损价，实际只能以开盘价成交）
            if low <= stop_loss:
                result = 'loss'
                exit_price = round(min(stop_loss, day_open), 2)
                exit_idx_actual = entry_idx + 1 + j
                exit_date = str(trade_dates[min(exit_idx_actual, len(trade_dates)-1)]
                                if exit_idx_actual < len(trade_dates) else entry_dt)
                days_held = j + 1
                break

            # 止盈：日内最高价触及止盈线 → 以止盈价成交（流动性假设成立）
            if high >= target:
                result = 'win'
                exit_price = target
                exit_idx_actual = entry_idx + 1 + j
                exit_date = str(trade_dates[min(exit_idx_actual, len(trade_dates)-1)]
                                if exit_idx_actual < len(trade_dates) else entry_dt)
                days_held = j + 1
                break

        # 计算收益率
        ret_pct = (exit_price - entry_price) / entry_price * 100

        return {
            'code': code,
            'name': name,
            'entry_date': entry_date,
            'entry_price': round(entry_price, 2),
            'stop_loss': round(stop_loss, 2),
            'target': round(target, 2),
            'exit_date': exit_date,
            'exit_price': round(exit_price, 2),
            'result': result,
            'return_pct': round(ret_pct, 1),
            'days_held': days_held,
            'match_count': match_count,
            'score': score,
        }

    # ------------------------------------------------------------------
    # 汇总统计
    # ------------------------------------------------------------------
    def _summarize(self, trades: List[Dict], skipped_days: int,
                    total_days: int) -> Dict:
        if not trades:
            return {
                'total_trades': 0,
                'skipped_days': skipped_days,
                'total_days': total_days,
                'error': '无有效交易记录',
            }

        wins = [t for t in trades if t['result'] == 'win']
        losses = [t for t in trades if t['result'] == 'loss']
        timeouts = [t for t in trades if t['result'] == 'timeout']

        win_count = len(wins)
        loss_count = len(losses)
        timeout_count = len(timeouts)
        total = len(trades)

        win_rate = win_count / max(win_count + loss_count, 1) * 100

        avg_win = np.mean([t['return_pct'] for t in wins]) if wins else 0
        avg_loss = np.mean([t['return_pct'] for t in losses]) if losses else 0
        avg_return = np.mean([t['return_pct'] for t in trades])

        # 盈亏比
        total_profit = sum(t['return_pct'] for t in wins)
        total_loss = sum(abs(t['return_pct']) for t in losses)
        profit_factor = total_profit / max(total_loss, 0.01)

        # 按匹配数分组
        by_match = defaultdict(list)
        for t in trades:
            by_match[t['match_count']].append(t)

        match_stats = {}
        for mc, ts in sorted(by_match.items()):
            w = len([t for t in ts if t['result'] == 'win'])
            l = len([t for t in ts if t['result'] == 'loss'])
            match_stats[f'{mc}战法重叠'] = {
                'trades': len(ts),
                'wins': w,
                'losses': l,
                'win_rate': round(w / max(w + l, 1) * 100, 1),
                'avg_return': round(np.mean([t['return_pct'] for t in ts]), 1),
            }

        # 最近N笔交易（方便review）
        recent = sorted(trades, key=lambda x: x['entry_date'], reverse=True)[:10]

        return {
            'total_trades': total,
            'wins': win_count,
            'losses': loss_count,
            'timeouts': timeout_count,
            'win_rate': round(win_rate, 1),
            'avg_win_return': round(float(avg_win), 1),
            'avg_loss_return': round(float(avg_loss), 1),
            'avg_return': round(float(avg_return), 1),
            'profit_factor': round(float(profit_factor), 2),
            'skipped_days': skipped_days,
            'total_days': total_days,
            'by_match': match_stats,
            'recent_trades': recent,
            'all_trades': sorted(trades, key=lambda x: x['entry_date'], reverse=True),
        }



# ======================================================================
# 文件导出
# ======================================================================
RESULT_LABELS = {'win': '胜', 'loss': '负', 'timeout': '超时'}


def _export_csv(r: Dict, filepath: str):
    """导出全部交易记录为CSV文件"""
    import csv
    trades = r.get('all_trades', r.get('recent_trades', []))
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['入场日期', '代码', '名称', '入场价', '止损价', '止盈价',
                         '离场日期', '离场价', '结果', '收益率%', '持仓天数',
                         '策略重叠数', '评分'])
        for t in trades:
            writer.writerow([
                t['entry_date'], t['code'], t['name'],
                t['entry_price'], t['stop_loss'], t['target'],
                t['exit_date'], t['exit_price'],
                RESULT_LABELS.get(t['result'], t['result']),
                t['return_pct'], t['days_held'],
                t['match_count'], t.get('score', 0),
            ])
    print(f'\n[CSV] 全部 {len(trades)} 笔交易已导出 → {filepath}')


def _export_txt(r: Dict, filepath: str):
    """导出全部交易记录为可读TXT文件"""
    trades = r.get('all_trades', r.get('recent_trades', []))
    lines = []
    lines.append('=' * 70)
    lines.append('  总筛选回测 — 完整交易记录')
    lines.append('=' * 70)
    lines.append('')
    lines.append(f'==== 交易统计 ====')
    lines.append(f'   总交易: {r["total_trades"]}笔 | 胜: {r["wins"]} | 负: {r["losses"]} | '
                 f'超时: {r.get("timeouts", 0)} | 有效天数: {r["total_days"]-r["skipped_days"]}')
    lines.append(f'   胜率: {r["win_rate"]}% | 平均收益: {r["avg_return"]}%')
    lines.append(f'   平均盈利: {r["avg_win_return"]}% | 平均亏损: {r["avg_loss_return"]}%')
    lines.append(f'   盈亏比: {r["profit_factor"]}')
    lines.append('')
    lines.append(f'==== 按重叠数分组 ====')
    for key, stats in r.get('by_match', {}).items():
        lines.append(f'   {key}: {stats["trades"]}笔 胜率{stats["win_rate"]}% '
                     f'均收益{stats["avg_return"]}%')
    lines.append('')
    lines.append(f'==== 全部交易记录 ({len(trades)}笔) ====')
    lines.append(f'   {"日期":<12} {"代码":<8} {"名称":<8} {"结果":<6} '
                 f'{"收益%":>7} {"持仓天":>6} {"匹配":>4} {"入场价":>8} {"离场价":>8}')
    lines.append('   ' + '-' * 80)
    for t in trades:
        if t['result'] == 'win':
            icon = '[W]'
        elif t['result'] == 'loss':
            icon = '[L]'
        else:
            icon = '[T]'
        lines.append(f'   {t["entry_date"]:<12} {t["code"]:<8} {t["name"]:<8} '
                     f'{icon} {t["return_pct"]:>+6.1f}% '
                     f'{t["days_held"]:>4}天 {t["match_count"]:>2}战法 '
                     f'{t["entry_price"]:>8.2f} {t["exit_price"]:>8.2f}')
    lines.append('')
    lines.append('[说明] 当日收盘运行总筛选取Top3，以次日开盘价入场，按止盈止损位判断。'
                 '[W]=胜 [L]=负 [T]=超时')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'\n[TXT] 全部 {len(trades)} 笔交易已导出 → {filepath}')


def _export_json(r: Dict, filepath: str):
    """导出完整回测结果（含统计和全部交易）为JSON文件"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    trades_count = len(r.get('all_trades', r.get('recent_trades', [])))
    print(f'\n[JSON] 全部 {trades_count} 笔交易已导出 → {filepath}')


# ======================================================================
# CLI
# ======================================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='总筛选回测')
    parser.add_argument('--days', type=int, default=30,
                        help='回测天数 (默认30)')
    parser.add_argument('--hold', type=int, default=10,
                        help='持仓天数上限 (默认10)')
    parser.add_argument('--top', type=int, default=3,
                        help='每日取Top N标的 (默认3)')
    parser.add_argument('--min-score', type=int, default=0, metavar='N',
                        help='最低评分阈值 (默认0=不过滤，建议85+)')
    parser.add_argument('--min-price', type=float, default=0, metavar='P',
                        help='最低股价 (默认0=不限)，如 10 表示只买≥10元')
    parser.add_argument('--max-price', type=float, default=0, metavar='P',
                        help='最高股价 (默认0=不限)，如 20 表示只买≤20元')
    parser.add_argument('--json', action='store_true',
                        help='输出JSON格式到stdout')
    parser.add_argument('--csv', type=str, default=None, metavar='PATH',
                        help='导出全部交易为CSV文件')
    parser.add_argument('--output', '-o', type=str, default=None, metavar='PATH',
                        help='导出全部交易到文件（根据扩展名自动选CSV/TXT/JSON）')
    args = parser.parse_args()

    score_info = f' 评分≥{args.min_score}' if args.min_score > 0 else ''
    price_info = ''
    if args.min_price > 0 or args.max_price > 0:
        lo = f'{args.min_price:.0f}' if args.min_price > 0 else '0'
        hi = f'{args.max_price:.0f}' if args.max_price > 0 else '∞'
        price_info = f' 股价{lo}-{hi}元'
    print(f'\n{"="*60}')
    print(f'  总筛选回测 — Top{args.top} 持仓{args.hold}天 回看{args.days}天{score_info}{price_info}')
    print(f'{"="*60}\n')

    bt = BacktestSummary()
    result = bt.run(
        lookback_days=args.days,
        hold_days=args.hold,
        top_n=args.top,
        min_score=args.min_score,
        min_price=args.min_price,
        max_price=args.max_price,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_report(result)

    # ---- 文件导出 ----
    csv_path = args.csv
    output_path = args.output

    # --csv 优先
    if csv_path:
        _export_csv(result, csv_path)

    # --output 根据扩展名自动判断
    if output_path:
        if output_path.endswith('.csv'):
            _export_csv(result, output_path)
        elif output_path.endswith('.json'):
            _export_json(result, output_path)
        else:
            _export_txt(result, output_path)


def _print_report(r: Dict):
    """格式化输出回测报告"""
    if r.get('error'):
        print(f'[ERROR] {r["error"]}')
        return

    print(f'==== 交易统计 ====')
    print(f'   总交易: {r["total_trades"]}笔 | 胜: {r["wins"]} | 负: {r["losses"]} | '
          f'超时: {r.get("timeouts", 0)} | 有效天数: {r["total_days"]-r["skipped_days"]}')
    print(f'   胜率: {r["win_rate"]}% | 平均收益: {r["avg_return"]}%')
    print(f'   平均盈利: {r["avg_win_return"]}% | 平均亏损: {r["avg_loss_return"]}%')
    print(f'   盈亏比: {r["profit_factor"]}')

    print(f'\n==== 按重叠数分组 ====')
    for key, stats in r.get('by_match', {}).items():
        print(f'   {key}: {stats["trades"]}笔 胜率{stats["win_rate"]}% '
              f'均收益{stats["avg_return"]}%')

    print(f'\n==== 最近交易 (最近10笔，全部记录见文件导出) ====')
    print(f'   {"日期":<12} {"代码":<8} {"名称":<8} {"结果":<6} '
          f'{"收益%":>7} {"持仓天":>6} {"匹配":>4}')
    for t in r.get('recent_trades', []):
        if t['result'] == 'win':
            icon = '[W]'
        elif t['result'] == 'loss':
            icon = '[L]'
        else:
            icon = '[T]'
        print(f'   {t["entry_date"]:<12} {t["code"]:<8} {t["name"]:<8} '
              f'{icon} {t["return_pct"]:>+6.1f}% '
              f'{t["days_held"]:>4}天 {t["match_count"]:>2}战法')

    print(f'\n[说明] 当日收盘运行总筛选取Top3，以次日开盘价入场，按止盈止损位判断。'
          f'[W]=胜 [L]=负 [T]=超时 '
          f'| 共 {r["total_trades"]} 笔交易，终端仅显示最近10笔')


if __name__ == '__main__':
    main()
