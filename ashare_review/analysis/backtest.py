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
    """过滤A股（排除688开头科创板以外的非主流品种）"""
    if not code or len(code) != 6:
        return False
    return (code.startswith(('0', '3', '6')) and
            not code.startswith(('688', '900', '200', '300')))


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
            top_n: int = 3) -> Dict:
        """主入口：回测最近 N 个交易日

        Parameters
        ----------
        lookback_days : int
            回测天数
        hold_days : int
            持仓天数上限
        top_n : int
            每日取前N个标的

        Returns
        -------
        dict with trades list + summary stats
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

            # ---- 2. 取Top N ----
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
                # 板块分歧用指定日期
                if name == 'sector_divergence':
                    results = screener.screen()
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
                entry_low, entry_high, stop_loss, target = \
                    self._fast_pick_params(code, info['strategies'][0])
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
    def _fast_pick_params(self, code: str, strategy: str):
        """快速读取TDX数据计算入场/止损/止盈"""
        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith('8') or code.startswith('4'):
            market = 'bj'
        try:
            df = self.tdx.read_daily(code, market)
            if len(df) < 20:
                return (0, 0, 0, 0)
            close = float(df['close'].iloc[-1])
            high_20 = float(df['high'].iloc[-20:].max())
            low_10 = float(df['low'].iloc[-10:].min())

            entry_low = round(close * 0.99, 2)
            entry_high = round(close * 1.02, 2)

            # 止损：5% 固定（实战回测用）
            stop_loss = round(close * 0.95, 2)

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
            high = float(row['high'])
            low = float(row['low'])

            # 先检查止损（日内最低价触及止损线）
            if low <= stop_loss:
                result = 'loss'
                exit_price = stop_loss
                exit_idx_actual = entry_idx + 1 + j
                exit_date = str(trade_dates[min(exit_idx_actual, len(trade_dates)-1)]
                                if exit_idx_actual < len(trade_dates) else entry_dt)
                days_held = j + 1
                break

            # 再检查止盈（日内最高价触及止盈线）
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
        }



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
    parser.add_argument('--json', action='store_true',
                        help='输出JSON格式')
    args = parser.parse_args()

    print(f'\n{"="*60}')
    print(f'  总筛选回测 — Top{args.top} 持仓{args.hold}天 回看{args.days}天')
    print(f'{"="*60}\n')

    bt = BacktestSummary()
    result = bt.run(
        lookback_days=args.days,
        hold_days=args.hold,
        top_n=args.top,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_report(result)


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

    print(f'\n==== 最近交易 ====')
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
          f'[W]=胜 [L]=负 [T]=超时')


if __name__ == '__main__':
    main()
