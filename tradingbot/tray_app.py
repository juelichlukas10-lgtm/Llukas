"""System-Tray-App für den Trading-Bot (Windows).

Startet den Bot automatisch im Hintergrund – ohne sichtbares
Terminal-Fenster – und stellt über ein Tray-Icon Steuerung sowie
Dashboard-Zugriff bereit. Gedacht für den Einsatz per Windows-Autostart
(siehe ``scripts/install_autostart.ps1``).

Manueller Start (zu Testzwecken, mit Konsole):
    python -m tradingbot.tray_app

Silenter Start (wie im Autostart, ohne Konsolenfenster):
    pythonw -m tradingbot.tray_app
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
DASHBOARD_PORT = 8501
DASHBOARD_URL = f"http://localhost:{DASHBOARD_PORT}"
SCANNER_DASHBOARD_PORT = 8502

#: Beliebiger, unkritischer lokaler Port – dient nur als Instanz-Sperre.
_LOCK_PORT = 47932

_IS_WINDOWS = sys.platform == "win32"
_CREATE_NO_WINDOW = 0x08000000 if _IS_WINDOWS else 0
_POPEN_KWARGS: dict[str, object] = {"cwd": PROJECT_ROOT}
if _IS_WINDOWS:
    _POPEN_KWARGS["creationflags"] = _CREATE_NO_WINDOW


def _acquire_single_instance_lock() -> socket.socket | None:
    """Verhindert Mehrfachstarts (z. B. Autostart + manueller Doppelklick).

    Bindet einen lokalen Port als Mutex-Ersatz; das Betriebssystem gibt
    den Port automatisch frei, sobald der Prozess endet – kein Cleanup
    nötig. Das Socket-Objekt muss am Leben gehalten werden, sonst wird
    der Port sofort wieder freigegeben.

    Returns:
        Das gebundene Socket bei Erfolg, sonst None (bereits eine Instanz aktiv).
    """
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind(("127.0.0.1", _LOCK_PORT))
        lock_socket.listen(1)
        return lock_socket
    except OSError:
        return None


class TradingBotTray:
    """Verwaltet Bot- und Dashboard-Prozess über ein Tray-Icon."""

    def __init__(self) -> None:
        self.bot_process: subprocess.Popen | None = None
        self.dashboard_process: subprocess.Popen | None = None
        self.scanner_process: subprocess.Popen | None = None
        self.scanner_dashboard_process: subprocess.Popen | None = None
        self._stopping = False
        self.icon = pystray.Icon(
            "tradingbot",
            self._make_icon(running=False),
            "TradingBot – gestoppt",
            menu=self._build_menu(),
        )

    # ------------------------------------------------------------------
    # Icon & Menü
    # ------------------------------------------------------------------

    def _make_icon(self, running: bool) -> Image.Image:
        """Zeichnet ein einfaches Balkenchart-Icon (grün = aktiv, grau = gestoppt)."""
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bar_color = (79, 190, 135, 255) if running else (140, 148, 168, 255)
        draw.ellipse((3, 3, size - 3, size - 3), fill=(14, 18, 28, 255))
        for x0, y0, x1, y1 in ((17, 38, 25, 50), (29, 24, 37, 50), (41, 14, 49, 50)):
            draw.rounded_rectangle((x0, y0, x1, y1), radius=2, fill=bar_color)
        return img

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Dashboard öffnen", self.open_dashboard, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Bot starten", self.start_bot, enabled=lambda item: self.bot_process is None
            ),
            pystray.MenuItem(
                "Bot stoppen", self.stop_bot, enabled=lambda item: self.bot_process is not None
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Scanner starten", self.start_scanner,
                enabled=lambda item: self.scanner_process is None,
            ),
            pystray.MenuItem(
                "Scanner stoppen", self.stop_scanner,
                enabled=lambda item: self.scanner_process is not None,
            ),
            pystray.MenuItem("Scanner-Dashboard öffnen", self.open_scanner_dashboard),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Logs-Ordner öffnen", self.open_logs),
            pystray.MenuItem("Projektordner öffnen", self.open_project_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self.quit_app),
        )

    def _set_running_state(self, running: bool) -> None:
        self.icon.icon = self._make_icon(running)
        self.icon.title = "TradingBot – läuft" if running else "TradingBot – gestoppt"
        self.icon.update_menu()

    # ------------------------------------------------------------------
    # Bot-Prozess
    # ------------------------------------------------------------------

    def start_bot(self, icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
        """Startet ``main.py run`` als unsichtbaren Hintergrundprozess."""
        if self.bot_process is not None:
            return
        self.bot_process = subprocess.Popen([PYTHON, "main.py", "run"], **_POPEN_KWARGS)
        self._set_running_state(True)
        self._notify("Bot gestartet (Paper-Modus gemäß config.yaml)")
        threading.Thread(target=self._watch_bot, args=(self.bot_process,), daemon=True).start()

    def stop_bot(self, icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
        """Beendet den Bot-Prozess geordnet."""
        if self.bot_process is None:
            return
        self._terminate(self.bot_process)
        self.bot_process = None
        self._set_running_state(False)
        self._notify("Bot gestoppt")

    def _watch_bot(self, process: subprocess.Popen) -> None:
        """Meldet einen unerwarteten Absturz des Bot-Prozesses."""
        process.wait()
        if not self._stopping and self.bot_process is process:
            self.bot_process = None
            self._set_running_state(False)
            self._notify("Bot wurde unerwartet beendet – siehe logs/tradingbot.log")

    # ------------------------------------------------------------------
    # Scanner-Prozess (Buy-the-Dip-Marktscanner, unabhängig vom Bot)
    # ------------------------------------------------------------------

    def start_scanner(
        self, icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None
    ) -> None:
        """Startet den Marktscanner (``main.py scan``) im Hintergrund."""
        if self.scanner_process is not None:
            return
        self.scanner_process = subprocess.Popen([PYTHON, "main.py", "scan"], **_POPEN_KWARGS)
        self.icon.update_menu()
        self._notify("Buy-the-Dip-Scanner gestartet")
        threading.Thread(
            target=self._watch_scanner, args=(self.scanner_process,), daemon=True
        ).start()

    def stop_scanner(
        self, icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None
    ) -> None:
        """Beendet den Scanner-Prozess geordnet."""
        if self.scanner_process is None:
            return
        self._terminate(self.scanner_process)
        self.scanner_process = None
        self.icon.update_menu()
        self._notify("Scanner gestoppt")

    def _watch_scanner(self, process: subprocess.Popen) -> None:
        """Meldet einen unerwarteten Absturz des Scanner-Prozesses."""
        process.wait()
        if not self._stopping and self.scanner_process is process:
            self.scanner_process = None
            self.icon.update_menu()
            self._notify("Scanner wurde unerwartet beendet – siehe logs/tradingbot.log")

    def open_scanner_dashboard(
        self, icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None
    ) -> None:
        """Startet das Scanner-Dashboard bei Bedarf und öffnet es im Browser."""
        if self.scanner_dashboard_process is None or self.scanner_dashboard_process.poll() is not None:
            self.scanner_dashboard_process = subprocess.Popen(
                [
                    PYTHON, "-m", "streamlit", "run", "tradingbot/dashboard/scanner_app.py",
                    "--server.headless", "true", "--server.port", str(SCANNER_DASHBOARD_PORT),
                ],
                **_POPEN_KWARGS,
            )
            time.sleep(3)
        webbrowser.open(f"http://localhost:{SCANNER_DASHBOARD_PORT}")

    # ------------------------------------------------------------------
    # Dashboard-Prozess (lazy: startet erst bei Bedarf)
    # ------------------------------------------------------------------

    def _ensure_dashboard(self) -> None:
        if self.dashboard_process is not None and self.dashboard_process.poll() is None:
            return
        self.dashboard_process = subprocess.Popen(
            [
                PYTHON, "-m", "streamlit", "run", "tradingbot/dashboard/app.py",
                "--server.headless", "true", "--server.port", str(DASHBOARD_PORT),
            ],
            **_POPEN_KWARGS,
        )
        time.sleep(3)  # Streamlit braucht kurz, bevor der Server antwortet.

    def open_dashboard(self, icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
        """Startet das Dashboard bei Bedarf und öffnet es im Standardbrowser."""
        self._ensure_dashboard()
        webbrowser.open(DASHBOARD_URL)

    # ------------------------------------------------------------------
    # Sonstiges
    # ------------------------------------------------------------------

    def open_logs(self, icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
        subprocess.Popen(["explorer", str(PROJECT_ROOT / "logs")])

    def open_project_folder(self, icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
        subprocess.Popen(["explorer", str(PROJECT_ROOT)])

    def quit_app(self, icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
        """Beendet Bot, Scanner, Dashboards und die Tray-App selbst."""
        self._stopping = True
        self.stop_bot()
        self.stop_scanner()
        for attr in ("dashboard_process", "scanner_dashboard_process"):
            process = getattr(self, attr)
            if process is not None:
                self._terminate(process)
                setattr(self, attr, None)
        self.icon.stop()

    def _notify(self, message: str) -> None:
        """Zeigt eine Windows-Benachrichtigung (Fehler werden stumm ignoriert)."""
        try:
            self.icon.notify(message, "TradingBot")
        except Exception:
            pass

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        """Beendet einen Prozess geordnet, mit Kill-Fallback nach Timeout."""
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    def run(self) -> None:
        """Startet den Bot automatisch und blockiert in der Tray-Event-Loop."""
        self.start_bot()
        self.icon.run()


def main() -> None:
    """Einstiegspunkt: verhindert Mehrfachstarts und startet die Tray-App."""
    lock = _acquire_single_instance_lock()
    if lock is None:
        # Es läuft bereits eine Instanz – Dashboard öffnen statt zweiter Instanz.
        webbrowser.open(DASHBOARD_URL)
        return
    TradingBotTray().run()


if __name__ == "__main__":
    main()
