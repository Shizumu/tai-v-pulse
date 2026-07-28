[CmdletBinding()]
param(
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
    $OutputEncoding = $utf8WithoutBom
} catch {}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workDir = Join-Path $projectRoot 'work'
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outputRoot = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    [Environment]::GetFolderPath('Desktop')
} elseif ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
}

$bundleName = "TaiVPulse-diagnostics-$timestamp"
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("$bundleName-$([Guid]::NewGuid().ToString('N'))")
$archivePath = Join-Path $outputRoot "$bundleName.zip"

function Protect-DiagnosticText {
    param([AllowEmptyString()][string]$Text)
    if ($null -eq $Text) { return '' }

    $safe = $Text
    $safe = [regex]::Replace($safe, 'AIza[0-9A-Za-z_-]{20,}', '<REDACTED_YOUTUBE_API_KEY>')
    $safe = [regex]::Replace(
        $safe,
        '(?im)(YOUTUBE_API_KEY\s*[:=]\s*)[^\s"''\r\n]+',
        '$1<REDACTED>'
    )
    $driveRoot = [System.IO.Path]::GetPathRoot($env:USERPROFILE)
    $usersRoot = Join-Path $driveRoot 'Users'
    $userPathPattern = '(?i)' + [regex]::Escape("$usersRoot\") + '[^\\\r\n]+'
    $safe = [regex]::Replace($safe, $userPathPattern, (Join-Path $usersRoot '<USER>'))
    if (-not [string]::IsNullOrWhiteSpace($env:USERNAME)) {
        $safe = $safe.Replace($env:USERNAME, '<USER>')
    }
    if (-not [string]::IsNullOrWhiteSpace($env:COMPUTERNAME)) {
        $safe = $safe.Replace($env:COMPUTERNAME, '<COMPUTER>')
    }
    return $safe
}

function Get-CommandVersionLine {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $resolved = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $resolved) { return '未偵測到' }
    try {
        $version = [string](& $resolved.Source @Arguments 2>$null | Select-Object -First 1)
        return "$(Protect-DiagnosticText $resolved.Source) | $($version.Trim())"
    } catch {
        return "$(Protect-DiagnosticText $resolved.Source) | 版本讀取失敗"
    }
}

try {
    New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
    if (Test-Path -LiteralPath $archivePath) {
        throw "診斷報告已存在，為避免覆寫已停止：$archivePath"
    }
    New-Item -ItemType Directory -Path $stagingRoot | Out-Null

    $packageVersion = '未知'
    $packageJsonPath = Join-Path $projectRoot 'package.json'
    if (Test-Path -LiteralPath $packageJsonPath -PathType Leaf) {
        try { $packageVersion = [string](Get-Content -Raw -LiteralPath $packageJsonPath -Encoding UTF8 | ConvertFrom-Json).version } catch {}
    }

    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $windowsSummary = "$($os.Caption) $($os.Version) build $($os.BuildNumber)"
    } catch {
        $windowsSummary = [Environment]::OSVersion.VersionString
    }
    $summaryLines = @(
        '台V Pulse 診斷報告',
        "產生時間（UTC）：$((Get-Date).ToUniversalTime().ToString('o'))",
        "台V Pulse 版本：$packageVersion",
        "Windows：$windowsSummary",
        "系統架構：$env:PROCESSOR_ARCHITECTURE",
        "PowerShell：$($PSVersionTable.PSVersion)",
        "Node.js：$(Get-CommandVersionLine -Command 'node' -Arguments @('--version'))",
        "npm：$(Get-CommandVersionLine -Command 'npm.cmd' -Arguments @('--version'))",
        "Python：$(Get-CommandVersionLine -Command 'python' -Arguments @('--version'))",
        '',
        '隱私說明：本報告不包含 .env、YouTube API Key、SQLite 資料庫或 Studio 原始檔。',
        '路徑中的 Windows 使用者名稱與電腦名稱已替換。'
    )

    try {
        $portLines = @()
        foreach ($port in @(3000, 8787)) {
            $connections = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
            if ($connections.Count -eq 0) {
                $portLines += "Port ${port}：未監聽"
            } else {
                foreach ($connection in $connections) {
                    $owner = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
                    $ownerName = if ($owner) { $owner.ProcessName } else { '未知程序' }
                    $portLines += "Port ${port}：PID $($connection.OwningProcess) / $ownerName"
                }
            }
        }
        $summaryLines += @('', '本機埠狀態：') + $portLines
    } catch {
        $summaryLines += @('', "本機埠狀態：讀取失敗（$($_.Exception.Message)）")
    }

    $summaryText = Protect-DiagnosticText ($summaryLines -join [Environment]::NewLine)
    Set-Content -LiteralPath (Join-Path $stagingRoot 'diagnostic-summary.txt') -Value $summaryText -Encoding UTF8

    $allowedWorkFiles = @(
        'api.log', 'api-error.log', 'web.log', 'web-error.log',
        'last-error.json', 'local-processes.json', 'diagnostics-error.json'
    )
    $allowedWorkFiles += @(Get-ChildItem -LiteralPath $workDir -Filter 'start-*.log' -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 5 | ForEach-Object { $_.Name })
    $allowedWorkFiles += @(Get-ChildItem -LiteralPath $workDir -Filter 'launcher-*.log' -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 5 | ForEach-Object { $_.Name })

    foreach ($fileName in @($allowedWorkFiles | Select-Object -Unique)) {
        $source = Join-Path $workDir $fileName
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { continue }
        $text = Get-Content -LiteralPath $source -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($null -eq $text) { $text = '' }
        if ($text.Length -gt 2MB) { $text = $text.Substring($text.Length - 2MB) }
        Set-Content -LiteralPath (Join-Path $stagingRoot $fileName) -Value (Protect-DiagnosticText $text) -Encoding UTF8
    }

    $installerLogRoot = Join-Path $env:LOCALAPPDATA 'TaiVPulse\logs'
    if (Test-Path -LiteralPath $installerLogRoot -PathType Container) {
        Get-ChildItem -LiteralPath $installerLogRoot -Filter 'installer-*.log' -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 2 | ForEach-Object {
                $text = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
                if ($null -eq $text) { $text = '' }
                Set-Content -LiteralPath (Join-Path $stagingRoot $_.Name) -Value (Protect-DiagnosticText $text) -Encoding UTF8
            }
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($stagingRoot, $archivePath, [System.IO.Compression.CompressionLevel]::Optimal, $false)
    Write-Host "診斷報告已建立：$archivePath" -ForegroundColor Green
    Write-Output "DIAGNOSTIC_ZIP=$archivePath"
    exit 0
} catch {
    Write-Host "[TVP-D001] 無法建立診斷報告：$($_.Exception.Message)" -ForegroundColor Red
    try {
        New-Item -ItemType Directory -Force -Path $workDir | Out-Null
        [ordered]@{
            code = 'TVP-D001'
            time = (Get-Date).ToUniversalTime().ToString('o')
            message = $_.Exception.Message
            location = $_.ScriptStackTrace
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $workDir 'diagnostics-error.json') -Encoding UTF8
    } catch {}
    exit 70
} finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
