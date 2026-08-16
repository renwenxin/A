# 复盘预测台账 + 准确率统计 设计文档

日期：2026-08-16
状态：已获用户逐节确认（brainstorming 流程）

## 1. 背景与目标

复盘系统每天生成大量**可验证的预测**，但目前没有任何模块在次日回头核对：
- 精选标的（涨停首板候选，含 score）已有雏形：存 `data/picks_history.json`（30 天）、有"昨日验证"逻辑，但**验证结果不落库**、胜率统计 `win_rate` 写死为 0、页面不展示
- 情绪周期次日走向、竞价预期两类预测完全没有跟踪

目标：建立**预测台账**（SQLite 落库）+ **次日自动验证** + **准确率统计**（整体/按类型/按分数段），让系统对自身预测能力有可量化的认知，并反向指导选股阈值与周期判断规则调整。

## 2. 范围（用户已确认）

| 决策点 | 选择 |
|---|---|
| 预测类型 | B：精选标的 + 情绪周期方向 + 竞价预期（弱转强候选后置，不纳入） |
| 存储 | SQLite（`data/prediction_ledger.db`），现有 picks_history.json 一次性迁移 |
| 验证时机 | 自动：生成复盘时顺带验证昨日预测并落库 |
| 历史追溯 | 全量追溯 picks_history.json 里的历史精选（情绪周期/竞价预期无历史） |
| 界面 | 独立页面 `/prediction_ledger`，复盘页不动 |
| 统计维度 | 整体 + 按类型 + 按分数段（≥60 / 50-59 / <50） |

## 3. 数据模型

新文件 `data/prediction_ledger.db`，单表 `predictions`：

```sql
CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pred_date TEXT NOT NULL,          -- 预测生成的交易日 YYYYMMDD
  pred_type TEXT NOT NULL,          -- 'picks' | 'cycle' | 'auction'
  item_key TEXT NOT NULL,           -- 精选=股票代码；cycle/auction='daily'
  item_name TEXT DEFAULT '',        -- 精选=股票名
  direction TEXT,                   -- picks=NULL；cycle='up|flat|down'；auction='high|flat|low'
  score REAL,                       -- 精选 score；其他 NULL
  detail TEXT DEFAULT '',           -- JSON：reasons/stage_desc/forecast_desc/当日涨停池代码等
  actual TEXT,                      -- 'zt|up3|up|flat|down' / 'up|flat|down' / 'high|flat|low'；NULL=未验证
  hit INTEGER,                      -- 1 命中 / 0 未命中 / NULL 未验证
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pred ON predictions(pred_date, pred_type, item_key);
CREATE INDEX IF NOT EXISTS idx_pred_date ON predictions(pred_date);
CREATE INDEX IF NOT EXISTS idx_pred_type ON predictions(pred_type);
```

关键设计：auction 行的 detail 中保存**当日涨停池代码列表**——竞价验证需要次日对同一批股票计算平均高开，涨停池不落库则次日无法找回。

## 4. 结构化预测字段（daily.py 改动）

现有函数文本输出原样保留，仅新增字段：

| 函数 | 新增字段 | 映射规则 |
|---|---|---|
| `_detect_cycle_stage` | `next_bias` | 启动期/发酵期→`up`；高潮末期/退潮期→`down`；高潮期/震荡期/冰点期→`flat` |
| `_forecast_next_auction` | `direction` | 火爆/偏强→`high`；中性→`flat`；偏弱/观望→`low` |

> 该 stage→bias 映射本身就是**待验证假设**：台账积累后若某 stage 预测准确率显著偏低，即为调整映射的依据。

## 5. 命中判定规则

### 5.1 精选标的 picks（次日 TDX 日线 + 次日涨停池）

`actual` 分级：涨停→`zt`；涨幅≥3%→`up3`；收涨→`up`；-3.0 ≤ 涨幅 < 0→`flat`（含 -3.0 边界）；涨幅 < -3.0→`down`
**命中 = `zt` 或 `up3`**（明细保留完整分级）
涨停池网络失败降级：按 TDX 涨幅判定（主板≥9.8% / 创业板科创≥19.6% / 北交所≥29.4%）。边界明确：is_zt 优先；涨幅 ≥3% → up3；-3% ≤ 涨幅 < 0 → flat；涨幅 < -3% → down

### 5.2 情绪周期 cycle（次日涨停池 vs 当日涨停池）

比值 `r` = 次日涨停家数 / 当日涨停家数。cycle 行的 detail 需保存当日涨停家数（cycle.metrics.total_zt），供次日验证时计算 r
- 实际 `up`：r ≥ 1.1
- 实际 `down`：r ≤ 0.9
- 实际 `flat`：0.9 < r < 1.1
命中 = 预测方向（next_bias）与次日实际方向一致；次日涨停池拉取失败 → 记"未验证"不判错

### 5.3 竞价预期 auction（TDX 次日日线，对当日涨停池代码）

`avg_gap` = mean(次日开盘 / 当日收盘 - 1) × 100
- 实际 `high`：avg_gap ≥ 1.5%
- 实际 `low`：avg_gap ≤ -0.5%
- 实际 `flat`：-0.5% < avg_gap < 1.5%
命中 = 预测方向（direction）与次日实际方向一致

### 5.4 统计口径

近 30 天窗口；分数段分桶 ≥60 / 50-59 / <50；未验证不计入分母。

## 6. 模块结构

新包 `ashare_review/prediction_ledger/`：

| 文件 | 职责 | 关键接口 |
|---|---|---|
| `store.py` | SQLite 读写层 | `LedgerStore(db_path)`：建表、`upsert_predictions(rows)`、`get_unverified()`、`mark_verified(id, actual, hit)`、`set_actual(pred_date, pred_type, item_key, actual, hit)`、`rows(window_days)`、`summary(window_days)` |
| `validate.py` | 判定引擎（纯函数） | `grade_pick(today_chg, is_zt)`、`grade_cycle(today_zt, next_zt)`、`grade_auction(avg_gap)`、`hit_for(type, direction, actual)` |
| `service.py` | 编排层 | `record_day(report_dict, trade_date)`、`validate_pending(tdx, ak)`、`migrate_picks_history()` |

接线（app.py，改动最小）：
1. `/review` 路由**新生成路径**（refresh=1 或首次生成）拿到 report 后调用 `service.record_day(...)` + `service.validate_pending(...)`（幂等）。**缓存命中路径不调用**——单纯查看缓存页不应触发网络验证（记录已在最初生成时完成），台账页"验证未验证项"按钮（POST /api/ledger/validate）提供手动触发
2. `daily.py` 仅加两个字段
3. `base.html` 导航加"📒 预测台账"入口

历史追溯 `migrate_picks_history`：遍历 picks_history.json 每个日期 D → 次日交易日 N → 用验证逻辑（TDX + 涨停池降级）逐只判定 → 写入 ledger；幂等可重跑。

## 7. UI（/prediction_ledger）

路由 `GET /prediction_ledger`（服务端渲染，沿用 v3 设计系统），页面结构：

1. **统计面板**（4 卡片，近 30 天）：
   - 精选命中率（命中/样本 + 分数段三行小条 ≥60 / 50-59 / <50）
   - 情绪周期准确率（已验证 n 条 / 命中率）
   - 竞价预期准确率（同上）
   - 样本覆盖（已验证天数 / 待验证条数）
2. **台账明细表**（按日期倒序分组）：日期 | 类型 | 内容 | 预测方向 | 实际结果 | 判定（✅/❌/⏳）
   - 筛选：类型下拉 + 只看未验证
3. **空状态**引导文案
4. "验证未验证项"按钮 → `POST /api/ledger/validate`

## 8. 测试（tests/test_prediction_ledger.py）

- store：建表/幂等 upsert/summary 聚合（窗口过滤、未验证不计分母）
- validate 纯函数边界：涨停/3%/-3% 边界、r=1.1/0.9、avg_gap=1.5%/-0.5%、hit_for 全组合
- daily.py 新字段：8 stage 全覆盖 + 5 档 forecast 全覆盖映射正确
- service：FakeTdx 端到端 record_day→validate_pending；迁移幂等（跑两遍行数不变）
- API：/prediction_ledger 200 + 关键数据

## 9. 错误处理

- 涨停池网络失败：精选用 TDX 降级判定；cycle 记"未验证"；auction 用 TDX 不受影响；不阻塞复盘生成
- validate_pending 每条 try/except，单条失败跳过
- SQLite 首次运行自动建库建表；写入 INSERT OR IGNORE 幂等

## 10. 非目标

- 弱转强候选的验证（需竞价明细数据，后置）
- 移除现有 `picks_history.json` 机制（保留不动，台账为增量；后续可再清理）
- regime 维度统计（历史覆盖确认后另议）
- 定时任务调度（手动/复盘生成时自动触发）
