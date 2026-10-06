# SPDX-License-Identifier: GPL-3.0-or-later
# Build both Windows artefacts from a clean checkout.
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#
# Produces, in dist\:
#   Emdee\                        the unpacked application
#   Emdee-<version>-windows-x64.zip   the portable archive
#   Emdee-<version>-setup.exe     the installer
#
# The same script runs locally and in CI, so what gets released is what was
# tested rather than a second recipe that drifts from the first.

[CmdletBinding()]
param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
Push-Location $repo
try {
    if (-not (Test-Path $Python)) { $Python = "python" }

    $version = (& $Python -c "import app; print(app.APP_VERSION)").Trim()
    if (-not $version) { throw "could not read the version from the app package" }
    Write-Host "Building Emdee $version" -ForegroundColor Cyan

    # 1. Icons. The .ico is compiled into the executable and used by the
    #    installer, the shortcut and the file association, so it has to exist
    #    before PyInstaller runs. Only the .ico: regenerating the freedesktop
    #    hicolor tree here would rewrite Linux's committed icons from a Windows
    #    machine, for no change anyone can see.
    Write-Host "`n[1/4] icons" -ForegroundColor Cyan
    & $Python tools\build_icons.py --only ico
    if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

    # 2. The application folder. The spec verifies its own output and fails the
    #    build if the WebEngine payload did not make it in.
    Write-Host "`n[2/4] PyInstaller" -ForegroundColor Cyan
    Remove-Item -Recurse -Force build, dist\Emdee -ErrorAction SilentlyContinue
    & $Python -m PyInstaller packaging\windows\emdee.spec --noconfirm --distpath dist --workpath build
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    # 3. Portable archive.
    Write-Host "`n[3/4] portable archive" -ForegroundColor Cyan
    $zip = "dist\Emdee-$version-windows-x64.zip"
    Remove-Item $zip -ErrorAction SilentlyContinue
    Compress-Archive -Path "dist\Emdee\*" -DestinationPath $zip -CompressionLevel Optimal
    "  $zip  ({0:N0} MB)" -f ((Get-Item $zip).Length / 1MB) | Write-Host

    # 4. Installer.
    if ($SkipInstaller) {
        Write-Host "`n[4/4] installer skipped" -ForegroundColor Yellow
    } else {
        Write-Host "`n[4/4] installer" -ForegroundColor Cyan
        $iscc = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $iscc) {
            throw "Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup"
        }
        & $iscc "packaging\windows\emdee.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    }

    Write-Host "`nArtefacts:" -ForegroundColor Green
    Get-ChildItem dist -File | ForEach-Object {
        "  {0,-42} {1,8:N0} MB" -f $_.Name, ($_.Length / 1MB) | Write-Host
    }
}
finally {
    Pop-Location
}
