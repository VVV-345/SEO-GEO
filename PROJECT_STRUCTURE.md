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
│   │   ├── scoring.py              # 可解释 SERP 竞争规则
│   │   └── ui.py                   # 关键词 Agent 独立 UI
│   ├── serp_competitor_agent/      # 抓取前列页面并归纳共同主题、FAQ 与内容缺口
│   │   └── ui.py                   # 竞品 Agent 独立 UI
│   ├── technical_seo_agent/        # 公共网站抓取、规则知识库、LLM总结与校验
│   │   └── ui.py                   # 技术审计 Agent 独立 UI
│   ├── content_brief_agent/        # 预留
│   ├── writing_agent/              # 预留
│   └── quality_agent/              # 预留
├── ui/
│   ├── main_ui.py                  # 只组装 Agent 标签页和公共状态栏
│   ├── app_state.py                # 跨 Agent 状态和事件
│   ├── task_runner.py              # 后台任务与主线程回调
│   ├── widgets.py                  # 文件选择、只读文本等公共组件
│   └── desktop.py                  # 旧导入路径兼容层
└── tests/                          # 规则、工具和 Agent 测试
```

当前使用普通 Python 函数编排。`tools` 和 `KeywordAgent` 都保持显式接口，后续需要人工审批、断点恢复、复杂分支或 tool-calling 时，可将它们包装为 LangChain tools 或 LangGraph nodes。

## Agent 运行流程

系统有两条清晰的流程：一条决定内容选题和页面写法；另一条并行检查客户网站的技术基础。

```text
内容生产主线

客户业务资料 + 种子词 + 已有页面
→ 关键词 Agent（已实现）
  → LLM 扩展候选长尾词、去重、按意图分类
  → 用户勾选需要验证的词
  → 工具只查询勾选词的百度相关搜索和 SERP URL
  → Python 估算 SERP 竞争度，LLM 根据业务与证据排序
  → 输出 P1 / P2 / P3 / 待验证关键词清单
→ 用户选择一个已取得 URL 的关键词
→ SERP + 竞品分析 Agent（已实现）
  → 抓取用户确认的前列 URL
  → 提取 Title、Meta、H1-H3、FAQ、表格、案例/数据原句和正文
  → LLM 归纳搜索意图、共同主题、内容缺口和建议结构
  → 输出竞品报告
→ Content Brief Agent（预留）
  → 将竞品报告、客户资料和已有页面转换为写作任务
→ 写作 Agent（预留）
  → 依据 Brief 和客户真实资料生成初稿
→ SEO/GEO 质检 Agent（预留）
  → 检查搜索意图、事实来源、关键词冲突、Meta、内链和 Schema 建议
→ 人工确认发布
```

```text
技术审计并行线

客户网站域名 + 可选业务资料 / 核心页面 / 审计目标
→ 技术 SEO 审计 Agent（已实现）
  → 工具读取 robots.txt、Sitemap、首页、核心页面和站内链接
  → Python 提取状态码、Title、Meta、H1、canonical、robots meta、Schema 和内链事实
  → 可选对代表页面运行 Lighthouse 实验室检测
  → Python 对照本地规则知识库生成可追溯问题
  → LLM 两阶段归并问题、安排修复顺序
  → Python 校验 LLM 没有编造 URL、收录、排名、流量或平台数据
  → 输出 P0 / P1 / P2 技术修复报告
→ 人工或开发人员修复网站
```

职责边界：

```text
关键词 Agent = 决定做哪个题目
SERP + 竞品分析 Agent = 决定这个题目对应的页面怎样写
技术 SEO 审计 Agent = 决定客户网站哪些技术基础需要修复
Content Brief Agent = 把研究结果变成写作要求
写作 Agent = 生成初稿
SEO/GEO 质检 Agent = 发布前审核
```

跨 Agent 数据通过 `ui/app_state.py` 共享，而不是一个 UI 直接读取另一个 UI 的控件。关键词 Agent 发布候选和 SERP 结果后，竞品 Agent 自动刷新可选择的关键词与 URL；技术审计 Agent 可以独立运行。
