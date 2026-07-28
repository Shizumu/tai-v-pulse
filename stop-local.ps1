param([switch]$Quiet)

$ErrorActionPreference = 'Stop'
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
    $OutputEncoding = $utf8WithoutBom
} catch {}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workDir = Join-Path $projectRoot 'work'
$pidFile = Join-Path $workDir 'local-processes.json'

function Get-RecordedPid {
    param($Record)
    if ($null -eq $Record) { return $null }
    if ($Record -is [int] -or $Record -is [long]) { return [int]$Record }
    if ($Record.PSObject.Properties.Name -contains 'pid') { return [int]$Record.pid }
    return $null
}

try {
    if (-not (Test-Path -LiteralPath $pidFile)) {
        if (-not $Quiet) { Write-Host '台V Pulse 目前未執行。' }
        exit 0
    }

    $records = Get-Content -Raw -LiteralPath $pidFile -Encoding UTF8 | ConvertFrom-Json
    $targets = @(
        [pscustomobject]@{ Record = $records.api; Names = @('python', 'python3', 'pythonw'); Label = '資料服務' },
        [pscustomobject]@{ Record = $records.web; Names = @('node'); Label = '網頁服務' }
    )

    foreach ($target in $targets) {
        $recordedPid = Get-RecordedPid $target.Record
        if (-not $recordedPid) { continue }
        $process = Get-Process -Id $recordedPid -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        if ($process.ProcessName -notin $target.Names) {
            Write-Warning "[TVP-E601] PID $recordedPid 已屬於其他程式，為避免誤關閉已略過。"
            continue
        }
        Stop-Process -Id $recordedPid -Force
        if (-not $Quiet) { Write-Host "已停止$($target.Label)。" }
    }

    Remove-Item -LiteralPath $pidFile -Force
    if (-not $Quiet) { Write-Host '台V Pulse 已停止。' -ForegroundColor Green }
    exit 0
} catch {
    Write-Host "[TVP-E602] 無法完整停止台V Pulse：$($_.Exception.Message)" -ForegroundColor Red
    exit 60
}
