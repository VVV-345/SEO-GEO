"""项目内所有 Agent 共用的 JSON LLM 客户端。"""
from __future__ import annotations

import json
from typing import Any, Protocol

from openai import OpenAI

from .config import LLMConfig


class JSONLLM(Protocol):
    """所有 Agent 所需的最小 JSON 模型接口，便于替换供应商或 Mock。"""

    def chat_json(
        self, system: str, user: str, *, name: str = "call", temperature: float = 0.3
    ) -> dict[str, Any]:
        """发送系统与用户消息，并返回解析后的 JSON 对象。"""
        ...


class OpenAILLM:
    """基于 OpenAI-compatible Chat Completions 的 JSON 客户端。"""

    def __init__(self, config: LLMConfig):
        """根据配置初始化同步客户端；base_url 可指向 DeepSeek 等服务。"""
        self.config = config
        self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)

    def chat_json(
        self, system: str, user: str, *, name: str = "call", temperature: float = 0.3
    ) -> dict[str, Any]:
        """调用模型的 JSON 输出模式，并对非法 JSON 给出可定位错误。"""
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
