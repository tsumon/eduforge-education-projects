# EduForge 教育知识库

完整 RAG 代码链路：**Embedding API → Qdrant 持久化向量数据库 + BM25 混合检索 → 回答 API → 原文引用与校验**。Embedding 和回答接口可来自不同的 OpenAI 兼容提供商。项目不下载、不加载本地模型，不依赖 PyTorch 或 Transformers。

保留课程资料管理、原文对照、问答历史、反馈及 Markdown/JSONL 导出。7 份示例材料为 AI 编写的合成资料，不代表真实院校教学政策或成绩；独立微调项目的模型没有接入在线服务。

## 配置和启动

Python 3.10+，已验证 Python 3.12。

```powershell
cd 01-edu-knowledge-base
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，填写接口配置后启动
python run.py --load-samples
```

打开 [教育知识库](http://127.0.0.1:8001)。不使用 `--load-samples` 时为空库，可主动载入合成示例或导入自己的资料。`scripts/start.bat` / `start.sh` 只安装普通项目依赖和启动应用。

`.env` 中需要填写：

| 配置 | 说明 |
|---|---|
| `EDU_EMBEDDING_BASE_URL` | Embedding 兼容接口基础地址，通常以 `/v1` 结尾 |
| `EDU_EMBEDDING_API_KEY` | Embedding 服务密钥；无鉴权接口可留空 |
| `EDU_EMBEDDING_MODEL` | 提供商支持的向量模型名 |
| `EDU_EMBEDDING_DIMENSIONS` | 实际返回维度，示例值 1536 需要按模型修改 |
| `EDU_LLM_BASE_URL` | 回答兼容接口基础地址 |
| `EDU_LLM_API_KEY` | 回答服务密钥；无鉴权接口可留空 |
| `EDU_LLM_MODEL` | 回答模型名 |

Embedding 请求路径为 `/embeddings`，回答路径为 `/chat/completions`，不要把这两个路径填到 BASE_URL。课程片段将发送给配置的提供商；API Key 只在后端读取，不返回给前端。环境变量优先于 `.env`，更改后重启。

未配置 API 而仅需原文抽取演示：

```powershell
python run.py --profile lexical --load-samples
```

该模式明确显示“词法检索 · 原文抽取”。默认 `rag` 模式缺少配置会提示缺项，不会静默冒充完整 RAG。

## 其他设置

| 配置 | 默认 | 说明 |
|---|---|---|
| `EDU_RAG_PROFILE` / `--profile` | `rag` | 完整链路；`lexical` 为轻量词法模式 |
| `EDU_LLM_PROVIDER` / `--llm-provider` | `openai` | 兼容 API；`none` 关闭回答模型 |
| `EDU_EMBEDDING_VERSION` | `1` | 提供商同名模型升级时增加，触发重新编码 |
| `EDU_EMBEDDING_QUERY_PREFIX` | 空 | 仅当提供商要求查询指令时填写 |
| `EDU_API_TIMEOUT` | `30` | API 超时秒数，最大 300 |
| `EDU_SEMANTIC_MIN_SCORE` | `0.50` | 余弦召回门槛，需按模型及资料校准 |
| `EDU_DATA_DIR` / `--data-dir` | `runtime` | SQLite 与 Qdrant 数据目录 |
| `EDU_API_TOKEN` | 空 | 配置后 API 和导出需 Bearer 令牌 |

## 检索与回答

```text
资料 → 标题/段落分片 → Embedding API → Qdrant
问题 → 查询向量 + BM25 → 排名融合 → 证据筛选 → 回答 API → 引用/数字校验
```

- Embedding 分批请求、按响应 `index` 恢复原始顺序并 L2 归一化；数量错误、维度错误、零向量、非数值或非有限值都会拒绝写入。
- 词法和向量各召回 10 个分片，使用 Reciprocal Rank Fusion 合并；精确词法证据优先，同义问法可独立走语义路径。余弦分数不是正确概率。
- SQLite 是事实来源，向量点使用稳定 ID、内容摘要和模型签名。资料增删自动同步，未变内容重启后复用；换接口、模型、维度或版本会重新编码。
- “设置”可查看模型名称、向量数量及同步状态，也可重建索引。索引异常时返回 503，阻止用失效索引回答；资料可能已经保存，可在修复依赖后重建。
- 回答须引用有效片段，每段数字须出现在该段引用原文中；校验失败会明确降级。只有语义相关证据而生成未通过时拒答，避免抽取无关段落。数字校验较保守，可能拒绝需要计算产生新数值的答案。
- 历史保留当时的模式、模型、检索分数及降级原因，引用对照实际原文。没有接入的标识符不会被其他主体的资料替代。

引文及数字校验不能证明全部语义正确，需要人工核对。Qdrant 本地模式面向单进程课程级资料，同一数据目录不能被多个进程同时打开。多人上线仍需权限隔离、备份恢复、限流及目标数据验收。扫描 PDF 需先做 OCR。

## 测试与截图

```powershell
python -m pytest -q
# 只运行完整 HTTP 协议集成测试
python -m pytest tests/test_rag_http.py -q
```

HTTP 集成测试运行本机测试提供商，经过真实 HTTP Embedding/回答协议和真实持久化 Qdrant。测试不下载模型，不调用外部付费接口，也不代表某个真实提供商已经联调成功；真实联调需填入你自己的配置。

原有 9 张中文截图在项目根目录，为此前界面验证；本轮按要求只修改代码及文档，未重新启动应用或截图。前端保持 Taste Skill 的布局，增加 API/向量状态和重建入口，无需 Node 构建。字体与许可证在 `static/fonts/`。

## 打包

```powershell
python scripts/package.py --group 示例组 --name 示例姓名
```

排除 `.env`、依赖、数据库、所有 `runtime*` 和模型缓存；只保留不含密钥的 `.env.example`。配置、响应及故障语义见 [API 文档](docs/API.md)，架构见 [RAG 设计](docs/rag-design.md)。
