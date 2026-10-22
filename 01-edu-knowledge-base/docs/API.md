# API

基础路径 `/api/knowledge`。配置 `EDU_API_TOKEN` 后，全部请求携带 `Authorization: Bearer <token>`；前端及下载均通过请求头传递令牌。

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/stats` | 资料/问答计数、实际回答模型、语义索引及策略 |
| POST | `/index/rebuild` | 从 SQLite 重新编码全部分片并对账 Qdrant |
| GET / POST | `/documents` | 列表 / 新建文本资料 |
| GET / DELETE | `/documents/{id}` | 资料和分片详情 / 删除并同步索引 |
| POST | `/import` | multipart `files`，可选 `category`，逐文件返回成功/失败 |
| POST | `/ask` | `{"question":"Python成绩怎么算？"}` |
| GET | `/history` | `limit`、`offset`、`refused`、`q` 过滤分页 |
| GET | `/history/{id}` | 完整回答、引用及当时 diagnostics |
| POST | `/history/{id}/feedback` | `{"helpful":true,"note":""}` |
| GET | `/history/export.jsonl` | 全部问答导出，需人工筛选后用于训练 |
| GET | `/export/{id}` | Markdown 导出 |
| GET / POST | `/sample` / `/sample/load` | 示例问题 / 导入合成资料 |

`/healthz` 返回版本及索引就绪状态，异常为 503；`/api/auth/check` 仅返回是否需要令牌。交互文档 `/api/docs`。

## 运行状态

`stats.retrieval` 包含 `mode: hybrid|lexical`、`ready`、`error`。完整 RAG 的 `vector_store` 提供 `engine: Qdrant`、`storage: local`、`chunks` 和 Embedding 的模型 ID、revision、dimension、pooling。

`stats.llm` 包含 `configured`、`provider: openai-compatible|none`、模型名。Embedding 状态同样提供 `configured`、模型名、维度和配置版本。`configured` 仅表示配置状态，不保证下一次模型请求一定成功。

不返回密钥或兼容服务地址；`data_dir` 仅为数据目录末级名称。

## 问答结果

保留字段 `id`、`question`、`answer`、`mode`、`refused`、`citations`、`diagnostics`、`created_at`、`feedback`。

- `mode=agent`：默认 LangGraph 工具调用模式，状态与调用记录见 `diagnostics.agent`；`llm`、`extractive` 仅用于显式关闭 Agent 的基础模式。
- `refused=true`：引用列表为空，答案以“现有资料不足以回答该问题。”开头。
- `citations`：来源编号 `n`、document/chunk ID、title、heading、完整原文 `text`、原文摘录 `excerpt`、`score`、`coverage`、`segment_coverage`、`lexical_score`、`semantic_score`。
- 完整 RAG 的 `score` 是排名融合分，`semantic_score` 是余弦相似度；词法模式的 `score` 为 BM25 分。两者不能直接比较，不是正确概率。
- `diagnostics.retrieval.mode` 和 `diagnostics.model` 记录该次检索及回答模型；`candidates` 和 `selected` 可追溯实际证据。
- 基础模式中 `diagnostics.degraded=true` 表示生成校验/调用失败；`diagnostics.llm` 提供脱敏原因。Agent 模式不使用规则答案掩盖失败，核验失败在预算内交给模型修正，最终失败明确拒答。

## Agent 运行结构

`GET /stats` 新增 `agent:{enabled,configured,framework:"langgraph",max_steps}`。`configured` 只表示配置存在，不表示提供商可用。接口路径、请求与历史导出保持兼容。

`POST /ask` 的 `diagnostics.agent` 包含：

```json
{
  "framework": "langgraph", "status": "completed", "model": {"configured":true,"provider":"openai-compatible","model":"用户配置的模型"},
  "steps": 4, "tool_calls": 3,
  "events": [{"kind":"tool","step":1,"tool":"search_knowledge","call_id":"提供商调用编号","arguments":{"query":"查询","limit":4},"status":"ok","observation":{"ok":true,"data":{}}}],
  "result": {"answer":"带[1]引用的答案","refused":false,"citations":[]}, "error": null
}
```

示例只说明字段。状态为 `completed / failed / limited / not_configured`。`completed` 也可为经过搜索后的拒答，须同时检查 `refused`；失败与超限 `result=null`、外层 `refused=true`，历史照常保存。核验事件为 `kind=validation`，失败反馈会送回模型修正。

模型只能调用 `search_knowledge(query,limit?)`、`read_evidence(source_ids)`、`check_answer(answer)`。参数强类型并拒绝未知字段，读取仅允许本次搜索编号；最终核验引用、数字和原文当前有效性。默认最多 8 次模型决策、12 次工具调用。错误正文、密钥、提供商地址不返回。
- `evidence_basis=lexical_and_semantic` 表示证据同时通过词法和语义确认；`semantic` 表示只走语义路径。后者在生成失败时拒答，避免把相关片段硬凑成答案。

常见 `diagnostics.reason`：`answered`、`no_question_terms`、`empty_index`、`no_candidates`、`semantic_score_below_threshold`、`unknown_identifiers`、`generation_not_grounded`；词法模式另有 `segment_coverage_below_threshold`、`coverage_below_threshold`、`score_below_threshold`。

## 错误及同步

400 输入或全部文件导入失败；401 令牌错误；404 资料/记录不存在；422 请求结构不合法；503 模型检索或索引依赖暂不可用。生成失败通常以 200 返回明确标记的抽取降级/拒答，检索失败则返回 503。

SQLite 写入提交后才同步向量。若同步失败，资料仍可能已保存，索引标记为不可用并停止问答；修复依赖后调用 `/index/rebuild`，服务启动时也会自动对账。客户端不应在 503 后盲目重复提交同一份资料。
