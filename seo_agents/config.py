"""从环境变量 / .env 读取配置。所有 agent 共用。"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # python-dotenv 未安装时退化为直接读环境变量
    pass


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
        raise RuntimeError(
            "缺少 LLM 配置：请在 .env 或环境变量中设置 LLM_API_KEY 与 LLM_MODEL"
            "（可选 LLM_BASE_URL）。参见 .env.example。"
        )
    return LLMConfig(base_url=base_url, api_key=api_key, model=model)
