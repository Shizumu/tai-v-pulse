[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $projectRoot 'start-local.ps1'
$requirementsPath = Join-Path $projectRoot 'requirements.txt'
$uninstallScript = Join-Path $projectRoot 'uninstall.ps1'
$launcherSource = Join-Path $projectRoot 'windows\TaiVPulseLauncher.cs'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("tai-v-pulse-startup-test-$([Guid]::NewGuid().ToString('N'))")
$originalPythonPath = [System.Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')

function Assert-True {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if (-not $Condition) { throw $Message }
}

try {
    $requirements = (Get-Content -LiteralPath $requirementsPath -Raw -Encoding UTF8).Trim()
    Assert-True -Condition ($requirements -eq 'tzdata==2026.3') -Message 'requirements.txt 未固定為 tzdata==2026.3。'

    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($startScript, [ref]$tokens, [ref]$parseErrors)
    Assert-True -Condition ($parseErrors.Count -eq 0) -Message 'start-local.ps1 無法由 Windows PowerShell parser 解析。'

    $requiredFunctions = @('Throw-TvpFailure', 'Get-FileSha256', 'Test-PythonTimeZoneData', 'Initialize-PythonRuntime')
    $functionDefinitions = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -in $requiredFunctions
    }, $true))
    foreach ($functionName in $requiredFunctions) {
        $definition = $functionDefinitions | Where-Object { $_.Name -eq $functionName } | Select-Object -First 1
        Assert-True -Condition ($null -ne $definition) -Message "start-local.ps1 缺少函式：$functionName"
        . ([scriptblock]::Create($definition.Extent.Text))
    }

    $startSource = Get-Content -LiteralPath $startScript -Raw -Encoding UTF8
    Assert-True -Condition $startSource.Contains("'--hostname', '127.0.0.1'") -Message '前端啟動未使用 vinext 支援的 --hostname 參數。'
    Assert-True -Condition (-not $startSource.Contains("'--host', '127.0.0.1'")) -Message '前端啟動仍含有 vinext 會忽略的 --host 參數。'

    $uninstallTokens = $null
    $uninstallParseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($uninstallScript, [ref]$uninstallTokens, [ref]$uninstallParseErrors)
    Assert-True -Condition ($uninstallParseErrors.Count -eq 0) -Message 'uninstall.ps1 無法由 Windows PowerShell parser 解析。'
    $uninstallSource = Get-Content -LiteralPath $uninstallScript -Raw -Encoding UTF8
    $launcherText = Get-Content -LiteralPath $launcherSource -Raw -Encoding UTF8
    Assert-True -Condition $uninstallSource.Contains('[switch]$RemoveData') -Message '解除安裝腳本缺少永久刪除資料選項。'
    Assert-True -Condition $uninstallSource.Contains('PreservedData-') -Message '解除安裝腳本缺少資料保留路徑。'
    Assert-True -Condition $launcherText.Contains('解除安裝') -Message 'Windows 啟動器缺少解除安裝入口。'
    Assert-True -Condition $launcherText.Contains('UseShellExecute = true') -Message 'Windows 啟動器未使用 Shell 開啟預設瀏覽器。'
    Assert-True -Condition $launcherText.Contains('HandleStartupOutput') -Message 'Windows 啟動器未在服務就緒訊息出現時開啟網頁。'
    Assert-True -Condition $launcherText.Contains('後台動態：') -Message 'Windows 啟動器缺少後台工作狀態。'
    Assert-True -Condition (-not $launcherText.Contains('UseWaitCursor = value')) -Message 'Windows 啟動器仍會把整個介面切成等待游標。'

    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $fakePython = Join-Path $testRoot 'fake-python.cmd'
    $markerPath = Join-Path $testRoot 'tzdata-installed.txt'
    $fakePythonSource = @'
@echo off
if "%~1"=="-c" (
  if exist "%TVP_FAKE_TZDATA_MARKER%" exit /b 0
  exit /b 1
)
if "%~1"=="-m" if "%~2"=="pip" (
  if "%~3"=="--version" (
    exit /b 0
  )
  >"%TVP_FAKE_TZDATA_MARKER%" echo installed
  exit /b 0
)
exit /b 99
'@
    [System.IO.File]::WriteAllText($fakePython, $fakePythonSource, [System.Text.Encoding]::ASCII)
    $env:TVP_FAKE_TZDATA_MARKER = $markerPath

    $workDirectory = Join-Path $testRoot 'work'
    New-Item -ItemType Directory -Path $workDirectory | Out-Null
    Initialize-PythonRuntime -PythonExe $fakePython -ProjectRoot $projectRoot -WorkDirectory $workDirectory
    Assert-True -Condition (Test-Path -LiteralPath $markerPath -PathType Leaf) -Message '缺少時區資料時未執行 pip 安裝。'
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path $workDirectory 'requirements.sha256') -PathType Leaf) -Message '時區資料安裝後未記錄 requirements 指紋。'
    $expectedPackages = Join-Path $workDirectory 'python-packages'
    Assert-True -Condition $env:PYTHONPATH.StartsWith($expectedPackages, [System.StringComparison]::OrdinalIgnoreCase) -Message 'PYTHONPATH 未優先指向台V Pulse 本機套件目錄。'

    Remove-Item -LiteralPath $markerPath -Force
    $fakePythonWithoutPip = Join-Path $testRoot 'fake-python-without-pip.cmd'
    [System.IO.File]::WriteAllText($fakePythonWithoutPip, "@echo off`r`nexit /b 1`r`n", [System.Text.Encoding]::ASCII)
    $caughtCode = $null
    try {
        Initialize-PythonRuntime -PythonExe $fakePythonWithoutPip -ProjectRoot $projectRoot -WorkDirectory $workDirectory
    } catch {
        $caughtCode = [string]$_.Exception.Data['TvpCode']
    }
    Assert-True -Condition ($caughtCode -eq 'TVP-E403') -Message 'pip 缺失時未回報 TVP-E403。'

    $migrationWorkDirectory = Join-Path $testRoot 'migration-work'
    New-Item -ItemType Directory -Path $migrationWorkDirectory | Out-Null
    $fakePythonWithExistingTimeZone = Join-Path $testRoot 'fake-python-existing-timezone.cmd'
    $fakePythonWithExistingTimeZoneSource = @'
@echo off
if "%~1"=="-c" exit /b 0
exit /b 1
'@
    [System.IO.File]::WriteAllText($fakePythonWithExistingTimeZone, $fakePythonWithExistingTimeZoneSource, [System.Text.Encoding]::ASCII)
    Initialize-PythonRuntime -PythonExe $fakePythonWithExistingTimeZone -ProjectRoot $projectRoot -WorkDirectory $migrationWorkDirectory
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path $migrationWorkDirectory 'requirements.sha256') -PathType Leaf) -Message '既有時區資料未建立 requirements 指紋。'

    Write-Host 'Windows 啟動、相依與解除安裝入口測試通過。' -ForegroundColor Green
} finally {
    [System.Environment]::SetEnvironmentVariable('PYTHONPATH', $originalPythonPath, 'Process')
    Remove-Item Env:TVP_FAKE_TZDATA_MARKER -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $testRoot -PathType Container) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
