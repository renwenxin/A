"""事件雷达（消息雷达）单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Task 1: ThemesStore ----------

def test_theme_dataclass_roundtrip():
    from ashare_review.event_radar.themes import Theme, ChainNode, ThemesStore
    t = Theme(id='ai', name='AI算力',
              chain_nodes=[ChainNode(node='液冷服务器', concept_name='液冷服务器', manual_codes=['000977'])])
    d = ThemesStore._to_dict(t)
    t2 = ThemesStore._from_dict(d)
    assert t2.id == 'ai' and t2.name == 'AI算力'
    assert t2.chain_nodes[0].node == '液冷服务器'
    assert t2.chain_nodes[0].manual_codes == ['000977']


def test_themes_crud(tmp_path):
    from ashare_review.event_radar.themes import Theme, ChainNode, ThemesStore
    store = ThemesStore(path=str(tmp_path / 'themes.json'))
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
    from ashare_review.event_radar.themes import Theme, ThemesStore
    store = ThemesStore(path=str(tmp_path / 'themes.json'))
    store.add(Theme(id='x', name='X', chain_nodes=[]))
    store2 = ThemesStore(path=str(tmp_path / 'themes.json'))
    assert store2.load()[0].id == 'x'


def test_theme_id_duplicate_rejected(tmp_path):
    from ashare_review.event_radar.themes import Theme, ThemesStore
    store = ThemesStore(path=str(tmp_path / 'themes.json'))
    assert store.add(Theme(id='a', name='A', chain_nodes=[])) is True
    assert store.add(Theme(id='a', name='A2', chain_nodes=[])) is False


# ---------- Task 2: EventsStore ----------

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


# ---------- Task 3: 预置主题库 ----------

def test_presets_valid():
    from ashare_review.event_radar.presets import PRESET_THEMES
    assert len(PRESET_THEMES) == 12
    ids = [t.id for t in PRESET_THEMES]
    assert len(set(ids)) == len(ids)
    for t in PRESET_THEMES:
        assert t.name and len(t.chain_nodes) >= 2
        for n in t.chain_nodes:
            assert n.node


def test_seed_default_themes(tmp_path):
    from ashare_review.event_radar.presets import seed_default_themes
    from ashare_review.event_radar.themes import ThemesStore
    store = ThemesStore(path=str(tmp_path / 'themes.json'))
    assert seed_default_themes(store) == 12
    assert seed_default_themes(store) == 0
    assert len(store.load()) == 12


# ---------- Task 4: 产业链展开 ----------

def test_resolve_node_stocks_fallback():
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


# ---------- Task 5: 分析核心分层 ----------

class FakeTdx:
    """返回可控信号的假 TDX：code -> dict(close, prev_close, volume, vol_ma5)"""
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

    data = {
        '600001': {'close': 10.0, 'prev_close': 9.0, 'volume': 300, 'vol_ma5': 100},   # +11.1% 涨停
        '600002': {'close': 10.0, 'prev_close': 9.3, 'volume': 250, 'vol_ma5': 100},   # +7.5% 大阳
        '600003': {'close': 10.0, 'prev_close': 9.9, 'volume': 200, 'vol_ma5': 100},   # +1.0% 量比2.0
        '600004': {'close': 10.0, 'prev_close': 9.8, 'volume': 160, 'vol_ma5': 100},   # +2.0% 量比1.6
        '600005': {'close': 10.0, 'prev_close': 10.0, 'volume': 100, 'vol_ma5': 100},  # 0% 量比1.0 → 排除
    }
    tdx = FakeTdx(data)
    sigs = {c: _stock_signal(tdx, c, '2026-08-15') for c in data}
    leaders, potentials = _classify(sigs)
    assert {s['code'] for s in leaders} == {'600001', '600002'}
    assert {s['code'] for s in potentials} == {'600003', '600004'}
    assert potentials[0]['code'] == '600003'   # 量比高者优先


def test_stock_signal_values():
    from ashare_review.event_radar.analyze import _stock_signal
    tdx = FakeTdx({'600001': {'close': 10.9, 'prev_close': 10.0, 'volume': 200, 'vol_ma5': 100}})
    s = _stock_signal(tdx, '600001', '2026-08-15')
    assert s['pct'] == 9.0
    assert s['vol_ratio'] == 2.0
    assert s['is_zt'] is False  # 9.0% < 9.5%


# ---------- Task 6: 报告与 Markdown ----------

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
    assert '|' in md
    assert '明日关注' in md


def test_report_save_load(tmp_path):
    from ashare_review.event_radar.report import save_result, load_result
    r = {'date': '2026-08-15', 'generated_at': 'x', 'events': []}
    p = save_result(r, '2026-08-15')
    assert p.endswith('2026-08-15.json')
    r2 = load_result('2026-08-15')
    assert r2['date'] == '2026-08-15'
    assert load_result('1999-01-01') is None


# ---------- Task 7: Web API ----------

def test_radar_themes_api():
    from ashare_review.web.app import app
    app.config['TESTING'] = True
    c = app.test_client()
    r = c.get('/api/radar/themes')
    assert r.status_code == 200
    data = r.get_json()
    assert data.get('total', 0) >= 12
    # 新增 + 清理
    r2 = c.post('/api/radar/themes', json={'id': 'test_theme', 'name': '测试主题',
                                           'chain_nodes': [{'node': '测试节点'}]})
    assert r2.status_code == 200 and r2.get_json().get('success')
    c.delete('/api/radar/themes/test_theme')
