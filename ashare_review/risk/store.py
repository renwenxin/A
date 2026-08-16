"""风控规则 — 共享配置持久化（原子写，损坏回退默认）"""
import json
import logging
import os
import tempfile
from typing import Dict, Optional

from .rules import DEFAULT_CONFIG, validate_config

logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get(
    'RISK_CONFIG',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'data', 'risk_config.json'))


def _merge_default(portfolio_id: str, saved: dict) -> dict:
    """已保存配置与默认合并：缺字段用默认。"""
    base = dict(DEFAULT_CONFIG[portfolio_id])
    for k, v in (saved or {}).items():
        if k == 'regime_scale' and isinstance(v, dict):
            merged = dict(base['regime_scale'])
            merged.update(v)
            base['regime_scale'] = merged
        else:
            base[k] = v
    return base


class RiskStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or CONFIG_PATH

    def _load_raw(self) -> dict:
        try:
            if os.path.exists(self.path):
                with open(self.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning('风控配置读取失败 %s（%s），回退默认', self.path, e)
        return {}

    def get(self, portfolio_id: str) -> dict:
        raw = self._load_raw()
        saved = raw.get(portfolio_id)
        cfg = _merge_default(portfolio_id, saved)
        return cfg

    def get_all(self) -> Dict[str, dict]:
        return {pid: self.get(pid) for pid in DEFAULT_CONFIG}

    def set(self, portfolio_id: str, cfg: dict) -> None:
        """校验并保存单份配置（缺字段用默认补齐）。非法抛 ValueError。"""
        if portfolio_id not in DEFAULT_CONFIG:
            raise ValueError(f'未知持仓: {portfolio_id}')
        full = _merge_default(portfolio_id, cfg)
        errors = validate_config(portfolio_id, full)
        if errors:
            raise ValueError('; '.join(errors))
        raw = self._load_raw()
        raw[portfolio_id] = full
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or '.',
                                   suffix='.tmp', prefix='risk_config_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
