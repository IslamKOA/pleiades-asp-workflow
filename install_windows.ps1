# Pléiades ASP Workflow - Windows bootstrap installer
# --------------------------------------------------

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=============================================="
Write-Host " Pléiades ASP Workflow - Windows Installer"
Write-Host "=============================================="
Write-Host ""

# --------------------------------------------------
# 1. Check Python
# --------------------------------------------------

Write-Host "[1/3] Checking Python..."

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


# --------------------------------------------------
# 2. Check Git
# --------------------------------------------------

Write-Host ""
Write-Host "[2/3] Checking Git..."

$gitCommand = Get-Command git -ErrorAction SilentlyContinue

if (-not $gitCommand) {

    Write-Host "Git was not found."
    Write-Host "Downloading a portable Git for Windows..."

    $gitRoot = Join-Path $env:LOCALAPPDATA "PleiadesASP\Git"
    $gitExe  = Join-Path $gitRoot "cmd\git.exe"

    if (-not (Test-Path $gitExe)) {

        New-Item -ItemType Directory -Force -Path $gitRoot | Out-Null

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

        Write-Host "Extracting portable Git..."

        Start-Process `
            -FilePath $installer `
            -ArgumentList "-y", "-o$gitRoot" `
            -Wait

        Remove-Item $installer -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path $gitExe)) {
        throw "Portable Git installation failed."
    }

    $env:PATH = "$(Join-Path $gitRoot 'cmd');$env:PATH"

    Write-Host "Git installed successfully."
}

git --version

if ($LASTEXITCODE -ne 0) {
    throw "Git check failed."
}


# --------------------------------------------------
# 3. Install Pléiades ASP Workflow
# --------------------------------------------------

Write-Host ""
Write-Host "[3/3] Installing Pléiades ASP Workflow..."

python -m pip install --upgrade `
    "git+https://github.com/IslamKOA/pleiades-asp-workflow.git"

if ($LASTEXITCODE -ne 0) {
    throw "Pléiades ASP Workflow installation failed."
}

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

Write-Host ""
Write-Host "=============================================="
Write-Host " Python package installation completed"
Write-Host "=============================================="
Write-Host ""
Write-Host "Next run:"
Write-Host "  asp-install 3.3.0"
Write-Host "  pleiades-workflow-init"
Write-Host ""
