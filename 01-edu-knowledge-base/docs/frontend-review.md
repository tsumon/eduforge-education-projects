# RAG 前端接手与验证

2026-10-21。接手范围是现有教育知识库，不迁移框架，不替换 API。

## 设计决定

- 技能：`design-taste-frontend` 当前安装版本，采用 `references/app-screens.md`，不是旧版营销页面规则。
- 方向：Achromatic ledger，seed `129812592`，未重抽。
- 产品特征：课程文档的标题、分类、分片和证据原文构成界面的内容；没有虚构访问量、成长率或客户证明。
- 视觉识别：低饱和蓝灰底色、定制打开的书页标记、单一深色操作色。蓝色只用于原文高亮与引用焦点。
- 核心问题：问答页“资料能支持怎样的回答”；资料页“有哪些可检索来源”；记录页“过去怎样回答及反馈”；设置页“当前回答策略和服务是什么”。
- 签名结构：回答与证据并排，引用定位不改变路由；手机改为上下阅读与底部导航。
- 本地 Geist 字体及 SIL 许可证随代码交付。没有图片素材或对外部字体 CDN 的运行时依赖。

## 视觉检查

改版前 Taste Skill：2 FAIL、5 WARN（字体不符、问答缺少 h1、对比度、手机边缘、数字及无用留白）。

改版后对问答、资料、记录、设置分别在 1440、768、390px 检查。报告与前后截图保存在本地 `.taste-skill/`，正式交付截图均用中文命名并放在项目根目录，检查 JSON 仍位于 `screenshots/`。

可随提交包查看的实测摘要为 `screenshots/visual-checks.json`：问答、资料、设置均为 0 FAIL / 0 WARN；历史为 0 FAIL / 1 WARN（全表稀疏列产生的留白，保留真实记录及完整问题列）。

ROUND 1
Seen in one second: 课程资料与问题入口。
Idea (one sentence): 让课程知识成为可核对的证据，而不是没有出处的聊天。
Live thing: 从当前资料提问，定位真实分片并保留反馈。
Layout words by section: shell=图标导航 / sources=来源列表 / answer=证据对照 / composer=提问 / records=数据表。
Scores: design 7 · usability 7.5 · creativity 7 · content 8 → overall 7.25
Caps applied: 使用应用界面规则，不适用营销页图片或动效数量限制。
Top three problems (most visible first), each with the screenshot it shows in and the fix:
1. desktop-fold：来源标题从侧栏溢出，补足 flex/grid min-width 和省略。
2. desktop-fold：来源大卡与提问面板争夺层级，改为平静来源导航。
3. mobile-fold：辅助文字偏小，提升正文与列表字号，保留日期/技术元数据的次级样式。

ROUND 2
Seen in one second: 教育知识库的提问与原文查阅工作区。
Idea (one sentence): 回答旁边就是其证据，用户无需切换页面核实。
Live thing: 真实 API 问答、引用高亮、资料导入、反馈与鉴权下载。
Layout words by section: shell=导航 / sources=课程列表 / answer=双栏对照 / phone=顺序阅读 / settings=标签与配置。
Scores: design 8 · usability 8 · creativity 7 · content 8 → overall 7.8
Caps applied: 无应用界面强制上限；截图为合成课程资料和回归测试问题，不是业务统计证明。
Top three problems (most visible first), each with the screenshot it shows in and the fix:
1. 历史页记录来自回归测试，重复问题会很多；交付保留空库启动方式，验证数据库单独存放并从打包排除。
2. 手机读长文件名时需要换行；保留完整来源而非删除字段。
3. 离线回答仍为词法检索和原句摘录；界面明确标注，不用“语义置信度”包装 BM25 分数。

## 功能证据

`screenshots/browser-check.json`：真实浏览器 17 项，含键盘提交、Shift+Enter、引用、拒答、资料新增与搜索、抽屉、历史筛选、反馈保存与修改、JSONL 与 Markdown 下载、令牌保存及带 Authorization 的导出、桌面/手机无横向溢出。

后端 `python -B -m pytest -q -p no:cacheprovider`：51 passed、4 subtests passed。补齐了损坏文件逐个失败、SQLite 160 次并发写入、坏模型响应自动降级回归。

代码检查的可解释提示：日期格式的前导零是日期；等宽字体仅用于代码；“—”表示缺失值。历史全表的列内留白由真实数据长度产生，不填造内容消除视觉提示。

## 数据与范围

7 份内置文档均是合成示例，原文件已声明；截图中的问答为验证操作。正式使用应导入自己的课程材料。默认启动不载入示例，`--load-samples` 或空库入口由用户主动选择。

保留原有 BM25 检索；未增加向量检索或声称检索分是语义概率。模型生成是可选模式，未配置外部模型时只展示原文抽取。
