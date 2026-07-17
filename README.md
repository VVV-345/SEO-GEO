# SEO-GEO Agents

中文 SEO / GEO 多 Agent 系统。当前包含 **关键词 Agent（v1：候选模式）**。

整体流程（6 个 agent，逐步补齐）：

```
客户资料 → [关键词 Agent] 选值得做的词 → 选一个 P1 词
        → [SERP+竞品 Agent] 找出前 10 怎么写
        → [Content Brief Agent] 形成写作要求
        → [写作 Agent] 生成初稿
        → [SEO/GEO 质检 Agent] 审核 → 人工确认发布
客户域名 → [技术 SEO 审计 Agent] 并行输出修复方案
```

本步实现的是第 1 个：**关键词 Agent**。

---

## 关键词 Agent v1

**职责**：`种子词 + 客户业务资料 + 已有页面 → P1/P2/P3 候选关键词清单`

**范围（重要）**：v1 为 **候选模式（candidate-llm-only）**，**不接入真实搜索量/难度数据源**。
排序基于 LLM 对三个维度的**估计**：

| 维度（1-5） | 含义 |
|---|---|
| commercial_intent | 商业意图，越接近购买/转化越高 |
| specificity | 长尾具体度，越聚焦细分场景越高 |
| serp_difficulty | SERP 竞争估计，依据经验判断前 10 通常谁在排 |

综合分：`composite = 商业意图 + 长尾具体度 + (6 − SERP难度)`，区间 [3,15]
分级：**P1 ≥ 11** ｜ **P2 8–10** ｜ **P3 ≤ 7**（相关性 low 一律降为 P3）

> 数据源接口已预留；接入 5118 / 百度推广 / 百度指数 后可输出带真实指标的 P1/P2/P3。

### 内部流程

```
种子词 + 客户业务 + 已有页面
  → [扩展]   LLM 生成 ≤N 个长尾候选词（含意图 / 相关性标注）
  → [过滤]   丢弃与客户业务低相关的词
  → [去重]   归一化后合并近重复
  → [打分]   LLM 三维度评分 → 综合分 → P1/P2/P3
  → 输出 JSON + Markdown
```

两次 LLM 调用（扩展、打分），中间是确定性的过滤 / 去重 / 分级（可单测）。

---

## 安装

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
```

兼容任何 OpenAI 兼容接口：DeepSeek、智谱 GLM、通义千问、Moonshot、OpenAI 等（各厂商 base_url 见 `.env.example`）。
要求提供商支持 JSON 模式（`response_format=json_object`）。

## 运行

```bash
# 真实调用（需配置 .env）
python -m seo_agents.keyword_agent \
  --seed 企业知识库 \
  --business examples/client_business.md \
  --pages examples/existing_pages.json \
  --num 50 -o output

# 无 API key 时用写死响应预览流程与输出形态
python -m seo_agents.keyword_agent --seed 企业知识库 \
  --business examples/client_business.md --mock
```

输出（`output/`）：
- `keywords_<seed>.json` — 结构化结果（每词的维度评分、综合分、分级、理由）
- `keywords_<seed>.md` — 人类可读报告（P1/P2/P3 速览 + 明细表）

## 测试

```bash
python -m unittest discover -s tests -v
```

单测覆盖纯函数（归一化、去重、过滤、综合分、分级）与端到端流程（注入假 LLM），**不依赖网络与 API key**。

---

## 后续路线

- [ ] 接入真实数据源（5118 / 百度推广 / 百度指数）→ 输出带真实搜索量/难度的 P1/P2/P3
- [ ] SERP + 竞品分析 Agent
- [ ] 技术 SEO 审计 / Content Brief / 写作 / 质检 Agent
