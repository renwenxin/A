# ashare_review/strategy_lab/collectors/boll_v9.py
"""超跌 BOLL V9 全量（调 analysis/boll_v9.scan_universe）"""
import json

from ..snapshot import enrich_stock


def collect_boll_v9(trade_date, tdx, ak, report_payload=None) -> list:
    from ...analysis.boll_v9 import scan_universe
    rows = scan_universe(trade_date=trade_date, tdx=tdx)
    items = []
    for i, r in enumerate(rows, start=1):     # rank 为展示位（YBT 降序）
        snap = enrich_stock(r['code'], tdx) or {}
        items.append({
            'code': r['code'], 'name': r['name'], 'rank': i,
            'sector': r.get('sector', '') or snap.get('sector', ''),
            'regime': '',
            'amount_yi': snap.get('amount_yi'), 'vol_ratio': snap.get('vol_ratio'),
            'seal_ratio': None,             # 超跌票非涨停, 天然无封成比
            'open_px': snap.get('open_px'), 'high_px': snap.get('high_px'),
            'low_px': snap.get('low_px'), 'close_px': snap.get('close_px') or r.get('close'),
            'gap_pct': None, 'next_return': None,
            'extra': json.dumps({
                'score': r.get('score'), 'dist_to_lb': r.get('dist_to_lb'),
                'rsi': r.get('rsi'), 'mfi': r.get('mfi')}, ensure_ascii=False),
        })
    return items
