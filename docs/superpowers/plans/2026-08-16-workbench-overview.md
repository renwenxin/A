# 首页全功能总览（Workbench 升级）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首页升级为全功能总览——16 个功能卡片全部有入口与说明（补 V1/V2/消息雷达/预测台账/策略验证台/个股分析），4 个数据型功能带轻量实时状态徽标。

**Architecture:** 只动 3 个文件：`index.html`（16 卡 + 徽标 JS）、`app.py`（新增 1 个 JSON API `/api/ledger/summary`，其余 3 个徽标数据源复用现有 API）、`style.css`（`.hc-badge` 样式）。零改动功能页本身。

**Tech Stack:** Flask + Jinja2 + vanilla JS（无新依赖）。

**设计依据:** `docs/superpowers/specs/2026-08-16-workbench-overview-design.md`（commit 6a41626）

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `ashare_review/web/app.py` | 修改：新增 `/api/ledger/summary` |
| `ashare_review/web/templates/index.html` | 修改：16 卡全功能总览 + 4 徽标 + JS |
| `ashare_review/web/static/style.css` | 修改：追加 `.hc-badge` |
| `ashare_review/tests/test_home_overview.py` | 新建：首页与 summary API 测试 |

**关键约定：**
- 徽标数据源：台账 `/api/ledger/summary`（新增）、验证台 `/api/strategy_bench/snapshots`（已有）、雷达 `/api/radar/results`（已有）、持仓 `/api/risk/status?portfolio=vol180`（已有）
- 徽标渲染：`textContent`（无 XSS）；API 失败/空数据 `display:none` 静默隐藏；绝不阻塞页面
- 原有 10 张卡片文案保留，仅分组位置微调

---

### Task 1: /api/ledger/summary + 测试

**Files:**
- Modify: `ashare_review/web/app.py`
- Create: `ashare_review/tests/test_home_overview.py`

- [ ] **Step 1: 写失败测试**

```python
# ashare_review/tests/test_home_overview.py
"""首页全功能总览测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_ledger_summary_api(tmp_path, monkeypatch):
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.prediction_ledger.service import record_day
    from ashare_review.web.app import app
    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 't.db'))
    record_day({
        'sentiment': {'picks': [{'code': '600001', 'name': 'A', 'score': 60, 'reasons': []}]},
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'metrics': {'total_zt': 60}},
        'auction_forecast': {'forecast': '偏强', 'direction': 'high'},
    }, '20260814', str(tmp_path / 't.db'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/api/ledger/summary')
    assert rv.status_code == 200
    data = rv.get_json()
    assert 'picks' in data and 'cycle' in data and 'auction' in data
    assert data['picks']['total'] == 1


def test_ledger_summary_api_empty(tmp_path, monkeypatch):
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.web.app import app
    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 'empty.db'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/api/ledger/summary')
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['picks']['total'] == 0 and data['picks']['rate'] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_home_overview.py -v`
Expected: FAIL — 404（路由未实现）

- [ ] **Step 3: 实现（app.py，`/api/ledger/validate` 路由之后）**

```python
@app.route('/api/ledger/summary')
def api_ledger_summary():
    """预测台账统计摘要（首页徽标用，30 天窗口）"""
    from ..prediction_ledger.service import DB_PATH
    from ..prediction_ledger.store import LedgerStore
    store = LedgerStore(DB_PATH)
    return jsonify(store.summary())
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_home_overview.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add ashare_review/web/app.py ashare_review/tests/test_home_overview.py
git commit -m "feat(home): /api/ledger/summary 统计摘要 API"
```

---

### Task 2: index.html 全功能总览

**Files:**
- Modify: `ashare_review/web/templates/index.html`（整文件重写）

- [ ] **Step 1: 重写模板（16 卡 + 4 徽标 + JS）**

```html
{% extends "base.html" %}
{% block title %}工作台 · 竞价交易系统{% endblock %}
{% block content %}
<div class="content-area">

    <!-- ====== 页面标题 ====== -->
    <div class="page-header">
        <div>
            <div class="page-title">工作台</div>
            <div class="page-date">一站式 A 股短线交易决策 · {{ today }}</div>
        </div>
        <a href="/breakout_v3" class="btn btn-primary">开始今日交易 →</a>
    </div>

    <!-- ====== 交易流程引导 ====== -->
    <div class="guide-banner">
        <div class="guide-steps">
            <div class="guide-step">
                <span class="step-icon">⏰</span>
                <div>
                    <div class="step-label">9:15</div>
                    <div class="step-desc">盘前准备 · 选股</div>
                </div>
            </div>
            <span class="step-arrow">→</span>
            <div class="guide-step">
                <span class="step-icon">🔔</span>
                <div>
                    <div class="step-label">9:25</div>
                    <div class="step-desc">竞价分析 · 确认</div>
                </div>
            </div>
            <span class="step-arrow">→</span>
            <div class="guide-step">
                <span class="step-icon">📊</span>
                <div>
                    <div class="step-label">盘中</div>
                    <div class="step-desc">盯盘交易 · 持仓管理</div>
                </div>
            </div>
            <span class="step-arrow">→</span>
            <div class="guide-step">
                <span class="step-icon">🔍</span>
                <div>
                    <div class="step-label">盘后</div>
                    <div class="step-desc">复盘分析 · 次日决策</div>
                </div>
            </div>
        </div>
    </div>

    <!-- ====== 盘前准备 ====== -->
    <div class="section-title">盘前准备 · 9:15 前</div>
    <div class="card-grid">
        <a href="/screening" class="home-card">
            <h2><span class="hc-icon">🔍</span>多策略选股</h2>
            <p><strong>六大策略并行筛选：</strong>1进2接力 / 机构票 / 龙头战法 / 突破形态 / 板块分歧 / 竞价抢筹，叠加动量/反转/质量因子评分。</p>
            <p><strong>五指标战法：</strong>启动突破 V1–V3 / 接力 / N 字反包 / 冰点底。</p>
            <div class="hc-meta">含 V2 选股池状态机管理（仓位分配）</div>
        </a>
        <a href="/breakout_v3" class="home-card">
            <h2><span class="hc-icon">🚀</span>启动突破 V3 <span class="nav-badge" style="vertical-align:middle;">主力</span></h2>
            <p><strong>MAVOL180 突破战法升级版：</strong>zigzag 找顶线突破 + 竞价确认 + N 字反包信号 + 移动止盈。</p>
            <p><strong>模拟交易面板：</strong>候选池 → 观察池 → 就绪 → 持仓 → 已完成，全流程跟踪。</p>
        </a>
        <a href="/breakout_v2" class="home-card">
            <h2><span class="hc-icon">🧮</span>V2 选股池</h2>
            <p><strong>V2 状态机选股池：</strong>观察 → 就绪 → 持仓流转，含仓位分配与买入信号管理。</p>
            <div class="hc-meta">与多策略选股联动，V2 池状态机</div>
        </a>
        <a href="/breakout" class="home-card">
            <h2><span class="hc-icon">📐</span>V1 启动突破</h2>
            <p><strong>经典启动突破筛选：</strong>压力位突破 + 放量确认，原始突破形态选股。</p>
        </a>
        <a href="/auction_analysis" class="home-card">
            <h2><span class="hc-icon">🔔</span>竞价四维分析</h2>
            <p><strong>9:25 竞价结束即时分析：</strong>集合竞价量价异动 / 开盘强度排名 / 竞价抢筹识别 / 高开弱转强。</p>
            <div class="hc-meta">数据来自 AKShare 实时接口，需交易日 9:25 后运行</div>
        </a>
    </div>

    <!-- ====== 盘中交易 ====== -->
    <div class="section-title">盘中交易 · 盯盘与持仓</div>
    <div class="card-grid">
        <a href="/zt_replica" class="home-card">
            <h2><span class="hc-icon">📋</span>涨停复制战法</h2>
            <p><strong>涨停板复制模型：</strong>识别首板/二板涨停 → 跟踪回踩 → 等待复制信号 → 模拟买卖。</p>
            <p><strong>信号类型：</strong>缩量回踩 / 放量反包 / 均线支撑 / 涨停复制确认。</p>
            <div class="hc-meta">含一年回测，自动生成 xlsx 报告</div>
        </a>
        <a href="/sim_portfolio" class="home-card">
            <h2><span class="hc-icon">💼</span>模拟持仓</h2>
            <p><strong>统一持仓管理：</strong>备选标的 / 模拟持仓 / 已完成交易三板块，支持加仓、减仓、清仓与成本重算。</p>
            <div class="hc-meta">贴近真实交易的资金模型 <span class="hc-badge" id="badge-portfolio"></span></div>
        </a>
        <a href="/v4_monitor" class="home-card">
            <h2><span class="hc-icon">📊</span>V4 监控手册</h2>
            <p><strong>实盘候选池监控：</strong>盘前检查清单 + 买入/卖出信号面板 + 市场情绪。</p>
            <div class="hc-meta">按流程手册逐项操作</div>
        </a>
        <a href="/stock/000001" class="home-card">
            <h2><span class="hc-icon">📈</span>个股深度分析</h2>
            <p><strong>单票技术面全景：</strong>多周期均线 / MACD / 筹码分布 / 量比，自动给出止损位、止盈目标与仓位建议。</p>
            <div class="hc-meta">输入任意 6 位代码即可分析（点击进入示例页）</div>
        </a>
    </div>

    <!-- ====== 盘后复盘 ====== -->
    <div class="section-title">盘后复盘 · 15:00 后</div>
    <div class="card-grid">
        <a href="/review" class="home-card">
            <h2><span class="hc-icon">📝</span>每日复盘报告</h2>
            <p><strong>全市场涨停复盘：</strong>涨停统计 / 连板高度 / 板块强度 / 龙虎榜速览，一键生成 AI 市场综述。</p>
            <div class="hc-meta">TDX 本地数据 + AKShare 在线补充</div>
        </a>
        <a href="/regime_picks" class="home-card">
            <h2><span class="hc-icon">🎯</span>行情诊断</h2>
            <p><strong>三战法 × 行情分类：</strong>缠论趋势 + 小盘风格 + 情绪温度 → 六档行情，自动推荐对应战法与今日标的。</p>
        </a>
        <a href="/event_radar" class="home-card">
            <h2><span class="hc-icon">📡</span>消息雷达</h2>
            <p><strong>事件驱动分析链：</strong>事件 → 产业 → 公司 → 资金 → 股价，12 个预置主题，勾选即分析。</p>
            <p><strong>产出：</strong>产业链节点 / 龙头 / 潜力股 / 龙虎榜 / 明日要点，一键导出。</p>
            <div class="hc-meta">盘后做功课 <span class="hc-badge" id="badge-radar"></span></div>
        </a>
        <a href="/prediction_ledger" class="home-card">
            <h2><span class="hc-icon">📒</span>预测台账</h2>
            <p><strong>复盘预测的次日验证：</strong>精选标的 / 情绪周期方向 / 竞价预期三类预测，自动验证并统计准确率。</p>
            <div class="hc-meta">预测→验证→校准闭环 <span class="hc-badge" id="badge-ledger"></span></div>
        </a>
    </div>

    <!-- ====== 数据与研究 ====== -->
    <div class="section-title">数据与研究 · 随时</div>
    <div class="card-grid">
        <a href="/strategy_bench" class="home-card">
            <h2><span class="hc-icon">🧪</span>策略验证台</h2>
            <p><strong>5 大战法统一回测：</strong>V3 / 1进2 / 冰点 / 尾盘 / 涨停复制，标准绩效指标 + 历史快照 + 双快照对比（含 git 版本对比）。</p>
            <div class="hc-meta">策略迭代的度量闭环 <span class="hc-badge" id="badge-bench"></span></div>
        </a>
        <a href="/fund_screening" class="home-card">
            <h2><span class="hc-icon">💰</span>基金挑选</h2>
            <p><strong>养基体系五条件：</strong>按板块筛出符合标准的主动基金 Top5，支持关键词搜索。</p>
        </a>
    </div>

    <!-- ====== 使用提示 ====== -->
    <div class="card">
        <div class="card-header">💡 使用提示</div>
        <div class="card-body" style="font-size:var(--fs-13);color:var(--muted);line-height:1.9;">
            <div>• <strong style="color:var(--ink-2);">数据源：</strong>TDX 本地日线 + AKShare 实时行情/竞价，首次使用需确保 TDX 数据已下载</div>
            <div>• <strong style="color:var(--ink-2);">非交易时间：</strong>竞价分析与实时看盘可能返回空数据</div>
            <div>• <strong style="color:var(--ink-2);">左侧导航：</strong>按 盘前 → 盘中 → 盘后 流程排列</div>
            <div>• <strong style="color:var(--ink-2);">快捷操作：</strong>在任意页面按 <code>Ctrl+K</code> 快速搜索股票代码</div>
        </div>
    </div>

</div>
{% endblock %}
{% block scripts %}
<script>
// ===== 首页状态徽标（轻量：失败静默隐藏，不阻塞渲染） =====
(function () {
    function fill(id, text) {
        var el = document.getElementById(id);
        if (el) { el.textContent = text; }
    }
    function pct(v) {
        if (v === null || v === undefined) return '—';
        return (v * 100).toFixed(0) + '%';
    }
    // 预测台账：精选/周期命中率
    fetch('/api/ledger/summary')
        .then(function (r) { return r.json(); })
        .then(function (d) {
            fill('badge-ledger', '🎯 精选 ' + pct(d.picks && d.picks.rate) +
                ' · 周期 ' + pct(d.cycle && d.cycle.rate));
        }).catch(function () {});
    // 策略验证台：最近快照
    fetch('/api/strategy_bench/snapshots')
        .then(function (r) { return r.json(); })
        .then(function (d) {
            var s = (d.snapshots || [])[0];
            if (!s) { fill('badge-bench', '🧪 暂无快照'); return; }
            var t = (s.created_at || '').slice(11, 16);
            var ann = s.metrics && s.metrics.annual_return;
            fill('badge-bench', '🧪 ' + t + (ann === null || ann === undefined ? '' : ' · 年化 ' + ann.toFixed(0) + '%'));
        }).catch(function () {});
    // 消息雷达：今日结果
    fetch('/api/radar/results')
        .then(function (r) { return r.json(); })
        .then(function (d) {
            var res = d.result;
            if (res && (res.themes || []).length) {
                fill('badge-radar', '📡 今日 ' + res.themes.length + ' 主题已分析');
            } else {
                fill('badge-radar', '📡 今日待分析');
            }
        }).catch(function () {});
    // 模拟持仓 + 风控状态
    fetch('/api/risk/status?portfolio=vol180')
        .then(function (r) { return r.json(); })
        .then(function (d) {
            var st = d.can_open ? '✅ 可开仓' : '⛔ 风控拦截';
            fill('badge-portfolio', '💼 ' + (d.positions || 0) + ' 只 · ' + st);
        }).catch(function () {});
})();
</script>
{% endblock %}
```

- [ ] **Step 2: 冒烟测试**

Run: `python -m pytest ashare_review/tests/test_home_overview.py -v` + Flask test_client 请求 `/` 200
Expected: PASS（页面 200 + 2 API 测试）

- [ ] **Step 3: 追加首页内容测试**

在 `test_home_overview.py` 追加：

```python
def test_home_page_has_all_features():
    from ashare_review.web.app import app
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/')
    assert rv.status_code == 200
    body = rv.data.decode('utf-8')
    for kw in ['消息雷达', '预测台账', '策略验证台', 'V2 选股池', 'V1 启动突破', '个股深度分析']:
        assert kw in body, kw
    # 4 个徽标容器
    for bid in ['badge-ledger', 'badge-radar', 'badge-bench', 'badge-portfolio']:
        assert bid in body, bid
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_home_overview.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add ashare_review/web/templates/index.html ashare_review/tests/test_home_overview.py
git commit -m "feat(home): 首页全功能总览（16 卡 + 4 状态徽标）"
```

---

### Task 3: style.css 徽标样式 + 全量回归 + 冒烟 + 推送

**Files:**
- Modify: `ashare_review/web/static/style.css`

- [ ] **Step 1: 追加 .hc-badge 样式（文件末尾）**

```css
/* ===== 首页功能卡状态徽标 ===== */
.hc-badge {
    display: inline-block;
    margin-left: 6px;
    padding: 1px 8px;
    border-radius: 999px;
    background: var(--brand-soft, #eef2ff);
    color: var(--brand, #4f46e5);
    font-size: 0.78em;
    font-weight: 600;
    white-space: nowrap;
}
```

（若 `--brand-soft` 不存在用 `#eef2ff`；`--brand` 已存在）

- [ ] **Step 2: 全量回归**

Run: `python -m pytest ashare_review/tests -q`
Expected: 全部通过（123 既有 + 3 新增 = 126 passed）

- [ ] **Step 3: 真实冒烟**

Run: Flask test_client 请求 `/`，确认 200 且四个徽标 API 各自返回（`/api/ledger/summary` 200、`/api/strategy_bench/snapshots` 200、`/api/radar/results` 200、`/api/risk/status` 200）
Expected: 全 200

- [ ] **Step 4: 提交推送**

```bash
git status   # 确认只含本功能文件
git add ashare_review/web/static/style.css
git commit -m "feat(home): 功能卡状态徽标样式"
git push origin main
```
