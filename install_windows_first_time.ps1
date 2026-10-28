# WASA-DEM - first-time Windows setup
# The standard installer now handles both existing and missing environment managers.
# No system Python, Conda, or Git prerequisite; preserves existing environments.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'WASA-DEM requires 64-bit Windows.'
}

$uri = 'https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/install_windows.ps1'
$installer = Join-Path $env:TEMP ('wasa-dem-install-' + [Guid]::NewGuid().ToString('N').Substring(0, 8) + '.ps1')
try {
    Write-Host 'WASA-DEM: checking for a working Conda/Mamba/Micromamba installation...' -ForegroundColor Cyan
    Write-Host 'If none is usable, standalone Micromamba will be installed automatically.'
    Invoke-WebRequest -Uri $uri -OutFile $installer -UseBasicParsing
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) {
        throw "WASA-DEM installation failed (exit code $LASTEXITCODE)."
    }
    Write-Host 'WASA-DEM installation completed.' -ForegroundColor Green
} finally {
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
}
