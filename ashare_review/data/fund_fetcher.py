"""基金数据获取 + 缓存（天天基金排行 + 雪球单只规模）

网络环境：fund.eastmoney.com（排行）与 xueqiu.com（规模）在本机可达，
与 push2.eastmoney.com 不同主机，不受东财推流断网影响。
- 排行按日缓存（净值/业绩变化慢，日级足够）
- 雪球规模按日增量缓存（只补拉缺失代码，避免每次全量重查）
- 所有 akshare 调用都用线程超时兜底，防止内部重试退避拖死请求
"""
import math
import queue as _queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import akshare as ak
import pandas as pd

from ..utils.cache import cache_get, cache_set

RANK_KINDS = ("混合型", "股票型")

# 名称含这些字样的一律排除（非主动单赛道基金：指数/ETF联接/LOF/增强/分级）
INDEX_NAME_PAT = (
    "ETF|指数|联接|LOF|增强|分级|货币|理财|发起式A|发起式C"
)


def _fetch_guarded(fn, timeout: float, label: str):
    """线程超时兜底：akshare 内部重试退避可能拖很久，超时就放弃。"""
    q = _queue.Queue(maxsize=1)

    def run():
        try:
            q.put(fn())
        except Exception as e:  # noqa: BLE001
            q.put(e)

    threading.Thread(target=run, daemon=True).start()
    try:
        r = q.get(timeout=timeout)
        if isinstance(r, Exception):
            raise r
        return r
    except _queue.Empty as e:
        raise TimeoutError(f"{label} 超时(>{timeout}s)") from e


# ---------------------------------------------------------------------------
# 天天基金开放基金排行（业绩）
# ---------------------------------------------------------------------------
def get_fund_rank(kind: str = "混合型") -> pd.DataFrame:
    """开放基金排行（当日缓存）。kind ∈ {混合型, 股票型}。

    返回列: 基金代码, 基金简称, 日期, 单位净值, 累计净值, 日增长率,
            近1周, 近1月, 近3月, 近6月, 近1年, 近2年, 近3年, 今年来, 成立来, 手续费
    """
    ns = f"fund_rank_{kind}"
    cached = cache_get(ns)
    if cached is not None:
        from io import StringIO

        return pd.read_json(StringIO(cached))

    df = _fetch_guarded(
        lambda: ak.fund_open_fund_rank_em(symbol=kind), 40, f"rank[{kind}]"
    )
    if df is not None and not df.empty:
        cache_set(ns, df.to_json(force_ascii=False))
    return df if df is not None else pd.DataFrame()


def get_all_rank() -> pd.DataFrame:
    """合并混合型 + 股票型排行，代码统一为 6 位字符串。"""
    frames = []
    for kind in RANK_KINDS:
        df = get_fund_rank(kind)
        if df is not None and not df.empty:
            df = df.copy()
            df["kind"] = kind
            frames.append(df)
    if not frames:
        raise RuntimeError("基金排行数据获取失败（网络不通？）")
    pool = pd.concat(frames, ignore_index=True)
    pool["基金代码"] = pool["基金代码"].astype(str).str.zfill(6)
    return pool


# ---------------------------------------------------------------------------
# 雪球单只基金规模（按需拉取，增量缓存）
# ---------------------------------------------------------------------------
def _parse_scale(raw: str) -> Optional[float]:
    """'2984.69万' → 0.298469 亿；'40.77亿' → 40.77；'—'/''/非数值 → None"""
    if not raw:
        return None
    raw = str(raw).strip()
    if raw in ("—", "-", "暂无", "0", "nan"):
        return None
    try:
        if "万亿" in raw:
            return float(raw.replace("万亿", "")) * 10000
        if "亿" in raw:
            return float(raw.replace("亿", ""))
        if "万" in raw:
            return float(raw.replace("万", "")) / 10000
        return float(raw)
    except ValueError:
        return None


def _fetch_scale_one(code: str) -> dict:
    """查单只基金规模（雪球），失败返回空占位。"""
    try:
        df = ak.fund_individual_basic_info_xq(symbol=code, timeout=12)
        if df is None or df.empty:
            return {}
        d = dict(zip(df["item"], df["value"]))
        raw = str(d.get("最新规模", "")).strip()
        return {
            "scale_yi": _parse_scale(raw),
            "scale_raw": raw,
            "manager": str(d.get("基金经理", "")).strip(),
            "ftype": str(d.get("基金类型", "")).strip(),
            "established": str(d.get("成立时间", "")).strip(),
        }
    except Exception:  # noqa: BLE001
        return {}


def get_fund_scale_xq(codes: List[str], workers: int = 12) -> Dict[str, dict]:
    """批量获取基金规模（雪球），按日增量缓存。

    只补拉当日缓存里缺失的代码；返回 {code: {scale_yi, scale_raw, manager, ftype, established}}。
    查询失败的代码会以空 dict 占位，避免每次重复拉取。
    """
    ns = "fund_scale_xq"
    store = cache_get(ns)
    if not isinstance(store, dict):
        store = {}

    need = [c for c in set(codes) if c not in store]
    if need:
        def _one(code: str):
            return code, _fetch_scale_one(code)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for code, info in pool.map(_one, need):
                if info:
                    store[code] = info
                else:
                    store[code] = {}  # 空占位，防止重复拉
        cache_set(ns, store)
    return store


# ---------------------------------------------------------------------------
# 规模展示辅助
# ---------------------------------------------------------------------------
def _nan_to_none(v):
    """None 或 NaN 统一归一为 None（pandas 会把 None 转成 NaN）。"""
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
    except TypeError:
        pass
    return v


def fmt_scale(scale_yi: Optional[float]) -> str:
    """亿 → 可读文本。None/NaN → '规模缺失'"""
    scale_yi = _nan_to_none(scale_yi)
    if scale_yi is None:
        return "规模缺失"
    if scale_yi < 0.01:
        return f"{scale_yi * 10000:.0f}万"
    if scale_yi < 100:
        return f"{scale_yi:.2f}亿"
    return f"{scale_yi:.0f}亿"


def scale_flag(scale_yi: Optional[float]) -> str:
    """规模档位标签（供 UI 展示五条件之一）。"""
    scale_yi = _nan_to_none(scale_yi)
    if scale_yi is None:
        return "规模未知"
    if scale_yi < 0.5:
        return "清盘风险"
    if scale_yi < 5:
        return "小规模✓"
    if scale_yi < 30:
        return "中等规模"
    if scale_yi <= 60:
        return "规模偏大"
    return "巨无霸✗"


def size_score(scale_yi: Optional[float]) -> float:
    """规模得分：0.5亿→100，2亿→85，10亿→67，50亿→48；缺失→45（中下）；超界→0（剔除）。"""
    scale_yi = _nan_to_none(scale_yi)
    if scale_yi is None:
        return 45.0
    if scale_yi < 0.5 or scale_yi > 60:
        return 0.0
    return max(15.0, min(100.0, 100.0 - 25.0 * math.log10(scale_yi / 0.5)))
