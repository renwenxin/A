"""因果候选池 — 修复静态 limit_up_pool.json 的幸存者偏差/未来函数

问题: 原候选池 limit_up_pool.json 是 2026-08-07 一次性构建的静态快照，
     内含"全年涨停≥10"的股票。回测起点(2025-08)时 70% 的池内股票其实
     还没达到年涨停≥10 —— 早期交易它们 = 用了未来信息，虚增收益。

解决: 逐日因果判定"前期强势"(近 250 个交易日涨停≥10)。
     每天只统计截至当天的历史，无任何未来函数。
     股票在回测期间"曾达标的集合"称为 ever_eligible（因果宇宙超集）。

用法:
    uni = CausalUniverse(tdx, start, end)          # 构建（一次性全市场扫描，可缓存）
    uni.eligible(code, date_obj)                    # 该日该股是否前期强势
    uni.codes                                        # 回测期间曾达标的股票集合
"""
import os
import struct
import json
from datetime import date, timedelta

import numpy as np

from ...data.tdx_reader import TdxReader, RECORD_SIZE
from ...utils.calendar import TradingCalendar

LOOKBACK = 250        # 统计近 250 个交易日
MIN_COUNT = 10        # 年涨停≥10
READ_DAYS = 520       # 尾部读取记录数（250 前窗 + 245 回测 + 缓冲）


def _threshold(code: str) -> float:
    """涨停阈值(%)，与 base.py 一致"""
    if code.startswith(('300', '301')):
        return 19.9
    if code.startswith('688'):
        return 19.9
    if code.startswith(('8', '4')):
        return 29.9
    return 9.9


def _is_a_stock(code: str) -> bool:
    if not code or len(code) != 6:
        return False
    return code.startswith(('0', '3', '6')) and not code.startswith(('900', '200'))


class CausalUniverse:
    """因果候选池"""

    def __init__(self, tdx: TdxReader, start: date, end: date, cache_path: str = None):
        self.tdx = tdx
        self.start = start
        self.end = end
        self.codes: set = set()          # ever-eligible 集合
        self._elig: dict = {}            # code -> set(date) 该股达标的日期
        self._lup_dates: dict = {}       # code -> set(date) 该股全部涨停日期（扫描窗口内）
        self._loaded_from_cache = False

        if cache_path and os.path.exists(cache_path):
            try:
                self._load(cache_path)
                self._loaded_from_cache = True
                return
            except Exception:
                pass
        self._build(start, end)
        if cache_path:
            try:
                self._save(cache_path)
            except Exception:
                pass

    # ── 构建 ──
    def _build(self, start: date, end: date):
        cal = TradingCalendar()
        start_int = int(start.strftime('%Y%m%d'))
        end_int = int(end.strftime('%Y%m%d'))
        # 需要覆盖的回测日期
        need_dates = set()
        d = start
        while d <= end:
            if cal.is_trading_day(d):
                need_dates.add(int(d.strftime('%Y%m%d')))
            d += timedelta(days=1)

        rec_dtype = np.dtype([
            ('date', '<u4'), ('open', '<u4'), ('high', '<u4'),
            ('low', '<u4'), ('close', '<u4'), ('amount', '<f4'),
            ('volume', '<u4'), ('rsv', '<u4'),
        ])

        n_stocks = 0
        for mkt in ['sh', 'sz', 'bj']:
            mdir = self.tdx._market_dir(mkt)
            if not os.path.isdir(mdir):
                continue
            for fn in os.listdir(mdir):
                if not fn.endswith('.day'):
                    continue
                code = fn[2:8]
                if not _is_a_stock(code):
                    continue
                fpath = os.path.join(mdir, fn)
                try:
                    fsize = os.path.getsize(fpath)
                    if fsize < RECORD_SIZE * (LOOKBACK + 20):
                        continue
                    with open(fpath, 'rb') as f:
                        f.seek(max(0, fsize - RECORD_SIZE * READ_DAYS))
                        data = f.read()
                    nrec = len(data) // RECORD_SIZE
                    if nrec < LOOKBACK + 20:
                        continue
                    arr = np.frombuffer(data, dtype=rec_dtype, count=nrec)
                    dts = arr['date']
                    closes = arr['close'].astype(float) / 100.0

                    # 计算涨跌幅 → 涨停标记 → 滚动250计数(shift1, 只看此前)
                    prev = np.roll(closes, 1)
                    prev[0] = np.nan
                    with np.errstate(divide='ignore', invalid='ignore'):
                        chg = (closes - prev) / prev * 100.0
                    limit_up = np.where(chg >= _threshold(code), 1, 0)
                    # 滚动 250 求和（含当天的 250 窗口，再 shift 1 = 仅此前 250 日）
                    trailing = _rolling_sum(limit_up, LOOKBACK)
                    trailing = np.roll(trailing, 1)
                    trailing[0] = 0

                    # 只取回测窗口内的达标日期
                    elig_dates = set()
                    lup_dates = set()
                    for k in range(nrec):
                        dk = int(dts[k])
                        if limit_up[k] == 1:
                            ds = str(dk)
                            lup_dates.add(date(int(ds[:4]), int(ds[4:6]), int(ds[6:8])))
                        if start_int <= dk <= end_int and dk in need_dates and trailing[k] >= MIN_COUNT:
                            ds = str(dk)
                            elig_dates.add(date(int(ds[:4]), int(ds[4:6]), int(ds[6:8])))
                    if elig_dates:
                        self.codes.add(code)
                        self._elig[code] = elig_dates
                        self._lup_dates[code] = lup_dates
                        n_stocks += 1
                except (OSError, struct.error):
                    continue
        print(f'[因果候选池] ever-eligible 股票: {len(self.codes)} 只')

    def eligible(self, code: str, d: date) -> bool:
        """该股在日期 d 是否'前期强势'(近250交易日涨停≥10)"""
        return d in self._elig.get(code, set())

    def limit_up_dates(self, code: str) -> set:
        """该股扫描窗口内的全部涨停日期（用于共涨停板块聚类）"""
        return self._lup_dates.get(code, set())

    # ── 缓存 ──
    def _save(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            'elig': {code: [int(x.strftime('%Y%m%d')) for x in dates]
                     for code, dates in self._elig.items()},
            'lup': {code: [int(x.strftime('%Y%m%d')) for x in dates]
                    for code, dates in self._lup_dates.items()},
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f)

    def _load(self, path):
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        if isinstance(payload, dict) and 'elig' in payload:
            elig = payload['elig']
            lup = payload.get('lup', {})
        else:  # 兼容旧格式
            elig = payload
            lup = {}
        for code, dates in elig.items():
            self._elig[code] = {date(int(str(d)[:4]), int(str(d)[4:6]), int(str(d)[6:8])) for d in dates}
            self.codes.add(code)
        for code, dates in lup.items():
            self._lup_dates[code] = {date(int(str(d)[:4]), int(str(d)[4:6]), int(str(d)[6:8])) for d in dates}
        print(f'[因果候选池] 从缓存加载: {len(self.codes)} 只')


def _rolling_sum(arr: np.ndarray, window: int) -> np.ndarray:
    """滑动窗口求和（等价 pandas rolling(sum, window)）"""
    out = np.zeros_like(arr, dtype=np.int64)
    csum = np.cumsum(arr, dtype=np.int64)
    out[:window] = csum[:window]
    out[window:] = csum[window:] - csum[:-window]
    return out
