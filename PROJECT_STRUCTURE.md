# 项目结构说明

这套是一个**中文 SEO/GEO 多 Agent 系统**，当前只实现了第 1 个 agent（关键词 Agent）。
本文档解释每个目录和文件是什么、负责什么。

---

## 目录树

```
SEO-GEO/
│
├── CLAUDE.md                  # 项目编码准则（Karpathy 指南），所有代码都要遵守
├── README.md                  # 项目总览：安装、运行、测试、后续路线
├── PROJECT_STRUCTURE.md       # ← 本文件：项目结构说明
│
├── requirements.txt           # Python 依赖清单
├── .env.example               # LLM 配置模板（各厂商 base_url 速查）
├── .gitignore                 # Git 忽略规则
│
├── seo_agents/                # ⭐ 核心：所有 agent 的代码都在这里
│   ├── __init__.py            #   包说明
│   ├── config.py              #   读取 LLM 配置（共用）
│   ├── llm.py                 #   OpenAI 兼容客户端封装（共用）
│   └── keyword_agent.py       #   关键词 Agent（已实现 v1）
│
├── examples/                  # 演示用输入资料
│   ├── client_business.md     #   客户业务资料样例（企业知识库厂商）
│   └── existing_pages.json    #   客户已有页面样例
│
├── tests/                     # 单元测试
│   └── test_keyword_agent.py  #   关键词 Agent 的 9 个单测
│
└── output/                    # 运行后自动生成，存放结果（已被 .gitignore 忽略）
    ├── keywords_企业知识库.json
    └── keywords_企业知识库.md
```

> 其余带点的目录（`.vscode/`、`.claude/`、`__pycache__/`）是编辑器/Python 缓存，不用管。

---

## 分层职责

整个系统分成三层，从下往上：

```
┌─────────────────────────────────────────────┐
│  各个 Agent（keyword_agent / 未来的 serp_agent …）  │  ← 业务逻辑层
├─────────────────────────────────────────────┤
│  共享基础：config.py + llm.py                │  ← 基础设施层（6 个 agent 复用）
├─────────────────────────────────────────────┤
│  外部：LLM API（OpenAI 兼容）                │  ← 外部依赖
└─────────────────────────────────────────────┘
```

**为什么要分层？** 后面还有 5 个 agent（SERP、审计、Brief、写作、质检），它们都要调 LLM、都要读配置。
所以把「读配置」和「调 LLM」抽成 `config.py` / `llm.py` 两个共用文件，每个新 agent 只管自己的业务逻辑，不重复造轮子。

---

## 逐个文件说明

### 根目录配置文件

| 文件 | 作用 | 你需要改吗 |
|---|---|---|
| `CLAUDE.md` | 编码行为准则（先想后写、简单优先、外科手术式修改、目标驱动） | 不用改 |
| `README.md` | 怎么安装、运行、测试 | 看它即可上手 |
| `requirements.txt` | 依赖：`openai`、`python-dotenv` | 装依赖用 |
| `.env.example` | LLM 配置模板，含 DeepSeek/智谱/通义/Moonshot/OpenAI 的 base_url | 复制成 `.env` 后填你的 key |
| `.gitignore` | 忽略 `.env`（含密钥）、`output/`、缓存 | 不用改 |

### `seo_agents/` —— 核心代码

#### `config.py`（共用 · 30 行）
- 定义 `LLMConfig`：存 `base_url` / `api_key` / `model`。
- `load_llm_config()`：从环境变量或 `.env` 读配置，缺 key/model 就报错提示。

#### `llm.py`（共用 · 40 行）
- `OpenAILLM` 类：把 OpenAI SDK 包一层，对外只暴露一个方法 `chat_json(system, user)`。
- 只用 **JSON 模式**（`response_format=json_object`），不用各厂商未必支持的进阶功能 → 保证 DeepSeek/智谱/通义/Moonshot 都能跑。

#### `keyword_agent.py`（关键词 Agent · 约 250 行，系统的核心）
这一个文件包含关键词 Agent 的全部内容，分 6 块：

| 代码块 | 作用 |
|---|---|
| **数据结构** | `Candidate`（候选词）、`KeywordResult`（带评分的最终词）、`AgentResult`（整体结果） |
| **纯函数** | `normalize_keyword`（归一化）、`dedupe_candidates`（去重）、`composite_score`（综合分）、`assign_tier`（分级）、`filter_by_relevance`（业务过滤）—— **不依赖 LLM，可单测** |
| **Prompts** | `EXPAND_SYSTEM`（扩展候选词的提示词）、`RANK_SYSTEM`（打分的提示词） |
| **解析** | `_parse_candidates` / `_parse_ranked`：防御性解析 LLM 返回的 JSON |
| **主流程** | `run_keyword_agent()`：扩展 → 过滤 → 去重 → 打分 → 分级 → 排序 |
| **输出** | `write_json` / `write_markdown`：写两种格式结果 |
| **Mock** | `MockLLM`：写死响应，无 API key 也能预览流程 |
| **CLI** | `main()`：命令行入口，`python -m seo_agents.keyword_agent ...` |

### `examples/` —— 演示输入

- `client_business.md`：一个虚构的「企业知识库」厂商的业务资料，演示 agent 怎么用它判断相关性。
- `existing_pages.json`：客户已经有的页面，演示 agent 怎么避免重复造轮子。

> 这两个只是样例。真实使用时换成你自己客户的资料即可。

### `tests/` —— 单元测试

- `test_keyword_agent.py`：9 个测试，覆盖归一化、去重、过滤、综合分、分级、端到端流程。
- 用标准库 `unittest`，**不需要装额外东西、不需要网络/key**。

### `output/` —— 运行结果（自动生成）

跑一次 agent 后自动出现，里面有：
- `keywords_<种子词>.json`：结构化结果（程序读用）。
- `keywords_<种子词>.md`：人类可读报告（P1/P2/P3 速览 + 明细表）。

---

## 数据流：关键词 Agent 内部怎么走

输入 → 两次 LLM 调用 + 中间确定性处理 → 输出：

```
输入：种子词 + 客户业务资料 + 已有页面
        │
        ▼
[1] 扩展（LLM 调用 #1，temperature 0.8）
    生成 ≤N 个长尾候选词，每个带：意图 + 与业务的相关性
        │
        ▼
[2] 过滤      丢掉与客户业务「低相关」的词          ← 纯函数，可测
        │
        ▼
[3] 去重      归一化后合并近重复词                  ← 纯函数，可测
        │
        ▼
[4] 打分（LLM 调用 #2，temperature 0.3）
    每个词三个维度评分：商业意图 / 长尾具体度 / SERP 竞争估计
        │
        ▼
[5] 分级      composite = 商业 + 具体度 + (6−难度)   ← 纯函数，可测
    P1≥11 ｜ P2 8-10 ｜ P3≤7
        │
        ▼
[6] 排序 + 输出   P1→P2→P3，组内按综合分降序
    写出 JSON + Markdown
```

**设计要点**：LLM 只负责「需要判断/生成」的两步（扩展、打分）；
过滤、去重、分级这些**有确定规则的步骤用纯函数写**，方便测试、结果稳定可解释。

---

## 关键概念速查

- **候选模式（candidate-llm-only）**：v1 不接真实数据源，输出标「候选」，不编造搜索量。
- **综合分公式**：`composite = 商业意图 + 长尾具体度 + (6 − SERP难度)`，范围 [3, 15]。
- **分级阈值**：P1 ≥ 11 ｜ P2 8–10 ｜ P3 ≤ 7（相关性 low 一律降为 P3）。
- **6 个 agent 的位置**：目前只有 `keyword_agent.py`；后面会加 `serp_agent.py`、`seo_audit_agent.py` 等，都放在 `seo_agents/` 下，共用 `config.py` / `llm.py`。

---

## 常用命令

```bash
# 装依赖
pip install -r requirements.txt

# 配置 LLM（填 base_url / api_key / model）
cp .env.example .env

# 真实跑关键词 Agent
python -m seo_agents.keyword_agent \
  --seed 企业知识库 \
  --business examples/client_business.md \
  --pages examples/existing_pages.json

# 无 key 预览流程（用写死数据）
python -m seo_agents.keyword_agent --seed 企业知识库 --mock

# 跑测试
python -m unittest discover -s tests -v
```
