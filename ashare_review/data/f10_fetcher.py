# -*- coding: utf-8 -*-
"""东财 PC_HSF10 公开接口抓取器（F10 同款数据）。

数据源（均为公开网页 JSON 接口，无鉴权，实测可达）：
  - 公司概况 / 主营构成 / 财务三表 / 股东：https://emweb.securities.eastmoney.com/PC_HSF10/*
  - 估值 / 龙虎榜：https://datacenter-web.eastmoney.com/api/data/v1/get

合规（用户四条铁律）：
  - 不破解鉴权：全部公开接口，无 token/签名
  - 不绕过反爬：正常 User-Agent，不做验证码/加密参数绕过
  - 不高频轰炸：每代码每天最多 1 次全量抓取（持久缓存），请求间限速 350ms
  - 不镜像/倒卖：只存必要字段，仅本地自用
  - 不逆向：只用东财公开 JSON 接口，不复刻通达信私有协议
"""
import json
import os
import re
import time
from datetime import date, datetime

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_EM_BASE = "https://emweb.securities.eastmoney.com/PC_HSF10"
_DC_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DEF_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "data", "cache", "f10")

# 请求间最小间隔（秒）—— 不高频轰炸
_MIN_INTERVAL = 0.35
_last_request = 0.0


def _throttle():
    global _last_request
    now = time.time()
    wait = _MIN_INTERVAL - (now - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = now


# 各表只提取的关键科目（按用户"只保存必要字段"要求）
_LRB_FIELDS = [
    "TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "OPERATE_COST",
    "OPERATE_PROFIT", "TOTAL_PROFIT", "NETPROFIT", "PARENT_NETPROFIT",
    "DEDUCT_PARENT_NETPROFIT",
    "TOTAL_OPERATE_INCOME_YOY", "OPERATE_INCOME_YOY",
    "PARENT_NETPROFIT_YOY", "DEDUCT_PARENT_NETPROFIT_YOY",
]
_ZCFZB_FIELDS = [
    "MONETARYFUNDS", "ACCOUNTS_RECE", "INVENTORY", "GOODWILL",
    "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_PARENT_EQUITY",
    "TOTAL_EQUITY",
]
_XJLLB_FIELDS = [
    "NETCASH_OPERATE", "NETCASH_INVEST", "NETCASH_FINANCE",
]


class F10Fetcher:
    """东财 F10 数据抓取器（每代码每天最多 1 次全量抓取）。"""

    def __init__(self, cache_dir: str = None):
        self.cache_dir = cache_dir or _DEF_CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _UA})

    # ── 对外入口 ──
    def get_f10(self, code: str, market: str = "", refresh: bool = False) -> dict:
        """获取单只股票 F10 档案。同一天内命中缓存不抓网络。

        任一 section 抓取失败 → 降级：有旧缓存用旧缓存，否则该 section=None。
        """
        code = str(code).zfill(6)
        cached = self._load_cache(code)
        today = date.today().strftime("%Y-%m-%d")
        if cached and cached.get("fetched_date") == today and not refresh:
            return cached

        sym = self._to_symbol(code, market)
        sections = self._fetch_all(sym, code)

        # 旧缓存兜底：失败 section 用昨天的值
        if cached:
            for k, v in sections.items():
                if v is None and cached.get(k):
                    sections[k] = cached[k]

        payload = {
            "code": code,
            "fetched_date": today,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **sections,
        }
        # 全部核心 section 都失败且无旧缓存 → 不落盘，下次调用重试
        # （lhb 返回 {"records": []} 属正常数据，不算失败）
        key_sections = [sections.get(k) for k in
                        ("company", "business", "financial", "shareholders", "valuation")]
        any_ok = any(v is not None for v in key_sections)
        if any_ok or cached is not None:
            self._save_cache(code, payload)
        return payload

    def clear_cache(self, code: str) -> bool:
        p = self._cache_path(code)
        if os.path.exists(p):
            try:
                os.remove(p)
                return True
            except OSError:
                pass
        return False

    # ── 各 section 抓取 ──
    def _fetch_all(self, sym: str, code: str) -> dict:
        return {
            "company": self._fetch_company(sym),
            "business": self._fetch_business(sym),
            "financial": self._fetch_financial(sym),
            "shareholders": self._fetch_shareholders(sym),
            "valuation": self._fetch_valuation(code),
            "lhb": self._fetch_lhb(code),
        }

    def _fetch_company(self, sym: str) -> dict:
        try:
            j = self._get_json(f"{_EM_BASE}/CompanySurvey/PageAjax", {"code": sym})
        except Exception:
            return None
        if not isinstance(j, dict):
            return None
        jbzl = (j.get("jbzl") or [{}])[0] if isinstance(j.get("jbzl"), list) else {}
        if not (jbzl.get("SECURITY_NAME_ABBR") or jbzl.get("ORG_NAME")):
            return None
        return {
            "name": jbzl.get("SECURITY_NAME_ABBR", ""),
            "full_name": jbzl.get("ORG_NAME", ""),
            "industry": jbzl.get("INDUSTRYCSRC1", ""),      # 证监会行业
            "chairman": jbzl.get("CHAIRMAN", ""),
            "reg_capital": jbzl.get("REG_CAPITAL", ""),      # 万元
            "business_scope": self._first_sent(jbzl.get("BUSINESS_SCOPE", "")),
            "profile": self._truncate(jbzl.get("ORG_PROFILE", ""), 220),
        }

    def _fetch_business(self, sym: str) -> dict:
        """主营构成：按行业(1)/产品(2)/地区(3) 分组，最新报告期 top 项。"""
        try:
            j = self._get_json(f"{_EM_BASE}/BusinessAnalysis/PageAjax", {"code": sym})
        except Exception:
            return None
        if not isinstance(j, dict):
            return None
        zygcfx = j.get("zygcfx") or []
        dates = sorted(set(r["REPORT_DATE"] for r in zygcfx if r.get("REPORT_DATE")),
                       reverse=True)
        if not dates:
            return None
        rd = dates[0]
        rows = [r for r in zygcfx if r.get("REPORT_DATE") == rd]

        def _top(t):
            items = [r for r in rows if str(r.get("MAINOP_TYPE")) == t]
            items.sort(key=lambda x: -(x.get("MBI_RATIO") or 0))
            out = []
            for r in items[:8]:
                if not r.get("ITEM_NAME"):
                    continue
                gm = r.get("GROSS_RPOFIT_RATIO")
                out.append({
                    "item": r["ITEM_NAME"],
                    "income_ratio": round((r.get("MBI_RATIO") or 0) * 100, 1),
                    "gross_margin": round(float(gm) * 100, 1) if gm is not None else None,
                    "income": r.get("MAIN_BUSINESS_INCOME"),
                })
            return out

        scope = ""
        zyfw = j.get("zyfw") or []
        if isinstance(zyfw, list) and zyfw:
            scope = self._first_sent(zyfw[0].get("BUSINESS_SCOPE", ""))
        return {
            "report_date": rd[:10],
            "scope": scope,
            "by_industry": _top("1"),
            "by_product": _top("2"),
            "by_region": _top("3"),
        }

    def _fetch_financial(self, sym: str) -> dict:
        """财务三大表：最新报告期关键科目。"""
        try:
            ct = self._get_company_type(sym)
        except Exception:
            return None
        if not ct:
            return None
        try:
            dts = self._get_json(f"{_EM_BASE}/NewFinanceAnalysis/lrbDateAjaxNew",
                                 {"companyType": ct, "reportDateType": "0",
                                  "code": sym})
        except Exception:
            return None
        dates = [d["REPORT_DATE"] for d in (dts.get("data") or [])
                 if d.get("REPORT_DATE")]
        if not dates:
            return None
        date_arg = ",".join(dates[:2])
        result = {"report_date": dates[0][:10], "lrb": {}, "zcfzb": {}, "xjllb": {}}
        for key, ep, fields in (("lrb", "lrbAjaxNew", _LRB_FIELDS),
                                ("zcfzb", "zcfzbAjaxNew", _ZCFZB_FIELDS),
                                ("xjllb", "xjllbAjaxNew", _XJLLB_FIELDS)):
            try:
                st = self._get_json(
                    f"{_EM_BASE}/NewFinanceAnalysis/{ep}",
                    {"companyType": ct, "reportDateType": "0",
                     "reportType": "1", "dates": date_arg, "code": sym})
                rows = st.get("data") or []
                row = rows[0] if rows else {}
                d = {}
                for f in fields:
                    v = row.get(f)
                    if isinstance(v, bool):
                        d[f] = v
                    elif isinstance(v, (int, float)) and not isinstance(v, bool):
                        d[f] = round(float(v), 4)
                    elif v is not None:
                        d[f] = v
                result[key] = d
            except Exception:
                result[key] = {}
        return result

    def _get_company_type(self, sym: str) -> str:
        html = self._get_text(f"{_EM_BASE}/NewFinanceAnalysis/Index",
                              {"type": "web", "code": sym.lower()})
        m = re.search(r'id="hidctype"[^>]*value="([^"]*)"', html)
        return m.group(1) if m else ""

    def _fetch_shareholders(self, sym: str) -> dict:
        try:
            j = self._get_json(f"{_EM_BASE}/ShareholderResearch/PageAjax",
                               {"code": sym})
        except Exception:
            return None
        if not isinstance(j, dict):
            return None
        sdgd = j.get("sdgd") or []
        top = [{
            "name": r.get("HOLDER_NAME", ""),
            "ratio": r.get("HOLD_NUM_RATIO"),
            "hold_num": r.get("HOLD_NUM"),
            "change": r.get("HOLD_NUM_CHANGE") or "",
        } for r in sdgd[:10] if r.get("HOLDER_NAME")]
        gdrs = j.get("gdrs") or []
        h = gdrs[0] if isinstance(gdrs, list) and gdrs else {}
        sjkzr = j.get("sjkzr") or []
        jgcc = j.get("jgcc") or []
        inst = jgcc[0] if isinstance(jgcc, list) and jgcc else {}
        if not (sdgd or sjkzr or gdrs):
            return None
        ratio = h.get("TOTAL_NUM_RATIO")
        inst_ratio = inst.get("TOTAL_SHARES_RATIO")
        return {
            "controller": (sjkzr[0].get("HOLDER_NAME", "") if sjkzr else ""),
            "top10": top,
            "top10_ratio": round(sum(r.get("ratio") or 0 for r in top), 1),
            "holders": h.get("HOLDER_TOTAL_NUM"),
            "holders_change_pct": round(float(ratio), 2) if ratio is not None else None,
            "hold_focus": h.get("HOLD_FOCUS", ""),
            "inst_count": inst.get("TOTAL_ORG_NUM"),
            "inst_ratio": round(float(inst_ratio), 1) if inst_ratio is not None else None,
        }

    def _fetch_valuation(self, code: str) -> dict:
        try:
            j = self._get_json(_DC_BASE, {
                "reportName": "RPT_VALUEANALYSIS_DET", "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": 1, "pageSize": 1,
                "sortColumns": "TRADE_DATE", "sortTypes": -1,
            })
        except Exception:
            return None
        d = ((j.get("result") or {}).get("data") or []) if isinstance(j, dict) else []
        if not d:
            return None
        r = d[0]
        return {
            "board_name": r.get("BOARD_NAME", ""),
            "total_mv": r.get("TOTAL_MARKET_CAP"),
            "float_mv": r.get("NOTLIMITED_MARKETCAP_A"),
            "total_shares": r.get("TOTAL_SHARES"),
            "float_shares": r.get("FREE_SHARES_A"),
            "pe_ttm": r.get("PE_TTM"),
            "pe_static": r.get("PE_LAR"),
            "pb": r.get("PB_MRQ"),
            "ps_ttm": r.get("PS_TTM"),
            "close": r.get("CLOSE_PRICE"),
            "change_pct": r.get("CHANGE_RATE"),
        }

    def _fetch_lhb(self, code: str) -> dict:
        """龙虎榜：近 5 次上榜详情 + 最新一次席位（机构/游资识别在分析层）。"""
        try:
            j = self._get_json(_DC_BASE, {
                "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW", "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": 1, "pageSize": 5,
                "sortColumns": "TRADE_DATE", "sortTypes": -1,
            })
        except Exception:
            return None
        d = ((j.get("result") or {}).get("data") or []) if isinstance(j, dict) else []
        if not d:
            return {"records": [], "seats_buy": [], "seats_sell": []}
        records = [{
            "date": r.get("TRADE_DATE", "")[:10],
            "reason": r.get("EXPLAIN") or r.get("EXPLANATION") or "",
            "buy_amt": r.get("BILLBOARD_BUY_AMT"),
            "sell_amt": r.get("BILLBOARD_SELL_AMT"),
            "net_amt": r.get("BILLBOARD_NET_AMT"),
            "deal_amt": r.get("BILLBOARD_DEAL_AMT"),
            "d5": r.get("D5_CLOSE_ADJCHRATE"),
            "d10": r.get("D10_CLOSE_ADJCHRATE"),
            "d20": r.get("D20_CLOSE_ADJCHRATE"),
        } for r in d[:5]]
        latest_date = records[0]["date"]
        seats_buy = self._fetch_seats(code, latest_date, "BUY")
        seats_sell = self._fetch_seats(code, latest_date, "SELL")
        return {"records": records, "seats_buy": seats_buy, "seats_sell": seats_sell}

    def _fetch_seats(self, code: str, date_str: str, side: str) -> list:
        rn = ("RPT_BILLBOARD_DAILYDETAILSBUY" if side == "BUY"
              else "RPT_BILLBOARD_DAILYDETAILSSELL")
        f = f'(SECURITY_CODE="{code}")(TRADE_DATE=\'{date_str} 00:00:00\')'
        try:
            j = self._get_json(_DC_BASE, {
                "reportName": rn, "columns": "ALL", "filter": f,
                "pageNumber": 1, "pageSize": 8,
                "sortColumns": "ACCUM_AMOUNT", "sortTypes": -1,
            })
        except Exception:
            return []
        d = ((j.get("result") or {}).get("data") or []) if isinstance(j, dict) else []
        return [{
            "name": r.get("OPERATEDEPT_NAME", ""),
            "buy": r.get("BUY"),
            "sell": r.get("SELL"),
            "net": r.get("NET"),
        } for r in d[:8] if r.get("OPERATEDEPT_NAME")]

    # ── 基础设施 ──
    @staticmethod
    def _to_symbol(code: str, market: str = "") -> str:
        m = market[:2].lower() if market else (
            "sh" if code.startswith("6")
            else "sz" if code.startswith(("0", "3"))
            else "bj")
        return (m.upper() + code)

    def _request(self, url: str, params: dict = None):
        _throttle()
        r = self.session.get(url, params=params, timeout=12)
        r.raise_for_status()
        return r

    def _get_json(self, url: str, params: dict = None) -> dict:
        return self._request(url, params).json()

    def _get_text(self, url: str, params: dict = None) -> str:
        return self._request(url, params).text

    def _cache_path(self, code: str) -> str:
        return os.path.join(self.cache_dir, f"{code}.json")

    def _load_cache(self, code: str) -> dict:
        p = self._cache_path(code)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _save_cache(self, code: str, payload: dict):
        p = self._cache_path(code)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _first_sent(text: str) -> str:
        if not text:
            return ""
        t = text.strip()
        for sep in ("。", "；", ";"):
            idx = t.find(sep)
            if idx > 0:
                return t[:idx]
        return t[:80]

    @staticmethod
    def _truncate(text: str, n: int) -> str:
        text = (text or "").strip()
        return text[:n] + ("…" if len(text) > n else "")
