"""通达信 .day 文件解析器"""
import os, struct
from datetime import date
from typing import List, Tuple
import pandas as pd
from .models import DailyBar

RECORD_SIZE = 32


def is_a_share_stock(market: str, code: str) -> bool:
    """判断某证券是否沪深京 A 股股票（用于全市场统计）。

    TDX 的 lday 目录混有大量非股票证券，统计涨跌家数时必须过滤：
      - sh: 5xxxxx=ETF/LOF基金, 8xxxxx=通达信板块指数(880), 1xxxxx=可转债,
            0xxxxx=上证/沪深指数, 2xxxxx=逆回购, 9xxxxx=沪B股
      - sz: 1xxxxx=基金/转债(123/128), 2xxxxx=深B股
      - bj: 899xxx=北证指数
    只保留真正的 A 股代码段（沪主板60x/科创688、深主板000/001/002/003、
    创业板300/301、北交所43/83/87/88/920）。
    """
    if not (len(code) == 6 and code.isdigit()):
        return False
    if market == 'sh':
        return code.startswith('6')
    if market == 'sz':
        # 深市 A 股：主板 000/001/002/003、创业板 300/301。
        # 注意用精确前缀，否则 sz399xxx（深证成指/创业板指/沪深300 等指数）
        # 会被误判为股票，污染全市场涨跌家数统计。
        return code.startswith(('000', '001', '002', '003', '300', '301'))
    if market == 'bj':
        return code.startswith(('4', '83', '87', '88', '920'))
    return False


def parse_day_file(filename: str, data: bytes) -> List[DailyBar]:
    """解析.day文件二进制内容，返回DailyBar列表
    filename格式: 'sh000001' 前2位=市场, 2-8位=代码
    data: 32字节/条记录的二进制数据
    """
    code = filename[2:8]
    market = filename[:2]
    results = []
    for i in range(len(data) // RECORD_SIZE):
        offset = i * RECORD_SIZE
        dt, op, hi, lo, cl, amt, vol, _ = struct.unpack('IIIIIfII', data[offset:offset+RECORD_SIZE])
        dt_str = str(dt)
        results.append(DailyBar(
            code=code, name='', market=market,
            trade_date=date(int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:8])),
            open=op/100.0, high=hi/100.0, low=lo/100.0, close=cl/100.0,
            volume=vol, amount=amt))
    return results


class TdxReader:
    """通达信日线数据读取器

    读取 vipdoc/{sh,sz,bj}/lday/*.day 文件

    数据路径优先级：
    1. 构造函数参数 tdx_root
    2. 环境变量 TDX_ROOT
    3. 默认值 D:\\tdx
    """

    def __init__(self, tdx_root: str = None):
        if tdx_root is None:
            tdx_root = os.environ.get('TDX_ROOT', r'D:\tdx')
        self.tdx_root = tdx_root
        self.vipdoc = os.path.join(tdx_root, 'vipdoc')
        if not os.path.isdir(self.vipdoc):
            import logging
            logging.getLogger(__name__).warning(
                f"通达信数据目录不存在: {self.vipdoc}。"
                f"请设置环境变量 TDX_ROOT 指向正确的通达信安装目录。")

    def _market_dir(self, market: str) -> str:
        m = market[:2].lower()
        return os.path.join(self.vipdoc, m, 'lday')

    def _minute_dir(self, market: str) -> str:
        m = market[:2].lower()
        return os.path.join(self.vipdoc, m, 'minline')

    def read_minute_max_volume(self, code: str, market: str, target_date: str = None) -> int:
        """读取1分钟线的当日最高单分钟成交量（单位：手）

        TDX 1分钟线格式 (.lc1): 32字节/条
        date(I) time(I) open(f) high(f) low(f) amount(f) volume(I) reserved(I)

        文件按时间升序排列，每天约240条记录（4小时×60分钟）。
        取最后240条作为最新交易日数据，取volume最大值。

        返回: 最高单分钟成交量（手），无数据返回0
        """
        fpath = os.path.join(self._minute_dir(market), f'{market}{code}.lc1')
        if not os.path.exists(fpath):
            return 0
        try:
            with open(fpath, 'rb') as f:
                data = f.read()
            total_records = len(data) // 32
            if total_records < 10:
                return 0

            # 最后~240条 = 最新交易日
            lookback = min(240, total_records)
            start_record = total_records - lookback

            max_vol = 0  # 股
            for i in range(start_record, total_records):
                offset = i * 32
                # IIffffII: date(I) time(I) o(f) h(f) l(f) amt(f) vol(I) reserved(I)
                _, _, _, _, _, _, vol, _ = struct.unpack(
                    'IIffffII', data[offset:offset+32])
                if vol > max_vol and vol < 500_000_000:  # 过滤异常值
                    max_vol = vol

            return max_vol // 100  # 股→手
        except Exception:
            return 0

    def list_stocks(self) -> List[Tuple[str, str]]:
        """列出所有股票 (code, market)"""
        stocks = []
        for mkt in ['sh', 'sz', 'bj']:
            d = self._market_dir(mkt)
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.endswith('.day'):
                        stocks.append((fn[2:8], mkt))
        return sorted(stocks)

    def read_daily(self, code: str, market: str) -> pd.DataFrame:
        """读取单只股票日线数据，返回DataFrame"""
        fpath = os.path.join(self._market_dir(market), f'{market}{code}.day')
        if not os.path.exists(fpath):
            raise FileNotFoundError(f'数据文件不存在: {fpath}')
        with open(fpath, 'rb') as f:
            bars = parse_day_file(f'{market}{code}', f.read())
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame([{
            'trade_date': b.trade_date, 'open': b.open, 'high': b.high,
            'low': b.low, 'close': b.close, 'volume': b.volume, 'amount': b.amount
        } for b in bars])
        return df.sort_values('trade_date').reset_index(drop=True)

    def read_multi(self, codes: List[Tuple[str, str]]) -> pd.DataFrame:
        """批量读取，返回带code列的合并DataFrame"""
        frames = []
        for code, mkt in codes:
            try:
                df = self.read_daily(code, mkt)
                if not df.empty:
                    df['code'], df['market'] = code, mkt
                    frames.append(df)
            except FileNotFoundError:
                pass
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def get_index_turnover(self, trade_date: date = None) -> dict:
        """从三大指数获取市场总成交额（亿）"""
        indices = [
            ('000001', 'sh', '上证指数'),
            ('399001', 'sz', '深证成指'),
        ]
        result = {'sh_amount': 0, 'sz_amount': 0, 'total_amount': 0, 'date': None}
        for code, mkt, name in indices:
            try:
                df = self.read_daily(code, mkt)
                if df.empty:
                    continue
                if trade_date:
                    df = df[df['trade_date'] == trade_date]
                bar = df.iloc[-1]
                amt_yi = bar['amount'] / 1e8
                if mkt == 'sh':
                    result['sh_amount'] = round(amt_yi, 0)
                else:
                    result['sz_amount'] = round(amt_yi, 0)
                result['date'] = bar['trade_date']
            except (FileNotFoundError, IndexError):
                pass
        result['total_amount'] = round(result['sh_amount'] + result['sz_amount'], 0)
        return result

    def get_market_breadth(self, trade_date: date = None) -> dict:
        """扫描全市场 .day 文件，统计沪深京 A 股涨跌家数

        每个文件只读尾部 64 字节（最后 2 条记录），
        A 股约 5600 只约需 1-3 秒。结果按日期缓存到内存。
        只统计真正的 A 股（is_a_share_stock 过滤），
        排除 ETF/LOF、转债、B 股、指数、通达信板块指数等非股票证券。
        """
        cache_key = str(trade_date) if trade_date else 'latest'
        if not hasattr(self, '_breadth_cache'):
            self._breadth_cache = {}
        if cache_key in self._breadth_cache:
            return self._breadth_cache[cache_key]

        up = down = flat = limit_up = limit_down = 0
        chg_sum = 0.0
        scanned = 0

        for mkt in ['sh', 'sz', 'bj']:
            d = self._market_dir(mkt)
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                if not fn.endswith('.day'):
                    continue
                # 文件名形如 sh600000.day / sz000001.day / bj920000.day
                code = fn[:-4][2:]
                if not is_a_share_stock(mkt, code):
                    continue
                fpath = os.path.join(d, fn)
                try:
                    fsize = os.path.getsize(fpath)
                    if fsize < RECORD_SIZE * 2:
                        continue
                    # 只读尾部 64 字节（最后 2 条 32 字节记录）
                    read_size = min(RECORD_SIZE * 2, fsize)
                    with open(fpath, 'rb') as f:
                        f.seek(fsize - read_size)
                        tail = f.read(read_size)

                    last_rec = tail[-RECORD_SIZE:]
                    prev_rec = tail[-RECORD_SIZE*2:-RECORD_SIZE] if len(tail) >= RECORD_SIZE*2 else last_rec

                    _, _, _, _, close, amt, _, _ = struct.unpack('IIIIIfII', last_rec)
                    _, _, _, _, prev_close, _, _, _ = struct.unpack('IIIIIfII', prev_rec)

                    close_price = close / 100.0
                    prev_price = prev_close / 100.0

                    if trade_date:
                        dt_int = struct.unpack('I', last_rec[:4])[0]
                        dt_str = str(dt_int)
                        bar_date = date(int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:8]))
                        if bar_date != trade_date:
                            continue

                    scanned += 1

                    if prev_price == 0:
                        flat += 1
                        continue

                    change_pct = (close_price - prev_price) / prev_price * 100
                    chg_sum += change_pct
                    if change_pct > 0:
                        up += 1
                    elif change_pct < 0:
                        down += 1
                    else:
                        flat += 1

                    if change_pct >= 9.9:
                        limit_up += 1
                    elif change_pct <= -9.9:
                        limit_down += 1

                except (OSError, struct.error, IndexError):
                    continue

        result = {
            'up_count': up, 'down_count': down, 'flat_count': flat,
            'limit_up_count': limit_up, 'limit_down_count': limit_down,
            'scanned': scanned,
            'avg_change_pct': round(chg_sum / scanned, 2) if scanned else 0.0,
        }
        self._breadth_cache[cache_key] = result
        return result
