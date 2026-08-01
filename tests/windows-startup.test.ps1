[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $projectRoot 'start-local.ps1'
$requirementsPath = Join-Path $projectRoot 'requirements.txt'
$uninstallScript = Join-Path $projectRoot 'uninstall.ps1'
$launcherSource = Join-Path $projectRoot 'windows\TaiVPulseLauncher.cs'
$updaterSource = Join-Path $projectRoot 'windows\TaiVPulseUpdater.cs'
$installerSource = Join-Path $projectRoot 'windows\TaiVPulseInstaller.cs'
$installerBuildScript = Join-Path $projectRoot 'scripts\build-windows-installer.ps1'
$publicPackageScript = Join-Path $projectRoot 'scripts\package-public.ps1'
$diagnosticScript = Join-Path $projectRoot 'export-diagnostics.ps1'
$releaseWorkflow = Join-Path $projectRoot '.github\workflows\release.yml'
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
    $updaterText = Get-Content -LiteralPath $updaterSource -Raw -Encoding UTF8
    $installerText = Get-Content -LiteralPath $installerSource -Raw -Encoding UTF8
    $installerBuildText = Get-Content -LiteralPath $installerBuildScript -Raw -Encoding UTF8
    $publicPackageText = Get-Content -LiteralPath $publicPackageScript -Raw -Encoding UTF8
    $diagnosticText = Get-Content -LiteralPath $diagnosticScript -Raw -Encoding UTF8
    $releaseWorkflowText = Get-Content -LiteralPath $releaseWorkflow -Raw -Encoding UTF8
    Assert-True -Condition $uninstallSource.Contains('[switch]$RemoveData') -Message '解除安裝腳本缺少永久刪除資料選項。'
    Assert-True -Condition $uninstallSource.Contains('PreservedData-') -Message '解除安裝腳本缺少資料保留路徑。'
    Assert-True -Condition $launcherText.Contains('解除安裝') -Message 'Windows 啟動器缺少解除安裝入口。'
    Assert-True -Condition $launcherText.Contains('UseShellExecute = true') -Message 'Windows 啟動器未使用 Shell 開啟預設瀏覽器。'
    Assert-True -Condition $launcherText.Contains('HandleStartupOutput') -Message 'Windows 啟動器未在服務就緒訊息出現時開啟網頁。'
    Assert-True -Condition $launcherText.Contains('後台動態：') -Message 'Windows 啟動器缺少後台工作狀態。'
    Assert-True -Condition $launcherText.Contains('hourly-live-scan') -Message 'Windows 啟動器缺少整點開台偵測工作標籤。'
    Assert-True -Condition (-not $launcherText.Contains('UseWaitCursor = value')) -Message 'Windows 啟動器仍會把整個介面切成等待游標。'
    Assert-True -Condition $launcherText.Contains('while (!process.WaitForExit(250))') -Message 'Windows 啟動器未使用有限等待確認啟動腳本結束。'
    Assert-True -Condition (-not $launcherText.Contains('process.WaitForExit();')) -Message 'Windows 啟動器仍可能無期限等待長時間服務保留的輸出管線。'
    Assert-True -Condition $launcherText.Contains('process.CancelOutputRead()') -Message 'Windows 啟動器未在腳本結束後釋放標準輸出讀取。'
    Assert-True -Condition $launcherText.Contains('api.github.com/repos/Shizumu/tai-v-pulse/releases/latest') -Message 'Windows 啟動器缺少正式 GitHub Release 更新來源。'
    Assert-True -Condition $launcherText.Contains('SHA-256 驗證失敗') -Message 'Windows 啟動器未拒絕 SHA-256 不符的更新。'
    Assert-True -Condition $launcherText.Contains('MessageBoxButtons.YesNo') -Message 'Windows 啟動器更新前未要求使用者確認。'
    Assert-True -Condition $launcherText.Contains('activity.CurrentJob') -Message 'Windows 啟動器未避開正在執行的背景資料工作。'
    Assert-True -Condition $updaterText.Contains('WaitForLauncher') -Message '獨立更新輔助程式未等待舊啟動器結束。'
    Assert-True -Condition $updaterText.Contains('--silent --no-launch --install-dir') -Message '獨立更新輔助程式未沿用安全覆蓋安裝流程。'
    Assert-True -Condition $updaterText.Contains('VersionMatches') -Message '獨立更新輔助程式未驗證安裝後版本。'
    Assert-True -Condition $installerText.Contains('"TaiVPulse-" + Version + ".ico"') -Message 'Windows 安裝器未使用版本化的最新版捷徑圖示。'
    Assert-True -Condition $installerText.Contains('iconPath + ",0"') -Message 'Windows 捷徑未明確指定最新版圖示檔。'
    Assert-True -Condition $installerBuildText.Contains('$installedIconName = "TaiVPulse-$version.ico"') -Message 'Windows 安裝包未帶入版本化的最新版圖示檔。'
    Assert-True -Condition $installerBuildText.Contains("Join-Path `$releaseRoot 'TaiVPulseUpdater.exe'") -Message 'Windows 安裝包未編譯獨立更新輔助程式。'
    Assert-True -Condition $publicPackageText.Contains("@('.github', 'app'") -Message '公開原始碼白名單未包含 GitHub Release workflow。'
    Assert-True -Condition $releaseWorkflowText.Contains('tags:') -Message 'GitHub Release workflow 未限制由版本標籤觸發。'
    Assert-True -Condition $releaseWorkflowText.Contains('scripts\package-public.ps1') -Message 'GitHub Release workflow 未使用公開白名單打包流程。'
    Assert-True -Condition $releaseWorkflowText.Contains('scripts\build-windows-installer.ps1') -Message 'GitHub Release workflow 未建立 Windows 安裝程式。'
    Assert-True -Condition $releaseWorkflowText.Contains('gh release create') -Message 'GitHub Release workflow 未發布驗證後成品。'
    Assert-True -Condition $diagnosticText.Contains("`$_.Name -like 'updater-*.log'") -Message '診斷報告未白名單收錄更新器 LOG。'

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
