param(
    [string]$RepoUrl = "https://github.com/vegetpig/super-imyaigc-signin.git",
    [string]$TargetDir = "$env:USERPROFILE\.codex\skills\super-imyaigc-signin",
    [string]$Phone = "YOUR_PHONE",
    [switch]$SkipDependencies,
    [switch]$SkipPlaywright,
    [switch]$Verify
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

function Ensure-Parent {
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent | Out-Null
    }
}

function Install-From-CurrentDirectory {
    param([string]$SourceDir, [string]$DestinationDir)

    if (Test-Path $DestinationDir) {
        throw "Target exists and is not a Git checkout: $DestinationDir"
    }

    Ensure-Parent $DestinationDir
    New-Item -ItemType Directory -Path $DestinationDir | Out-Null
    Get-ChildItem -Force $SourceDir |
        Where-Object { $_.Name -notin @(".git", "__pycache__") } |
        Copy-Item -Destination $DestinationDir -Recurse -Force
}

Require-Command git
Require-Command python

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptRoot = (Resolve-Path $scriptRoot).Path
$targetExists = Test-Path $TargetDir
$targetGit = Test-Path (Join-Path $TargetDir ".git")
$currentLooksLikeRepo = (Test-Path (Join-Path $scriptRoot "SKILL.md")) -and (Test-Path (Join-Path $scriptRoot "scripts\config.json"))

Write-Step "Preparing skill directory"
if ($targetGit) {
    git -C $TargetDir pull --ff-only
} elseif ($targetExists) {
    $targetResolved = (Resolve-Path $TargetDir).Path
    if ($targetResolved -eq $scriptRoot) {
        Write-Host "Using current directory: $TargetDir"
    } else {
        throw "Target directory already exists but is not a Git checkout: $TargetDir"
    }
} elseif ($currentLooksLikeRepo) {
    Install-From-CurrentDirectory -SourceDir $scriptRoot -DestinationDir $TargetDir
} else {
    Ensure-Parent $TargetDir
    git clone $RepoUrl $TargetDir
}

Write-Step "Entering skill directory"
Set-Location $TargetDir
Write-Host $TargetDir

if (-not $SkipDependencies) {
    Write-Step "Installing Python dependencies"
    python -m pip install -r requirements.txt
}

if (-not $SkipPlaywright) {
    Write-Step "Installing Playwright Chromium"
    python -m playwright install chromium
}

Write-Step "Creating local directories from config"
$configPath = Join-Path $TargetDir "scripts\config.json"
$config = Get-Content -Raw $configPath | ConvertFrom-Json
$paths = $config.paths
foreach ($dir in @($paths.cookie_dir, $paths.screenshot_dir)) {
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
    }
}
foreach ($file in @($paths.log_file, $paths.history_file)) {
    if ($file) {
        Ensure-Parent $file
    }
}

if ($Verify) {
    Write-Step "Verifying login and model list"
    python ".\scripts\signin.py" --phone $Phone --model-count
}

Write-Step "Install complete"
Write-Host "Skill path: $TargetDir"
Write-Host ""
Write-Host "Common verification commands:"
Write-Host "python `".\scripts\signin.py`" --phone $Phone --model-count"
Write-Host "python `".\scripts\imyai_chat.py`" --phone $Phone --list-models-compact"
Write-Host "python `".\scripts\imyai_image.py`" --phone $Phone --list-models-compact"
