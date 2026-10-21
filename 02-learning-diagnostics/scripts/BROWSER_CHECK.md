# 真实浏览器回归

运行环境：Node.js 22+、本机已安装 Chrome / Edge / Chromium。脚本通过现有 `design-taste-frontend` 的零依赖 CDP 客户端启动隐藏的临时浏览器，不需要 npm 安装。

在本项目目录执行。正式服务保持 8002，且首次空态检查要求其 `/api/diagnostics/reports` 为空；脚本只对 8002 做 GET，不会向正式库生成演示报告。

另开两个终端启动隔离服务：

```powershell
python -u run.py --port 8004 --data-dir runtime-browser-review
```

```powershell
$env:EDU_API_TOKEN='diagnostics-browser-review'
python -u run.py --port 8005 --data-dir runtime-browser-auth
```

然后在第三个终端运行：

```powershell
node scripts/browser_check.mjs
```

当前脚本没有位置参数，固定使用以上三个本地端口。可用环境变量 `TASTE_SKILL_DIR` 指定技能安装目录（其中应含 `scripts/lib/cdp.mjs`），默认位置为 `~/.agents/skills/design-taste-frontend`。可用 `CHROME_PATH` 指定浏览器可执行文件。浏览器使用独立临时配置，结束时关闭并清理浏览器配置。

运行结果在 `docs/screenshots/browser-check.json`；所有 PNG 截图均以中文文件名保存到项目根目录，包含 1440px 桌面、390px 手机、学生练习和复核面板；同名存在时追加“复拍”时间标识，不覆盖原图。JSON 中 `screenshot_path_base: project_root` 表示截图路径以本项目根目录为基准。JSON 记录真实服务端口与测试库、每项断言结果、掌握度原值和缺失显示、原始 CSV 行号、复核与撤回审计、下载是否携带 Authorization、页面宽度与控制台错误。

检查会向 8004/8005 的隔离库生成合成报告和上传示例 CSV。不会删除历史报告；每轮上传使用不同班级名称，可重复运行。`failed_requests` 中的唯一预期 400 是故意缺少必需列的 CSV，标为 `expected: true`，同时确认前端显示了服务端错误文本。令牌只用于本地测试，不写入 URL 或检查 JSON。

完成后按 Ctrl+C 停止两个辅助服务，正式 8002 可继续运行。

可另做设计技能检查：

```powershell
node "$env:TASTE_SKILL_DIR/scripts/inspect.mjs" http://127.0.0.1:8004 --app --widths 1440,768,390 --out .taste-skill/inspect
```

此命令需先设置 `TASTE_SKILL_DIR`。可选设计检查的新产物仅作本地诊断，`.taste-skill/` 已排除打包；本次已交付的6张设计检查 JPG 已改中文名保留在项目根目录，对应报告位于 `docs/screenshots/taste-inspect/report.json`。静态代码检查：

```powershell
node --check static/app.js
node --check scripts/browser_check.mjs
node "$env:TASTE_SKILL_DIR/scripts/lint.mjs" static/index.html static/style.css static/app.js --app
```
