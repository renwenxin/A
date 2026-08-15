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
