"""配置加载器 — 无外部依赖，启动零失败"""
import os
from .defaults import (
    FeatureConfig, LLMProviderConfig,
    DEFAULT_PROVIDERS, AGENT_DEFAULTS,
)

class ConfigLoader:
    def __init__(self):
        self.features = FeatureConfig()
        self.providers: dict[str, LLMProviderConfig] = {}
        self._load()

    def _load(self):
        for name, cfg in DEFAULT_PROVIDERS.items():
            api_key = os.getenv(cfg.api_key_env, '')
            self.providers[name] = LLMProviderConfig(
                model=cfg.model,
                api_key_env=cfg.api_key_env,
                base_url=cfg.base_url,
                timeout=cfg.timeout,
            )
            # Store resolved api_key for quick access
            self.providers[name]._resolved_key = api_key

    def get_api_key(self, provider: str) -> str:
        p = self.providers.get(provider)
        if p is None:
            return ''
        return os.getenv(p.api_key_env, '')

    def get_agent_defaults(self) -> dict:
        return dict(AGENT_DEFAULTS)

    @property
    def default_provider(self) -> str:
        return AGENT_DEFAULTS['default_provider']

    @property
    def default_model(self) -> str:
        return self.providers.get(self.default_provider, DEFAULT_PROVIDERS['deepseek']).model
