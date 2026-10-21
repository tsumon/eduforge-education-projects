# 教育知识库模型 LoRA 微调工程

## 本包为仅代码和报告版

按用户要求，本 ZIP 不含原始/划分数据集、模型权重、分词器文件、虚拟环境或依赖缓存；本机完整工程未被删除或改动。历史训练结果、截图和曲线保留，必要的真实运行清单、日志、评估与原始预测副本位于 `reports/evidence/`。原始制品完整性清单可能列出已省略权重的摘要，不表示这些权重已打包。

**运行训练、构建数据或完整测试前，必须先补齐输入。** 原始合成资料 `data/raw/documents.json` 也按要求省略；生成脚本保留，但不能在没有来源资料的情况下重建同一数据集。请从本机完整工程恢复该输入文件，或自行准备符合 `finetune/dataset.py` 格式的合成来源（group_id、title、synthetic=true、term、definition、facts、keywords），再执行：

```powershell
python -m finetune.build_dataset --source data/raw/documents.json
python -m finetune.check_leakage
# 按下文安装依赖后，再运行测试、训练及重新生成预测。
```

新输入不保证复现报告中的既有分数；历史证据仅说明原完整工程的实际运行。报告中的历史 `outputs/` 路径属于完整工程，包内 HTML 已改用 `reports/evidence/` 的证据副本；模型/分词器链接已取消并标明未打包。

---

独立的 Python 脚本项目，对应《项目实战》4.1。目标是让因果语言模型依据检索片段作答，覆盖术语与格式、忠实回答、多片段整合、来源引用、资料不足拒答五种能力。本目录不依赖其他 EduForge 项目的代码。

**数据全部为人工编写的合成教育演示资料，不含真实学生资料或学校制度。** 已实际完成 Qwen 中文预训练基座的 100 步 LoRA 监督训练、独立保留的 8 步 pilot，以及公开随机微型模型的流程冒烟。实际状态、参数和限制见 `reports/RESULTS.md`。训练轮次完成不等于教育质量达标，随机模型也不能用于教育问答。

## 环境与快速检查

Windows PowerShell，在本目录执行，Python 3.10+（本机验证 3.12）。使用 CPU，不需要 CUDA、API key、付费接口或 Hugging Face 账户。依赖和缓存仅保存在当前项目。

```powershell
cd 03-kb-lora-finetune
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
.venv\Scripts\python.exe -m finetune.env_check --require-training
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

无 uv 时可手动 `python -m venv .venv`，然后用 `.venv\Scripts\python.exe -m pip install -r requirements.txt`。CPU 版 torch 可先从 `https://download.pytorch.org/whl/cpu` 安装。`requirements-tested.txt` 记录实际验证的版本。普通数据构建与独立评分只需 Python 标准库；训练边界测试需要训练依赖。

## 数据与防泄漏

```powershell
python -m finetune.build_dataset
python -m finetune.check_leakage
```

`data/raw/documents.json` 包含 12 份独立合成来源。每份生成四条可回答样例，以及无召回、无关召回、冲突召回三条拒答样例，共 84 条。每份文档的所有样例必须进入同一集合：train 8 个文档/56 条、dev 2 个/14 条、test 2 个/14 条。三个集合都覆盖五类能力。重复文档组、样例 ID、规范化问题、来源 ID 和逐字证据跨集时检查失败。`split_report.json` 保存种子和内容摘要。

这只是**文档分组与逐字重复检查**，不能排除模板相似性或语义近似泄漏。正式项目应按原始文档家族/课程/版本归组，人工审核相近资料，扩大领域覆盖。当前数据很少且拒答占 3/7，不能代表真实业务分布。

各划分共享同一组题型模板，所以是 **document-group held-out，不是 template-held-out**。拒答问题明文包含 `no_context` / `irrelevant` / `conflict` 情境标记，它是带标记的合成小样本实验，不能代表真实无标记问题上的拒答泛化。开发者已查看 pilot 的同一测试集输出；后续 100 步没有根据它修改数据、学习率或 rank，但不能把此测试称为最终盲测。

每条数据包含 `question`、`contexts`、`reference`、`capability`、`group_id`、`synthetic`。`reference` 只在训练目标或独立评分中使用；推理 prompt 仅使用问题与召回片段。输出统一为：

```json
{"answer":"依据资料的回答","citations":["来源编号#1"],"refused":false,"missing_information":""}
```

## 实际流程冒烟

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_smoke.ps1
# 网络不可用时，可使用离线随机 GPT-2 架构（仅流程验证）
powershell -ExecutionPolicy Bypass -File scripts\run_smoke.ps1 -OfflineRandom
```

默认使用免费公开 `hf-internal-testing/tiny-random-LlamaForCausalLM`，其名称中的 random 表明它没有可用语言能力。脚本运行 8 步真实 LoRA 优化，保存真实 `adapter_model.safetensors`、日志、训练清单，再分别生成未挂载 adapter 的基线与挂载后的预测，并独立评分。每次运行创建新目录，避免覆盖已有训练证据。

离线选项使用 Transformers 的随机微型 GPT-2 因果语言模型和只在 train 上拟合的字节级 tokenizer。它同样执行 PEFT LoRA，但不是预训练大模型的质量训练，不能替代 Qwen 实验。

## 正式中文模型训练流程（配置已交付，未据此宣称质量达标）

建议的公开基座为 `Qwen/Qwen2.5-0.5B-Instruct`。首次下载需要网络，CPU 训练明显慢于 GPU；训练前根据机器内存缩小 batch/长度或换用 GPU。默认使用 float32 CPU，不引入 Windows 上兼容性复杂的量化依赖。

```powershell
.venv\Scripts\python.exe -m finetune.train_lora --config configs/qwen_cpu100.json --output outputs/formal/qwen-run2
# 有 CUDA 版本 torch 和 GPU 时可追加 --device cuda
.venv\Scripts\python.exe -m finetune.infer --model Qwen/Qwen2.5-0.5B-Instruct --revision 7ae557604adf67be50417f59c2c2f167def9a775 --output reports/qwen-baseline.jsonl
.venv\Scripts\python.exe -m finetune.infer --adapter outputs/formal/qwen-run2 --output reports/qwen-adapter.jsonl
.venv\Scripts\python.exe -m finetune.evaluate --pred reports/qwen-adapter.jsonl --baseline reports/qwen-baseline.jsonl --output reports/qwen-comparison.json
```

本机 100 步实际运行位于 `outputs/formal/qwen-run1`，可用固定 revision 的 `configs/qwen_cpu100.json` 复现到新目录：max_steps=100、max_length=1024、batch_size=1、gradient_accumulation=1、rank=8、learning_rate=0.0002、seed=42、CPU 2 线程。原先 `qwen_lora.json` 的累积 4 仅因 CPU 开销明确覆盖为 1，其余预定配置保持不变；没有依据 test 表现调参或修预测。已有结果目录拒绝覆盖。

独立保留的短程 pilot 是 8 步、长度 512、累积 1，输出为 `outputs/qwen-pilot/adapter`。

复现本次短程试训可使用 `--config configs/qwen_pilot.json --output outputs/qwen-pilot-repeat/adapter`；该配置固定了本次基座 revision。已有结果目录拒绝覆盖。

正式运行应把 `--revision` 固定为模型提交 SHA，并在基线推理使用同一 SHA；默认 `main` 便于首次运行但不保证长期重现。训练清单同时记录实际解析的 commit。只在 train 上计算梯度，在 dev 上记录初末 token 加权 loss；test 不用于训练或超参选择。训练先检查全部输入长度，超长直接报错，避免静默截断证据或答案。prompt 和 padding 的 label 设为 -100，仅对答案计损失；只允许 `lora_` 参数参与优化。

训练支持 max steps、batch、梯度累积、学习率、rank、种子和设备。它是小数据可运行参考实现，**不提供断点续训、分布式训练或生产级模型发布**。输出目录已有 adapter 时拒绝覆盖。

## 评估与验收

`finetune.evaluate` 独立读取人工参考与推理 JSONL，不调用生成模型，也不会把标准答案喂回推理。预测必须覆盖同一测试集全部 ID，每条恰好一次；缺失、多余、重复都会失败。主要结果使用 `outcomes.answerable_only` 的引用精确/召回率（8 个可回答样例），以及 `outcomes.refusal_only` 的严格成功率（6 个必须拒答样例）。另外保留按能力的 JSON、引用、关键词、拒答字段符合与字符 F1 宏平均；宏平均含空引用配对和非拒答分类正确，不能当成真正引用率或拒答成功率。定义见 `docs/metrics.md`。基线与 adapter 采用相同版本、prompt、测试集和解码参数。

这些是透明的规则代理指标：引用存在不等于结论忠实，关键词/字符匹配不等于理解。`docs/human-review.md` 提供人工复核表。正式验收需真实资料、领域专家和独立测试集，不能用训练 loss 下降声称业务能力提升。

## 文件与提交

- `finetune/`：数据构建、检查、环境检测、训练、推理、独立评估。
- `data/raw/`、`data/processed/`：合成来源、固定划分与摘要。
- `configs/`：正式 Qwen 与离线随机冒烟配置。
- `outputs/`、`reports/`：实际 adapter、训练日志、预测与结果说明。
- `MODEL_CARD.md`：用途、数据、实际训练状态和限制。

测试截图和损失曲线均使用中文文件名，直接位于项目根目录，可直接打开：

- [桌面测试截图](模型微调_桌面测试截图.png)、[手机测试截图](模型微调_手机测试截图.png)。
- [百步训练损失曲线](模型微调_百步训练损失曲线.png)、[八步试训损失曲线](模型微调_八步试训损失曲线.png)、[冒烟训练损失曲线](模型微调_冒烟训练损失曲线.png)。

本次仅搬移并改名，未重训、重绘或重截图；搬移前后 SHA256 见 `reports/image-path-migration.json`。截图检查 JSON 仍在 `screenshots/report-check.json`，三份 HTML 报告保持本地相对图片引用。

```powershell
python scripts/package.py --group 你的组名 --name 你的姓名
```

提交 ZIP 排除 .venv、缓存、基座权重、运行临时文件和 .env，包含源码、合成数据、文档、测试及真实 adapter 训练证据。组名与姓名必须自己填写。

实现参考：[Transformers 因果语言模型](https://huggingface.co/docs/transformers/tasks/language_modeling)、[PEFT LoRA](https://huggingface.co/docs/peft/package_reference/lora)、[Qwen 基座模型卡](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct)。
