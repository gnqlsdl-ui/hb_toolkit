# Build a Blender Extension zip into release/.
# Reads version from blender_manifest.toml and writes
# release/hb_toolkit_v{MAJOR}.{MINOR}.{PATCH}.zip
#
# The project root IS the extension package: zip is staged so that
# the archive contains a top-level "hb_toolkit/" folder containing
# blender_manifest.toml and all Python modules.
#
# Usage:  pwsh .\build.ps1   (or)   powershell -File .\build.ps1

param()

$ErrorActionPreference = 'Stop'

$Root         = $PSScriptRoot
$PackageName  = 'hb_toolkit'
$ReleaseDir   = Join-Path $Root 'release'
$ManifestFile = Join-Path $Root 'blender_manifest.toml'

if (-not (Test-Path $ManifestFile)) { throw "Manifest not found: $ManifestFile" }

$manifestText = Get-Content $ManifestFile -Raw
$match = [regex]::Match($manifestText, '(?m)^\s*version\s*=\s*"(\d+)\.(\d+)\.(\d+)"')
if (-not $match.Success) { throw "Could not parse 'version' from $ManifestFile" }

$version = "$($match.Groups[1].Value).$($match.Groups[2].Value).$($match.Groups[3].Value)"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

$zipPath = Join-Path $ReleaseDir "${PackageName}_v${version}.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

# Folders & files at project root that are NOT part of the extension package.
$excludeDirs = @('__pycache__', '.git', '.cursor', 'release', 'docs', '.vscode', '.idea', 'reference', 'scripts', '.github')
$excludeFiles = @('build.ps1', 'build.py', 'README.md', '.gitignore')
$excludeSuffixes = @('.pyc', '.pyo', '.zip')

# Stage into a temp folder under "<package>/" so the archive root is correct.
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("hb_build_" + [guid]::NewGuid())
$tmpPkg = Join-Path $tmp $PackageName
New-Item -ItemType Directory -Force -Path $tmpPkg | Out-Null

$rootPath = (Resolve-Path $Root).Path
Get-ChildItem -Path $rootPath -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($rootPath.Length + 1)
    $parts = $rel -split '[\\/]'

    $skip = $false
    foreach ($p in $parts) { if ($excludeDirs -contains $p) { $skip = $true; break } }
    if ($skip) { return }
    if ($excludeFiles -contains $_.Name) { return }
    if ($excludeSuffixes -contains $_.Extension) { return }

    $dest = Join-Path $tmpPkg $rel
    $destParent = Split-Path $dest -Parent
    if ($destParent -and -not (Test-Path $destParent)) {
        New-Item -ItemType Directory -Force -Path $destParent | Out-Null
    }
    Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
}

# Sanity check: manifest MUST be inside the archive.
if (-not (Test-Path (Join-Path $tmpPkg 'blender_manifest.toml'))) {
    Remove-Item -Recurse -Force $tmp
    throw "blender_manifest.toml missing from staged package"
}

Compress-Archive -Path (Join-Path $tmp '*') -DestinationPath $zipPath -Force
Remove-Item -Recurse -Force $tmp

$size = (Get-Item $zipPath).Length / 1KB
Write-Host ("[build] OK -> {0} ({1:N1} KB)  [Extension v{2}]" -f $zipPath.Substring($Root.Length + 1), $size, $version)
