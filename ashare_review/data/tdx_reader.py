"""通达信 .day 文件解析器"""
import os, struct
from datetime import date
from typing import List, Tuple
import pandas as pd
from .models import DailyBar

RECORD_SIZE = 32

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
    """

    def __init__(self, tdx_root: str = r'D:\tdx'):
        self.vipdoc = os.path.join(tdx_root, 'vipdoc')

    def _market_dir(self, market: str) -> str:
        m = market[:2].lower()
        return os.path.join(self.vipdoc, m, 'lday')

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
