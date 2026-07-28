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
$projectRoot = Split-Path -Parent $PSScriptRoot
$packageJsonPath = Join-Path $projectRoot 'package.json'
$packageJson = Get-Content -LiteralPath $packageJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
$version = [string]$packageJson.version

if ([string]::IsNullOrWhiteSpace($version)) {
    throw 'package.json 未設定版本。'
}

$outputRoot = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    Join-Path $projectRoot 'outputs'
} elseif ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
}

$releaseName = "tai-v-pulse-$version-public"
$stagingRoot = Join-Path $outputRoot $releaseName
$archivePath = Join-Path $outputRoot "$releaseName.zip"
$checksumPath = "$archivePath.sha256"

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

foreach ($target in @($stagingRoot, $archivePath, $checksumPath)) {
    if (Test-Path -LiteralPath $target) {
        throw "輸出已存在，為避免覆寫請先移走：$target"
    }
}

$topLevelFiles = @(
    '.env.example',
    '.gitignore',
    'cloudflare-env.d.ts',
    'export-diagnostics.ps1',
    'install-prerequisites.ps1',
    'LICENSE',
    'next.config.ts',
    'NOTICE',
    'package-lock.json',
    'package.json',
    'postcss.config.mjs',
    'PUBLIC_RELEASE.md',
    'README.md',
    'requirements.txt',
    'start-local.ps1',
    'stop-local.ps1',
    'tsconfig.json',
    'uninstall.ps1',
    'vite.config.ts',
    '停止台V Pulse.cmd',
    '啟動台V Pulse.cmd'
)

$sourceDirectories = @('app', 'build', 'collector', 'public', 'windows', 'worker')
$blockedDirectoryNames = @(
    '.git', '.next', '.vinext', '.wrangler', '_sites-preview',
    '__pycache__', 'dist', 'node_modules', 'outputs', 'work'
)
$blockedExtensions = @(
    '.csv', '.db', '.dll', '.exe', '.log', '.pdb', '.pyc', '.pyo',
    '.sqlite', '.sqlite3', '.tsv', '.zip'
)

function Copy-ReleaseFile {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )

    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        throw "缺少發佈必要檔案：$RelativePath"
    }

    $destination = Join-Path $stagingRoot $RelativePath
    $destinationDirectory = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
    Copy-Item -LiteralPath $SourcePath -Destination $destination
}

function Get-ChildRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$ParentPath,
        [Parameter(Mandatory = $true)][string]$ChildPath
    )

    $parentFullPath = [System.IO.Path]::GetFullPath($ParentPath).TrimEnd('\', '/')
    $childFullPath = [System.IO.Path]::GetFullPath($ChildPath)
    $prefix = "$parentFullPath\"
    if (-not $childFullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "路徑不在預期目錄內：$childFullPath"
    }

    return $childFullPath.Substring($prefix.Length)
}

try {
    New-Item -ItemType Directory -Path $stagingRoot | Out-Null

    foreach ($relativePath in $topLevelFiles) {
        Copy-ReleaseFile -SourcePath (Join-Path $projectRoot $relativePath) -RelativePath $relativePath
    }

    $requirementsPath = Join-Path $stagingRoot 'requirements.txt'
    $requirements = (Get-Content -LiteralPath $requirementsPath -Raw -Encoding UTF8).Trim()
    if ($requirements -ne 'tzdata==2026.3') {
        throw 'requirements.txt 必須只固定使用已驗證的 tzdata==2026.3。'
    }

    Copy-ReleaseFile `
        -SourcePath (Join-Path $projectRoot '.openai\hosting.json') `
        -RelativePath '.openai\hosting.json'

    foreach ($directoryName in $sourceDirectories) {
        $sourceDirectory = Join-Path $projectRoot $directoryName
        if (-not (Test-Path -LiteralPath $sourceDirectory -PathType Container)) {
            throw "缺少發佈必要目錄：$directoryName"
        }

        Get-ChildItem -LiteralPath $sourceDirectory -File -Recurse | ForEach-Object {
            $relativePath = Get-ChildRelativePath -ParentPath $projectRoot -ChildPath $_.FullName
            $segments = $relativePath -split '[\\/]'
            $containsBlockedDirectory = @($segments | Where-Object { $_ -in $blockedDirectoryNames }).Count -gt 0
            $isBlockedExtension = $_.Extension.ToLowerInvariant() -in $blockedExtensions
            $isEnvironmentFile = $_.Name -like '.env*'

            if (-not $containsBlockedDirectory -and -not $isBlockedExtension -and -not $isEnvironmentFile) {
                Copy-ReleaseFile -SourcePath $_.FullName -RelativePath $relativePath
            }
        }
    }

    $manifestPath = Join-Path $stagingRoot '.tai-v-pulse-manifest.txt'
    [string[]]$manifestEntries = @(Get-ChildItem -LiteralPath $stagingRoot -File -Recurse | ForEach-Object {
        (Get-ChildRelativePath -ParentPath $stagingRoot -ChildPath $_.FullName).Replace('\', '/')
    } | Sort-Object)
    [System.IO.File]::WriteAllLines($manifestPath, $manifestEntries, $utf8WithoutBom)

    # Windows PowerShell 5.1 reads UTF-8 without a BOM using the active ANSI
    # code page. Keep every distributed PowerShell script normalized to UTF-8
    # with BOM even if an editor rewrites a repository file without its BOM.
    $utf8WithoutBomStrict = [System.Text.UTF8Encoding]::new($false, $true)
    $utf8WithBom = [System.Text.UTF8Encoding]::new($true)
    $packagedPowerShellFiles = @(Get-ChildItem -LiteralPath $stagingRoot -Filter '*.ps1' -File -Recurse)
    foreach ($powerShellFile in $packagedPowerShellFiles) {
        $content = [System.IO.File]::ReadAllText($powerShellFile.FullName, $utf8WithoutBomStrict)
        [System.IO.File]::WriteAllText($powerShellFile.FullName, $content, $utf8WithBom)
        $bytes = [System.IO.File]::ReadAllBytes($powerShellFile.FullName)
        if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
            throw "PowerShell 腳本缺少 UTF-8 BOM：$($powerShellFile.FullName)"
        }
    }

    $envExamplePath = Join-Path $stagingRoot '.env.example'
    $envExample = Get-Content -LiteralPath $envExamplePath -Raw -Encoding UTF8
    if ($envExample -notmatch '(?m)^TAI_V_PULSE_EDITION=public[ \t]*\r?$' -or
        $envExample -notmatch '(?m)^NEXT_PUBLIC_TAI_V_PULSE_EDITION=public[ \t]*\r?$') {
        throw '.env.example 未固定使用 public 版設定。'
    }
    $apiKeyLine = [System.Text.RegularExpressions.Regex]::Match(
        $envExample,
        '(?m)^YOUTUBE_API_KEY=([^\r\n]*)\r?$'
    )
    if (-not $apiKeyLine.Success -or
        -not [string]::IsNullOrWhiteSpace($apiKeyLine.Groups[1].Value)) {
        throw '.env.example 含有非空白的 YouTube API Key。'
    }

    $releaseFiles = Get-ChildItem -LiteralPath $stagingRoot -File -Recurse
    $forbiddenFiles = @($releaseFiles | Where-Object {
        $_.Name -eq '.env' -or
        $_.Extension.ToLowerInvariant() -in $blockedExtensions -or
        ((Get-ChildRelativePath -ParentPath $stagingRoot -ChildPath $_.FullName) -split '[\\/]' | Where-Object { $_ -in $blockedDirectoryNames })
    })
    if ($forbiddenFiles.Count -gt 0) {
        throw "發佈目錄含有禁止檔案：$($forbiddenFiles.FullName -join ', ')"
    }

    $textExtensions = @('.cmd', '.cs', '.css', '.d.ts', '.example', '.json', '.md', '.mjs', '.ps1', '.py', '.ts', '.tsx')
    $textFiles = @($releaseFiles | Where-Object {
        $_.Extension.ToLowerInvariant() -in $textExtensions -or $_.Name -in @('LICENSE', 'NOTICE')
    })
    $absolutePathLeak = $textFiles | Select-String -Pattern 'C:\\Users\\' -List
    if ($absolutePathLeak) {
        throw "發佈內容含有使用者絕對路徑：$($absolutePathLeak.Path -join ', ')"
    }

    Compress-Archive -LiteralPath $stagingRoot -DestinationPath $archivePath -CompressionLevel Optimal
    $hash = Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
    "$($hash.Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($archivePath))" |
        Set-Content -LiteralPath $checksumPath -Encoding ascii

    Write-Host "已建立對外測試包：$archivePath" -ForegroundColor Green
    Write-Host "SHA-256：$($hash.Hash.ToLowerInvariant())"
    Write-Host "檔案數：$($releaseFiles.Count)"
} catch {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    if (Test-Path -LiteralPath $checksumPath) {
        Remove-Item -LiteralPath $checksumPath -Force
    }
    throw
}
