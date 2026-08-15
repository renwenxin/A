"""事件记录：events.jsonl 追加 + 按日查询。"""
import json, os
from dataclasses import dataclass, asdict
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
            f.write(json.dumps(asdict(ev), ensure_ascii=False) + '
')

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
