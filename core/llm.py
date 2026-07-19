"""项目内所有 Agent 共用的 JSON LLM 客户端。"""
from __future__ import annotations

import json
from typing import Any, Protocol

from openai import OpenAI

from .config import LLMConfig


class JSONLLM(Protocol):
    def chat_json(
        self, system: str, user: str, *, name: str = "call", temperature: float = 0.3
    ) -> dict[str, Any]: ...


class OpenAILLM:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)

    def chat_json(
        self, system: str, user: str, *, name: str = "call", temperature: float = 0.3
    ) -> dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.config.model,
            temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"[{name}] 模型没有返回合法 JSON：{content[:500]}") from error
