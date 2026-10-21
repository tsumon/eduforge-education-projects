# 实际训练结果与限制

日期：2026-10-21。主报告对应 `outputs/formal/qwen-run1/` 的真实 Qwen 100 步训练。所有数字来自保存的 manifest、日志和原始预测；没有修写模型预测。

## 主要结果：真实中文预训练基座完成 100 步 LoRA

| 项目 | 实际值 |
|---|---|
| 基座 | Qwen/Qwen2.5-0.5B-Instruct |
| 固定 revision | 7ae557604adf67be50417f59c2c2f167def9a775 |
| optimizer steps | 100 |
| batch / 梯度累积 / CPU 线程 | 1 / 1 / 2 |
| max_length / 学习率 / LoRA rank | 1024 / 0.0002 / 8 |
| 总参数 / 可训练参数 | 498,431,872 / 4,399,104 |
| Adapter 权重 L1 变化 | 6481.460399866104 |
| Dev token 加权 loss | 0.7284830343610835 → 0.000622255706058818 |
| 训练与最终 dev 评估耗时 | 239.599 秒 |
| 冻结基座 SHA256 | 517b2a3fffc18d5c6ae288364c0693938d803f9d278b04e95650cee5bcb2fd48 |
| 冻结基座训练前后 | 完全一致 |

原先交付配置预定 100 步。为了 CPU 开销，梯度累积从 4 明确覆盖为 1；其余学习率、rank、种子与数据划分保持原定设置。该调整没有依据测试结果。可复现配置是 `configs/qwen_cpu100.json`，实际 adapter 是 `adapter_model.safetensors`（17,640,136 字节）。

## 独立生成评估：按正确分母展示

相同固定基座、相同 prompt、相同 14 条文档组留出测试、相同 seed=42、greedy、max_new_tokens=256。100 步 adapter 从磁盘重新加载后生成预测。基线复用 pilot 阶段真实生成的相同基座原始输出，未改写；摘要与复用来源记录在 `baseline_reuse.json`。

| 样本子集 / 指标 | Baseline | 100-step adapter |
|---|---:|---:|
| 可回答样例引用精确率（n=8） | 0.0000 | 1.0000 |
| 可回答样例引用召回率（n=8） | 0.0000 | 1.0000 |
| 必须拒答样例严格成功率（n=6） | 0.0000 | 1.0000 |
| JSON 有效率（n=14） | 0.7143 | 1.0000 |

这只是本套小型合成数据上的规则结果，**不能称为真实业务准确率或教育质量达标**。宏平均诊断项仍保留在 JSON 中，但引用宏平均包含拒答空集，拒答字段宏平均包含非拒答分类正确；不能用它们替代上表的子集分母。指标定义、固定措辞误判限制见 `docs/metrics.md`。

## 必须保留的实质限制

- 12 份人工编写、明确标注的合成教育来源，84 条样例；train 8 个文档/56 条、dev 2 个/14 条、test 2 个/14 条。跨集文档组、样例 ID、问题、来源 ID、逐字证据检查通过。
- 划分是 **document-group held-out**，各集合共享同样的题型模板，**不是 template-held-out**。
- 拒答问题明文带 `no_context` / `irrelevant` / `conflict` 情境标记。不能据此推断真实无标记问题的拒答泛化。
- 开发者已查看同一测试集的 pilot 输出。100 步没有根据它调整模型参数、数据或预测，但后续结果**不是未见的盲测**。正式验收必须新增独立资料、无标记问题与人工复核。
- 规则评分不判断完整语义忠实性。即使这 14 条得分为 1，也不能推断真实学校课程、制度或学生评估的可靠性；低 dev loss 也可能来自共享模板和小样本。

## 保留的较早运行

- `outputs/qwen-pilot/`：同中文基座的独立 8 步 pilot，真 adapter 保留。JSON 有效率 0.714286 → 1.0，但 **8 个可回答样例的引用精确/召回率均为 0，6 个必须拒答样例严格成功率也为 0**。旧宏平均 0.428571 的引用分数只来自拒答空集，0.571429 的拒答字段分数只来自非拒答样例，不能当作能力成功率。报告：`pilot-report.html`。
- `outputs/verified-smoke/`：公开随机微型 Llama 的 8 步真实 LoRA 冒烟，基座 SHA256 前后一致、adapter 权重实际变化；六项旧代理指标及子集结果均为 0。随机模型没有可用教育语言能力。报告：`smoke-report.html`。
- `outputs/public-smoke/`：首次微型运行记录，后由固定 revision、带冻结哈希的 verified-smoke 主验证记录补充。

## 可核对制品

主 HTML：`training-report.html`。全部图片直接放在项目根目录：[百步训练损失曲线](../模型微调_百步训练损失曲线.png)、[八步试训损失曲线](../模型微调_八步试训损失曲线.png)、[冒烟训练损失曲线](../模型微调_冒烟训练损失曲线.png)、[桌面测试截图](../模型微调_桌面测试截图.png)、[手机测试截图](../模型微调_手机测试截图.png)。训练、基线、adapter 预测、完整独立评估和 SHA256 清单均在 `outputs/formal/qwen-run1/`。数据构建报告和防泄漏检查分别为 `dataset-build.json`、`leakage-check.json`。

实际 Qwen tokenizer 检查全部 84 条：train 最长 304 tokens、dev 294、test 254；本轮长度 1024 不需要截断证据或答案。环境为 Windows/Python 3.12.8/CPU（无 CUDA）、torch 2.14.1+cpu、Transformers 4.57.6、PEFT 0.18.1，完整版本见 `environment.json` 和 `../requirements-tested.txt`。最终测试证据见 `tests-final.txt`。

提交包保留真实 adapter、日志、评估、截图和数据，排除基座权重、依赖、缓存、临时文件。没有付费 API 调用，也没有使用 API key。
