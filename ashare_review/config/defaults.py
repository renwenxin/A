"""默认配置常量：不需要 YAML 文件也能运行，所有值有合理默认"""
from dataclasses import dataclass

@dataclass
class LLMProviderConfig:
    model: str
    api_key_env: str = ''
    base_url: str = ''
    timeout: int = 60

@dataclass
class FeatureConfig:
    agents: bool = True
    alpha: bool = True
    nl_strategy: bool = True

DEFAULT_PROVIDERS = {
    'deepseek': LLMProviderConfig(
        model='deepseek-chat',
        api_key_env='DEEPSEEK_API_KEY',
        base_url='https://api.deepseek.com/v1',
    ),
    'claude': LLMProviderConfig(
        model='claude-sonnet-4-6',
        api_key_env='ANTHROPIC_API_KEY',
        base_url='https://api.anthropic.com',
        timeout=120,
    ),
    # 本地 Ollama（OpenAI 兼容接口，无需 API Key）
    'ollama': LLMProviderConfig(
        model='qwen3:8b',
        api_key_env='OLLAMA_API_KEY',
        base_url='http://localhost:11434/v1',
        timeout=180,
    ),
}

AGENT_DEFAULTS = {
    'default_provider': 'deepseek',
    'temperature': 0.3,
    'max_tokens': 2048,
    'max_parallel': 5,
    'timeout_seconds': 120,
}
