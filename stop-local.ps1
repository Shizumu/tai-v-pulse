$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $projectRoot 'work\local-processes.json'
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host '沒有找到正在執行的台V Pulse 程序。'
    exit 0
}

$processes = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
@($processes.api, $processes.web) | ForEach-Object {
    if ($_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $_ -Force
    }
}
Remove-Item -LiteralPath $pidFile
Write-Host '台V Pulse 已停止。'
