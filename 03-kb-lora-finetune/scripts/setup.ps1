param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "请先安装 uv，或手动建立 .venv 并 pip install -r requirements.txt" }
& uv --cache-dir .uv-cache venv .venv --python $Python
if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败" }
& uv --cache-dir .uv-cache pip install --python .venv\Scripts\python.exe "torch>=2.5,<3" --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw "安装 CPU torch 失败" }
& uv --cache-dir .uv-cache pip install --python .venv\Scripts\python.exe -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "安装训练依赖失败" }
& .venv\Scripts\python.exe -m finetune.env_check --require-training
if ($LASTEXITCODE -ne 0) { throw "环境检查失败" }
