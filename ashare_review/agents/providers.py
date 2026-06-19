"""LLM Provider 抽象层 — 支持 Claude / DeepSeek / OpenAI"""
import json
import os
import httpx
from abc import ABC, abstractmethod
from typing import AsyncIterator


class LLMProvider(ABC):
    def __init__(self, model: str, api_key: str, base_url: str = '', timeout: int = 60):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    @abstractmethod
    async def chat(self, messages: list[dict], **kwargs) -> str: ...

    @abstractmethod
    async def chat_stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]: ...

    @staticmethod
    def _build_headers(api_key: str) -> dict:
        return {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容 API（DeepSeek, OpenAI, 大部分国产模型都用这个格式）"""

    async def chat(self, messages: list[dict], **kwargs) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions" if self.base_url else \
              "https://api.deepseek.com/v1/chat/completions"
        body = {
            'model': self.model,
            'messages': messages,
            'temperature': kwargs.get('temperature', 0.3),
            'max_tokens': kwargs.get('max_tokens', 2048),
        }
        if 'response_format' in kwargs:
            body['response_format'] = kwargs['response_format']
        if 'tools' in kwargs:
            body['tools'] = kwargs['tools']

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=body,
                                     headers=self._build_headers(self.api_key))
            resp.raise_for_status()
            data = resp.json()
            return data['choices'][0]['message']['content']

    async def chat_stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        url = f"{self.base_url.rstrip('/')}/chat/completions" if self.base_url else \
              "https://api.deepseek.com/v1/chat/completions"
        body = {
            'model': self.model, 'messages': messages,
            'temperature': kwargs.get('temperature', 0.3),
            'max_tokens': kwargs.get('max_tokens', 2048),
            'stream': True,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream('POST', url, json=body,
                                      headers=self._build_headers(self.api_key)) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith('data: '):
                        data_str = line[6:]
                        if data_str == '[DONE]':
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data['choices'][0].get('delta', {})
                            content = delta.get('content', '')
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue

    def chat_sync(self, messages: list[dict], **kwargs) -> str:
        """同步包装器 — orchestrator 在线程池中使用"""
        import asyncio
        return asyncio.run(self.chat(messages, **kwargs))


from ..config import get_config


def create_provider(provider_name: str = None) -> LLMProvider:
    """工厂函数：从配置加载 Provider"""
    cfg = get_config()
    name = provider_name or cfg.default_provider
    api_key = cfg.get_api_key(name)
    provider_cfg = cfg.providers.get(name)

    if provider_cfg is None:
        from ..config.defaults import DEFAULT_PROVIDERS
        provider_cfg = DEFAULT_PROVIDERS.get(name, DEFAULT_PROVIDERS['deepseek'])

    return OpenAICompatProvider(
        model=provider_cfg.model,
        api_key=api_key,
        base_url=provider_cfg.base_url,
        timeout=provider_cfg.timeout,
    )
