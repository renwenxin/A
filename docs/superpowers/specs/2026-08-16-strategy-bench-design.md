# 策略验证台（Strategy Bench） 设计文档

日期：2026-08-16
状态：已获用户逐节确认（brainstorming 流程）

## 1. 背景与目标

仓库有 20+ 个散落的回测脚本（v2/v3/v4、1进2、冰点、尾盘、涨停复制、双系统、缠论…），各自有 main() 和导出，但**输出结构各异、无统一绩效口径、无历史对比**。策略改动后无法回答"变好还是变坏"。

目标：**统一回测入口 + 标准绩效指标 + 历史快照 + 双快照对比**，让策略迭代有可度量的闭环。

## 2. 范围（用户已确认）

| 决策点 | 选择 |
|---|---|
| 覆盖策略 | A：核心 5 个（启动突破V3 / 1进2 / 冰点 / 尾盘 / 涨停复制），适配器可扩展 |
| 指标口径 | A：统一权益曲线，一套公式算所有策略 |
| 快照与对比 | A：SQLite `data/strategy_bench.db`，`git_sha` 支持版本对比，双快照并排 Δ |
| 界面 | A：独立页面 /strategy_bench + 后台线程任务 + 轮询 |
| 参数 | A：每策略核心参数面板（无参数扫描，预留接口） |
| 接入方式 | 适配器模式（零侵入现有回测脚本） |

## 3. 数据模型

`data/strategy_bench.db`，单表 `snapshots`：

```sql
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id TEXT NOT NULL,       -- 'v3' | 'one_two' | 'ice' | 'tail' | 'zt_replica'
  params TEXT NOT NULL,            -- JSON 参数（回显到面板）
  git_sha TEXT,                    -- 运行时 git HEAD
  created_at TEXT DEFAULT (datetime('now','localtime')),
  metrics TEXT NOT NULL,           -- JSON 统一指标
  equity_curve TEXT,               -- JSON [[exit_date, 累计收益%], ...]
  trades_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snap_strategy ON snapshots(strategy_id, created_at);
```

## 4. 统一指标口径（metrics.py 纯函数）

输入：归一化逐笔交易 `[{entry_date, exit_date, return_pct}]`

| 指标 | 公式 | 说明 |
|---|---|---|
| 总交易数 | len(trades) | — |
| 胜率 | 盈利笔数 / 总笔数 | — |
| 平均盈/亏 | mean(正收益) / mean(|负收益|) | — |
| 盈亏比 | 平均盈 / |平均亏| | — |
| 利润因子 | 总盈利 / |总亏损| | — |
| 累计收益 | ∏(1+rᵢ) − 1 | 复利 |
| 年化收益 | (1+累计收益)^(252/跨度交易日) − 1 | 跨度 = 首个 entry → 末个 exit 的交易日数（TradingCalendar） |
| 最大回撤 | 权益曲线峰值→谷值最大跌幅 | 权益曲线 = 按 exit_date 排序的累计收益序列 |
| 夏普（交易级近似） | mean(r)/std(r) × √(年化交易次数) | 年化交易次数 = 总笔数 × 252 / 跨度交易日；口径一致可横评 |

边界：0 笔 / std=0 → 对应指标置 None（页面显示"—"）。

## 5. 适配器层（零侵入）

归一化交易 schema：`{'entry_date': 'YYYYMMDD', 'exit_date': 'YYYYMMDD', 'return_pct': float}`

Adapter 基类（`adapters/base.py`）：
```python
class StrategyAdapter:
    strategy_id: str
    name: str
    description: str
    param_schema: list   # [{name, label, type, default, min, max}, ...]
    def run(self, params: dict, tdx=None, ak=None) -> List[Trade]: ...
```

5 个 adapter（内部调用现有脚本，不改动它们）：
- `v3` ← analysis/v3_backtest.py
- `one_two` ← analysis/one_two_backtest.py
- `ice` ← analysis/strategy_regime/ice_backtest.py
- `tail` ← analysis/tail_session_backtest.py
- `zt_replica` ← analysis/zt_replica_backtest.py

- `run(params, tdx=None, ak=None)` 把 tdx/ak 透传给现有回测类 → 测试可注入 fake
- 归一化在 adapter 内：读 result dict 的逐笔交易；缺 exit_date 用 entry_date + days_held 经日历推算
- 参数面板初拟：回看天数 / 持仓天数 / 入选前N / 最低分（V3、1进2）；回看天数 / 超跌阈值（冰点）；回看天数 / 尾盘时间段（尾盘）；回看天数 / 持仓天数 / 入选前N（涨停复制）——计划阶段读各脚本 main() 定稿

## 6. 快照存储与对比（store.py）

| 方法 | 职责 |
|---|---|
| `upsert_snapshot(strategy_id, params, git_sha, metrics, equity_curve, trades_count)` | 落库 |
| `list_snapshots(strategy_id=None, limit=50)` | 列表（倒序，可筛选） |
| `latest_snapshot(strategy_id)` | 最近一次 |
| `get_snapshot(id)` | 单条 |
| `compare(id_a, id_b)` | 对比（见下） |

对比输出：`{a, b, metrics: [{key, label, a, b, delta, delta_pct, better}], curves: {a: [...], b: [...]}}`
- 比率类 Δ 用百分点，收益类用绝对值；better 标记哪边更优（回撤越小越好，其余越大越好）
- 支持：同策略不同参数 / 同参数不同 git_sha / 跨策略横评

## 7. Web 接线与 UI

路由：
- `GET /strategy_bench` → 页面
- `POST /api/strategy_bench/run` {strategy_id, params} → 后台线程 → job_id
- `GET /api/strategy_bench/job/{job_id}` → running/done/error + 进度
- `GET /api/strategy_bench/snapshots?strategy_id=` → 列表
- `GET /api/strategy_bench/compare?a=&b=` → 对比 JSON

后台任务：模块级 JOBS dict + threading.Thread + 锁；线程内 adapter.run → metrics → 落库 → 更新状态；页面轮询 2s。

页面结构（`strategy_bench.html`，v3 设计系统）：
1. 策略选择：5 个 tab
2. 参数表单：param_schema 动态渲染 + 「⚡ 运行回测」 + 进度条
3. 结果区：指标卡片（年化/最大回撤/夏普/胜率/盈亏比/利润因子 + 交易数）+ 权益曲线（原生 SVG 折线，红涨绿跌）
4. 快照列表：筛选、最近 50 条、git_sha 前 7 位、参数摘要、核心指标、复选框
5. 对比模式：勾选两个 → Δ 表格 + 双曲线叠加

导航：base.html 在"预测台账"后加「🧪 策略验证台」。

## 8. 测试（tests/test_strategy_bench.py）

- metrics 纯函数：手算已知序列（+10%, -5%, +6% → 胜率 2/3、盈亏比 2.0、累计 10.77%）；边界（0 笔、单笔、std=0）；回撤峰值谷值
- store：upsert/list/latest/get/compare（Δ、better、回撤方向）
- adapters：注入 fake tdx/ak 跑通 5 个 run() 归一化，断言 schema
- API：页面 200、run 返回 job_id、poll 到 done、compare 结构
- param_schema：5 个 adapter 字段完整

## 9. 错误处理

- 回测异常 → job error + 消息（不污染快照库）
- 回测超时（默认 30 分钟）→ 标记 error
- 网络失败 → 现有脚本内部降级，adapter 不额外处理
- 对比参数非法 → 400 JSON
- SQLite 自动建库；线程独立连接

## 10. 非目标

- 参数扫描/寻优（预留接口，后续独立功能）
- 接入其余 15+ 研究脚本（alpha/lightgbm/缠论等）
- 定时自动跑回测（手动触发）
- 精确日内净值口径（交易级近似足够对比）
