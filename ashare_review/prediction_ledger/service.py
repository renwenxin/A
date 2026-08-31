"""预测台账 — 编排层

record_day:         把复盘报告中的三类预测写入台账（幂等）
validate_pending:   自动验证所有未验证记录（tdx 本地行情 + ak 涨停池，失败降级）
migrate_picks_history: 一次性追溯导入 picks_history.json 的历史精选
"""
import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from ..utils.calendar import TradingCalendar
from .store import LedgerStore
from .validate import (grade_pick, grade_cycle, grade_auction, hit_for,
                       hit_for_auction_verdict)

DB_PATH = os.environ.get(
    'LEDGER_DB',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'data', 'prediction_ledger.db'))
PICKS_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'picks_history.json')
CACHE_PERSIST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'cache', 'persist')


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
    cycle_detail = None
    if cycle.get('stage'):
        cycle_detail = {
            'stage': cycle['stage'],
            'stage_desc': cycle.get('stage_desc', ''),
            'total_zt': (cycle.get('metrics') or {}).get('total_zt', 0),
        }
        rows.append({
            'pred_date': trade_date, 'pred_type': 'cycle', 'item_key': 'daily',
            'item_name': cycle['stage'], 'direction': cycle.get('next_bias'),
            'score': None,
            'detail': json.dumps(cycle_detail, ensure_ascii=False),
        })

    # 大盘开盘涨跌家数：独立类型（当日观测，不入 cycle 明细）
    mkt = report.get('market_overview') or {}
    open_up = mkt.get('open_up_count', 0)
    open_down = mkt.get('open_down_count', 0)
    if open_up or open_down:
        rows.append({
            'pred_date': trade_date, 'pred_type': 'market_open', 'item_key': 'daily',
            'item_name': f"开盘涨{open_up}家/跌{open_down}家",
            'direction': None, 'score': None,
            'detail': json.dumps({
                'open_up': open_up, 'open_down': open_down,
                'open_flat': mkt.get('open_flat_count', 0),
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

    store = LedgerStore(db_path)
    inserted = store.upsert_predictions(rows)
    # 情绪周期行若已存在，刷新明细（重生成报告时带出开盘涨跌家数等最新值，
    # 不覆盖 actual/hit）
    if cycle_detail is not None:
        store.update_detail(trade_date, 'cycle', 'daily',
                            json.dumps(cycle_detail, ensure_ascii=False))
    return inserted


def record_pick_auctions(report: Optional[Dict], trade_date: str,
                         db_path: Optional[str] = None) -> int:
    """把昨日标的验证的逐票竞价判断写入台账（幂等，pred_date=昨日）。

    每只带竞价判定（抢筹/达标/观望）的昨日标的一条记录，写入即带当日
    actual/hit（竞价判断属当日观测，验证结果随报告同时可得）。
    返回写入条数。
    """
    db_path = db_path or DB_PATH
    if not report or report.get('error'):
        return 0
    try:
        dt = datetime.strptime(trade_date, '%Y%m%d').date()
    except ValueError:
        return 0
    calendar = TradingCalendar()
    prev = calendar.prev_trading_day(dt, offset=1)
    if prev is None:
        return 0
    prev_ymd = prev.strftime('%Y%m%d')
    store = LedgerStore(db_path)
    inserted = 0
    for yp in report.get('yesterday_picks') or []:
        auction = yp.get('auction') or {}
        verdict = auction.get('verdict')
        if not verdict:
            continue
        actual = grade_pick(yp.get('today_chg') or 0.0, bool(yp.get('is_zt_today')))
        hit = hit_for_auction_verdict(verdict, actual)
        vol = auction.get('vol_rule') or {}
        detail = {
            'auction_type': auction.get('type'),
            'open_pct': auction.get('open_pct'),
            'verdict_desc': vol.get('desc') or auction.get('desc'),
            'vol_0924': vol.get('vol_0924'),
            'vol_0925': vol.get('vol_0925'),
            'prev_max_minute_vol': vol.get('prev_max_minute_vol'),
        }
        n = store.upsert_predictions([{
            'pred_date': prev_ymd, 'pred_type': 'pick_auction',
            'item_key': str(yp.get('code', '')), 'item_name': yp.get('name', ''),
            'direction': verdict, 'score': None,
            'detail': json.dumps(detail, ensure_ascii=False),
        }])
        if n:
            store.set_actual(prev_ymd, 'pick_auction',
                             str(yp.get('code', '')), actual, hit)
            inserted += n
    return inserted


def backfill_pick_auctions_from_cache(db_path: Optional[str] = None,
                                      cache_dir: Optional[str] = None) -> int:
    """从持久复盘报告缓存回填 pick_auction（幂等）。

    竞价判定入台账功能上线前生成的报告没有写 pick_auction 行；这里扫
    data/cache/persist/review_report_*.json，把已算好的昨日竞价判定补写。
    返回新增行数。单文件失败跳过，不中断。
    """
    import glob
    db_path = db_path or DB_PATH
    cache_dir = cache_dir or CACHE_PERSIST_DIR
    if not os.path.isdir(cache_dir):
        return 0
    inserted = 0
    for path in sorted(glob.glob(os.path.join(cache_dir, 'review_report_*.json'))):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            payload = data.get('_payload') if isinstance(data, dict) else data
            if not payload or payload.get('error'):
                continue
            td = str(payload.get('date', '')).replace('-', '')
            if not td or td == 'N/A':
                continue
            inserted += record_pick_auctions(payload, td, db_path)
        except Exception:
            continue
    return inserted


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


def _win_metrics(df, pos: Optional[int]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """三个卖出口径胜负（相对首日开盘）：返回 (win, win_open, win_high)。

    首日 = pos 对应的验证日，次日 = 其下一交易日。
      win        = 次日收盘 > 首日开盘
      win_open   = 次日开盘 > 首日开盘
      win_high   = 次日高点 > 首日开盘（缺 high 列 → None）
    次日未到 / 数据异常返回 (None, None, None)。
    """
    if df is None or pos is None or pos + 1 >= len(df):
        return None, None, None
    try:
        day1_open = float(df.iloc[pos]['open'])
        day2 = df.iloc[pos + 1]
        day2_open = float(day2['open'])
        day2_close = float(day2['close'])
        if not day1_open or not day2_open or not day2_close:
            return None, None, None
        win = 1 if day2_close > day1_open else 0
        win_open = 1 if day2_open > day1_open else 0
        if 'high' not in df.columns:
            win_high = None
        else:
            day2_high = float(day2['high'])
            win_high = (1 if day2_high > day1_open else 0) if day2_high else None
        return win, win_open, win_high
    except Exception:
        return None, None, None


def _pick_wins(tdx, code: str, next_date: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """次日三口径胜负：(收盘, 开盘, 高点) vs 首日开盘。次日未出返回 (None,None,None)。"""
    try:
        df = tdx.read_daily(code, _market_of(code))
        target = datetime.strptime(next_date, '%Y%m%d').date()
        pos = _row_position(df, target)
        return _win_metrics(df, pos)
    except Exception:
        return None, None, None


def _pick_actual(tdx, code: str, zt_codes: set, next_date: str) -> Tuple[Optional[str], Optional[int], Optional[int], Optional[int], Optional[int]]:
    """验证单只精选：定位 next_date 的日线 + 涨停集合判定。

    返回 (actual, hit, win, win_open, win_high)。
    win 三口径 = 次日收盘/开盘/高点 vs 首日开盘（首日=next_date 验证日），
    次日未到返回 None（不影响 actual/hit）。
    """
    win = win_open = win_high = None
    try:
        df = tdx.read_daily(code, _market_of(code))
        target = datetime.strptime(next_date, '%Y%m%d').date()
        pos = _row_position(df, target)
        if pos is None:
            return None, None, None, None, None
        prev_close = float(df.iloc[pos - 1]['close'])
        today_close = float(df.iloc[pos]['close'])
        today_chg = (today_close - prev_close) / prev_close * 100 if prev_close else 0.0
        win, win_open, win_high = _win_metrics(df, pos)
    except Exception:
        return None, None, None, None, None
    is_zt = str(code) in zt_codes or today_chg >= _zt_limit_pct(code)
    actual = grade_pick(today_chg, is_zt)
    return actual, hit_for('picks', None, actual), win, win_open, win_high


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
                win = win_open = win_high = None
                if row['pred_type'] == 'picks':
                    actual, hit, win, win_open, win_high = _pick_actual(
                        tdx, row['item_key'], zt_codes, next_date)
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
                    store.mark_verified(row['id'], actual, hit, win, win_open, win_high)
                    validated += 1
            except Exception:
                continue
    # 补写已验证精选的次日收盘胜负（第二天数据已可得时）
    try:
        refresh_pick_wins(tdx, calendar=calendar, db_path=db_path)
    except Exception:
        pass
    return validated


def refresh_pick_wins(tdx, calendar: Optional[TradingCalendar] = None,
                      db_path: Optional[str] = None) -> int:
    """为已验证但缺 win 三口径、且次日收盘已可得的精选补写 win。返回补写条数。

    胜负需第二天（验证日的下一交易日）收盘/开盘/高点，验证当天往往还没出，
    需次日再跑一次补写；旧数据只有 win 无 win_open/win_high 时也会在此补全。幂等。
    """
    db_path = db_path or DB_PATH
    calendar = calendar or TradingCalendar()
    store = LedgerStore(db_path)
    updated = 0
    for r in store.rows(365):
        if r['pred_type'] != 'picks' or r['hit'] is None or r.get('win_open') is not None:
            continue
        next_date = _next_trade_ymd(calendar, r['pred_date'])
        if not next_date:
            continue
        win, win_open, win_high = _pick_wins(tdx, r['item_key'], next_date)
        if win is None:
            continue
        store.set_actual(r['pred_date'], 'picks', r['item_key'],
                         r['actual'], r['hit'], win, win_open, win_high)
        updated += 1
    return updated


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
    except Exception as e:
        logger.warning('预测台账历史迁移：读取 %s 失败（%s），跳过迁移', history_file, e)
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
            actual, hit, win, win_open, win_high = _pick_actual(tdx, code, zt_codes, next_date)
            if actual is None:
                continue
            inserted += store.upsert_predictions([{
                'pred_date': pred_date, 'pred_type': 'picks',
                'item_key': code, 'item_name': p.get('name', ''),
                'direction': None, 'score': p.get('score'),
                'detail': json.dumps({'reasons': p.get('reasons', [])}, ensure_ascii=False),
            }])
            store.set_actual(pred_date, 'picks', code, actual, hit, win, win_open, win_high)
    return inserted
