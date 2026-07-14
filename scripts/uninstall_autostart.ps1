<#
.SYNOPSIS
    Entfernt den TradingBot-Autostart wieder (Gegenstueck zu
    install_autostart.ps1). Beendet keine bereits laufende Tray-App –
    dafuer im Tray-Icon-Menue "Beenden" waehlen.
#>

$ErrorActionPreference = "Stop"

$StartupFolder = [Environment]::GetFolderPath("Startup")
$StartupVbs = Join-Path $StartupFolder "TradingBot.vbs"
if (Test-Path $StartupVbs) {
    Remove-Item $StartupVbs -Force
    Write-Output "Autostart-Eintrag entfernt: $StartupVbs"
} else {
    Write-Output "Kein Autostart-Eintrag gefunden (bereits entfernt?)."
}

$DesktopVbs = Join-Path ([Environment]::GetFolderPath("Desktop")) "TradingBot starten.vbs"
if (Test-Path $DesktopVbs) {
    Remove-Item $DesktopVbs -Force
    Write-Output "Desktop-Starter entfernt: $DesktopVbs"
}

Write-Output ""
Write-Output "Hinweis: Eine bereits laufende Tray-App wird dadurch NICHT beendet."
Write-Output "Dazu im Tray-Icon-Menue (unten rechts in der Taskleiste) 'Beenden' waehlen."
