"""SEO/GEO 多 Agent 系统。

当前包含：
- keyword_agent：关键词 Agent（v1 候选模式）

共享基础（后续 agent 复用）：
- config：从环境变量 / .env 读取 LLM 配置
- llm：OpenAI 兼容客户端封装
"""
