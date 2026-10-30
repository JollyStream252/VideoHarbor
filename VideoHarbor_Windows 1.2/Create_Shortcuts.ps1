# Adds a normal Windows Start Menu shortcut and a desktop shortcut for VideoHarbor.
# For the proper taskbar icon/grouping, first build dist\VideoHarbor.exe.
$ErrorActionPreference = 'Stop'
$folder = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $folder 'dist\VideoHarbor.exe'
if (!(Test-Path $exe)) {
    Write-Host 'Build the standalone EXE first using Build_Exe_Windows.bat.'
    exit 1
}
$icon = Join-Path $folder 'assets\VideoHarbor.ico'
$locations = @(
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\VideoHarbor.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'VideoHarbor.lnk')
)
$shell = New-Object -ComObject WScript.Shell
foreach ($loc in $locations) {
    $shortcut = $shell.CreateShortcut($loc)
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = Split-Path $exe
    $shortcut.IconLocation = "$icon,0"
    $shortcut.Description = 'VideoHarbor 1.2 — yt-dlp video and playlist downloader'
    $shortcut.Save()
    Write-Host "Created: $loc"
}
Write-Host ''
Write-Host 'Launch VideoHarbor from Start. While it is open, right-click its taskbar icon and choose Pin to taskbar.'
