# -*- coding: utf-8 -*-
"""用分钟线验证当日竞价判断（龙哥 A/B/C 竞价三型 + 9:24/9:25 量能三档）。

A/B/C 形态（基于通达信 .lc1 分钟线 09:31 起走势 + 前一日收盘/最大单分钟量）：
  - A型·低开高走  ⭐⭐⭐⭐⭐ 该弱不弱（低开但 5 分钟翻红）
  - B型·平开放量  ⭐⭐⭐⭐   平开 + 放量拉升（主力抢筹）
  - C型·高开砸盘  💀      高开 + 5 分钟下杀（诱多陷阱）
  - 一字板 / 高开高走 / 高开回落 / 平开整理 / 低开走弱 为相邻形态

量能三档（9:24/9:25 竞价分笔量，与 review_v2.html 手动判定同规则，verdict 优先采用）：
  - 抢筹：9:24量 ≥ 9:25量的50% 且 9:25量 ≥ 昨日最高单分钟量的50%
  - 达标：9:25量 ≥ 昨日最高单分钟量的50%
  - 观望：量能不达标
纯函数，方便单测。
"""
from typing import Dict, List, Optional


def _verdict_for(typ: str, open_pct: float) -> str:
    """竞价信号收敛为三动作：抢筹 / 达标 / 观望。

    - 抢筹：资金主动抢筹（一字板 / A型翻红 / B型放量拉升 / 高开高走）
    - 达标：高开幅度达标但未确认（高开≥3%但回落，或平开小幅拉升）
    - 观望：不及预期（低开走弱 / 高开砸盘 / 无量平开整理 / 涨停开板）
    """
    if typ in ("一字板·涨停封死", "A型·低开高走", "B型·平开放量", "高开高走"):
        return "抢筹"
    if typ == "高开回落":
        return "达标" if open_pct >= 3 else "观望"
    if typ == "平开拉升":
        return "达标"
    return "观望"


def classify_auction_bars(bars: List[dict], prev_close: float,
                          prev_max_minute_vol: Optional[float] = None,
                          limit_pct: float = 10.0) -> Optional[dict]:
    """根据当日分钟线判断竞价类型。

    bars: read_minute_bars() 输出的当日 1 分钟线（09:31 起），每根含
          time(分钟数)/open/high/low/close/volume
    prev_close: 前一日收盘价
    prev_max_minute_vol: 前一日最大单分钟成交量（股，用于放量对比）
    limit_pct: 涨停幅度（主板10/创业板科创板20）
    返回 {type, sentiment, desc, open_pct, first5_trend, vol_ratio, turn_red} 或 None
    """
    if not bars or not prev_close or prev_close <= 0:
        return None
    first = bars[0]
    open_pct = (first["open"] - prev_close) / prev_close * 100

    # 前 5 分钟价格：取第 6 根 bar 的 open 近似；不足则用最后一根
    t0 = first["time"]
    p5 = None
    for b in bars:
        if b["time"] - t0 >= 5:
            p5 = b["open"]
            break
    if p5 is None:
        p5 = first["open"]
    last_close = bars[-1]["close"]
    first5_trend = (p5 - first["open"]) / first["open"] * 100 if first["open"] else 0.0
    turn_red = p5 > prev_close
    first_vol = first.get("volume") or 0
    vol_ratio = (round(first_vol / prev_max_minute_vol, 2)
                 if prev_max_minute_vol else None)

    def _r(typ, sentiment, desc):
        return {
            "type": typ, "sentiment": sentiment,
            "verdict": _verdict_for(typ, open_pct),
            "desc": desc,
            "open_pct": round(open_pct, 2), "first5_trend": round(first5_trend, 2),
            "vol_ratio": vol_ratio, "turn_red": turn_red,
        }

    # 一字/涨停开盘：开盘即涨停价
    if open_pct >= limit_pct * 0.95:
        if last_close >= first["open"] * 0.995:
            return _r("一字板·涨停封死", "strong", "开盘即涨停且全天未见明显开板，极强")
        return _r("涨停开板", "mid", f"开盘涨停但盘中开板（首5分钟{first5_trend:+.1f}%）")
    # 低开
    if open_pct < -1:
        if turn_red:
            return _r("A型·低开高走", "strong",
                      f"低开{open_pct:.1f}%后5分钟内翻红——该弱不弱，最强承接信号")
        return _r("低开走弱", "weak", f"低开{open_pct:.1f}%且未翻红，承接弱")
    # 平开
    if open_pct <= 1:
        if vol_ratio is not None and vol_ratio >= 0.3 and first5_trend > 0:
            return _r("B型·平开放量", "mid",
                      f"平开+放量（首分钟量比{vol_ratio:.0f}%）拉升，主力抢筹")
        if first5_trend > 1.5:
            return _r("平开拉升", "mid", f"平开小幅拉升（{first5_trend:+.1f}%）")
        return _r("平开整理", "neutral", "平开震荡整理，方向待定")
    # 高开
    if first5_trend < -1:
        return _r("C型·高开砸盘", "weak",
                  f"高开{open_pct:.1f}%后5分钟内下杀（{first5_trend:+.1f}%），诱多陷阱")
    if first5_trend > 0:
        return _r("高开高走", "strong", f"高开{open_pct:.1f}%且持续走强，强势")
    return _r("高开回落", "mid", f"高开{open_pct:.1f}%但走平回落，接力需谨慎")


def classify_auction_volume(vol_0924, vol_0925,
                            prev_max_minute_vol) -> Optional[dict]:
    """9:24/9:25 竞价量能三档判定（与 review_v2.html 手动判定同规则）。

    - 抢筹：9:24量 ≥ 9:25量的50% 且 9:25量 ≥ 昨日最高单分钟量的50%
    - 达标：9:25量 ≥ 昨日最高单分钟量的50%
    - 观望：量能不达标

    vol_0924/vol_0925 为 9:24、9:25 竞价分笔量，prev_max_minute_vol 为昨日
    最高单分钟量，三者单位需一致（股/股 或 手/手）。
    量能数据不全时返回 None（调用方走 A/B/C 形态降级）。
    """
    if not vol_0925 or not prev_max_minute_vol:
        return None
    vol_0924 = vol_0924 or 0
    half = prev_max_minute_vol * 0.5
    if vol_0924 >= vol_0925 * 0.5 and vol_0925 >= half:
        verdict, sentiment = "抢筹", "strong"
        desc = (f"9:24量{vol_0924 / 100:.0f}手 ≥ 9:25量{vol_0925 / 100:.0f}手×50%，"
                f"且 9:25量 ≥ 昨日最高单分钟量{prev_max_minute_vol / 100:.0f}手×50%")
    elif vol_0925 >= half:
        verdict, sentiment = "达标", "mid"
        desc = (f"9:25量{vol_0925 / 100:.0f}手 ≥ 昨日最高单分钟量"
                f"{prev_max_minute_vol / 100:.0f}手×50%（{half / 100:.0f}手）")
    else:
        verdict, sentiment = "观望", "weak"
        desc = (f"量能不达标：9:25量{vol_0925 / 100:.0f}手 < 昨日最高单分钟量"
                f"×50%（{half / 100:.0f}手）")
    return {
        "verdict": verdict, "sentiment": sentiment, "desc": desc,
        "vol_0924": int(vol_0924), "vol_0925": int(vol_0925),
        "prev_max_minute_vol": int(prev_max_minute_vol),
    }


def verify_minute_auction(tdx, code: str, market: str, trade_date: str,
                          vol_0924: Optional[int] = None,
                          vol_0925: Optional[int] = None) -> Optional[dict]:
    """用 trade_date 当天分钟线验证竞价判断（盘后可用）。

    tdx: TdxReader 实例
    trade_date: 'YYYYMMDD'
    vol_0924/vol_0925: 可选，当日 9:24/9:25 竞价分笔量（股）。传入后 verdict
    按量能三档规则判定，A/B/C 形态保留在 type 与 desc 中；不传或数据不全则
    退回纯 A/B/C 形态判定。
    无当日/前一日分钟数据时返回 None。
    """
    bars = tdx.read_minute_bars(code, market, days=15)
    if not bars:
        return None
    by_date: Dict[str, list] = {}
    for b in bars:
        by_date.setdefault(b["date"], []).append(b)
    dates = sorted(by_date.keys())
    target_date = None
    for d in dates:
        if d.replace("-", "") == trade_date:
            target_date = d
            break
    if not target_date:
        return None
    idx = dates.index(target_date)
    if idx == 0:
        return None  # 缺前一日数据，算不了高开幅度
    prev_date = dates[idx - 1]
    prev_close = by_date[prev_date][-1]["close"]
    prev_max_minute_vol = max((b["volume"] for b in by_date[prev_date]), default=0)
    limit_pct = 20.0 if code.startswith(("30", "68")) else 10.0
    base = classify_auction_bars(by_date[target_date], prev_close,
                                 prev_max_minute_vol, limit_pct)
    if base is None:
        return None
    # 量能三档优先：verdict/sentiment 按 9:24/9:25 规则，形态保留为 desc 副信息
    vol = classify_auction_volume(vol_0924, vol_0925, prev_max_minute_vol)
    if vol:
        base["verdict"] = vol["verdict"]
        base["sentiment"] = vol["sentiment"]
        base["desc"] = f"{vol['desc']}；形态：{base['type']}（{base['desc']}）"
        base["vol_rule"] = vol
    return base
