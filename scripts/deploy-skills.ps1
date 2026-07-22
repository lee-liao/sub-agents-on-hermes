<#
.SYNOPSIS
    Sync this repo's skills into the Hermes deployment. The repo is the source of truth.

.DESCRIPTION
    Skills are authored here and copied to %HERMES_HOME%\skills. Editing the
    deployed copy is how ~20 KB of operational content once ended up existing on
    exactly one machine, unversioned (see docs/prd/03-open-threads.md Thread 10).
    This script makes the sync one command so that never has to happen again.

    The copy is deliberately ONE-WAY (repo -> deployment). If the deployment has
    edits you want to keep, -Check will tell you, and you reverse-sync by hand
    and commit before deploying.

.PARAMETER Check
    Report drift and change nothing. Exits 1 if any skill differs, so it works
    as a pre-commit or CI guard.

.PARAMETER HermesHome
    Override the deployment root. Defaults to $env:HERMES_HOME, then
    $env:LOCALAPPDATA\hermes.

.EXAMPLE
    .\scripts\deploy-skills.ps1 -Check     # what would change?
    .\scripts\deploy-skills.ps1            # do it
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [string]$HermesHome
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# repo-relative skill dir -> deployment-relative path under <HermesHome>\skills.
# Most skills map 1:1; windows-ai-agent-adaptation lives under the
# software-development tree in the deployment, so it needs an explicit target.
$SkillMap = @{
    'claude-code-gold'           = 'claude-code-gold'
    'windows-ai-agent-adaptation' = 'software-development\windows-ai-agent-adaptation'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$srcRoot  = Join-Path $repoRoot 'skills'

if (-not $HermesHome) { $HermesHome = $env:HERMES_HOME }
if (-not $HermesHome) { $HermesHome = Join-Path $env:LOCALAPPDATA 'hermes' }
$dstRoot = Join-Path $HermesHome 'skills'

if (-not (Test-Path $dstRoot)) {
    throw "Hermes skills directory not found: $dstRoot (pass -HermesHome to override)"
}

Write-Host "repo   : $srcRoot"
Write-Host "deploy : $dstRoot"
Write-Host ""

# Compare by content hash so line-ending noise in git does not read as drift.
function Get-FileMap([string]$root) {
    $map = @{}
    if (-not (Test-Path $root)) { return $map }
    Get-ChildItem -Path $root -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length).TrimStart('\')
        if ($rel -like '__pycache__*' -or $rel -like '*\__pycache__\*') { return }
        $map[$rel] = (Get-FileHash -Path $_.FullName -Algorithm SHA256).Hash
    }
    return $map
}

$drift = $false

foreach ($name in $SkillMap.Keys | Sort-Object) {
    $src = Join-Path $srcRoot $name
    $dst = Join-Path $dstRoot $SkillMap[$name]

    if (-not (Test-Path $src)) {
        Write-Warning "$name : not in the repo, skipping"
        continue
    }

    $srcFiles = Get-FileMap $src
    $dstFiles = Get-FileMap $dst

    $new     = @($srcFiles.Keys | Where-Object { -not $dstFiles.ContainsKey($_) })
    $changed = @($srcFiles.Keys | Where-Object { $dstFiles.ContainsKey($_) -and $dstFiles[$_] -ne $srcFiles[$_] })
    # Deployment-only files are reported, never deleted: they may be content that
    # belongs in the repo and has not been reverse-synced yet.
    $extra   = @($dstFiles.Keys | Where-Object { -not $srcFiles.ContainsKey($_) })

    if (-not $new -and -not $changed -and -not $extra) {
        Write-Host "  OK       $name" -ForegroundColor Green
        continue
    }

    $drift = $true
    Write-Host "  DRIFT    $name" -ForegroundColor Yellow
    foreach ($f in $new)     { Write-Host "      + $f            (repo only)" }
    foreach ($f in $changed) { Write-Host "      ~ $f            (differs)" }
    foreach ($f in $extra)   { Write-Host "      ! $f            (DEPLOYMENT ONLY - reverse-sync and commit before deploying)" -ForegroundColor Red }

    if (-not $Check) {
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        foreach ($f in ($new + $changed)) {
            $s = Join-Path $src $f
            $d = Join-Path $dst $f
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $d) | Out-Null
            Copy-Item -Path $s -Destination $d -Force
        }
        Write-Host "      -> deployed $($new.Count + $changed.Count) file(s)" -ForegroundColor Cyan
    }
}

Write-Host ""
if ($Check) {
    if ($drift) { Write-Host "Drift detected. Run without -Check to deploy." -ForegroundColor Yellow; exit 1 }
    Write-Host "All skills in sync." -ForegroundColor Green
    exit 0
}
Write-Host "Deploy complete. Re-run with -Check to confirm." -ForegroundColor Green
