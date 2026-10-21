# 学情诊断 API

前缀 `/api/diagnostics`。设置 `EDU_API_TOKEN` 时，全部诊断接口（包括示例和导出）须带 `Authorization: Bearer <令牌>`，无效令牌返回401；未设置时不验证。公开 `GET /api/auth/check` 返回 `{required:true|false}`。Swagger 位于 `/api/docs`，OpenAPI 位于 `/api/openapi.json`。错误为 `{"detail": "说明"}`；参数缺失或类型错误使用 FastAPI 的 422 校验数组。缺失掌握度一律为 JSON `null`，不是 0。

| 方法 | 路径 | 输入与输出 |
|---|---|---|
| GET | `/schema` | 列说明、类型映射、分层标签、默认 rubric/syllabus、输入限制和方法说明 |
| GET | `/sample?kind=csv` | 合成 CSV 下载，UTF-8 BOM；默认 kind 为 csv |
| GET | `/sample?kind=syllabus` | 默认进度 JSON 下载 |
| GET | `/sample?kind=rubric` | 默认评分 JSON 下载 |
| POST | `/demo` | 读取合成CSV生成并保存报告；`is_demo=true`，告警中注明合成资料 |
| POST | `/upload` | multipart 必需 `file`（CSV），可选 `syllabus`、`rubric`（UTF-8 JSON文件）、`class_name`（文本），返回并保存完整报告 |
| GET | `/reports?limit=100` | `{reports:[{id,class_name,course,created_at,source_name,is_demo,student_count,average_mastery}]}`，新→旧，limit 1—200 |
| GET | `/reports/{id}` | 完整报告；不存在 404 |
| GET | `/reports/{id}/export` | Markdown下载，包含个体证据、补讲、题目/提示/答案、方法与评分进度快照；不存在404 |
| POST | `/reports/{id}/review` | JSON `{reviewer,note?,reviewed?:true}`，保存人工复核并返回完整报告；`reviewed:false`撤回到待复核。reviewer 1—80字、note最多500字，不存在404 |

`GET /healthz` 返回 `{ok:true,version,mode:"rule_based"}`。不自动生成报告，不向外部模型或服务发送成绩。

## 报告结构

```json
{
  "id": "report_...", "class_name": "Python A班", "course": "Python 程序设计",
  "created_at": "带时区的ISO时间", "source_name": "class.csv", "is_demo": false,
  "summary": {
    "student_count": 8, "topic_count": 6, "average_mastery": 70.0,
    "at_risk_count": 2, "record_count": 98,
    "assessed_student_count": 8, "insufficient_student_count": 0
  },
  "topics": [{
    "topic": "循环", "week": 3, "taught": true, "status": "assessed", "mastery": 65.0,
    "student_count": 8, "assessed_count": 8, "weak_count": 3, "weak_rate": 0.375,
    "priority": "medium", "record_count": 24,
    "evidence": ["第N行 · 学号 · 知识点 · 考核/题目 · 得分/满分 · 日期 · 输入错因标签"],
    "error_tags": {"循环边界": 2}
  }],
  "students": [{
    "student_id": "S001", "name": "示例姓名", "mastery": 42.0,
    "level": "foundation", "status": "assessed", "record_count": 12,
    "weak_topics": ["循环"], "evidence": ["原始行号与考核证据"],
    "topic_scores": [{"topic":"循环","mastery":42.0,"status":"assessed","record_count":3,"by_type":{},"evidence":[]}],
    "exercises": [{"topic":"循环","level":"foundation","question":"人工题库题目","answer":"参考答案","hint":"提示"}]
  }],
  "reteaching": [{"topic":"循环","priority":"medium","reason":"由实际人数、薄弱比例和均分计算","suggestion":"复盘原题、变式与复测"}],
  "warnings": ["跳过行、数据不足和教学进度提示"],
  "parse_stats": {"rows":99,"valid":98,"duplicates":0,"skipped":{}},
  "methodology": {"mode":"rule_based","description":"规则统计说明","formula":"加权公式","minimum_records":2,"missing_data":"不补零","scope":"本次输入样本","rubric":{},"syllabus":{}}
}
```

上例仅说明字段，不是固定返回数据。`status` 为 `assessed / insufficient_data / not_taught / unknown_topic`；仅 `assessed` 有有效掌握度。`taught` 为 true/false，进度外为 null。`priority` 为 `high / medium / none / not_evaluated`。`level` 为 `foundation / consolidation / challenge`，数据不足为 null。无可评估学生时 `average_mastery=null`，`at_risk_count=0`。

`student_count` 是知识点有输入记录的人数；`assessed_count` 是其中达到最少正权重记录要求的人数。薄弱比例以 `assessed_count` 为分母；人数为 0 时比例为 null。错因标签来自输入者；缺少标签时证据会明确说明“未标注错因”。

## CSV 与配置

必需列 `student_id,name,topic,assessment_type,item,score,max_score,date`，中文对应 `学号,姓名,知识点,考核类型,题目,得分,满分,日期`；可选 `error_tag,note`（错因、备注）。`assessment_type` 支持 `homework/quiz/interaction` 和中文作业/随堂测/课堂互动。

CSV 8MB、50,000行；配置256KB；非法/非有限数字跳过并告警。满分须大于0且不超过1e9，得分不得超出0—满分。缺必需表头或无有效记录返回400。日期错误保留但置空，重复键（学号+知识点+类型+题号+日期）保留最后一条。

报告维度另外限制为最多2,000名学生、200个知识点，学生数×知识点数≤100,000。知识点为进度表与CSV的并集，包含未授课和进度外知识点；在分组评分和报告矩阵构建前检查，超限返回400并建议按班级/章节拆分。`/schema.limits` 返回 `max_students`、`max_topics`、`max_student_topic_cells`。

`syllabus`：`{class_name,course,taught_through_week,topics:[{topic,week}]}`；当前周次0—100，知识点周次1—100，不允许重名。`rubric`：完整示例由 `/sample?kind=rubric` 下载；未指定字段使用默认值，指定的嵌套对象须完整。权重恰含homework/quiz/interaction、非负且和为1；`0<weak<challenge≤100`；最少记录1—100，练习上限1—20。

上传的评分与进度仅应用于当前报告；默认配置和既有报告不会变更。报告JSON与Markdown保留配置快照，可追踪每次结论的口径。

## 人工复核与操作记录

完整报告还包含 `review:{status:"pending"|"reviewed",reviewer,note,reviewed_at}` 与 `audit_log:[{action,created_at,...}]`。新报告初始待复核，创建操作记录包含来源文件、时间及是否合成示例。复核保存复核人、时间和备注；撤回追加 `review_reset` 操作记录，不会删除旧记录或重算成绩。列表摘要额外返回 `review_status`。报告与导出均提示需要教师人工复核，不执行自动处罚或正式学生评价。自行填写的复核人是操作标识，不代表认证身份。

启用令牌时，浏览器下载必须通过带 Authorization 的 fetch 获取 Blob；普通 `<a href>` 下载不会携带会话保存的令牌。
