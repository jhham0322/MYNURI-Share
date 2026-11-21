param(
    [string]$Python = 'python',
    [ValidateRange(1024,65535)][int]$Port = 18782,
    [string]$Provider = 'codex'
)
$ErrorActionPreference = 'Stop'
$pythonCommand = (Get-Command $Python -ErrorAction Stop).Source
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$run = Join-Path $root 'run/mobile'
New-Item -ItemType Directory -Path $run -Force | Out-Null
& $pythonCommand -m nuri --mobile --mobile-isolated --mobile-host 127.0.0.1 --mobile-port $Port `
    --mobile-db (Join-Path $run 'commands.sqlite3') --rag-dir (Join-Path $run 'rag') `
    --paseo --paseo-provider $Provider --paseo-mode read-only --no-tray --no-improve `
    --log-file (Join-Path $run 'executor.log')
exit $LASTEXITCODE
