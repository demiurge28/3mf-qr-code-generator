<#
.SYNOPSIS
    qr23mf installer for Windows.

.DESCRIPTION
    Mirrors install.sh. Locates a Python 3.11+ interpreter, picks uv
    (preferred) or pipx (fallback; offers to install uv via the official
    Astral installer if neither is present), installs qr23mf as a
    user-level tool from the current source tree, and verifies the
    binary is reachable. Tkinter ships with the python.org Windows
    installer by default, so no separate Tk step is needed unless you
    installed Python without the "tcl/tk and IDLE" option.

.PARAMETER NonInteractive
    Auto-confirm every prompt.

.PARAMETER SkipTk
    Do not warn when tkinter is missing. Kept for parity with install.sh.

.PARAMETER Tool
    Force a specific install tool ("uv" or "pipx"). Defaults to uv when
    present, otherwise pipx.

.EXAMPLE
    pwsh -ExecutionPolicy Bypass -File install.ps1
    pwsh -ExecutionPolicy Bypass -File install.ps1 -NonInteractive
    pwsh -ExecutionPolicy Bypass -File install.ps1 -Tool pipx

.NOTES
    If you get "running scripts is disabled on this system", invoke as
    above with '-ExecutionPolicy Bypass' or run
    'Set-ExecutionPolicy -Scope Process Bypass' in the current session
    first.
#>

[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$SkipTk,
    [ValidateSet('', 'uv', 'pipx')]
    [string]$Tool = ''
)

$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
# Pretty printing
# -----------------------------------------------------------------------------

function Write-Cyan   ([string]$m) { Write-Host $m -ForegroundColor Cyan }
function Write-Green  ([string]$m) { Write-Host $m -ForegroundColor Green }
function Write-Yellow ([string]$m) { Write-Host $m -ForegroundColor Yellow }
function Write-Red    ([string]$m) { Write-Host $m -ForegroundColor Red }

function Stop-Install ([string]$m) {
    Write-Red "Error: $m"
    throw $m
}

function Confirm ([string]$prompt = 'Continue?') {
    if ($NonInteractive) { return $true }
    $reply = Read-Host "$prompt [Y/n]"
    if ([string]::IsNullOrWhiteSpace($reply)) { return $true }
    return $reply.Trim().ToLower() -in @('y', 'yes')
}

# -----------------------------------------------------------------------------
# Preflight: source layout
# -----------------------------------------------------------------------------

$scriptDir = $PSScriptRoot
if (-not $scriptDir) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location -Path $scriptDir

if (-not (Test-Path (Join-Path $scriptDir 'pyproject.toml'))) {
    Stop-Install "pyproject.toml not found in $scriptDir. Run this script from the qr23mf source tree."
}

Write-Cyan "==> qr23mf installer"
Write-Cyan "    source: $scriptDir"
Write-Cyan "    host:   windows"

# -----------------------------------------------------------------------------
# Python >= 3.11
#
# Probe the 'py' launcher (py -3.13 / 3.12 / 3.11) first because it is
# the blessed way to address a specific Python version on Windows, then
# fall back to bare 'python' and 'python3' on PATH.
# -----------------------------------------------------------------------------

function Get-PythonCommand {
    $candidates = @(
        @{ Exe = 'py';     Args = @('-3.13') },
        @{ Exe = 'py';     Args = @('-3.12') },
        @{ Exe = 'py';     Args = @('-3.11') },
        @{ Exe = 'python'; Args = @()          },
        @{ Exe = 'python3';Args = @()          }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $versionArgs = @($c.Args) + @('-c', 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
        try {
            $version = & $c.Exe @versionArgs 2>$null
        } catch {
            continue
        }
        if (-not $version) { continue }
        $parts = $version.Trim().Split('.')
        if ($parts.Length -lt 2) { continue }
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
            return [pscustomobject]@{
                Exe     = $c.Exe
                Args    = $c.Args
                Version = $version.Trim()
            }
        }
    }
    return $null
}

$python = Get-PythonCommand
if (-not $python) {
    Stop-Install ("No Python 3.11+ found. Tried 'py -3.13' / '-3.12' / '-3.11', " +
                  "'python', 'python3'. Install Python 3.11+ from " +
                  "https://www.python.org/downloads/ (ensure " +
                  "'tcl/tk and IDLE' is checked for GUI support) and re-run.")
}
Write-Cyan "    python: $($python.Version) ($($python.Exe) $($python.Args -join ' '))"

# -----------------------------------------------------------------------------
# Pick install tool (uv preferred, pipx fallback, offer to install uv)
# -----------------------------------------------------------------------------

function Select-InstallTool {
    if ($Tool) {
        if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
            Stop-Install "$Tool requested via -Tool but not found on PATH."
        }
        return $Tool
    }
    if (Get-Command uv   -ErrorAction SilentlyContinue) { return 'uv'   }
    if (Get-Command pipx -ErrorAction SilentlyContinue) { return 'pipx' }

    Write-Yellow 'Neither uv nor pipx found on PATH.'
    if (Confirm 'Install uv (https://docs.astral.sh/uv/) now?') {
        # Official Astral installer. Review https://astral.sh/uv/install.ps1
        # before running if you want to vet it.
        $env:Path = ($env:Path + ';' + "$env:USERPROFILE\.local\bin").Trim(';')
        Invoke-Expression (Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' -UseBasicParsing)
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Stop-Install 'uv install did not put uv on PATH. Re-open your shell and re-run.'
        }
        return 'uv'
    }
    Stop-Install 'No installer available. Install uv or pipx and re-run this script.'
}

$toolName = Select-InstallTool
Write-Cyan "    install tool: $toolName"

# -----------------------------------------------------------------------------
# Tk bindings (tkinter)
#
# The python.org Windows installer ships Tcl/Tk by default; this is a
# best-effort check with an actionable hint when it's missing.
# -----------------------------------------------------------------------------

if (-not $SkipTk) {
    $tkArgs = @($python.Args) + @('-c', 'import tkinter')
    & $python.Exe @tkArgs *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Cyan '    tk: already available'
    } else {
        Write-Yellow "Tkinter is not available in $($python.Exe). 'qr23mf gui' needs it."
        Write-Yellow ("On Windows re-run the python.org installer and enable the " +
                      "'tcl/tk and IDLE' optional feature, or install a Python build " +
                      "that bundles Tk.")
    }
}

# -----------------------------------------------------------------------------
# Install qr23mf from the source tree
# -----------------------------------------------------------------------------

Write-Cyan "==> Installing qr23mf from $scriptDir via $toolName"
switch ($toolName) {
    'uv'   { & uv   tool install --force --reinstall . }
    'pipx' { & pipx install --force . }
}
if ($LASTEXITCODE -ne 0) {
    Stop-Install "Installation via $toolName failed with exit code $LASTEXITCODE."
}

# -----------------------------------------------------------------------------
# Verify
# -----------------------------------------------------------------------------

if (Get-Command qr23mf -ErrorAction SilentlyContinue) {
    $version = & qr23mf --version
    Write-Green "==> Installed: $version"
    Write-Green "    Try:  qr23mf generate --text 'https://example.com' --out coaster.3mf"
    Write-Green "    Or:   qr23mf gui"
} else {
    Write-Yellow 'qr23mf installed but not on PATH yet. You may need to update your shell:'
    switch ($toolName) {
        'uv'   { Write-Yellow '    Run:  uv tool update-shell     # then open a new PowerShell window' }
        'pipx' { Write-Yellow '    Run:  pipx ensurepath          # then open a new PowerShell window' }
    }
}
