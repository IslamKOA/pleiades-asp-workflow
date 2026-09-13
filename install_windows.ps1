# Pléiades ASP Workflow - Windows bootstrap installer
# --------------------------------------------------

$ErrorActionPreference = "Stop"


# ==================================================
# Configuration
# ==================================================

# Default ASP version.
# The user can override it before running this script:
#
# $env:PLEIADES_ASP_VERSION = "3.4.0"
#
$AspVersion = if ($env:PLEIADES_ASP_VERSION) {
    $env:PLEIADES_ASP_VERSION.Trim()
} else {
    "3.3.0"
}


# ==================================================
# Helper functions
# ==================================================

function Test-IsAdministrator {

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()

    $principal = New-Object Security.Principal.WindowsPrincipal($identity)

    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}


function Get-InstalledWslDistros {

    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        return @()
    }

    $raw = & wsl.exe --list --quiet 2>$null
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        return @()
    }

    $distros = @()

    foreach ($line in $raw) {

        # Some Windows/WSL configurations return NUL characters.
        $name = ($line -replace "`0", "").Trim()

        if ($name) {
            $distros += $name
        }
    }

    return $distros
}


function Get-UsableWslDistro {

    $installed = @(Get-InstalledWslDistros)

    if ($installed.Count -eq 0) {
        return $null
    }

    # --------------------------------------------------
    # Explicit user override
    # --------------------------------------------------

    if ($env:PLEIADES_ASP_WSL_DISTRO) {

        $requested = $env:PLEIADES_ASP_WSL_DISTRO.Trim()

        if ($installed -contains $requested) {

            if (-not $requested.ToLower().StartsWith("docker-desktop")) {
                return $requested
            }
        }
    }


    # --------------------------------------------------
    # Remove Docker internal WSL distributions
    # --------------------------------------------------

    $usable = @(
        $installed | Where-Object {
            -not $_.ToLower().StartsWith("docker-desktop")
        }
    )

    if ($usable.Count -eq 0) {
        return $null
    }


    # --------------------------------------------------
    # Prefer Ubuntu
    # --------------------------------------------------

    foreach ($distro in $usable) {

        if ($distro.ToLower().StartsWith("ubuntu")) {
            return $distro
        }
    }


    # --------------------------------------------------
    # Prefer Debian if Ubuntu is unavailable
    # --------------------------------------------------

    foreach ($distro in $usable) {

        if ($distro.ToLower().StartsWith("debian")) {
            return $distro
        }
    }


    # Otherwise use the first real Linux distribution.
    return $usable[0]
}


function Install-UbuntuWsl {

    Write-Host ""
    Write-Host "No usable Linux WSL distribution was found."
    Write-Host "Installing Ubuntu automatically..."
    Write-Host ""

    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {

        throw @"
WSL is not available on this Windows installation.

Pléiades ASP Workflow requires WSL on Windows because
Ames Stereo Pipeline is executed using the official Linux distribution.

Windows 10 version 2004 or newer, or Windows 11, is required.
"@
    }


    # --------------------------------------------------
    # Install Ubuntu
    # --------------------------------------------------

    if (Test-IsAdministrator) {

        & wsl.exe --install -d Ubuntu
        $installExitCode = $LASTEXITCODE

    } else {

        Write-Host "Windows administrator approval may be requested."

        $process = Start-Process `
            -FilePath "wsl.exe" `
            -ArgumentList "--install", "-d", "Ubuntu" `
            -Verb RunAs `
            -Wait `
            -PassThru

        $installExitCode = $process.ExitCode
    }


    # --------------------------------------------------
    # Retry using web-download if necessary
    # --------------------------------------------------

    if ($installExitCode -ne 0) {

        Write-Host ""
        Write-Host "Standard WSL installation did not complete."
        Write-Host "Retrying Ubuntu installation using web download..."
        Write-Host ""

        if (Test-IsAdministrator) {

            & wsl.exe --install --web-download -d Ubuntu
            $installExitCode = $LASTEXITCODE

        } else {

            $process = Start-Process `
                -FilePath "wsl.exe" `
                -ArgumentList "--install", "--web-download", "-d", "Ubuntu" `
                -Verb RunAs `
                -Wait `
                -PassThru

            $installExitCode = $process.ExitCode
        }
    }


    Start-Sleep -Seconds 3


    # --------------------------------------------------
    # Check whether Ubuntu became available
    # --------------------------------------------------

    $distro = Get-UsableWslDistro

    if (-not $distro) {

        throw @"
WSL/Ubuntu installation has been started, but Windows has not
made the Linux distribution available yet.

A Windows restart may be required.

Restart Windows if requested, complete the first Ubuntu setup
if Windows asks for a Linux username/password, activate your
Conda environment again, and rerun the SAME installer command.

No manual ASP installation is required.
"@
    }

    return $distro
}


function Test-Bzip2Archive {

    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $validator = @'
import bz2
import sys

path = sys.argv[1]

try:
    with bz2.open(path, "rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
except Exception as exc:
    print(f"Invalid bzip2 archive: {exc}", file=sys.stderr)
    raise SystemExit(1)

raise SystemExit(0)
'@

    & python -c $validator $Path

    return ($LASTEXITCODE -eq 0)
}


# ==================================================
# Installer header
# ==================================================

Write-Host ""
Write-Host "=============================================="
Write-Host " Pléiades ASP Workflow - Windows Installer"
Write-Host "=============================================="
Write-Host ""
Write-Host "Requested ASP version: $AspVersion"
Write-Host ""


# ==================================================
# 1. Check Python
# ==================================================

Write-Host "[1/6] Checking Python..."

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {

    Write-Host ""
    Write-Host "[ERROR] Python was not found."
    Write-Host "Please install/activate Python or your Conda environment first."

    exit 1
}

python --version

if ($LASTEXITCODE -ne 0) {
    throw "Python check failed."
}


# ==================================================
# 2. Check Git
# ==================================================

Write-Host ""
Write-Host "[2/6] Checking Git..."

$gitCommand = Get-Command git -ErrorAction SilentlyContinue

if (-not $gitCommand) {

    Write-Host "Git was not found."
    Write-Host "Downloading PortableGit for Windows..."

    $gitRoot = Join-Path $env:LOCALAPPDATA "PleiadesASP\Git"
    $gitExe  = Join-Path $gitRoot "cmd\git.exe"

    if (-not (Test-Path $gitExe)) {

        New-Item `
            -ItemType Directory `
            -Force `
            -Path $gitRoot |
            Out-Null


        # Query official Git for Windows GitHub releases.
        $release = Invoke-RestMethod `
            -Uri "https://api.github.com/repos/git-for-windows/git/releases/latest"


        $asset = $release.assets |
            Where-Object {
                $_.name -match "^PortableGit-.*-64-bit\.7z\.exe$"
            } |
            Select-Object -First 1


        if (-not $asset) {
            throw "Could not locate the official 64-bit PortableGit package."
        }


        $installer = Join-Path $env:TEMP $asset.name

        Write-Host "Downloading $($asset.name)..."


        Invoke-WebRequest `
            -Uri $asset.browser_download_url `
            -OutFile $installer


        Write-Host "Extracting PortableGit..."


        Start-Process `
            -FilePath $installer `
            -ArgumentList "-y", "-o$gitRoot" `
            -Wait


        Remove-Item `
            $installer `
            -Force `
            -ErrorAction SilentlyContinue
    }


    if (-not (Test-Path $gitExe)) {
        throw "PortableGit installation failed."
    }


    # Make Git available in the current PowerShell session.
    $env:PATH = "$(Join-Path $gitRoot 'cmd');$env:PATH"

    Write-Host "PortableGit installed successfully."
}


git --version

if ($LASTEXITCODE -ne 0) {
    throw "Git check failed."
}


# ==================================================
# 3. Install Pléiades ASP Workflow
# ==================================================

Write-Host ""
Write-Host "[3/6] Installing Pléiades ASP Workflow..."


python -m pip install --upgrade `
    "git+https://github.com/IslamKOA/pleiades-asp-workflow.git"


if ($LASTEXITCODE -ne 0) {
    throw "Pléiades ASP Workflow installation failed."
}


# --------------------------------------------------
# Windows RPC support
# --------------------------------------------------

Write-Host ""
Write-Host "Installing Windows RPC support..."


python -m pip install --upgrade geojson

if ($LASTEXITCODE -ne 0) {
    throw "geojson installation failed."
}


python -m pip install --no-deps "rpcm==1.4.10"

if ($LASTEXITCODE -ne 0) {
    throw "rpcm installation failed."
}


# ==================================================
# 4. Prepare WSL
# ==================================================

Write-Host ""
Write-Host "[4/6] Checking Windows Subsystem for Linux..."


$WslDistro = Get-UsableWslDistro


if (-not $WslDistro) {

    $WslDistro = Install-UbuntuWsl
}


if (-not $WslDistro) {
    throw "No usable WSL Linux distribution is available."
}


Write-Host "Using WSL distribution: $WslDistro"


# Make the selected Linux distribution available to
# pleiades-asp-runner in the current process.
$env:PLEIADES_ASP_WSL_DISTRO = $WslDistro


# Remember it for future PowerShell/Jupyter sessions.
[Environment]::SetEnvironmentVariable(
    "PLEIADES_ASP_WSL_DISTRO",
    $WslDistro,
    "User"
)


# --------------------------------------------------
# Verify the selected WSL distribution
# --------------------------------------------------

Write-Host "Testing WSL Linux environment..."


& wsl.exe `
    -d $WslDistro `
    -- `
    sh -lc 'printf "%s" "$HOME"'


if ($LASTEXITCODE -ne 0) {

    throw @"
The Linux distribution '$WslDistro' is installed but could not
be initialized.

Launch the distribution once if Windows requests initial Linux
user setup, then rerun this installer.
"@
}


Write-Host ""
Write-Host "WSL Linux environment is ready."


# ==================================================
# 5. Install Ames Stereo Pipeline
# ==================================================

Write-Host ""
Write-Host "[5/6] Preparing Ames Stereo Pipeline $AspVersion..."


# --------------------------------------------------
# Validate previously downloaded ASP archives
# --------------------------------------------------

$AspDownloadDir = Join-Path `
    $HOME `
    ".pleiades-asp-runner\downloads\$AspVersion"


if (Test-Path $AspDownloadDir) {

    $cachedArchives = @(
        Get-ChildItem `
            -Path $AspDownloadDir `
            -Filter "*.tar.bz2" `
            -File `
            -ErrorAction SilentlyContinue
    )


    foreach ($archive in $cachedArchives) {

        Write-Host ""
        Write-Host "Checking cached ASP archive:"
        Write-Host "  $($archive.FullName)"


        $validArchive = Test-Bzip2Archive `
            -Path $archive.FullName


        if (-not $validArchive) {

            Write-Host ""
            Write-Host "Cached ASP archive is incomplete or corrupted."
            Write-Host "Removing it so a clean copy will be downloaded..."

            Remove-Item `
                $archive.FullName `
                -Force

        } else {

            Write-Host "Cached ASP archive is valid."
        }
    }
}


# --------------------------------------------------
# Install ASP
# --------------------------------------------------

Write-Host ""
Write-Host "Installing Ames Stereo Pipeline $AspVersion..."


asp-install $AspVersion


if ($LASTEXITCODE -ne 0) {
    throw "ASP $AspVersion installation failed."
}


# ==================================================
# 6. Initialize workflow
# ==================================================

Write-Host ""
Write-Host "[6/6] Initializing Pléiades ASP Workflow..."


$WorkflowDir = Join-Path `
    $HOME `
    "Pleiades_ASP_Workflow"


$WorkflowNotebook = Join-Path `
    $WorkflowDir `
    "Pleiades_ASP_Workflow.ipynb"


# Do not overwrite an existing initialized workspace.
if (Test-Path $WorkflowNotebook) {

    Write-Host ""
    Write-Host "Workflow workspace already exists:"
    Write-Host "  $WorkflowDir"
    Write-Host ""
    Write-Host "Existing workspace will be kept unchanged."

} else {

    pleiades-workflow-init

    if ($LASTEXITCODE -ne 0) {
        throw "Workflow initialization failed."
    }
}


# ==================================================
# Finished
# ==================================================

Write-Host ""
Write-Host "=============================================="
Write-Host " Installation completed successfully"
Write-Host "=============================================="
Write-Host ""
Write-Host "ASP version : $AspVersion"
Write-Host "WSL distro  : $WslDistro"
Write-Host ""
Write-Host "Workflow directory:"
Write-Host "  $WorkflowDir"
Write-Host ""
Write-Host "Launch the workflow with:"
Write-Host ""
Write-Host '  jupyter lab "$HOME/Pleiades_ASP_Workflow/Pleiades_ASP_Workflow.ipynb"'
Write-Host ""
