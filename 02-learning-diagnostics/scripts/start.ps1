param([int]$Port = 8002)
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    python -c "import fastapi, uvicorn, multipart, dotenv, langgraph" 2>$null
    if ($LASTEXITCODE -ne 0) {
        python -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查 Python 与网络配置。' }
    }
    python run.py --port $Port
} finally {
    Pop-Location
}
