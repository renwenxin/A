"""筛选器基类"""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from ..data.models import ScreeningResult
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher

class BaseScreener(ABC):
    def __init__(self, tdx: TdxReader = None, ak_fetcher: AkshareFetcher = None):
        self.tdx = tdx or TdxReader()
        self.ak = ak_fetcher or AkshareFetcher()
        self._name_map: Dict[str, str] = {}
        self._name_map_loaded = False

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

    @abstractmethod
    def screen(self, **kwargs) -> List[ScreeningResult]:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    def _score(self, conditions_met: int, total_conditions: int) -> float:
        return round(conditions_met / total_conditions * 100, 1)
