param(
    [switch]$NoOpen,
    [switch]$EditEnv,
    [switch]$CheckEnv
)

$ErrorActionPreference = 'Stop'
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
$utf8WithBom = [System.Text.UTF8Encoding]::new($true)
try {
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
    $OutputEncoding = $utf8WithoutBom
} catch {}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workDir = Join-Path $projectRoot 'work'
$pidFile = Join-Path $workDir 'local-processes.json'
$lastErrorFile = Join-Path $workDir 'last-error.json'
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

$sessionLog = Join-Path $workDir ("start-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$transcriptStarted = $false
$exitCode = 0

function Throw-TvpFailure {
    param(
        [Parameter(Mandatory = $true)][string]$Code,
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [Parameter(Mandatory = $true)][string]$Message
    )

    $exception = New-Object System.Exception($Message)
    $exception.Data['TvpCode'] = $Code
    $exception.Data['TvpExitCode'] = $ExitCode
    throw $exception
}

function Refresh-ProcessPath {
    $currentPath = $env:Path
    $machinePath = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = @($machinePath, $userPath, $currentPath) -join ';'
}

function Find-CompatibleNode {
    $command = Get-Command node -ErrorAction SilentlyContinue
    if (-not $command) { return $null }

    try {
        $versionText = [string](& $command.Source --version 2>$null | Select-Object -First 1)
        $version = [version]$versionText.Trim().TrimStart('v')
        if ($version -ge [version]'22.13.0') { return $command.Source }
    } catch {
        return $null
    }

    return $null
}

function Find-CompatiblePython {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if (-not $command -or $command.Source -like '*\WindowsApps\python*.exe') { return $null }

    try {
        $executable = [string](& $command.Source -c 'import sys; assert sys.version_info >= (3, 11); print(sys.executable)' 2>$null | Select-Object -Last 1)
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $executable.Trim() -PathType Leaf)) {
            return $executable.Trim()
        }
    } catch {
        return $null
    }

    return $null
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-PythonTimeZoneData {
    param([Parameter(Mandatory = $true)][string]$PythonExe)

    try {
        & $PythonExe -c "from zoneinfo import ZoneInfo; ZoneInfo('America/Los_Angeles'); ZoneInfo('Asia/Taipei')" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Initialize-PythonRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$WorkDirectory
    )

    $requirementsPath = Join-Path $ProjectRoot 'requirements.txt'
    if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
        Throw-TvpFailure -Code 'TVP-E103' -ExitCode 10 -Message '缺少 requirements.txt，請重新安裝完整的台V Pulse。'
    }

    $pythonPackagesDirectory = Join-Path $WorkDirectory 'python-packages'
    $requirementsStatePath = Join-Path $WorkDirectory 'requirements.sha256'
    New-Item -ItemType Directory -Force -Path $pythonPackagesDirectory | Out-Null
    $existingPythonPath = [System.Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
    $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($existingPythonPath)) {
        $pythonPackagesDirectory
    } else {
        "$pythonPackagesDirectory$([System.IO.Path]::PathSeparator)$existingPythonPath"
    }

    $requirementsHash = Get-FileSha256 -Path $requirementsPath
    $recordedRequirementsHash = if (Test-Path -LiteralPath $requirementsStatePath -PathType Leaf) {
        (Get-Content -LiteralPath $requirementsStatePath -Raw -Encoding ASCII).Trim().ToLowerInvariant()
    } else {
        ''
    }
    $timeZoneDataReady = Test-PythonTimeZoneData -PythonExe $PythonExe
    if ($timeZoneDataReady -and [string]::IsNullOrWhiteSpace($recordedRequirementsHash)) {
        $requirementsHash | Set-Content -LiteralPath $requirementsStatePath -Encoding ASCII
        return
    }
    if ($timeZoneDataReady -and $recordedRequirementsHash -eq $requirementsHash) { return }

    & $PythonExe -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Throw-TvpFailure -Code 'TVP-E403' -ExitCode 42 -Message 'Python 缺少 pip，無法安裝必要的時區資料。請使用官方 Python 安裝程式修復 pip 後再試。'
    }

    Write-Host '正在安裝台V Pulse 必要的 Python 時區資料…' -ForegroundColor Cyan
    & $PythonExe -m pip install `
        --disable-pip-version-check `
        --no-input `
        --upgrade `
        --target $pythonPackagesDirectory `
        --requirement $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        Throw-TvpFailure -Code 'TVP-E403' -ExitCode 42 -Message "Python 時區資料安裝失敗，程序結束碼：$LASTEXITCODE。請確認可連上 PyPI 後匯出診斷報告。"
    }

    if (-not (Test-PythonTimeZoneData -PythonExe $PythonExe)) {
        Throw-TvpFailure -Code 'TVP-E403' -ExitCode 42 -Message 'Python 時區資料安裝完成後仍無法載入。請匯出診斷報告。'
    }
    $requirementsHash | Set-Content -LiteralPath $requirementsStatePath -Encoding ASCII
    Write-Host 'Python 時區資料已準備完成。' -ForegroundColor Green
}

function Request-NpmInstall {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $choice = [System.Windows.Forms.MessageBox]::Show(
            "台V Pulse 尚未安裝前端套件。要現在自動安裝嗎？`r`n`r`n程式會從 npm 官方套件來源下載相依套件，檔案只會放在台V Pulse 安裝資料夾。",
            '台V Pulse 首次安裝',
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question
        )
        return $choice -eq [System.Windows.Forms.DialogResult]::Yes
    } catch {
        $choice = Read-Host '尚未安裝前端套件。要現在自動安裝嗎？(Y/N)'
        return $choice -match '^(?i:y|yes)$'
    }
}

function Wait-HttpReady {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [System.Diagnostics.Process]$Process
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if ($Process) {
            $Process.Refresh()
            if ($Process.HasExited) { return $false }
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 400) {
                return $true
            }
        } catch {}
        Start-Sleep -Milliseconds 750
    } while ([DateTime]::UtcNow -lt $deadline)

    return $false
}

function Get-RecordedPid {
    param($Record)
    if ($null -eq $Record) { return $null }
    if ($Record -is [int] -or $Record -is [long]) { return [int]$Record }
    if ($Record.PSObject.Properties.Name -contains 'pid') { return [int]$Record.pid }
    return $null
}

function Stop-StaleRecordedProcess {
    param(
        $Record,
        [Parameter(Mandatory = $true)][string[]]$AllowedNames
    )

    $recordedPid = Get-RecordedPid $Record
    if (-not $recordedPid) { return }
    $process = Get-Process -Id $recordedPid -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -in $AllowedNames) {
        Stop-Process -Id $recordedPid -Force -ErrorAction SilentlyContinue
    }
}

function Get-ApiKeyState {
    param([Parameter(Mandatory = $true)][string]$EnvPath)

    $value = $null
    $occurrences = 0
    foreach ($line in Get-Content -LiteralPath $EnvPath -Encoding UTF8) {
        $match = [System.Text.RegularExpressions.Regex]::Match(
            $line,
            '^[ \t]*(?:export[ \t]+)?YOUTUBE_API_KEY[ \t]*=[ \t]*(.*?)[ \t]*$'
        )
        if ($match.Success) {
            $occurrences += 1
            $value = $match.Groups[1].Value.Trim()
            if ($value.Length -ge 2) {
                $firstCharacter = $value.Substring(0, 1)
                $lastCharacter = $value.Substring($value.Length - 1, 1)
                if (($firstCharacter -eq '"' -and $lastCharacter -eq '"') -or
                    ($firstCharacter -eq "'" -and $lastCharacter -eq "'")) {
                    $value = $value.Substring(1, $value.Length - 2).Trim()
                }
            }
        }
    }

    return [pscustomobject]@{
        Configured = -not [string]::IsNullOrWhiteSpace($value)
        Occurrences = $occurrences
    }
}

try {
    try {
        [System.IO.File]::WriteAllText($sessionLog, '', $utf8WithBom)
        Start-Transcript -LiteralPath $sessionLog -Append | Out-Null
        $transcriptStarted = $true
    } catch {
        Write-Warning '無法啟用啟動逐字記錄；其他 LOG 仍會繼續寫入。'
    }

    Write-Host "台V Pulse 啟動診斷：$sessionLog"
    Refresh-ProcessPath

    $envPath = Join-Path $projectRoot '.env'
    $envCreated = $false
    if (-not (Test-Path -LiteralPath $envPath)) {
        $envExamplePath = Join-Path $projectRoot '.env.example'
        if (-not (Test-Path -LiteralPath $envExamplePath -PathType Leaf)) {
            Throw-TvpFailure -Code 'TVP-E101' -ExitCode 10 -Message '缺少 .env.example，請重新安裝完整的台V Pulse。'
        }
        Copy-Item -LiteralPath $envExamplePath -Destination $envPath
        $envCreated = $true
    }

    $apiKeyState = Get-ApiKeyState -EnvPath $envPath
    if ($CheckEnv) {
        if ($apiKeyState.Configured) {
            Write-Host "YOUTUBE_API_KEY 設定可辨識；共找到 $($apiKeyState.Occurrences) 行同名設定，採用最後一行。" -ForegroundColor Green
        } else {
            Write-Host "[TVP-E301] 尚未設定 YouTube API Key；共找到 $($apiKeyState.Occurrences) 行同名設定。" -ForegroundColor Yellow
            $exitCode = 30
        }
    } else {
        if ($envCreated -or -not $apiKeyState.Configured -or $EditEnv) {
            if ($envCreated) {
                Write-Host "已建立設定檔：$envPath" -ForegroundColor Yellow
            } elseif (-not $apiKeyState.Configured) {
                Write-Host "尚未設定 YouTube API Key，正在開啟設定檔。" -ForegroundColor Yellow
            } else {
                Write-Host '正在開啟設定檔。'
            }

            try {
                Start-Process notepad.exe -ArgumentList @("`"$envPath`"")
            } catch {
                Write-Warning "無法自動開啟記事本。請手動開啟：$envPath"
            }

            if (-not $apiKeyState.Configured) {
                Throw-TvpFailure -Code 'TVP-E301' -ExitCode 30 -Message '請在設定檔填入 YOUTUBE_API_KEY，儲存後再按一次「啟動」。請勿把 API Key 放進診斷報告或傳給他人。'
            }
        }

        $nodeExe = Find-CompatibleNode
        $pythonExe = Find-CompatiblePython
        if (-not $nodeExe -or -not $pythonExe) {
            $installerPath = Join-Path $projectRoot 'install-prerequisites.ps1'
            if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
                Throw-TvpFailure -Code 'TVP-E102' -ExitCode 10 -Message '缺少 install-prerequisites.ps1，請重新安裝完整的台V Pulse。'
            }

            & $installerPath -NeedNode:(-not [bool]$nodeExe) -NeedPython:(-not [bool]$pythonExe)
            Refresh-ProcessPath
            $nodeExe = Find-CompatibleNode
            $pythonExe = Find-CompatiblePython

            if (-not $nodeExe -or -not $pythonExe) {
                $stillMissing = @()
                if (-not $nodeExe) { $stillMissing += 'Node.js 22.13.0 以上' }
                if (-not $pythonExe) { $stillMissing += 'Python 3.11 以上' }
                Throw-TvpFailure -Code 'TVP-E201' -ExitCode 20 -Message "尚未偵測到：$($stillMissing -join '、')。完成官方安裝後，請關閉再重新開啟台V Pulse。"
            }
        }

        Write-Host "Node.js：$(& $nodeExe --version)"
        Write-Host "Python：$(& $pythonExe --version)"
        Write-Host 'YouTube API Key 設定可辨識；內容不會寫入 LOG。' -ForegroundColor Green

        Initialize-PythonRuntime -PythonExe $pythonExe -ProjectRoot $projectRoot -WorkDirectory $workDir

        $packageLockPath = Join-Path $projectRoot 'package-lock.json'
        if (-not (Test-Path -LiteralPath $packageLockPath -PathType Leaf)) {
            Throw-TvpFailure -Code 'TVP-E103' -ExitCode 10 -Message '缺少 package-lock.json，請重新安裝完整的台V Pulse。'
        }
        $vinextCli = Join-Path $projectRoot 'node_modules\vinext\dist\cli.js'
        $dependencyStatePath = Join-Path $workDir 'package-lock.sha256'
        $packageLockHash = Get-FileSha256 -Path $packageLockPath
        $recordedPackageLockHash = if (Test-Path -LiteralPath $dependencyStatePath -PathType Leaf) {
            (Get-Content -LiteralPath $dependencyStatePath -Raw -Encoding ASCII).Trim().ToLowerInvariant()
        } else {
            ''
        }
        $vinextReady = Test-Path -LiteralPath $vinextCli -PathType Leaf
        if ($vinextReady -and [string]::IsNullOrWhiteSpace($recordedPackageLockHash)) {
            $packageLockHash | Set-Content -LiteralPath $dependencyStatePath -Encoding ASCII
            $recordedPackageLockHash = $packageLockHash
        }
        $needsNpmInstall = -not $vinextReady -or $recordedPackageLockHash -ne $packageLockHash
        if ($needsNpmInstall) {
            if (-not $vinextReady -and -not (Request-NpmInstall)) {
                Throw-TvpFailure -Code 'TVP-E401' -ExitCode 40 -Message '已取消前端套件安裝。下次啟動時可重新選擇自動安裝。'
            }

            $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
            if (-not $npmCommand) { $npmCommand = Get-Command npm -ErrorAction SilentlyContinue }
            if (-not $npmCommand) {
                Throw-TvpFailure -Code 'TVP-E401' -ExitCode 40 -Message '已偵測到 Node.js，但找不到 npm。請重新安裝 Node.js LTS。'
            }

            $npmAction = if ($vinextReady) { '正在同步新版前端套件，可能需要幾分鐘…' } else { '正在安裝台V Pulse 前端套件，可能需要幾分鐘…' }
            Write-Host $npmAction -ForegroundColor Cyan
            Push-Location $projectRoot
            try {
                & $npmCommand.Source install --no-audit --no-fund
                if ($LASTEXITCODE -ne 0) {
                    Throw-TvpFailure -Code 'TVP-E402' -ExitCode 41 -Message "npm install 失敗，程序結束碼：$LASTEXITCODE。請匯出診斷報告。"
                }
            } finally {
                Pop-Location
            }

            if (-not (Test-Path -LiteralPath $vinextCli)) {
                Throw-TvpFailure -Code 'TVP-E402' -ExitCode 41 -Message 'npm install 已結束，但仍找不到 vinext。請匯出診斷報告。'
            }
            $packageLockHash | Set-Content -LiteralPath $dependencyStatePath -Encoding ASCII
        }

        # Some desktop runtimes expose both Path and PATH. Windows Start-Process
        # treats them as duplicate dictionary keys, so normalize before launch.
        $processPath = [System.Environment]::GetEnvironmentVariable('Path', 'Process')
        [System.Environment]::SetEnvironmentVariable('PATH', $null, 'Process')
        [System.Environment]::SetEnvironmentVariable('Path', $processPath, 'Process')

        $alreadyRunning = $false
        if (Test-Path -LiteralPath $pidFile) {
            try {
                $recorded = Get-Content -Raw -LiteralPath $pidFile -Encoding UTF8 | ConvertFrom-Json
                $recordedApiPid = Get-RecordedPid $recorded.api
                $recordedWebPid = Get-RecordedPid $recorded.web
                $recordedApi = if ($recordedApiPid) { Get-Process -Id $recordedApiPid -ErrorAction SilentlyContinue } else { $null }
                $recordedWeb = if ($recordedWebPid) { Get-Process -Id $recordedWebPid -ErrorAction SilentlyContinue } else { $null }
                if ($recordedApi -and $recordedWeb -and
                    (Wait-HttpReady -Url 'http://127.0.0.1:8787/api/health' -TimeoutSeconds 3) -and
                    (Wait-HttpReady -Url 'http://127.0.0.1:3000' -TimeoutSeconds 3)) {
                    $alreadyRunning = $true
                } else {
                    Stop-StaleRecordedProcess -Record $recorded.api -AllowedNames @('python', 'python3', 'pythonw')
                    Stop-StaleRecordedProcess -Record $recorded.web -AllowedNames @('node')
                    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
                }
            } catch {
                Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
            }
        }

        if (-not $alreadyRunning) {
            $apiLog = Join-Path $workDir 'api.log'
            $apiErrorLog = Join-Path $workDir 'api-error.log'
            $webLog = Join-Path $workDir 'web.log'
            $webErrorLog = Join-Path $workDir 'web-error.log'

            $apiProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','collector.server') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $apiLog -RedirectStandardError $apiErrorLog -PassThru
            $webArguments = @("`"$vinextCli`"", 'dev', '--hostname', '127.0.0.1', '--port', '3000')
            $webProcess = Start-Process -FilePath $nodeExe -ArgumentList $webArguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $webLog -RedirectStandardError $webErrorLog -PassThru

            [ordered]@{
                started_at = (Get-Date).ToUniversalTime().ToString('o')
                api = [ordered]@{ pid = $apiProcess.Id; executable = $pythonExe }
                web = [ordered]@{ pid = $webProcess.Id; executable = $nodeExe }
            } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidFile -Encoding UTF8

            if (-not (Wait-HttpReady -Url 'http://127.0.0.1:8787/api/health' -TimeoutSeconds 30 -Process $apiProcess)) {
                $apiProcess.Refresh()
                if ($apiProcess.HasExited) { $apiProcess.WaitForExit() }
                $detail = if ($apiProcess.HasExited) { "後端程序提前結束，程序結束碼：$($apiProcess.ExitCode)。" } else { '後端程序未在 30 秒內回應。' }
                Throw-TvpFailure -Code 'TVP-E501' -ExitCode 50 -Message "$detail 請匯出診斷報告。"
            }

            if (-not (Wait-HttpReady -Url 'http://127.0.0.1:3000' -TimeoutSeconds 90 -Process $webProcess)) {
                $webProcess.Refresh()
                if ($webProcess.HasExited) { $webProcess.WaitForExit() }
                $detail = if ($webProcess.HasExited) { "前端程序提前結束，程序結束碼：$($webProcess.ExitCode)。" } else { '前端未在 90 秒內完成第一次啟動。' }
                Throw-TvpFailure -Code 'TVP-E502' -ExitCode 51 -Message "$detail 請匯出診斷報告。"
            }
        }

        if (Test-Path -LiteralPath $lastErrorFile) {
            Remove-Item -LiteralPath $lastErrorFile -Force -ErrorAction SilentlyContinue
        }
        if (-not $NoOpen) {
            Start-Process 'http://localhost:3000'
        }
        Write-Host '台V Pulse 已啟動：http://localhost:3000' -ForegroundColor Green
        if ($alreadyRunning) { Write-Host '偵測到既有服務，未重複啟動。' }
    }
} catch {
    $failureCode = [string]$_.Exception.Data['TvpCode']
    $failureExitCode = $_.Exception.Data['TvpExitCode']
    if ([string]::IsNullOrWhiteSpace($failureCode)) { $failureCode = 'TVP-E900' }
    if ($null -eq $failureExitCode) { $failureExitCode = 90 }
    $exitCode = [int]$failureExitCode

    [ordered]@{
        code = $failureCode
        time = (Get-Date).ToUniversalTime().ToString('o')
        message = $_.Exception.Message
        script = 'start-local.ps1'
        log = $sessionLog
    } | ConvertTo-Json | Set-Content -LiteralPath $lastErrorFile -Encoding UTF8

    Write-Host "[$failureCode] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "啟動 LOG：$sessionLog" -ForegroundColor Yellow
    Write-Host '可從台V Pulse 啟動器按「匯出診斷報告」後提供 ZIP 供查閱。' -ForegroundColor Yellow
} finally {
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
    }
}

exit $exitCode
