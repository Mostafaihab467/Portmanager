<#
    Creates Windows shortcuts for Port Manager: a Start Menu entry, a Desktop
    entry and an elevated ("Run as administrator") variant.

    If build_portable.bat has been run, the shortcuts start dist\PortManager\
    PortManager.exe; otherwise they start the script with pythonw.exe.

    Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File make_shortcut.ps1
            -NoDesktop        skip the Desktop shortcut
            -SkipAdmin        skip the elevated shortcut
#>
[CmdletBinding()]
param(
    [switch]$NoDesktop,
    [switch]$SkipAdmin
)

$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
$Script = Join-Path $ProjectDir 'port_manager.py'
$Icon = Join-Path $ProjectDir 'assets\icon.ico'
$BuiltExe = Join-Path $ProjectDir 'dist\PortManager\PortManager.exe'

function Test-Tkinter {
    param([string]$Python)
    & $Python -c "import tkinter" 2>$null
    return $LASTEXITCODE -eq 0
}

function Find-PythonW {
    # Prefer the interpreter that `run.bat` and `install_deps.bat` use, so the
    # shortcut and the pip-installed psutil always point at the same Python.
    $exes = @()
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) { $exes += $python.Source }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $out = (& $py.Source -3 -c "import sys; print(sys.executable)") 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) { $exes += $out.Trim() }
        } catch { }
    }
    foreach ($exe in $exes) {
        if (-not (Test-Path $exe)) { continue }
        $w = Join-Path (Split-Path $exe -Parent) 'pythonw.exe'
        if (-not (Test-Path $w)) { continue }
        if (-not (Test-Tkinter $exe)) {
            Write-Host "skipping $exe (no tkinter)"
            continue
        }
        return (Resolve-Path $w).Path
    }
    throw "Could not find a pythonw.exe with tkinter - install Python 3 with the Tk option (see install_deps.bat)."
}

# Only a real executable owns its process image, which is what Explorer,
# Alt-Tab and Task Manager read the icon from - pythonw.exe always shows Python.
if (Test-Path $BuiltExe) {
    $Target = (Resolve-Path $BuiltExe).Path
    $TargetArgs = ''
    $WorkDir = Split-Path $Target -Parent
    Write-Host "target: $Target"
} elseif (Test-Path $Script) {
    $Target = Find-PythonW
    $TargetArgs = "`"$Script`""
    $WorkDir = $ProjectDir
    Write-Host "target: $Target $TargetArgs"
    Write-Host "        (run build_portable.bat to get the app icon in Task Manager)"
} else {
    throw "Neither port_manager.py nor dist\PortManager\PortManager.exe was found."
}

$shell = New-Object -ComObject WScript.Shell

function New-AppShortcut {
    param(
        [string]$Path,
        [string]$Description,
        [switch]$Admin
    )
    $dir = Split-Path $Path -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $lnk = $shell.CreateShortcut($Path)
    $lnk.TargetPath = $Target
    $lnk.Arguments = $TargetArgs
    $lnk.WorkingDirectory = $WorkDir
    $lnk.IconLocation = if (Test-Path $Icon) { "$Icon,0" } else { "$Target,0" }
    $lnk.Description = $Description
    $lnk.Save()
    if ($Admin) {
        # Shortcut flag 0x20 at byte 21 = "Run as administrator".
        $bytes = [System.IO.File]::ReadAllBytes($Path)
        $bytes[21] = $bytes[21] -bor 0x20
        [System.IO.File]::WriteAllBytes($Path, $bytes)
    }
    Write-Host "created  $Path"
}

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
New-AppShortcut -Path (Join-Path $startMenu 'Port Manager.lnk') `
    -Description 'View, filter and kill processes by port'

if (-not $SkipAdmin) {
    New-AppShortcut -Path (Join-Path $startMenu 'Port Manager (Administrator).lnk') `
        -Description 'Port Manager started with administrator rights' `
        -Admin
}

if (-not $NoDesktop) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    New-AppShortcut -Path (Join-Path $desktop 'Port Manager.lnk') `
        -Description 'View, filter and kill processes by port'
}

Write-Host ""
Write-Host "Look for 'Port Manager' in Start, or pin it to the taskbar."
