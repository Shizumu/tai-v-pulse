[CmdletBinding()]
param(
    [string]$NodePath,
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false, $true)
$utf8WithBom = [System.Text.UTF8Encoding]::new($true)
try {
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
    $OutputEncoding = $utf8WithoutBom
} catch {}

$projectRoot = Split-Path -Parent $PSScriptRoot
$workDir = Join-Path $projectRoot 'work'
$logPath = Join-Path $workDir 'launcher-encoding-smoke.log'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("tai-v-pulse-encoding-test-$([Guid]::NewGuid().ToString('N'))")

function Assert-Contains {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not $Text.Contains($Expected)) {
        throw "$Label 缺少測試文字：$Expected"
    }
}

function Read-StrictUtf8 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    return $utf8WithoutBom.GetString($bytes)
}

try {
    New-Item -ItemType Directory -Force -Path $workDir, $testRoot | Out-Null

    $childScript = Join-Path $testRoot 'powershell-child.ps1'
    $childSource = @'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
Write-Output '尚未設定 YouTube API Key'
[Console]::Error.WriteLine('請在設定檔填入 YOUTUBE_API_KEY')
'@
    [System.IO.File]::WriteAllText($childScript, $childSource, $utf8WithBom)

    Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.IO;
using System.Text;

public static class TaiVPulseEncodingProbe
{
    public static int Run(string powershellPath, string scriptPath, string logPath)
    {
        Encoding utf8WithoutBom = new UTF8Encoding(false);
        object gate = new object();
        using (var writer = new StreamWriter(logPath, false, new UTF8Encoding(true)))
        using (var process = new Process())
        {
            writer.WriteLine("台V Pulse 啟動器");
            writer.Flush();
            process.StartInfo.FileName = powershellPath;
            process.StartInfo.Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + scriptPath + "\"";
            process.StartInfo.UseShellExecute = false;
            process.StartInfo.CreateNoWindow = true;
            process.StartInfo.RedirectStandardOutput = true;
            process.StartInfo.RedirectStandardError = true;
            process.StartInfo.StandardOutputEncoding = utf8WithoutBom;
            process.StartInfo.StandardErrorEncoding = utf8WithoutBom;

            DataReceivedEventHandler receive = delegate(object sender, DataReceivedEventArgs e)
            {
                if (e.Data == null) return;
                lock (gate)
                {
                    writer.WriteLine(e.Data);
                    writer.Flush();
                }
            };
            process.OutputDataReceived += receive;
            process.ErrorDataReceived += receive;
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            process.WaitForExit();
            process.WaitForExit();
            return process.ExitCode;
        }
    }
}
'@

    $windowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $exitCode = [TaiVPulseEncodingProbe]::Run($windowsPowerShell, $childScript, $logPath)
    if ($exitCode -ne 0) { throw "PowerShell UTF-8 子程序結束碼：$exitCode" }

    $logBytes = [System.IO.File]::ReadAllBytes($logPath)
    if ($logBytes.Length -lt 3 -or $logBytes[0] -ne 0xEF -or $logBytes[1] -ne 0xBB -or $logBytes[2] -ne 0xBF) {
        throw '啟動器測試 LOG 缺少 UTF-8 BOM。'
    }
    $logText = Read-StrictUtf8 -Path $logPath
    Assert-Contains -Text $logText -Expected '台V Pulse 啟動器' -Label '啟動器測試 LOG'
    Assert-Contains -Text $logText -Expected '尚未設定 YouTube API Key' -Label '啟動器測試 LOG'
    Assert-Contains -Text $logText -Expected '請在設定檔填入 YOUTUBE_API_KEY' -Label '啟動器測試 LOG'

    $python = if ([string]::IsNullOrWhiteSpace($PythonPath)) {
        (Get-Command python -ErrorAction Stop).Source
    } else {
        (Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path
    }
    $node = if ([string]::IsNullOrWhiteSpace($NodePath)) {
        (Get-Command node -ErrorAction Stop).Source
    } else {
        (Resolve-Path -LiteralPath $NodePath -ErrorAction Stop).Path
    }
    $pythonSource = Join-Path $testRoot 'utf8-probe.py'
    $nodeSource = Join-Path $testRoot 'utf8-probe.js'
    [System.IO.File]::WriteAllText($pythonSource, "print('台V Pulse 啟動器')`n", $utf8WithoutBom)
    [System.IO.File]::WriteAllText($nodeSource, "console.log('尚未設定 YouTube API Key');`n", $utf8WithoutBom)
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $processPath = [System.Environment]::GetEnvironmentVariable('Path', 'Process')
    [System.Environment]::SetEnvironmentVariable('PATH', $null, 'Process')
    [System.Environment]::SetEnvironmentVariable('Path', $processPath, 'Process')

    $pythonLog = Join-Path $testRoot 'python.log'
    $pythonErrorLog = Join-Path $testRoot 'python-error.log'
    $pythonProcess = Start-Process -FilePath $python -ArgumentList @("`"$pythonSource`"") -RedirectStandardOutput $pythonLog -RedirectStandardError $pythonErrorLog -PassThru -Wait -WindowStyle Hidden
    if ($pythonProcess.ExitCode -ne 0) { throw "Python UTF-8 測試結束碼：$($pythonProcess.ExitCode)" }
    Assert-Contains -Text (Read-StrictUtf8 -Path $pythonLog) -Expected '台V Pulse 啟動器' -Label 'Python stdout LOG'

    $nodeLog = Join-Path $testRoot 'node.log'
    $nodeErrorLog = Join-Path $testRoot 'node-error.log'
    $nodeProcess = Start-Process -FilePath $node -ArgumentList @("`"$nodeSource`"") -RedirectStandardOutput $nodeLog -RedirectStandardError $nodeErrorLog -PassThru -Wait -WindowStyle Hidden
    if ($nodeProcess.ExitCode -ne 0) { throw "Node.js UTF-8 測試結束碼：$($nodeProcess.ExitCode)" }
    Assert-Contains -Text (Read-StrictUtf8 -Path $nodeLog) -Expected '尚未設定 YouTube API Key' -Label 'Node.js stdout LOG'

    $diagnosticOutput = Join-Path $testRoot 'diagnostics'
    New-Item -ItemType Directory -Path $diagnosticOutput | Out-Null
    $diagnosticStdout = Join-Path $testRoot 'diagnostics-stdout.log'
    $diagnosticStderr = Join-Path $testRoot 'diagnostics-stderr.log'
    $diagnosticScript = Join-Path $projectRoot 'export-diagnostics.ps1'
    $diagnosticArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -OutputDirectory "{1}"' -f $diagnosticScript, $diagnosticOutput
    $diagnosticProcess = Start-Process -FilePath $windowsPowerShell -ArgumentList $diagnosticArguments -RedirectStandardOutput $diagnosticStdout -RedirectStandardError $diagnosticStderr -PassThru -Wait -WindowStyle Hidden
    if ($diagnosticProcess.ExitCode -ne 0) {
        throw "診斷匯出 UTF-8 測試結束碼：$($diagnosticProcess.ExitCode)"
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archivePath = (Get-ChildItem -LiteralPath $diagnosticOutput -Filter '*.zip' -File | Select-Object -First 1).FullName
    if ([string]::IsNullOrWhiteSpace($archivePath)) { throw '診斷匯出未產生 ZIP。' }
    $archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $entry = $archive.Entries | Where-Object { $_.Name -eq 'launcher-encoding-smoke.log' } | Select-Object -First 1
        if (-not $entry) { throw '診斷 ZIP 缺少啟動器 UTF-8 測試 LOG。' }
        $reader = New-Object System.IO.StreamReader($entry.Open(), $utf8WithoutBom, $true)
        try { $diagnosticLogText = $reader.ReadToEnd() } finally { $reader.Dispose() }
    } finally {
        $archive.Dispose()
    }
    Assert-Contains -Text $diagnosticLogText -Expected '台V Pulse 啟動器' -Label '診斷 ZIP LOG'
    Assert-Contains -Text $diagnosticLogText -Expected '尚未設定 YouTube API Key' -Label '診斷 ZIP LOG'
    Assert-Contains -Text $diagnosticLogText -Expected '請在設定檔填入 YOUTUBE_API_KEY' -Label '診斷 ZIP LOG'

    $powerShellFiles = Get-ChildItem -LiteralPath $projectRoot -Filter '*.ps1' -File -Recurse | Where-Object {
        $_.FullName -notmatch '\\(node_modules|\.next|dist|outputs|work)\\'
    }
    foreach ($powerShellFile in $powerShellFiles) {
        $bytes = [System.IO.File]::ReadAllBytes($powerShellFile.FullName)
        if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
            throw "Windows PowerShell 腳本缺少 UTF-8 BOM：$($powerShellFile.FullName)"
        }
        [void]$utf8WithoutBom.GetString($bytes)
    }

    Write-Host "UTF-8 編碼測試通過：$logPath" -ForegroundColor Green
    Write-Host $logText
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
