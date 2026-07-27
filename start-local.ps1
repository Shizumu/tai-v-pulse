$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workDir = Join-Path $projectRoot 'work'
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

$envPath = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination $envPath
    Write-Host '已建立 .env。請先填入 YOUTUBE_API_KEY，再重新執行本檔。' -ForegroundColor Yellow
    Start-Process notepad.exe -ArgumentList $envPath
    exit 0
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$pythonExe = if ($pythonCommand) { $pythonCommand.Source } else { 'C:\Users\edcba\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' }
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodeExe = if ($nodeCommand) { $nodeCommand.Source } else { 'C:\Users\edcba\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' }

if (-not (Test-Path -LiteralPath $pythonExe)) { throw '找不到 Python。請先安裝 Python 3.11 以上版本。' }
if (-not (Test-Path -LiteralPath $nodeExe)) { throw '找不到 Node.js。請先安裝 Node.js 22 以上版本。' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'node_modules\vinext\dist\cli.js'))) { throw '找不到前端套件，請先執行 npm install。' }

# Some desktop runtimes expose both Path and PATH. Windows Start-Process treats
# them as duplicate dictionary keys, so normalize them before launching workers.
$processPath = [System.Environment]::GetEnvironmentVariable('Path', 'Process')
[System.Environment]::SetEnvironmentVariable('PATH', $null, 'Process')
[System.Environment]::SetEnvironmentVariable('Path', $processPath, 'Process')

$apiLog = Join-Path $workDir 'api.log'
$webLog = Join-Path $workDir 'web.log'
$apiProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','collector.server') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $apiLog -RedirectStandardError (Join-Path $workDir 'api-error.log') -PassThru
$webProcess = Start-Process -FilePath $nodeExe -ArgumentList @((Join-Path $projectRoot 'node_modules\vinext\dist\cli.js'),'dev','--host','127.0.0.1','--port','3000') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $webLog -RedirectStandardError (Join-Path $workDir 'web-error.log') -PassThru

@{ api = $apiProcess.Id; web = $webProcess.Id } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $workDir 'local-processes.json') -Encoding UTF8
Start-Sleep -Seconds 3
Start-Process 'http://localhost:3000'
Write-Host '台V Pulse 已啟動：http://localhost:3000' -ForegroundColor Green
Write-Host '要停止服務時，執行 stop-local.ps1。'
