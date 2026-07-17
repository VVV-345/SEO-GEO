"""OpenAI 兼容的 LLM 客户端封装。所有 agent 共用。

设计原则：只用 `response_format={"type": "json_object"}` 的 JSON 模式，
不依赖各厂商未必支持的 strict structured output / pydantic parse，
保证在 DeepSeek / 智谱 / 通义 / Moonshot / OpenAI 上都能跑。
"""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from .config import LLMConfig


class OpenAILLM:
    """OpenAI 兼容客户端，对外只暴露 chat_json。"""

    def __init__(self, cfg: LLMConfig, *, default_temperature: float = 0.5):
        self._cfg = cfg
        self._default_temperature = default_temperature
        self._client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        name: str = "call",
        temperature: float | None = None,
    ) -> dict[str, Any]:
        resp = self._client.chat.completions.create(
            model=self._cfg.model,
            temperature=self._default_temperature if temperature is None else temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"[{name}] LLM 返回的不是合法 JSON：{e}\n原始内容（前 500 字）：{content[:500]}"
            ) from e
