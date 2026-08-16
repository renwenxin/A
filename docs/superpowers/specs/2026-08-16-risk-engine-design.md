# 风控规则层（Risk Engine） 设计文档

日期：2026-08-16
状态：已获用户逐节确认（brainstorming 流程）

## 1. 背景与目标

系统目前止步于"选出标的 + 模拟持仓执行"，风控参数全部硬编码：止损线（vol180 -6% / zt_replica -5%）、单票仓位 10%、最大持仓 10、每日新开 3 只。选股→买入之间没有可配置的风控层，也没有"行情不好自动降仓位"的机制。

目标：**统一风控规则引擎**——止损线/仓位/持仓上限/每日新开/回撤熔断/regime 仓位缩放全部可配置并**真正作用于买卖执行**，从"给股票"升级为"给交易计划"。

## 2. 范围（用户已确认）

| 决策点 | 选择 |
|---|---|
| 覆盖范围 | A：Vol180 + 涨停复制两个模拟持仓共用统一引擎 |
| 生效方式 | A：配置化 + 自动执行（止损/仓位/熔断真正拦） |
| 回撤基准 | A：组合历史峰值（初始资金 + portfolio_history 最高 total） |
| 默认值 | regime 缩放沿用 v3_backtest regime_weights；熔断 8%、修复恢复 4% |
| 界面 | A：持仓页内嵌风控卡片 + 编辑弹窗（共享配置，不加独立页面） |
| 架构 | 独立 `risk/` 模块（rules/evaluate/store）+ portfolio 薄接入 |

## 3. 配置模型与存储

共享文件 `data/risk_config.json`，按 portfolio 分节：

```json
{
  "vol180": {
    "stop_loss_pct": -6.0,
    "per_position_pct": 10.0,
    "max_positions": 10,
    "max_new_per_day": 3,
    "drawdown_breaker_pct": 8.0,
    "drawdown_recover_pct": 4.0,
    "regime_scale": {"强势趋势": 1.0, "题材轮动": 0.7, "震荡观望": 0.3,
                     "弱市回调": 0.2, "退潮下跌": 0.0, "冰点超跌": 0.3}
  },
  "zt_replica": {
    "stop_loss_pct": -5.0,
    "per_position_pct": 10.0,
    "max_positions": 10,
    "max_new_per_day": 3,
    "drawdown_breaker_pct": 8.0,
    "drawdown_recover_pct": 4.0,
    "regime_scale": {"强势趋势": 1.0, "题材轮动": 0.7, "震荡观望": 0.3,
                     "弱市回调": 0.2, "退潮下跌": 0.0, "冰点超跌": 0.3}
  }
}
```

- 默认值与现状常量完全一致 → **不改配置行为零变化**
- `risk/rules.py` 内置 DEFAULT_CONFIG 随代码分发；`risk/store.py` 读写 JSON，缺失/损坏回退默认
- regime 缩放系数 0 的 regime 视为禁止开仓

## 4. 判定引擎（risk/evaluate.py 纯函数）

`evaluate(config, state, regime) -> {can_open, blocked_reasons[], suggested_size_pct, regime_scale, drawdown_pct}`
- state = {positions, opened_today, total_value, history_peak}
- 拦截顺序：回撤熔断（total_value 相对 history_peak 回撤 ≥ breaker）→ regime scale=0 → 持仓达上限 → 每日新开达上限
- 熔断恢复：回撤 < recover 时自动解除
- 建议仓位 = per_position_pct × regime_scale（未知 regime → 1.0 不误拦）
- blocked_reasons 为中文可读文本（喂页面/日志）

`stop_loss_pct(config) -> float`：卖出点读取的止损线。

## 5. portfolio 接入点（每类 3 处）

Vol180SimPortfolio：
1. 买入信号循环前：MAX_POSITIONS/MAX_NEW_PER_DAY/PER_POSITION_PCT 常量 → 读配置 + evaluate 拦截；建议仓位用 suggested_size_pct
2. 买入份额计算：position_capital = INITIAL_CAPITAL × PER_POSITION_PCT → INITIAL_CAPITAL × suggested_size_pct%（基数保持初始资金，与现状一致；suggested_size_pct 已含 regime 缩放，默认配置下 = 10% 行为不变）
3. 止损检查 _check_sell_vol180/_check_sell_v3：硬编码 -6% → config.stop_loss_pct

ZTReplicaSimPortfolio：
1. max_new 计算：MAX_NEW_PER_DAY 常量 → 配置 + evaluate 拦截
2. shares 计算：INITIAL_CAPITAL × PER_POSITION_PCT → 现金 × suggested_size_pct%
3. 止损检查：硬编码 -5% → config.stop_loss_pct

regime 输入：`live_diagnosis.get_regime_diagnosis()['regime']`（读缓存，廉价）；不可用回退 1.0。

## 6. Web 接线与 UI

API：
- `GET /api/risk/config` → 两份配置
- `POST /api/risk/config` → 保存（校验，非法 400）
- `GET /api/risk/status?portfolio=vol180|zt_replica` → 当日 regime/缩放/建议仓位/回撤进度/拦截状态+原因

UI：两个持仓页顶部各嵌"🛡️ 风控"卡片（regime 徽章 + 缩放系数 + 建议仓位 + 回撤进度条 + 开仓状态）+「编辑规则」弹窗（表单含全部参数与 regime 系数表）。不加导航入口。

## 7. 测试（tests/test_risk_engine.py）

- rules：默认配置完整性、校验（止损 -50% 拒绝、系数负数拒绝、缺字段回退）
- evaluate：正常/熔断边界（=breaker 触发、<breaker 放行、<recover 解除）/regime 0 拦截/仓位缩放/持仓上限/每日新开/多因叠加/未知 regime
- store：默认回退、往返、损坏 JSON
- 接入回归：FakeTdx 驱动单日扫描，不改配置行为与现状一致；改配置后止损提前触发、熔断生效
- API：GET/POST/400/status

## 8. 错误处理

- 配置文件缺失/损坏 → 回退默认 + warning
- regime 获取失败 → scale 1.0（不误拦）
- 配置校验失败 → 400 + 错误字段（不写入）
- 原子写（tmp + replace）

## 9. 非目标

- 独立风控页面（内嵌卡片）
- 每持仓独立配置分文件（共享一份分节）
- 精确到个股的动态止损（移动止损已由各战法自身实现，本层只管硬止损线配置）
- 历史规则变更审计（改配置不记历史）
