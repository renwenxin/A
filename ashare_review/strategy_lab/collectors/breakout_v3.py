# ashare_review/strategy_lab/collectors/breakout_v3.py
"""启动突破 V3 · 明日突破预测 Top10（复用 tools/breakout_predict.py）"""
import json

from ..snapshot import enrich_stock


def collect_breakout_v3(trade_date, tdx, ak, report_payload=None) -> list:
    from ...tools.breakout_predict import BreakoutPredictor
    pred = BreakoutPredictor()
    pred.verify_pending()
    top = pred.predict(trade_date=trade_date, top_n=10, persist=False)
    items = []
    for i, p in enumerate(top, start=1):
        snap = enrich_stock(p['code'], tdx) or {}
        items.append({
            'code': p['code'], 'name': p['name'], 'rank': i,
            'sector': snap.get('sector', ''), 'regime': '',
            'amount_yi': snap.get('amount_yi'), 'vol_ratio': snap.get('vol_ratio'),
            'seal_ratio': snap.get('seal_ratio'),
            'open_px': snap.get('open_px'), 'high_px': snap.get('high_px'),
            'low_px': snap.get('low_px'), 'close_px': snap.get('close_px') or p.get('close'),
            'gap_pct': None, 'next_return': None,
            'extra': json.dumps({
                'score': p.get('score'), 'pressure': p.get('pressure'),
                'dist_pct': p.get('dist_pct'), 'reasons': p.get('reasons', [])},
                ensure_ascii=False),
        })
    return items
