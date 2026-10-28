# WASA-DEM - Windows desktop shortcut setup
# Run independently on an existing installation. No ASP reinstallation or user-data deletion.
param(
    [string]$Workspace = (Join-Path $HOME 'Pleiades_ASP_Workflow'),
    [string]$ManagerExecutable = '',
    [string]$ManagerName = '',
    [string]$EnvironmentName = 'pleiades_asp',
    [string]$IconPath = ''
)
$ErrorActionPreference = 'Stop'

function Resolve-ManagerExecutable {
    $known = @(
        (Join-Path $env:LOCALAPPDATA 'micromamba\micromamba.exe'),
        (Join-Path $HOME 'micromamba\micromamba.exe'),
        (Join-Path $HOME 'micromamba\Library\bin\micromamba.exe')
    )
    # Prefer the same Micromamba executable as the normal installer, even if
    # a different Conda installation also appears on PATH.
    $micromambaCommand = Get-Command 'micromamba.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($micromambaCommand -and (Test-Path -LiteralPath $micromambaCommand.Source -PathType Leaf)) {
        return @{ Name = 'micromamba'; Exe = $micromambaCommand.Source }
    }
    foreach ($candidate in $known) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return @{ Name = 'micromamba'; Exe = $candidate }
        }
    }
    foreach ($candidate in @('mamba.exe', 'mamba.bat', 'conda.exe', 'conda.bat')) {
        $cmd = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd -and (Test-Path -LiteralPath $cmd.Source -PathType Leaf)) {
            return @{ Name = ($candidate -replace '\.(exe|bat)$', ''); Exe = $cmd.Source }
        }
    }
    foreach ($root in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:PROGRAMDATA)) {
        if ($root) {
            foreach ($folder in @('miniforge3', 'miniconda3', 'anaconda3', 'Miniforge3', 'Miniconda3', 'Anaconda3')) {
                $known += Join-Path $root "$folder\Scripts\conda.exe"
                $known += Join-Path $root "$folder\condabin\conda.bat"
            }
        }
    }
    foreach ($candidate in $known) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $name = if ($candidate -match 'micromamba') { 'micromamba' } else { 'conda' }
            return @{ Name = $name; Exe = $candidate }
        }
    }
    throw 'Micromamba, Mamba, or Conda was not found. Install WASA-DEM first, or provide -ManagerExecutable.'
}

$Workspace = [IO.Path]::GetFullPath($Workspace)
$notebook = Join-Path $Workspace 'Pleiades_ASP_Workflow.ipynb'
if (-not (Test-Path -LiteralPath $notebook -PathType Leaf)) {
    throw "The WASA-DEM notebook was not found at $notebook. Install the workflow first or pass -Workspace."
}
if (-not $ManagerExecutable) {
    $manager = Resolve-ManagerExecutable
    $ManagerExecutable = $manager.Exe
    $ManagerName = $manager.Name
} elseif (-not $ManagerName) {
    $ManagerName = if ($ManagerExecutable -match 'micromamba') { 'micromamba' } elseif ($ManagerExecutable -match 'mamba') { 'mamba' } else { 'conda' }
}
if (-not (Test-Path -LiteralPath $ManagerExecutable -PathType Leaf)) {
    throw "Environment manager executable not found: $ManagerExecutable"
}
$ManagerExecutable = [IO.Path]::GetFullPath($ManagerExecutable)

# First-time installs may set MAMBA_ROOT_PREFIX only in the installer process.
# Recover it when the shortcut is configured later in a new PowerShell session.
if ($ManagerName -eq 'micromamba' -and -not $env:MAMBA_ROOT_PREFIX) {
    foreach ($root in @((Join-Path $HOME 'micromamba'), (Join-Path $env:LOCALAPPDATA 'micromamba'))) {
        if (Test-Path -LiteralPath (Join-Path $root "envs\$EnvironmentName") -PathType Container) {
            $env:MAMBA_ROOT_PREFIX = $root
            break
        }
    }
}
$envList = & $ManagerExecutable env list --json
if ($LASTEXITCODE -ne 0) { throw "Could not inspect environments using $ManagerExecutable." }
$envInfo = ($envList | Out-String) | ConvertFrom-Json
$environmentMatches = @($envInfo.envs | Where-Object { (Split-Path -Path $_ -Leaf) -eq $EnvironmentName })
if ($environmentMatches.Count -eq 0) {
    throw "The $EnvironmentName environment was not found. Run install_windows.ps1 first."
}

# Explicit Micromamba root makes new desktop-launched PowerShell sessions reliable.
$mambaRoot = ''
if ($ManagerName -eq 'micromamba') {
    $environmentPath = [string]$environmentMatches[0]
    $parent = Split-Path -Path $environmentPath -Parent
    if ((Split-Path -Path $parent -Leaf) -eq 'envs') {
        $mambaRoot = Split-Path -Path $parent -Parent
    } elseif ($env:MAMBA_ROOT_PREFIX) {
        $mambaRoot = $env:MAMBA_ROOT_PREFIX
    }
}

$configPath = Join-Path $Workspace 'wasa_dem_launcher.json'
@{
    manager_executable = $ManagerExecutable
    manager_name = $ManagerName
    environment_name = $EnvironmentName
    notebook_path = $notebook
    mamba_root_prefix = $mambaRoot
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $configPath -Encoding UTF8

$launcherPath = Join-Path $Workspace 'Launch_WASA-DEM.ps1'
$launcherSource = @'
# WASA-DEM desktop launcher. Generated by create_wasa_dem_shortcut.ps1.
$ErrorActionPreference = 'Stop'
try {
    $config = Join-Path $PSScriptRoot 'wasa_dem_launcher.json'
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
        throw 'Launcher configuration is missing. Re-run create_wasa_dem_shortcut.ps1.'
    }
    $settings = Get-Content -LiteralPath $config -Raw -Encoding UTF8 | ConvertFrom-Json
    $exe = [string]$settings.manager_executable
    $notebook = [string]$settings.notebook_path
    $environment = [string]$settings.environment_name
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "Environment manager not found: $exe"
    }
    if (-not (Test-Path -LiteralPath $notebook -PathType Leaf)) {
        throw "Workflow notebook not found: $notebook"
    }
    if ($settings.manager_name -eq 'micromamba' -and $settings.mamba_root_prefix) {
        $env:MAMBA_ROOT_PREFIX = [string]$settings.mamba_root_prefix
    }
    Set-Location -LiteralPath (Split-Path -Path $notebook -Parent)
    Write-Host 'Starting WASA-DEM in JupyterLab...' -ForegroundColor Cyan
    & $exe run -n $environment jupyter lab $notebook
    if ($LASTEXITCODE -ne 0) {
        throw "JupyterLab exited with code $LASTEXITCODE."
    }
} catch {
    Write-Host "Unable to launch WASA-DEM: $($_.Exception.Message)" -ForegroundColor Red
    Read-Host 'Press Enter to close this window'
    exit 1
}
'@
Set-Content -LiteralPath $launcherPath -Value $launcherSource -Encoding UTF8

# Physical Desktop folder also supports OneDrive desktop redirection.
$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw "Windows Desktop folder could not be located. Run the launcher script manually: $launcherPath"
}
$shortcutPath = Join-Path $desktop 'WASA-DEM.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $launcherPath + '"'
$shortcut.WorkingDirectory = $Workspace
$shortcut.Description = 'WASA-DEM - optical satellite stereo DSM workflow'
$shortcut.WindowStyle = 1
# The official icon is distributed WITH the GitHub repository.
# Install/update: take the image from the downloaded ZIP (offline at this stage).
# Standalone shortcut setup: fetch the same image from GitHub's main branch.
# Explicit -IconPath is still supported as a manual override.
$repoIcon = Join-Path $PSScriptRoot 'assets\WASA-DEM.ico'
$iconUrl = 'https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/assets/WASA-DEM.ico'
$canonicalIcon = Join-Path $Workspace 'WASA-DEM.ico'
$sourceIcon = ''
$temporaryDownload = ''
try {
    if ($IconPath) {
        if (-not (Test-Path -LiteralPath $IconPath -PathType Leaf)) {
            throw "Requested WASA-DEM icon not found: $IconPath"
        }
        $sourceIcon = [IO.Path]::GetFullPath($IconPath)
    } elseif (Test-Path -LiteralPath $repoIcon -PathType Leaf) {
        $sourceIcon = $repoIcon
        Write-Host "Using WASA-DEM icon bundled with the GitHub repository."
    } else {
        # The script may have been downloaded alone rather than run from the
        # repository ZIP. Fetch its matching default icon automatically.
        $temporaryDownload = Join-Path $env:TEMP ('wasa-dem-icon-' + [Guid]::NewGuid().ToString('N') + '.ico')
        try {
            Invoke-WebRequest -Uri $iconUrl -OutFile $temporaryDownload -UseBasicParsing -ErrorAction Stop
            $sourceIcon = $temporaryDownload
            Write-Host "Downloaded official WASA-DEM icon from GitHub."
        } catch {
            if (Test-Path -LiteralPath $canonicalIcon -PathType Leaf) {
                $sourceIcon = $canonicalIcon
                Write-Warning "GitHub icon download failed; reusing the locally cached WASA-DEM icon."
            } else {
                throw ("Could not download the official WASA-DEM icon from GitHub: " + $_.Exception.Message)
            }
        }
    }
    $iconFile = Get-Item -LiteralPath $sourceIcon -ErrorAction Stop
    $magic = @(Get-Content -LiteralPath $sourceIcon -Encoding Byte -TotalCount 6 -ErrorAction Stop)
    if ($iconFile.Length -lt 1024 -or $magic.Count -lt 6 -or
        $magic[0] -ne 0 -or $magic[1] -ne 0 -or $magic[2] -ne 1 -or $magic[3] -ne 0) {
        throw "The downloaded file is not a valid Windows .ico image: $sourceIcon"
    }
    # A content-hashed filename ensures Windows does not keep showing an older
    # icon from Explorer's cache when the official GitHub image changes.
    $iconHash = (Get-FileHash -LiteralPath $sourceIcon -Algorithm SHA256).Hash.Substring(0, 12).ToLowerInvariant()
    $versionedIcon = Join-Path $Workspace ("WASA-DEM-" + $iconHash + ".ico")
    if (-not (Test-Path -LiteralPath $versionedIcon -PathType Leaf)) {
        Copy-Item -LiteralPath $sourceIcon -Destination $versionedIcon -Force
    }
    if (-not ([IO.Path]::GetFullPath($sourceIcon) -ieq [IO.Path]::GetFullPath($canonicalIcon))) {
        Copy-Item -LiteralPath $sourceIcon -Destination $canonicalIcon -Force
    }
    $shortcut.IconLocation = "$versionedIcon,0"
    $shortcut.Save()
    Write-Host "Official GitHub icon applied: $versionedIcon" -ForegroundColor Green
} finally {
    if ($temporaryDownload -and (Test-Path -LiteralPath $temporaryDownload -PathType Leaf)) {
        Remove-Item -LiteralPath $temporaryDownload -Force -ErrorAction SilentlyContinue
    }
}

# Ask Explorer to reload shortcut icons, without restarting the desktop shell.
try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WASADemIconRefresh {
    [DllImport("shell32.dll")]
    public static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item1, IntPtr item2);
}
'@ -ErrorAction Stop
    [WASADemIconRefresh]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
} catch {
    Write-Host 'If the icon is cached, right-click the Windows Desktop and select Refresh (F5).' -ForegroundColor Yellow
}

Write-Host "Desktop shortcut ready: $shortcutPath" -ForegroundColor Green
Write-Host "It opens the existing notebook using $ManagerName and the $EnvironmentName environment."
