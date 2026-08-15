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
