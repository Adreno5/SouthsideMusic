# Creates Launch.lnk in the current directory, pointing at launcher\Launch.exe.
#
# Nuitka standalone cannot resolve its runtime python DLL from another folder,
# and that DLL must not sit in the app root either (the app's free-threaded
# Python would load it through scipy -> ctypes and crash), so this shortcut is
# the root level entry point instead of a copy of the exe.
#
# Run it with the app root as the current directory: build.bat pushes into
# build.result\raw first, and an installed tree under Program Files gets its
# shortcuts from installer.iss instead.
#
# The paths stored in the .lnk have to be absolute. WScript.Shell resolves a
# relative target against the current directory while saving (a relative one
# was stored as C:\sub\cmd.exe) and never resolves a relative "Start in"
# against the shortcut, so relative values would break as soon as the tree
# moves or is installed somewhere else.

$ErrorActionPreference = 'Stop'

$rootPath = (Get-Location).Path
$exePath = Join-Path $rootPath 'launcher\Launch.exe'
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "Missing launcher: $exePath (run this from the app root)"
}

$linkPath = Join-Path $rootPath 'Launch.lnk'
# Drop an existing shortcut first: one made by hand has "Start in" set to
# launcher\, and that working directory must never survive a build.
if (Test-Path -LiteralPath $linkPath) {
    Remove-Item -LiteralPath $linkPath -Force
}
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = $exePath
# Start in = the folder holding the shortcut (the app root), never launcher\.
$link.WorkingDirectory = $rootPath
$link.IconLocation = "$exePath,0"
$link.Description = 'Southside Music'
$link.Save()

Write-Host "  Shortcut: $linkPath -> $exePath"
