"""akshare数据获取 + SQLite缓存"""
import akshare as ak
import pandas as pd
import sqlite3
import json
from datetime import datetime, date
from typing import List, Optional
from .models import LimitUpInfo, AuctionInfo, LhbInfo, StockInfo

class AkshareFetcher:
    """A股行情数据获取器，带SQLite缓存减少重复请求"""

    def __init__(self, cache_db: str = 'ashare_review/cache.db'):
        self.cache_db = cache_db
        self._init_cache()

    def _init_cache(self):
        conn = sqlite3.connect(self.cache_db)
        conn.execute('''CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY, data TEXT, updated TIMESTAMP)''')
        conn.commit()
        conn.close()

    def _cache_get(self, key: str, ttl_minutes: int = 5) -> Optional[str]:
        conn = sqlite3.connect(self.cache_db)
        row = conn.execute('SELECT data, updated FROM cache WHERE key=?', (key,)).fetchone()
        conn.close()
        if row and (datetime.now() - datetime.fromisoformat(row[1])).seconds < ttl_minutes * 60:
            return row[0]
        return None

    def _cache_set(self, key: str, data: str):
        conn = sqlite3.connect(self.cache_db)
        conn.execute('INSERT OR REPLACE INTO cache VALUES (?, ?, ?)',
                     (key, data, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_spot_df(self) -> pd.DataFrame:
        """实时行情快照"""
        cache_key = 'spot_all'
        cached = self._cache_get(cache_key, ttl_minutes=2)
        if cached:
            return pd.read_json(cached)
        df = ak.stock_zh_a_spot_em()
        self._cache_set(cache_key, df.to_json())
        return df

    def get_limit_up_pool(self, trade_date: Optional[str] = None) -> List[LimitUpInfo]:
        """当日涨停板列表"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        cache_key = f'zt_pool_{trade_date}'
        cached = self._cache_get(cache_key, ttl_minutes=3)
        if cached:
            return [LimitUpInfo(**d) for d in json.loads(cached)]
        try:
            df = ak.stock_zt_pool_em(date=trade_date)
        except Exception:
            try:
                df = ak.stock_zt_pool_strong_em(date=trade_date)
            except Exception:
                return []
        results = []
        for _, row in df.iterrows():
            results.append(LimitUpInfo(
                code=str(row.get('代码', '')).zfill(6),
                name=str(row.get('名称', '')),
                limit_up_time=str(row.get('涨停时间', row.get('首次封板时间', ''))),
                seal_amount=float(row.get('封单额', row.get('封单资金', 0))) / 10000,
                turnover=float(row.get('成交额', 0)) / 10000,
                float_market_cap=float(row.get('流通市值', 0)) / 1e8,
                consecutive=int(row.get('连板数', 1)),
                is_first=row.get('连板数', 2) == 1,
                is_seal='是' in str(row.get('是否炸板', row.get('封板情况', '是'))),
                is_broken='炸板' in str(row.get('涨停统计', row.get('封板情况', ''))),
                board_type=str(row.get('涨停类型', row.get('板型', '')))
            ))
        if results:
            self._cache_set(cache_key, json.dumps([{
                'code': r.code, 'name': r.name, 'limit_up_time': r.limit_up_time,
                'seal_amount': r.seal_amount, 'turnover': r.turnover,
                'float_market_cap': r.float_market_cap, 'consecutive': r.consecutive,
                'is_first': r.is_first, 'is_seal': r.is_seal, 'is_broken': r.is_broken,
                'board_type': r.board_type
            } for r in results]))
        return results

    def get_auction_data(self) -> List[AuctionInfo]:
        """集合竞价数据"""
        cache_key = f'auction_{datetime.now().strftime("%Y%m%d")}'
        cached = self._cache_get(cache_key, ttl_minutes=30)
        if cached:
            return [AuctionInfo(**d) for d in json.loads(cached)]
        try:
            df = ak.stock_zh_a_auction_em()
        except Exception:
            return []
        results = []
        for _, row in df.iterrows():
            results.append(AuctionInfo(
                code=str(row.get('代码', '')).zfill(6),
                name=str(row.get('名称', '')),
                auction_volume=int(row.get('竞价成交量', 0)),
                auction_amount=float(row.get('竞价成交额', 0)),
                auction_price=float(row.get('竞价价格', 0)),
                open_change_pct=float(row.get('开盘涨幅', row.get('开盘涨跌幅', 0))),
                preclose_volume=0
            ))
        if results:
            self._cache_set(cache_key, json.dumps([{
                'code': r.code, 'name': r.name, 'auction_volume': r.auction_volume,
                'auction_amount': r.auction_amount, 'auction_price': r.auction_price,
                'open_change_pct': r.open_change_pct, 'preclose_volume': r.preclose_volume
            } for r in results]))
        return results

    def get_lhb(self, trade_date: Optional[str] = None) -> List[LhbInfo]:
        """龙虎榜"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        cache_key = f'lhb_{trade_date}'
        cached = self._cache_get(cache_key, ttl_minutes=120)
        if cached:
            return [LhbInfo(**d) for d in json.loads(cached)]
        try:
            df = ak.stock_lhb_detail_em(date=trade_date)
        except Exception:
            return []
        results = []
        for _, row in df.iterrows():
            results.append(LhbInfo(
                code=str(row.get('代码', '')).zfill(6),
                name=str(row.get('名称', '')),
                trade_date=date.today(),
                reason=str(row.get('上榜原因', '')),
                buy_amount=float(row.get('买入总计', 0)) / 10000,
                sell_amount=float(row.get('卖出总计', 0)) / 10000,
                net_amount=float(row.get('净买额', 0)) / 10000,
                seats=[]
            ))
        if results:
            self._cache_set(cache_key, json.dumps([{
                'code': r.code, 'name': r.name, 'trade_date': str(r.trade_date),
                'reason': r.reason, 'buy_amount': r.buy_amount,
                'sell_amount': r.sell_amount, 'net_amount': r.net_amount, 'seats': r.seats
            } for r in results]))
        return results

    def get_concept_boards(self) -> pd.DataFrame:
        """概念板块行情"""
        cache_key = 'concept_boards'
        cached = self._cache_get(cache_key, ttl_minutes=5)
        if cached:
            return pd.read_json(cached)
        try:
            df = ak.stock_board_concept_name_em()
        except Exception:
            return pd.DataFrame()
        self._cache_set(cache_key, df.to_json())
        return df

    def get_industry_boards(self) -> pd.DataFrame:
        """行业板块行情"""
        cache_key = 'industry_boards'
        cached = self._cache_get(cache_key, ttl_minutes=5)
        if cached:
            return pd.read_json(cached)
        try:
            df = ak.stock_board_industry_name_em()
        except Exception:
            return pd.DataFrame()
        self._cache_set(cache_key, df.to_json())
        return df
