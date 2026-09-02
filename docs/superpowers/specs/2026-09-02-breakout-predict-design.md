# 启动突破 V3 — 明日突破预测（2026-09-02）

## 背景

用户确认逻辑哥思路的第三层：不只抓"当日已突破"，还要**提前埋伏**——
选出压力位下方蓄力、**预测明天会放量突破压力位**的标的（视频："把买点更精准化，
坐在它起爆点/启动点"）。

现有 watch 蓄势池（压力位下方 0~10%）即候选池。需要新增"明日突破概率评分"。

## 边界（诚实口径）

- 无法保证预测准；做的是"突破前夜特征"的条件概率评分（逻辑哥："不能确定百分百，
  但能做相对的确定性"）
- 权重由 TDX 历史数据校准（统计特征出现后次日实际突破率），非拍脑袋
- 每天预测落台账，次日自动验证（记 hit），跑两周校准特征有效性

## 特征（候选，待校准）

| 特征 | 依据 | 计算 |
|---|---|---|
| 贴压力位近 | 越近越易试盘突破 | dist 0~-3% |
| 量能萎缩/地量 | 缩量=抛压耗尽 | 今日量 < 5日均量×0.7 |
| 试盘痕迹 | 近10日摸高压力位又收回 | 近10日 high ≥ pressure×0.98 |
| 均线粘合向上 | 三线粘合发散=资金进场 | 5/10/20 粘合(<3%)且向上 |
| 股性 | 涨停基因好才拉得动 | limit_count ≥15 |
| 板块联动 | 同板块已突破→跟风概率大 | sector 内已突破 ≥2 |
| 大盘环境 | 追涨赚钱才做突破 | 上证>MA60 且 涨停数≥60 |

## 架构

- `analysis/breakout_predict_calibrate.py`：历史校准（候选池 521 只 × 250 日，
  压力位用前60日高点近似，统计蓄势日特征 → 次日突破率）
- `tools/breakout_predict.py`（或并入 sim_portfolio）：预测评分 + SQLite 台账
  （`data/breakout_predict.db`，表 predictions: date/code/name/score/features/
  next_date/next_breakout/hit）
- API `/api/breakout_v3/predict`；面板 watch 上方「🔥 明日突破预测 Top10」
- 次日验证：run_daily/refresh 时对昨日 predictions 查 TDX 收盘是否站上当日压力位

## 校准结果（analysis/breakout_predict_calibrate.py, 16833 蓄势日样本, 4s）

| 条件 | 次日突破率 | lift |
|---|---|---|
| 基准（全部蓄势日） | 15.3% | — |
| 距压力位≤3% (near) | 34.5% | +19.1pp |
| near+probe+limit≥15 | **38.3%** (n=1437) | +22.9pp |
| near+probe+vol_shrink+limit | 41.5% (n=118) | +26.2pp |
| 地量 vol_shrink（单独） | 11.1% | **-4.3pp（负! 不作正向权重）** |
| 无任何特征 | 7.9% | — |

评分权重（由 lift 折算）: 基础25 + near30 + limit≥15:8 + probe:6 + ma_bull:5 +
vol_up(温和放量):5 + 地量仅在 near 基础上 +3。

## 验收

- 校准报告：基准突破率 vs 各特征组合（样本数+次日突破率）✅
- 今日（9-02）预测 top10 输出含依据 ✅（安泰集团79/国机精工77/金安国纪77...）
- 台账落库（breakout_predict.db pending 10）；测试覆盖评分函数与台账 ✅
- API /api/breakout_v3/predict 200；面板「🔥 明日突破预测」栏 ✅
