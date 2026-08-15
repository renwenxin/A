"""核心分析：资金验证 + 龙头/潜力分层。"""
import json, os
from typing import Dict, List, Optional

# 分层阈值（可调整）
LEADER_PCT = 7.0          # 龙头：涨幅 >= 7%
POTENTIAL_PCT_MAX = 3.0   # 潜力：涨幅 <= 3%
POTENTIAL_VOL_RATIO = 1.5 # 且 量比 >= 1.5
ZT_PCT = 9.5              # 主板涨停阈值

_NAME_MAP_CACHE = None


def _market(code: str) -> str:
    if code.startswith(('6', '9')):
        return 'sh'
    if code.startswith(('4', '8')):
        return 'bj'
    return 'sz'


def _lookup_name(code: str) -> str:
    """从名称缓存读股票名，缺失返回 code。"""
    global _NAME_MAP_CACHE
    if _NAME_MAP_CACHE is None:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'data', 'stock_name_map.json')
        try:
            _NAME_MAP_CACHE = json.load(open(p, encoding='utf-8'))
        except Exception:
            _NAME_MAP_CACHE = {}
    return _NAME_MAP_CACHE.get(code, code)


def _stock_signal(tdx, code: str, trade_date: str) -> Optional[Dict]:
    """读 TDX 最新一根K线，计算 涨幅/量比/涨停。"""
    try:
        df = tdx.read_daily(code, _market(code))
        if df is None or df.empty:
            return None
        last = df.iloc[-1]
        close = float(last['close'])
        if 'prev_close' in df.columns:
            prev = float(last['prev_close'])
        elif len(df) > 1:
            prev = float(df['close'].iloc[-2])
        else:
            prev = close
        vol = float(last['volume'])
        if 'vol_ma5' in df.columns:
            vol_ma5 = float(last['vol_ma5'])
        else:
            vol_ma5 = float(df['volume'].tail(5).mean()) if len(df) >= 5 else vol
        vol_ratio = round(vol / vol_ma5, 2) if vol_ma5 > 0 else 0.0
        pct = round((close / prev - 1) * 100, 2) if prev > 0 else 0.0
        is_zt = pct >= ZT_PCT - 0.1
        return {'code': code, 'pct': pct, 'vol_ratio': vol_ratio, 'is_zt': is_zt,
                'close': round(close, 2)}
    except Exception:
        return None


def _classify(signals: Dict[str, Dict]) -> tuple:
    """分层：龙头（涨幅>=7% 或涨停）+ 潜力（0<涨幅<=3% 且量比>=1.5）。"""
    sigs = [s for s in signals.values() if s]
    leaders = [s for s in sigs if s['is_zt'] or s['pct'] >= LEADER_PCT]
    leaders.sort(key=lambda s: (s['is_zt'], s['pct'], s['vol_ratio']), reverse=True)
    potentials = [s for s in sigs if 0 < s['pct'] <= POTENTIAL_PCT_MAX and s['vol_ratio'] >= POTENTIAL_VOL_RATIO]
    potentials.sort(key=lambda s: s['vol_ratio'], reverse=True)
    return leaders[:3], potentials[:5]


def _lhb_hits(codes: set, lhb_list: List[Dict]) -> List[Dict]:
    hits = [x for x in (lhb_list or []) if str(x.get('code', '')) in codes]
    return [{'code': x['code'], 'name': x.get('name', ''),
             'net_buy': x.get('net_amount', 0), 'type': x.get('type', '')} for x in hits]


def analyze_event(theme, description: str, tdx, fetcher, trade_date: str) -> Dict:
    """分析单个主题事件：产业链展开 + 个股信号 + 分层 + 龙虎榜。"""
    from .chain import resolve_node_stocks

    chains = []
    all_codes = set()
    for node in theme.chain_nodes:
        codes, source = resolve_node_stocks(node, fetcher)
        all_codes |= set(codes)
        chains.append({'node': node.node, 'codes': codes, 'source': source})

    signals = {}
    for code in sorted(all_codes):
        sig = _stock_signal(tdx, code, trade_date)
        if sig:
            sig['name'] = _lookup_name(code)
            signals[code] = sig

    leaders, potentials = _classify(signals)

    lhb = []
    if fetcher is not None:
        try:
            lhb = _lhb_hits(all_codes, fetcher.get_lhb(trade_date))
        except Exception:
            lhb = []

    return {
        'theme_id': theme.id, 'theme': theme.name, 'description': description,
        'chains': chains,
        'leaders': leaders, 'potentials': potentials,
        'lhb': lhb, 'next_day_notes': '',
    }
