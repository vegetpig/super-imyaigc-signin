param(
    [string]$RepoUrl = "https://github.com/vegetpig/super-imyaigc-signin.git",
    [string]$TargetDir = "$env:USERPROFILE\.codex\skills\super-imyaigc-signin",
    [string]$Phone = "",
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

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        $rendered = @($FilePath) + $Arguments
        throw "Command failed with exit code ${LASTEXITCODE}: $($rendered -join ' ')"
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
$currentLooksLikeRepo = (Test-Path (Join-Path $scriptRoot "SKILL.md")) -and (Test-Path (Join-Path $scriptRoot "scripts\config.template.json"))

Write-Step "Preparing skill directory"
if ($targetGit) {
    Invoke-Checked -FilePath "git" -Arguments @("-C", $TargetDir, "pull", "--ff-only")
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
    Invoke-Checked -FilePath "git" -Arguments @("clone", $RepoUrl, $TargetDir)
}

Write-Step "Entering skill directory"
Set-Location $TargetDir
Write-Host $TargetDir

if (-not $SkipDependencies) {
    Write-Step "Installing Python dependencies"
    Invoke-Checked -FilePath "python" -Arguments @("-m", "pip", "install", "-r", "requirements.txt")
}

if (-not $SkipPlaywright) {
    Write-Step "Installing Playwright Chromium"
    Invoke-Checked -FilePath "python" -Arguments @("-m", "playwright", "install", "chromium")
}

$configPath = Join-Path $TargetDir "scripts\config.json"
$templatePath = Join-Path $TargetDir "scripts\config.template.json"
if (-not (Test-Path $configPath)) {
    if (-not (Test-Path $templatePath)) {
        throw "Missing config template: $templatePath"
    }
    Write-Step "Bootstrapping local config"
    Copy-Item -LiteralPath $templatePath -Destination $configPath -Force
}

Write-Step "Creating local directories from config"
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
    $verifyArgs = @(".\scripts\signin.py")
    if ($Phone) {
        $verifyArgs += @("--phone", $Phone)
    }
    $verifyArgs += "--model-count"
    Invoke-Checked -FilePath "python" -Arguments $verifyArgs
}

Write-Step "Install complete"
Write-Host "Skill path: $TargetDir"
Write-Host ""
Write-Host "Common verification commands:"
$displayPhone = if ($Phone) { $Phone } else { "YOUR_PHONE" }
Write-Host "python `".\scripts\signin.py`" --phone $displayPhone --model-count"
Write-Host "python `".\scripts\imyai_chat.py`" --phone $displayPhone --list-models-compact"
Write-Host "python `".\scripts\imyai_image.py`" --phone $displayPhone --list-models-compact"
