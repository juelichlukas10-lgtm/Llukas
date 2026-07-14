<#
.SYNOPSIS
    Richtet den TradingBot als Windows-Autostart ein (Tray-App, kein
    sichtbares Terminal-Fenster).

.DESCRIPTION
    Legt einen versteckten VBScript-Starter an, der die Tray-App
    (tradingbot/tray_app.py) via pythonw.exe ohne Konsolenfenster
    startet, und platziert ihn:
      - im Windows-Autostart-Ordner (startet automatisch bei Anmeldung)
      - zusaetzlich als Verknuepfung auf dem Desktop (manueller Start)

.NOTES
    Entfernen: scripts\uninstall_autostart.ps1 ausfuehren.
#>

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    Write-Error "python.exe wurde nicht im PATH gefunden. Bitte zuerst Python installieren/PATH pruefen."
    exit 1
}

$PythonwExe = Join-Path (Split-Path $PythonExe) "pythonw.exe"
if (-not (Test-Path $PythonwExe)) {
    Write-Error "pythonw.exe wurde nicht gefunden (erwartet neben python.exe: $PythonwExe)."
    exit 1
}

$TrayModule = "tradingbot.tray_app"

$VbsContent = @"
Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = "$ProjectRoot"
objShell.Run """$PythonwExe"" -m $TrayModule", 0, False
"@

$VbsPath = Join-Path $ProjectRoot "scripts\tradingbot_launcher.vbs"
Set-Content -Path $VbsPath -Value $VbsContent -Encoding ASCII

# Autostart-Eintrag (startet automatisch bei jeder Windows-Anmeldung).
$StartupFolder = [Environment]::GetFolderPath("Startup")
$StartupVbs = Join-Path $StartupFolder "TradingBot.vbs"
Copy-Item -Path $VbsPath -Destination $StartupVbs -Force

# Zusaetzlicher Starter auf dem Desktop (manueller Start ohne Warten auf Neustart).
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$DesktopVbs = Join-Path $DesktopPath "TradingBot starten.vbs"
Copy-Item -Path $VbsPath -Destination $DesktopVbs -Force

Write-Output "Fertig eingerichtet:"
Write-Output "  pythonw:        $PythonwExe"
Write-Output "  Autostart:      $StartupVbs"
Write-Output "  Desktop-Starter: $DesktopVbs"
Write-Output ""
Write-Output "Der Bot startet ab jetzt automatisch, sobald du dich bei Windows anmeldest."
Write-Output "Zum sofortigen Start jetzt: Doppelklick auf 'TradingBot starten.vbs' auf dem Desktop."
Write-Output "Zum Entfernen: scripts\uninstall_autostart.ps1 ausfuehren."
