<#
.SYNOPSIS
    Bumps the project version in one place (pyproject.toml) and shows what changed.

.DESCRIPTION
    pyproject.toml is the single source of truth: the CLI (--version), the web UI
    (/api/config) and the installer name all read it from there.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\bump-version.ps1 0.2.0
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\bump-version.ps1 -Part minor

.NOTES
    ASCII only: Windows PowerShell 5.1 reads .ps1 files without a BOM as ANSI.
#>
[CmdletBinding(DefaultParameterSetName = 'explicit')]
param(
    [Parameter(ParameterSetName = 'explicit', Position = 0)]
    [string] $Version,

    [Parameter(ParameterSetName = 'part', Mandatory = $true)]
    [ValidateSet('major', 'minor', 'patch')]
    [string] $Part

)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$PyProject = Join-Path $Root 'pyproject.toml'

$text = Get-Content -Path $PyProject -Raw
if ($text -notmatch '(?m)^version\s*=\s*"([^"]+)"') { throw "cannot find version = \"...\" in $PyProject" }
$current = $Matches[1]
Write-Host "current version: $current" -ForegroundColor Cyan

if ($PSCmdlet.ParameterSetName -eq 'part') {
    $parts = $current.Split('.')
    while ($parts.Count -lt 3) { $parts += '0' }
    $major = [int] $parts[0]
    $minor = [int] $parts[1]
    $patch = [int] $parts[2]
    switch ($Part) {
        'major' { $major++; $minor = 0; $patch = 0 }
        'minor' { $minor++; $patch = 0 }
        'patch' { $patch++ }
    }
    $Version = "$major.$minor.$patch"
}

if (-not $Version) { throw 'pass a version such as 0.2.0, or -Part major|minor|patch' }

try { [void][version] $Version } catch { throw "'$Version' is not a valid version number" }
if ($Version -eq $current) { Write-Host 'nothing to do' -ForegroundColor Yellow; exit 0 }

$updated = $text -replace '(?m)^version\s*=\s*"[^"]+"', "version = `"$Version`""
Set-Content -Path $PyProject -Value $updated -NoNewline -Encoding Utf8

Write-Host "new version    : $Version" -ForegroundColor Green
Write-Host ''
Write-Host 'next steps:' -ForegroundColor Cyan
Write-Host "  .venv\Scripts\python.exe -m pytest"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1 -Tag v$Version"
