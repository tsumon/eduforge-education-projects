# EduForge 教育知识库

把课程资料、课程文档、项目文档导入成可检索的知识库；提问后给出**逐句标注出处**的回答，资料不足时**明确拒答**并说明原因；全部问答留痕并可导出 Markdown。

- 零外部服务：Python 3.10+ / FastAPI / SQLite，不需要任何 API 密钥即可运行。
- 回答模式显式标记：`extractive`（资料原句抽取拼接）；配置了 OpenAI 兼容接口时为 `llm`（模型基于引用片段生成，引用编号非法则自动降级为抽取式）。
- 内置 7 份**合成**演示资料（大纲、讲义、项目需求书、评分细则、FAQ），文件头均有声明，不代表任何真实院校。

## 快速开始

```bash
cd 01-edu-knowledge-base
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py --load-samples            # http://127.0.0.1:8001
```

Windows 也可直接双击 `scripts/start.bat`（会自动建虚拟环境并安装依赖）。

启动后：

1. 在「资料」页导入 TXT / MD / CSV / DOCX / PDF，或新建文本。空库可选择载入示例。
2. 在「问答」页输入问题，回答与原文证据并排展示；点引用可定位并高亮原文。
3. 「用示例问题试一试」为可选入口，含资料不足的拒答示例。
4. 查看、反馈或导出当前回答，也可到「记录」筛选历史并导出 JSONL。

## 接手后的前端改版

使用 `design-taste-frontend` 的 **Achromatic ledger** 应用风格（seed `129812592`）：浅灰蓝工作区、图标导航、课程来源侧栏、回答与原文证据并排。手机切换为底部导航，表格转为资料行。沿用原生 HTML/CSS/JS，无需 Node 构建。

字体与许可证保存在 `static/fonts/`，运行时不依赖外网。访问令牌用于 API 和文件导出；引用不会污染页面路由，资料详情支持 Esc、焦点约束和返回原控件。

真实浏览器截图均用中文命名，直接放在项目根目录（如 `01_桌面_知识问答.png`、`02_手机_知识问答.png`）；验证 JSON 在 `screenshots/`，视觉检查说明见 `docs/frontend-review.md`。

## 可选：接入大模型

```bash
set EDU_LLM_BASE_URL=https://api.example.com/v1     # OpenAI 兼容
set EDU_LLM_API_KEY=sk-...
set EDU_LLM_MODEL=qwen-plus
```

配置后顶部「模式」显示为「生成」。模型只拿到检索出的片段，提示词要求逐句标 `[n]` 且不得越过片段；服务端校验引用编号集合 ⊆ 候选集合，否则降级为抽取式并写入 `diagnostics.llm`。

## 判定逻辑（为什么答 / 为什么拒）

1. 分词：中文按停用字切成词组，词组内取字 bigram；英文/数字整词。无需 jieba，跨环境结果一致。
2. 检索：BM25 取前 5 个分片。
3. 词法重排：得分 ≥ 最佳分 × 0.45 的候选，按「词组覆盖率 → BM25」重排，让把问题各部分都提到的分片排前。
4. 门控（任一不满足即拒答）：
   - 词组覆盖率 ≥ 50%：问题里至少一半的词组能在分片里找到（挡住"量子计算 / Java"这类资料里不存在的对象）；
   - 内容词覆盖率 ≥ 34%：按 idf 加权，资料从未出现的词也计入分母（挡住"期末考试具体日期"这类主题相关但事实缺失的问题）；
   - BM25 ≥ 1.5。
5. 抽取：每个证据分片挑与问题重叠最多的 1—2 句，拼成带 `[n]` 的回答。

全部阈值在 `app/core/answer.py: AnswerPolicy`，每次回答的 `diagnostics` 字段都会原样记录候选得分与命中的规则。

## 目录

```
app/core/        零第三方依赖：tokenizer / chunker / bm25 / answer / store / importers / llm
app/service.py   业务门面（可脱离 FastAPI 单测）
app/api.py       FastAPI 路由  /api/knowledge/*
app/main.py      create_app(data_dir=None)
static/          原生 HTML/CSS/JS 界面
data/samples/    合成演示资料 + questions.json（演示问题及预期行为）
tests/           test_core / test_service（无需 fastapi）、test_api（需 fastapi+httpx，缺失自动跳过）
scripts/         start.bat / start.sh / package.py / render_preview.py
docs/API.md      接口文档
runtime/         运行时数据（SQLite），已 gitignore
```

## 测试

```bash
pytest -q                 # 或
python -m unittest discover -s tests -t .
```

`tests/test_service.py::test_sample_questions_behave_as_labelled` 会把 `questions.json` 里每个问题的 `expect` 与系统实际行为逐一对照，演示问题的"预期拒答"不是写死的标签，而是被测试守住的行为。

2026-10-21 接手验证：51 个测试通过，含损坏 PDF/DOCX 混合导入、160 次并发问答写库、无效模型响应降级回归。浏览器回归 17 项通过，覆盖问答、原文引用、拒答、新建与筛选、历史、反馈及带令牌的下载。

安装了 Taste Skill 且本机有 Chrome/Edge 时，可复跑浏览器检查（需使用独立验证数据库，避免污染正式资料）：

```bash
python run.py --port 8001 --data-dir runtime-review --load-samples
# 另一个终端；可用 TASTE_SKILL_DIR 指向技能目录
node scripts/browser_check.mjs
```

## 打包提交

```bash
python scripts/package.py --group 第三组 --name 张三
# → dist/第三组_张三_教育知识库_教育.zip（排除 .venv/runtime/dist/__pycache__ 等）
```

## 已知边界

- 检索是词法的（BM25 + 覆盖率），同义改写（"扣分" vs "按 80% 计"）靠词组级部分匹配兜底，复杂改写可能漏召回；接入 LLM 模式可改善表达，但不改变召回。
- 扫描版 PDF 没有文本层，导入会被拒绝并提示。
- 单进程内存索引，面向课程级语料（数千分片）；增删资料后全量重建索引。
