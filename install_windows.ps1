# Pleiades ASP Workflow - Windows installer and updater
# Run in PowerShell: powershell -ExecutionPolicy Bypass -File .\INSTALL_WINDOWS.ps1
# Requires micromamba. Does not delete WSL distributions or project data.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepositoryZip = 'https://github.com/IslamKOA/pleiades-asp-workflow/archive/refs/heads/main.zip'
$EnvironmentName = 'pleiades_asp'
$AspVersion = if ($env:PLEIADES_ASP_VERSION) { $env:PLEIADES_ASP_VERSION.Trim() } else { '3.3.0' }
$Workspace = Join-Path $HOME 'Pleiades_ASP_Workflow'

function Assert-Success([string]$Description) {
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit code $LASTEXITCODE)." }
}

function Get-MicromambaExecutable {
    $cmd = Get-Command micromamba.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
        (Join-Path $HOME 'micromamba\micromamba.exe'),
        (Join-Path $HOME 'micromamba\Library\bin\micromamba.exe'),
        (Join-Path $env:LOCALAPPDATA 'micromamba\micromamba.exe')
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw 'micromamba is required. Install micromamba before running this installer.'
}

function Get-UsableWslDistro {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { return $null }
    $raw = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $names = @($raw | ForEach-Object { ($_ -replace "`0", '').Trim() } |
        Where-Object { $_ -and $_ -notlike 'docker-desktop*' })
    if ($env:PLEIADES_ASP_WSL_DISTRO -and $names -contains $env:PLEIADES_ASP_WSL_DISTRO) {
        return $env:PLEIADES_ASP_WSL_DISTRO
    }
    foreach ($name in $names) { if ($name -like 'Ubuntu*') { return $name } }
    foreach ($name in $names) { if ($name -like 'Debian*') { return $name } }
    if ($names.Count -gt 0) { return $names[0] }
    return $null
}

Write-Host '=== Pleiades ASP Workflow: Install / Update ==='
$Mamba = Get-MicromambaExecutable
Write-Host "micromamba: $Mamba"

# 1. Create the dedicated environment if it does not exist.
$environmentsJson = & $Mamba env list --json
Assert-Success 'List micromamba environments'
$envs = $environmentsJson | ConvertFrom-Json
$environmentPath = Join-Path $HOME 'micromamba\envs\pleiades_asp'
$environmentExists = @($envs.envs | Where-Object {
    (Split-Path $_ -Leaf) -eq $EnvironmentName
}).Count -gt 0
if (-not $environmentExists) {
    Write-Host '[1/6] Creating Python environment...'
    & $Mamba create -y -n $EnvironmentName -c conda-forge python=3.11 pip
    Assert-Success 'Create Python environment'
} else {
    Write-Host '[1/6] Reusing existing Python environment.'
}

# 2. Download exactly one GitHub revision; install the package and notebook from it.
Write-Host '[2/6] Downloading latest GitHub revision (Git not required)...'
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('pleiades-install-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
try {
    $zipPath = Join-Path $tempRoot 'repository.zip'
    Invoke-WebRequest -Uri $RepositoryZip -OutFile $zipPath -UseBasicParsing
    Expand-Archive -LiteralPath $zipPath -DestinationPath $tempRoot -Force
    $repo = Get-ChildItem -LiteralPath $tempRoot -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'pyproject.toml') } |
        Select-Object -First 1
    if (-not $repo) { throw 'Downloaded repository lacks pyproject.toml.' }
    $repoRoot = $repo.FullName

    Write-Host '[3/6] Installing package and Jupyter into pleiades_asp...'
    & $Mamba run -n $EnvironmentName python -m pip install --upgrade --no-cache-dir $repoRoot jupyterlab ipywidgets
    Assert-Success 'Install workflow and Jupyter'

    # 3. Ensure a working WSL distribution. Never unregister an existing distro.
    Write-Host '[4/6] Checking WSL...'
    $distro = Get-UsableWslDistro
    if (-not $distro) {
        if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
            throw 'WSL is unavailable. Enable Windows Subsystem for Linux and rerun this installer.'
        }
        Write-Host 'Ubuntu is not installed. Windows may request elevation and/or a restart.'
        & wsl.exe --install -d Ubuntu
        Assert-Success 'Install Ubuntu WSL'
        $distro = Get-UsableWslDistro
        if (-not $distro) {
            throw 'Complete Ubuntu initial setup (and restart Windows if requested), then rerun this script.'
        }
    }
    $env:PLEIADES_ASP_WSL_DISTRO = $distro
    [Environment]::SetEnvironmentVariable('PLEIADES_ASP_WSL_DISTRO', $distro, 'User')
    & wsl.exe -d $distro -- sh -lc 'printf "WSL home: %s\n" "$HOME"'
    Assert-Success 'Initialize WSL distribution'

    # 4. Install managed ASP in WSL. Preserve scientific data and other environments.
    Write-Host "[5/6] Installing/verifying ASP $AspVersion in $distro..."
    & $Mamba run -n $EnvironmentName asp-install $AspVersion
    Assert-Success 'ASP installation'

    # 5. Refresh the installed workspace, then overwrite ONLY the shipped notebook
    # using the very same downloaded GitHub revision as the Python package.
    Write-Host '[6/6] Updating notebook and workspace...'
    & $Mamba run -n $EnvironmentName pleiades-workflow-init
    Assert-Success 'Initialize workflow workspace'
    New-Item -ItemType Directory -Force -Path $Workspace | Out-Null

    $notebook = Join-Path $repoRoot 'Pleiades_ASP_Workflow.ipynb'
    if (-not (Test-Path -LiteralPath $notebook)) {
        $notebook = Get-ChildItem -LiteralPath $repoRoot -Recurse -Filter 'Pleiades_ASP_Workflow.ipynb' -File |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $notebook -or -not (Test-Path -LiteralPath $notebook)) {
        throw 'Cannot locate Pleiades_ASP_Workflow.ipynb in the GitHub download.'
    }
    $notebookContent = Get-Content -LiteralPath $notebook -Raw -Encoding UTF8
    if ($notebookContent -notmatch 'from pleiades_asp_workflow\.interface_bundle import asp_utils2') {
        throw 'GitHub notebook still uses an unsupported local import; fix notebook startup before deploying.'
    }
    Copy-Item -LiteralPath $notebook -Destination (Join-Path $Workspace 'Pleiades_ASP_Workflow.ipynb') -Force

    # Verify package import comes from the installation and staging exists.
    & $Mamba run -n $EnvironmentName python -c "from pleiades_asp_workflow.interface_bundle import asp_utils2; from pleiades_asp_runner.staging import stage_stereo_command; print('Verified:', asp_utils2.__file__)"
    Assert-Success 'Verify packaged interface and staging'

    Write-Host ''
    Write-Host 'Installation/update completed.' -ForegroundColor Green
    Write-Host "Environment: $EnvironmentName"
    Write-Host "WSL distribution: $distro"
    Write-Host "Notebook: $(Join-Path $Workspace 'Pleiades_ASP_Workflow.ipynb')"
    Write-Host 'Launch with:'
    Write-Host ('  micromamba run -n {0} jupyter lab "{1}"' -f $EnvironmentName, (Join-Path $Workspace 'Pleiades_ASP_Workflow.ipynb'))
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
