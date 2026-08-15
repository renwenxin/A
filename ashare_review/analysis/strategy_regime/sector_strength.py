"""板块共振强度 — 共涨停聚类（无外部板块映射，纯 TDX 因果）

背景: 离线无可靠板块映射（industry_map 只覆盖38%，东方财富接口不通）。
方案: 用"过去常一起涨停的股票"作为板块代理——
  一只股票破板时，统计它过去 60 日内常共涨停(≥2次)的"同伴"当天是否也在涨停；
  同伴数≥2 = 板块共振（板块活跃），≥5 = 涨停潮级（板块爆发）。
这最贴近战法"选股先看板块，只做核心，坚决不碰杂毛"。

因果性: 同伴关系用 回测开始前一年 的共涨停统计构建（完全无未来函数）；
       当日板块强度只统计当天实际涨停的同伴。
"""
import os
import json
from datetime import date, timedelta
from collections import defaultdict

from ...data.tdx_reader import TdxReader
from . import causal_universe as cu


class SectorStrength:
    """共涨停板块强度"""

    def __init__(self, uni: cu.CausalUniverse, pre_start: date, pre_end: date,
                 min_co: int = 2, cache_path: str = None):
        """构建同伴关系。pre_start~pre_end = 回测前一年（构建期，须完全在回测之前）。"""
        self.uni = uni
        self.min_co = min_co
        self.partners: dict = {}   # code -> set(partner codes)
        self._day_zt: dict = {}    # date -> set(codes) 当日涨停（缓存）

        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    self.partners = json.load(f)
                return
            except Exception:
                pass

        self._build(pre_start, pre_end)
        if cache_path:
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(self.partners, f)
            except Exception:
                pass

    def _build(self, pre_start: date, pre_end: date):
        # 回测前一年内 每个涨停日 → 该日涨停的 codes
        day_codes = defaultdict(set)
        for code in self.uni.codes:
            for d in self.uni.limit_up_dates(code):
                if pre_start <= d <= pre_end:
                    day_codes[d].add(code)

        # 统计 两两共涨停次数（仅在 pre 窗口内）
        co = defaultdict(int)
        for d, codes in day_codes.items():
            lst = sorted(codes)
            for i in range(len(lst)):
                for j in range(i + 1, len(lst)):
                    co[(lst[i], lst[j])] += 1

        # 构建同伴集合（共涨停 ≥ min_co 次）
        adj = defaultdict(set)
        for (a, b), cnt in co.items():
            if cnt >= self.min_co:
                adj[a].add(b)
                adj[b].add(a)
        self.partners = {code: sorted(ps) for code, ps in adj.items()}
        n_has = len(self.partners)
        n_total = len(self.uni.codes)
        print(f'[板块共振] 同伴关系: {sum(len(v) for v in self.partners.values())} 对, '
              f'{n_has}/{n_total} 只有同伴')

    def _today_zt(self, d: date) -> set:
        if d not in self._day_zt:
            self._day_zt[d] = {c for c in self.uni.codes if d in self.uni.limit_up_dates(c)}
        return self._day_zt[d]

    def strength(self, code: str, d: date) -> int:
        """code 在日期 d 的板块强度 = 当天涨停的同伴数。无同伴记录返回 0（视为无板块）。"""
        ps = self.partners.get(code)
        if not ps:
            return 0
        today = self._today_zt(d)
        return sum(1 for p in ps if p in today)

    def resonance(self, code: str, d: date, threshold: int = 2) -> bool:
        """是否板块共振（板块强度 ≥ threshold）。无同伴 → False（不碰杂毛）。"""
        ps = self.partners.get(code)
        if not ps:
            return False
        today = self._today_zt(d)
        cnt = sum(1 for p in ps if p in today)
        return cnt >= threshold
