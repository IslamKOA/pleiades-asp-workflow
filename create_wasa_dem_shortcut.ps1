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
# Use the user-approved artwork from the repository as the Windows shortcut
# icon. Windows Shell needs ICO, so convert the JPG to a multi-resolution ICO
# using only System.Drawing in Windows PowerShell (no Python/Pillow required).
function Convert-WasaArtworkToIcon([string]$ImagePath, [string]$OutputPath) {
    Add-Type -AssemblyName System.Drawing
    $source = [System.Drawing.Image]::FromFile($ImagePath)
    try {
        $frames = New-Object System.Collections.ArrayList
        foreach ($pixels in @(16, 24, 32, 48, 64, 128, 256)) {
            $bitmap = New-Object System.Drawing.Bitmap -ArgumentList @($pixels, $pixels)
            try {
                $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
                try {
                    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
                    $graphics.DrawImage($source, 0, 0, $pixels, $pixels)
                } finally {
                    $graphics.Dispose()
                }
                $memory = New-Object System.IO.MemoryStream
                try {
                    $bitmap.Save($memory, [System.Drawing.Imaging.ImageFormat]::Png)
                    [void]$frames.Add(@{ Size = $pixels; Bytes = $memory.ToArray() })
                } finally {
                    $memory.Dispose()
                }
            } finally {
                $bitmap.Dispose()
            }
        }
        $stream = [System.IO.File]::Create($OutputPath)
        try {
            $writer = New-Object System.IO.BinaryWriter($stream)
            try {
                $writer.Write([UInt16]0)
                $writer.Write([UInt16]1)
                $writer.Write([UInt16]$frames.Count)
                $offset = 6 + 16 * $frames.Count
                foreach ($frame in $frames) {
                    $sizeByte = if ($frame.Size -eq 256) { 0 } else { $frame.Size }
                    $writer.Write([byte]$sizeByte)
                    $writer.Write([byte]$sizeByte)
                    $writer.Write([byte]0)
                    $writer.Write([byte]0)
                    $writer.Write([UInt16]1)
                    $writer.Write([UInt16]32)
                    $writer.Write([UInt32]$frame.Bytes.Length)
                    $writer.Write([UInt32]$offset)
                    $offset += $frame.Bytes.Length
                }
                foreach ($frame in $frames) { $writer.Write([byte[]]$frame.Bytes) }
            } finally { $writer.Dispose() }
        } finally { $stream.Dispose() }
    } finally { $source.Dispose() }
}

$repoArtwork = Join-Path $PSScriptRoot 'assets\WASA-DEM-approved-artwork.jpg'
$artworkUrl = 'https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/assets/WASA-DEM-approved-artwork.jpg'
$fallbackIco = Join-Path $PSScriptRoot 'assets\WASA-DEM.ico'
$fallbackIconUrl = 'https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/assets/WASA-DEM.ico'
$canonicalIcon = Join-Path $Workspace 'WASA-DEM.ico'
$sourceIcon = ''
$tempArt = ''
$tempIcon = ''
try {
    if ($IconPath) {
        if (-not (Test-Path -LiteralPath $IconPath -PathType Leaf)) {
            throw "Requested WASA-DEM icon not found: $IconPath"
        }
        $sourceIcon = [IO.Path]::GetFullPath($IconPath)
    } else {
        $artworkPath = $repoArtwork
        if (-not (Test-Path -LiteralPath $artworkPath -PathType Leaf)) {
            $tempArt = Join-Path $env:TEMP ('wasa-dem-artwork-' + [Guid]::NewGuid().ToString('N') + '.jpg')
            try {
                Invoke-WebRequest -Uri $artworkUrl -OutFile $tempArt -UseBasicParsing -ErrorAction Stop
                $artworkPath = $tempArt
            } catch {
                Write-Warning "Could not download approved artwork; trying the fallback icon."
                $artworkPath = ''
            }
        }
        if ($artworkPath) {
            $tempIcon = Join-Path $env:TEMP ('wasa-dem-rendered-' + [Guid]::NewGuid().ToString('N') + '.ico')
            try {
                Convert-WasaArtworkToIcon -ImagePath $artworkPath -OutputPath $tempIcon
                $sourceIcon = $tempIcon
                Write-Host 'Using the approved WASA-DEM artwork from GitHub.' -ForegroundColor Green
            } catch {
                Write-Warning ("Image-to-icon conversion failed; trying fallback ICO: " + $_.Exception.Message)
                $sourceIcon = ''
            }
        }
        if (-not $sourceIcon) {
            if (Test-Path -LiteralPath $fallbackIco -PathType Leaf) {
                $sourceIcon = $fallbackIco
            } else {
                $tempIcon = Join-Path $env:TEMP ('wasa-dem-fallback-' + [Guid]::NewGuid().ToString('N') + '.ico')
                try {
                    Invoke-WebRequest -Uri $fallbackIconUrl -OutFile $tempIcon -UseBasicParsing -ErrorAction Stop
                    $sourceIcon = $tempIcon
                } catch {
                    if (Test-Path -LiteralPath $canonicalIcon -PathType Leaf) {
                        $sourceIcon = $canonicalIcon
                        Write-Warning 'No connection to GitHub; reusing a previously saved icon.'
                    } else {
                        throw "Cannot download either WASA-DEM artwork or its fallback ICO."
                    }
                }
            }
        }
    }
    $magic = @(Get-Content -LiteralPath $sourceIcon -Encoding Byte -TotalCount 6 -ErrorAction Stop)
    if ((Get-Item -LiteralPath $sourceIcon).Length -lt 1024 -or $magic.Count -lt 6 -or
        $magic[0] -ne 0 -or $magic[1] -ne 0 -or $magic[2] -ne 1 -or $magic[3] -ne 0) {
        throw "Invalid Windows icon file: $sourceIcon"
    }
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
    Write-Host "WASA-DEM desktop icon applied: $versionedIcon" -ForegroundColor Green
} finally {
    foreach ($tmp in @($tempArt, $tempIcon)) {
        if ($tmp -and (Test-Path -LiteralPath $tmp -PathType Leaf)) {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        }
    }
}

# Refresh Explorer icon cache without closing/restarting Explorer.
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
    Write-Host 'If the icon is cached, right-click the Desktop and select Refresh (F5).' -ForegroundColor Yellow
}

Write-Host "Desktop shortcut ready: $shortcutPath" -ForegroundColor Green
Write-Host "It opens the existing notebook using $ManagerName and the $EnvironmentName environment."
