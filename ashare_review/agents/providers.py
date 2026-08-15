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

    @abstractmethod
    def chat_sync(self, messages: list[dict], **kwargs) -> str: ...

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
        """同步包装器 — 在线程中使用，安全创建事件循环"""
        import asyncio
        import threading
        try:
            # 如果当前线程已有运行中的事件循环，直接使用
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # 在线程中运行新的 event loop（避免嵌套）
            result_container = []
            exception_container = []

            def _run_in_thread():
                try:
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    result_container.append(new_loop.run_until_complete(
                        self.chat(messages, **kwargs)))
                except Exception as e:
                    exception_container.append(e)
                finally:
                    new_loop.close()

            t = threading.Thread(target=_run_in_thread, daemon=True)
            t.start()
            t.join(timeout=self.timeout + 10)
            if exception_container:
                raise exception_container[0]
            if result_container:
                return result_container[0]
            raise RuntimeError("Provider call timed out")
        else:
            return asyncio.run(self.chat(messages, **kwargs))


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API（Messages API 原生格式）

    将 OpenAI 兼容的 messages 格式自动转换为 Anthropic 格式：
    - system 消息提取到顶层 system 参数
    - user/assistant 消息按 Anthropic 格式组装
    - response_format 转换为 Anthropic tool_use 预填
    """

    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, model: str, api_key: str, base_url: str = '', timeout: int = 120):
        super().__init__(model, api_key,
                         base_url or "https://api.anthropic.com",
                         timeout)

    def _build_anthropic_headers(self) -> dict:
        return {
            'x-api-key': self.api_key,
            'anthropic-version': self.ANTHROPIC_VERSION,
            'Content-Type': 'application/json',
        }

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """将 OpenAI 格式 messages 转为 Anthropic 格式

        Returns:
            (system_prompt, anthropic_messages)
        """
        system_parts = []
        anthropic_msgs = []

        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')

            if role == 'system':
                system_parts.append(content)
            elif role in ('user', 'assistant'):
                anthropic_msgs.append({'role': role, 'content': content})
            elif role == 'function' or role == 'tool':
                # 工具返回结果 → 作为 user 消息
                anthropic_msgs.append({
                    'role': 'user',
                    'content': f"[工具返回] {content}"
                })

        system_prompt = '\n\n'.join(system_parts) if system_parts else ''
        return system_prompt, anthropic_msgs

    async def chat(self, messages: list[dict], **kwargs) -> str:
        system_prompt, anthropic_msgs = self._convert_messages(messages)

        body = {
            'model': self.model,
            'messages': anthropic_msgs,
            'max_tokens': kwargs.get('max_tokens', 2048),
            'temperature': kwargs.get('temperature', 0.3),
        }
        if system_prompt:
            body['system'] = system_prompt

        # response_format → 指导模型输出 JSON 的 system 指令
        response_format = kwargs.get('response_format')
        if response_format and isinstance(response_format, dict):
            schema = response_format.get('schema', {})
            if schema:
                json_instruction = (
                    "You MUST respond with valid JSON that matches this schema:\n"
                    f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
                    "Respond ONLY with the JSON object, no other text."
                )
                if body.get('system'):
                    body['system'] = body['system'] + '\n\n' + json_instruction
                else:
                    body['system'] = json_instruction

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url.rstrip('/')}/v1/messages",
                json=body,
                headers=self._build_anthropic_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

            # Anthropic 响应格式: content[0].text
            content_block = data.get('content', [{}])
            if content_block and isinstance(content_block, list):
                return content_block[0].get('text', '')
            return ''

    async def chat_stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        system_prompt, anthropic_msgs = self._convert_messages(messages)

        body = {
            'model': self.model,
            'messages': anthropic_msgs,
            'max_tokens': kwargs.get('max_tokens', 2048),
            'temperature': kwargs.get('temperature', 0.3),
            'stream': True,
        }
        if system_prompt:
            body['system'] = system_prompt

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                'POST',
                f"{self.base_url.rstrip('/')}/v1/messages",
                json=body,
                headers=self._build_anthropic_headers(),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith('data: '):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)
                            if data.get('type') == 'content_block_delta':
                                delta = data.get('delta', {})
                                text = delta.get('text', '')
                                if text:
                                    yield text
                            elif data.get('type') == 'message_stop':
                                break
                        except (json.JSONDecodeError, KeyError):
                            continue

    def chat_sync(self, messages: list[dict], **kwargs) -> str:
        import asyncio
        import threading
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            result_container = []
            exception_container = []

            def _run_in_thread():
                try:
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    result_container.append(new_loop.run_until_complete(
                        self.chat(messages, **kwargs)))
                except Exception as e:
                    exception_container.append(e)
                finally:
                    new_loop.close()

            t = threading.Thread(target=_run_in_thread, daemon=True)
            t.start()
            t.join(timeout=self.timeout + 10)
            if exception_container:
                raise exception_container[0]
            if result_container:
                return result_container[0]
            raise RuntimeError("Provider call timed out")
        else:
            return asyncio.run(self.chat(messages, **kwargs))


from ..config import get_config


def create_provider(provider_name: str = None) -> LLMProvider:
    """工厂函数：从配置加载 Provider

    自动检测 Provider 类型：
    - Anthropic/Claude → 使用 AnthropicProvider（原生 Messages API）
    - 其他（DeepSeek/OpenAI 等）→ 使用 OpenAICompatProvider
    """
    cfg = get_config()
    name = provider_name or cfg.default_provider
    api_key = cfg.get_api_key(name)

    if not api_key:
        if name == 'ollama':
            # 本地 Ollama 无需鉴权，占位即可（服务端会忽略该头）
            api_key = 'ollama-local'
        else:
            # 给出明确的错误提示而非静默失败
            from ..config.defaults import DEFAULT_PROVIDERS
            provider_cfg = DEFAULT_PROVIDERS.get(name)
            env_var = provider_cfg.api_key_env if provider_cfg else f'{name.upper()}_API_KEY'
            raise ValueError(
                f"API Key 未配置！请设置环境变量 {env_var}，"
                f"或在 .env 文件中添加 {env_var}=your_key"
            )

    provider_cfg = cfg.providers.get(name)
    if provider_cfg is None:
        from ..config.defaults import DEFAULT_PROVIDERS
        provider_cfg = DEFAULT_PROVIDERS.get(name, DEFAULT_PROVIDERS['deepseek'])

    # 判断是否为 Anthropic/Claude provider
    is_anthropic = (
        name == 'claude' or
        'anthropic' in (provider_cfg.base_url or '').lower() or
        'claude' in (provider_cfg.model or '').lower()
    )

    if is_anthropic:
        return AnthropicProvider(
            model=provider_cfg.model,
            api_key=api_key,
            base_url=provider_cfg.base_url,
            timeout=provider_cfg.timeout,
        )

    return OpenAICompatProvider(
        model=provider_cfg.model,
        api_key=api_key,
        base_url=provider_cfg.base_url,
        timeout=provider_cfg.timeout,
    )
