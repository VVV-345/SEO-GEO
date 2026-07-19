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
    base_url = os.getenv("LLM_BASE_URL", "").strip() or None
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not api_key or not model:
        raise RuntimeError("请在 .env 中设置 LLM_API_KEY 和 LLM_MODEL；LLM_BASE_URL 可选。")
    return LLMConfig(base_url=base_url, api_key=api_key, model=model)
