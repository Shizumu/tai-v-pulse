[CmdletBinding()]
param(
    [string]$OutputDirectory,
    [switch]$KeepTemporary
)

$ErrorActionPreference = 'Stop'
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
    $OutputEncoding = $utf8WithoutBom
} catch {}
$projectRoot = Split-Path -Parent $PSScriptRoot
$packageJson = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'package.json') -Encoding UTF8 | ConvertFrom-Json
$version = [string]$packageJson.version
if ([string]::IsNullOrWhiteSpace($version)) {
    throw '[TVP-B001] package.json 未設定版本。'
}

$outputRoot = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    Join-Path $projectRoot 'outputs'
} elseif ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
}

$installerName = "tai-v-pulse-$version-setup.exe"
$installerPath = Join-Path $outputRoot $installerName
$checksumPath = "$installerPath.sha256"
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
foreach ($target in @($installerPath, $checksumPath)) {
    if (Test-Path -LiteralPath $target) {
        throw "[TVP-B002] 輸出已存在，為避免覆寫請先移走：$target"
    }
}

$tempParent = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
$tempRoot = Join-Path $tempParent ("tai-v-pulse-build-$([Guid]::NewGuid().ToString('N'))")
$tempRootFull = [System.IO.Path]::GetFullPath($tempRoot)
if (-not $tempRootFull.StartsWith("$tempParent\tai-v-pulse-build-", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '[TVP-B003] 暫存建置路徑不安全。'
}

function New-RoundedRectanglePath {
    param(
        [Parameter(Mandatory = $true)][System.Drawing.RectangleF]$Bounds,
        [Parameter(Mandatory = $true)][float]$Radius
    )
    $diameter = $Radius * 2
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddArc($Bounds.Left, $Bounds.Top, $diameter, $diameter, 180, 90)
    $path.AddArc($Bounds.Right - $diameter, $Bounds.Top, $diameter, $diameter, 270, 90)
    $path.AddArc($Bounds.Right - $diameter, $Bounds.Bottom - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($Bounds.Left, $Bounds.Bottom - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

function New-TaiVPulseIconBitmap {
    param([Parameter(Mandatory = $true)][int]$Size)
    $bitmap = New-Object System.Drawing.Bitmap $Size, $Size
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $scale = $Size / 256.0
        $graphics.ScaleTransform($scale, $scale)

        # Match the web brand mark: #15201c rounded square, white V and #efd16c hard shadow.
        $mainBounds = New-Object System.Drawing.RectangleF 12, 12, 212, 212
        $shadowBounds = New-Object System.Drawing.RectangleF 36, 36, 212, 212
        $shadowPath = New-RoundedRectanglePath -Bounds $shadowBounds -Radius 64
        $mainPath = New-RoundedRectanglePath -Bounds $mainBounds -Radius 64
        $shadowBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 239, 209, 108))
        $mainBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 21, 32, 28))
        $letterBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
        $letterPath = New-Object System.Drawing.Drawing2D.GraphicsPath
        $fontFamily = New-Object System.Drawing.FontFamily 'Arial'
        $stringFormat = [System.Drawing.StringFormat]::GenericTypographic
        try {
            $graphics.FillPath($shadowBrush, $shadowPath)
            $graphics.FillPath($mainBrush, $mainPath)
            $letterPath.AddString(
                'V',
                $fontFamily,
                [int][System.Drawing.FontStyle]::Bold,
                132,
                (New-Object System.Drawing.PointF 0, 0),
                $stringFormat
            )
            $letterBounds = $letterPath.GetBounds()
            $matrix = New-Object System.Drawing.Drawing2D.Matrix
            try {
                $matrix.Translate(
                    [float](118 - ($letterBounds.Left + ($letterBounds.Width / 2))),
                    [float](114 - ($letterBounds.Top + ($letterBounds.Height / 2)))
                )
                $letterPath.Transform($matrix)
            } finally {
                $matrix.Dispose()
            }
            $graphics.FillPath($letterBrush, $letterPath)
        } finally {
            $fontFamily.Dispose()
            $letterPath.Dispose()
            $letterBrush.Dispose()
            $mainBrush.Dispose()
            $shadowBrush.Dispose()
            $mainPath.Dispose()
            $shadowPath.Dispose()
        }
    } finally {
        $graphics.Dispose()
    }
    return $bitmap
}

function New-TaiVPulseIcon {
    param([Parameter(Mandatory = $true)][string]$Path)
    Add-Type -AssemblyName System.Drawing
    $sizes = @(16, 20, 24, 32, 40, 48, 64, 128, 256)
    $images = @()
    foreach ($size in $sizes) {
        $bitmap = New-TaiVPulseIconBitmap -Size $size
        $memory = New-Object System.IO.MemoryStream
        try {
            $bitmap.Save($memory, [System.Drawing.Imaging.ImageFormat]::Png)
            $images += ,$memory.ToArray()
        } finally {
            $memory.Dispose()
            $bitmap.Dispose()
        }
    }

    $stream = [System.IO.File]::Create($Path)
    $writer = New-Object System.IO.BinaryWriter $stream
    try {
        $writer.Write([UInt16]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]$sizes.Count)
        $imageOffset = 6 + (16 * $sizes.Count)
        for ($index = 0; $index -lt $sizes.Count; $index++) {
            $dimension = if ($sizes[$index] -ge 256) { [byte]0 } else { [byte]$sizes[$index] }
            $writer.Write($dimension)
            $writer.Write($dimension)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([UInt16]1)
            $writer.Write([UInt16]32)
            $writer.Write([UInt32]$images[$index].Length)
            $writer.Write([UInt32]$imageOffset)
            $imageOffset += $images[$index].Length
        }
        foreach ($image in $images) {
            $writer.Write([byte[]]$image)
        }
    } finally {
        $writer.Dispose()
        $stream.Dispose()
    }
}

function Invoke-CSharpCompiler {
    param(
        [Parameter(Mandatory = $true)][string]$Compiler,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    & $Compiler @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "[TVP-B004] $Label 編譯失敗，csc 結束碼：$LASTEXITCODE"
    }
}

try {
    New-Item -ItemType Directory -Path $tempRoot | Out-Null
    $packageOutput = Join-Path $tempRoot 'package'
    & (Join-Path $PSScriptRoot 'package-public.ps1') -OutputDirectory $packageOutput

    $releaseRoot = Join-Path $packageOutput "tai-v-pulse-$version-public"
    if (-not (Test-Path -LiteralPath $releaseRoot -PathType Container)) {
        throw '[TVP-B005] 找不到已完成安全檢查的對外內容。'
    }

    $frameworkRoot = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
    $compiler = Join-Path $frameworkRoot 'csc.exe'
    if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
        $frameworkRoot = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319'
        $compiler = Join-Path $frameworkRoot 'csc.exe'
    }
    if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
        throw '[TVP-B006] 找不到 Windows 內建 C# 編譯器，無法建立 EXE。'
    }

    $iconPath = Join-Path $tempRoot 'TaiVPulse.ico'
    New-TaiVPulseIcon -Path $iconPath
    $versionParts = @($version.Split('.') | ForEach-Object { [int]$_ })
    while ($versionParts.Count -lt 4) { $versionParts += 0 }
    $assemblyVersion = ($versionParts[0..3] -join '.')
    $assemblyInfoPath = Join-Path $tempRoot 'AssemblyInfo.cs'
    @"
using System.Reflection;
[assembly: AssemblyTitle("台V Pulse")]
[assembly: AssemblyProduct("台V Pulse")]
[assembly: AssemblyCompany("台V Pulse")]
[assembly: AssemblyCopyright("Copyright (c) 台V Pulse contributors")]
[assembly: AssemblyVersion("$assemblyVersion")]
[assembly: AssemblyFileVersion("$assemblyVersion")]
[assembly: AssemblyInformationalVersion("$version")]
"@ | Set-Content -LiteralPath $assemblyInfoPath -Encoding UTF8
    $launcherPath = Join-Path $releaseRoot 'TaiVPulse.exe'
    $commonCompilerArguments = @(
        '/nologo', '/target:winexe', '/optimize+', '/platform:anycpu', '/codepage:65001',
        "/win32icon:$iconPath",
        "/reference:$(Join-Path $frameworkRoot 'System.dll')",
        "/reference:$(Join-Path $frameworkRoot 'System.Drawing.dll')",
        "/reference:$(Join-Path $frameworkRoot 'System.Windows.Forms.dll')"
    )
    Invoke-CSharpCompiler -Compiler $compiler -Label '啟動器' -Arguments ($commonCompilerArguments + @(
        "/out:$launcherPath",
        $assemblyInfoPath,
        (Join-Path $projectRoot 'windows\TaiVPulseLauncher.cs')
    ))

    if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
        throw '[TVP-B007] 啟動器編譯完成後找不到 TaiVPulse.exe。'
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $payloadPath = Join-Path $tempRoot 'payload.zip'
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $releaseRoot,
        $payloadPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )
    $versionPath = Join-Path $tempRoot 'version.txt'
    Set-Content -LiteralPath $versionPath -Value $version -Encoding UTF8

    $tempInstaller = Join-Path $tempRoot $installerName
    Invoke-CSharpCompiler -Compiler $compiler -Label '安裝程式' -Arguments ($commonCompilerArguments + @(
        "/reference:$(Join-Path $frameworkRoot 'System.IO.Compression.dll')",
        "/reference:$(Join-Path $frameworkRoot 'System.IO.Compression.FileSystem.dll')",
        "/out:$tempInstaller",
        "/resource:$payloadPath,TaiVPulse.Payload.zip",
        "/resource:$versionPath,TaiVPulse.Version.txt",
        $assemblyInfoPath,
        (Join-Path $projectRoot 'windows\TaiVPulseInstaller.cs')
    ))

    $validationRoot = Join-Path $tempRoot 'validation-install'
    $validationArguments = @(
        '--silent', '--install-dir', "`"$validationRoot`"",
        '--no-shortcuts', '--no-register', '--no-launch'
    ) -join ' '
    $validation = Start-Process -FilePath $tempInstaller -ArgumentList $validationArguments -Wait -PassThru -WindowStyle Hidden
    if ($validation.ExitCode -ne 0) {
        throw "[TVP-B008] 安裝程式解壓縮驗證失敗，結束碼：$($validation.ExitCode)"
    }
    foreach ($required in @('.tai-v-pulse-manifest.txt', 'TaiVPulse.exe', 'start-local.ps1', 'requirements.txt', 'export-diagnostics.ps1', 'LICENSE', 'NOTICE')) {
        if (-not (Test-Path -LiteralPath (Join-Path $validationRoot $required) -PathType Leaf)) {
            throw "[TVP-B008] 安裝驗證缺少檔案：$required"
        }
    }
    foreach ($forbidden in @('.env', 'work\tai_v_pulse.sqlite3')) {
        if (Test-Path -LiteralPath (Join-Path $validationRoot $forbidden)) {
            throw "[TVP-B009] EXE 封裝含有禁止資料：$forbidden"
        }
    }

    $preservedEnvPath = Join-Path $validationRoot '.env'
    $preservedDatabasePath = Join-Path $validationRoot 'work\tai_v_pulse.sqlite3'
    $preservedRuntimePath = Join-Path $validationRoot 'node_modules\keep-runtime.txt'
    $staleProgramPath = Join-Path $validationRoot 'app\stale-upgrade-file.txt'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $preservedDatabasePath), (Split-Path -Parent $preservedRuntimePath) | Out-Null
    [System.IO.File]::WriteAllText($preservedEnvPath, "YOUTUBE_API_KEY=upgrade-test-placeholder`r`n", [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($preservedDatabasePath, 'upgrade-database-placeholder', [System.Text.Encoding]::ASCII)
    [System.IO.File]::WriteAllText($preservedRuntimePath, 'keep-runtime', [System.Text.Encoding]::ASCII)
    [System.IO.File]::WriteAllText($staleProgramPath, 'remove-stale-program-file', [System.Text.Encoding]::ASCII)

    $upgradeValidation = Start-Process -FilePath $tempInstaller -ArgumentList $validationArguments -Wait -PassThru -WindowStyle Hidden
    if ($upgradeValidation.ExitCode -ne 0) {
        throw "[TVP-B012] 安裝程式覆蓋升級驗證失敗，結束碼：$($upgradeValidation.ExitCode)"
    }
    if ((Get-Content -LiteralPath $preservedEnvPath -Raw -Encoding UTF8).Trim() -ne 'YOUTUBE_API_KEY=upgrade-test-placeholder') {
        throw '[TVP-B012] 覆蓋升級未保留 .env。'
    }
    if ((Get-Content -LiteralPath $preservedDatabasePath -Raw -Encoding ASCII) -ne 'upgrade-database-placeholder') {
        throw '[TVP-B012] 覆蓋升級未保留 SQLite 資料。'
    }
    if (-not (Test-Path -LiteralPath $preservedRuntimePath -PathType Leaf)) {
        throw '[TVP-B012] 覆蓋升級不應刪除既有 node_modules。'
    }
    if (Test-Path -LiteralPath $staleProgramPath) {
        throw '[TVP-B012] 覆蓋升級未清除舊版程式檔。'
    }
    $parseFailed = $false
    Get-ChildItem -LiteralPath $validationRoot -Filter '*.ps1' -File -Recurse | ForEach-Object {
        $tokens = $null
        $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$parseErrors) | Out-Null
        if ($parseErrors.Count -gt 0) {
            $parseFailed = $true
            Write-Warning "PowerShell 5.1 解析失敗：$($_.FullName)"
        }
    }
    if ($parseFailed) {
        throw '[TVP-B010] 安裝內容含有 Windows PowerShell 5.1 無法解析的腳本。'
    }
    $installedLauncherVersion = [System.Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $validationRoot 'TaiVPulse.exe')).FileVersion
    if ($installedLauncherVersion -ne $assemblyVersion) {
        throw "[TVP-B011] 啟動器檔案版本錯誤：$installedLauncherVersion"
    }

    Copy-Item -LiteralPath $tempInstaller -Destination $installerPath
    $hash = Get-FileHash -LiteralPath $installerPath -Algorithm SHA256
    "$($hash.Hash.ToLowerInvariant())  $installerName" | Set-Content -LiteralPath $checksumPath -Encoding ASCII
    Write-Host "已建立 Windows 安裝程式：$installerPath" -ForegroundColor Green
    Write-Host "SHA-256：$($hash.Hash.ToLowerInvariant())"
    Write-Host "檔案大小：$([Math]::Round((Get-Item -LiteralPath $installerPath).Length / 1MB, 2)) MB"
} catch {
    if (Test-Path -LiteralPath $installerPath) { Remove-Item -LiteralPath $installerPath -Force }
    if (Test-Path -LiteralPath $checksumPath) { Remove-Item -LiteralPath $checksumPath -Force }
    throw
} finally {
    if (-not $KeepTemporary -and
        (Test-Path -LiteralPath $tempRootFull -PathType Container) -and
        $tempRootFull.StartsWith("$tempParent\tai-v-pulse-build-", [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $tempRootFull -Recurse -Force -ErrorAction SilentlyContinue
    } elseif ($KeepTemporary -and (Test-Path -LiteralPath $tempRootFull -PathType Container)) {
        Write-Host "保留暫存建置目錄供診斷：$tempRootFull" -ForegroundColor Yellow
    }
}
