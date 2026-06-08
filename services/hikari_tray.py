#!/usr/bin/env python3
"""
HIKARI v3 - System Tray Icon
Shows in menu bar, click for menu, always running in background
"""

import os
import sys
import threading
import time

# Add HIKARI to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import rumps
    RUMPS_AVAILABLE = True
except ImportError:
    RUMPS_AVAILABLE = False
    print("[TRAY] rumps not installed. Install with: pip install rumps")

class HIKARI_Tray:
    def __init__(self):
        if not RUMPS_AVAILABLE:
            return

        self.app = rumps.App("HIKARI", title="🔴", tooltip="HIKARI - Your AI Assistant")

        # Build menu
        menu_items = [
            rumps.MenuItem("HIKARI v3 - Online"),
            None,  # Separator
            rumps.MenuItem("Status", callback=self.show_status),
            rumps.MenuItem("Memory", callback=self.show_memory),
            None,
            rumps.MenuItem("Start Voice Mode", callback=self.start_voice),
            rumps.MenuItem("Restart Service", callback=self.restart),
            None,
            rumps.MenuItem("Quit HIKARI", callback=self.quit_app),
        ]

        self.app.menu = menu_items

        # Start HIKARI service in background
        self._hikari_thread = None
        self._start_hikari()

    def _start_hikari(self):
        """Start HIKARI service in background thread"""
        def run_hikari():
            from core.orchestrator import get_orchestrator
            self.orchestrator = get_orchestrator()
            print("[TRAY] HIKARI brain loaded")

        self._hikari_thread = threading.Thread(target=run_hikari, daemon=True)
        self._hikari_thread.start()
        time.sleep(2)  # Give time to load

    def show_status(self, sender):
        if hasattr(self, 'orchestrator'):
            status = self.orchestrator._get_status_report()
            rumps.alert(title="HIKARI Status", message=status[:200])
        else:
            rumps.alert(title="HIKARI", message="Still loading...")

    def show_memory(self, sender):
        if hasattr(self, 'orchestrator'):
            mem = self.orchestrator.memory.get_user_summary()
            msg = f"Conversations: {mem.get('total_conversations', 0)}\nFacts: {mem.get('facts_learned', 0)}"
            rumps.alert(title="HIKARI Memory", message=msg)

    def start_voice(self, sender):
        rumps.alert(title="HIKARI", message="Say 'Hey HIKARI' to activate voice mode!")

    def restart(self, sender):
        rumps.alert(title="HIKARI", message="Restarting service...")
        self._start_hikari()

    def quit_app(self, sender):
        rumps.quit()

    def run(self):
        """Run the tray app"""
        if RUMPS_AVAILABLE:
            self.app.run()
        else:
            print("[TRAY] System tray not available. Run with --daemon instead.")


if __name__ == "__main__":
    HIKARI_Tray().run()