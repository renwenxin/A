"""配置管理 — 功能开关 & LLM Provider. 自动从环境变量读取 API Key."""
from .loader import ConfigLoader

_config = None

def get_config() -> 'ConfigLoader':
    global _config
    if _config is None:
        _config = ConfigLoader()
    return _config
