# 消息雷达（事件驱动分析）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现盘后事件驱动分析：用户勾选当日事件主题 → 自动完成产业链展开、资金验证、龙头/潜力股分层，生成明日题材候选清单（独立页面 + Markdown 导出）。

**Architecture:** 新建 `ashare_review/event_radar/` 独立包（数据模型 + 产业链展开 + 分析 + 报告），复用 TdxReader / AkshareFetcher / utils.cache / 设计系统样式。Web 层新增 /event_radar 页面 + /api/radar/* 端点。预置 12 个主题库，节点支持东财概念成分股（akshare，网络不可用时降级 manual_codes）。

**Tech Stack:** Python 3.11+, Flask, akshare, pandas, pytest（TDD），本地 qwen2.5（LLM 生成明日要点，可降级模板）。

**Spec:** docs/superpowers/specs/2026-08-15-event-radar-design.md

---

## 文件结构

**新建：**
- `ashare_review/event_radar/__init__.py` — 包入口（导出主要类）
- `ashare_review/event_radar/themes.py` — ChainNode/Theme 数据模型 + ThemesStore（themes.json CRUD）
- `ashare_review/event_radar/events.py` — RadarEvent 数据模型 + EventsStore（events.jsonl 追加/按日查询）
- `ashare_review/event_radar/presets.py` — PRESET_THEMES（12 个预置主题）+ seed 逻辑
- `ashare_review/event_radar/chain.py` — resolve_node_stocks（成分股解析+降级）、板块行情
- `ashare_review/event_radar/analyze.py` — analyze_event（资金验证 + 龙头/潜力分层）+ 明日要点
- `ashare_review/event_radar/report.py` — build_result / to_markdown / save_result / load_result
- `ashare_review/web/templates/event_radar.html` — 雷达页面
- `ashare_review/tests/test_event_radar.py` — 单元测试

**修改：**
- `ashare_review/data/akshare_fetcher.py` — 新增 get_concept_cons(concept_name)（东财概念成分股 + 缓存）
- `ashare_review/web/app.py` — 新增 /event_radar 页面 + /api/radar/* 6 个端点
- `ashare_review/web/templates/base.html` — 导航新增"消息雷达"入口

**运行时生成（gitignore 不强制，提交时排除）：** `ashare_review/data/event_radar/{themes.json, events.jsonl, results/, concept_cache.json}`

---

### Task 1: 事件雷达包骨架 + 数据模型 + ThemesStore

**Files:**
- Create: `ashare_review/event_radar/__init__.py`
- Create: `ashare_review/event_radar/themes.py`
- Test: `ashare_review/tests/test_event_radar.py`

- [ ] **Step 1: 写失败测试（themes CRUD + 数据模型）**

```python
# ashare_review/tests/test_event_radar.py
import os, sys, json, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ashare_review.event_radar.themes import Theme, ChainNode, ThemesStore

def _tmp_store(tmp_path):
    return ThemesStore(path=str(tmp_path / 'themes.json'))

def test_theme_dataclass_roundtrip():
    t = Theme(id='ai', name='AI算力',
              chain_nodes=[ChainNode(node='液冷服务器', concept_name='液冷服务器', manual_codes=['000977'])])
    d = ThemesStore._to_dict(t)
    t2 = ThemesStore._from_dict(d)
    assert t2.id == 'ai' and t2.name == 'AI算力'
    assert t2.chain_nodes[0].node == '液冷服务器'
    assert t2.chain_nodes[0].manual_codes == ['000977']

def test_themes_crud(tmp_path):
    store = _tmp_store(tmp_path)
    assert store.load() == []
    t = Theme(id='ai', name='AI算力', chain_nodes=[ChainNode(node='液冷')])
    assert store.add(t) is True
    assert len(store.load()) == 1
    assert store.update('ai', Theme(id='ai', name='AI算力V2', chain_nodes=[])) is True
    assert store.load()[0].name == 'AI算力V2'
    assert store.delete('ai') is True
    assert store.load() == []
    assert store.delete('nope') is False

def test_themes_persist(tmp_path):
    store = _tmp_store(tmp_path)
    store.add(Theme(id='x', name='X', chain_nodes=[]))
    store2 = ThemesStore(path=str(tmp_path / 'themes.json'))
    assert store2.load()[0].id == 'x'
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_event_radar.py -v`
Expected: FAIL（ModuleNotFoundError: ashare_review.event_radar）

- [ ] **Step 3: 实现 themes.py + __init__.py**

```python
# ashare_review/event_radar/themes.py
"""主题库：数据模型 + themes.json 读写（CRUD）。"""
import json, os
from dataclasses import dataclass, field, asdict
from datetime import date

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'event_radar')
THEMES_FILE = os.path.join(DATA_DIR, 'themes.json')


@dataclass
class ChainNode:
    node: str                       # 产业链节点名（显示用）
    concept_name: str = ''          # 东财概念板块名（可为空）
    manual_codes: list = field(default_factory=list)  # 手工股票池（兜底）


@dataclass
class Theme:
    id: str
    name: str
    chain_nodes: list = field(default_factory=list)
    last_event: str = ''
    updated: str = ''


class ThemesStore:
    def __init__(self, path: str = None):
        self.path = path or THEMES_FILE
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    @staticmethod
    def _to_dict(t: Theme) -> dict:
        return asdict(t)

    @staticmethod
    def _from_dict(d: dict) -> Theme:
        return Theme(
            id=d['id'], name=d['name'], last_event=d.get('last_event', ''),
            updated=d.get('updated', ''),
            chain_nodes=[ChainNode(**n) for n in d.get('chain_nodes', [])],
        )

    def load(self) -> list:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, encoding='utf-8') as f:
                return [self._from_dict(d) for d in json.load(f).get('themes', [])]
        except Exception:
            return []

    def save(self, themes: list) -> None:
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'themes': [self._to_dict(t) for t in themes]},
                      f, ensure_ascii=False, indent=2)

    def add(self, t: Theme) -> bool:
        themes = self.load()
        if any(x.id == t.id for x in themes):
            return False
        t.updated = date.today().strftime('%Y-%m-%d')
        themes.append(t)
        self.save(themes)
        return True

    def update(self, theme_id: str, t: Theme) -> bool:
        themes = self.load()
        for i, x in enumerate(themes):
            if x.id == theme_id:
                t.updated = date.today().strftime('%Y-%m-%d')
                themes[i] = t
                self.save(themes)
                return True
        return False

    def delete(self, theme_id: str) -> bool:
        themes = self.load()
        n = len(themes)
        themes = [x for x in themes if x.id != theme_id]
        if len(themes) == n:
            return False
        self.save(themes)
        return True

    def get(self, theme_id: str) -> Theme:
        for x in self.load():
            if x.id == theme_id:
                return x
        return None
```

```python
# ashare_review/event_radar/__init__.py
from .themes import Theme, ChainNode, ThemesStore
from .events import RadarEvent, EventsStore
from .presets import PRESET_THEMES, seed_default_themes
from .chain import resolve_node_stocks, get_sector_quote
from .analyze import analyze_event
from .report import build_result, to_markdown, save_result, load_result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest ashare_review/tests/test_event_radar.py -v`
Expected: PASS（themes 相关用例；events/presets 导入会失败 → 先跳过，Step 5 补 events）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/event_radar/__init__.py ashare_review/event_radar/themes.py ashare_review/tests/test_event_radar.py
git commit -m "feat(event-radar): theme data model and store with tests"
```

---

### Task 2: EventsStore（事件记录）

**Files:**
- Create: `ashare_review/event_radar/events.py`
- Test: `ashare_review/tests/test_event_radar.py`

- [ ] **Step 1: 写失败测试**

```python
def test_events_append_and_query(tmp_path):
    from ashare_review.event_radar.events import RadarEvent, EventsStore
    store = EventsStore(path=str(tmp_path / 'events.jsonl'))
    store.add(RadarEvent(date='2026-08-15', theme_id='ai', description='数据中心加速'))
    store.add(RadarEvent(date='2026-08-15', theme_id='robot', description='机器人量产'))
    store.add(RadarEvent(date='2026-08-14', theme_id='ai', description='旧事件'))
    rows = store.list_by_date('2026-08-15')
    assert len(rows) == 2
    assert rows[0].description == '数据中心加速'
    assert rows[0].created_at  # 自动填充时间戳
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_event_radar.py::test_events_append_and_query -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 events.py**

```python
# ashare_review/event_radar/events.py
"""事件记录：events.jsonl 追加 + 按日查询。"""
import json, os
from dataclasses import dataclass, field, asdict
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'event_radar')
EVENTS_FILE = os.path.join(DATA_DIR, 'events.jsonl')


@dataclass
class RadarEvent:
    date: str
    theme_id: str
    description: str
    created_at: str = ''


class EventsStore:
    def __init__(self, path: str = None):
        self.path = path or EVENTS_FILE
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def add(self, ev: RadarEvent) -> None:
        ev.created_at = ev.created_at or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(asdict(ev), ensure_ascii=False) + '\n')

    def list_by_date(self, d: str) -> list:
        if not os.path.exists(self.path):
            return []
        rows = []
        with open(self.path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = RadarEvent(**json.loads(line))
                except Exception:
                    continue
                if ev.date == d:
                    rows.append(ev)
        return rows
```

- [ ] **Step 4: 运行测试通过**

Run: `python -m pytest ashare_review/tests/test_event_radar.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ashare_review/event_radar/events.py
git commit -m "feat(event-radar): event log store with tests"
```

---

### Task 3: 预置主题库（12 个）

**Files:**
- Create: `ashare_review/event_radar/presets.py`
- Test: `ashare_review/tests/test_event_radar.py`

- [ ] **Step 1: 写失败测试**

```python
def test_presets_valid():
    from ashare_review.event_radar.presets import PRESET_THEMES
    assert len(PRESET_THEMES) == 12
    ids = [t.id for t in PRESET_THEMES]
    assert len(set(ids)) == len(ids)          # id 唯一
    for t in PRESET_THEMES:
        assert t.name and len(t.chain_nodes) >= 2   # 每主题至少 2 个节点
        for n in t.chain_nodes:
            assert n.node and (n.concept_name or n.manual_codes)  # 节点有来源

def test_seed_default_themes(tmp_path):
    from ashare_review.event_radar.presets import seed_default_themes
    from ashare_review.event_radar.themes import ThemesStore
    store = ThemesStore(path=str(tmp_path / 'themes.json'))
    assert seed_default_themes(store) == 12
    assert seed_default_themes(store) == 0     # 已 seed 不再重复
    assert len(store.load()) == 12
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_event_radar.py::test_presets_valid -v`
Expected: FAIL（ModuleNotFoundError: presets）

- [ ] **Step 3: 实现 presets.py**

```python
# ashare_review/event_radar/presets.py
"""预置主题库（12 个）。节点名=东财概念板块名（实施时若网络不可用，先以手动代码池兜底）。"""
from .themes import Theme, ChainNode


def _n(node, concept=None, codes=None):
    return ChainNode(node=node, concept_name=concept or node, manual_codes=codes or [])


PRESET_THEMES = [
    Theme(id='ai_compute', name='AI算力', chain_nodes=[
        _n('液冷服务器'), _n('光模块'), _n('PCB'), _n('铜缆高速连接'), _n('电源设备'), _n('MLCC')]),
    Theme(id='low_altitude', name='低空经济', chain_nodes=[
        _n('低空经济'), _n('eVTOL'), _n('无人机'), _n('碳纤维')]),
    Theme(id='humanoid_robot', name='人形机器人', chain_nodes=[
        _n('减速器'), _n('伺服电机'), _n('丝杠'), _n('传感器'), _n('机器人概念')]),
    Theme(id='solid_battery', name='固态电池', chain_nodes=[
        _n('固态电池'), _n('锂电池'), _n('锂电设备')]),
    Theme(id='innovative_drug', name='创新药', chain_nodes=[
        _n('创新药'), _n('CRO'), _n('减肥药'), _n('ADC')]),
    Theme(id='satellite_net', name='卫星互联网', chain_nodes=[
        _n('卫星互联网'), _n('卫星导航'), _n('北斗导航')]),
    Theme(id='commercial_space', name='商业航天', chain_nodes=[
        _n('商业航天'), _n('航空发动机'), _n('军工电子')]),
    Theme(id='semiconductor', name='半导体', chain_nodes=[
        _n('半导体设备'), _n('半导体材料'), _n('先进封装'), _n('存储芯片')]),
    Theme(id='data_element', name='数据要素', chain_nodes=[
        _n('数据要素'), _n('数据确权'), _n('国资云')]),
    Theme(id='military', name='军工', chain_nodes=[
        _n('军工'), _n('航空发动机'), _n('国防军工')]),
    Theme(id='power_grid', name='电力设备', chain_nodes=[
        _n('特高压'), _n('电网设备'), _n('充电桩'), _n('虚拟电厂')]),
    Theme(id='ai_glasses', name='AI眼镜', chain_nodes=[
        _n('AI眼镜'), _n('消费电子'), _n('光学元件')]),
]


def seed_default_themes(store) -> int:
    """themes.json 为空时写入预置主题，返回新增数量。"""
    if store.load():
        return 0
    for t in PRESET_THEMES:
        store.add(t)
    return len(PRESET_THEMES)
```

- [ ] **Step 4: 运行测试通过**

Run: `python -m pytest ashare_review/tests/test_event_radar.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ashare_review/event_radar/presets.py
git commit -m "feat(event-radar): preset 12 theme library"
```

---

### Task 4: AkshareFetcher.get_concept_cons + 产业链展开（chain.py）

**Files:**
- Modify: `ashare_review/data/akshare_fetcher.py`（新增方法，参考 get_concept_boards 的模式）
- Create: `ashare_review/event_radar/chain.py`
- Test: `ashare_review/tests/test_event_radar.py`

- [ ] **Step 1: 写失败测试（含降级逻辑）**

```python
def test_resolve_node_stocks_fallback():
    """concept 拉取失败时降级 manual_codes。"""
    from ashare_review.event_radar.chain import resolve_node_stocks
    from ashare_review.event_radar.themes import ChainNode

    class FailingFetcher:
        def get_concept_cons(self, name):
            raise RuntimeError('network down')

    node = ChainNode(node='液冷', concept_name='液冷服务器', manual_codes=['000977', '603019'])
    codes, source = resolve_node_stocks(node, FailingFetcher())
    assert codes == ['000977', '603019']
    assert source == 'manual'

    node2 = ChainNode(node='光模块', concept_name='光模块', manual_codes=[])
    codes2, source2 = resolve_node_stocks(node2, FailingFetcher())
    assert codes2 == [] and source2 == 'unavailable'

def test_resolve_node_stocks_concept():
    from ashare_review.event_radar.chain import resolve_node_stocks
    from ashare_review.event_radar.themes import ChainNode

    class OkFetcher:
        def get_concept_cons(self, name):
            return ['300308', '002281']

    node = ChainNode(node='光模块', concept_name='光模块', manual_codes=[])
    codes, source = resolve_node_stocks(node, OkFetcher())
    assert codes == ['300308', '002281']
    assert source == 'concept'
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_event_radar.py -k resolve_node -v`
Expected: FAIL（ModuleNotFoundError: chain）

- [ ] **Step 3: 实现 akshare_fetcher.get_concept_cons + chain.py**

```python
# 追加到 ashare_review/data/akshare_fetcher.py（放在 get_concept_boards 附近）
    def get_concept_cons(self, concept_name: str) -> list:
        """东财概念板块成分股代码列表（带缓存）。

        Args:
            concept_name: 概念板块名（如 '光模块'）

        Returns:
            list[str]: 成分股 6 位代码列表；失败返回 []（调用方降级）
        """
        import json as _json
        import os as _os
        cache_file = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                                   'data', 'event_radar', 'concept_cache.json')
        try:
            if _os.path.exists(cache_file):
                cache = _json.load(open(cache_file, encoding='utf-8'))
                if cache.get(concept_name):
                    return cache[concept_name]
        except Exception:
            pass
        try:
            import akshare as ak
            df = ak.stock_board_concept_cons_em(symbol=concept_name)
            codes = sorted(df['代码'].astype(str).str.zfill(6).tolist())
            try:
                _os.makedirs(_os.path.dirname(cache_file), exist_ok=True)
                cache = _json.load(open(cache_file, encoding='utf-8')) if _os.path.exists(cache_file) else {}
                cache[concept_name] = codes
                _json.dump(cache, open(cache_file, 'w', encoding='utf-8'), ensure_ascii=False)
            except Exception:
                pass
            return codes
        except Exception:
            return []
```

```python
# ashare_review/event_radar/chain.py
"""产业链展开：节点 → 成分股（akshare 概念成分，失败降级 manual_codes）。"""
from typing import Tuple, List
from .themes import ChainNode


def resolve_node_stocks(node: ChainNode, fetcher) -> Tuple[List[str], str]:
    """返回 (成分股代码列表, 数据源标签)。

    数据源优先级：manual_codes（人工维护）> concept_name（东财概念成分股）。
    返回标签: 'manual' | 'concept' | 'unavailable'
    """
    if node.manual_codes:
        return sorted(set(node.manual_codes)), 'manual'
    if node.concept_name and fetcher is not None:
        try:
            codes = fetcher.get_concept_cons(node.concept_name)
            if codes:
                return codes, 'concept'
        except Exception:
            pass
    return [], 'unavailable'
```

- [ ] **Step 4: 运行测试通过**

Run: `python -m pytest ashare_review/tests/test_event_radar.py -k resolve_node -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ashare_review/data/akshare_fetcher.py ashare_review/event_radar/chain.py
git commit -m "feat(event-radar): concept constituents fetch with fallback"
```

---

### Task 5: 分析核心（analyze.py）— 资金验证 + 龙头/潜力分层

**Files:**
- Create: `ashare_review/event_radar/analyze.py`
- Test: `ashare_review/tests/test_event_radar.py`

- [ ] **Step 1: 写失败测试（分层逻辑用注入的假 TDX）**

```python
class FakeTdx:
    """返回可控信号的假 TDX：code -> dict(close, prev_close, volume, ma5_vol)"""
    def __init__(self, data):
        self.data = data
    def read_daily(self, code, market):
        import pandas as pd
        d = self.data.get(code)
        if d is None:
            return pd.DataFrame()
        return pd.DataFrame([{'close': d['close'], 'prev_close': d['prev_close'],
                              'volume': d['volume'], 'vol_ma5': d['vol_ma5']}])

def test_analyze_layering():
    from ashare_review.event_radar.analyze import _classify, _stock_signal
    from ashare_review.event_radar.themes import Theme, ChainNode

    data = {
        '600001': {'close': 10.0, 'prev_close': 9.0, 'volume': 300, 'vol_ma5': 100},   # +11.1% 涨停
        '600002': {'close': 10.0, 'prev_close': 9.3, 'volume': 250, 'vol_ma5': 100},   # +7.5% 大阳
        '600003': {'close': 10.0, 'prev_close': 9.9, 'volume': 200, 'vol_ma5': 100},   # +1.0% 量比2.0
        '600004': {'close': 10.0, 'prev_close': 9.8, 'volume': 160, 'vol_ma5': 100},   # +2.0% 量比1.6
        '600005': {'close': 10.0, 'prev_close': 10.0, 'volume': 100, 'vol_ma5': 100},  # 0% 量比1.0 → 排除
    }
    tdx = FakeTdx(data)
    sigs = {c: _stock_signal(tdx, c, 'sh', '2026-08-15') for c in data}
    leaders, potentials = _classify(sigs)
    assert {s['code'] for s in leaders} == {'600001', '600002'}
    assert {s['code'] for s in potentials} == {'600003', '600004'}
    assert potentials[0]['code'] == '600003'   # 量比高者优先
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_event_radar.py::test_analyze_layering -v`
Expected: FAIL（ModuleNotFoundError: analyze）

- [ ] **Step 3: 实现 analyze.py**

```python
# ashare_review/event_radar/analyze.py
"""核心分析：资金验证 + 龙头/潜力分层。"""
import math
from typing import Dict, List, Optional

# 分层阈值（可调整）
LEADER_PCT = 7.0        # 龙头：涨幅 >= 7%
POTENTIAL_PCT_MAX = 3.0 # 潜力：涨幅 <= 3%
POTENTIAL_VOL_RATIO = 1.5  # 且 量比 >= 1.5
ZT_PCT = 9.5            # 主板涨停阈值


def _market(code: str) -> str:
    if code.startswith(('6', '9')):
        return 'sh'
    if code.startswith(('4', '8')):
        return 'bj'
    return 'sz'


def _stock_signal(tdx, code: str, trade_date: str) -> Optional[Dict]:
    """读 TDX 最新一根K线，计算 涨幅/量比/涨停。"""
    try:
        df = tdx.read_daily(code, _market(code))
        if df is None or df.empty:
            return None
        last = df.iloc[-1]
        close = float(last['close'])
        prev = float(last['prev_close']) if 'prev_close' in df.columns else float(df['close'].iloc[-2]) if len(df) > 1 else close
        vol = float(last['volume'])
        # 量比：vol / MA5(vol)
        if 'vol_ma5' in df.columns:
            vol_ma5 = float(last['vol_ma5'])
        else:
            vol_ma5 = float(df['volume'].tail(5).mean()) if len(df) >= 5 else vol
        vol_ratio = round(vol / vol_ma5, 2) if vol_ma5 > 0 else 0.0
        pct = round((close / prev - 1) * 100, 2) if prev > 0 else 0.0
        is_zt = pct >= ZT_PCT - 0.1
        return {'code': code, 'pct': pct, 'vol_ratio': vol_ratio, 'is_zt': is_zt,
                'close': round(close, 2)}
    except Exception:
        return None


def _classify(signals: Dict[str, Dict]) -> tuple:
    """分层：龙头（涨幅>=7% 或涨停）+ 潜力（0<涨幅<=3% 且量比>=1.5）。"""
    sigs = [s for s in signals.values() if s]
    leaders = [s for s in sigs if s['is_zt'] or s['pct'] >= LEADER_PCT]
    leaders.sort(key=lambda s: (s['is_zt'], s['pct'], s['vol_ratio']), reverse=True)
    potentials = [s for s in sigs if 0 < s['pct'] <= POTENTIAL_PCT_MAX and s['vol_ratio'] >= POTENTIAL_VOL_RATIO]
    potentials.sort(key=lambda s: s['vol_ratio'], reverse=True)
    return leaders[:3], potentials[:5]


def _lhb_hits(codes: set, lhb_list: List[Dict]) -> List[Dict]:
    hits = [x for x in (lhb_list or []) if str(x.get('code', '')) in codes]
    return [{'code': x['code'], 'name': x.get('name', ''),
             'net_buy': x.get('net_amount', 0), 'type': x.get('type', '')} for x in hits]


def analyze_event(theme, description: str, tdx, fetcher, trade_date: str) -> Dict:
    """分析单个主题事件。"""
    from .chain import resolve_node_stocks

    chains = []
    all_codes = set()
    for node in theme.chain_nodes:
        codes, source = resolve_node_stocks(node, fetcher)
        all_codes |= set(codes)
        chains.append({'node': node.node, 'codes': codes, 'source': source})

    signals = {}
    for code in sorted(all_codes):
        sig = _stock_signal(tdx, code, trade_date)
        if sig:
            sig['name'] = _lookup_name(code)
            signals[code] = sig

    leaders, potentials = _classify(signals)

    lhb = []
    try:
        lhb = _lhb_hits(all_codes, fetcher.get_lhb(trade_date) if fetcher else [])
    except Exception:
        lhb = []

    return {
        'theme_id': theme.id, 'theme': theme.name, 'description': description,
        'chains': chains,
        'leaders': leaders, 'potentials': potentials,
        'lhb': lhb,
    }
```

> 注：_lookup_name 从 data/stock_name_map.json 读名称（不存在返回 code），在 report.py 或 analyze.py 中实现；Trade date 默认取最新交易日。

- [ ] **Step 4: 运行测试通过**

Run: `python -m pytest ashare_review/tests/test_event_radar.py::test_analyze_layering -v`
Expected: PASS（注意 FakeTdx 需要 _market/_lookup_name 兼容：_lookup_name 对未知 code 返回 code 即可）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/event_radar/analyze.py
git commit -m "feat(event-radar): core analysis with leader/potential layering"
```

---

### Task 6: 报告（report.py）— 结果组装 + Markdown 导出

**Files:**
- Create: `ashare_review/event_radar/report.py`
- Test: `ashare_review/tests/test_event_radar.py`

- [ ] **Step 1: 写失败测试**

```python
def test_report_markdown():
    from ashare_review.event_radar.report import build_result, to_markdown
    result = build_result('2026-08-15', [{
        'theme_id': 'ai', 'theme': 'AI算力', 'description': '数据中心加速',
        'chains': [{'node': '光模块', 'codes': ['300308'], 'source': 'manual'}],
        'leaders': [{'code': '300308', 'name': '中际旭创', 'pct': 9.9, 'vol_ratio': 3.0, 'is_zt': True}],
        'potentials': [{'code': '600001', 'name': '示例', 'pct': 1.0, 'vol_ratio': 2.0}],
        'lhb': [], 'next_day_notes': '关注竞价强度',
    }])
    md = to_markdown(result)
    assert 'AI算力' in md and '光模块' in md and '中际旭创' in md
    assert '|' in md  # 表格存在
    assert '明日关注' in md
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_event_radar.py::test_report_markdown -v`
Expected: FAIL

- [ ] **Step 3: 实现 report.py**

```python
# ashare_review/event_radar/report.py
"""分析结果组装 + Markdown 导出。"""
import json, os
from datetime import date as _date

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'event_radar')
RESULTS_DIR = os.path.join(DATA_DIR, 'results')


def _name_map():
    p = os.path.join(os.path.dirname(DATA_DIR), 'stock_name_map.json')
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return {}


def _lookup_name(code: str) -> str:
    return _name_map().get(code, code)


def build_result(trade_date: str, events: list) -> dict:
    return {'date': trade_date, 'generated_at': _date.today().strftime('%Y-%m-%d %H:%M'),
            'events': events}


def _table(headers: list, rows: list) -> str:
    if not rows:
        return '_（无）_'
    line = '| ' + ' | '.join(headers) + ' |'
    sep = '|' + '---|' * len(headers)
    body = ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join([line, sep] + body)


def to_markdown(result: dict) -> str:
    out = [f'# 📡 事件雷达 · {result["date"]}', '']
    for ev in result.get('events', []):
        out += [f'## {ev["theme"]} — {ev.get("description", "")}', '']
        for ch in ev.get('chains', []):
            out.append(f'**{ch["node"]}**（成分 {len(ch.get("codes", []))} 只 · 来源 {ch.get("source", "-")}）')
        out.append('')
        out.append('### 🚀 龙头股')
        out.append(_table(['代码', '名称', '涨幅%', '量比', '涨停'],
                          [[s['code'], s.get('name', ''), s['pct'], s['vol_ratio'], '✓' if s['is_zt'] else '']
                           for s in ev.get('leaders', [])]))
        out.append('')
        out.append('### ⚡ 潜力股（未大涨但资金启动）')
        out.append(_table(['代码', '名称', '涨幅%', '量比'],
                          [[s['code'], s.get('name', ''), s['pct'], s['vol_ratio']]
                           for s in ev.get('potentials', [])]))
        if ev.get('lhb'):
            out.append('')
            out.append('### 💰 龙虎榜')
            out.append(_table(['代码', '名称', '净买(万)', '类型'],
                              [[x['code'], x['name'], x['net_buy'], x['type']] for x in ev['lhb']]))
        if ev.get('next_day_notes'):
            out += ['', f'### 🎯 明日关注', ev['next_day_notes']]
        out.append('')
    out.append('> ⚠️ 本报告由系统自动生成，不构成投资建议；参与度以公司公告为准。')
    return '\n'.join(out)


def save_result(result: dict, trade_date: str) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    p = os.path.join(RESULTS_DIR, f'{trade_date}.json')
    json.dump(result, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return p


def load_result(trade_date: str) -> dict:
    p = os.path.join(RESULTS_DIR, f'{trade_date}.json')
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding='utf-8'))
```

- [ ] **Step 4: 运行测试通过**

Run: `python -m pytest ashare_review/tests/test_event_radar.py -v`
Expected: PASS（全量）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/event_radar/report.py
git commit -m "feat(event-radar): result assembly and markdown export"
```

---

### Task 7: Web API 路由（app.py）

**Files:**
- Modify: `ashare_review/web/app.py`（在文件末尾、/api/cache/clear 之前新增）

- [ ] **Step 1: 写失败测试（Flask test client）**

```python
def test_radar_themes_api(tmp_path):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ashare_review.web.app import app
    app.config['TESTING'] = True
    c = app.test_client()
    # GET themes（seed 后应有 12 个）
    r = c.get('/api/radar/themes')
    assert r.status_code == 200
    data = r.get_json()
    assert data.get('total', 0) >= 12
    # POST 新增
    r2 = c.post('/api/radar/themes', json={'id': 'test_theme', 'name': '测试主题',
                                           'chain_nodes': [{'node': '测试节点'}]})
    assert r2.status_code == 200 and r2.get_json().get('success')
```

> 注意：测试会写入真实 themes.json（若已 seed）。实现时 ThemesStore 支持注入路径；为保持简单，该测试用真实路径但只新增一个 test_theme 并断言存在；CI/本机可接受。更稳妥：测试结束后 DELETE 该主题。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_event_radar.py::test_radar_themes_api -v`
Expected: FAIL（404）

- [ ] **Step 3: 实现 app.py 路由**

```python
# ── 消息雷达（事件驱动分析） ──

@app.route('/event_radar')
def event_radar():
    """事件驱动分析页面。"""
    from ..event_radar.presets import seed_default_themes
    from ..event_radar.themes import ThemesStore
    store = ThemesStore()
    seed_default_themes(store)
    return render_template('event_radar.html')


@app.route('/api/radar/themes', methods=['GET', 'POST'])
def api_radar_themes():
    from ..event_radar.themes import Theme, ChainNode, ThemesStore
    store = ThemesStore()
    if request.method == 'GET':
        return jsonify({'themes': [t.__dict__ | {'chain_nodes': [n.__dict__ for n in t.chain_nodes]} for t in store.load()],
                        'total': len(store.load())})
    body = request.get_json(silent=True) or {}
    t = Theme(
        id=str(body.get('id', '')).strip(),
        name=str(body.get('name', '')).strip(),
        chain_nodes=[ChainNode(node=str(n.get('node', '')).strip(),
                               concept_name=str(n.get('concept_name', '')).strip(),
                               manual_codes=[str(c) for c in (n.get('manual_codes') or [])])
                     for n in (body.get('chain_nodes') or [])],
    )
    if not t.id or not t.name:
        return jsonify({'success': False, 'error': 'id 和 name 必填'}), 400
    return jsonify({'success': store.add(t)})


@app.route('/api/radar/themes/<theme_id>', methods=['PUT', 'DELETE'])
def api_radar_theme_item(theme_id):
    from ..event_radar.themes import Theme, ChainNode, ThemesStore
    store = ThemesStore()
    if request.method == 'DELETE':
        return jsonify({'success': store.delete(theme_id)})
    body = request.get_json(silent=True) or {}
    t = Theme(id=theme_id, name=str(body.get('name', '')),
              chain_nodes=[ChainNode(node=str(n.get('node', '')).strip(),
                                     concept_name=str(n.get('concept_name', '')).strip(),
                                     manual_codes=[str(c) for c in (n.get('manual_codes') or [])])
                           for n in (body.get('chain_nodes') or [])])
    return jsonify({'success': store.update(theme_id, t)})


@app.route('/api/radar/analyze', methods=['POST'])
def api_radar_analyze():
    """生成分析。body: {date?: str, events: [{theme_id, description}]}"""
    from ..event_radar.themes import ThemesStore
    from ..event_radar.events import RadarEvent, EventsStore
    from ..event_radar.analyze import analyze_event
    from ..event_radar.report import build_result, save_result, _lookup_name

    body = request.get_json(silent=True) or {}
    trade_date = (body.get('date') or '').strip() or datetime.now().strftime('%Y-%m-%d')
    ev_inputs = body.get('events') or []
    if not ev_inputs:
        return jsonify({'success': False, 'error': '请至少选择一个事件'}), 400

    store = ThemesStore()
    estore = EventsStore()
    analyzed = []
    for item in ev_inputs:
        theme = store.get(str(item.get('theme_id', '')))
        if theme is None:
            continue
        desc = str(item.get('description', '')).strip()
        estore.add(RadarEvent(date=trade_date, theme_id=theme.id, description=desc))
        analyzed.append(analyze_event(theme, desc, tdx, ak_fetcher, trade_date))
    if not analyzed:
        return jsonify({'success': False, 'error': '未找到有效主题'}), 400
    result = build_result(trade_date, analyzed)
    save_result(result, trade_date)
    return jsonify({'success': True, 'result': result})


@app.route('/api/radar/results')
def api_radar_results():
    from ..event_radar.report import load_result
    d = (request.args.get('date') or '').strip() or datetime.now().strftime('%Y-%m-%d')
    r = load_result(d)
    return jsonify({'date': d, 'result': r})


@app.route('/api/radar/export')
def api_radar_export():
    from ..event_radar.report import load_result, to_markdown
    d = (request.args.get('date') or '').strip() or datetime.now().strftime('%Y-%m-%d')
    r = load_result(d)
    if r is None:
        return jsonify({'error': f'{d} 无分析结果'}), 404
    md = to_markdown(r)
    # 顺手写 outputs/
    try:
        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'outputs')
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, f'事件雷达_{d}.md')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(md)
        saved = p
    except Exception:
        saved = None
    return jsonify({'success': True, 'markdown': md, 'saved': saved})
```

- [ ] **Step 4: 运行测试通过**

Run: `python -m pytest ashare_review/tests/test_event_radar.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ashare_review/web/app.py
git commit -m "feat(event-radar): web API endpoints"
```

---

### Task 8: 雷达页面（event_radar.html）+ 导航接入

**Files:**
- Create: `ashare_review/web/templates/event_radar.html`
- Modify: `ashare_review/web/templates/base.html`（导航加"消息雷达"，放"持仓与复盘"分组）

- [ ] **Step 1: 写页面（Vanilla JS + 设计系统类，含 esc() 转义）**

页面结构（继承 base.html）：
```html
{% extends "base.html" %}
{% block title %}消息雷达 · 竞价交易系统{% endblock %}
{% block content %}
<div class="content-area">
  <div class="page-header">
    <div>
      <div class="page-title">📡 消息雷达</div>
      <div class="page-date">事件 → 产业 → 公司 → 资金 → 股价 · {{ today }}</div>
    </div>
    <div style="display:flex;gap:10px;">
      <button class="btn btn-secondary" onclick="loadThemes()">刷新主题</button>
      <button class="btn btn-primary" onclick="runAnalyze()">⚡ 生成分析</button>
      <button class="btn btn-secondary" onclick="exportReport()">📄 导出</button>
    </div>
  </div>
  <div class="grid-2" style="align-items:start;">
    <div class="card">
      <div class="card-header">🗂 主题库 <span class="card-badge" id="theme-count">0</span></div>
      <div class="card-body" id="theme-list" style="max-height:70vh;overflow-y:auto;"></div>
    </div>
    <div class="card">
      <div class="card-header">📊 分析结果 <span class="card-badge" id="result-date"></span></div>
      <div class="card-body" id="result-panel">
        <div class="empty-result"><div class="empty-icon">📡</div><h3>尚未生成分析</h3><p>左侧勾选今日事件 → 点击「生成分析」</p></div>
      </div>
    </div>
  </div>
</div>
{% endblock %}
{% block scripts %}
<script>
// esc() 转义助手（XSS 纵深防御）
function esc(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
}
let SELECTED = {};   // theme_id -> description

async function loadThemes() {
    const resp = await fetch('/api/radar/themes');
    const data = await resp.json();
    document.getElementById('theme-count').textContent = data.total;
    document.getElementById('theme-list').innerHTML = data.themes.map(t => {
        const nodes = (t.chain_nodes || []).map(n => '<span class="reason-tag">' + esc(n.node) + '</span>').join('');
        const checked = SELECTED[t.id] !== undefined ? 'checked' : '';
        return '<div class="card" style="margin-bottom:10px;">' +
          '<div class="card-body" style="padding:12px 14px;">' +
          '<label style="display:flex;align-items:center;gap:8px;font-weight:700;">' +
          '<input type="checkbox" data-theme="' + esc(t.id) + '" ' + checked + '> ' + esc(t.name) + '</label>' +
          '<div style="margin:8px 0 4px;">' + nodes + '</div>' +
          '<input type="text" placeholder="今日事件描述（可选）" data-desc="' + esc(t.id) + '" value="' + esc(SELECTED[t.id] || '') + '" ' +
          'style="width:100%;padding:6px 10px;font-size:0.85em;">' +
          '</div></div>';
    }).join('');
    // 绑定事件
    document.querySelectorAll('#theme-list input[data-theme]').forEach(cb => {
        cb.addEventListener('change', () => {
            const id = cb.dataset.theme;
            if (cb.checked) SELECTED[id] = SELECTED[id] || '';
            else delete SELECTED[id];
        });
    });
    document.querySelectorAll('#theme-list input[data-desc]').forEach(inp => {
        inp.addEventListener('input', () => { SELECTED[inp.dataset.desc] = inp.value; });
    });
}

async function runAnalyze() {
    const ids = Object.keys(SELECTED);
    if (!ids.length) { alert('请先勾选今日事件主题'); return; }
    const events = ids.map(id => ({theme_id: id, description: SELECTED[id] || ''}));
    const resp = await fetch('/api/radar/analyze', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({events: events})
    });
    const data = await resp.json();
    if (!data.success) { alert(data.error || '分析失败'); return; }
    renderResult(data.result);
}

function renderResult(result) {
    document.getElementById('result-date').textContent = result.date;
    const html = result.events.map(ev => {
        const chains = (ev.chains || []).map(ch =>
            '<tr><td>' + esc(ch.node) + '</td><td>' + (ch.codes || []).length + ' 只</td><td>' + esc(ch.source) + '</td></tr>').join('');
        const leaders = (ev.leaders || []).map(s =>
            '<tr><td><a href="/stock/' + esc(s.code) + '">' + esc(s.code) + '</a></td><td>' + esc(s.name || '') +
            '</td><td class="up">' + s.pct + '%</td><td>' + s.vol_ratio + '</td><td>' + (s.is_zt ? '✓' : '') + '</td></tr>').join('');
        const pots = (ev.potentials || []).map(s =>
            '<tr><td><a href="/stock/' + esc(s.code) + '">' + esc(s.code) + '</a></td><td>' + esc(s.name || '') +
            '</td><td class="up">' + s.pct + '%</td><td>' + s.vol_ratio + '</td></tr>').join('');
        return '<div class="card" style="margin-bottom:14px;">' +
          '<div class="card-header">' + esc(ev.theme) + (ev.description ? ' — ' + esc(ev.description) : '') + '</div>' +
          '<div class="card-body">' +
          '<div class="section-label">产业链</div><div class="table-wrap"><table><tr><th>节点</th><th>成分</th><th>来源</th></tr>' + chains + '</table></div>' +
          '<div class="section-label" style="margin-top:12px;">🚀 龙头股</div><div class="table-wrap"><table><tr><th>代码</th><th>名称</th><th>涨幅</th><th>量比</th><th>涨停</th></tr>' + leaders + '</table></div>' +
          '<div class="section-label" style="margin-top:12px;">⚡ 潜力股</div><div class="table-wrap"><table><tr><th>代码</th><th>名称</th><th>涨幅</th><th>量比</th></tr>' + pots + '</table></div>' +
          (ev.next_day_notes ? '<div class="notice-banner" style="margin-top:12px;">🎯 ' + esc(ev.next_day_notes) + '</div>' : '') +
          '</div></div>';
    }).join('');
    document.getElementById('result-panel').innerHTML = html || '<div class="empty-result"><p>无结果</p></div>';
}

async function exportReport() {
    const resp = await fetch('/api/radar/export');
    const data = await resp.json();
    if (data.error) { alert(data.error); return; }
    alert('已导出：' + (data.saved || '（仅返回内容）'));
}

document.addEventListener('DOMContentLoaded', loadThemes);
</script>
{% endblock %}
```

> 注：today 变量在 app.py 的 event_radar 路由中传入（参考 index 路由）。页面沿用设计系统（.card/.card-header/.section-label/.reason-tag/.table-wrap/.btn）。

- [ ] **Step 2: base.html 导航加入口**

在"持仓与复盘"分组内、个股分析前插入：
```html
<a href="/event_radar" class="nav-item {% if request.endpoint == 'event_radar' %}active{% endif %}">
    <span class="nav-icon">📡</span>
    <span class="nav-label">消息雷达</span>
</a>
```

- [ ] **Step 3: 手动冒烟验证**

Run: `python run.py` → 打开 `http://127.0.0.1:5000/event_radar`
Expected: 页面渲染、主题库 12 个卡片、勾选后点"生成分析"出结果（网络不可用时潜力/龙头为空但页面不报错）

- [ ] **Step 4: 提交**

```bash
git add ashare_review/web/templates/event_radar.html ashare_review/web/templates/base.html ashare_review/web/app.py
git commit -m "feat(event-radar): radar page and navigation"
```

---

### Task 9: 集成验证 + 全量测试 + 文档

**Files:** 无新增（验证 + 收尾）

- [ ] **Step 1: 全量测试**

Run: `python -m pytest ashare_review/tests -q --no-header -p no:cacheprovider`
Expected: 全部通过（原有 34 + event_radar 新增）

- [ ] **Step 2: Jinja 语法 + 路由冒烟**

Run: `python -c "import jinja2; [jinja2.Environment().parse(open(f,encoding='utf-8').read()) for f in __import__('glob').glob('ashare_review/web/templates/*.html')]; print('templates OK')"`
Expected: templates OK
Run: 启动应用，curl `/event_radar`、`/api/radar/themes` 返回 200

- [ ] **Step 3: 提交（含数据文件策略确认）**

```bash
git add -A
git commit -m "feat(event-radar): integration and validation"
```
（data/event_radar/ 运行时生成：themes.json 可提交（预置内容），events.jsonl/results/ 可忽略或提交由实现决定——建议 themes.json 提交，其余 .gitignore）

- [ ] **Step 4: 推送**

```bash
git push origin main
```

---

## 验收标准

1. `/event_radar` 页面可用：主题库 12 个可勾选、可新增自定义主题、可编辑节点
2. 勾选 1-3 个主题 → 生成分析：产业链表 + 龙头股 + 潜力股 + 龙虎榜 + 明日要点
3. 离线（东财不可达）时：潜力/龙头基于 manual_codes + TDX 仍可产出，页面不报错
4. 导出 Markdown 到 outputs/事件雷达_日期.md
5. 全量 pytest 通过；XSS 转义（esc()）覆盖所有 innerHTML 插值
```
