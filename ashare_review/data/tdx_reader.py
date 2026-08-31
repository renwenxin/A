"""通达信 .day 文件解析器"""
import os, struct
from datetime import date
from typing import List, Tuple, Optional
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

    def read_minute_bars(self, code: str, market: str, days: int = 15) -> List[dict]:
        """读取最近 days 个交易日的 1 分钟线。

        TDX .lc1 每 32 字节一条：
          date(H) time(H) open(f) high(f) low(f) close(f) amount(f) volume(I) reserved(I)
        date 为 TDX 编码：(year-2004)*2048 + month*100 + day
        time 为当天分钟数（如 571 = 09:31）。

        返回 [{date:'YYYY-MM-DD', time:571, open, high, low, close, amount, volume}]
        按文件顺序（时间升序）。volume 单位为股。
        """
        fpath = os.path.join(self._minute_dir(market), f'{market}{code}.lc1')
        if not os.path.exists(fpath):
            return []
        try:
            with open(fpath, 'rb') as f:
                data = f.read()
            total = len(data) // 32
            if total == 0:
                return []
            start = max(0, total - days * 240)
            bars = []
            for i in range(start, total):
                offset = i * 32
                dt, tm, op, hi, lo, cl, amt, vol, _ = struct.unpack(
                    'HHfffffII', data[offset:offset + 32])
                yr = 2004 + dt // 2048
                rest = dt % 2048
                bars.append({
                    'date': f'{yr}-{rest // 100:02d}-{rest % 100:02d}',
                    'time': tm,
                    'open': op, 'high': hi, 'low': lo, 'close': cl,
                    'amount': amt, 'volume': vol,
                })
            return bars
        except Exception:
            return []

    def read_minute_max_volume(self, code: str, market: str, target_date: str = None) -> int:
        """读取最新交易日的最高单分钟成交量（单位：手）

        基于 read_minute_bars 正确解析 .lc1（旧版 struct 解包错误已修复）。
        返回: 最高单分钟成交量（手），无数据返回0
        """
        bars = self.read_minute_bars(code, market, days=1)
        if not bars:
            return 0
        last_date = bars[-1]['date']
        max_vol = 0
        for b in bars:
            if b['date'] != last_date:
                continue
            v = b['volume']
            if 0 < v < 500_000_000:  # 过滤异常值
                max_vol = max(max_vol, v)
        return max_vol // 100  # 股→手

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

    def _find_date_pair(self, fpath: str, target_date: date) -> Optional[tuple]:
        """从 .day 文件尾部向前定位 target_date 的记录及其前一交易日记录。

        返回 (target_rec, prev_rec)；目标日在文件首条（无前一交易日）或找不到
        时返回 None。target_rec 的开盘/收盘 vs prev_rec 的收盘即该日开盘涨跌。
        """
        target_int = target_date.year * 10000 + target_date.month * 100 + target_date.day
        fsize = os.path.getsize(fpath)
        if fsize < RECORD_SIZE * 2:
            return None
        with open(fpath, 'rb') as f:
            pos = fsize - RECORD_SIZE
            while pos >= 0:
                f.seek(pos)
                rec = f.read(RECORD_SIZE)
                if struct.unpack('I', rec[:4])[0] == target_int:
                    if pos < RECORD_SIZE:
                        return None
                    f.seek(pos - RECORD_SIZE)
                    prev = f.read(RECORD_SIZE)
                    return rec, prev
                pos -= RECORD_SIZE
        return None

    def get_market_breadth(self, trade_date: date = None) -> dict:
        """扫描全市场 .day 文件，统计沪深京 A 股涨跌家数

        trade_date=None → 最新交易日（读每个文件尾部 2 条记录，快）；
        trade_date=指定日期 → 从尾部向前定位该日记录 + 前一交易日，计算该日
        口径（不再静默回退最新日，修掉历史日期错位 bug）。
        A 股约 5600 只约需 1-3 秒。结果按日期缓存到内存。
        只统计真正的 A 股（is_a_share_stock 过滤），
        排除 ETF/LOF、转债、B 股、指数、通达信板块指数等非股票证券。
        除收盘涨跌家数外，同时统计开盘口径（open_up_count/open_down_count）：
        高开=open>昨收，低开=open<昨收，供"今日开盘涨跌家数"展示。
        """
        cache_key = str(trade_date) if trade_date else 'latest'
        if not hasattr(self, '_breadth_cache'):
            self._breadth_cache = {}
        if cache_key in self._breadth_cache:
            return self._breadth_cache[cache_key]

        up = down = flat = limit_up = limit_down = 0
        open_up = open_down = open_flat = 0
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
                    if trade_date is None:
                        # 最新交易日：只读尾部 64 字节（最后 2 条记录）
                        fsize = os.path.getsize(fpath)
                        if fsize < RECORD_SIZE * 2:
                            continue
                        read_size = min(RECORD_SIZE * 2, fsize)
                        with open(fpath, 'rb') as f:
                            f.seek(fsize - read_size)
                            tail = f.read(read_size)
                        last_rec = tail[-RECORD_SIZE:]
                        prev_rec = tail[-RECORD_SIZE*2:-RECORD_SIZE] if len(tail) >= RECORD_SIZE*2 else last_rec
                    else:
                        # 指定日期：定位该日记录 + 前一交易日
                        pair = self._find_date_pair(fpath, trade_date)
                        if pair is None:
                            continue
                        last_rec, prev_rec = pair

                    _, open_i, _, _, close, amt, _, _ = struct.unpack('IIIIIfII', last_rec)
                    _, _, _, _, prev_close, _, _, _ = struct.unpack('IIIIIfII', prev_rec)

                    close_price = close / 100.0
                    prev_price = prev_close / 100.0
                    open_price = open_i / 100.0

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

                    # 开盘口径：高开=open>昨收，低开=open<昨收
                    if prev_price > 0 and open_price > 0:
                        open_pct = (open_price - prev_price) / prev_price * 100
                        if open_pct > 0:
                            open_up += 1
                        elif open_pct < 0:
                            open_down += 1
                        else:
                            open_flat += 1
                    elif prev_price > 0:
                        open_flat += 1  # 开盘价缺失，归入平开

                    if change_pct >= 9.9:
                        limit_up += 1
                    elif change_pct <= -9.9:
                        limit_down += 1

                except (OSError, struct.error, IndexError):
                    continue

        result = {
            'up_count': up, 'down_count': down, 'flat_count': flat,
            'limit_up_count': limit_up, 'limit_down_count': limit_down,
            'open_up_count': open_up, 'open_down_count': open_down,
            'open_flat_count': open_flat,
            'scanned': scanned,
            'avg_change_pct': round(chg_sum / scanned, 2) if scanned else 0.0,
        }
        self._breadth_cache[cache_key] = result
        return result
