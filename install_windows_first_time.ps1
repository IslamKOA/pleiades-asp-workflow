# Pléiades ASP Workflow - first-time Windows setup (no Conda required)
# Downloads standalone Micromamba for the current user, then runs the standard installer.
# Does not change or remove any existing Python / Conda environments.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not [Environment]::Is64BitOperatingSystem) {
    throw '64-bit Windows is required.'
}
$micromambaExe = Join-Path $env:LOCALAPPDATA 'micromamba\micromamba.exe'
$micromambaRoot = Join-Path $HOME 'micromamba'
$scriptUrl = 'https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/install_windows.ps1'

# Reuse existing managers: the standard installer chooses Micromamba, Mamba, or Conda.
$existingManager = @('micromamba.exe','mamba.exe','mamba.bat','conda.exe','conda.bat') |
    ForEach-Object { Get-Command $_ -CommandType Application -ErrorAction SilentlyContinue } |
    Where-Object { $_ } | Select-Object -First 1
$knownManager = @(
    $micromambaExe,
    (Join-Path $HOME 'micromamba\micromamba.exe'),
    (Join-Path $HOME 'miniforge3\Scripts\conda.exe'),
    (Join-Path $HOME 'miniconda3\Scripts\conda.exe'),
    (Join-Path $HOME 'anaconda3\Scripts\conda.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Miniforge3\Scripts\conda.exe')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }

if (-not $existingManager -and -not $knownManager) {
    Write-Host 'No environment manager detected. Installing lightweight Micromamba...'
    if (-not (Get-Command tar.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Windows tar.exe is required to extract Micromamba.'
    }
    $work = Join-Path $env:TEMP ('asp-micromamba-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    try {
        $archive = Join-Path $work 'micromamba.tar.bz2'
        Invoke-WebRequest -Uri 'https://micro.mamba.pm/api/micromamba/win-64/latest' -OutFile $archive -UseBasicParsing
        & tar.exe -xf $archive -C $work 'Library/bin/micromamba.exe'
        if ($LASTEXITCODE -ne 0) { throw "Micromamba extraction failed (exit code $LASTEXITCODE)." }
        $extracted = Join-Path $work 'Library\bin\micromamba.exe'
        if (-not (Test-Path -LiteralPath $extracted -PathType Leaf)) { throw 'Micromamba executable not found in downloaded archive.' }
        New-Item -ItemType Directory -Path (Split-Path $micromambaExe -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $extracted -Destination $micromambaExe -ErrorAction Stop
        & $micromambaExe --version
        if ($LASTEXITCODE -ne 0) { throw 'Micromamba executable verification failed.' }
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host 'Micromamba installed successfully.' -ForegroundColor Green
} else {
    Write-Host 'Existing environment manager detected; no additional manager will be installed.'
}

# Provide a default Micromamba root for newly installed Micromamba.
# Preserve an explicit user setting when present.
if ((Test-Path -LiteralPath $micromambaExe -PathType Leaf) -and -not $env:MAMBA_ROOT_PREFIX) {
    $env:MAMBA_ROOT_PREFIX = $micromambaRoot
}

$installer = Join-Path $env:TEMP ('asp-install-' + [Guid]::NewGuid().ToString('N').Substring(0,8) + '.ps1')
try {
    Write-Host 'Running the standard Pléiades ASP workflow installer...'
    Invoke-WebRequest -Uri $scriptUrl -OutFile $installer -UseBasicParsing
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { throw "Pléiades ASP installation failed (exit code $LASTEXITCODE)." }
    Write-Host 'Pléiades ASP Workflow setup completed.' -ForegroundColor Green
} finally {
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
}
