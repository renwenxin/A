# -*- coding: utf-8 -*-
"""F10 数据层 + 确定性分析单测（不打网络，用本地 fixture）。"""
import json
import os
import tempfile

import pytest

from ashare_review.data.f10_fetcher import F10Fetcher
from ashare_review.analysis.stock_f10_analysis import analyze_stock, classify_seat

# ── 600127 金健米业 真实接口响应（精简后的关键字段） ──

_COMPANY = {"jbzl": [{
    "SECURITY_NAME_ABBR": "金健米业",
    "ORG_NAME": "金健米业股份有限公司",
    "INDUSTRYCSRC1": "制造业-农副食品加工业",
    "CHAIRMAN": "帅富成",
    "REG_CAPITAL": 64178.3218,
    "BUSINESS_SCOPE": "许可项目:食品生产;食品销售;乳制品生产;粮食收购;粮油仓储服务。",
    "ORG_PROFILE": "金健米业股份有限公司于1998年4月在上海证券交易所上市,是农业产业化国家重点发展企业。",
}]}

_BUSINESS = {
    "zyfw": [{"BUSINESS_SCOPE": "许可项目:食品生产;粮食收购;粮油仓储服务。"}],
    "zygcfx": [
        {"REPORT_DATE": "2025-12-31 00:00:00", "MAINOP_TYPE": "1",
         "ITEM_NAME": "粮油食品加工业", "MBI_RATIO": 0.815572, "GROSS_RPOFIT_RATIO": 0.070497},
        {"REPORT_DATE": "2025-12-31 00:00:00", "MAINOP_TYPE": "1",
         "ITEM_NAME": "农产品贸易类", "MBI_RATIO": 0.09, "GROSS_RPOFIT_RATIO": 0.028},
        {"REPORT_DATE": "2025-12-31 00:00:00", "MAINOP_TYPE": "2",
         "ITEM_NAME": "粮油食品", "MBI_RATIO": 0.813, "GROSS_RPOFIT_RATIO": 0.07},
        {"REPORT_DATE": "2025-12-31 00:00:00", "MAINOP_TYPE": "2",
         "ITEM_NAME": "农产品贸易", "MBI_RATIO": 0.117, "GROSS_RPOFIT_RATIO": 0.028},
        {"REPORT_DATE": "2025-12-31 00:00:00", "MAINOP_TYPE": "2",
         "ITEM_NAME": "休闲食品", "MBI_RATIO": 0.034, "GROSS_RPOFIT_RATIO": 0.17},
        {"REPORT_DATE": "2025-12-31 00:00:00", "MAINOP_TYPE": "3",
         "ITEM_NAME": "中南", "MBI_RATIO": 0.737, "GROSS_RPOFIT_RATIO": 0.07},
        {"REPORT_DATE": "2025-12-31 00:00:00", "MAINOP_TYPE": "3",
         "ITEM_NAME": "西南", "MBI_RATIO": 0.161, "GROSS_RPOFIT_RATIO": 0.07},
    ],
}

_DATES = {"data": [{"REPORT_DATE": "2026-03-31 00:00:00"},
                   {"REPORT_DATE": "2025-12-31 00:00:00"}]}

_LRB = {"data": [{
    "REPORT_DATE": "2026-03-31 00:00:00",
    "TOTAL_OPERATE_INCOME": 813027952.47, "OPERATE_INCOME": 813027952.47,
    "OPERATE_COST": 747793439.87, "OPERATE_PROFIT": 878153.85,
    "TOTAL_PROFIT": 392211.03, "NETPROFIT": 145504.16,
    "PARENT_NETPROFIT": 175024.59, "DEDUCT_PARENT_NETPROFIT": -499069.4,
    "TOTAL_OPERATE_INCOME_YOY": 5.6825074714, "OPERATE_INCOME_YOY": 5.6825074714,
    "PARENT_NETPROFIT_YOY": -98.0898951273,
    "DEDUCT_PARENT_NETPROFIT_YOY": -106.4268499622,
}]}

_ZCFZB = {"data": [{
    "REPORT_DATE": "2026-03-31 00:00:00",
    "MONETARYFUNDS": 73475793.62, "ACCOUNTS_RECE": 73536854.26,
    "INVENTORY": 654145946.66, "GOODWILL": None,
    "TOTAL_ASSETS": 2228808097.01, "TOTAL_LIABILITIES": 1506034265.08,
    "TOTAL_PARENT_EQUITY": 671395769.41, "TOTAL_EQUITY": 722773831.93,
}]}

_XJLLB = {"data": [{
    "REPORT_DATE": "2026-03-31 00:00:00",
    "NETCASH_OPERATE": -179910953.08, "NETCASH_INVEST": -10823534.47,
    "NETCASH_FINANCE": 77871257.51,
}]}

_SHAREHOLDERS = {
    "sdgd": [
        {"HOLDER_NAME": "湖南粮食集团有限责任公司", "HOLD_NUM_RATIO": 21.34,
         "HOLD_NUM": 136932251, "HOLD_NUM_CHANGE": "不变"},
        {"HOLDER_NAME": "香港中央结算有限公司", "HOLD_NUM_RATIO": 1.97,
         "HOLD_NUM": 12611990, "HOLD_NUM_CHANGE": "7635139"},
    ],
    "gdrs": [{"HOLDER_TOTAL_NUM": 100115, "TOTAL_NUM_RATIO": 2.7326,
              "HOLD_FOCUS": "非常分散"}],
    "sjkzr": [{"HOLDER_NAME": "湖南省人民政府国有资产监督管理委员会"}],
    "jgcc": [{"TOTAL_ORG_NUM": 4, "TOTAL_SHARES_RATIO": 23.55925206}],
    "ltgf": [{"HOLD_NUM_COUNT": 163906449, "OTHER_UNLIMITED_SHARES": 477876769}],
}

_VALUATION = {"result": {"data": [{
    "SECURITY_CODE": "600127", "BOARD_NAME": "农产品加工",
    "TOTAL_MARKET_CAP": 5018744764.76, "NOTLIMITED_MARKETCAP_A": 5018744764.76,
    "TOTAL_SHARES": 641783218, "FREE_SHARES_A": 641783218,
    "PE_TTM": -962.31361668, "PE_LAR": 1330.25350285, "PB_MRQ": 7.47509143,
    "PS_TTM": 1.47535256, "CLOSE_PRICE": 7.82, "CHANGE_RATE": 9.985935302391,
    "TRADE_DATE": "2026-08-19 00:00:00",
}]}}

_LHB = {"result": {"data": [{
    "TRADE_DATE": "2026-08-19 00:00:00",
    "EXPLAIN": "非S证券连续三个交易日内收盘价格涨幅偏离值累计达到20%的证券",
    "BILLBOARD_BUY_AMT": 75733436.9, "BILLBOARD_SELL_AMT": 73231362,
    "BILLBOARD_NET_AMT": 2502074.9, "BILLBOARD_DEAL_AMT": 148964798.9,
    "D5_CLOSE_ADJCHRATE": None, "D10_CLOSE_ADJCHRATE": None,
}]}}

_LHB_BUY = {"result": {"data": [
    {"OPERATEDEPT_NAME": "沪股通专用", "BUY": 11506872, "SELL": None, "NET": 11506872},
    {"OPERATEDEPT_NAME": "开源证券股份有限公司西安太华路证券营业部",
     "BUY": 23524493, "SELL": None, "NET": 23524493},
]}}

_LHB_SELL = {"result": {"data": [
    {"OPERATEDEPT_NAME": "中信建投证券股份有限公司北京朝阳分公司",
     "BUY": None, "SELL": 4371533, "NET": -4371533},
]}}

FIXTURES = {
    "company": _COMPANY, "business": _BUSINESS, "dates": _DATES,
    "lrb": _LRB, "zcfzb": _ZCFZB, "xjllb": _XJLLB, "shareholders": _SHAREHOLDERS,
    "RPT_VALUEANALYSIS_DET": _VALUATION,
    "RPT_DAILYBILLBOARD_DETAILSNEW": _LHB,
    "RPT_BILLBOARD_DAILYDETAILSBUY": _LHB_BUY,
    "RPT_BILLBOARD_DAILYDETAILSSELL": _LHB_SELL,
}


class FakeFetcher(F10Fetcher):
    """用本地 fixture 替换网络响应，同时记录调用次数。"""

    def __init__(self, cache_dir, fixtures):
        super().__init__(cache_dir=cache_dir)
        self.fixtures = fixtures
        self.calls = []

    def _get_json(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        if params and params.get("reportName"):
            return self.fixtures.get(params["reportName"], {"result": {"data": []}})
        if url.endswith("CompanySurvey/PageAjax"):
            return self.fixtures.get("company", {})
        if url.endswith("BusinessAnalysis/PageAjax"):
            return self.fixtures.get("business", {})
        if url.endswith("ShareholderResearch/PageAjax"):
            return self.fixtures.get("shareholders", {})
        if "DateAjaxNew" in url:
            return self.fixtures.get("dates", {})
        if "lrbAjaxNew" in url:
            return self.fixtures.get("lrb", {})
        if "zcfzbAjaxNew" in url:
            return self.fixtures.get("zcfzb", {})
        if "xjllbAjaxNew" in url:
            return self.fixtures.get("xjllb", {})
        return {}

    def _get_text(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return '<html><input id="hidctype" value="4"/></html>'


def make_fetcher(tmp_path, fixtures=None):
    return FakeFetcher(str(tmp_path), fixtures if fixtures is not None else FIXTURES)


# ── 数据层解析 ──

def test_sections_extracted(tmp_path):
    f = make_fetcher(tmp_path)
    f10 = f.get_f10("600127")
    assert f10["company"]["name"] == "金健米业"
    assert f10["company"]["industry"] == "制造业-农副食品加工业"
    # 主营构成：按产品 top 项
    assert f10["business"]["by_product"][0]["item"] == "粮油食品"
    assert f10["business"]["by_product"][0]["income_ratio"] == 81.3
    assert f10["business"]["by_industry"][0]["item"] == "粮油食品加工业"
    assert f10["business"]["by_region"][0]["item"] == "中南"
    # 财务三表
    assert f10["financial"]["report_date"] == "2026-03-31"
    assert f10["financial"]["lrb"]["PARENT_NETPROFIT"] == 175024.59
    assert f10["financial"]["zcfzb"]["TOTAL_LIABILITIES"] == 1506034265.08
    assert f10["financial"]["xjllb"]["NETCASH_OPERATE"] == -179910953.08
    # 估值
    assert f10["valuation"]["pe_ttm"] < 0
    assert f10["valuation"]["board_name"] == "农产品加工"
    # 股东
    assert f10["shareholders"]["controller"] == "湖南省人民政府国有资产监督管理委员会"
    assert f10["shareholders"]["holders_change_pct"] == 2.73
    assert f10["shareholders"]["inst_ratio"] == 23.6
    # 龙虎榜
    assert len(f10["lhb"]["records"]) == 1
    assert f10["lhb"]["records"][0]["net_amt"] > 0
    assert f10["lhb"]["seats_buy"][0]["name"] == "沪股通专用"


def test_cache_serves_same_day(tmp_path):
    f = make_fetcher(tmp_path)
    f.get_f10("600127")
    assert f.calls, "首次抓取应有网络请求"
    f2 = make_fetcher(tmp_path)
    f10 = f2.get_f10("600127")
    assert f2.calls == [], "同一天缓存命中不应再发请求"
    assert f10["company"]["name"] == "金健米业"


def test_refresh_refetches(tmp_path):
    f = make_fetcher(tmp_path)
    f.get_f10("600127")
    n1 = len(f.calls)
    f.get_f10("600127", refresh=True)
    assert len(f.calls) > n1, "refresh=True 应强制重抓"


def test_total_failure_not_cached(tmp_path):
    f = make_fetcher(tmp_path, fixtures={})
    f10 = f.get_f10("600127")
    assert f10.get("company") is None
    assert f10.get("financial") is None
    cache_file = os.path.join(str(tmp_path), "600127.json")
    assert not os.path.exists(cache_file), "全部失败不应落盘坏缓存"


def test_partial_failure_falls_back_to_old_cache(tmp_path):
    f = make_fetcher(tmp_path)
    f.get_f10("600127")
    # 伪造旧缓存：company 用假值，且日期过期（2000-01-01）
    cache_file = os.path.join(str(tmp_path), "600127.json")
    with open(cache_file, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    payload["fetched_date"] = "2000-01-01"
    payload["company"] = {"name": "旧公司", "industry": "旧行业"}
    with open(cache_file, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    # 这次 company 抓取失败（fixtures 去掉 company）
    fixtures = dict(FIXTURES)
    fixtures["company"] = {}
    f3 = make_fetcher(tmp_path, fixtures)
    f10 = f3.get_f10("600127")
    assert f10["company"]["name"] == "旧公司", "失败 section 应回落旧缓存"


# ── 席位分类 ──

def test_classify_seat():
    assert classify_seat("沪股通专用") == "机构"
    assert classify_seat("机构专用") == "机构"
    assert classify_seat("深股通专用") == "机构"
    assert classify_seat("开源证券股份有限公司西安太华路证券营业部") == "游资"
    assert classify_seat("") == "游资"


# ── 确定性分析 ──

def _build_f10():
    d = tempfile.mkdtemp()
    f = FakeFetcher(d, FIXTURES)
    return f.get_f10("600127")


def test_analyze_stock_nine_dims():
    f10 = _build_f10()
    ctx = {
        "change_pct": 9.99,
        "limit_info": {"consecutive": 3, "seal_pressure": 22.86},
        "hot_boards": [{"name": "农产品加", "zt_count": 3},
                       {"name": "焦炭Ⅱ", "zt_count": 3}],
        "board_names": ["农产品加工"],
    }
    res = analyze_stock(f10, ctx)
    assert len(res["dims"]) == 9
    keys = [d["key"] for d in res["dims"]]
    assert keys == ["company_value", "industry_cycle", "financial_health",
                    "valuation", "capital_profile", "chip_structure",
                    "up_reason", "sustainability", "risks"]
    assert res["summary"]
    # 经营现金流为负 → 财务健康至少 🟡
    fh = next(d for d in res["dims"] if d["key"] == "financial_health")
    assert fh["level"] in ("🟡", "🔴")
    # PE(TTM) 为负 → 估值非 🟢
    val = next(d for d in res["dims"] if d["key"] == "valuation")
    assert val["level"] in ("🟡", "🔴")
    # 涨停 + 题材与主营吻合 → 为什么涨
    ur = next(d for d in res["dims"] if d["key"] == "up_reason")
    assert "连板" in ur["verdict"]
    assert ("吻合" in ur["verdict"]) or ("关联" in ur["verdict"])
    # 全部点存在
    for d in res["dims"]:
        assert d["verdict"], f"{d['key']} 缺结论"
        assert isinstance(d["points"], list)


def test_analyze_stock_non_limit_ctx():
    f10 = _build_f10()
    ctx = {"change_pct": 1.2, "limit_info": None, "hot_boards": [],
           "board_names": ["农产品加工"]}
    res = analyze_stock(f10, ctx)
    ur = next(d for d in res["dims"] if d["key"] == "up_reason")
    assert "连板" not in ur["verdict"]
