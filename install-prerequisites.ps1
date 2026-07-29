[CmdletBinding()]
param(
    [switch]$NeedNode,
    [switch]$NeedPython,
    [ValidateSet('Prompt', 'AutoInstall', 'OpenDownloads', 'Cancel')]
    [string]$Action = 'Prompt'
)

$ErrorActionPreference = 'Stop'
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
    $OutputEncoding = $utf8WithoutBom
} catch {}
$nodeDownloadUrl = 'https://nodejs.org/en/download'
$pythonDownloadUrl = 'https://www.python.org/downloads/windows/'
$missing = @()
if ($NeedNode) { $missing += 'Node.js 22.13.0 以上' }
if ($NeedPython) { $missing += 'Python 3.11 以上' }

if ($missing.Count -eq 0) {
    Write-Host 'Node.js 與 Python 已符合執行需求。' -ForegroundColor Green
    return
}

function Open-OfficialDownloads {
    if ($NeedNode) { Start-Process $nodeDownloadUrl }
    if ($NeedPython) { Start-Process $pythonDownloadUrl }
    Write-Host '已開啟官方下載頁。安裝完成後請重新執行 start-local.ps1。' -ForegroundColor Yellow
}

function Request-InstallAction {
    $wingetAvailable = [bool](Get-Command winget -ErrorAction SilentlyContinue)
    $missingLabel = $missing -join '、'

    try {
        Add-Type -AssemblyName System.Windows.Forms
        if ($wingetAvailable) {
            $message = "台V Pulse 需要先安裝：$missingLabel。`r`n`r`n是：使用 Windows Package Manager 自動安裝`r`n否：開啟官方下載頁手動安裝`r`n取消：暫不安裝"
            $choice = [System.Windows.Forms.MessageBox]::Show(
                $message,
                '台V Pulse 首次安裝',
                [System.Windows.Forms.MessageBoxButtons]::YesNoCancel,
                [System.Windows.Forms.MessageBoxIcon]::Information
            )
            if ($choice -eq [System.Windows.Forms.DialogResult]::Yes) { return 'AutoInstall' }
            if ($choice -eq [System.Windows.Forms.DialogResult]::No) { return 'OpenDownloads' }
            return 'Cancel'
        }

        $message = "台V Pulse 需要先安裝：$missingLabel。`r`n`r`n這台電腦沒有 Windows Package Manager。要開啟 Node.js／Python 官方下載頁嗎？"
        $choice = [System.Windows.Forms.MessageBox]::Show(
            $message,
            '台V Pulse 首次安裝',
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Information
        )
        if ($choice -eq [System.Windows.Forms.DialogResult]::Yes) { return 'OpenDownloads' }
        return 'Cancel'
    } catch {
        Write-Host "台V Pulse 需要先安裝：$missingLabel。" -ForegroundColor Yellow
        if ($wingetAvailable) {
            Write-Host '[1] 使用 Windows Package Manager 自動安裝'
            Write-Host '[2] 開啟官方下載頁手動安裝'
            Write-Host '[3] 取消'
            $choice = Read-Host '請輸入 1、2 或 3'
            if ($choice -eq '1') { return 'AutoInstall' }
            if ($choice -eq '2') { return 'OpenDownloads' }
            return 'Cancel'
        }

        Write-Host '這台電腦沒有 Windows Package Manager，將開啟官方下載頁。'
        return 'OpenDownloads'
    }
}

if ($Action -eq 'Prompt') {
    $Action = Request-InstallAction
}

if ($Action -eq 'Cancel') {
    Write-Host '已取消安裝。'
    return
}

if ($Action -eq 'OpenDownloads') {
    Open-OfficialDownloads
    return
}

$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
    Write-Warning '找不到 Windows Package Manager，改為開啟官方下載頁。'
    Open-OfficialDownloads
    return
}

$packages = @()
if ($NeedNode) {
    $packages += [pscustomobject]@{
        Name = 'Node.js LTS'
        Id = 'OpenJS.NodeJS.LTS'
        DownloadUrl = $nodeDownloadUrl
    }
}
if ($NeedPython) {
    $packages += [pscustomobject]@{
        Name = 'Python 3.13'
        Id = 'Python.Python.3.13'
        DownloadUrl = $pythonDownloadUrl
    }
}

foreach ($package in $packages) {
    Write-Host "正在安裝 $($package.Name)…" -ForegroundColor Cyan
    & $winget.Source install `
        --id $package.Id `
        --exact `
        --source winget `
        --accept-package-agreements `
        --accept-source-agreements

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "$($package.Name) 自動安裝失敗，將開啟官方下載頁。"
        Start-Process $package.DownloadUrl
    }
}

Write-Host '安裝流程已結束。台V Pulse 會重新檢查版本；若仍未偵測到，請重新開啟 PowerShell。' -ForegroundColor Green
