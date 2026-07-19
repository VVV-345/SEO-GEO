"""从环境变量或 .env 读取 OpenAI-compatible 模型配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class LLMConfig:
    base_url: str | None
    api_key: str
    model: str


def load_llm_config() -> LLMConfig:
    """读取默认模型配置，供关键词、写作等 Agent 共用。"""
    base_url = os.getenv("LLM_BASE_URL", "").strip() or None
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not api_key or not model:
        raise RuntimeError("请在 .env 中设置 LLM_API_KEY 和 LLM_MODEL；LLM_BASE_URL 可选。")
    return LLMConfig(base_url=base_url, api_key=api_key, model=model)


def load_competitor_llm_config() -> LLMConfig:
    """读取竞品 Agent 的模型配置，未设置时回退到默认 ``LLM_*``。

    可在 .env 使用 ``COMPETITOR_LLM_API_KEY``、``COMPETITOR_LLM_MODEL`` 和
    ``COMPETITOR_LLM_BASE_URL`` 为竞品分析选择另一个 OpenAI-compatible 模型。
    """
    default = load_llm_config()
    return LLMConfig(
        base_url=os.getenv("COMPETITOR_LLM_BASE_URL", "").strip() or default.base_url,
        api_key=os.getenv("COMPETITOR_LLM_API_KEY", "").strip() or default.api_key,
        model=os.getenv("COMPETITOR_LLM_MODEL", "").strip() or default.model,
    )
