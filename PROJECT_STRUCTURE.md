# 项目结构

```text
SEO-GEO/
├── main.py                         # 统一 CLI 入口
├── app.py                          # CLI/UI 共用的工作流组装层
├── ui.py                           # 桌面 UI 兼容启动入口
├── core/                           # 模型配置和 LLM 客户端
├── tools/                          # 所有 Agent 可复用的工具
│   ├── file_reader.py              # PDF/DOCX/TXT/MD/HTML/JSON 等资料读取
│   ├── webpage.py                  # URL 下载、正文清洗与结构提取
│   └── baidu_serp.py               # 百度下拉词、相关搜索与自然结果
│   ├── baidu_browser.py            # requests 失败时用系统 Edge 提取百度结果 URL
│   ├── serp_url_tool.py             # 按用户勾选词查询URL，支持单词独立重试
│   └── progress.py                 # 跨 Agent 的结构化进度事件与订阅器
├── agents/
│   ├── keyword_agent/              # 当前已实现
│   │   ├── agent.py                # 工作流编排
│   │   ├── models.py               # 输入输出结构
│   │   ├── prompts.py              # 扩词与排序提示词
│   │   └── scoring.py              # 可解释 SERP 竞争规则
│   ├── serp_competitor_agent/      # 抓取前列页面并归纳共同主题、FAQ 与内容缺口
│   ├── technical_seo_agent/        # 预留
│   ├── content_brief_agent/        # 预留
│   ├── writing_agent/              # 预留
│   └── quality_agent/              # 预留
├── ui/
│   └── desktop.py                  # 多 Agent 桌面工作台
└── tests/                          # 规则、工具和 Agent 测试
```

当前使用普通 Python 函数编排。`tools` 和 `KeywordAgent` 都保持显式接口，后续需要人工审批、断点恢复、复杂分支或 tool-calling 时，可将它们包装为 LangChain tools 或 LangGraph nodes。
