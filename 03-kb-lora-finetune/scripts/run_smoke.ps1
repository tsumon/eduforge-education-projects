param([switch]$OfflineRandom)
$ErrorActionPreference = "Stop"
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw '请先运行 scripts\setup.ps1' }
function Invoke-TaskPython { param([string[]]$Arguments); & $taskPython @Arguments; if ($LASTEXITCODE -ne 0) { throw "步骤失败: $Arguments" } }
$taskRun = 'outputs/smoke-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
Invoke-TaskPython -Arguments @('-m','finetune.build_dataset')
Invoke-TaskPython -Arguments @('-m','finetune.check_leakage')
Invoke-TaskPython -Arguments @('-m','unittest','discover','-s','tests','-v')
if ($OfflineRandom) {
    Invoke-TaskPython -Arguments @('-m','finetune.train_lora','--config','configs/smoke_random.json','--output',"$taskRun/adapter")
    $taskBase = "$taskRun/smoke-base"
} else {
    $taskBase = 'hf-internal-testing/tiny-random-LlamaForCausalLM'
    $taskRevision = '9fb191250dd56d0ba7ec9785a025ed29c03d5998'
    Invoke-TaskPython -Arguments @('-m','finetune.train_lora','--model',$taskBase,'--revision',$taskRevision,'--purpose','smoke','--output',"$taskRun/adapter",'--max-steps','8','--gradient-accumulation','1','--max-length','2048','--learning-rate','0.001','--rank','4')
}
if ($OfflineRandom) { $taskRevision = 'main' }
Invoke-TaskPython -Arguments @('-m','finetune.infer','--model',$taskBase,'--revision',$taskRevision,'--output',"$taskRun/baseline.jsonl",'--max-new-tokens','64')
Invoke-TaskPython -Arguments @('-m','finetune.infer','--adapter',"$taskRun/adapter",'--output',"$taskRun/adapter.jsonl",'--max-new-tokens','64')
Invoke-TaskPython -Arguments @('-m','finetune.evaluate','--pred',"$taskRun/adapter.jsonl",'--baseline',"$taskRun/baseline.jsonl",'--output',"$taskRun/evaluation.json")
Write-Output "冒烟证据已保存到 $taskRun。随机微型模型不能证明中文问答质量或提升。"
