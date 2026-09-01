# 启动突破 V3 — 通达信主图公式逐行对齐（2026-09-01）

## 背景

用户提供逻辑哥完整通达信主图公式（SWL/SWS 操盘线 + zigzag 找顶线/找底线 + UAR 波浪等），
要求代码与公式逐行对齐。核对发现 3 处偏差、若干未实现项。

## 核对结论

| 段落 | 公式 | 原实现 | 结论 |
|---|---|---|---|
| 找顶线 zigzag 全链路 | HH→FG01→FG0→FG1→FG→G1X→G1→G2→NN→DRAWLINE | 已实现 | ✅ 逐行一致 |
| G/D 均线 | `G:=HA(C,5); D:=HA(C,10)`（Heikin-Ashi 收盘均线） | 普通 MA5/MA10 | ⚠️ 已修 |
| SWS 生命线 | `DMA(EMA20, MAX(1,100*SUM(VOL,5)/(3*CAPITAL)))` | 权重归一化到 (0,1] + 固定 5.5e9 股 | ⚠️ 已修 |
| 找底线 | `DRAWLINE(UU,L,REF(UU,1),REF(L,1),1)` | 未输出 | ⚠️ 已补 |
| UAR 波浪/↑↓箭头/HPTP/涨停统计 | — | 未实现 | ❌ 本期不做（不影响 V3 选股） |

## 修改内容

1. **HA 均线**（`analysis/indicators.py: calc_zigzag_find_top_line`）
   - `HA_C = (O+H+L+C)/4`，`G = MA(HA_C,5)`，`D = MA(HA_C,10)`
   - 替代 FG02/FD02 方向过滤里的 ma5/ma10；函数不再依赖外部 ma5/ma10 列
   - 效果：与普通 MA 在趋势明确期结果一致；在趋势转折/粘合期 swing 点归属更贴近公式

2. **SWS 公式还原**（`calc_swl_sws(df, capital_hands=None)`）
   - 通达信 DMA 动态权重 A 有效区间 [0,1]；公式 `MAX(1, ...)` 保证 A>=1 →
     DMA 取 A=1（X 全替换）→ **SWS = EMA(CLOSE,20)**（生命线 = 20日EMA）
   - 操盘线控盘 SWL>SWS ⟺ EMA10>EMA20
   - 旧实现把 A 归一化到 (0,1] 做换手加权慢速 DMA（语义相反），且小盘股高换手时
     A 巨大导致 overflow，已按通达信标准行为修正
   - `capital_hands` 参数保留兼容调用方（按推导 SWS 不依赖 CAPITAL）；
     `data/float_share.py` 保留作未来公式扩展用

3. **找底线输出**（`calc_zigzag_find_top_line`）
   - `find_bottom_line`：DRAWLINE(UU,L,REF(UU,1),REF(L,1),1) 线性外推，NaN 回退 60 日最低
   - 输出 `_uu` 列；供后续"蓄势"判定做下方支撑参考

## 数据口径

- VOL（本库 .day）= 股；通达信 CAPITAL = 手(100 股) → `SUM(VOL,5)/100` 换算
- 候选池压力位需 `force_rebuild_pool=True` 重建后生效（旧缓存用普通 MA 找顶线）

## 测试

- `tests/test_main_formula.py` 7 用例：HA 均线不依赖 ma 列、找底线输出、SWS=EMA20 语义、
  enrich_all 透传、流通股本默认回退
- 全套 232 测试通过

## 遗留（本期不做）

- UAR 波浪（KMJ=MA(C,3) 红绿柱 + UAR1A↑/UAR19↓）= 视频"反转确认/做T"信号
- ZT 涨停标记 / DRAWICON 突破图标 / HPTP 副图 / 涨停统计（面板已有 limit_count 近似）
