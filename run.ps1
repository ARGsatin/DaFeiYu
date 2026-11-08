$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

Write-Host ''
Write-Host '   ~~~ 大肥鱼 正在入水 ~~~' -ForegroundColor Cyan
Write-Host ''

# 优先用项目里的 .venv（依赖都装在里面），没有才退回系统的 python
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = 'python' }

& $py -m app
