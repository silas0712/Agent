<#
.SYNOPSIS
    Builds the Windows installer for agentteam (self-contained, no Python needed).

.DESCRIPTION
    Two stages:
      1. payload  - downloads the embeddable CPython matching your interpreter,
                    installs agentteam + the [web] extras into it with pip and
                    writes the .cmd launchers  -> build\installer\payload
      2. setup    - compiles packaging\agentteam.iss with Inno Setup (installed
                    automatically through winget / choco / the vendor download)
                    -> dist\agentteam-<version>-setup.exe

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1 -PayloadOnly

.NOTES
    Keep this file ASCII-only: Windows PowerShell 5.1 reads .ps1 files without a
    BOM as ANSI, so non-ASCII text here would be garbled (the same reason
    scripts\start-agentteam.bat is ASCII-only).
#>
[CmdletBinding()]
param(
    [string] $Version,         # defaults to the version in pyproject.toml
    [string] $PythonVersion,   # e.g. 3.12.7, defaults to your local interpreter
    [string] $PayloadDir,      # defaults to build\installer\payload
    [string] $OutDir,          # defaults to .\dist
    [switch] $PayloadOnly,
    [switch] $SkipSmokeTest,
    [switch] $Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # keeps Invoke-WebRequest quiet & fast

$Root     = Split-Path -Parent $PSScriptRoot
$CacheDir = Join-Path $Root 'build\installer\cache'
$IssPath  = Join-Path $Root 'packaging\agentteam.iss'
if (-not $PayloadDir) { $PayloadDir = Join-Path $Root 'build\installer\payload' }
if (-not $OutDir) { $OutDir = Join-Path $Root 'dist' }

function Step([string] $Text) { Write-Host ''; Write-Host "=== $Text" -ForegroundColor Cyan }
function Info([string] $Text) { Write-Host "    $Text" -ForegroundColor DarkGray }
function Warn([string] $Text) { Write-Host "    [!] $Text" -ForegroundColor Yellow }
function Fail([string] $Text) { Write-Host "[ERROR] $Text" -ForegroundColor Red; exit 1 }

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
function Resolve-Version([string] $Explicit) {
    if ($Explicit) { return $Explicit }
    $pyproject = Get-Content (Join-Path $Root 'pyproject.toml') -Raw
    if ($pyproject -match '(?m)^version\s*=\s*"([^"]+)"') { return $Matches[1] }
    Fail 'cannot find the project version in pyproject.toml'
}

function Resolve-Python([string] $Explicit) {
    if ($Explicit) {
        if (-not (Test-Path $Explicit)) { Fail "python.exe not found: $Explicit" }
        return @{ Exe = $Explicit; Prefix = @() }
    }
    $venv = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path $venv) { return @{ Exe = $venv; Prefix = @() } }
    if (Get-Command py -ErrorAction SilentlyContinue) { return @{ Exe = 'py'; Prefix = @('-3') } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return @{ Exe = $cmd.Source; Prefix = @() } }
    Fail 'Python 3.10+ not found; install Python or create .venv first'
}

function Invoke-Python([hashtable] $Python, [string[]] $Arguments) {
    $exe = $Python.Exe
    $all = @($Python.Prefix) + $Arguments
    $output = & $exe @all 2>&1
    if ($LASTEXITCODE -ne 0) { Fail "python invocation failed: $exe $($all -join ' ')`n$($output -join "`n")" }
    return $output
}

function Get-EmbedZip([string] $PyVersion) {
    $zip = Join-Path $CacheDir "python-$PyVersion-embed-amd64.zip"
    if (Test-Path $zip) { Info "reusing the cached runtime: $zip"; return $zip }
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
    $url = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip"
    Info "downloading $url"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip -TimeoutSec 600
    } catch {
        Fail "cannot download the embeddable runtime ($url). Check the network, or drop the zip into $CacheDir manually."
    }
    return $zip
}

function Get-NewestPatch([string] $Branch, [string] $Ceiling) {
    # python.org does not always keep the exact micro version: pick the newest
    # patch release of the same 3.x branch that is not newer than our ceiling.
    try {
        $page = Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/' -TimeoutSec 120
    } catch {
        return $null
    }
    $pattern = [regex]::Escape($Branch) + '\.(\d+)'
    $found = [regex]::Matches($page.Content, $pattern) |
        ForEach-Object { $_.Value } |
        Sort-Object { [version] $_ } -Unique
    if ($Ceiling) { $found = $found | Where-Object { [version] $_ -le [version] $Ceiling } }
    if (-not $found) { return $null }
    return ($found | Select-Object -Last 1)
}

function Test-Url([string] $Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Head -Uri $Url -TimeoutSec 60
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-Iscc {
    $candidates = @()
    $onPath = Get-Command iscc -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    foreach ($version in @('7', '6')) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup $version\ISCC.exe")
        $candidates += (Join-Path $env:ProgramFiles "Inno Setup $version\ISCC.exe")
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup $version\ISCC.exe")
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Get-ChineseMessagesFile([string] $Iscc) {
    # the compiler ships no Chinese translation, so fall back to the vendored copy
    $compilerLanguages = Join-Path (Split-Path -Parent $Iscc) 'Languages'
    if (Test-Path $compilerLanguages) {
        $found = Get-ChildItem -Path $compilerLanguages -Recurse -Filter 'ChineseSimplified.isl' -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
        if ($found) { return $found }
    }
    $vendored = Join-Path $Root 'packaging\translations\ChineseSimplified.isl'
    if (Test-Path $vendored) { return $vendored }

    $target = Join-Path $CacheDir 'ChineseSimplified.isl'
    if (-not (Test-Path $target)) {
        try {
            New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
            Info 'downloading the Simplified Chinese setup translation'
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 -OutFile $target `
                -Uri 'https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/ChineseSimplified.isl'
        } catch {
            Warn "cannot fetch the Chinese translation: $($_.Exception.Message)"
            return $null
        }
    }
    if ((Test-Path $target) -and (Get-Item $target).Length -gt 4KB) { return $target }
    return $null
}

function Test-PeFile([string] $Path) {
    if (-not (Test-Path $Path)) { return $false }
    if ((Get-Item $Path).Length -lt 1MB) { return $false }
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $head = New-Object byte[] 2
        $stream.Read($head, 0, 2) | Out-Null
    } finally {
        $stream.Dispose()
    }
    return ($head[0] -eq 0x4D -and $head[1] -eq 0x5A)   # "MZ"
}

function Get-InnoSetupUrl {
    # 1) the vendor mirror listing (stable, versioned)
    try {
        $listing = Invoke-WebRequest -UseBasicParsing -Uri 'https://files.jrsoftware.org/is/6/' -TimeoutSec 120
        $match = [regex]::Matches($listing.Content, 'innosetup-\d+(\.\d+)+\.exe') |
            ForEach-Object { $_.Value } | Sort-Object -Unique | Select-Object -Last 1
        if ($match) { return "https://files.jrsoftware.org/is/6/$match" }
    } catch {
        Warn "the Inno Setup mirror listing is unreachable: $($_.Exception.Message)"
    }
    # 2) the GitHub release assets
    try {
        $release = Invoke-WebRequest -UseBasicParsing -Uri 'https://api.github.com/repos/jrsoftware/issrc/releases/latest' -TimeoutSec 120
        $json = $release.Content | ConvertFrom-Json
        $asset = $json.assets | Where-Object { $_.name -like '*.exe' } | Select-Object -First 1
        if ($asset) { return $asset.browser_download_url }
    } catch {
        Warn "the Inno Setup GitHub release is unreachable: $($_.Exception.Message)"
    }
    return $null
}

function Install-InnoSetup {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Info 'installing Inno Setup through winget (up to 4 minutes) ...'
        try {
            $winget = Start-Process -FilePath 'winget' -PassThru -WindowStyle Hidden -ArgumentList @(
                'install', '--id', 'JRSoftware.InnoSetup', '-e',
                '--accept-package-agreements', '--accept-source-agreements', '--silent'
            )
            if (-not $winget.WaitForExit(240000)) {
                $winget.Kill()
                Warn 'winget timed out (it may be waiting for an elevation prompt); using the vendor download'
            }
        } catch {
            Warn "winget failed: $($_.Exception.Message)"
        }
        $iscc = Get-Iscc
        if ($iscc) { return $iscc }
    }

    $url = Get-InnoSetupUrl
    if (-not $url) {
        Fail 'cannot locate the Inno Setup installer; install it manually from https://jrsoftware.org/isdl.php (or winget install JRSoftware.InnoSetup)'
    }
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
    $installer = Join-Path $CacheDir ([System.IO.Path]::GetFileName($url))
    Info "downloading $url"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer -TimeoutSec 900
    if (-not (Test-PeFile $installer)) {
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
        Fail "the downloaded Inno Setup installer is not a valid executable; install Inno Setup manually from https://jrsoftware.org/isdl.php"
    }

    Info 'installing Inno Setup silently (per-user, no administrator rights needed)'
    $proc = Start-Process -FilePath $installer -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER' -PassThru -Wait
    if ($proc.ExitCode -ne 0) { Fail "the Inno Setup installer failed (exit code $($proc.ExitCode))" }

    $iscc = Get-Iscc
    if (-not $iscc) {
        Fail 'Inno Setup was installed but ISCC.exe is still missing; install it manually from https://jrsoftware.org/isdl.php'
    }
    return $iscc
}

# --------------------------------------------------------------------------
# 0. what are we building?
# --------------------------------------------------------------------------
$Version = Resolve-Version $Version
$Python = Resolve-Python $null

$queryCmd = @($Python.Prefix) + @('-c', "import sys; print('{0}.{1}.{2}'.format(*sys.version_info[:3]))")
$rawLocal = & $Python.Exe @queryCmd 2>&1
if ($LASTEXITCODE -ne 0) { Fail "cannot query the local Python version:`n$($rawLocal -join "`n")" }
$localVersion = ($rawLocal | Select-Object -Last 1).ToString().Trim()
if ($PythonVersion) {
    $wantedBranch = ($PythonVersion -split '\.')[0..1] -join '.'
    $localBranch = ($localVersion -split '\.')[0..1] -join '.'
    if ($wantedBranch -ne $localBranch) {
        Fail "the runtime must match the interpreter that installs the wheels (local: $localVersion)"
    }
} else {
    $PythonVersion = $localVersion
}

Step "agentteam installer build $Version"
Info "project root  : $Root"
Info "payload dir   : $PayloadDir"
Info "output dir    : $OutDir"
Info "interpreter   : $($Python.Exe) ($localVersion)"
Info "runtime       : $PythonVersion"

# --------------------------------------------------------------------------
# 1. payload
# --------------------------------------------------------------------------
Step "[1/3] building the payload"
$embedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Url $embedUrl)) {
    $branch = ($PythonVersion -split '\.')[0..1] -join '.'
    $fallback = Get-NewestPatch $branch $PythonVersion
    if (-not $fallback) { Fail "no embeddable runtime is available for $PythonVersion" }
    Warn "python.org has no embeddable build for $PythonVersion; using $fallback"
    $PythonVersion = $fallback
}

$zip = Get-EmbedZip $PythonVersion
if (Test-Path $PayloadDir) { Remove-Item $PayloadDir -Recurse -Force }
$PyDir = Join-Path $PayloadDir 'python'
New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $PyDir)
Info "unpacked the embeddable runtime into $PyDir"

$pth = Get-ChildItem -Path $PyDir -Filter 'python*._pth' | Select-Object -First 1
if (-not $pth) { Fail 'the embeddable runtime has no ._pth file' }
$pthLines = @(Get-Content -Path $pth.FullName)
$pthLines = $pthLines | ForEach-Object { if ($_.Trim() -eq '#import site') { 'import site' } else { $_ } }
if (-not ($pthLines | Where-Object { $_.Trim() -eq 'Lib\site-packages' })) {
    $pthLines += 'Lib\site-packages'
}
Set-Content -Path $pth.FullName -Value $pthLines -Encoding Ascii
Info "patched $($pth.Name) (site enabled, Lib\site-packages on sys.path)"

$SitePackages = Join-Path $PyDir 'Lib\site-packages'
New-Item -ItemType Directory -Force -Path $SitePackages | Out-Null
Info 'installing agentteam[web] and its dependencies (pip --target)'
Push-Location $Root
try {
    Invoke-Python $Python @(
        '-m', 'pip', 'install', '--target', $SitePackages,
        '--upgrade', '--no-cache-dir', '--disable-pip-version-check',
        '--no-warn-script-location', '.[web]'
    ) | Out-Null
} finally {
    Pop-Location
}


# launchers ----------------------------------------------------------------
$cliLauncher = @(
    '@echo off'
    'rem agentteam CLI launcher - generated by scripts\build-installer.ps1'
    'setlocal EnableExtensions'
    'chcp 65001 >nul'
    'set "HERE=%~dp0"'
    'set "DATA=%LOCALAPPDATA%\agentteam"'
    'if not exist "%DATA%" mkdir "%DATA%" >nul 2>nul'
    'if not defined AGENT_ENV_FILE  set "AGENT_ENV_FILE=%DATA%\.env"'
    'if not defined AGENT_WORKSPACE  set "AGENT_WORKSPACE=%DATA%\workspace"'
    'if not defined AGENT_RUNS_DIR   set "AGENT_RUNS_DIR=%DATA%\runs"'
    'set "PYTHONHOME="'
    'set "PYTHONPATH="'
    '"%HERE%python\python.exe" -X utf8 -m agentteam.main %*'
    'set "CODE=%ERRORLEVEL%"'
    'if "%~1"=="" pause'
    'endlocal & exit /b %CODE%'
)
Set-Content -Path (Join-Path $PayloadDir 'agentteam-cli.cmd') -Value $cliLauncher -Encoding Ascii

$webLauncher = @(
    '@echo off'
    'rem agentteam Web UI launcher - generated by scripts\build-installer.ps1'
    'setlocal EnableExtensions'
    'chcp 65001 >nul'
    'title agentteam Web UI'
    'set "HERE=%~dp0"'
    'set "DATA=%LOCALAPPDATA%\agentteam"'
    'if not exist "%DATA%" mkdir "%DATA%" >nul 2>nul'
    'if not defined AGENT_ENV_FILE  set "AGENT_ENV_FILE=%DATA%\.env"'
    'if not defined AGENT_WORKSPACE  set "AGENT_WORKSPACE=%DATA%\workspace"'
    'if not defined AGENT_RUNS_DIR   set "AGENT_RUNS_DIR=%DATA%\runs"'
    'set "PYTHONHOME="'
    'set "PYTHONPATH="'
    '"%HERE%python\python.exe" -X utf8 -m agentteam.main --serve --open-browser %*'
    'set "CODE=%ERRORLEVEL%"'
    'if not "%CODE%"=="0" pause'
    'endlocal & exit /b %CODE%'
)
Set-Content -Path (Join-Path $PayloadDir 'agentteam-web.cmd') -Value $webLauncher -Encoding Ascii

Copy-Item -Path (Join-Path $Root '.env.example') -Destination (Join-Path $PayloadDir '.env.example') -Force
Copy-Item -Path (Join-Path $Root 'README.md') -Destination (Join-Path $PayloadDir 'README.md') -Force

$readme = @(
    "agentteam $Version - Windows bundle"
    ''
    'How to start'
    '  1. Start menu -> agentteam -> "agentteam Web UI"'
    '     (or double click agentteam-web.cmd next to this file)'
    '  2. Start menu -> agentteam -> "agentteam CLI" for a command line run'
    '     of the built-in mock goal.'
    ''
    'Where are my files?'
    "  workspace : %LOCALAPPDATA%\agentteam\workspace   (files written by the agents)"
    "  runs      : %LOCALAPPDATA%\agentteam\runs        (session logs + transcripts)"
    "  config    : %LOCALAPPDATA%\agentteam\.env        (edit this to use a real model)"
    ''
    'Using a real model (any OpenAI compatible endpoint)'
    '  Open %LOCALAPPDATA%\agentteam\.env and set, for example:'
    '    AGENT_LLM_PROVIDER=openai'
    '    OPENAI_API_KEY=sk-...'
    '    OPENAI_BASE_URL=https://api.openai.com/v1'
    '    AGENT_ACTOR_MODEL=gpt-4o-mini'
    '  Every role (planner / actor / reviewer / tester) can use its own model.'
    ''
    'Everything is self-contained: the bundled python\ directory is a private'
    'CPython runtime, so nothing is installed system-wide and uninstalling the'
    'program never touches your other Python installations.'
)
Set-Content -Path (Join-Path $PayloadDir 'README.txt') -Value $readme -Encoding Utf8

$payloadSize = [math]::Round(((Get-ChildItem $PayloadDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Info "payload written to $PayloadDir ($payloadSize MB)"


# --------------------------------------------------------------------------
# 2. smoke test
# --------------------------------------------------------------------------
if (-not $SkipSmokeTest) {
    Step '[2/3] smoke testing the payload'
    $embedPy = Join-Path $PyDir 'python.exe'
    if (-not (Test-Path $embedPy)) { Fail "the payload has no python.exe ($embedPy)" }

    $sandbox = Join-Path $Root 'build\installer\smoke'
    if (Test-Path $sandbox) { Remove-Item $sandbox -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $sandbox | Out-Null

    $savedEnv = @{}
    foreach ($key in @('AGENT_WORKSPACE', 'AGENT_RUNS_DIR', 'AGENT_BUS', 'AGENT_LLM_PROVIDER', 'AGENT_ENV_FILE')) {
        $savedEnv[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    }
    $env:AGENT_WORKSPACE = Join-Path $sandbox 'workspace'
    $env:AGENT_RUNS_DIR = Join-Path $sandbox 'runs'
    $env:AGENT_BUS = 'memory'
    $env:AGENT_LLM_PROVIDER = 'mock'
    $env:AGENT_ENV_FILE = Join-Path $sandbox 'absent.env'

    try {
        $imports = & $embedPy '-X' 'utf8' '-c' 'import agentteam, fastapi, uvicorn, websockets, rich; print(''-payload ok-'')'
        if ($LASTEXITCODE -ne 0) { Fail "the payload cannot import agentteam:`n$($imports -join "`n")" }
        Info ($imports | Select-Object -Last 1)

        $probe = & $embedPy '-X' 'utf8' '-m' 'agentteam.main' '--quiet' '--version'
        if ($LASTEXITCODE -ne 0) { Fail 'the payload cannot run agentteam.main' }
        Info (($probe | Select-Object -Last 1))

        $demo = & $embedPy '-X' 'utf8' '-m' 'agentteam.main' '--quiet'
        if ($LASTEXITCODE -ne 0) { Fail "the offline demo goal failed:`n$($demo -join "`n")" }
        if (-not (Test-Path (Join-Path $env:AGENT_WORKSPACE 'hello.py'))) {
            Fail 'the offline demo goal did not produce hello.py'
        }
        Info 'offline demo goal completed (hello.py written)'
    } finally {
        foreach ($key in $savedEnv.Keys) {
            if ($null -eq $savedEnv[$key]) { Remove-Item "env:$key" -ErrorAction SilentlyContinue }
            else { Set-Item "env:$key" $savedEnv[$key] }
        }
    }
    Remove-Item $sandbox -Recurse -Force -ErrorAction SilentlyContinue
} else {
    Step '[2/3] smoke test skipped'
}


# --------------------------------------------------------------------------
# 3. installer
# --------------------------------------------------------------------------
if ($PayloadOnly) {
    Step 'done (payload only)'
    Write-Host "payload: $PayloadDir" -ForegroundColor Green
    exit 0
}

Step '[3/3] compiling the installer'
if (-not (Test-Path $IssPath)) { Fail "missing $IssPath" }
$iscc = Get-Iscc
if (-not $iscc) {
    Warn 'Inno Setup was not found'
    $iscc = Install-InnoSetup
}
Info "using $iscc"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$isccArgs = @(
    "/DAppVersion=$Version"
    "/DPayloadDir=$PayloadDir"
    "/DOutDir=$OutDir"
)
$chinese = Get-ChineseMessagesFile -Iscc $iscc
if ($chinese) {
    $isccArgs += '/DWithChinese=1'
    $isccArgs += "/DChineseMessagesFile=$chinese"
    Info "the setup wizard speaks Simplified Chinese: $chinese"
} else {
    Warn 'no ChineseSimplified.isl found; the setup wizard stays English'
}
$isccArgs += $IssPath
& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) { Fail "ISCC exited with code $LASTEXITCODE" }

$setup = Join-Path $OutDir "agentteam-$Version-setup.exe"
if (-not (Test-Path $setup)) { Fail "the installer was not produced ($setup)" }
$setupSize = [math]::Round(((Get-Item $setup).Length / 1MB), 1)

Step 'done'
Write-Host "installer: $setup ($setupSize MB)" -ForegroundColor Green
Write-Host "payload  : $PayloadDir" -ForegroundColor Green

