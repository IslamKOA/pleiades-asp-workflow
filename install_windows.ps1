# WASA-DEM - Windows installer and updater
# Run: powershell -ExecutionPolicy Bypass -File .\install_windows.ps1
# Reuses existing Conda/Mamba/Micromamba or installs standalone Micromamba if missing.
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

function Add-ManagerCandidate([string]$Name, [string]$Exe) {
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe -PathType Leaf)) { return }
    $resolved = (Resolve-Path -LiteralPath $Exe).Path
    if (@($script:ManagerCandidates | Where-Object { $_.Exe -ieq $resolved }).Count -eq 0) {
        [void]$script:ManagerCandidates.Add(@{ Name = $Name; Exe = $resolved })
    }
}

function Find-EnvironmentManager {
    # Search both PATH and common installation directories. Python itself is
    # always run INSIDE the selected environment, never via system PATH.
    $script:ManagerCandidates = New-Object System.Collections.ArrayList
    foreach ($entry in @(
        @{ Name = 'micromamba'; Commands = @('micromamba.exe') },
        @{ Name = 'mamba'; Commands = @('mamba.exe', 'mamba.bat') },
        @{ Name = 'conda'; Commands = @('conda.exe', 'conda.bat') }
    )) {
        foreach ($commandName in $entry.Commands) {
            $cmd = Get-Command $commandName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($cmd) { Add-ManagerCandidate $entry.Name $cmd.Source }
        }
    }
    if ($env:CONDA_EXE) { Add-ManagerCandidate 'conda' $env:CONDA_EXE }
    if ($env:MAMBA_EXE) { Add-ManagerCandidate 'mamba' $env:MAMBA_EXE }
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA 'micromamba\micromamba.exe'),
        (Join-Path $HOME 'micromamba\micromamba.exe'),
        (Join-Path $HOME 'micromamba\Library\bin\micromamba.exe'),
        (Join-Path $HOME '.local\bin\micromamba.exe')
    )) { Add-ManagerCandidate 'micromamba' $candidate }

    foreach ($root in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:PROGRAMDATA, $env:CONDA_PREFIX)) {
        if (-not $root) { continue }
        foreach ($folder in @(
            '', 'miniforge3','miniconda3','anaconda3',
            'Miniforge3','Miniconda3','Anaconda3',
            'Programs\Miniforge3','Programs\Miniconda3','Programs\Anaconda3',
            'Continuum\anaconda3'
        )) {
            $base = if ($folder) { Join-Path $root $folder } else { $root }
            foreach ($relative in @('Scripts\conda.exe','condabin\conda.bat')) {
                Add-ManagerCandidate 'conda' (Join-Path $base $relative)
            }
        }
    }

    # If an existing pleiades_asp environment is found, choose its manager.
    # Otherwise choose the first usable manager and create a new environment.
    $firstWorking = $null
    foreach ($candidate in $script:ManagerCandidates) {
        if ($candidate.Name -eq 'micromamba' -and -not $env:MAMBA_ROOT_PREFIX) {
            foreach ($root in @((Join-Path $HOME 'micromamba'), (Join-Path $env:LOCALAPPDATA 'micromamba'))) {
                if (Test-Path -LiteralPath (Join-Path $root "envs\$EnvironmentName") -PathType Container) {
                    $env:MAMBA_ROOT_PREFIX = $root
                    break
                }
            }
            if (-not $env:MAMBA_ROOT_PREFIX) { $env:MAMBA_ROOT_PREFIX = Join-Path $HOME 'micromamba' }
        }
        try {
            $raw = & $candidate.Exe env list --json 2>$null
            if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
            $info = ($raw | Out-String) | ConvertFrom-Json -ErrorAction Stop
            if ($null -eq $info.envs) { throw 'Missing environments in JSON output' }
            $hasEnvironment = @($info.envs | Where-Object {
                (Split-Path -Path $_ -Leaf) -eq $EnvironmentName
            }).Count -gt 0
            if ($hasEnvironment) {
                Write-Host "Found existing $EnvironmentName environment using $($candidate.Name)."
                return $candidate
            }
            if (-not $firstWorking) { $firstWorking = $candidate }
        } catch {
            Write-Warning ("Ignoring unavailable environment manager at " + $candidate.Exe + ": " + $_.Exception.Message)
        }
    }
    if ($firstWorking) { return $firstWorking }
    return $null
}

function Install-MicromambaFallback {
    # Official standalone Micromamba build: no preinstalled Python or Git.
    if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
    if (-not (Get-Command tar.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Windows tar.exe is required to extract Micromamba. Install tar or a Conda-compatible manager and rerun.'
    }
    $exe = Join-Path $env:LOCALAPPDATA 'micromamba\micromamba.exe'
    if (Test-Path -LiteralPath $exe -PathType Leaf) {
        $env:MAMBA_ROOT_PREFIX = if ($env:MAMBA_ROOT_PREFIX) { $env:MAMBA_ROOT_PREFIX } else { Join-Path $HOME 'micromamba' }
        return @{ Name = 'micromamba'; Exe = $exe }
    }
    Write-Host 'No working Conda/Mamba/Micromamba found; installing standalone Micromamba for this user...' -ForegroundColor Yellow
    $stage = Join-Path $env:TEMP ('wasa-micromamba-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    try {
        $archive = Join-Path $stage 'micromamba.tar.bz2'
        Invoke-WebRequest -Uri 'https://micro.mamba.pm/api/micromamba/win-64/latest' -OutFile $archive -UseBasicParsing
        & tar.exe -xf $archive -C $stage 'Library/bin/micromamba.exe'
        Assert-ExitCode 'Extract Micromamba'
        $extracted = Join-Path $stage 'Library\bin\micromamba.exe'
        if (-not (Test-Path -LiteralPath $extracted -PathType Leaf)) { throw 'Micromamba executable missing in extracted archive.' }
        New-Item -ItemType Directory -Path (Split-Path $exe -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $extracted -Destination $exe
        & $exe --version
        Assert-ExitCode 'Verify Micromamba'
    } finally {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not $env:MAMBA_ROOT_PREFIX) { $env:MAMBA_ROOT_PREFIX = Join-Path $HOME 'micromamba' }
    return @{ Name = 'micromamba'; Exe = $exe }
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

Write-Host '=== WASA-DEM: Install / Update ==='
$Manager = Find-EnvironmentManager
if (-not $Manager) { $Manager = Install-MicromambaFallback }
Write-Host "Environment manager: $($Manager.Name)"
Write-Host "Executable: $($Manager.Exe)"

# 1. Create a separate environment, if needed.
Write-Host '[1/7] Checking dedicated Python environment...'
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
# Verify the real Python inside the managed environment. Never use Windows'
# Python app alias or a possibly unrelated system-level python.exe.
# Avoid Conda's Windows python -c quoting behavior: some Conda versions
# strip embedded quotes and produce invalid Python even with a valid env.
# Run a temporary Python file instead, without relying on system python.exe.
$pythonCheck = Join-Path $env:TEMP ('wasa-dem-python-check-' + [Guid]::NewGuid().ToString('N') + '.py')
@(
    'import sys'
    'print("WASA-DEM Python:", sys.executable, sys.version)'
    'assert sys.version_info >= (3, 10), "WASA-DEM requires Python >= 3.10"'
) | Set-Content -LiteralPath $pythonCheck -Encoding UTF8
try {
    Invoke-EnvironmentCommand @('run', '-n', $EnvironmentName, 'python', $pythonCheck) 'Verify environment Python (>=3.10)'
} finally {
    Remove-Item -LiteralPath $pythonCheck -Force -ErrorAction SilentlyContinue
}

# 2. Download the repository once, ensuring notebook and Python package match.
Write-Host '[2/7] Downloading GitHub main branch (Git is not required)...'
# Prefer a short build path: Windows wheel builds can exceed legacy MAX_PATH
# when temporary directories and package-data notebook paths are nested.
# Fall back to a user-writable directory if the drive root is restricted.
$shortBuildBase = Join-Path $env:SystemDrive 'asp-build'
try {
    New-Item -ItemType Directory -Path $shortBuildBase -Force -ErrorAction Stop | Out-Null
} catch {
    $shortBuildBase = Join-Path $HOME 'asp-build'
    New-Item -ItemType Directory -Path $shortBuildBase -Force | Out-Null
}
$tempRoot = Join-Path $shortBuildBase ('p-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
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

    # Shorten extracted archive folder name before pip/setuptools build.
    $shortRepoRoot = Join-Path $tempRoot 'src'
    Move-Item -LiteralPath $repoRoot -Destination $shortRepoRoot
    $repoRoot = $shortRepoRoot

    # pip may create additional temporary build directories; keep those short too.
    $oldTemp = $env:TEMP
    $oldTmp = $env:TMP
    $env:TEMP = $tempRoot
    $env:TMP = $tempRoot

    # 3. Install/update from this same downloaded revision.
    Write-Host '[3/7] Installing workflow and Jupyter...'
    Invoke-EnvironmentCommand @('run', '-n', $EnvironmentName, 'python', '-m', 'pip', 'install', '--upgrade', '--force-reinstall', '--no-cache-dir', $repoRoot) 'Install Python package'

    # 4. Install/initialize a Linux distro if none is available.
    Write-Host '[4/7] Checking WSL...'
    $distro = Find-UsableWslDistro
    if (-not $distro) {
        if (-not (Get-Command wsl.exe -CommandType Application -ErrorAction SilentlyContinue)) {
            throw 'WSL is not available. Enable WSL and rerun the installer.'
        }
        Write-Host ''
        Write-Host 'Ubuntu will now be installed. Windows may ask for administrator approval or a restart.' -ForegroundColor Yellow
        Write-Host 'On first use, Ubuntu may ask you to choose a Linux username and password.' -ForegroundColor Yellow
        Write-Host 'Once you see a Linux prompt (for example user@computer:$), type exit.' -ForegroundColor Yellow
        Write-Host 'The Windows installer will then continue automatically to ASP installation.' -ForegroundColor Yellow
        Write-Host ''
        & wsl.exe --install -d Ubuntu
        $wslInstallExit = $LASTEXITCODE
        $distro = Find-UsableWslDistro
        if (-not $distro) {
            throw 'Ubuntu setup has not completed. If a Windows restart is required, restart and run this installer again.'
        }
        if ($wslInstallExit -ne 0) {
            Write-Warning "Ubuntu installer returned $wslInstallExit, but '$distro' is now registered; checking usability."
        }
    }
    $env:PLEIADES_ASP_WSL_DISTRO = $distro
    [Environment]::SetEnvironmentVariable('PLEIADES_ASP_WSL_DISTRO', $distro, 'User')
    & wsl.exe -d $distro -- sh -lc 'printf "WSL home: %s\n" "$HOME"'
    Assert-ExitCode 'Initialize WSL'

    # 5. Install or verify the managed Linux ASP distribution.
    Write-Host "[5/7] Installing/verifying ASP $AspVersion in $distro..."
    Invoke-EnvironmentCommand @('run', '-n', $EnvironmentName, 'asp-install', $AspVersion) 'Install ASP'

    # 6. Refresh only package-managed workspace resources.
    Write-Host '[6/7] Refreshing notebook and workspace...'
    Invoke-EnvironmentCommand @('run', '-n', $EnvironmentName, 'pleiades-workflow-init') 'Initialize workspace'

    $notebook = Join-Path $Workspace 'Pleiades_ASP_Workflow.ipynb'
    if (-not (Test-Path -LiteralPath $notebook -PathType Leaf)) {
        throw "Notebook was not created: $notebook"
    }
    $notebookContent = Get-Content -LiteralPath $notebook -Raw -Encoding UTF8
    if ($notebookContent -notmatch 'from pleiades_asp_workflow\.interface_bundle import asp_utils2') {
        throw 'The installed notebook does not contain the corrected packaged import. Check the committed GitHub notebook and package data.'
    }
    # Use a Python script file here as well; no nested quotes are passed
    # through Windows PowerShell -> Conda run -> Python.
    $importCheck = Join-Path $tempRoot 'verify_wasa_dem_imports.py'
    @(
        'from pleiades_asp_workflow.interface_bundle import asp_utils2'
        'from pleiades_asp_runner.staging import stage_stereo_command'
        'print("WASA-DEM interface:", asp_utils2.__file__)'
    ) | Set-Content -LiteralPath $importCheck -Encoding UTF8
    Invoke-EnvironmentCommand @('run', '-n', $EnvironmentName, 'python', $importCheck) 'Verify interface and staging imports'

    # Add the desktop shortcut without changing existing Python/ASP installations.
    Write-Host '[7/7] Creating/updating WASA-DEM desktop shortcut...'
    try {
        & (Join-Path $repoRoot 'create_wasa_dem_shortcut.ps1') -Workspace $Workspace -ManagerExecutable $Manager.Exe -ManagerName $Manager.Name -EnvironmentName $EnvironmentName
    } catch {
        Write-Warning ("WASA-DEM desktop shortcut could not be created: " + $_.Exception.Message)
        Write-Warning "The workflow is installed and can still be launched manually."
    }

    Write-Host ''
    Write-Host 'Installation/update completed.' -ForegroundColor Green
    Write-Host "Environment manager: $($Manager.Name)"
    Write-Host "Environment: $EnvironmentName"
    Write-Host "WSL distribution: $distro"
    Write-Host "Notebook: $notebook"
    Write-Host 'Launch WASA-DEM from your Windows Desktop shortcut, or use:'
    Write-Host ('  & "{0}" run -n {1} jupyter lab "{2}"' -f $Manager.Exe, $EnvironmentName, $notebook)
} finally {
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
