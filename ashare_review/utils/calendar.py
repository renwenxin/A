"""交易日历 — 基于 akshare 新浪交易日历 + 本地节假日缓存"""
import json
import os
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)

# 内置节假日回退列表（2026年A股休市日，不含周末）
_BUILTIN_HOLIDAYS_2026 = {
    # 元旦
    date(2026, 1, 1), date(2026, 1, 2),
    # 春节
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 23), date(2026, 2, 24),
    # 清明节
    date(2026, 4, 6),
    # 劳动节
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),
    # 端午节
    date(2026, 6, 22), date(2026, 6, 23),
    # 中秋节
    date(2026, 9, 25),
    # 国庆节
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),
    date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8),
}


class TradingCalendar:
    def __init__(self, cache_dir: str = None):
        self._cache: Optional[set] = None
        self._cache_dir = cache_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data'
        )
        self._cache_file = os.path.join(self._cache_dir, 'trading_calendar.json')

    def _load_from_cache_file(self) -> Optional[set]:
        """从本地缓存文件加载交易日历"""
        try:
            if os.path.exists(self._cache_file):
                with open(self._cache_file, 'r') as f:
                    data = json.load(f)
                    dates = [datetime.strptime(d, '%Y-%m-%d').date()
                            for d in data.get('trading_days', [])]
                    if dates:
                        return set(dates)
        except Exception as e:
            logger.debug(f"读取交易日历缓存失败: {e}")
        return None

    def _save_to_cache_file(self, trading_days: set):
        """保存交易日历到本地缓存"""
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            with open(self._cache_file, 'w') as f:
                json.dump({
                    'trading_days': sorted([d.strftime('%Y-%m-%d') for d in trading_days]),
                    'updated': date.today().strftime('%Y-%m-%d'),
                }, f)
        except Exception as e:
            logger.warning(f"保存交易日历缓存失败: {e}")

    def _load(self):
        """加载交易日历（优先级：本地缓存 > akshare API > 内置回退）"""
        if self._cache is not None:
            return

        # 1. 尝试本地缓存
        cached = self._load_from_cache_file()
        if cached:
            logger.info(f"从本地缓存加载了 {len(cached)} 个交易日")
            self._cache = cached
            return

        # 2. 尝试 akshare API
        try:
            import akshare as ak
            import pandas as pd
            df = ak.tool_trade_date_hist_sina()
            trading_days = set(
                pd.to_datetime(df['trade_date']).dt.date.tolist())
            logger.info(f"从 akshare 加载了 {len(trading_days)} 个交易日")
            self._cache = trading_days
            self._save_to_cache_file(trading_days)
            return
        except Exception as e:
            logger.warning(f"akshare 交易日历加载失败: {e}，使用内置回退列表")

        # 3. 内置回退：生成近5年的所有工作日，扣除已知节假日
        import pandas as pd
        today = date.today()
        all_dates = pd.date_range(
            start=today - timedelta(days=365 * 3),
            end=today + timedelta(days=365),
            freq='B'  # business day (Mon-Fri)
        )
        trading_days = set(d.date() for d in all_dates)
        trading_days -= _BUILTIN_HOLIDAYS_2026

        logger.info(f"使用内置回退列表：{len(trading_days)} 个交易日")
        self._cache = trading_days

    def is_trading_day(self, d: date = None) -> bool:
        """判断是否为交易日"""
        if d is None:
            d = date.today()
        self._load()
        return d in self._cache

    def prev_trading_day(self, d: date = None, offset: int = 1) -> date:
        """获取前 N 个交易日（使用真实交易日历）

        Args:
            d: 参考日期（默认今天）
            offset: 往前数几个交易日（默认 1）
        """
        if d is None:
            d = date.today()
        self._load()

        if not self._cache:
            # 回退到工作日判断
            count = 0
            while count < offset:
                d = d - timedelta(days=1)
                if d.weekday() < 5:
                    count += 1
            return d

        # 使用真实交易日历
        count = 0
        max_lookback = 365  # 最多往前找一年
        while count < offset and max_lookback > 0:
            d = d - timedelta(days=1)
            max_lookback -= 1
            if d in self._cache:
                count += 1

        if count < offset:
            logger.warning(f"未找到足够的交易日（需要{offset}个，找到{count}个）")

        return d

    def next_trading_day(self, d: date = None, offset: int = 1) -> date:
        """获取后 N 个交易日"""
        if d is None:
            d = date.today()
        self._load()

        if not self._cache:
            count = 0
            while count < offset:
                d = d + timedelta(days=1)
                if d.weekday() < 5:
                    count += 1
            return d

        count = 0
        max_lookforward = 365
        while count < offset and max_lookforward > 0:
            d = d + timedelta(days=1)
            max_lookforward -= 1
            if d in self._cache:
                count += 1

        return d

    def trading_days_between(self, start: date, end: date) -> int:
        """计算两个日期之间的交易日数量"""
        self._load()
        if not self._cache:
            days = pd.date_range(start, end, freq='B')
            return len(days)

        count = 0
        d = start
        while d <= end:
            if d in self._cache:
                count += 1
            d = d + timedelta(days=1)
        return count
