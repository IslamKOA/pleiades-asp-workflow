# WASA-DEM - lightweight Windows update (existing installation only)
# Updates package code only; preserves environments, dependencies, ASP, WSL,
# existing notebooks, shortcuts, settings, and research data.
$ErrorActionPreference = 'Stop'
$EnvironmentName = 'pleiades_asp'
$PackageUrl = 'https://github.com/IslamKOA/pleiades-asp-workflow/archive/refs/heads/main.zip'

Write-Host '=== WASA-DEM: Lightweight Windows Update ==='
Write-Host 'Close JupyterLab and stop WASA-DEM processing before updating.'

# Find the Python interpreter in the existing dedicated environment.
# Do not create, delete, activate, or solve any environment.
$roots = New-Object System.Collections.ArrayList
foreach ($value in @($env:CONDA_PREFIX, $env:CONDA_ENVS_PATH, $env:MAMBA_ROOT_PREFIX, $HOME, $env:LOCALAPPDATA)) {
    if ($value) { [void]$roots.Add($value) }
}
foreach ($base in @($HOME, $env:LOCALAPPDATA)) {
    if (-not $base) { continue }
    foreach ($folder in @('miniconda3', 'anaconda3', 'miniforge3', 'micromamba', 'Miniconda3', 'Anaconda3', 'Miniforge3')) {
        [void]$roots.Add((Join-Path $base $folder))
    }
}
$pythonCandidates = New-Object System.Collections.ArrayList
foreach ($root in $roots) {
    [void]$pythonCandidates.Add((Join-Path $root "envs\$EnvironmentName\python.exe"))
    if ((Split-Path $root -Leaf) -eq $EnvironmentName) {
        [void]$pythonCandidates.Add((Join-Path $root 'python.exe'))
    }
}

# Also consult Conda/Mamba environment lists (supports nonstandard env paths).
$managers = @('conda', 'micromamba', 'mamba')
foreach ($manager in $managers) {
    $cmd = Get-Command $manager -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { continue }
    try {
        $json = & $cmd.Source env list --json 2>$null
        if ($LASTEXITCODE -ne 0) { continue }
        $info = ($json | Out-String) | ConvertFrom-Json
        foreach ($dir in @($info.envs)) {
            if ((Split-Path $dir -Leaf) -eq $EnvironmentName) {
                [void]$pythonCandidates.Add((Join-Path $dir 'python.exe'))
            }
        }
    } catch {
        Write-Host "Skipping unavailable manager: $manager"
    }
}
$python = $null
foreach ($candidate in $pythonCandidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        $python = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}
if (-not $python) {
    throw "Existing $EnvironmentName environment not found. Use the full Windows installer for a new installation."
}

# Verify this is the correct environment before modifying it.
$prefix = & $python -c 'import os,sys; print(os.path.basename(sys.prefix))'
if ($LASTEXITCODE -ne 0 -or (($prefix | Out-String).Trim() -ne $EnvironmentName)) {
    throw "Refusing to modify unexpected Python environment: $python"
}
Write-Host "Using existing environment: $python"
Write-Host 'Updating WASA-DEM package only (no dependency resolution, no ASP/WSL changes)...'
& $python -m pip install --upgrade --force-reinstall --no-deps --no-cache-dir $PackageUrl
if ($LASTEXITCODE -ne 0) { throw 'WASA-DEM Python package update failed.' }

# Avoid nested Python string quotes: Windows PowerShell 5.1 can strip them
# when forwarding native-command arguments.
& $python -c 'import pleiades_asp_workflow; import pleiades_asp_workflow.interface_bundle.asp_utils2'
if ($LASTEXITCODE -ne 0) { throw 'Updated WASA-DEM package failed its import check.' }
Write-Host '[OK] Python import verification passed.'

Write-Host '[OK] WASA-DEM Python package updated.' -ForegroundColor Green
Write-Host 'Existing notebooks and project files were not modified.'
Write-Host 'Dependencies, ASP, WSL, Conda environment, and desktop shortcut were left unchanged.'
Write-Host 'Restart JupyterLab to use the updated package.'
