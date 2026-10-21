# 教育知识库 API

前缀 `/api/knowledge`。交互式文档：`/api/docs`（Swagger UI）。所有错误以 `{"detail": ...}` 返回。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stats` | 资料/分片/提问/拒答计数、分类分布、LLM 配置、阈值 |
| GET | `/documents` | `{documents:[{id,title,category,source_name,chunk_count,created_at}], categories:[...]}` |
| GET | `/documents/{id}` | 资料正文、来源、字符数与完整分片列表 |
| POST | `/documents` | JSON `{title, content, category}` → 201 文档摘要；正文 < 20 字或分类非法 → 400 |
| POST | `/import` | multipart `files`（多文件）+ `category`；逐文件返回 `{imported:[], failed:[{file,error}]}`；全部失败 → 400 |
| DELETE | `/documents/{id}` | 删除文档及其分片；不存在 → 404 |
| POST | `/ask` | JSON `{question}` → 问答记录（见下） |
| GET | `/history?limit=20&offset=0&refused=false&q=关键词` | `{history:[记录],total,limit,offset}`，新→旧；`refused` 和 `q` 可省略 |
| GET | `/history/{id}` | 单条记录 |
| POST | `/history/{id}/feedback` | JSON `{helpful:boolean,note?:string}`，返回更新后的问答记录 |
| GET | `/history/export.jsonl` | 导出全部历史；每行一个完整 JSON 问答记录 |
| GET | `/export/{id}` | Markdown 下载（含引用原文与判定依据） |
| GET | `/sample` | `{questions:[{question, expect: answer/refuse, topic}]}` |
| POST | `/sample/load` | 导入内置合成演示资料（幂等） |

## 问答记录

```json
{
  "id": "qa_xxx",
  "question": "作业迟交怎么扣分？",
  "answer": "根据已导入资料，相关内容如下：\n1. …… [1]（出处：《…》·作业提交规则）\n\n说明：以上回答为检索到的资料原句抽取拼接（模式 extractive）……",
  "mode": "extractive",
  "refused": false,
  "citations": [
    {"n": 1, "document_id": "doc_xxx", "title": "……", "heading": "作业提交规则",
     "chunk_id": "chk_xxx", "text": "原文分片", "score": 22.27, "coverage": 0.4, "segment_coverage": 0.5}
  ],
  "diagnostics": {
    "question_terms": ["作业", "业迟", "迟交", "扣分"],
    "question_segments": [["作业", "业迟", "迟交"], ["扣分"]],
    "policy": {"min_coverage": 0.34, "min_segment_coverage": 0.5, "...": "..."},
    "candidates": [{"chunk_id": "...", "title": "...", "score": 22.27, "coverage": 0.4, "segment_coverage": 0.5}],
    "top_score": 22.27, "top_coverage": 0.4, "top_segment_coverage": 0.5,
    "reason": "answered",
    "selected": ["chk_xxx"]
  },
  "created_at": "2026-10-21T14:48:15+08:00"
}
```

`mode` 取值：`extractive`（原句抽取）/ `llm`（模型基于引用生成）。`refused=true` 时 `citations` 为空，`answer` 以「现有资料不足以回答该问题。」开头，`diagnostics.reason` 为以下之一：

| reason | 含义 |
|---|---|
| `no_question_terms` | 问题没有可检索实词 |
| `empty_index` | 知识库为空 |
| `no_candidates` | 没有分片与问题任何词匹配 |
| `segment_coverage_below_threshold` | 问题词组覆盖率 < 50%（问题对象不在资料中） |
| `coverage_below_threshold` | 内容词覆盖率 < 34%（主题相关但事实缺失） |
| `score_below_threshold` | BM25 < 1.5 |

## 环境变量

| 变量 | 作用 |
|---|---|
| `EDU_DATA_DIR` | 数据目录（默认 `runtime/`） |
| `EDU_API_TOKEN` | 可选访问控制；设置后所有 `/api/knowledge/*` 请求须携带 `Authorization: Bearer <token>`，下载同样需要 |
| `EDU_LLM_BASE_URL` / `EDU_LLM_API_KEY` / `EDU_LLM_MODEL` | OpenAI 兼容模型；URL 与模型名称配置后启用，API key 可按提供方需要填写 |

健康检查：`GET /healthz`。访问控制探测：`GET /api/auth/check` 返回 `{required:boolean}`，这两个接口不要求令牌。

损坏 PDF/DOCX 作为逐文件失败项返回，成功文件继续导入；全部失败返回 400。模型响应为坏 JSON、缺少文本或引用非法时自动降级为原文抽取，诊断原因保留在结果中。
