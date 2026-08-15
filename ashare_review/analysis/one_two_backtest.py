"""1进2战法专属回测 — 对每日复盘精选1进2标的做止盈止损回测

基于龙哥1进2接力战法：
- 数据源：TDX本地日线（主）+ akshare涨停池（辅，仅近~20天可用）
- 盘后筛选：T日首板，按质量打分，Top 5进入候选池
- 开盘介入：T+1日开盘价入场（模拟实战）
- 最长持有3天：1进2不涨停即走，不恋战
- 止盈止损：+7%止盈 / -5%止损（基于1进2战法三种卖出场景）

筛选条件（严格按战法）：
1. 股价 3-15元（战法要求）
2. 排除一字板（买不到）
3. 排除ST、北交所
4. 优先放量首板
5. akshare可用时：封单强度>0.015 + 封成比>0.5

用法：
    python -m ashare_review.analysis.one_two_backtest --days 60
"""
import sys, os, json, struct
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ashare_review.data.tdx_reader import TdxReader, RECORD_SIZE
from ashare_review.data.akshare_fetcher import AkshareFetcher
from ashare_review.screening.one_two import OneTwoScreener
from ashare_review.utils.calendar import TradingCalendar
from ashare_review.data.models import LimitUpInfo


def _is_a_stock(code: str) -> bool:
    """过滤A股（排除B股、港股通等非主流品种）"""
    if not code or len(code) != 6:
        return False
    return (code.startswith(('0', '3', '6')) and
            not code.startswith(('900', '200')))


def _is_yizi_board(lu) -> bool:
    """判断是否一字板：封板时间为09:25"""
    try:
        return str(lu.limit_up_time).replace(':', '')[:4] == '0925'
    except (ValueError, AttributeError, TypeError):
        return False


def _board_limit_threshold(code: str) -> float:
    """根据股票代码返回涨停阈值(%)"""
    if code.startswith(('300', '301')):
        return 19.9
    if code.startswith('688'):
        return 19.9
    if code.startswith(('8', '4')):
        return 29.9
    return 9.9


# ======================================================================
# TDX涨停池索引（历史回测的核心数据源）
# ======================================================================
class TdxLimitUpIndex:
    """预扫描TDX .day文件，构建日期→涨停股列表的索引

    一次性读取所有.day文件的近期记录，后续按日期O(1)查询。
    扫描范围由lookback_days决定（留余量应对节假日）。
    """

    def __init__(self, tdx: TdxReader, lookback_calendar_days: int = 120):
        self.tdx = tdx
        self.lookback_days = lookback_calendar_days
        # {date_obj: [(code, open, high, low, close, volume, prev_close, market)]}
        self._index: Dict[date, List[Tuple]] = defaultdict(list)
        self._built = False

    def build(self, start_date: date, end_date: date):
        """扫描所有.day文件，构建涨停索引"""
        if self._built:
            return

        # 留足余量：从start_date往前多读一些以保证有prev_close
        scan_start = start_date - timedelta(days=10)
        records_to_read = (end_date - scan_start).days + 5
        read_bytes = records_to_read * RECORD_SIZE

        total_files = 0
        matched = 0

        for market in ['sh', 'sz']:
            mdir = self.tdx._market_dir(market)
            if not os.path.exists(mdir):
                continue
            files = [f for f in os.listdir(mdir) if f.endswith('.day')]
            for fname in files:
                total_files += 1
                code = fname[2:8]  # e.g. 'sh000001.day' → '000001'
                if not _is_a_stock(code):
                    continue

                fpath = os.path.join(mdir, fname)
                fsize = os.path.getsize(fpath)
                if fsize < RECORD_SIZE * 2:
                    continue

                # 只读文件尾部（enough to cover our date range）
                read_size = min(read_bytes, fsize)
                try:
                    with open(fpath, 'rb') as f:
                        f.seek(fsize - read_size)
                        data = f.read(read_size)
                except Exception:
                    continue

                threshold = _board_limit_threshold(code)
                num_records = len(data) // RECORD_SIZE

                # 解析所有读取的记录
                records = []
                for i in range(num_records):
                    offset = i * RECORD_SIZE
                    dt_int, op, hi, lo, cl, amt, vol, _ = struct.unpack(
                        'IIIIIfII', data[offset:offset + RECORD_SIZE])
                    dt_str = str(dt_int)
                    try:
                        d = date(int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:8]))
                    except ValueError:
                        continue
                    records.append({
                        'date': d,
                        'open': op / 100.0,
                        'high': hi / 100.0,
                        'low': lo / 100.0,
                        'close': cl / 100.0,
                        'volume': vol,
                        'amount': amt,
                    })

                # 找在目标日期范围内的涨停日
                for j in range(1, len(records)):
                    r = records[j]
                    if r['date'] < start_date or r['date'] > end_date:
                        continue
                    prev = records[j-1]
                    if prev['close'] <= 0:
                        continue

                    change_pct = (r['close'] - prev['close']) / prev['close'] * 100
                    if change_pct >= threshold - 0.1:  # 允许微小误差
                        # 判断是否一字板（TDX近似：开盘即涨停且全天无交易）
                        is_yizi = (abs(r['open'] - r['close']) < 0.01 and
                                   abs(r['high'] - r['low']) < 0.01 and
                                   abs(r['high'] - r['close']) < 0.01)
                        self._index[r['date']].append({
                            'code': code,
                            'market': market,
                            'open': r['open'],
                            'high': r['high'],
                            'low': r['low'],
                            'close': r['close'],
                            'volume': r['volume'],
                            'amount': r['amount'],
                            'prev_close': prev['close'],
                            'change_pct': round(change_pct, 1),
                            'is_yizi': is_yizi,
                        })
                        matched += 1

        self._built = True
        print(f'[TDX涨停索引] 扫描{total_files}只股票 → '
              f'找到{matched}条涨停记录覆盖{len(self._index)}个交易日')

    def get_first_boards(self, d: date) -> List[Dict]:
        """获取指定日期的所有首板股票（TDX无法区分连板，全部返回）"""
        return self._index.get(d, [])

    def has_data_for(self, d: date) -> bool:
        """检查是否有该日期的数据"""
        return d in self._index


# ======================================================================
# 1进2回测器
# ======================================================================
class OneTwoBacktest:
    """1进2战法专属回测器

    双数据源：
    - akshare（优先）：含封单额/流通市值/板型等精细数据，仅近~20交易日可用
    - TDX本地（回退）：含OHLCV基础数据，覆盖全部历史
    """

    HOLD_DAYS = 3           # 最长持有3天（1进2核心规则）
    MAX_GAP_UP = 0.07       # 次日开盘涨幅超过7% → 放弃（一字板预期）
    TAKE_PROFIT = 0.07      # 止盈 +7%（模拟7-8点卖出）
    STOP_LOSS = -0.05       # 止损 -5%（接受卖飞，不接受血亏）
    MIN_SCORE = 40          # 最低评分阈值

    def __init__(self):
        self.tdx = TdxReader()
        self.ak = AkshareFetcher()
        self.calendar = TradingCalendar()
        self.screener = OneTwoScreener(self.tdx, self.ak)
        self._tdx_index: Optional[TdxLimitUpIndex] = None

    # ==================================================================
    # 主入口
    # ==================================================================
    def run(self, lookback_days: int = 60, top_n: int = 5,
            min_score: int = 0, min_price: float = 3.0, max_price: float = 15.0) -> Dict:
        """主入口：回测最近 N 个交易日"""
        _min_score = min_score if min_score > 0 else self.MIN_SCORE
        trade_dates = self._get_trade_dates(lookback_days)

        # 构建TDX涨停索引（覆盖所有历史日期）
        print(f'正在构建TDX涨停索引 (覆盖{trade_dates[0]}~{trade_dates[-1]})...')
        self._tdx_index = TdxLimitUpIndex(self.tdx, lookback_calendar_days=lookback_days + 30)
        self._tdx_index.build(trade_dates[0], trade_dates[-1])
        print()

        print(f'回测范围: {trade_dates[0]} ~ {trade_dates[-1]} 共{len(trade_dates)}个交易日')
        print(f'筛选条件: 评分≥{_min_score} | 股价{min_price}-{max_price}元 | 持仓≤{self.HOLD_DAYS}天')
        print()

        all_trades = []
        skipped_days = 0
        akshare_days = 0
        tdx_days = 0

        for i, td in enumerate(trade_dates):
            td_str = td.strftime('%Y%m%d')
            source_label = ''

            # ---- 大盘环境过滤 ----
            market_ok = self._check_market_env(td_str)
            if not market_ok:
                skipped_days += 1
                print(f'\r[{i+1}/{len(trade_dates)}] {td_str} 大盘跌>1.5%→跳过', end='', flush=True)
                continue

            # ---- 1. 尝试akshare获取涨停池 ----
            candidates, source = self._get_one_two_picks(td_str, _min_score,
                                                          min_price, max_price)
            if source == 'akshare':
                akshare_days += 1
            else:
                tdx_days += 1
            source_label = 'A' if source == 'akshare' else 'T'

            if not candidates:
                skipped_days += 1
                print(f'\r[{i+1}/{len(trade_dates)}] {td_str} [{source_label}] 无候选→跳过', end='', flush=True)
                continue

            # ---- 2. 取Top N ----
            top_picks = candidates[:top_n]

            # ---- 3. 模拟交易 ----
            for pick in top_picks:
                trade = self._simulate_trade(
                    code=pick['code'],
                    name=pick['name'],
                    signal_date=td_str,
                    signal_close=pick['close'],
                    score=pick['score'],
                    source=source,
                )
                if trade:
                    all_trades.append(trade)

            print(f'\r[{i+1}/{len(trade_dates)}] {td_str} [{source_label}] '
                  f'{len(top_picks)}只候选', end='', flush=True)

        print()
        return self._summarize(all_trades, skipped_days, lookback_days,
                               akshare_days, tdx_days)

    # ==================================================================
    # 获取1进2精选标的（双数据源）
    # ==================================================================
    def _get_one_two_picks(self, trade_date: str, min_score: int,
                            min_price: float, max_price: float) -> Tuple[List[Dict], str]:
        """获取1进2候选标的

        Returns:
            (picks_list, source) where source is 'akshare' or 'tdx'
        """
        # ---- 尝试akshare（优先，含封单/板型等精细数据） ----
        try:
            akshare_picks = self._get_picks_akshare(trade_date, min_score,
                                                     min_price, max_price)
            if akshare_picks:
                return akshare_picks, 'akshare'
        except Exception:
            pass

        # ---- TDX回退（仅OHLCV基础数据） ----
        try:
            td_dt = datetime.strptime(trade_date, '%Y%m%d').date()
        except ValueError:
            return [], 'none'

        tdx_picks = self._get_picks_tdx(td_dt, min_price, max_price)
        return tdx_picks, 'tdx'

    # ==================================================================
    # akshare数据源
    # ==================================================================
    def _get_picks_akshare(self, trade_date: str, min_score: int,
                            min_price: float, max_price: float) -> List[Dict]:
        """通过akshare涨停池 + OneTwoScreener获取精选标的"""
        try:
            results = self.screener.screen(night_mode=True, trade_date=trade_date)
        except Exception:
            return []

        if not results:
            return []

        # 获取首板原始数据用于战法过滤
        try:
            limit_ups = self.ak.get_limit_up_pool(trade_date=trade_date)
            lu_by_code = {lu.code: lu for lu in limit_ups if lu.is_first}
        except Exception:
            lu_by_code = {}

        picks = []
        for r in results:
            if not _is_a_stock(r.code):
                continue
            if r.score < min_score:
                continue

            lu = lu_by_code.get(r.code)
            if lu is None:
                continue

            # 战法过滤
            if _is_yizi_board(lu):
                continue
            if lu.float_market_cap > 100 or lu.float_market_cap <= 0:
                continue

            # 封单强度（封单额/流通市值 > 0.015）
            # 注意：akshare历史数据封单额可能为0（数据缺失），此时放宽此条件
            has_seal_data = lu.seal_amount > 0 and lu.float_market_cap > 0
            if has_seal_data:
                seal_strength = lu.seal_amount / (lu.float_market_cap * 10000)
                if seal_strength < 0.015:
                    continue

            # 封成比（封单额/成交额 > 0.5）
            # 同样：封单数据缺失时放宽
            if has_seal_data and lu.turnover > 0:
                if lu.seal_amount / lu.turnover < 0.5:
                    continue

            # 股价过滤
            signal_close = self._get_signal_close(r.code, trade_date)
            if signal_close <= 0:
                continue
            if min_price > 0 and signal_close < min_price:
                continue
            if max_price > 0 and signal_close > max_price:
                continue

            picks.append({
                'code': r.code,
                'name': r.name,
                'score': r.score,
                'close': signal_close,
                'float_market_cap': lu.float_market_cap,
                'seal_ratio': round(lu.seal_amount / lu.turnover, 2) if (lu.turnover > 0 and lu.seal_amount > 0) else 0,
                'board_type': lu.board_type,
                'limit_up_time': lu.limit_up_time,
                'data_source': 'akshare',
            })

        picks.sort(key=lambda x: x['score'], reverse=True)
        return picks

    # ==================================================================
    # TDX数据源（历史回退）
    # ==================================================================
    def _get_picks_tdx(self, td_dt: date, min_price: float,
                        max_price: float) -> List[Dict]:
        """通过TDX本地数据筛选1进2候选（简化版）

        评分维度：
        1. 价格区间（3-15元最优）
        2. 成交量（放量优于缩量）
        3. 涨停质量（非一字板，有换手）
        4. 涨停时间近似（高开少=早封板概率高）
        """
        if self._tdx_index is None:
            return []

        stocks = self._tdx_index.get_first_boards(td_dt)
        if not stocks:
            return []

        picks = []
        for s in stocks:
            code = s['code']
            close = s['close']
            volume = s['volume']
            change = s['change_pct']

            # 价格过滤
            if close < min_price or close > max_price:
                continue

            # 排除一字板（TDX检测）
            if s['is_yizi']:
                continue

            # 排除创业板/科创板（20%涨跌幅，不适合1进2接力）
            threshold = _board_limit_threshold(code)
            if threshold > 10.0:
                continue

            # ---- 简化评分 ----
            score = 0

            # 1) 价格评分：5-10元最优（战法核心区间）
            if 5 <= close <= 10:
                score += 25
            elif 3 <= close <= 15:
                score += 15
            else:
                score += 5

            # 2) 涨幅评分：接近涨停上限=封板坚决
            if change >= 10.0:
                score += 20  # 主板涨停
            elif change >= 9.5:
                score += 15
            else:
                score += 5   # 创业板20%涨停

            # 3) 量能评分：成交量越大越好（市场合力）
            if volume > 100_000_000:  # >1亿股
                score += 20
            elif volume > 50_000_000:
                score += 15
            elif volume > 20_000_000:
                score += 10
            else:
                score += 3

            # 4) 成交额评分（大资金参与）
            amount_yi = s['amount'] / 1e8
            if amount_yi > 5:
                score += 15  # >5亿=市场合力
            elif amount_yi > 2:
                score += 10
            elif amount_yi > 1:
                score += 5

            # 5) 上影线小 = 封板稳定（非烂板）
            if s['high'] > 0:
                upper_shadow = (s['high'] - close) / (s['high'] - s['low'] + 0.01)
            else:
                upper_shadow = 0
            if upper_shadow < 0.2:  # 几乎无上影线=强势封板
                score += 15
            elif upper_shadow < 0.5:
                score += 8

            # 6) 非尾盘涨停（高开幅度小暗示非尾盘偷板）
            if s['open'] > 0:
                gap = (s['open'] - s['prev_close']) / s['prev_close']
            else:
                gap = 0
            if 0.01 <= gap <= 0.05:  # 小幅高开后涨停=早盘板概率高
                score += 10
            elif gap < 0.01:
                score += 5  # 平开涨停=可能是下午板

            if score < 40:
                continue

            # 获取股票名称
            name = self._get_stock_name(code)

            picks.append({
                'code': code,
                'name': name,
                'score': min(score, 100),
                'close': close,
                'float_market_cap': 0,  # TDX无此数据
                'seal_ratio': 0,
                'board_type': '',
                'limit_up_time': '',
                'data_source': 'tdx',
            })

        picks.sort(key=lambda x: x['score'], reverse=True)
        return picks

    # ==================================================================
    # 辅助：股票名称
    # ==================================================================
    def _get_stock_name(self, code: str) -> str:
        """获取股票名称（多级缓存回退）"""
        try:
            from ashare_review.screening.base import BaseScreener
            bs = BaseScreener.__new__(BaseScreener)
            bs.__init__()
            name = bs._get_name(code)
            if name:
                return name
        except Exception:
            pass
        return code

    # ==================================================================
    # 读取信号日收盘价
    # ==================================================================
    def _get_signal_close(self, code: str, trade_date: str) -> float:
        """获取指定日期TDX数据的收盘价"""
        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith('8') or code.startswith('4'):
            market = 'bj'
        try:
            df = self.tdx.read_daily(code, market)
            if df.empty or len(df) < 5:
                return 0
            if 'trade_date' in df.columns:
                try:
                    target_d = datetime.strptime(trade_date, '%Y%m%d').date()
                except ValueError:
                    return 0
                for i in range(len(df) - 1, -1, -1):
                    td = df['trade_date'].iloc[i]
                    td_d = td.date() if hasattr(td, 'date') else td
                    if hasattr(td_d, 'date'):
                        td_d = td_d.date()
                    if td_d == target_d:
                        return round(float(df['close'].iloc[i]), 2)
            return 0
        except Exception:
            return 0

    # ==================================================================
    # 大盘环境过滤
    # ==================================================================
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

    # ==================================================================
    # 获取交易日列表
    # ==================================================================
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

    # ==================================================================
    # 模拟单笔交易（1进2专属）
    # ==================================================================
    def _simulate_trade(self, code: str, name: str,
                         signal_date: str, signal_close: float,
                         score: int, source: str = 'tdx') -> Optional[Dict]:
        """模拟一笔1进2交易

        逻辑：
        1. 信号日(T日)选出标的
        2. T+1日开盘价入场
        3. 开盘涨幅>7% → 放弃
        4. 止盈+7%/止损-5%，最长持有3天
        """
        if signal_close <= 0:
            return None

        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith('8') or code.startswith('4'):
            market = 'bj'
        try:
            df = self.tdx.read_daily(code, market)
            if df.empty or len(df) < 20:
                return None
        except Exception:
            return None

        # 定位信号日
        try:
            signal_dt = datetime.strptime(signal_date, '%Y%m%d').date()
        except ValueError:
            return None

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

        signal_idx = None
        for i, d in enumerate(trade_dates):
            if d == signal_dt:
                signal_idx = i
                break

        if signal_idx is None:
            return None

        # T+1入场
        entry_idx = signal_idx + 1
        if entry_idx >= len(df):
            return None

        entry_row = df.iloc[entry_idx]
        entry_price = float(entry_row['open'])
        entry_date = str(trade_dates[entry_idx]) if entry_idx < len(trade_dates) else str(signal_dt)

        if entry_price <= 0:
            return None

        # 高开过滤
        gap_pct = (entry_price - signal_close) / signal_close
        if gap_pct > self.MAX_GAP_UP:
            return {
                'code': code, 'name': name,
                'entry_date': entry_date,
                'entry_price': round(entry_price, 2),
                'stop_loss': 0, 'target': 0,
                'exit_date': entry_date,
                'exit_price': round(entry_price, 2),
                'result': 'skipped_gap',
                'return_pct': 0, 'days_held': 0,
                'score': score, 'gap_pct': round(gap_pct * 100, 1),
                'source': source,
            }

        # 止盈止损
        target = round(entry_price * (1 + self.TAKE_PROFIT), 2)
        stop_loss = round(entry_price * (1 + self.STOP_LOSS), 2)

        # 向前看hold_days个交易日
        exit_idx = min(entry_idx + self.HOLD_DAYS, len(df) - 1)
        if exit_idx <= entry_idx:
            result = 'timeout'
            exit_price = float(entry_row['close'])
            exit_dt = entry_date
            days_held = 0
            ret_pct = (exit_price - entry_price) / entry_price * 100
            return {
                'code': code, 'name': name,
                'entry_date': entry_date,
                'entry_price': round(entry_price, 2),
                'stop_loss': round(stop_loss, 2),
                'target': round(target, 2),
                'exit_date': exit_dt,
                'exit_price': round(exit_price, 2),
                'result': 'timeout',
                'return_pct': round(ret_pct, 1),
                'days_held': days_held,
                'score': score, 'gap_pct': round(gap_pct * 100, 1),
                'source': source,
            }

        future = df.iloc[entry_idx + 1:exit_idx + 1]

        result = 'timeout'
        exit_price = float(future['close'].iloc[-1]) if not future.empty else float(entry_row['close'])
        last_future_idx = min(exit_idx, len(trade_dates) - 1)
        exit_dt = str(trade_dates[last_future_idx]) if last_future_idx < len(trade_dates) else entry_date
        days_held = self.HOLD_DAYS

        # 先查T+1当天日内
        entry_high = float(entry_row['high'])
        entry_low = float(entry_row['low'])

        if entry_low <= stop_loss:
            result = 'loss'
            exit_price = round(min(stop_loss, float(entry_row['open'])), 2)
            exit_dt = entry_date
            days_held = 0
        elif entry_high >= target:
            result = 'win'
            exit_price = target
            exit_dt = entry_date
            days_held = 0

        # 继续检查后续交易日
        if result == 'timeout':
            for j in range(len(future)):
                row = future.iloc[j]
                day_high = float(row['high'])
                day_low = float(row['low'])
                day_open = float(row['open'])

                if day_low <= stop_loss:
                    result = 'loss'
                    exit_price = round(min(stop_loss, day_open), 2)
                    actual_idx = entry_idx + 1 + j
                    exit_dt = str(trade_dates[min(actual_idx, len(trade_dates)-1)]) \
                        if actual_idx < len(trade_dates) else entry_date
                    days_held = j + 1
                    break

                if day_high >= target:
                    result = 'win'
                    exit_price = target
                    actual_idx = entry_idx + 1 + j
                    exit_dt = str(trade_dates[min(actual_idx, len(trade_dates)-1)]) \
                        if actual_idx < len(trade_dates) else entry_date
                    days_held = j + 1
                    break

        ret_pct = (exit_price - entry_price) / entry_price * 100

        return {
            'code': code, 'name': name,
            'entry_date': entry_date,
            'entry_price': round(entry_price, 2),
            'stop_loss': round(stop_loss, 2),
            'target': round(target, 2),
            'exit_date': exit_dt,
            'exit_price': round(exit_price, 2),
            'result': result,
            'return_pct': round(ret_pct, 1),
            'days_held': days_held,
            'score': score, 'gap_pct': round(gap_pct * 100, 1),
            'source': source,
        }

    # ==================================================================
    # 汇总统计
    # ==================================================================
    def _summarize(self, trades: List[Dict], skipped_days: int,
                    total_days: int, akshare_days: int = 0,
                    tdx_days: int = 0) -> Dict:
        if not trades:
            return {
                'total_trades': 0,
                'skipped_days': skipped_days,
                'total_days': total_days,
                'effective_days': total_days - skipped_days,
                'error': '无有效交易记录',
            }

        skipped_gap = [t for t in trades if t['result'] == 'skipped_gap']
        valid_trades = [t for t in trades if t['result'] != 'skipped_gap']

        wins = [t for t in valid_trades if t['result'] == 'win']
        losses = [t for t in valid_trades if t['result'] == 'loss']
        timeouts = [t for t in valid_trades if t['result'] == 'timeout']

        win_count = len(wins)
        loss_count = len(losses)
        timeout_count = len(timeouts)
        total_valid = len(valid_trades)
        total = len(trades)

        win_rate = win_count / max(win_count + loss_count, 1) * 100

        avg_win = np.mean([t['return_pct'] for t in wins]) if wins else 0
        avg_loss = np.mean([t['return_pct'] for t in losses]) if losses else 0
        avg_return = np.mean([t['return_pct'] for t in valid_trades]) if valid_trades else 0

        total_profit = sum(t['return_pct'] for t in wins)
        total_loss = sum(abs(t['return_pct']) for t in losses)
        profit_factor = total_profit / max(total_loss, 0.01)

        # 按评分分组
        by_score = defaultdict(list)
        for t in valid_trades:
            if t['score'] >= 80:
                by_score['评分≥80(优质)'].append(t)
            elif t['score'] >= 60:
                by_score['评分60-79(良好)'].append(t)
            else:
                by_score['评分<60(一般)'].append(t)

        score_stats = {}
        for key, ts in sorted(by_score.items()):
            w = len([t for t in ts if t['result'] == 'win'])
            l = len([t for t in ts if t['result'] == 'loss'])
            score_stats[key] = {
                'trades': len(ts),
                'wins': w, 'losses': l,
                'win_rate': round(w / max(w + l, 1) * 100, 1),
                'avg_return': round(np.mean([t['return_pct'] for t in ts]), 1),
            }

        # 按数据源分组
        by_source = defaultdict(list)
        for t in valid_trades:
            src = t.get('source', 'unknown')
            by_source[f'数据源:{src}'].append(t)

        source_stats = {}
        for key, ts in sorted(by_source.items()):
            w = len([t for t in ts if t['result'] == 'win'])
            l = len([t for t in ts if t['result'] == 'loss'])
            source_stats[key] = {
                'trades': len(ts),
                'wins': w, 'losses': l,
                'win_rate': round(w / max(w + l, 1) * 100, 1),
                'avg_return': round(np.mean([t['return_pct'] for t in ts]), 1),
            }

        # 按持仓天数分组
        by_days = defaultdict(list)
        for t in valid_trades:
            by_days[f'持仓{t["days_held"]}天'].append(t)

        days_stats = {}
        for key, ts in sorted(by_days.items()):
            w = len([t for t in ts if t['result'] == 'win'])
            l = len([t for t in ts if t['result'] == 'loss'])
            days_stats[key] = {
                'trades': len(ts),
                'wins': w, 'losses': l,
                'win_rate': round(w / max(w + l, 1) * 100, 1),
                'avg_return': round(np.mean([t['return_pct'] for t in ts]), 1),
            }

        recent = sorted(valid_trades, key=lambda x: x['entry_date'], reverse=True)[:10]

        # 滚动胜率
        daily_returns = [t['return_pct'] for t in sorted(valid_trades, key=lambda x: x['entry_date'])]
        rolling_winrates = []
        for i in range(len(daily_returns) - 9):
            window = daily_returns[i:i+10]
            w = sum(1 for r in window if r > 0)
            rolling_winrates.append(round(w / 10 * 100, 1))

        return {
            'total_trades': total,
            'valid_trades': total_valid,
            'skipped_gap': len(skipped_gap),
            'wins': win_count, 'losses': loss_count, 'timeouts': timeout_count,
            'win_rate': round(win_rate, 1),
            'avg_win_return': round(float(avg_win), 1),
            'avg_loss_return': round(float(avg_loss), 1),
            'avg_return': round(float(avg_return), 1),
            'total_profit': round(float(total_profit), 1),
            'total_loss': round(float(total_loss), 1),
            'profit_factor': round(float(profit_factor), 2),
            'skipped_days': skipped_days,
            'total_days': total_days,
            'effective_days': total_days - skipped_days,
            'akshare_days': akshare_days,
            'tdx_days': tdx_days,
            'by_score': score_stats,
            'by_source': source_stats,
            'by_days': days_stats,
            'rolling_winrates': rolling_winrates[-5:] if rolling_winrates else [],
            'recent_trades': recent,
            'all_trades': sorted(valid_trades, key=lambda x: x['entry_date'], reverse=True),
        }


# ======================================================================
# 文件导出
# ======================================================================
RESULT_LABELS = {'win': '胜', 'loss': '负', 'timeout': '超时', 'skipped_gap': '高开放弃'}


def _export_csv(r: Dict, filepath: str):
    import csv
    trades = r.get('all_trades', [])
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['入场日期', '代码', '名称', '入场价', '止损价', '止盈价',
                         '离场日期', '离场价', '结果', '收益率%', '持仓天数',
                         '评分', '开盘涨幅%', '数据源'])
        for t in trades:
            writer.writerow([
                t['entry_date'], t['code'], t['name'],
                t['entry_price'], t['stop_loss'], t['target'],
                t['exit_date'], t['exit_price'],
                RESULT_LABELS.get(t['result'], t['result']),
                t['return_pct'], t['days_held'],
                t['score'], t.get('gap_pct', 0),
                t.get('source', ''),
            ])
    print(f'\n[CSV] 全部 {len(trades)} 笔交易已导出 → {filepath}')


def _export_txt(r: Dict, filepath: str):
    trades = r.get('all_trades', [])
    lines = []
    lines.append('=' * 85)
    lines.append('  1进2战法回测 — 完整交易记录')
    lines.append('=' * 85)
    lines.append('')
    lines.append(f'==== 回测概况 ====')
    lines.append(f'   回测天数: {r["total_days"]}天 | 有效交易日: {r.get("effective_days", r["total_days"]-r["skipped_days"])}天')
    lines.append(f'   akshare覆盖: {r.get("akshare_days", "?")}天 | TDX回退: {r.get("tdx_days", "?")}天')
    lines.append(f'   总候选: {r["total_trades"]}笔 | 有效交易: {r["valid_trades"]}笔 '
                 f'| 高开放弃: {r.get("skipped_gap", 0)}笔')
    lines.append('')
    lines.append(f'==== 交易统计 ====')
    lines.append(f'   胜: {r["wins"]}笔 | 负: {r["losses"]}笔 | 超时: {r.get("timeouts", 0)}笔')
    lines.append(f'   胜率: {r["win_rate"]}% | 平均收益: {r["avg_return"]}%')
    lines.append(f'   平均盈利: {r["avg_win_return"]}% | 平均亏损: {r["avg_loss_return"]}%')
    lines.append(f'   总盈利: {r.get("total_profit", 0)}% | 总亏损: {r.get("total_loss", 0)}% '
                 f'| 盈亏比: {r["profit_factor"]}')
    rolling = r.get('rolling_winrates', [])
    if rolling:
        lines.append(f'   滚动10笔胜率(最近5段): {rolling}')
    lines.append('')
    lines.append(f'==== 按数据源分组 ====')
    for key, stats in r.get('by_source', {}).items():
        lines.append(f'   {key}: {stats["trades"]}笔 胜率{stats["win_rate"]}% '
                     f'均收益{stats["avg_return"]}%')
    lines.append('')
    lines.append(f'==== 按评分分组 ====')
    for key, stats in r.get('by_score', {}).items():
        lines.append(f'   {key}: {stats["trades"]}笔 胜率{stats["win_rate"]}% '
                     f'均收益{stats["avg_return"]}%')
    lines.append('')
    lines.append(f'==== 按持仓天数分组 ====')
    for key, stats in r.get('by_days', {}).items():
        lines.append(f'   {key}: {stats["trades"]}笔 胜率{stats["win_rate"]}% '
                     f'均收益{stats["avg_return"]}%')
    lines.append('')
    lines.append(f'==== 全部交易记录 ({len(trades)}笔) ====')
    lines.append(f'   {"入场日":<12} {"代码":<8} {"名称":<8} {"结果":<6} '
                 f'{"收益%":>7} {"持仓":>4} {"入场价":>8} {"离场价":>8} {"评分":>4} {"源":>3}')
    lines.append('   ' + '-' * 85)
    for t in trades:
        if t['result'] == 'win':
            icon = '[胜]'
        elif t['result'] == 'loss':
            icon = '[负]'
        elif t['result'] == 'skipped_gap':
            icon = '[弃]'
        else:
            icon = '[超]'
        lines.append(f'   {t["entry_date"]:<12} {t["code"]:<8} {t["name"]:<8} '
                     f'{icon} {t["return_pct"]:>+6.1f}% '
                     f'{t["days_held"]:>3}天 '
                     f'{t["entry_price"]:>8.2f} {t["exit_price"]:>8.2f} '
                     f'{t["score"]:>3}分 '
                     f'{t.get("source", "?"):>3}')
    lines.append('')
    lines.append('[说明] T日收盘筛选Top5 → T+1开盘价入场 → 止盈+7%/止损-5% → 最长持仓3天')
    lines.append('  数据源: A=akshare(含封单/板型等精细数据) T=TDX(仅OHLCV基础数据)')
    lines.append('  [胜]=止盈 [负]=止损 [超]=3天到期 [弃]=高开放弃')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'\n[TXT] 全部 {len(trades)} 笔交易已导出 → {filepath}')


def _export_json(r: Dict, filepath: str):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    trades_count = len(r.get('all_trades', []))
    print(f'\n[JSON] 全部 {trades_count} 笔交易已导出 → {filepath}')


# ======================================================================
# CLI
# ======================================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='1进2战法回测')
    parser.add_argument('--days', type=int, default=60,
                        help='回测天数 (默认60)')
    parser.add_argument('--top', type=int, default=5,
                        help='每日取Top N 1进2标的 (默认5)')
    parser.add_argument('--min-score', type=int, default=0, metavar='N',
                        help='最低评分阈值 (默认0=使用内置阈值40)')
    parser.add_argument('--min-price', type=float, default=3.0, metavar='P',
                        help='最低股价 (默认3元)')
    parser.add_argument('--max-price', type=float, default=15.0, metavar='P',
                        help='最高股价 (默认15元)')
    parser.add_argument('--json', action='store_true',
                        help='输出JSON格式到stdout')
    parser.add_argument('--output', '-o', type=str, default=None, metavar='PATH',
                        help='导出全部交易到文件（根据扩展名自动选CSV/TXT/JSON）')
    args = parser.parse_args()

    price_info = f' 股价{args.min_price}-{args.max_price}元'
    score_info = f' 评分≥{args.min_score}' if args.min_score > 0 else ' 评分≥40(内置)'

    print(f'\n{"="*65}')
    print(f'  1进2战法回测 — Top{args.top} 持仓3天 回看{args.days}天')
    print(f'  {price_info}{score_info}')
    print(f'{"="*65}\n')

    bt = OneTwoBacktest()
    result = bt.run(
        lookback_days=args.days,
        top_n=args.top,
        min_score=args.min_score,
        min_price=args.min_price,
        max_price=args.max_price,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_report(result)

    if args.output:
        if args.output.endswith('.csv'):
            _export_csv(result, args.output)
        elif args.output.endswith('.json'):
            _export_json(result, args.output)
        else:
            _export_txt(result, args.output)


def _print_report(r: Dict):
    if r.get('error'):
        print(f'[ERROR] {r["error"]}')
        return

    print(f'\n==== 交易统计 ====')
    print(f'   回测{r["total_days"]}天 | 有效{r.get("effective_days", r["total_days"]-r["skipped_days"])}天')
    print(f'   akshare覆盖: {r.get("akshare_days", "?")}天 | TDX回退: {r.get("tdx_days", "?")}天')
    print(f'   总候选: {r["total_trades"]}笔 | 高开放弃: {r.get("skipped_gap", 0)}笔')
    print(f'   胜: {r["wins"]}笔 | 负: {r["losses"]}笔 | 超时: {r.get("timeouts", 0)}笔')
    print(f'   胜率: {r["win_rate"]}% | 平均收益: {r["avg_return"]}%')
    print(f'   平均盈利: {r["avg_win_return"]}% | 平均亏损: {r["avg_loss_return"]}%')
    print(f'   总盈利: {r.get("total_profit", 0)}% | 总亏损: {r.get("total_loss", 0)}% '
          f'| 盈亏比: {r["profit_factor"]}')
    rolling = r.get('rolling_winrates', [])
    if rolling:
        print(f'   滚动10笔胜率(最近5段): {rolling}')

    print(f'\n==== 按数据源分组 ====')
    for key, stats in r.get('by_source', {}).items():
        print(f'   {key}: {stats["trades"]}笔 胜率{stats["win_rate"]}% '
              f'均收益{stats["avg_return"]}%')

    print(f'\n==== 按评分分组 ====')
    for key, stats in r.get('by_score', {}).items():
        print(f'   {key}: {stats["trades"]}笔 胜率{stats["win_rate"]}% '
              f'均收益{stats["avg_return"]}%')

    print(f'\n==== 按持仓天数分组 ====')
    for key, stats in r.get('by_days', {}).items():
        print(f'   {key}: {stats["trades"]}笔 胜率{stats["win_rate"]}% '
              f'均收益{stats["avg_return"]}%')

    print(f'\n==== 最近交易 (最近10笔) ====')
    print(f'   {"入场日":<12} {"代码":<8} {"名称":<8} {"结果":<6} '
          f'{"收益%":>7} {"持仓":>4} {"入场价":>8} {"离场价":>8} {"评分":>4} {"源":>3}')
    for t in r.get('recent_trades', []):
        if t['result'] == 'win':
            icon = '[胜]'
        elif t['result'] == 'loss':
            icon = '[负]'
        elif t['result'] == 'skipped_gap':
            icon = '[弃]'
        else:
            icon = '[超]'
        print(f'   {t["entry_date"]:<12} {t["code"]:<8} {t["name"]:<8} '
              f'{icon} {t["return_pct"]:>+6.1f}% '
              f'{t["days_held"]:>3}天 '
              f'{t["entry_price"]:>8.2f} {t["exit_price"]:>8.2f} '
              f'{t["score"]:>3}分 '
              f'{t.get("source", "?"):>3}')

    print(f'\n[说明] T日收盘筛选Top5 → T+1开盘价入场 → 止盈+7%/止损-5% → 最长持仓3天')
    print(f'  数据源: A=akshare(精细) T=TDX(基础) | [胜]=止盈 [负]=止损 [超]=到期 [弃]=高开放弃')
    print(f'  共 {r["valid_trades"]} 笔有效交易，终端仅显示最近10笔')


if __name__ == '__main__':
    main()
