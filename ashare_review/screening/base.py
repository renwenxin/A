"""筛选器基类"""
import os, struct, threading
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


def _code_segment(code: str) -> str:
    """股票代码段分组：沪(6) / 深主板(0) / 创业板(3) / 其他(北交所等)

    逻辑哥接力战法"代码段跟随"用：市场晋级集中在哪个代码段就跟哪个——
    晋级都是 6 开头(沪)就都做 6 票，只有 0 开头(深)有强度就做 0 票。
    """
    if code.startswith('6'):
        return 'sh6'
    if code.startswith('0'):
        return 'sz0'
    if code.startswith('3'):
        return 'sz3'
    return 'other'


class BaseScreener(ABC):
    def __init__(self, tdx: TdxReader = None, ak_fetcher: AkshareFetcher = None):
        self.tdx = tdx or TdxReader()
        self.ak = ak_fetcher or AkshareFetcher()
        self._name_map: Dict[str, str] = {}
        self._name_map_loaded = False
        self._limit_up_cache: Dict[str, int] = {}  # code -> count

        # 实例级别缓存（修复类级别共享导致的多线程竞态）
        self._name_lock = threading.Lock()
        self._auction_name_map: Dict[str, str] = {}
        self._auction_name_loaded = False
        self._sector_map: Dict[str, str] = {}
        self._sector_map_loaded = False

    def _get_name(self, code: str) -> str:
        """根据代码获取股票名称（多源回退）"""
        if not self._name_map_loaded:
            self._load_name_map()
        name = self._name_map.get(code, '')
        # 回退到竞价数据
        if not name:
            name = self._get_name_from_auction(code)
        return name

    def _load_name_map(self):
        """从多个数据源加载代码→名称映射（线程安全）"""
        with self._name_lock:
            if self._name_map_loaded:
                return
            # 1. spot_df（行情快照，覆盖面最广）
            try:
                spot_df = self.ak.get_spot_df()
                for _, row in spot_df.iterrows():
                    c = str(row.get('代码', '')).zfill(6)
                    n = str(row.get('名称', ''))
                    if c and n:
                        self._name_map[c] = n
            except Exception:
                pass

            # 2. 涨停池（通常有名字，且包含最近活跃股票）
            try:
                limit_ups = self.ak.get_limit_up_pool()
                for lu in limit_ups:
                    if lu.code and lu.name:
                        if lu.code not in self._name_map or not self._name_map[lu.code]:
                            self._name_map[lu.code] = lu.name
            except Exception:
                pass

            self._name_map_loaded = True

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

    def segment_stats(self, limit_ups: list = None) -> dict:
        """统计涨停池晋级(连板≥2)票的代码段分布，判断市场主攻方向（代码段跟随）。

        逻辑哥接力战法：市场连板晋级集中在哪个代码段就跟哪个——
        晋级都是 6 开头(沪)就都做 6 票，只有 0 开头(深)有强度就做 0 票。
        晋级信号太弱(<3只晋级票)或主攻段落在北交所时返回 dominant=None，
        调用方跳过该加分（避免低信号日误伤）。

        Returns:
            {'dominant': 'sh6'|'sz0'|'sz3'|None, 'label': str,
             'counts': {...}, 'max_cons': {...}, 'total': int}
        """
        if limit_ups is None:
            try:
                limit_ups = self.ak.get_limit_up_pool()
            except Exception:
                limit_ups = []
        labels = {'sh6': '沪主板/科创(6)', 'sz0': '深主板(0)',
                  'sz3': '创业板(3)', 'other': '北交所/其他'}
        counts = {'sh6': 0, 'sz0': 0, 'sz3': 0, 'other': 0}
        max_cons = {'sh6': 0, 'sz0': 0, 'sz3': 0, 'other': 0}
        for lu in limit_ups:
            cons = getattr(lu, 'consecutive', 0) or 0
            if cons < 2:
                continue  # 只看晋级票
            seg = _code_segment(lu.code)
            counts[seg] += 1
            max_cons[seg] = max(max_cons[seg], cons)
        total = sum(counts.values())
        dominant = max(counts, key=lambda s: (counts[s], max_cons[s]))
        if total < 3 or counts[dominant] == 0 or dominant == 'other':
            dominant = None
        return {
            'dominant': dominant,
            'label': labels.get(dominant, ''),
            'counts': counts, 'max_cons': max_cons, 'total': total,
        }

    @abstractmethod
    def screen(self, **kwargs) -> List[ScreeningResult]:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    def _score(self, conditions_met: int, total_conditions: int) -> float:
        return round(conditions_met / total_conditions * 100, 1)
