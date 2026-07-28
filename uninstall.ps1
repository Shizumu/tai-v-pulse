[CmdletBinding()]
param(
    [switch]$Quiet,
    [switch]$RemoveData
)

$ErrorActionPreference = 'Stop'
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
    $OutputEncoding = $utf8WithoutBom
} catch {}
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dataRoot = Join-Path $env:LOCALAPPDATA 'TaiVPulse'
$preserveRoot = Join-Path $dataRoot "PreservedData-$timestamp"
$removeUserData = [bool]$RemoveData

try {
    if (-not $Quiet) {
        Add-Type -AssemblyName System.Windows.Forms
        $choice = [System.Windows.Forms.MessageBox]::Show(
            "要解除安裝台V Pulse 嗎？`r`n`r`n按「是」：保留 .env、資料庫與本機分析資料。`r`n按「否」：永久刪除所有本機資料。`r`n按「取消」：不解除安裝。",
            '解除安裝台V Pulse',
            [System.Windows.Forms.MessageBoxButtons]::YesNoCancel,
            [System.Windows.Forms.MessageBoxIcon]::Warning
        )
        if ($choice -eq [System.Windows.Forms.DialogResult]::Cancel) {
            Write-Output 'UNINSTALL_CANCELLED=1'
            exit 0
        }
        $removeUserData = $choice -eq [System.Windows.Forms.DialogResult]::No
        if ($removeUserData) {
            $confirmDelete = [System.Windows.Forms.MessageBox]::Show(
                "確定要永久刪除所有台V Pulse 本機資料嗎？`r`n`r`n這會刪除 API Key 設定、SQLite、Studio 結構化資料、既有保留資料與 LOG，而且無法復原。",
                '永久刪除本機資料',
                [System.Windows.Forms.MessageBoxButtons]::YesNo,
                [System.Windows.Forms.MessageBoxIcon]::Error
            )
            if ($confirmDelete -ne [System.Windows.Forms.DialogResult]::Yes) {
                Write-Output 'UNINSTALL_CANCELLED=1'
                exit 0
            }
        }
    }

    $stopScript = Join-Path $projectRoot 'stop-local.ps1'
    if (Test-Path -LiteralPath $stopScript -PathType Leaf) {
        & $stopScript -Quiet
    }

    $hasData = (Test-Path -LiteralPath (Join-Path $projectRoot '.env') -PathType Leaf) -or
        (Test-Path -LiteralPath (Join-Path $projectRoot 'work') -PathType Container)
    if ($hasData -and -not $removeUserData) {
        New-Item -ItemType Directory -Force -Path $preserveRoot | Out-Null
        foreach ($name in @('.env', 'work')) {
            $source = Join-Path $projectRoot $name
            if (Test-Path -LiteralPath $source) {
                Move-Item -LiteralPath $source -Destination (Join-Path $preserveRoot $name) -Force
            }
        }
    }

    $desktop = [Environment]::GetFolderPath('Desktop')
    $startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) '台V Pulse'
    Remove-Item -LiteralPath (Join-Path $desktop '台V Pulse.lnk') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $startMenu -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TaiVPulse' -Recurse -Force -ErrorAction SilentlyContinue

    $cleanupScript = Join-Path ([System.IO.Path]::GetTempPath()) "tai-v-pulse-uninstall-$timestamp.cmd"
    $escapedProjectRoot = $projectRoot.Replace('%', '%%')
    $cleanupLines = @(
        '@echo off',
        'timeout /t 2 /nobreak >nul',
        "rmdir /s /q `"$escapedProjectRoot`""
    )
    if ($removeUserData) {
        $localAppDataRoot = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd([char[]]@('\', '/'))
        $dataRootFull = [System.IO.Path]::GetFullPath($dataRoot).TrimEnd([char[]]@('\', '/'))
        $expectedDataRoot = [System.IO.Path]::GetFullPath((Join-Path $localAppDataRoot 'TaiVPulse')).TrimEnd([char[]]@('\', '/'))
        if (-not $dataRootFull.Equals($expectedDataRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "拒絕刪除非預期資料路徑：$dataRootFull"
        }
        $escapedDataRoot = $dataRootFull.Replace('%', '%%')
        $cleanupLines += "rmdir /s /q `"$escapedDataRoot`""
    }
    $cleanupLines += 'del /q "%~f0"'
    $cleanupLines | Set-Content -LiteralPath $cleanupScript -Encoding ASCII
    Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', "`"$cleanupScript`"") -WindowStyle Hidden

    if (-not $Quiet) {
        $message = if ($removeUserData) {
            '台V Pulse 已解除安裝，所有本機資料將永久刪除。'
        } elseif ($hasData) {
            "台V Pulse 已解除安裝。`r`n本機資料已保留在：`r`n$preserveRoot"
        } else {
            '台V Pulse 已解除安裝。'
        }
        [System.Windows.Forms.MessageBox]::Show($message, '台V Pulse', 'OK', 'Information') | Out-Null
    }
    exit 0
} catch {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            "[TVP-U001] 解除安裝失敗：`r`n$($_.Exception.Message)",
            '台V Pulse',
            'OK',
            'Error'
        ) | Out-Null
    } catch {}
    exit 80
}
