# ashare_review/strategy_lab/snapshot.py
"""单股 T 日快照 enrich — 三池统一口径

字段: amount_yi(成交额亿) / vol_ratio(当日量/前5日均量) / seal_ratio(封成比,仅涨停有)
      open_px/high_px/low_px/close_px / sector(所属行业)
数据源: tdx.read_daily 日线(amount 单位元→亿 /1e8) + industry_map.json(代码→行业)
       seal_ratio 由调用方从当日涨停池传入 {code: seal_ratio}（非涨停为 None）
"""
import json, os

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
_IND_FILE = os.path.join(_DATA_DIR, 'industry_map.json')

_industry_map = None


def _load_industry_map() -> dict:
    global _industry_map
    if _industry_map is None:
        try:
            with open(_IND_FILE, encoding='utf-8') as f:
                _industry_map = json.load(f)
        except Exception:
            _industry_map = {}
    return _industry_map


def enrich_stock(code: str, tdx, seal_map: dict = None) -> dict:
    """读单股 T 日(最后交易日)快照；失败返回 {}（调用方跳过该股）。"""
    code = str(code).zfill(6)
    market = 'sh' if code.startswith(('6', '9', '5')) else (
        'bj' if code.startswith(('4', '8', '920')) else 'sz')
    try:
        df = tdx.read_daily(code, market)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    last = df.iloc[-1]
    close = float(last['close'])
    amount = float(last['amount'] or 0)
    vol = float(last['volume'] or 0)
    vol_ma5_prev = float(df['volume'].iloc[-6:-1].mean()) if len(df) >= 6 else 0.0
    imap = _load_industry_map()
    seal = seal_map.get(code) if seal_map else None
    return {
        'amount_yi': round(amount / 1e8, 2),
        'vol_ratio': round(vol / vol_ma5_prev, 2) if vol_ma5_prev > 0 else None,
        'seal_ratio': float(seal) if seal is not None else None,
        'open_px': float(last['open']), 'high_px': float(last['high']),
        'low_px': float(last['low']), 'close_px': close,
        'sector': str(imap.get(code, '') or ''),
    }
