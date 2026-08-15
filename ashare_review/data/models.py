"""统一数据模型 — 屏蔽通达信和akshare的数据差异"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

@dataclass
class DailyBar:
    code: str
    name: str
    market: str          # 'sh' | 'sz' | 'bj'
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int          # 成交量(股)
    amount: float        # 成交额(元)

@dataclass
class StockInfo:
    code: str
    name: str
    market: str
    industry: str = ''
    concept_list: list = field(default_factory=list)
    float_market_cap: float = 0.0  # 流通市值(亿)
    close_price: float = 0.0
    limit_up_count_1y: int = 0   # 近一年涨停次数

@dataclass
class LimitUpInfo:
    code: str
    name: str
    limit_up_time: str       # 涨停时间 如 '09:35'
    seal_amount: float       # 封单额(万元)
    turnover: float          # 成交额(万元)
    float_market_cap: float  # 流通市值(亿)
    consecutive: int         # 连板数
    is_first: bool           # 是否首板
    is_seal: bool            # 是否封死
    is_broken: bool = False  # 是否炸板
    board_type: str = ''     # '一字板'|'T字板'|'换手板'
    close_price: float = 0.0 # 最新价/收盘价

@dataclass
class AuctionInfo:
    code: str
    name: str
    auction_volume: int     # 竞价成交量
    auction_amount: float   # 竞价成交额
    auction_price: float    # 竞价价格
    open_change_pct: float  # 开盘涨跌幅(%)
    preclose_volume: int    # 昨日爆量(最高量柱)
    vol_0924: int = 0       # 9:24分竞价量(股)
    vol_0925: int = 0       # 9:25分竞价量(股)

@dataclass
class LhbInfo:
    code: str
    name: str
    trade_date: date
    reason: str             # 上榜原因
    buy_amount: float       # 买入总额(万元)
    sell_amount: float      # 卖出总额(万元)
    net_amount: float       # 净买入(万元)
    seats: list = field(default_factory=list)

@dataclass
class ScreeningResult:
    code: str
    name: str
    strategy: str           # 策略名
    score: float            # 0-100
    reasons: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)
