param([int]$Port = 8002)
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    python run.py --port $Port
} finally {
    Pop-Location
}
