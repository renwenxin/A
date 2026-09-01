"""流通股本(CAPITAL, 手)映射 — 通达信主图公式 SWS 的 CAPITAL 数据源

公式原文:
    SWS:=DMA(EMA(CLOSE,20),MAX(1,100*(SUM(VOL,5)/(3*CAPITAL))))

CAPITAL = 流通股本(手)。数据源: clist 实时快照(流通市值/最新价/100)，
缓存到 data/cache/float_share_map.json（TTL 1 天）。网络失败时回退默认值。
"""
import json, os, time
from typing import Dict, Optional

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
_CACHE_FILE = os.path.join(_DATA_DIR, 'cache', 'float_share_map.json')
_TTL_SECONDS = 24 * 3600  # 1天

# 默认流通股本(手) ≈ 55亿股（旧代码固定 5.5e9 股 的近似值，无数据时兜底）
DEFAULT_FLOAT_SHARE_HANDS = 5.5e7

_map: Dict[str, float] = {}
_loaded = False


def load_capital_hands_map(force: bool = False) -> Dict[str, float]:
    """加载 {code: 流通股本(手)}。

    优先读本地缓存（1 天内），缺失/过期时从 akshare 快照拉取。
    拉取失败不抛异常，返回当前（可能为空）的 map。
    """
    global _map, _loaded
    if _loaded and not force:
        return _map

    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('updated') and time.time() - float(data['updated']) < _TTL_SECONDS:
                _map = data.get('map', {}) or {}
                _loaded = True
                return _map
        except Exception:
            pass

    try:
        from .akshare_fetcher import AkshareFetcher
        spot = AkshareFetcher().get_spot_df()
        m: Dict[str, float] = {}
        if spot is not None and not spot.empty:
            for _, row in spot.iterrows():
                code = str(row.get('代码', '')).strip().zfill(6)
                if len(code) != 6:
                    continue
                try:
                    mcap = float(row.get('流通市值', 0) or 0)
                    price = float(row.get('最新价', 0) or 0)
                except (ValueError, TypeError):
                    continue
                if mcap > 0 and price > 0:
                    hands = mcap / price / 100.0  # 元→股→手
                    m[code] = round(hands, 0)
        if m:
            os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
            with open(_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump({'updated': time.time(), 'map': m}, f, ensure_ascii=False)
            _map = m
    except Exception:
        pass
    _loaded = True
    return _map


def get_capital_hands(code: str, default: Optional[float] = None) -> float:
    """按代码取流通股本(手)；无数据/无缓存时返回默认值。"""
    if default is None:
        default = DEFAULT_FLOAT_SHARE_HANDS
    m = load_capital_hands_map()
    try:
        v = m.get(str(code).zfill(6))
    except Exception:
        v = None
    return float(v) if v and v > 0 else float(default)


if __name__ == '__main__':
    m = load_capital_hands_map(force=True)
    print(f'流通股本映射: {len(m)} 只')
    for c in ('600519', '000001', '300750'):
        print(f'  {c}: {get_capital_hands(c):.0f} 手')
