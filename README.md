# SEO-GEO Agents

Python 实现的中文 SEO/GEO 多 Agent 项目。当前已完成关键词机会 Agent、单关键词的 SERP + 竞品分析 Agent，以及第一版技术 SEO 审计 Agent。

## 当前关键词流程

```text
种子词 + 客户业务资料（多文件，可选）+ 客户已有页面 URL（可选）
→ 读取 PDF/DOCX/TXT/MD/HTML/JSON 等资料
→ 下载客户页面并提取标题、Meta、H1-H3、正文
→ LLM 扩展长尾词、按搜索任务去重聚类、标注意图
→ 仅查询百度下拉词，按意图在 UI 分组展示
→ 用户勾选需要验证的候选词
→ 仅为勾选词查询相关搜索和前 10 自然结果 URL
→ 规则计算 SERP 竞争估计
→ LLM 基于业务与 SERP 证据排序
→ JSON + Markdown 关键词机会清单
```

百度 URL 采集按三层回退：桌面搜索 HTML → 移动搜索 HTML → 系统 Edge（Playwright）。浏览器回退只读取百度结果页的标题和 URL，不访问竞品正文；一旦静态路径在本次任务中失效，后续关键词会复用同一浏览器上下文。

进入竞品分析前会排除明确的百度广告服务页、无法解析的百度中转链接、百度站内搜索/文库搜索聚合页和带付费搜索跟踪参数的结果。被排除的地址不会静默丢弃，其 URL 与原因会保存在 `filtered_results` / `filtered_urls` 并显示在 UI 和关键词报告中。百度百科等独立内容页不会提前过滤，即使抓取阶段可能返回 403。

> SERP 竞争分是当前百度结果快照的规则估算，不是搜索量、百度指数或第三方关键词难度。百度结构和访问限制可能导致结果不完整。

## 安装

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env
```

真实运行需要在 `.env` 填入 `LLM_API_KEY`、`LLM_MODEL`，按提供商需要设置 `LLM_BASE_URL`。

## UI

```powershell
python ui.py
```

UI 支持填写需求描述、逐次添加或删除多个业务资料文件、填写补充资料、输入多个客户页面 URL，并在各 Agent 标签页展示输出。默认 Mock 模式可离线验证流程。

UI 按 Agent 模块拆分，每个 Agent 自己维护 `ui.py`：

```text
agents/keyword_agent/ui.py
agents/serp_competitor_agent/ui.py
agents/technical_seo_agent/ui.py
agents/content_brief_agent/ui.py
agents/writing_agent/ui.py
agents/quality_agent/ui.py

ui/main_ui.py       # 只组装标签页和公共状态栏
ui/app_state.py     # 跨 Agent 共享结果与事件
ui/task_runner.py   # 后台线程、进度和回调
ui/widgets.py       # 通用控件
```

Agent UI 不直接读取其他模块控件。关键词 Agent 把候选与 SERP 写入 `AppState`，竞品 Agent 订阅状态变化后刷新关键词和 URL。技术审计 Agent 可以独立运行。旧的 `ui.desktop` 导入路径仍保留兼容。

扩词模型会同时收到：种子词、需求描述、合并后的业务资料，以及已有页面的标题、Meta、H1-H3 和清洗正文（抓取失败时传递错误状态）。

关键词 UI 是两阶段操作：

1. 点击“生成候选词”，此阶段不会查询自然结果 URL。
2. 候选词按搜索意图分组，双击候选行或按空格勾选。
3. 点击“获取勾选词 URL”，只查询被勾选的词。
4. 某个词失败时，选中该行点击“重试当前词 URL”，不会重新查询其他成功词。

独立 URL 工具位于 `tools/serp_url_tool.py`，其他 Agent 可调用 `SerpURLTool.fetch(keyword)` 或 `fetch_many(keywords)`。

## CLI

离线测试：

```powershell
python main.py keyword --seed 企业知识库 --files examples/client_business.md --mock
```

真实调用：

```powershell
python main.py keyword `
  --seed 企业知识库 知识库私有化 `
  --files materials/company.pdf materials/product.docx `
  --pages https://example.com/product https://example.com/blog `
  --num 20
```

输出统一保存在一次运行目录中：

```text
output/<项目名>/<北京时间戳>/
├── run.json
├── input/
│   ├── project.json
│   └── source_manifest.json
├── keyword/
│   ├── candidates.json
│   ├── candidates.md
│   ├── serp_results.json
│   ├── opportunities.json
│   └── report.md
└── competitor/
    └── <关键词>/
        ├── pages.json
        ├── report.json
        └── report.md
```

点击“生成候选词”会创建新时间戳目录；之后查询勾选词、重试单词以及后续竞品 Agent 都复用该目录。

## SERP + 竞品分析流程

关键词 Agent 取得落地页 URL 后，切换到“SERP + 竞品”标签页：

1. 从下拉框选择一个确定关键词，界面自动带入该词已取得的 URL。
2. 人工检查、增删 URL，并选择本次最多抓取的页面数。
3. 点击“开始 SERP + 竞品分析”。系统逐页提取 Title、Meta Description、H1-H3 层级、FAQ、表格、案例原句、数字原句和清洗正文。
4. LLM 只根据成功抓取的页面与客户资料，归纳搜索意图、页面类型、共同主题、常见模块、FAQ、案例/数据证据、内容缺口、必写项和建议结构。

单页抓取失败不会中断整个任务；失败原因会保留在页面证据中，也不会被误判为竞品内容缺口。默认与关键词 Agent 共用 `.env` 中的 `LLM_*`，需要单独模型时可配置：

```text
COMPETITOR_LLM_BASE_URL=
COMPETITOR_LLM_API_KEY=
COMPETITOR_LLM_MODEL=
```

未设置的竞品模型字段自动回退到对应的 `LLM_*`。

## 技术 SEO 审计

技术 SEO 标签页可独立运行，最少只需输入客户域名。第一版流程：

```text
域名 + 可选审计目标/业务背景/核心页面
→ 读取 robots.txt 与 Sitemap
→ 受控同域抓取页面和内链
→ 提取状态码、Title、Meta、H1、canonical、robots meta、JSON-LD 等事实
→ 可选对少量代表页运行本地 Lighthouse
→ Python 对照本地官方来源规则库命中问题
→ LLM 只能整理既有 finding_id 和执行顺序
→ Python 校验后输出 P0/P1/P2 报告
```

技术规则位于 `agents/technical_seo_agent/knowledge/core_rules.json`，每条包含来源、核对日期、适用范围、限制、修复和验收方法。第一版引用百度搜索资源平台官方入口以及 Google Search Central、Schema.org、Chrome/Web.dev 的具体公开文档；不会把通用规则冒充成百度对客户网站的具体结论。

Lighthouse 是可选的实验室检测。需要本机安装 Node.js、Chrome/Edge 和 Lighthouse CLI：

```powershell
npm install -g lighthouse
```

如果没有安装，审计仍会继续，并在 `lighthouse.json` 和报告限制中记录“未执行”；不会生成虚假性能分数。Lighthouse 数据也不会被表述为真实用户体验或百度排名原因。

技术审计输出：

```text
output/<项目>/<北京时间戳>/technical_seo/
├── crawl_config.json
├── robots_snapshot.txt
├── sitemap_snapshot.json
├── pages.json
├── link_graph.json
├── lighthouse.json
├── rule_findings.json
├── audit_report.json
└── audit_report.md
```

未提供百度搜索资源平台、GSC 或服务器日志数据时，报告只描述公共网站可验证事实，不判断真实收录、曝光、排名或蜘蛛访问。

技术审计 CLI 离线测试：

```powershell
python main.py technical-seo --domain https://example.com --core-urls /product --mock
```

真实审计示例：

```powershell
python main.py technical-seo `
  --domain https://example.com `
  --goal "检查核心产品页抓取与索引基础" `
  --files materials/company.pdf `
  --core-urls https://example.com/product `
  --max-pages 50
```

运行时会显示结构化步骤：读取资料、解析页面、扩展候选词、查询百度 SERP（x/N）、排序和完成。桌面 UI 会把同一组事件显示为进度条和执行时间线；其他 Agent 可复用 `tools/progress.py` 的 `ProgressReporter`。

## SERP 竞争规则

规则只使用当前前 N 条（默认 5）的可观察信息：

- 标题直接覆盖关键词比例：35%
- 强势平台或权威域名比例：35%
- 首页结果比例：15%
- 少数域名重复占位：15%

前 5 条都未获取完整时，竞争等级标记为 `unknown`。业务机会分由业务匹配、商业接近、具体程度和 SERP 可进入性共同计算。报告不输出搜索量，也不把估算称为真实关键词难度。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 为什么当前未引入 LangChain/LangGraph

当前流程是明确的线性管道，普通 Python 更容易测试和定位失败。等出现人工审批、并行 Agent、断点恢复、条件路由或模型自主选择工具时，再将现有 `tools/` 包装为 LangChain tools，并用 LangGraph 编排；不需要重写数据采集与评分逻辑。
