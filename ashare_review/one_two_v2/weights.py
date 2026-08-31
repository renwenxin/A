# ashare_review/one_two_v2/weights.py
"""今日1进2 — 可配权重与阈值（视频方法论默认值，页面可调）"""
import json
import logging
import os
import tempfile
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DIMENSIONS = ['quality', 'theme_stage', 'emotion', 'energy_ladder',
              'volume_health', 'theme_overlay', 'cap_price', 'status']

DEFAULT_WEIGHTS: Dict[str, Dict] = {
    'dimensions': {
        'quality': 30, 'theme_stage': 15, 'emotion': 10, 'energy_ladder': 10,
        'volume_health': 15, 'theme_overlay': 8, 'cap_price': 7, 'status': 5,
    },
    'thresholds': {
        'auction_ratio_high': 10.0, 'auction_ratio_mid': 5.0, 'auction_ratio_low': 3.0,
        'volume_health_pct': 80.0, 'cap_max': 100.0, 'price_max': 15.0,
    },
}

_WEIGHTS_PATH = os.environ.get(
    'ONE_TWO_WEIGHTS',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'data', 'one_two_weights.json'))
_LOCK = threading.Lock()


def _merge_default(saved: Optional[dict]) -> dict:
    base = {'dimensions': dict(DEFAULT_WEIGHTS['dimensions']),
            'thresholds': dict(DEFAULT_WEIGHTS['thresholds'])}
    if not isinstance(saved, dict):
        return base
    if isinstance(saved.get('dimensions'), dict):
        for k, v in saved['dimensions'].items():
            if k in base['dimensions'] and isinstance(v, (int, float)) and not isinstance(v, bool):
                base['dimensions'][k] = v
    if isinstance(saved.get('thresholds'), dict):
        for k, v in saved['thresholds'].items():
            if k in base['thresholds'] and isinstance(v, (int, float)) and not isinstance(v, bool):
                base['thresholds'][k] = v
    return base


def validate_weights(weights: dict) -> List[str]:
    errors = []
    dims = weights.get('dimensions')
    if not isinstance(dims, dict):
        return ['dimensions 必须为对象']
    for k in DIMENSIONS:
        v = dims.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0 <= v <= 50):
            errors.append(f'dimensions.{k} 需在 0~50 之间（当前 {v}）')
    th = weights.get('thresholds')
    if not isinstance(th, dict):
        errors.append('thresholds 必须为对象')
    else:
        for k in ('auction_ratio_high', 'auction_ratio_mid', 'auction_ratio_low',
                  'volume_health_pct', 'cap_max', 'price_max'):
            v = th.get(k)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                errors.append(f'thresholds.{k} 必须 >0（当前 {v}）')
        if all(isinstance(th.get(k), (int, float)) and not isinstance(th.get(k), bool)
               for k in ('auction_ratio_high', 'auction_ratio_mid', 'auction_ratio_low')):
            if not (th['auction_ratio_low'] <= th['auction_ratio_mid'] <= th['auction_ratio_high']):
                errors.append('thresholds 需 low ≤ mid ≤ high')
    return errors


class WeightStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or _WEIGHTS_PATH

    def _load_raw(self) -> dict:
        try:
            if os.path.exists(self.path):
                with open(self.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning('1进2 权重读取失败 %s（%s），回退默认', self.path, e)
        return {}

    def get(self) -> dict:
        return _merge_default(self._load_raw().get('weights'))

    def set(self, weights: dict) -> None:
        full = _merge_default(weights)
        errors = validate_weights(full)
        if errors:
            raise ValueError('; '.join(errors))
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        with _LOCK:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or '.',
                                       suffix='.tmp', prefix='one_two_weights_')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump({'weights': full}, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except Exception:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
