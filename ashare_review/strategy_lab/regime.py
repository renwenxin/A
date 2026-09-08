# ashare_review/strategy_lab/regime.py
"""行情分类（6 档 regime）当日取数。

strategy_regime 的 market_state.csv 是回测固定窗口缓存（截至 2026-08-07）。
实验室要对任意交易日取 6 档 regime：以目标日为 end 按需重建 state_df 并 compute_regime。

重建成本：全市场 .day 尾部广度扫描（一次性数十秒级，带磁盘缓存）。
缓存：data/strategy_lab/market_state.csv —— 与 strategy_regime 的缓存文件隔离，
避免覆盖回测用缓存、破坏回测口径。
"""
import os
from datetime import date

import pandas as pd

from ..analysis.strategy_regime import market_state
from ..analysis.strategy_regime import regime as regime_mod

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
_LAB_DIR = os.path.join(_DATA_DIR, 'strategy_lab')
_STATE_CACHE = os.path.join(_LAB_DIR, 'market_state.csv')

# 回看起点：比 strategy_regime 的 2025-08-08 略早，保证 MA60/缠论前视充分
_START = date(2025, 7, 1)


def get_regime_for(trade_date: str) -> str:
    """返回 trade_date（YYYY-MM-DD）当日 6 档 regime；数据不可达返回 ''。"""
    target = _parse_date(trade_date)
    if target is None:
        return ''
    df = _load_state_through(target)
    if df is None or df.empty:
        return ''
    # 兼容两种 date 表示：真实 load_state 已归一化为 datetime.date；
    # mock/字符串源则先解析再比，保证同一语义。
    row = df[pd.to_datetime(df['date']).dt.date == target]
    if row.empty:
        return ''
    val = row.iloc[0].get('regime')
    return str(val) if val is not None and not pd.isna(val) else ''


def _parse_date(d_str: str):
    try:
        return date.fromisoformat(str(d_str))
    except Exception:
        return None


def _load_state_through(target: date) -> pd.DataFrame:
    """读/建缓存 state_df（含 regime 列），保证缓存覆盖到 target。

    两条路径：
    1. 缓存文件存在且最后日期 >= target → 直接读 CSV（快路径，同日二次调用秒回）；
    2. 否则 → market_state.load_state(_START, target, cache_path, rebuild=True)
       从 _START 全量重建并覆盖写盘。

    必须传 rebuild=True：真实 load_state（market_state.py:23-38）在 cache_path
    存在且非 rebuild 时会直接读旧 CSV 返回、无视 start/end —— 不传 rebuild 则
    跨天（缓存落后于 target）永远读旧文件，get_regime_for 恒返回 ''。
    起点固定为 _START 而非"缓存已有最新"：compute_regime 需要 MA60 与前 20 日
    动量（regime.py:25-27 pct_change(20)），从缓存截断起点重建会丢前视窗口。
    """
    os.makedirs(_LAB_DIR, exist_ok=True)
    df = None
    if os.path.exists(_STATE_CACHE):
        try:
            cached = pd.read_csv(_STATE_CACHE)
            cached['date'] = pd.to_datetime(cached['date']).dt.date
            if not cached.empty and cached['date'].max() >= target:
                df = cached
        except Exception:
            df = None
    if df is None:
        df = market_state.load_state(_START, target, cache_path=_STATE_CACHE, rebuild=True)
    return regime_mod.compute_regime(df)
