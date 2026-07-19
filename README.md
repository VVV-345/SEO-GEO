# SEO-GEO Agents

Python 实现的中文 SEO/GEO 多 Agent 项目。当前完成关键词机会 Agent，其他 Agent 已预留独立目录。

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

输出保存到 `output/keyword_opportunities_<种子词>.json` 和 `.md`。

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
