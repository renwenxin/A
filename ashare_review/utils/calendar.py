"""交易日历"""
import pandas as pd
from datetime import date, timedelta
from typing import List

class TradingCalendar:
    def __init__(self):
        self._cache = None

    def _load(self):
        if self._cache is None:
            try:
                import akshare as ak
                df = ak.tool_trade_date_hist_sina()
                self._cache = set(
                    pd.to_datetime(df['trade_date']).dt.date.tolist())
            except Exception:
                self._cache = set()

    def is_trading_day(self, d: date = None) -> bool:
        if d is None:
            d = date.today()
        self._load()
        return d in self._cache

    def prev_trading_day(self, d: date = None, offset: int = 1) -> date:
        if d is None:
            d = date.today()
        self._load()
        count = 0
        while count < offset:
            d = d - timedelta(days=1)
            if d.weekday() < 5:
                count += 1
        return d
