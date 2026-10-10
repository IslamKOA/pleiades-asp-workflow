
# Pleiades ASP Workflow - Windows installer and updater
# Run: powershell -ExecutionPolicy Bypass -File .\install_windows.ps1
# Requires an existing micromamba, mamba, or conda installation.
# Never deletes existing WSL distributions, Conda environments, or user projects.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepositoryZip = 'https://github.com/IslamKOA/pleiades-asp-workflow/archive/refs/heads/main.zip'
$EnvironmentName = 'pleiades_asp'
$AspVersion = if ($env:PLEIADES_ASP_VERSION) { $env:PLEIADES_ASP_VERSION.Trim() } else { '3.3.0' }
$Workspace = Join-Path $HOME 'Pleiades_ASP_Workflow'

function Assert-ExitCode([string]$Action) {
    if ($LASTEXITCODE -ne 0) { throw "$Action failed (exit code $LASTEXITCODE)." }
}

function Resolve-Executable([string[]]$Names, [string[]]$Candidates) {
    foreach ($name in $Names) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command -and $command.Source -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) {
            return $command.Source
        }
    }
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    return $null
}

function Find-EnvironmentManager {
    # Prefer micromamba if available; do not confuse PowerShell's 'Mamba' alias
    # with the micromamba.exe executable.
    $micromamba = Resolve-Executable @('micromamba.exe') @(
        (Join-Path $env:LOCALAPPDATA 'micromamba\micromamba.exe'),
        (Join-Path $HOME 'micromamba\micromamba.exe'),
        (Join-Path $HOME 'micromamba\Library\bin\micromamba.exe')
    )
    if ($micromamba) { return @{ Name = 'micromamba'; Exe = $micromamba } }

    $mamba = Resolve-Executable @('mamba.exe', 'mamba.bat') @()
    if ($mamba) { return @{ Name = 'mamba'; Exe = $mamba } }

    $condaCandidates = @()
    foreach ($root in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:PROGRAMDATA)) {
        if ($root) {
            foreach ($folder in @('miniforge3', 'miniconda3', 'anaconda3', 'Miniforge3', 'Miniconda3', 'Anaconda3')) {
                $condaCandidates += Join-Path $root "$folder\Scripts\conda.exe"
                $condaCandidates += Join-Path $root "$folder\condabin\conda.bat"
            }
        }
    }
    $conda = Resolve-Executable @('conda.exe', 'conda.bat') $condaCandidates
    if ($conda) { return @{ Name = 'conda'; Exe = $conda } }
    throw 'No supported environment manager found. Install micromamba, Miniforge, Miniconda, or Anaconda and rerun this installer.'
}

function Invoke-EnvironmentCommand([string[]]$Arguments, [string]$Action) {
    & $Manager.Exe @Arguments
    Assert-ExitCode $Action
}

function Find-UsableWslDistro {
    if (-not (Get-Command wsl.exe -CommandType Application -ErrorAction SilentlyContinue)) { return $null }
    $raw = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $names = @($raw | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object {
        $_ -and $_ -notlike 'docker-desktop*'
    })
    if ($env:PLEIADES_ASP_WSL_DISTRO -and $names -contains $env:PLEIADES_ASP_WSL_DISTRO) {
        return $env:PLEIADES_ASP_WSL_DISTRO
    }
    foreach ($name in $names) { if ($name -like 'Ubuntu*') { return $name } }
    foreach ($name in $names) { if ($name -like 'Debian*') { return $name } }
    if ($names.Count -gt 0) { return $names[0] }
    return $null
}

Write-Host '=== Pleiades ASP Workflow: Install / Update ==='
$Manager = Find-EnvironmentManager
Write-Host "Environment manager: $($Manager.Name)"
Write-Host "Executable: $($Manager.Exe)"

# 1. Create a separate environment, if needed.
Write-Host '[1/6] Checking dedicated Python environment...'
$environmentList = & $Manager.Exe env list --json
Assert-ExitCode 'Query environments'
$environmentInfo = ($environmentList | Out-String) | ConvertFrom-Json
$environmentExists = @($environmentInfo.envs | Where-Object {
    (Split-Path -Path $_ -Leaf) -eq $EnvironmentName
}).Count -gt 0
if (-not $environmentExists) {
    Invoke-EnvironmentCommand @('create', '-y', '-n', $EnvironmentName, '-c', 'conda-forge', 'python=3.11', 'pip') 'Create Python environment'
} else {
    Write-Host "Reusing existing environment: $EnvironmentName"
}

# 2. Download the repository once, ensuring notebook and Python package match.
Write-Host '[2/6] Downloading GitHub main branch (Git is not required)...'
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('pleiades-install-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
try {
    $zipPath = Join-Path $tempRoot 'repository.zip'
    Invoke-WebRequest -Uri $RepositoryZip -OutFile $zipPath -UseBasicParsing
    Expand-Archive -LiteralPath $zipPath -DestinationPath $tempRoot -Force
    $repo = Get-ChildItem -LiteralPath $tempRoot -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'pyproject.toml') } |
        Select-Object -First 1
    if (-not $repo) { throw 'GitHub archive does not contain pyproject.toml.' }
    $repoRoot = $repo.FullName

    # 3. Install/update from this same downloaded revision.
    Write-Host '[3/6] Installing workflow and Jupyter...'
    Invoke-EnvironmentCommand @('run', '-n', $EnvironmentName, 'python', '-m', 'pip', 'install', '--upgrade', '--force-reinstall', '--no-cache-dir', $repoRoot) 'Install Python package'

    # 4. Install/initialize a Linux distro if none is available.
    Write-Host '[4/6] Checking WSL...'
    $distro = Find-UsableWslDistro
    if (-not $distro) {
        if (-not (Get-Command wsl.exe -CommandType Application -ErrorAction SilentlyContinue)) {
            throw 'WSL is not available. Enable WSL and rerun the installer.'
        }
        Write-Host 'No WSL Linux distribution found. Requesting Ubuntu installation...'
        & wsl.exe --install -d Ubuntu
        Assert-ExitCode 'Install Ubuntu'
        $distro = Find-UsableWslDistro
        if (-not $distro) {
            throw 'Complete the Windows restart and Ubuntu first-run username/password setup, then rerun this installer.'
        }
    }
    $env:PLEIADES_ASP_WSL_DISTRO = $distro
    [Environment]::SetEnvironmentVariable('PLEIADES_ASP_WSL_DISTRO', $distro, 'User')
    & wsl.exe -d $distro -- sh -lc 'printf "WSL home: %s\n" "$HOME"'
    Assert-ExitCode 'Initialize WSL'

    # 5. Install or verify the managed Linux ASP distribution.
    Write-Host "[5/6] Installing/verifying ASP $AspVersion in $distro..."
    Invoke-EnvironmentCommand @('run', '-n', $EnvironmentName, 'asp-install', $AspVersion) 'Install ASP'

    # 6. Refresh only package-managed workspace resources.
    Write-Host '[6/6] Refreshing notebook and workspace...'
    Invoke-EnvironmentCommand @('run', '-n', $EnvironmentName, 'pleiades-workflow-init') 'Initialize workspace'

    $notebook = Join-Path $Workspace 'Pleiades_ASP_Workflow.ipynb'
    if (-not (Test-Path -LiteralPath $notebook -PathType Leaf)) {
        throw "Notebook was not created: $notebook"
    }
    $notebookContent = Get-Content -LiteralPath $notebook -Raw -Encoding UTF8
    if ($notebookContent -notmatch 'from pleiades_asp_workflow\.interface_bundle import asp_utils2') {
        throw 'The installed notebook does not contain the corrected packaged import. Check the committed GitHub notebook and package data.'
    }
    Invoke-EnvironmentCommand @('run', '-n', $EnvironmentName, 'python', '-c', "from pleiades_asp_workflow.interface_bundle import asp_utils2; from pleiades_asp_runner.staging import stage_stereo_command; print('Interface:', asp_utils2.__file__)") 'Verify interface and staging imports'

    Write-Host ''
    Write-Host 'Installation/update completed.' -ForegroundColor Green
    Write-Host "Environment manager: $($Manager.Name)"
    Write-Host "Environment: $EnvironmentName"
    Write-Host "WSL distribution: $distro"
    Write-Host "Notebook: $notebook"
    Write-Host 'Launch the workflow using:'
    Write-Host ('  & "{0}" run -n {1} jupyter lab "{2}"' -f $Manager.Exe, $EnvironmentName, $notebook)
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
