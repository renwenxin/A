"""筛选器基类"""
import os, struct
from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from ..data.models import ScreeningResult
from ..data.tdx_reader import TdxReader, RECORD_SIZE
from ..data.akshare_fetcher import AkshareFetcher

# 各板块涨停阈值
_BOARD_LIMIT = {
    'main': 9.9,    # 主板 10%
    'gem': 19.9,    # 创业板 20%
    'star': 19.9,   # 科创板 20%
    'bj': 29.9,     # 北交所 30%
}

def _board_limit_threshold(code: str) -> float:
    """根据股票代码返回涨停阈值(%)"""
    if code.startswith(('300', '301')):
        return _BOARD_LIMIT['gem']
    if code.startswith('688'):
        return _BOARD_LIMIT['star']
    if code.startswith(('8', '4')):
        return _BOARD_LIMIT['bj']
    return _BOARD_LIMIT['main']


class BaseScreener(ABC):
    def __init__(self, tdx: TdxReader = None, ak_fetcher: AkshareFetcher = None):
        self.tdx = tdx or TdxReader()
        self.ak = ak_fetcher or AkshareFetcher()
        self._name_map: Dict[str, str] = {}
        self._name_map_loaded = False
        self._limit_up_cache: Dict[str, int] = {}  # code -> count

    def _get_name(self, code: str) -> str:
        """根据代码获取股票名称"""
        if not self._name_map_loaded:
            self._load_name_map()
        return self._name_map.get(code, '')

    def _load_name_map(self):
        """从akshare或TDX缓存加载代码→名称映射"""
        try:
            spot_df = self.ak.get_spot_df()
            for _, row in spot_df.iterrows():
                c = str(row.get('代码', '')).zfill(6)
                n = str(row.get('名称', ''))
                if c and n:
                    self._name_map[c] = n
        except Exception:
            pass
        self._name_map_loaded = True

    _auction_name_map: Dict[str, str] = {}
    _auction_name_loaded = False

    _sector_map: Dict[str, str] = {}
    _sector_map_loaded = False

    def _get_sector(self, code: str) -> str:
        """获取股票所属板块/行业（从涨停池的 board_type 字段）。

        涨停池的 board_type 实际存储的是 akshare 的 所属行业。
        """
        if not self._sector_map_loaded:
            try:
                limit_ups = self.ak.get_limit_up_pool()
                for lu in limit_ups:
                    if lu.board_type and lu.code:
                        self._sector_map[lu.code] = lu.board_type
            except Exception:
                pass
            self._sector_map_loaded = True
        return self._sector_map.get(code, '')

    def _get_name_from_auction(self, code: str) -> str:
        """从竞价数据缓存获取股票名称（spot_df 缺失时的回退方案）"""
        if not self._auction_name_loaded:
            try:
                auctions = self.ak.get_auction_data()
                for a in auctions:
                    if a.name:
                        self._auction_name_map[a.code] = a.name
            except Exception:
                pass
            self._auction_name_loaded = True
        return self._auction_name_map.get(code, '')

    def _count_limit_ups(self, code: str, lookback: int = 250) -> int:
        """统计近一年涨停次数（带缓存）

        读取 TDX .day 文件尾部 lookback 条日线，按板块阈值计数。
        首次计算后缓存，同一筛选器实例内复用。
        """
        if code in self._limit_up_cache:
            return self._limit_up_cache[code]
        threshold = _board_limit_threshold(code)
        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith('8') or code.startswith('4'):
            market = 'bj'
        fpath = os.path.join(self.tdx._market_dir(market), f'{market}{code}.day')
        if not os.path.exists(fpath):
            self._limit_up_cache[code] = 0
            return 0
        fsize = os.path.getsize(fpath)
        if fsize < RECORD_SIZE * 2:
            self._limit_up_cache[code] = 0
            return 0
        read_size = min(RECORD_SIZE * lookback, fsize)
        with open(fpath, 'rb') as f:
            f.seek(fsize - read_size)
            tail = f.read(read_size)
        records = len(tail) // RECORD_SIZE
        count = 0
        prev_close = None
        for i in range(records):
            offset = i * RECORD_SIZE
            close = struct.unpack('I', tail[offset+16:offset+20])[0] / 100.0
            if prev_close is not None and prev_close > 0:
                change_pct = (close - prev_close) / prev_close * 100
                if change_pct >= threshold:
                    count += 1
            prev_close = close
        self._limit_up_cache[code] = count
        return count

    @abstractmethod
    def screen(self, **kwargs) -> List[ScreeningResult]:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    def _score(self, conditions_met: int, total_conditions: int) -> float:
        return round(conditions_met / total_conditions * 100, 1)
