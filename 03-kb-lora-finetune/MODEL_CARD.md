---
language:
- zh
pipeline_tag: text-generation
license: apache-2.0
base_model: Qwen/Qwen2.5-0.5B-Instruct
tags:
- education
- lora
- synthetic
---

# EduForge 教育 RAG LoRA 参考工程

本目录交付可运行训练/推理/独立评估工具与合成样例。**它不是已经达到教育问答质量标准的模型发布。** 实际模型基座、参数、步数、loss、adapter 权重变化和依赖版本记录在各运行目录 `training_manifest.json`，真实对比见 `reports/RESULTS.md`。

已实际完成 100 步监督训练的中文预训练基座：Qwen/Qwen2.5-0.5B-Instruct（Apache-2.0，使用时遵循原模型许可），固定 revision `7ae557604adf67be50417f59c2c2f167def9a775`。4,399,104 个 LoRA 参数，batch=1，gradient_accumulation=1，max_length=1024，学习率 0.0002，CPU 2 线程；真实 adapter 位于 `outputs/formal/qwen-run1/adapter_model.safetensors`。基座冻结哈希前后一致，adapter 权重确实更新。**训练轮次完成不等于通过教育质量验收。**

另保留独立 8 步 pilot：`outputs/qwen-pilot/adapter`。100 步来自预先配置，累积由 4 改为 1 仅为 CPU 计算开销，没有根据测试结果修改数据、学习率、rank 或预测。

另外保留公开微型随机 Llama 冒烟运行，用于检查 LoRA 优化、保存、加载和评估接口，不提供教育语义能力；其六项代理指标全部为 0，不能宣称能力提升。离线随机 GPT-2 选项也仅是流程夹具。

训练资料：12 份人工编写的合成教育来源，84 条模板样例，包含五种能力，按源文档组划分 56/14/14。没有真实学生数据，不能据此推出对真实学校政策、课程或成绩判断的准确性。不存在对学生进行自动决策的授权或适用性。

训练方法：Transformers 因果语言模型，PEFT LoRA 适配全部线性投影；冻结基座，只优化 LoRA 参数，答案监督，prompt/padding 不计损失。test 不用于训练；所有自动评估均需报告具体数据摘要和指标定义。

限制：这是 document-group held-out，各集合共享题型模板；拒答问题明文包含 no_context/irrelevant/conflict 情境标记，不能证明真实无标记问题的泛化。开发者已查看同一测试集的 pilot 输出，因此最终结果不属于完全未见的盲测。数据量极小、拒答比例偏高；自动规则指标不判断完整语义忠实性，训练 loss 也不代表回答质量。报告主要看可回答样例的引用指标和必须拒答样例的严格成功率，不能将含空集/非拒答贡献的宏平均当作成功率。正式验收必须补充真实授权资料、人工复核和新增独立测试。
