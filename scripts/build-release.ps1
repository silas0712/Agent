<#
.SYNOPSIS
    One command release: tests -> wheel/sdist -> Windows installer -> checksums.

.DESCRIPTION
    Steps
      1. resolve the version (pyproject.toml, or -Version)
      2. run the test suite
      3. build the Python artefacts with the "build" package (dist\*.whl, dist\*.tar.gz)
      4. build the Windows installer through scripts\build-installer.ps1
      5. write dist\SHA256SUMS.txt
      6. with -Push: commit, tag and push (the tag starts the release workflow)

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1 -Version 0.2.0 -SkipInstaller
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1 -Push

.NOTES
    ASCII only: Windows PowerShell 5.1 reads .ps1 files without a BOM as ANSI.
#>
[CmdletBinding()]
param(
    [string] $Version,
    [string] $Tag,
    [switch] $SkipTests,
    [switch] $SkipInstaller,
    [switch] $SkipDist,
    [switch] $Push,
    [switch] $Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$DistDir = Join-Path $Root 'dist'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }

function Step([string] $Text) { Write-Host ''; Write-Host "=== $Text" -ForegroundColor Cyan }
function Info([string] $Text) { Write-Host "    $Text" -ForegroundColor DarkGray }
function Fail([string] $Text) { Write-Host "[ERROR] $Text" -ForegroundColor Red; exit 1 }

Step 'release'
$pyproject = Get-Content (Join-Path $Root 'pyproject.toml') -Raw
if (-not $Version) {
    if ($pyproject -notmatch '(?m)^version\s*=\s*"([^"]+)"') { Fail 'cannot find the version in pyproject.toml' }
    $Version = $Matches[1]
}
if (-not $Tag) { $Tag = "v$Version" }
Info "version : $Version"
Info "tag     : $Tag"
Info "python  : $Python"

$dirty = (& git -C $Root status --porcelain)
if ($dirty -and $Push -and -not $Force) {
    Write-Host '    [!] the working tree is dirty:' -ForegroundColor Yellow
    $dirty | ForEach-Object { Write-Host "        $_" -ForegroundColor Yellow }
    Fail 'commit or stash your changes first (or pass -Force)'
}

if (-not $SkipTests) {
    Step 'tests'
    & $Python -m pytest -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { Fail 'the test suite is red; refusing to release' }
}

if (-not $SkipDist) {
    Step 'wheel + sdist'
    & $Python -c 'import build' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Info 'installing the "build" package'
        & $Python -m pip install --quiet --disable-pip-version-check build wheel
        if ($LASTEXITCODE -ne 0) { Fail 'cannot install the build package' }
    }
    if (Test-Path $DistDir) { Remove-Item $DistDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
    & $Python -m build --outdir $DistDir
    if ($LASTEXITCODE -ne 0) { Fail 'python -m build failed' }
}

if (-not $SkipInstaller) {
    Step 'windows installer'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build-installer.ps1') -Version $Version
    if ($LASTEXITCODE -ne 0) { Fail 'the installer build failed' }
}

Step 'checksums'
$artefacts = Get-ChildItem -Path $DistDir -File | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | Sort-Object Name
if (-not $artefacts) { Fail "no artefacts in $DistDir" }
$lines = foreach ($artefact in $artefacts) {
    $hash = (Get-FileHash -Path $artefact.FullName -Algorithm SHA256).Hash.ToLower()
    "$hash  $($artefact.Name)"
}
Set-Content -Path (Join-Path $DistDir 'SHA256SUMS.txt') -Value $lines -Encoding Ascii
$lines | ForEach-Object { Info $_ }

if ($Push) {
    Step 'git'
    & git -C $Root add -A
    & git -C $Root commit -m "release: $Tag" | Out-Null
    & git -C $Root tag -a $Tag -m "agentteam $Version"
    if ($LASTEXITCODE -ne 0) { Fail "cannot create the tag $Tag (does it already exist?)" }
    & git -C $Root push origin HEAD
    if ($LASTEXITCODE -ne 0) { Fail 'git push failed' }
    & git -C $Root push origin $Tag
    if ($LASTEXITCODE -ne 0) { Fail 'pushing the tag failed' }
    Info "pushed $Tag - the release workflow will attach the artefacts"
} else {
    Step 'next steps'
    Write-Host "  git add -A; git commit -m \"release: $Tag\"; git tag -a $Tag -m \"agentteam $Version\"; git push origin HEAD --follow-tags" -ForegroundColor DarkGray
    Write-Host "  gh release create $Tag dist\* --generate-notes" -ForegroundColor DarkGray
}

Step 'done'
Get-ChildItem $DistDir -File | ForEach-Object {
    Write-Host ("  {0,-34} {1,8:N1} MB" -f $_.Name, ($_.Length / 1MB)) -ForegroundColor Green
}
