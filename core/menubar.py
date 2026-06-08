"""
HIKARI v2.0 - macOS Menu Bar App
Lightweight system tray integration using rumps
"""

import os
import sys
import threading
import subprocess

try:
    import rumps

    RUMPS_AVAILABLE = True
except ImportError:
    RUMPS_AVAILABLE = False


class HIKARIMenuBar(rumps.App):
    """macOS menu bar app for HIKARI"""

    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator
        self.is_running = False
        self.is_muted = False

        menu_items = {
            "Start HIKARI": self.start_hikari,
            "Stop HIKARI": self.stop_hikari,
            None: None,
            "Text Mode": self.text_mode,
            "Server Only": self.server_mode,
            None: None,
            "Mute Voice": self.toggle_mute,
            "Status": self.show_status,
            None: None,
            "Quit": self.quit_app,
        }

        super().__init__(
            "HIKARI",
            title="HIKARI",
            menu=menu_items,
            quit_button=None,
        )

    @rumps.clicked("Start HIKARI")
    def start_hikari(self, _):
        if not self.is_running:
            self.is_running = True
            threading.Thread(target=self._run_voice_mode, daemon=True).start()
            rumps.notification("HIKARI", "Started", "HIKARI is now listening")

    @rumps.clicked("Stop HIKARI")
    def stop_hikari(self, _):
        self.is_running = False
        rumps.notification("HIKARI", "Stopped", "HIKARI has been stopped")

    @rumps.clicked("Text Mode")
    def text_mode(self, _):
        subprocess.Popen(
            [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "hikari.py"),
                "--text",
            ],
            cwd=os.path.dirname(__file__),
        )

    @rumps.clicked("Server Only")
    def server_mode(self, _):
        subprocess.Popen(
            [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "hikari.py"),
                "--server",
            ],
            cwd=os.path.dirname(__file__),
        )

    @rumps.clicked("Mute Voice")
    def toggle_mute(self, sender):
        self.is_muted = not self.is_muted
        sender.title = "Unmute Voice" if self.is_muted else "Mute Voice"

    @rumps.clicked("Status")
    def show_status(self, _):
        if self.orchestrator:
            status = self.orchestrator._get_status_report()
            rumps.notification("HIKARI Status", "", status[:200])

    @rumps.clicked("Quit")
    def quit_app(self, _):
        rumps.quit_application()

    def _run_voice_mode(self):
        if self.orchestrator:
            self.orchestrator.run_voice_loop()


def run_menu_bar(orchestrator=None):
    """Run the menu bar app"""
    if not RUMPS_AVAILABLE:
        print(
            "[MENUBAR] rumps not installed. Install with: pip install pyobjc-framework-Cocoa pyobjc-framework-UserNotifications rumps"
        )
        return

    app = HIKARIMenuBar(orchestrator)
    app.run()
