"""预测台账 — 编排层

record_day:         把复盘报告中的三类预测写入台账（幂等）
validate_pending:   自动验证所有未验证记录（tdx 本地行情 + ak 涨停池，失败降级）
migrate_picks_history: 一次性追溯导入 picks_history.json 的历史精选
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..utils.calendar import TradingCalendar
from .store import LedgerStore
from .validate import grade_pick, grade_cycle, grade_auction, hit_for

DB_PATH = os.environ.get(
    'LEDGER_DB',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'data', 'prediction_ledger.db'))
PICKS_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'picks_history.json')


def _market_of(code: str) -> str:
    if str(code).startswith(('8', '4', '92')):
        return 'bj'
    return 'sh' if str(code).startswith('6') else 'sz'


def _zt_limit_pct(code: str) -> float:
    if str(code).startswith(('30', '68')):
        return 19.6
    if str(code).startswith(('8', '4', '92')):
        return 29.4
    return 9.8


def _next_trade_ymd(calendar: TradingCalendar, trade_date: str) -> Optional[str]:
    try:
        d = datetime.strptime(trade_date, '%Y%m%d').date()
    except ValueError:
        return None
    n = calendar.next_trading_day(d, offset=1)
    return n.strftime('%Y%m%d') if n else None


def record_day(report: Optional[Dict], trade_date: str, db_path: Optional[str] = None) -> int:
    """把报告中的三类预测写入台账。返回插入行数。幂等。"""
    db_path = db_path or DB_PATH
    if not report or report.get('error'):
        return 0
    rows: List[Dict] = []

    for p in (report.get('sentiment') or {}).get('picks', []) or []:
        rows.append({
            'pred_date': trade_date, 'pred_type': 'picks',
            'item_key': str(p.get('code', '')), 'item_name': p.get('name', ''),
            'direction': None, 'score': p.get('score'),
            'detail': json.dumps({'reasons': p.get('reasons', [])}, ensure_ascii=False),
        })

    cycle = report.get('cycle') or {}
    if cycle.get('stage'):
        rows.append({
            'pred_date': trade_date, 'pred_type': 'cycle', 'item_key': 'daily',
            'item_name': cycle['stage'], 'direction': cycle.get('next_bias'),
            'score': None,
            'detail': json.dumps({
                'stage': cycle['stage'],
                'stage_desc': cycle.get('stage_desc', ''),
                'total_zt': (cycle.get('metrics') or {}).get('total_zt', 0),
            }, ensure_ascii=False),
        })

    auction = report.get('auction_forecast') or {}
    if auction.get('forecast'):
        rows.append({
            'pred_date': trade_date, 'pred_type': 'auction', 'item_key': 'daily',
            'item_name': auction['forecast'], 'direction': auction.get('direction'),
            'score': None,
            'detail': json.dumps({
                'forecast': auction['forecast'],
                'forecast_desc': auction.get('forecast_desc', ''),
                'pool_codes': report.get('limit_up_codes', []),
            }, ensure_ascii=False),
        })

    return LedgerStore(db_path).upsert_predictions(rows)


def _row_position(df, target) -> Optional[int]:
    """返回 target(date) 在日线 DataFrame 中的位置；无该日或为首行返回 None。"""
    if df is None or df.empty:
        return None
    df = df.reset_index(drop=True)
    mask = df['trade_date'] == target
    if not mask.any():
        return None
    pos = int(mask.idxmax())
    return None if pos == 0 else pos


def _pick_actual(tdx, code: str, zt_codes: set, next_date: str) -> Tuple[Optional[str], Optional[int]]:
    """验证单只精选：定位 next_date 的日线 + 涨停集合判定。返回 (actual, hit)。"""
    try:
        df = tdx.read_daily(code, _market_of(code))
        target = datetime.strptime(next_date, '%Y%m%d').date()
        pos = _row_position(df, target)
        if pos is None:
            return None, None
        prev_close = float(df.iloc[pos - 1]['close'])
        today_close = float(df.iloc[pos]['close'])
        today_chg = (today_close - prev_close) / prev_close * 100 if prev_close else 0.0
    except Exception:
        return None, None
    is_zt = str(code) in zt_codes or today_chg >= _zt_limit_pct(code)
    actual = grade_pick(today_chg, is_zt)
    return actual, hit_for('picks', None, actual)


def _auction_actual(tdx, codes: List[str], next_date: str) -> Optional[str]:
    """当日涨停池次日平均高开幅度分级。数据不可用返回 None。"""
    gaps = []
    target = datetime.strptime(next_date, '%Y%m%d').date()
    for code in codes:
        try:
            df = tdx.read_daily(code, _market_of(code))
            pos = _row_position(df, target)
            if pos is None:
                continue
            prev_close = float(df.iloc[pos - 1]['close'])
            open_price = float(df.iloc[pos]['open'])
            if prev_close:
                gaps.append((open_price / prev_close - 1) * 100)
        except Exception:
            continue
    if not gaps:
        return None
    return grade_auction(sum(gaps) / len(gaps))


def validate_pending(tdx, ak, calendar: Optional[TradingCalendar] = None,
                     db_path: Optional[str] = None) -> int:
    """验证所有未验证记录，返回成功验证条数。单条失败跳过，不中断。"""
    db_path = db_path or DB_PATH
    calendar = calendar or TradingCalendar()
    store = LedgerStore(db_path)
    pending = store.get_unverified()
    by_date: Dict[str, List[dict]] = {}
    for row in pending:
        by_date.setdefault(row['pred_date'], []).append(row)

    validated = 0
    for pred_date, rows in by_date.items():
        next_date = _next_trade_ymd(calendar, pred_date)
        if not next_date:
            continue
        try:
            next_pool = ak.get_limit_up_pool(next_date) or []
            pool_ok = True
        except Exception:
            next_pool, pool_ok = [], False
        zt_codes = {str(lu.code) for lu in next_pool}
        cycle_ok = pool_ok and len(next_pool) > 0

        for row in rows:
            try:
                if row['pred_type'] == 'picks':
                    actual, hit = _pick_actual(tdx, row['item_key'], zt_codes, next_date)
                elif row['pred_type'] == 'cycle':
                    if not cycle_ok:
                        continue
                    detail = json.loads(row['detail']) if row['detail'] else {}
                    today_zt = int(detail.get('total_zt', 0))
                    actual = grade_cycle(today_zt, len(zt_codes))
                    hit = hit_for('cycle', row['direction'], actual)
                elif row['pred_type'] == 'auction':
                    detail = json.loads(row['detail']) if row['detail'] else {}
                    actual = _auction_actual(tdx, detail.get('pool_codes', []), next_date)
                    hit = hit_for('auction', row['direction'], actual)
                else:
                    continue
                if actual is not None and hit is not None:
                    store.mark_verified(row['id'], actual, hit)
                    validated += 1
            except Exception:
                continue
    return validated


def migrate_picks_history(tdx, ak, calendar: Optional[TradingCalendar] = None,
                          db_path: Optional[str] = None,
                          history_file: Optional[str] = None) -> int:
    """追溯导入 picks_history.json 的历史精选（含验证结果）。幂等，返回插入行数。"""
    db_path = db_path or DB_PATH
    history_file = history_file or PICKS_HISTORY_FILE
    if not os.path.exists(history_file):
        return 0
    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            history = json.load(f)
    except Exception:
        return 0
    calendar = calendar or TradingCalendar()
    store = LedgerStore(db_path)
    inserted = 0
    for pred_date, picks in history.items():
        next_date = _next_trade_ymd(calendar, pred_date)
        if not next_date:
            continue
        try:
            next_pool = ak.get_limit_up_pool(next_date) or []
        except Exception:
            next_pool = []
        zt_codes = {str(lu.code) for lu in next_pool}
        for p in picks or []:
            code = str(p.get('code', ''))
            if not code:
                continue
            actual, hit = _pick_actual(tdx, code, zt_codes, next_date)
            if actual is None:
                continue
            inserted += store.upsert_predictions([{
                'pred_date': pred_date, 'pred_type': 'picks',
                'item_key': code, 'item_name': p.get('name', ''),
                'direction': None, 'score': p.get('score'),
                'detail': json.dumps({'reasons': p.get('reasons', [])}, ensure_ascii=False),
            }])
            store.set_actual(pred_date, 'picks', code, actual, hit)
    return inserted
