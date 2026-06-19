# Phase 3: Web 界面 (Tasks 12-19)

## Part of the Vibe-Trading integration plan

**Goal:** 新增 Chat / Alpha / Strategies 3个页面 + 11个API端点 + 增强4个现有页面

---

### Task 12: Chat API 端点

**Files:**
- Modify: `ashare_review/web/app.py` — 添加 `/api/chat/*` 路由

- [ ] **Step 1: 在 app.py 中添加 Chat API**

在 `app.py` 的 `SCREENERS = {...}` 之后，添加新的 API 路由：

```python
# ---- Chat API (Vibe-Trading integration) ----
import asyncio

@app.route('/api/chat', methods=['POST'])
def api_chat():
    """发起对话 — 返回 task_id 用于 SSE 订阅"""
    body = request.get_json(silent=True) or {}
    message = body.get('message', '')
    if not message:
        return jsonify({'error': 'message is required'}), 400

    task_id = _create_task()

    # 启动后台线程执行 Agent 分析
    def run():
        try:
            from ..agents.orchestrator import SwarmOrchestrator
            from ..agents.tools import execute_tool, TOOL_DEFINITIONS

            orch = SwarmOrchestrator()
            _emit_event(task_id, 'status', {'msg': f'开始分析: {message[:50]}...'})

            # 判断是否包含具体股票代码
            import re
            codes = re.findall(r'\b(\d{6})\b', message)

            if codes:
                # 单股深度分析
                for code in codes[:3]:  # 最多分析3只
                    _emit_event(task_id, 'status', {'msg': f'正在分析 {code}...'})
                    loop = asyncio.new_event_loop()
                    plan = loop.run_until_complete(
                        orch.analyze_stock(code, '', message))
                    loop.close()
                    _emit_event(task_id, 'agent_result', plan.to_dict())
                    _emit_event(task_id, 'trading_plan', plan.to_dict())
            else:
                # 市场综合分析
                _emit_event(task_id, 'status', {'msg': '开始市场综合分析...'})
                loop = asyncio.new_event_loop()
                plan = loop.run_until_complete(
                    orch.analyze_stock('000001', '上证指数', message))
                loop.close()
                _emit_event(task_id, 'trading_plan', plan.to_dict())

            _complete_task(task_id, {'done': True})
        except Exception as e:
            import traceback
            traceback.print_exc()
            _fail_task(task_id, str(e))

    t = threading.Thread(target=run, daemon=True)
    t.start()

    return jsonify({'task_id': task_id, 'status': 'processing'})


@app.route('/api/chat/stream/<task_id>')
def api_chat_stream(task_id):
    """SSE 流式订阅 Agent 分析过程"""
    return _sse_stream(task_id)


@app.route('/api/chat/history')
def api_chat_history():
    """对话历史 — 从 SQLite 读取（简化：内存实现）"""
    return jsonify({'history': []})


@app.route('/api/agent/analyze', methods=['POST'])
def api_agent_analyze():
    """对单只股票触发 Agent 分析"""
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    strategy = body.get('strategy', 'leader')
    if not code:
        return jsonify({'error': 'code is required'}), 400

    task_id = _create_task()

    def run():
        try:
            from ..agents.orchestrator import SwarmOrchestrator
            orch = SwarmOrchestrator()
            _emit_event(task_id, 'status', {'msg': f'AI分析 {code}...'})

            # 获取股票名称
            name = ''
            detail = {}
            if strategy in SCREENERS:
                s = SCREENERS[strategy]
                name = s._get_name(code)
                detail = {'strategy': strategy}

            _emit_event(task_id, 'status', {'msg': f'调用7个Agent并行分析 {name}({code})...'})

            import asyncio
            loop = asyncio.new_event_loop()
            plan = loop.run_until_complete(
                orch.analyze_stock(code, name, json.dumps(detail, ensure_ascii=False)))
            loop.close()

            _emit_event(task_id, 'trading_plan', plan.to_dict())
            _complete_task(task_id, plan.to_dict())
        except Exception as e:
            import traceback
            traceback.print_exc()
            _fail_task(task_id, str(e))

    t = threading.Thread(target=run, daemon=True)
    t.start()

    return jsonify({'task_id': task_id, 'status': 'processing'})
```

- [ ] **Step 2: 测试 Chat API（无 LLM 调用）**

```bash
cd D:/cursor/project && python -c "
from ashare_review.web.app import app
client = app.test_client()
resp = client.post('/api/chat', json={'message': '分析今天的市场'})
data = resp.get_json()
print(f'task_id={data.get(\"task_id\", \"\")}')
"
# 启动 Flask 并手动测试
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/web/app.py && git commit -m "feat: add Chat and Agent analysis API endpoints"
```

---

### Task 13: Alpha API + Strategy API 端点

**Files:**
- Modify: `ashare_review/web/app.py` — 添加 Alpha 和 Strategy API

- [ ] **Step 1: 添加 Alpha API 路由**

```python
# ---- Alpha API (Vibe-Trading integration) ----

@app.route('/api/alpha/list')
def api_alpha_list():
    zoo = request.args.get('zoo', '')
    from ..alpha.registry import get_registry
    r = get_registry()
    if zoo:
        factors = r.list_by_zoo(zoo)
    else:
        factors = r.list_all()
    return jsonify({
        'total': len(factors),
        'factors': r.summary(),
    })


@app.route('/api/alpha/eval', methods=['POST'])
def api_alpha_eval():
    body = request.get_json(silent=True) or {}
    factor_id = body.get('factor_id', '')
    code = body.get('code', '600519')
    days = body.get('days', 250)

    from ..alpha.registry import get_registry
    from ..alpha.evaluator import evaluate_factor
    r = get_registry()
    factor = r.get(factor_id)
    if factor is None:
        return jsonify({'error': f'Factor {factor_id} not found'}), 404

    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith('8') or code.startswith('4'):
        market = 'bj'
    df = tdx.read_daily(code, market)
    if df.empty:
        return jsonify({'error': f'No data for {code}'}), 404

    report = evaluate_factor(factor, df)
    return jsonify(report.to_dict())


@app.route('/api/alpha/compare', methods=['POST'])
def api_alpha_compare():
    body = request.get_json(silent=True) or {}
    factor_ids = body.get('factor_ids', [])
    code = body.get('code', '600519')

    from ..alpha.compare import compare_factors
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith('8') or code.startswith('4'):
        market = 'bj'
    df = tdx.read_daily(code, market)
    if df.empty:
        return jsonify({'error': f'No data for {code}'}), 404

    results = compare_factors(factor_ids, df)
    return jsonify({'factors': results})


# ---- Strategy API (Vibe-Trading integration) ----

@app.route('/api/strategy/templates')
def api_strategy_templates():
    from ..nl_strategy.templates import BUILTIN_TEMPLATES
    data = {}
    for k, v in BUILTIN_TEMPLATES.items():
        data[k] = {
            'name': v.name, 'description': v.description,
            'conditions_count': len(v.conditions),
        }
    return jsonify({'templates': data})


@app.route('/api/strategy/parse', methods=['POST'])
def api_strategy_parse():
    body = request.get_json(silent=True) or {}
    description = body.get('description', '')
    if not description:
        return jsonify({'error': 'description is required'}), 400

    from ..nl_strategy.parser import parse_strategy
    result = parse_strategy(description)
    if result['success']:
        return jsonify({
            'success': True,
            'spec': result['spec'].to_dict(),
        })
    return jsonify({'success': False, 'error': result.get('error', 'Parse failed')})


@app.route('/api/strategy/execute', methods=['POST'])
def api_strategy_execute():
    body = request.get_json(silent=True) or {}
    spec_dict = body.get('spec', {})
    template_id = body.get('template', '')

    from ..nl_strategy.spec import StrategySpec
    from ..nl_strategy.templates import BUILTIN_TEMPLATES
    from ..nl_strategy.executor import execute_strategy

    if template_id and template_id in BUILTIN_TEMPLATES:
        spec = BUILTIN_TEMPLATES[template_id]
    elif spec_dict:
        spec = StrategySpec.from_dict(spec_dict)
    else:
        return jsonify({'error': 'spec or template required'}), 400

    results = execute_strategy(spec)
    return jsonify({
        'strategy': spec.name,
        'total': len(results),
        'results': results,
    })


@app.route('/api/strategy/backtest', methods=['POST'])
def api_strategy_backtest():
    body = request.get_json(sent=True) or {}
    spec_dict = body.get('spec', {})
    days = body.get('days', 60)

    from ..nl_strategy.spec import StrategySpec
    from ..nl_strategy.executor import execute_strategy
    spec = StrategySpec.from_dict(spec_dict)

    # 简化回测：只看当前日结果 + 简单统计
    results = execute_strategy(spec)
    return jsonify({
        'strategy': spec.name,
        'days': days,
        'results_count': len(results),
        'note': '完整回测需接入 backtest 引擎',
    })
```

- [ ] **Step 2: 测试 API**

```bash
cd D:/cursor/project && python -c "
from ashare_review.web.app import app
client = app.test_client()
# Alpha list
resp = client.get('/api/alpha/list')
print('Alpha:', resp.get_json().get('total'))
# Strategy templates
resp = client.get('/api/strategy/templates')
print('Templates:', list(resp.get_json()['templates'].keys()))
"
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/web/app.py && git commit -m "feat: add Alpha and Strategy API endpoints"
```

---

### Task 14: Chat 页面模板

**Files:**
- Create: `ashare_review/web/templates/chat.html`

- [ ] **Step 1: 创建 Chat 页面**

```html
<!-- ashare_review/web/templates/chat.html -->
{% extends "base.html" %}
{% block content %}
<h1>💬 AI 投研助手</h1>

<div id="chat-container">
    <div id="chat-messages"></div>
    <div id="agent-cards"></div>
</div>

<div id="chat-input-area">
    <textarea id="chat-input" rows="2"
        placeholder="输入你的问题...&#10;例如: 帮我分析今天连板股里哪些还能追？"></textarea>
    <button id="chat-send" onclick="sendMessage()">发送</button>
</div>

<script>
let currentTaskId = null;
let eventSource = null;

function sendMessage() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;

    // 显示用户消息
    appendMessage('user', msg);
    input.value = '';
    document.getElementById('agent-cards').innerHTML = '';

    // 发送请求
    fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: msg})
    })
    .then(r => r.json())
    .then(data => {
        if (data.task_id) {
            currentTaskId = data.task_id;
            subscribeStream(data.task_id);
        }
    });
}

function subscribeStream(taskId) {
    if (eventSource) eventSource.close();

    eventSource = new EventSource('/api/chat/stream/' + taskId);

    eventSource.onmessage = function(e) {
        const msg = JSON.parse(e.data);
        if (msg.type === 'status') {
            appendStatus(msg.data.msg);
        } else if (msg.type === 'agent_result') {
            appendAgentCard(msg.data);
        } else if (msg.type === 'trading_plan') {
            appendTradingPlan(msg.data);
        } else if (msg.type === 'done') {
            appendStatus('分析完成 ✅');
            eventSource.close();
        } else if (msg.type === 'error') {
            appendStatus('错误: ' + msg.data.message, true);
            eventSource.close();
        }
    };

    eventSource.onerror = function() {
        appendStatus('连接中断，分析可能仍在后台进行', true);
    };
}

function appendMessage(role, content) {
    const el = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = 'chat-msg chat-' + role;
    div.innerHTML = `<strong>${role === 'user' ? '👤 你' : '🤖 AI'}</strong><p>${content}</p>`;
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
}

function appendStatus(msg, isError) {
    const el = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = 'chat-status' + (isError ? ' chat-error' : '');
    div.textContent = msg;
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
}

function appendAgentCard(data) {
    const el = document.getElementById('agent-cards');
    const card = document.createElement('div');
    card.className = 'agent-card';
    const dirEmoji = data.direction === 'bullish' ? '🔴' :
                     data.direction === 'bearish' ? '🟢' : '⚪';
    card.innerHTML = `
        <div class="agent-card-header">
            <span class="agent-name">${data.agent || ''}</span>
            <span class="agent-direction">${dirEmoji} ${data.direction || 'neutral'}</span>
            <span class="agent-score">${data.score || 0}分</span>
        </div>
        <div class="agent-card-body">
            <div class="agent-points">${(data.key_points || []).map(p => `<li>${p}</li>`).join('')}</div>
            <div class="agent-risks">⚠️ ${(data.risks || []).join('; ')}</div>
        </div>`;
    el.appendChild(card);
}

function appendTradingPlan(data) {
    const el = document.getElementById('agent-cards');
    const card = document.createElement('div');
    card.className = 'trading-plan-card';
    const actionLabel = data.action === 'buy' ? '买入' :
                        data.action === 'sell' ? '卖出' :
                        data.action === 'hold' ? '持有' : '观望';
    const actionEmoji = data.action === 'buy' ? '🔴' :
                        data.action === 'sell' ? '🟢' :
                        data.action === 'hold' ? '📌' : '👀';
    card.innerHTML = `
        <div class="plan-header">
            <span class="plan-action">${actionEmoji} ${actionLabel}</span>
            <span class="plan-risk risk-${data.risk_level || 'medium'}">风险: ${data.risk_level || 'medium'}</span>
        </div>
        ${data.action === 'buy' ? `
        <div class="plan-detail">
            <p>入场: ${data.entry_zone ? data.entry_zone.join(' - ') : 'N/A'}</p>
            <p>止损: ${data.stop_loss || 'N/A'}</p>
            <p>目标: ${data.targets ? data.targets.join(', ') : 'N/A'}</p>
            <p>仓位: ${(data.position_pct * 100).toFixed(0)}%</p>
        </div>` : ''}
        <div class="plan-rationale">${data.rationale || ''}</div>`;
    el.appendChild(card);
}
</script>
{% endblock %}
```

- [ ] **Step 2: 添加路由**

In `app.py`:

```python
@app.route('/chat')
def chat():
    return render_template('chat.html')
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/web/templates/chat.html ashare_review/web/app.py && git commit -m "feat: add Chat page with SSE streaming agent cards"
```

---

### Task 15: Alpha 页面 + Strategies 页面

**Files:**
- Create: `ashare_review/web/templates/alpha.html`
- Create: `ashare_review/web/templates/strategies.html`

- [ ] **Step 1: 创建 Alpha 因子库页面**

```html
<!-- ashare_review/web/templates/alpha.html -->
{% extends "base.html" %}
{% block content %}
<h1>📊 Alpha 因子库</h1>

<div class="alpha-toolbar">
    <select id="alpha-zoo" onchange="loadFactors()">
        <option value="">全部因子</option>
        <option value="gtja191">国泰君安191</option>
        <option value="alpha101">Alpha101</option>
        <option value="custom">龙哥特色</option>
    </select>
    <input id="alpha-code" type="text" value="600519" placeholder="股票代码" style="width:100px">
    <button onclick="loadFactors()">加载因子</button>
    <button onclick="compareSelected()">对比选中</button>
</div>

<div id="alpha-grid" class="factor-grid"></div>

<script>
function loadFactors() {
    const zoo = document.getElementById('alpha-zoo').value;
    fetch('/api/alpha/list' + (zoo ? '?zoo=' + zoo : ''))
        .then(r => r.json())
        .then(data => {
            const grid = document.getElementById('alpha-grid');
            grid.innerHTML = data.factors.map(f => `
                <div class="factor-card" id="fc-${f.id}">
                    <input type="checkbox" class="factor-check" value="${f.id}">
                    <div class="factor-id">${f.id}</div>
                    <div class="factor-name">${f.name}</div>
                    <div class="factor-meta">${f.category} | ${f.zoo}</div>
                </div>`).join('');
        });
}

function compareSelected() {
    const checked = document.querySelectorAll('.factor-check:checked');
    const ids = Array.from(checked).map(c => c.value);
    const code = document.getElementById('alpha-code').value;
    if (ids.length < 2) { alert('请至少选择2个因子'); return; }

    fetch('/api/alpha/compare', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({factor_ids: ids, code: code})
    })
    .then(r => r.json())
    .then(data => {
        const grid = document.getElementById('alpha-grid');
        grid.innerHTML = '<h3>对比结果 (按IR排序)</h3>' +
            '<table><tr><th>因子</th><th>IC均值</th><th>IC标准差</th><th>IR</th><th>星级</th></tr>' +
            data.factors.map(f => `
                <tr>
                    <td>${f.factor_id}</td>
                    <td>${f.ic_mean}</td><td>${f.ic_std}</td>
                    <td><strong>${f.ir}</strong></td>
                    <td>${'⭐'.repeat(f.stars)}</td>
                </tr>`).join('') + '</table>';
    });
}

loadFactors();
</script>
{% endblock %}
```

```html
<!-- ashare_review/web/templates/strategies.html -->
{% extends "base.html" %}
{% block content %}
<h1>📝 智能策略</h1>

<div class="strategy-section">
    <h3>预设模板</h3>
    <div id="template-grid" class="template-grid"></div>
</div>

<div class="strategy-section">
    <h3>自定义策略</h3>
    <textarea id="nl-input" rows="3"
        placeholder="描述你的选股思路...&#10;例如: 找今天涨停时间在上午10点前，封单金额超过1亿，流通市值小于100亿，且属于今天热点板块的股票"></textarea>
    <div class="strategy-actions">
        <button onclick="parseStrategy()">🤖 AI解析策略</button>
        <button onclick="executeTemplate()">▶ 立即筛选</button>
    </div>
</div>

<div id="strategy-result"></div>

<script>
function loadTemplates() {
    fetch('/api/strategy/templates')
        .then(r => r.json())
        .then(data => {
            const grid = document.getElementById('template-grid');
            grid.innerHTML = Object.entries(data.templates).map(([id, t]) => `
                <div class="template-card" onclick="selectTemplate('${id}')">
                    <h4>${t.name}</h4>
                    <p>${t.description}</p>
                    <span>${t.conditions_count} 条件</span>
                </div>`).join('');
        });
}

let currentTemplate = '';

function selectTemplate(id) {
    currentTemplate = id;
    document.querySelectorAll('.template-card').forEach(c => c.classList.remove('selected'));
    event.target.closest('.template-card').classList.add('selected');
}

function executeTemplate() {
    if (!currentTemplate) { alert('请先选择一个模板'); return; }
    fetch('/api/strategy/execute', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({template: currentTemplate})
    })
    .then(r => r.json())
    .then(data => showResults(data));
}

function parseStrategy() {
    const desc = document.getElementById('nl-input').value.trim();
    if (!desc) return;
    fetch('/api/strategy/parse', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({description: desc})
    })
    .then(r => r.json())
    .then(data => {
        const el = document.getElementById('strategy-result');
        if (data.success) {
            el.innerHTML = '<h4>解析成功: ' + data.spec.name + '</h4>' +
                '<pre>' + JSON.stringify(data.spec.conditions, null, 2) + '</pre>';
        } else {
            el.innerHTML = '<p class="error">解析失败: ' + data.error + '</p>';
        }
    });
}

function showResults(data) {
    const el = document.getElementById('strategy-result');
    el.innerHTML = '<h4>' + data.strategy + ' (共' + data.total + '只)</h4>' +
        '<table><tr><th>代码</th><th>名称</th><th>评分</th></tr>' +
        data.results.map(r => `<tr><td>${r.code}</td><td>${r.name}</td><td>${r.score}</td></tr>`).join('') +
        '</table>';
}

loadTemplates();
</script>
{% endblock %}
```

- [ ] **Step 2: 添加路由**

In `app.py`:

```python
@app.route('/alpha')
def alpha_page():
    return render_template('alpha.html')

@app.route('/strategies')
def strategies_page():
    return render_template('strategies.html')
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/web/templates/alpha.html ashare_review/web/templates/strategies.html ashare_review/web/app.py && git commit -m "feat: add Alpha library and Strategy editor pages"
```

---

### Task 16: 导航更新

**Files:**
- Modify: `ashare_review/web/templates/base.html`

- [ ] **Step 1: 在导航栏添加新页面链接**

```html
<!-- 在现有导航项后面添加： -->
    <nav>
        <a href="/">首页</a>
        <a href="/screening">选股面板</a>
        <a href="/review">复盘报告</a>
        <a href="/chat">💬 AI投研</a>
        <a href="/alpha">📊 因子库</a>
        <a href="/strategies">📝 策略</a>
    </nav>
```

- [ ] **Step 2: Commit**

```bash
cd D:/cursor/project && git add ashare_review/web/templates/base.html && git commit -m "feat: add new nav links for Chat, Alpha, and Strategies"
```

---

### Task 17: 选股页 AI 分析按钮

**Files:**
- Modify: `ashare_review/web/templates/screening.html`

- [ ] **Step 1: 在结果卡片中添加 AI 分析按钮**

在 `screening.html` 的精选卡片渲染部分，每个卡片底部添加：

```html
<button class="btn-ai-analyze" onclick="analyzeWithAI('${item.code}', '${strategy}')"
        style="margin-top:8px;padding:6px 12px;background:#6c5ce7;color:white;border:none;border-radius:4px;cursor:pointer">
    🤖 AI分析
</button>
```

以及在页面底部添加弹窗容器：

```html
<div id="ai-modal" class="ai-modal" style="display:none">
    <div class="ai-modal-content">
        <span class="ai-modal-close" onclick="closeAIModal()">&times;</span>
        <h3 id="ai-modal-title">AI 分析中...</h3>
        <div id="ai-modal-body"></div>
    </div>
</div>
```

添加 JS 函数：

```javascript
let aiEventSource = null;

function analyzeWithAI(code, strategy) {
    document.getElementById('ai-modal').style.display = 'block';
    document.getElementById('ai-modal-title').textContent = '🤖 AI分析 ' + code;
    document.getElementById('ai-modal-body').innerHTML = '<p>正在调用7个AI分析师...</p>';

    if (aiEventSource) aiEventSource.close();

    fetch('/api/agent/analyze', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({code: code, strategy: strategy})
    })
    .then(r => r.json())
    .then(data => {
        if (data.task_id) {
            aiEventSource = new EventSource('/api/chat/stream/' + data.task_id);
            aiEventSource.onmessage = function(e) {
                const msg = JSON.parse(e.data);
                if (msg.type === 'status') {
                    document.getElementById('ai-modal-body').innerHTML +=
                        '<p>' + msg.data.msg + '</p>';
                } else if (msg.type === 'trading_plan') {
                    showTradingPlanInModal(msg.data);
                } else if (msg.type === 'done') {
                    aiEventSource.close();
                }
            };
        }
    });
}

function showTradingPlanInModal(data) {
    const actions = {buy: '买入', sell: '卖出', hold: '持有', watch: '观望'};
    const risks = {low: '低风险', medium: '中风险', high: '高风险'};
    document.getElementById('ai-modal-body').innerHTML = `
        <div class="plan-result">
            <h3>${actions[data.action] || '观望'}</h3>
            <p>风险等级: ${risks[data.risk_level] || '未知'}</p>
            ${data.action === 'buy' ? `
                <p>入场区: ${data.entry_zone ? data.entry_zone.join(' - ') : 'N/A'}</p>
                <p>止损: ${data.stop_loss}</p>
                <p>目标: ${data.targets ? data.targets.join(', ') : 'N/A'}</p>
                <p>建议仓位: ${(data.position_pct * 100).toFixed(0)}%</p>
            ` : ''}
            <div class="rationale">${data.rationale || ''}</div>
            ${(data.agent_opinions || []).map(o => `
                <div class="opinion-chip">
                    <strong>${o.agent}</strong>: ${o.direction} (${o.score}分)
                </div>`).join('')}
        </div>`;
}

function closeAIModal() {
    document.getElementById('ai-modal').style.display = 'none';
    if (aiEventSource) aiEventSource.close();
}
```

- [ ] **Step 2: Commit**

```bash
cd D:/cursor/project && git add ashare_review/web/templates/screening.html && git commit -m "feat: add AI analysis button to screening results"
```

---

### Task 18: 个股详情页 + 复盘页增强

**Files:**
- Modify: `ashare_review/web/templates/stock_detail.html`
- Modify: `ashare_review/web/templates/review.html`

- [ ] **Step 1: 个股详情页底部添加 AI 多空观点区块**

在 `stock_detail.html` 的 `{% endblock %}` 之前添加：

```html
<!-- AI 多空观点 -->
<div style="margin-top:20px;background:white;padding:20px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1)">
    <h3>🤖 AI 多空观点
        <button onclick="loadAIOpinions('{{ code }}')"
                style="float:right;padding:6px 16px;background:#6c5ce7;color:white;border:none;border-radius:4px;cursor:pointer">
            分析
        </button>
    </h3>
    <div id="ai-opinions">点击"分析"按钮触发多Agent分析</div>
</div>

<script>
function loadAIOpinions(code) {
    document.getElementById('ai-opinions').innerHTML = '<p>分析中...</p>';
    fetch('/api/agent/analyze', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({code: code, strategy: 'leader'})
    })
    .then(r => r.json())
    .then(data => {
        let es = new EventSource('/api/chat/stream/' + data.task_id);
        let opinions = [];
        es.onmessage = function(e) {
            const msg = JSON.parse(e.data);
            if (msg.type === 'agent_result') {
                opinions.push(msg.data);
                renderOpinions(opinions);
            } else if (msg.type === 'trading_plan') {
                renderOpinions(opinions, msg.data);
            } else if (msg.type === 'done') {
                es.close();
            }
        };
    });
}

function renderOpinions(opinions, plan) {
    const el = document.getElementById('ai-opinions');
    let html = opinions.map(o => `
        <div class="opinion-row">
            <span class="op-agent">${o.agent}</span>
            <span class="op-dir ${o.direction === 'bullish' ? 'up' : o.direction === 'bearish' ? 'down' : ''}">${o.direction}</span>
            <span>评分: ${o.score}</span>
            <span>信心: ${(o.confidence * 100).toFixed(0)}%</span>
        </div>`).join('');
    if (plan) {
        html += `<div class="plan-summary"><strong>综合建议:</strong> ${plan.action} | 风险: ${plan.risk_level}
                 | 仓位: ${(plan.position_pct * 100).toFixed(0)}%</div>`;
        html += `<div class="plan-text">${plan.rationale || ''}</div>`;
    }
    el.innerHTML = html;
}
</script>
```

- [ ] **Step 2: 复盘页顶部添加 AI 综述区块**

在 `review.html` 的内容顶部添加：

```html
<!-- AI 市场综述 -->
<div class="ai-summary-box" id="ai-summary-box">
    <button onclick="loadAISummary()" class="btn-ai-summary">🤖 生成AI市场综述</button>
    <div id="ai-summary-content"></div>
</div>

<script>
function loadAISummary() {
    const el = document.getElementById('ai-summary-content');
    el.innerHTML = '<p>生成中...</p>';
    fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            message: `请根据以下复盘数据生成今日A股市场综述：涨停总数{{ report.total_limit_ups }}，
                     封板率等... 请给出：1.市场总览 2.热点板块 3.情绪周期 4.竞价预期 5.操作建议。`
        })
    })
    .then(r => r.json())
    .then(data => {
        let es = new EventSource('/api/chat/stream/' + data.task_id);
        es.onmessage = function(e) {
            const msg = JSON.parse(e.data);
            if (msg.type === 'trading_plan') {
                el.innerHTML = '<div class="ai-review">' + msg.data.rationale + '</div>';
            }
        };
    });
}
</script>
```

- [ ] **Step 3: Commit**

```bash
cd D:/cursor/project && git add ashare_review/web/templates/stock_detail.html ashare_review/web/templates/review.html && git commit -m "feat: add AI opinions to stock detail and AI summary to review page"
```

---

### Task 19: CSS 新增样式

**Files:**
- Modify: `ashare_review/web/static/style.css` — 追加新样式

- [ ] **Step 1: 追加 Chat/Agent/Alpha/Strategy 相关 CSS**

在 `style.css` 末尾追加：

```css
/* ====== Chat Page ====== */
#chat-container { display: flex; gap: 20px; min-height: 60vh; }
#chat-messages { flex: 1; background: white; border-radius: 8px; padding: 15px; overflow-y: auto; max-height: 60vh; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
#agent-cards { flex: 1; overflow-y: auto; max-height: 60vh; }
.chat-msg { margin-bottom: 12px; padding: 10px 14px; border-radius: 8px; }
.chat-user { background: #e8e0ff; text-align: right; }
.chat-assistant { background: #f0f0f0; }
.chat-status { color: #666; font-style: italic; margin: 4px 0; font-size: 0.9em; }
.chat-error { color: #e94560; }
.chat-msg strong { display: block; margin-bottom: 4px; }
#chat-input-area { display: flex; gap: 10px; margin-top: 15px; }
#chat-input { flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 1em; resize: vertical; }
#chat-send { padding: 10px 24px; background: #6c5ce7; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 1em; }
#chat-send:hover { background: #5a4bd1; }

/* Agent Cards */
.agent-card { background: white; border-radius: 8px; padding: 14px; margin-bottom: 10px; box-shadow: 0 1px 6px rgba(0,0,0,0.08); }
.agent-card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.agent-name { font-weight: bold; font-size: 1.05em; }
.agent-score { background: #6c5ce7; color: white; padding: 2px 10px; border-radius: 12px; font-size: 0.85em; }
.agent-points { font-size: 0.9em; color: #444; }
.agent-risks { font-size: 0.85em; color: #e94560; margin-top: 6px; }

/* Trading Plan Card */
.trading-plan-card { background: linear-gradient(135deg, #1a1a2e, #2d2d44); color: #eee; border-radius: 10px; padding: 20px; margin-top: 12px; }
.plan-header { display: flex; justify-content: space-between; margin-bottom: 12px; font-size: 1.2em; }
.plan-action { font-weight: bold; }
.plan-detail p { margin: 4px 0; font-size: 0.95em; }
.plan-rationale { margin-top: 12px; padding-top: 12px; border-top: 1px solid #444; font-size: 0.9em; line-height: 1.5; }
.risk-low { color: #00a854; }
.risk-medium { color: #f0c040; }
.risk-high { color: #e94560; }

/* Alpha Page */
.factor-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-top: 15px; }
.factor-card { background: white; padding: 14px; border-radius: 8px; cursor: pointer; transition: 0.15s; box-shadow: 0 1px 4px rgba(0,0,0,0.06); display: flex; align-items: flex-start; gap: 8px; }
.factor-card:hover { box-shadow: 0 2px 10px rgba(108,92,231,0.2); }
.factor-check { margin-top: 2px; }
.factor-id { font-family: monospace; background: #f0f0ff; padding: 2px 8px; border-radius: 3px; font-size: 0.85em; }
.factor-name { font-weight: bold; }
.factor-meta { color: #999; font-size: 0.8em; }
.alpha-toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 15px; }
.alpha-toolbar select, .alpha-toolbar input { padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; }
.alpha-toolbar button { padding: 6px 14px; background: #6c5ce7; color: white; border: none; border-radius: 4px; cursor: pointer; }

/* Strategy Page */
.template-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; margin: 12px 0; }
.template-card { background: white; padding: 16px; border-radius: 8px; cursor: pointer; border: 2px solid transparent; transition: 0.15s; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.template-card:hover { border-color: #6c5ce7; }
.template-card.selected { border-color: #6c5ce7; background: #f8f7ff; }
.template-card h4 { margin: 0 0 4px; color: #6c5ce7; }
.template-card p { font-size: 0.9em; color: #555; margin: 4px 0; }
.strategy-section { margin: 20px 0; }
.strategy-actions { margin: 10px 0; display: flex; gap: 10px; }
.strategy-actions button { padding: 8px 18px; border: none; border-radius: 5px; cursor: pointer; font-size: 1em; }
.strategy-actions button:first-child { background: #6c5ce7; color: white; }
.strategy-actions button:last-child { background: #e94560; color: white; }
#nl-input { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 1em; resize: vertical; }

/* AI Modal */
.ai-modal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1000; display: flex; justify-content: center; align-items: center; }
.ai-modal-content { background: white; border-radius: 12px; padding: 30px; max-width: 700px; width: 90%; max-height: 80vh; overflow-y: auto; }
.ai-modal-close { float: right; font-size: 1.5em; cursor: pointer; }

/* AI Opinions on Stock Detail */
.opinion-row { display: flex; gap: 15px; align-items: center; padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
.op-agent { font-weight: bold; min-width: 100px; }
.plan-summary { margin-top: 12px; padding: 10px; background: #f8f7ff; border-radius: 6px; }
.plan-text { margin-top: 8px; font-size: 0.9em; line-height: 1.5; color: #444; }

/* AI Summary on Review */
.ai-summary-box { margin: 15px 0; padding: 15px; background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
.btn-ai-summary { padding: 10px 20px; background: #6c5ce7; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 1em; }
.btn-ai-analyze { padding: 6px 12px; background: #6c5ce7; color: white; border: none; border-radius: 4px; cursor: pointer; }
.ai-review { line-height: 1.7; }
```

- [ ] **Step 2: Commit**

```bash
cd D:/cursor/project && git add ashare_review/web/static/style.css && git commit -m "feat: add CSS styles for Chat, Alpha, Strategy, and AI components"
```
