# PowerShell setup script — should be converted to setup.sh
param(
    [string]$InstallPath = "/opt/dgx-remote"
)

Write-Host "Installing to $InstallPath"
$env:DGX_HOME = $InstallPath
Set-Location $PSScriptRoot

New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null
Write-Host "Setup complete."
