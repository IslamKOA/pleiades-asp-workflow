# Pléiades ASP Workflow - Windows first-time setup (no Conda/Micromamba required)
# Run in Windows PowerShell:
# powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_windows_first_time.ps1
# Installs Miniforge for the current user only, then uses install_windows.ps1.
# Does not remove or change existing Python/Conda environments.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'A 64-bit Windows installation is required.'
}

$workflowScript = Join-Path $env:TEMP 'pleiades_asp_install_windows.ps1'
$workflowUrl = 'https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/install_windows.ps1'
$miniforgePath = Join-Path $HOME 'miniforge3'
$miniforgeConda = Join-Path $miniforgePath 'Scripts\conda.exe'

# Existing environment managers are supported by the standard installer.
$existingManager = @('micromamba.exe', 'mamba.exe', 'conda.exe', 'conda.bat') |
    ForEach-Object { Get-Command $_ -CommandType Application -ErrorAction SilentlyContinue } |
    Where-Object { $_ } | Select-Object -First 1
$knownConda = @(
    $miniforgeConda,
    (Join-Path $HOME 'miniconda3\Scripts\conda.exe'),
    (Join-Path $HOME 'anaconda3\Scripts\conda.exe'),
    (Join-Path $env:LOCALAPPDATA 'micromamba\micromamba.exe')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }

if (-not $existingManager -and -not $knownConda) {
    if (Test-Path -LiteralPath $miniforgePath) {
        throw "Directory already exists but Miniforge is not detected: $miniforgePath. Check that installation before proceeding."
    }

    Write-Host 'No Conda or Micromamba installation detected. Installing Miniforge for this user...'
    $miniforgeUrl = 'https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe'
    $download = Join-Path $env:TEMP ('Miniforge3-' + [Guid]::NewGuid().ToString('N') + '.exe')
    try {
        Invoke-WebRequest -Uri $miniforgeUrl -OutFile $download -UseBasicParsing
        # Miniforge uses an NSIS installer. The /D= installation path must be last.
        $arguments = @('/S', '/InstallationType=JustMe', '/RegisterPython=0', '/AddToPath=0', "/D=$miniforgePath")
        $process = Start-Process -FilePath $download -ArgumentList $arguments -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Miniforge installation failed (exit code $($process.ExitCode))." }
        if (-not (Test-Path -LiteralPath $miniforgeConda -PathType Leaf)) {
            throw "Miniforge installer finished but conda.exe was not found at: $miniforgeConda"
        }
    } finally {
        Remove-Item -LiteralPath $download -Force -ErrorAction SilentlyContinue
    }
    Write-Host 'Miniforge installed successfully.' -ForegroundColor Green
} else {
    Write-Host 'An existing environment manager was detected; no additional manager will be installed.'
}

Write-Host 'Downloading and running the standard ASP workflow installer...'
Invoke-WebRequest -Uri $workflowUrl -OutFile $workflowScript -UseBasicParsing
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $workflowScript
if ($LASTEXITCODE -ne 0) {
    throw "Pléiades ASP installation failed (exit code $LASTEXITCODE)."
}
Write-Host 'Pléiades ASP workflow setup completed.' -ForegroundColor Green
