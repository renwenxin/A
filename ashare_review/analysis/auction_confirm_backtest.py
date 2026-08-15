"""竞价确认型选股方式回测比较

四种方式：
  1. 1进2盘后筛选 → T+1开盘买入
  2. 竞价抢筹（高开2%~6%筛选）→ 当日开盘买入
  3. 优化总筛选（多因子竞价精选）→ 开盘买入
  4. 龙虎榜竞价抢筹（T-1龙虎榜净买 ∩ T日竞价抢筹）→ 开盘买入

止损止盈：可配置，默认 -5%/+7%

数据源：TDX本地日线（覆盖全历史）+ akshare龙虎榜

用法：
    python -m ashare_review.analysis.auction_confirm_backtest --days 120
"""
import sys, os, json, struct, csv, time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ashare_review.data.tdx_reader import TdxReader, RECORD_SIZE
from ashare_review.utils.calendar import TradingCalendar


# ======================================================================
# 辅助函数
# ======================================================================
def _is_a_stock(code: str) -> bool:
    """过滤A股（排除B股、港股通等非主流品种）"""
    if not code or len(code) != 6:
        return False
    return (code.startswith(('0', '3', '6')) and
            not code.startswith(('900', '200')))


def _board_limit_threshold(code: str) -> float:
    """根据股票代码返回涨停阈值(%)"""
    if code.startswith(('300', '301', '688')):
        return 19.9
    if code.startswith(('8', '4')):
        return 29.9
    return 9.9


def _is_yizi_board_tdx(record: Dict) -> bool:
    """判断是否一字板（基于TDX日线数据近似判断）"""
    return record.get('is_yizi', False)


# ======================================================================
# TDX涨停池索引（复用 one_two_backtest.py 的逻辑）
# ======================================================================
class TdxLimitUpIndex:
    """预扫描TDX .day文件，构建日期→涨停股列表的索引"""

    def __init__(self, tdx: TdxReader):
        self.tdx = tdx
        self._index: Dict[date, List[Dict]] = defaultdict(list)
        self._built = False

    def build(self, start_date: date, end_date: date):
        """扫描所有.day文件，构建涨停索引"""
        if self._built:
            return

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
                code = fname[2:8]
                if not _is_a_stock(code):
                    continue

                fpath = os.path.join(mdir, fname)
                fsize = os.path.getsize(fpath)
                if fsize < RECORD_SIZE * 2:
                    continue

                read_size = min(read_bytes, fsize)
                try:
                    with open(fpath, 'rb') as f:
                        f.seek(fsize - read_size)
                        data = f.read(read_size)
                except Exception:
                    continue

                threshold = _board_limit_threshold(code)
                num_records = len(data) // RECORD_SIZE

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

                for j in range(1, len(records)):
                    r = records[j]
                    if r['date'] < start_date or r['date'] > end_date:
                        continue
                    prev = records[j-1]
                    if prev['close'] <= 0:
                        continue

                    change_pct = (r['close'] - prev['close']) / prev['close'] * 100
                    if change_pct >= threshold - 0.1:
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
        """获取指定日期的所有涨停股"""
        return self._index.get(d, [])

    def has_data_for(self, d: date) -> bool:
        return d in self._index


# ======================================================================
# 通用名称缓存
# ======================================================================
_name_cache: Dict[str, str] = {}

def _get_stock_name(code: str) -> str:
    """获取股票名称"""
    if code in _name_cache:
        return _name_cache[code]
    try:
        from ashare_review.screening.base import BaseScreener
        bs = BaseScreener.__new__(BaseScreener)
        bs.__init__()
        name = bs._get_name(code)
        if name:
            _name_cache[code] = name
            return name
    except Exception:
        pass
    return code


# ======================================================================
# 模拟竞价抢筹筛选器（基于TDX日线数据模拟）
# ======================================================================
class SimulatedAuctionScreener:
    """用TDX日线数据模拟竞价抢筹筛选

    可模拟的维度：
    - 价：open_change_pct（高开幅度）→ 核心筛选 2%~6%
    - 量：成交量相对大小（替代竞价量）
    - 势：板块强度（简化）

    不可模拟的维度（竞价盘口特有）：
    - 9:20-9:25形态（vol_0924/vol_0925）
    - 竞价封单额
    """

    MIN_OPEN_PCT = 2.0   # 最低高开
    MAX_OPEN_PCT = 6.0   # 最高高开
    MIN_SCORE = 25

    def screen(self, trade_date: date, tdx: TdxReader,
               tdx_index: TdxLimitUpIndex) -> List[Dict]:
        """在指定交易日筛选竞价抢筹标的

        参数:
            trade_date: 目标交易日（竞价日 = 买入日）
            tdx: TDX数据读取器
            tdx_index: 涨停索引

        返回: [{code, name, score, open_pct, reasons, ...}]
        """
        # 获取当日有交易的所有股票的开盘数据
        # 从涨停池中取前一日涨停股（含首板和连板），因为它们最可能有竞价异动
        yesterday = self._prev_trade_date(trade_date)
        candidates = []

        # 方法：扫描所有近期有涨停记录的股票，检查今日开盘
        # 同时扫描 .day 文件尾部
        checked = set()

        # 1. 从涨停索引获取近3日的涨停股
        for delta in range(0, 4):
            check_date = trade_date - timedelta(days=delta)
            boards = tdx_index.get_first_boards(check_date)
            for b in boards:
                if b['code'] not in checked:
                    checked.add(b['code'])
                    result = self._evaluate_stock(b['code'], trade_date, tdx)
                    if result:
                        candidates.append(result)

        # 限制结果数
        candidates.sort(key=lambda x: x['score'], reverse=True)
        return candidates[:30]

    def _prev_trade_date(self, d: date) -> date:
        """前一个交易日（简化：跳过周末）"""
        d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    def _evaluate_stock(self, code: str, trade_date: date,
                        tdx: TdxReader) -> Optional[Dict]:
        """评估单只股票在指定日的竞价表现"""
        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith('8') or code.startswith('4'):
            market = 'bj'

        fpath = os.path.join(tdx._market_dir(market), f'{market}{code}.day')
        if not os.path.exists(fpath):
            return None
        fsize = os.path.getsize(fpath)
        if fsize < RECORD_SIZE * 3:
            return None

        # 读取尾部记录，找到trade_date当天的数据
        read_size = min(RECORD_SIZE * 30, fsize)
        with open(fpath, 'rb') as f:
            f.seek(fsize - read_size)
            data = f.read(read_size)

        num_records = len(data) // RECORD_SIZE
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

        # 找到trade_date的记录
        target_idx = None
        for i, r in enumerate(records):
            if r['date'] == trade_date:
                target_idx = i
                break

        if target_idx is None or target_idx == 0:
            return None

        today = records[target_idx]
        yesterday = records[target_idx - 1]

        if today['open'] <= 0 or yesterday['close'] <= 0:
            return None
        if today['volume'] == 0:
            return None

        # ---- 核心筛选：高开2%~6% ----
        open_pct = (today['open'] - yesterday['close']) / yesterday['close'] * 100
        if open_pct < self.MIN_OPEN_PCT or open_pct > self.MAX_OPEN_PCT:
            return None

        # ---- 排除一字板（开盘即涨停，无买入机会） ----
        if abs(today['open'] - today['close']) < 0.01 and today['high'] == today['low']:
            return None

        # ---- 排除ST（简化判断：低价+近期异常） ----
        if today['close'] < 2.0:
            return None

        # ---- 评分 ----
        score = 0
        reasons = []

        # 1) 高开幅度评分
        if open_pct >= 5:
            score += 18
            reasons.append(f'强势高开{open_pct:.1f}%')
        elif open_pct >= 3:
            score += 12
            reasons.append(f'高开{open_pct:.1f}%')
        else:
            score += 6
            reasons.append(f'小高开{open_pct:.1f}%')

        # 2) 量能评分：当日成交量 vs 近期最大量
        recent_vols = [r['volume'] for r in records[max(0, target_idx-20):target_idx]]
        recent_max_vol = max(recent_vols) if recent_vols else today['volume']
        if recent_max_vol > 0:
            vol_ratio = today['volume'] / recent_max_vol
            if vol_ratio >= 0.8:
                score += 25
                reasons.append(f'爆量(量比{vol_ratio:.1f})')
            elif vol_ratio >= 0.5:
                score += 15
                reasons.append(f'放量(量比{vol_ratio:.1f})')
            elif vol_ratio >= 0.3:
                score += 8
                reasons.append(f'量能适中(量比{vol_ratio:.1f})')
            else:
                score -= 5
                reasons.append(f'缩量(量比{vol_ratio:.1f})·力度不足')

        # 3) 成交额评分
        amount_yi = today['amount'] / 1e8
        if amount_yi >= 5:
            score += 15
            reasons.append(f'成交额{amount_yi:.1f}亿·大资金')
        elif amount_yi >= 2:
            score += 8
            reasons.append(f'成交额{amount_yi:.1f}亿')
        elif amount_yi >= 0.5:
            score += 3

        # 4) 量价配合
        if open_pct >= 3 and vol_ratio >= 0.5:
            score += 10
            reasons.append('量价齐升·最强信号')
        elif open_pct >= 2 and vol_ratio < 0.2:
            score -= 5
            reasons.append('高价低量·诱多嫌疑')

        # 5) 涨停基因（近期涨停次数）
        limit_up_count = 0
        for j in range(max(0, target_idx-126), target_idx):
            r = records[j]
            prev_r = records[j-1] if j > 0 else None
            if prev_r and prev_r['close'] > 0:
                chg = (r['close'] - prev_r['close']) / prev_r['close'] * 100
                threshold = _board_limit_threshold(code)
                if chg >= threshold - 0.1:
                    limit_up_count += 1

        if limit_up_count >= 5:
            score += 8
            reasons.append(f'涨停基因活跃({limit_up_count}次)')
        elif limit_up_count >= 2:
            score += 3

        if score < self.MIN_SCORE:
            return None

        name = _get_stock_name(code)
        return {
            'code': code,
            'name': name,
            'score': min(score, 100),
            'open_pct': round(open_pct, 1),
            'close': round(today['close'], 2),
            'open_price': round(today['open'], 2),
            'volume': today['volume'],
            'amount_yi': round(amount_yi, 1),
            'reasons': reasons,
        }


# ======================================================================
# 1进2历史筛选器（基于TDX数据）
# ======================================================================
class SimulatedOneTwoScreener:
    """用TDX日线数据模拟1进2盘后筛选

    筛选条件（战法要求）：
    1. 前一日首板涨停
    2. 排除一字板
    3. 股价 3-15元
    4. 流通市值适中（TDX无法获取，放宽）
    5. 量能评分
    """

    MIN_PRICE = 3.0
    MAX_PRICE = 15.0
    MIN_SCORE = 40

    def screen(self, signal_date: date,  # 信号日（T日，选出首板的日子）
               tdx: TdxReader,
               tdx_index: TdxLimitUpIndex) -> List[Dict]:
        """T日盘后筛选1进2候选

        参数:
            signal_date: T日（首板日）
            tdx: TDX数据读取器
            tdx_index: 涨停索引

        返回: [{code, name, score, close, reasons, ...}]
        """
        first_boards = tdx_index.get_first_boards(signal_date)
        if not first_boards:
            return []

        candidates = []
        for s in first_boards:
            code = s['code']
            close = s['close']
            volume = s['volume']

            # 股价过滤
            if close < self.MIN_PRICE or close > self.MAX_PRICE:
                continue

            # 排除一字板
            if s['is_yizi']:
                continue

            # 排除创业板/科创板（涨停阈值不同）
            threshold = _board_limit_threshold(code)
            if threshold > 10.0:
                continue

            # ---- 评分 ----
            score = 0
            reasons = []

            # 1) 价格评分：5-10元最优
            if 5 <= close <= 10:
                score += 25
                reasons.append('股价5-10元·接力最佳区间')
            elif 3 <= close <= 15:
                score += 15
                reasons.append('股价3-15元')

            # 2) 涨幅评分
            change = s['change_pct']
            if change >= 10.0:
                score += 20
            elif change >= 9.5:
                score += 15

            # 3) 量能评分
            if volume > 100_000_000:
                score += 20
                reasons.append(f'巨量{volume/1e8:.1f}亿股')
            elif volume > 50_000_000:
                score += 15
                reasons.append(f'放量{volume/1e8:.2f}亿股')
            elif volume > 20_000_000:
                score += 10
                reasons.append(f'量能{volume/1e8:.2f}亿股')
            else:
                score += 3

            # 4) 成交额评分
            amount_yi = s['amount'] / 1e8
            if amount_yi > 5:
                score += 15
                reasons.append(f'成交额{amount_yi:.1f}亿')
            elif amount_yi > 2:
                score += 10
            elif amount_yi > 1:
                score += 5

            # 5) 上影线小（封板稳定）
            if s['high'] > 0:
                upper_shadow = (s['high'] - close) / (s['high'] - s['low'] + 0.01)
            else:
                upper_shadow = 0
            if upper_shadow < 0.2:
                score += 15
                reasons.append('封板稳定(无上影)')
            elif upper_shadow < 0.5:
                score += 8

            # 6) 开盘幅度（小幅高开涨停=早盘板概率高）
            if s['open'] > 0:
                gap = (s['open'] - s['prev_close']) / s['prev_close']
            else:
                gap = 0
            if 0.01 <= gap <= 0.05:
                score += 10
                reasons.append('小幅高开涨停·早盘板')
            elif gap < 0.01:
                score += 5

            if score < self.MIN_SCORE:
                continue

            name = _get_stock_name(code)
            candidates.append({
                'code': code,
                'name': name,
                'score': min(score, 100),
                'close': close,
                'volume': volume,
                'amount_yi': round(amount_yi, 1),
                'change_pct': change,
                'reasons': reasons,
            })

        candidates.sort(key=lambda x: x['score'], reverse=True)
        return candidates[:20]


# ======================================================================
# 龙虎榜历史数据索引（用于回测）
# ======================================================================
class LhbIndex:
    """预获取龙虎榜数据，构建日期→净买入股票集合的索引

    逻辑：T-1日龙虎榜净买入的标的，T日竞价抢筹确认后买入
    """

    def __init__(self):
        self._index: Dict[date, Set[str]] = defaultdict(set)    # date -> {codes with net_buy>0}
        self._detail: Dict[date, Dict[str, Dict]] = defaultdict(dict)  # date -> {code -> info}
        self._built = False
        self._cache_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'ashare_review', 'data', 'lhb_history_cache.json')

    def build(self, start_date: date, end_date: date):
        """批量获取龙虎榜历史数据并建立索引"""
        if self._built:
            return

        # 先尝试从本地缓存加载
        if self._load_cache(start_date, end_date):
            self._built = True
            return

        print(f'获取龙虎榜数据 {start_date} ~ {end_date} ...')
        try:
            import akshare as ak
            df = ak.stock_lhb_detail_em(
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d'))
            if df is not None and not df.empty:
                self._parse_df(df, start_date, end_date)
        except Exception as e:
            print(f'  批量获取失败({e})，改用逐日获取...')
            self._fetch_day_by_day(start_date, end_date)

        self._built = True
        total_dates = len(self._index)
        total_codes = sum(len(v) for v in self._index.values())
        print(f'[龙虎榜索引] {total_dates}个交易日, {total_codes}条净买入记录')

        # 保存缓存
        self._save_cache()

    def _parse_df(self, df, start_date: date, end_date: date):
        """解析akshare龙虎榜DataFrame"""
        cols = list(df.columns)
        code_col = next((c for c in cols if c == '代码'), None)
        name_col = next((c for c in cols if c == '名称'), None)
        date_col = next((c for c in cols if c in ('上榜日', '上榜日期')), None)
        net_col = next((c for c in cols if c in ('龙虎榜净买额', '买卖净额', '净买额', '净买入额')), None)
        buy_col = next((c for c in cols if c in ('龙虎榜买入额', '买入金额', '买入总计')), None)
        sell_col = next((c for c in cols if c in ('龙虎榜卖出额', '卖出金额', '卖出总计')), None)
        reason_col = next((c for c in cols if c == '上榜原因'), None)

        for _, row in df.iterrows():
            code = str(row.get(code_col, '')).zfill(6) if code_col else ''
            if not code or code == '000000' or not _is_a_stock(code):
                continue

            # 解析日期
            trade_date = None
            if date_col:
                raw_date = row.get(date_col)
                if raw_date:
                    try:
                        if isinstance(raw_date, (date, datetime)):
                            trade_date = raw_date if isinstance(raw_date, date) else raw_date.date()
                        else:
                            raw_str = str(raw_date).replace('-', '').replace('/', '')[:8]
                            trade_date = date(int(raw_str[:4]), int(raw_str[4:6]), int(raw_str[6:8]))
                    except Exception:
                        continue

            if trade_date is None or trade_date < start_date or trade_date > end_date:
                continue

            net_amount = float(row.get(net_col, 0) or 0) if net_col else 0
            buy_amount = float(row.get(buy_col, 0) or 0) if buy_col else 0
            sell_amount = float(row.get(sell_col, 0) or 0) if sell_col else 0
            if net_amount == 0:
                net_amount = buy_amount - sell_amount

            # 只保留净买入>0的标的
            if net_amount > 0:
                self._index[trade_date].add(code)
                self._detail[trade_date][code] = {
                    'name': str(row.get(name_col, '')) if name_col else '',
                    'net_amount': net_amount,
                    'buy_amount': buy_amount,
                    'sell_amount': sell_amount,
                    'reason': str(row.get(reason_col, '')) if reason_col else '',
                }

    def _fetch_day_by_day(self, start_date: date, end_date: date):
        """逐日获取龙虎榜数据（回退方案）"""
        import akshare as ak
        from ashare_review.data.akshare_fetcher import AkshareFetcher
        ak_fetcher = AkshareFetcher()

        d = start_date
        count = 0
        total = (end_date - start_date).days + 1
        while d <= end_date:
            if d.weekday() < 5:
                try:
                    lhb_list = ak_fetcher.get_lhb(d.strftime('%Y%m%d'))
                    for l in lhb_list:
                        if l.net_amount > 0:
                            self._index[d].add(l.code)
                            self._detail[d][l.code] = {
                                'name': l.name,
                                'net_amount': l.net_amount,
                                'buy_amount': l.buy_amount,
                                'sell_amount': l.sell_amount,
                                'reason': l.reason,
                            }
                    if lhb_list:
                        count += 1
                except Exception:
                    pass
                time.sleep(0.1)  # 避免请求过快
            d += timedelta(days=1)
            if count > 0 and count % 20 == 0:
                print(f'  龙虎榜: {count}天已获取...')

    def get_net_buy_codes(self, d: date) -> Set[str]:
        """获取指定日期龙虎榜净买入的股票代码集合"""
        return self._index.get(d, set())

    def get_lhb_info(self, d: date, code: str) -> Optional[Dict]:
        """获取某日某股的龙虎榜详情"""
        return self._detail.get(d, {}).get(code)

    def _save_cache(self):
        """保存龙虎榜索引到本地JSON缓存"""
        try:
            data = {
                'index': {str(k): list(v) for k, v in self._index.items()},
                'detail': {str(k): {c: d for c, d in v.items()} for k, v in self._detail.items()},
                'updated': datetime.now().isoformat(),
            }
            os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)
            with open(self._cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, default=str)
        except Exception:
            pass

    def _load_cache(self, start_date: date, end_date: date) -> bool:
        """从本地缓存加载（检查日期范围是否覆盖）"""
        if not os.path.exists(self._cache_file):
            return False
        try:
            with open(self._cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 加载所有缓存数据
            idx = data.get('index', {})
            detail = data.get('detail', {})
            for date_str, codes in idx.items():
                try:
                    d = date.fromisoformat(date_str)
                except ValueError:
                    d = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
                if start_date <= d <= end_date:
                    self._index[d] = set(codes)
            for date_str, code_info in detail.items():
                try:
                    d = date.fromisoformat(date_str)
                except ValueError:
                    d = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
                if start_date <= d <= end_date:
                    self._detail[d] = code_info
            # 只要覆盖了大部分日期就认为有效
            covered = len(self._index)
            expected = (end_date - start_date).days // 7 * 5  # 大约的交易日数
            if covered >= expected * 0.5:  # 至少覆盖50%
                print(f'[龙虎榜缓存] 从本地加载 {covered} 天数据')
                return True
        except Exception:
            pass
        return False


# ======================================================================
# 竞价确认型回测器
# ======================================================================
class AuctionConfirmBacktest:
    """竞价确认型四种选股方式回测比较

    四种方式（同一天买入，便于对比）：
    - 方式1（1进2）: T-1日首板筛选 → T日开盘买入
    - 方式2（竞价抢筹）: T日竞价信号筛选 → T日开盘买入
    - 方式3（优化总筛选）: 多因子竞价精选（竞价+龙虎榜+涨停基因+量比）→ T日开盘买入
    - 方式4（龙虎榜竞价）: T-1龙虎榜净买 ∩ T日竞价抢筹 → T日开盘买入
    """

    # 默认参数（可配置）
    STOP_LOSS = -0.05      # 止损 -5%
    TAKE_PROFIT = 0.07     # 止盈 +7%
    MAX_HOLD_DAYS = 3      # 最长持仓天数

    def __init__(self):
        self.tdx = TdxReader()
        self.calendar = TradingCalendar()
        self.tdx_index: Optional[TdxLimitUpIndex] = None
        self.lhb_index: Optional[LhbIndex] = None
        self.one_two = SimulatedOneTwoScreener()
        self.auction = SimulatedAuctionScreener()

    # ==================================================================
    # 主入口
    # ==================================================================
    def run(self, lookback_days: int = 120,
            stop_loss: float = None, take_profit: float = None,
            max_hold: int = None, top_n: int = 10,
            enable_lhb: bool = True) -> Dict:
        """主入口：回测最近 N 个交易日，比较四种方式

        参数:
            lookback_days: 回测天数
            stop_loss: 止损比例（如 -0.05 = -5%）
            take_profit: 止盈比例（如 0.07 = +7%）
            max_hold: 最长持仓天数
            top_n: 每种方式每日最多取前N只
            enable_lhb: 是否启用龙虎榜竞价抢筹策略
        """
        if stop_loss is not None:
            self.STOP_LOSS = stop_loss
        if take_profit is not None:
            self.TAKE_PROFIT = take_profit
        if max_hold is not None:
            self.MAX_HOLD_DAYS = max_hold

        trade_dates = self._get_trade_dates(lookback_days)
        print(f'回测范围: {trade_dates[0]} ~ {trade_dates[-1]} 共{len(trade_dates)}个交易日')
        print(f'参数: 止损{self.STOP_LOSS*100:+.0f}% | 止盈{self.TAKE_PROFIT*100:+.0f}% | 持仓≤{self.MAX_HOLD_DAYS}天 | 每日Top{top_n}')
        print()

        # 构建TDX涨停索引
        print('构建TDX涨停索引...')
        self.tdx_index = TdxLimitUpIndex(self.tdx)
        self.tdx_index.build(trade_dates[0] - timedelta(days=3), trade_dates[-1])
        print()

        # 构建龙虎榜索引
        if enable_lhb:
            print('构建龙虎榜索引...')
            self.lhb_index = LhbIndex()
            self.lhb_index.build(trade_dates[0] - timedelta(days=3), trade_dates[-1])
            print()

        # 四种方式的交易记录
        trades_1v2 = []          # 方式1: 1进2
        trades_auction = []      # 方式2: 竞价抢筹
        trades_optimized = []    # 方式3: 优化总筛选（多因子竞价精选）
        trades_lhb_auction = []  # 方式4: 龙虎榜竞价抢筹

        for i, td in enumerate(trade_dates):
            td_str = td.strftime('%Y%m%d')

            # 大盘环境过滤
            if not self._check_market_env(td_str):
                continue

            # ---- 方式1: 1进2（T-1首板 → T开盘买） ----
            yesterday = self._prev_trade_date(td)
            ot_picks = self.one_two.screen(yesterday, self.tdx, self.tdx_index)
            ot_codes = {p['code']: p for p in ot_picks[:top_n]}

            for code, pick in ot_codes.items():
                trade = self._simulate_trade(
                    code=code, name=pick['name'],
                    entry_date=td,
                    signal_close=pick['close'],
                    score=pick['score'],
                    method='1进2',
                )
                if trade:
                    trades_1v2.append(trade)

            # ---- 方式2: 竞价抢筹（T日竞价信号 → T开盘买） ----
            auc_picks = self.auction.screen(td, self.tdx, self.tdx_index)
            auc_codes = {p['code']: p for p in auc_picks[:top_n]}

            for code, pick in auc_codes.items():
                trade = self._simulate_trade(
                    code=code, name=pick['name'],
                    entry_date=td,
                    signal_close=pick['close'],
                    score=pick['score'],
                    method='竞价抢筹',
                )
                if trade:
                    trades_auction.append(trade)

            # ---- 方式3: 优化总筛选（多因子竞价精选 → 每日Top3） ----
            # 因子：竞价量价(50%) + 龙虎榜确认(25%) + 涨停基因(15%) + 量比(10%)
            auc_all = self.auction.screen(td, self.tdx, self.tdx_index)  # 全部竞价候选
            auc_all_dict = {p['code']: p for p in auc_all}

            # 扩展候选池：T-1龙虎榜净买入的标的也评估竞价条件
            if self.lhb_index:
                for code in self.lhb_index.get_net_buy_codes(yesterday):
                    if code not in auc_all_dict:
                        result = self.auction._evaluate_stock(code, td, self.tdx)
                        if result:
                            auc_all_dict[code] = result

            # 多因子综合评分
            optimized_scored = []
            for code, pick in auc_all_dict.items():
                mf_score = pick['score'] * 0.5  # 基础：竞价量价评分占50%

                # 龙虎榜因子（25%）
                if self.lhb_index and code in self.lhb_index.get_net_buy_codes(yesterday):
                    lhb_info = self.lhb_index.get_lhb_info(yesterday, code)
                    net_amt = lhb_info.get('net_amount', 0) if lhb_info else 0
                    if net_amt > 10000:       # 净买>1亿 → 强力确认
                        mf_score += 25
                    elif net_amt > 5000:      # 净买>5000万
                        mf_score += 18
                    elif net_amt > 1000:      # 净买>1000万
                        mf_score += 10
                    else:
                        mf_score += 5

                # 涨停基因因子（15%）
                for reason in pick.get('reasons', []):
                    if '涨停基因' in reason:
                        # 提取次数
                        import re as _re
                        match = _re.search(r'(\d+)次', reason)
                        if match:
                            cnt = int(match.group(1))
                            mf_score += min(cnt * 3, 15)  # 最多15分

                # 量比因子（10%）
                for reason in pick.get('reasons', []):
                    if '爆量' in reason:
                        mf_score += 10
                    elif '放量' in reason:
                        mf_score += 6
                    elif '缩量' in reason:
                        mf_score -= 3

                optimized_scored.append((code, pick, round(mf_score, 1)))

            optimized_scored.sort(key=lambda x: -x[2])
            opt_top = optimized_scored[:top_n]

            for code, pick, mf_score in opt_top:
                trade = self._simulate_trade(
                    code=code, name=pick['name'],
                    entry_date=td,
                    signal_close=pick['close'],
                    score=min(round(mf_score), 100),
                    method='优化总筛选',
                )
                if trade:
                    trades_optimized.append(trade)

            # ---- 方式4: 龙虎榜竞价抢筹（T-1龙虎榜净买 ∩ T日竞价抢筹） ----
            if enable_lhb and self.lhb_index:
                lhb_codes = self.lhb_index.get_net_buy_codes(yesterday)
                lhb_auc_codes = set(auc_codes.keys()) & lhb_codes
                for code in lhb_auc_codes:
                    auc_p = auc_codes[code]
                    lhb_info = self.lhb_index.get_lhb_info(yesterday, code)
                    bonus = 5 if (lhb_info and lhb_info.get('net_amount', 0) > 10000) else 0  # 净买>1亿加分
                    trade = self._simulate_trade(
                        code=code, name=auc_p['name'],
                        entry_date=td,
                        signal_close=auc_p['close'],
                        score=min(auc_p['score'] + bonus, 100),
                        method='龙虎榜竞价',
                    )
                    if trade:
                        trades_lhb_auction.append(trade)

            # 进度
            ot_n = len(ot_codes)
            auc_n = len(auc_codes)
            opt_n = len(opt_top)
            lhb_n = len(set(auc_codes.keys()) & (self.lhb_index.get_net_buy_codes(yesterday) if self.lhb_index else set()))
            t1, t2, t3, t4 = len(trades_1v2), len(trades_auction), len(trades_optimized), len(trades_lhb_auction)
            print(f'\r[{i+1}/{len(trade_dates)}] {td_str} '
                  f'1进2:{ot_n} 竞价:{auc_n} 优化:{opt_n} 龙虎榜竞价:{lhb_n} | '
                  f'累计 1进2:{t1} 竞价:{t2} 优化:{t3} 龙虎榜竞价:{t4}',
                  end='', flush=True)

        print('\n')
        return self._compare_results(trades_1v2, trades_auction, trades_optimized,
                                     trades_lhb_auction, lookback_days)

    # ==================================================================
    # 模拟单笔交易
    # ==================================================================
    def _simulate_trade(self, code: str, name: str,
                        entry_date: date, signal_close: float,
                        score: int, method: str) -> Optional[Dict]:
        """模拟一笔竞价确认型交易

        逻辑：
        1. 入场日开盘价买入
        2. 止盈+7% / 止损-5%
        3. 最长持有 MAX_HOLD_DAYS 天
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

        # 解析日期列
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

        # 找到入场日索引
        entry_idx = None
        for i, d in enumerate(trade_dates):
            if d == entry_date:
                entry_idx = i
                break

        if entry_idx is None:
            return None

        entry_row = df.iloc[entry_idx]
        entry_price = float(entry_row['open'])

        if entry_price <= 0:
            return None

        # 高开放弃：开盘涨幅 > 7%（一字板预期，放弃）
        gap_pct = (entry_price - signal_close) / signal_close if signal_close > 0 else 0
        if gap_pct > 0.07:
            return {
                'code': code, 'name': name,
                'entry_date': str(entry_date),
                'entry_price': round(entry_price, 2),
                'exit_date': str(entry_date),
                'exit_price': round(entry_price, 2),
                'result': 'skipped',
                'return_pct': 0, 'days_held': 0,
                'score': score, 'method': method,
                'gap_pct': round(gap_pct * 100, 1),
            }

        # 止盈止损价
        target = round(entry_price * (1 + self.TAKE_PROFIT), 2)
        stop_loss = round(entry_price * (1 + self.STOP_LOSS), 2)

        # 向前看MAX_HOLD_DAYS天
        exit_idx = min(entry_idx + self.MAX_HOLD_DAYS, len(df) - 1)
        if exit_idx <= entry_idx:
            # 没有后续数据，用当天收盘价离场
            ret_pct = (float(entry_row['close']) - entry_price) / entry_price * 100
            return {
                'code': code, 'name': name,
                'entry_date': str(entry_date),
                'entry_price': round(entry_price, 2),
                'stop_loss': round(stop_loss, 2),
                'target': round(target, 2),
                'exit_date': str(entry_date),
                'exit_price': round(float(entry_row['close']), 2),
                'result': 'timeout',
                'return_pct': round(ret_pct, 1),
                'days_held': 0,
                'score': score, 'method': method,
                'gap_pct': round(gap_pct * 100, 1),
            }

        # A股T+1规则：买入当天不能卖出，只检查T+1及之后的交易日
        future = df.iloc[entry_idx + 1:exit_idx + 1]

        result = 'timeout'
        exit_price = float(future['close'].iloc[-1]) if not future.empty else float(entry_row['close'])
        exit_dt = str(trade_dates[min(exit_idx, len(trade_dates)-1)]) \
            if exit_idx < len(trade_dates) else str(entry_date)
        days_held = self.MAX_HOLD_DAYS

        # 从T+1开始逐日检查止损/止盈
        for j in range(len(future)):
            row = future.iloc[j]
            high = float(row['high'])
            low = float(row['low'])
            open_price = float(row['open'])

            if low <= stop_loss:
                result = 'loss'
                exit_price = round(min(stop_loss, open_price), 2)
                actual_idx = entry_idx + 1 + j
                exit_dt = str(trade_dates[min(actual_idx, len(trade_dates)-1)]) \
                    if actual_idx < len(trade_dates) else str(entry_date)
                days_held = j + 1
                break

            if high >= target:
                result = 'win'
                exit_price = target
                actual_idx = entry_idx + 1 + j
                exit_dt = str(trade_dates[min(actual_idx, len(trade_dates)-1)]) \
                    if actual_idx < len(trade_dates) else str(entry_date)
                days_held = j + 1
                break

        ret_pct = (exit_price - entry_price) / entry_price * 100

        return {
            'code': code, 'name': name,
            'entry_date': str(entry_date),
            'entry_price': round(entry_price, 2),
            'stop_loss': round(stop_loss, 2),
            'target': round(target, 2),
            'exit_date': exit_dt,
            'exit_price': round(exit_price, 2),
            'result': result,
            'return_pct': round(ret_pct, 1),
            'days_held': days_held,
            'score': score, 'method': method,
            'gap_pct': round(gap_pct * 100, 1),
        }

    # ==================================================================
    # 汇总统计
    # ==================================================================
    def _calc_stats(self, trades: List[Dict], method_name: str) -> Dict:
        """计算单种方式的统计"""
        valid = [t for t in trades if t['result'] != 'skipped']
        skipped = [t for t in trades if t['result'] == 'skipped']
        wins = [t for t in valid if t['result'] == 'win']
        losses = [t for t in valid if t['result'] == 'loss']
        timeouts = [t for t in valid if t['result'] == 'timeout']

        n_valid = len(valid)
        n_total = len(trades)
        n_win = len(wins)
        n_loss = len(losses)
        n_timeout = len(timeouts)
        n_skipped = len(skipped)

        win_rate = n_win / max(n_win + n_loss, 1) * 100

        avg_win = np.mean([t['return_pct'] for t in wins]) if wins else 0
        avg_loss = np.mean([t['return_pct'] for t in losses]) if losses else 0
        avg_return = np.mean([t['return_pct'] for t in valid]) if valid else 0

        total_profit = sum(t['return_pct'] for t in wins)
        total_loss = sum(abs(t['return_pct']) for t in losses)
        profit_factor = total_profit / max(total_loss, 0.01)

        # 最大回撤（按时间排序的累计收益）
        sorted_trades = sorted(valid, key=lambda x: x['entry_date'])
        cum_returns = []
        cum = 0
        peak = 0
        max_dd = 0
        for t in sorted_trades:
            cum += t['return_pct']
            cum_returns.append(cum)
            peak = max(peak, cum)
            dd = peak - cum
            max_dd = max(max_dd, dd)

        # 按年化（假设平均持仓3天）
        avg_days_held = np.mean([t['days_held'] for t in valid]) if valid else 0

        return {
            'method': method_name,
            'total_signals': n_total,
            'valid_trades': n_valid,
            'skipped': n_skipped,
            'wins': n_win,
            'losses': n_loss,
            'timeouts': n_timeout,
            'win_rate': round(win_rate, 1),
            'avg_return': round(float(avg_return), 1),
            'avg_win': round(float(avg_win), 1),
            'avg_loss': round(float(avg_loss), 1),
            'total_profit': round(float(total_profit), 1),
            'total_loss': round(float(total_loss), 1),
            'profit_factor': round(float(profit_factor), 2),
            'max_drawdown_pct': round(float(max_dd), 1),
            'avg_days_held': round(float(avg_days_held), 1),
            'all_trades': sorted(valid, key=lambda x: x['entry_date'], reverse=True),
        }

    def _compare_results(self, trades_1v2: List[Dict],
                         trades_auction: List[Dict],
                         trades_optimized: List[Dict],
                         trades_lhb_auction: List[Dict],
                         total_days: int) -> Dict:
        """比较四种方式的回测结果"""
        stats_1v2 = self._calc_stats(trades_1v2, '1进2盘后筛选')
        stats_auction = self._calc_stats(trades_auction, '竞价抢筹(高开2%~6%)')
        stats_optimized = self._calc_stats(trades_optimized, '优化总筛选(多因子精选)')
        stats_lhb = self._calc_stats(trades_lhb_auction, '龙虎榜竞价')

        return {
            'parameters': {
                'stop_loss_pct': round(self.STOP_LOSS * 100, 0),
                'take_profit_pct': round(self.TAKE_PROFIT * 100, 0),
                'max_hold_days': self.MAX_HOLD_DAYS,
                'total_days': total_days,
            },
            'methods': {
                '1进2': stats_1v2,
                '竞价抢筹': stats_auction,
                '优化总筛选': stats_optimized,
                '龙虎榜竞价': stats_lhb,
            },
            'ranking': self._rank_methods(stats_1v2, stats_auction, stats_optimized, stats_lhb),
        }

    def _rank_methods(self, *stats_list) -> List[Dict]:
        """按综合指标排名"""
        rankings = []
        for s in stats_list:
            # 综合得分 = 胜率*0.4 + 平均收益*0.3 + 盈亏比*0.2 + 交易次数*0.1
            score = (s['win_rate'] * 0.4 +
                     max(0, s['avg_return']) * 3 * 0.3 +
                     min(s['profit_factor'], 5) * 10 * 0.2 +
                     min(s['valid_trades'] / 2, 10) * 0.1)
            rankings.append({
                'method': s['method'],
                'composite_score': round(score, 1),
                'win_rate': s['win_rate'],
                'avg_return': s['avg_return'],
                'profit_factor': s['profit_factor'],
                'valid_trades': s['valid_trades'],
            })

        rankings.sort(key=lambda x: x['composite_score'], reverse=True)
        return rankings

    # ==================================================================
    # 辅助
    # ==================================================================
    def _get_trade_dates(self, lookback: int) -> List[date]:
        """获取最近N个交易日"""
        d = date.today() - timedelta(days=1)
        dates = []
        while len(dates) < lookback:
            if d.weekday() < 5 and self.calendar.is_trading_day(d):
                dates.append(d)
            d -= timedelta(days=1)
            if len(dates) == 0 and (date.today() - d).days > 365:
                break
        return list(reversed(dates))

    def _prev_trade_date(self, d: date) -> date:
        """前一个交易日"""
        d = d - timedelta(days=1)
        while d.weekday() >= 5 or not self.calendar.is_trading_day(d):
            d -= timedelta(days=1)
            if (date.today() - d).days > 365:
                break
        return d

    # ---- 大盘环境过滤 ----
    _index_cache = None

    def _check_market_env(self, trade_date: str) -> bool:
        """上证跌幅>1.5%则空仓"""
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


# ======================================================================
# 参数扫描：测试不同止损止盈组合
# ======================================================================
def run_param_sweep(lookback_days: int = 60) -> List[Dict]:
    """扫描不同的止损止盈参数组合，找出最优参数"""
    stop_losses = [-0.03, -0.05, -0.07]
    take_profits = [0.05, 0.07, 0.10]

    results = []
    for sl in stop_losses:
        for tp in take_profits:
            print(f'\n{"="*60}')
            print(f'测试: 止损{sl*100:+.0f}% | 止盈{tp*100:+.0f}%')
            print(f'{"="*60}')

            bt = AuctionConfirmBacktest()
            result = bt.run(
                lookback_days=lookback_days,
                stop_loss=sl,
                take_profit=tp,
                max_hold=3,
                top_n=10,
            )

            # 记录每种方式的胜率
            for key, stats in result['methods'].items():
                results.append({
                    'stop_loss': f'{sl*100:+.0f}%',
                    'take_profit': f'{tp*100:+.0f}%',
                    'method': key,
                    'win_rate': stats['win_rate'],
                    'avg_return': stats['avg_return'],
                    'profit_factor': stats['profit_factor'],
                    'valid_trades': stats['valid_trades'],
                    'wins': stats['wins'],
                    'losses': stats['losses'],
                })

    return results


# ======================================================================
# CLI & 报告输出
# ======================================================================
RESULT_LABELS = {'win': '胜', 'loss': '负', 'timeout': '超时', 'skipped': '高开放弃'}


def _print_comparison(result: Dict):
    """打印四种方式对比报告"""
    params = result['parameters']
    methods = result['methods']
    ranking = result['ranking']
    all_keys = ['1进2', '竞价抢筹', '优化总筛选', '龙虎榜竞价']

    print(f'\n{"="*80}')
    print(f'  竞价确认型四种选股方式回测对比')
    print(f'{"="*80}')
    print(f'  参数: 止损{params["stop_loss_pct"]:+.0f}% | '
          f'止盈{params["take_profit_pct"]:+.0f}% | '
          f'持仓≤{params["max_hold_days"]}天 | 回看{params["total_days"]}天')
    print()

    # 排名
    print(f'  {"-"*76}')
    print(f'  {"排名":<4} {"选股方式":<22} {"综合分":<8} {"胜率":<8} {"均收益":<8} {"盈亏比":<8} {"交易数":<8}')
    print(f'  {"-"*76}')
    for i, r in enumerate(ranking):
        medal = ['[1]', '[2]', '[3]', '[4]'][i] if i < 4 else f'{i+1}.'
        print(f'  {medal:<4} {r["method"]:<22} {r["composite_score"]:<8.1f} '
              f'{r["win_rate"]:<8.1f}% {r["avg_return"]:<8.1f}% '
              f'{r["profit_factor"]:<8.2f} {r["valid_trades"]:<8}')
    print(f'  {"-"*76}')
    print()

    # 详细统计
    for key in all_keys:
        stats = methods.get(key)
        if not stats:
            continue
        print(f'  +-- {stats["method"]} {"-"*40}+')
        print(f'  | 信号总数: {stats["total_signals"]:<5}  '
              f'有效交易: {stats["valid_trades"]:<5}  '
              f'高开放弃: {stats["skipped"]:<5}     |')
        print(f'  | 胜: {stats["wins"]:<5} 负: {stats["losses"]:<5} '
              f'超时: {stats["timeouts"]:<5}                         |')
        print(f'  | 胜率: {stats["win_rate"]:<6.1f}%  '
              f'平均收益: {stats["avg_return"]:<+6.1f}%  '
              f'盈亏比: {stats["profit_factor"]:<6.2f}            |')
        print(f'  | 平均盈利: {stats["avg_win"]:<+5.1f}%  '
              f'平均亏损: {stats["avg_loss"]:<+5.1f}%  '
              f'最大回撤: {stats["max_drawdown_pct"]:<6.1f}%          |')
        print(f'  | 平均持仓: {stats["avg_days_held"]:<5.1f}天    '
              f'总盈利: {stats["total_profit"]:<+6.1f}%  '
              f'总亏损: {stats["total_loss"]:<6.1f}%         |')
        print(f'  +{"-"*62}+')
        print()

    # 最近交易
    print(f'  {"-"*80}')
    print(f'  最近10笔交易 (四种方式合并)')
    print(f'  {"-"*80}')
    all_recent = []
    for key in all_keys:
        stats = methods.get(key)
        if stats:
            for t in stats['all_trades'][:5]:
                all_recent.append(t)
    all_recent.sort(key=lambda x: x['entry_date'], reverse=True)

    print(f'  {"日期":<12} {"方式":<16} {"代码":<8} {"名称":<8} '
          f'{"结果":<6} {"收益%":>7} {"持仓":>4} {"入场价":>8} {"离场价":>8}')
    print(f'  {"-"*80}')
    for t in all_recent[:10]:
        if t['result'] == 'win':
            icon = '[胜]'
        elif t['result'] == 'loss':
            icon = '[负]'
        elif t['result'] == 'skipped':
            icon = '[弃]'
        else:
            icon = '[超]'
        method_short = t['method'][:16]
        print(f'  {t["entry_date"]:<12} {method_short:<16} {t["code"]:<8} {t["name"]:<8} '
              f'{icon} {t["return_pct"]:>+6.1f}% '
              f'{t["days_held"]:>3}天 '
              f'{t["entry_price"]:>8.2f} {t["exit_price"]:>8.2f}')

    print()
    print(f'  [说明] T-1日盘后1进2筛选 -> T日开盘买入 -> 止盈{params["take_profit_pct"]:+.0f}%/止损{params["stop_loss_pct"]:+.0f}%')
    print(f'         T日竞价抢筹筛选(高开2%-6%) -> T日开盘买入 -> 同止损止盈')
    print(f'         优化总筛选 = 竞价量价(50%)+龙虎榜(25%)+涨停基因(15%)+量比(10%) → 每日Top3')
    print(f'         龙虎榜竞价 = T-1龙虎榜净买入 且 T日竞价抢筹 (机构+游资共振)')
    print(f'         [胜]=止盈 [负]=止损 [超]=到期 [弃]=高开放弃')
    print()


def _export_csv(result: Dict, filepath: str):
    """导出三种方式全部交易到CSV"""
    methods = result['methods']
    all_trades = []
    for key, stats in methods.items():
        for t in stats.get('all_trades', []):
            all_trades.append(t)

    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['选股方式', '入场日期', '代码', '名称', '入场价', '止损价', '止盈价',
                         '离场日期', '离场价', '结果', '收益率%', '持仓天数', '评分'])
        for t in sorted(all_trades, key=lambda x: x['entry_date'], reverse=True):
            writer.writerow([
                t['method'], t['entry_date'], t['code'], t['name'],
                t['entry_price'], t['stop_loss'], t['target'],
                t['exit_date'], t['exit_price'],
                RESULT_LABELS.get(t['result'], t['result']),
                t['return_pct'], t['days_held'], t['score'],
            ])
    print(f'\n[CSV] 全部 {len(all_trades)} 笔交易已导出 → {filepath}')


def _export_json(result: Dict, filepath: str):
    """导出为JSON"""
    # 移除all_trades以减少文件大小（可选）
    export = {
        'parameters': result['parameters'],
        'ranking': result['ranking'],
        'methods': {},
    }
    for key, stats in result['methods'].items():
        export['methods'][key] = {
            k: v for k, v in stats.items()
            if k != 'all_trades'
        }
        export['methods'][key]['trade_count'] = stats['valid_trades']

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(export, f, ensure_ascii=False, indent=2, default=str)
    print(f'[JSON] 统计已导出 → {filepath}')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='竞价确认型三种选股方式回测对比')
    parser.add_argument('--days', type=int, default=120,
                        help='回测天数 (默认120)')
    parser.add_argument('--stop-loss', type=float, default=-0.05, metavar='PCT',
                        help='止损比例 (默认-0.05=-5%%)')
    parser.add_argument('--take-profit', type=float, default=0.07, metavar='PCT',
                        help='止盈比例 (默认0.07=+7%%)')
    parser.add_argument('--hold', type=int, default=3, metavar='DAYS',
                        help='最长持仓天数 (默认3)')
    parser.add_argument('--top', type=int, default=10, metavar='N',
                        help='每种方式每日最多取前N只 (默认10)')
    parser.add_argument('--no-lhb', action='store_true',
                        help='禁用龙虎榜竞价抢筹策略')
    parser.add_argument('--param-sweep', action='store_true',
                        help='参数扫描：测试不同止损止盈组合')
    parser.add_argument('--json', action='store_true',
                        help='输出JSON统计')
    parser.add_argument('--csv', type=str, default=None, metavar='PATH',
                        help='导出全部交易为CSV')
    parser.add_argument('--output', '-o', type=str, default=None, metavar='PATH',
                        help='导出到文件（自动选CSV/JSON/TXT）')
    args = parser.parse_args()

    if args.param_sweep:
        print(f'\n{"="*80}')
        print(f'  竞价确认型 — 参数扫描（测试不同止损止盈组合）')
        print(f'  回看{args.days}天')
        print(f'{"="*80}\n')
        sweep_results = run_param_sweep(lookback_days=args.days)

        # 打印参数扫描结果
        print(f'  {"-"*80}')
        print(f'  参数扫描结果汇总')
        print(f'{"="*80}')
        print(f'  {"止损":<8} {"止盈":<8} {"方式":<20} {"胜率":<8} {"均收益":<8} {"盈亏比":<8} {"交易数":<8}')
        print(f'  {"-"*80}')

        # 按方式+止损止盈排序
        sweep_results.sort(key=lambda x: (x['method'], -x['win_rate']))
        for r in sweep_results:
            print(f'  {r["stop_loss"]:<8} {r["take_profit"]:<8} {r["method"]:<20} '
                  f'{r["win_rate"]:<7.1f}% {r["avg_return"]:<+7.1f}% '
                  f'{r["profit_factor"]:<8.2f} {r["valid_trades"]:<8}')

        # 找每种方式的最优参数
        print(f'\n  {"-"*80}')
        print(f'  每种方式最优参数:')
        print(f'  {"-"*80}')
        for method in ['1进2', '竞价抢筹', '优化总筛选']:
            method_results = [r for r in sweep_results if r['method'] == method]
            if not method_results:
                continue
            best = max(method_results, key=lambda x: x['win_rate'])
            print(f'  {method}: 止损{best["stop_loss"]} 止盈{best["take_profit"]} '
                  f'-> 胜率{best["win_rate"]:.1f}% 均收益{best["avg_return"]:+.1f}% '
                  f'盈亏比{best["profit_factor"]:.2f} ({best["valid_trades"]}笔)')
        return

    # ---- 正常运行 ----
    print(f'\n{"="*80}')
    print(f'  竞价确认型四种选股方式回测')
    print(f'  止损{args.stop_loss*100:+.0f}% | 止盈{args.take_profit*100:+.0f}% '
          f'| 持仓≤{args.hold}天 | 回看{args.days}天')
    print(f'{"="*80}\n')

    bt = AuctionConfirmBacktest()
    result = bt.run(
        lookback_days=args.days,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        max_hold=args.hold,
        top_n=args.top,
        enable_lhb=not args.no_lhb,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_comparison(result)

    if args.csv:
        _export_csv(result, args.csv)

    if args.output:
        if args.output.endswith('.csv'):
            _export_csv(result, args.output)
        elif args.output.endswith('.json'):
            _export_json(result, args.output)
        else:
            # TXT输出
            _export_csv(result, args.output)  # 用CSV格式


if __name__ == '__main__':
    main()
