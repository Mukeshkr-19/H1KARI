"""
HIKARI v2.0 - Proactive Alerts & Scheduling System
Scheduled tasks, periodic checks, smart notifications
"""

import os
import time
import json
import threading
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timedelta
from pathlib import Path

from core.quiet import debug
from core.runtime_paths import legacy_data_dir

DATA_DIR = legacy_data_dir()
SCHEDULES_FILE = DATA_DIR / "schedules.json"


class ScheduledTask:
    """A single scheduled task"""

    def __init__(
        self, name: str, interval_seconds: int, callback: Callable, enabled: bool = True
    ):
        self.name = name
        self.interval = interval_seconds
        self.callback = callback
        self.enabled = enabled
        self.last_run = 0
        self.run_count = 0
        self.next_run = time.time() + interval_seconds

    def should_run(self) -> bool:
        if not self.enabled:
            return False
        return time.time() >= self.next_run

    def run(self):
        try:
            result = self.callback()
            self.last_run = time.time()
            self.next_run = self.last_run + self.interval
            self.run_count += 1
            return result
        except Exception as e:
            debug(f"[SCHEDULER] Error in task '{self.name}': {e}")
            self.next_run = time.time() + 60  # Retry in 1 minute

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "interval": f"{self.interval}s",
            "enabled": self.enabled,
            "last_run": datetime.fromtimestamp(self.last_run).strftime("%H:%M:%S")
            if self.last_run
            else "Never",
            "run_count": self.run_count,
            "next_run_in": f"{max(0, int(self.next_run - time.time()))}s",
        }


class AlertRule:
    """Conditional alert rule"""

    def __init__(
        self,
        name: str,
        condition: Callable[[], bool],
        action: Callable[[], str],
        cooldown: int = 3600,
    ):
        self.name = name
        self.condition = condition
        self.action = action
        self.cooldown = cooldown
        self.last_triggered = 0
        self.enabled = True

    def check(self) -> Optional[str]:
        if not self.enabled:
            return None
        if time.time() - self.last_triggered < self.cooldown:
            return None
        try:
            if self.condition():
                result = self.action()
                self.last_triggered = time.time()
                return result
        except Exception as e:
            debug(f"[ALERT] Error in rule '{self.name}': {e}")
        return None


class Scheduler:
    """Manages scheduled tasks and alert rules"""

    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator
        self.tasks: List[ScheduledTask] = []
        self.alerts: List[AlertRule] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.notifications: List[Dict] = []
        self._load_schedules()

    def _load_schedules(self):
        try:
            if SCHEDULES_FILE.exists():
                with open(SCHEDULES_FILE, "r") as f:
                    data = json.load(f)
                # Restore enabled states
                saved_states = {
                    t["name"]: t.get("enabled", True) for t in data.get("tasks", [])
                }
                for task in self.tasks:
                    if task.name in saved_states:
                        task.enabled = saved_states[task.name]
        except Exception:
            pass

    def _save_schedules(self):
        try:
            data = {
                "tasks": [t.get_status() for t in self.tasks],
                "alerts": [a.name for a in self.alerts],
                "last_updated": datetime.now().isoformat(),
            }
            with open(SCHEDULES_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            debug(f"[SCHEDULER] Save error: {e}")

    def add_task(
        self, name: str, interval_seconds: int, callback: Callable
    ) -> ScheduledTask:
        """Add a recurring task"""
        task = ScheduledTask(name, interval_seconds, callback)
        self.tasks.append(task)
        debug(f"[SCHEDULER] Added task: {name} (every {interval_seconds}s)")
        return task

    def add_alert(
        self,
        name: str,
        condition: Callable[[], bool],
        action: Callable[[], str],
        cooldown: int = 3600,
    ):
        """Add a conditional alert rule"""
        alert = AlertRule(name, condition, action, cooldown)
        self.alerts.append(alert)
        debug(f"[SCHEDULER] Added alert: {name}")

    def start(self):
        """Start the scheduler loop"""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        debug("[SCHEDULER] Started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        debug("[SCHEDULER] Stopped")

    def _loop(self):
        while self._running:
            try:
                # Run scheduled tasks
                for task in self.tasks:
                    if task.should_run():
                        result = task.run()
                        if result:
                            self._add_notification("scheduled", task.name, str(result))

                # Check alert rules
                for alert in self.alerts:
                    result = alert.check()
                    if result:
                        self._add_notification("alert", alert.name, result)

                self._save_schedules()
                time.sleep(1)

            except Exception as e:
                debug(f"[SCHEDULER] Loop error: {e}")
                time.sleep(5)

    def _add_notification(self, type: str, source: str, message: str):
        self.notifications.append(
            {
                "type": type,
                "source": source,
                "message": message,
                "time": datetime.now().isoformat(),
                "read": False,
            }
        )
        if len(self.notifications) > 100:
            self.notifications = self.notifications[-100:]

        # Push to connected devices
        if self.orchestrator and self.orchestrator.ws_server:
            self.orchestrator.ws_server.broadcast(
                {
                    "type": "notification",
                    "source": source,
                    "message": message,
                }
            )

    def get_notifications(self, unread_only: bool = False) -> List[Dict]:
        if unread_only:
            return [n for n in self.notifications if not n["read"]]
        return self.notifications[-20:]

    def mark_all_read(self):
        for n in self.notifications:
            n["read"] = True

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "tasks": [t.get_status() for t in self.tasks],
            "alerts": len(self.alerts),
            "notifications": len(self.notifications),
            "unread": sum(1 for n in self.notifications if not n["read"]),
        }


def setup_default_scheduler(orchestrator) -> Scheduler:
    """Set up default scheduled tasks for HIKARI"""
    scheduler = Scheduler(orchestrator)
    eager_legacy_brain = not getattr(orchestrator, "brain_v2_enabled", False)

    # Weather check every 30 minutes
    def weather_check():
        from agents.research import ResearchAgent

        agent = ResearchAgent(eager_legacy_brain=eager_legacy_brain)
        location = orchestrator.memory.get_preference("location", "")
        if location:
            return agent.get_weather(location)
        return None

    scheduler.add_task("weather_check", 1800, weather_check)

    # News check every hour
    def news_check():
        from agents.research import ResearchAgent

        agent = ResearchAgent(eager_legacy_brain=eager_legacy_brain)
        return agent.get_news()[:200]  # First 200 chars

    scheduler.add_task("news_check", 3600, news_check)

    # Morning briefing at 7 AM
    def morning_check():
        now = datetime.now()
        if now.hour == 7 and now.minute < 5:
            from agents.research import ResearchAgent

            agent = ResearchAgent(eager_legacy_brain=eager_legacy_brain)
            return agent.get_morning_briefing()
        return None

    scheduler.add_task("morning_briefing", 300, morning_check)

    # Alert: severe weather
    def severe_weather_alert():
        from agents.research import ResearchAgent

        agent = ResearchAgent(eager_legacy_brain=eager_legacy_brain)
        location = orchestrator.memory.get_preference("location", "")
        if location:
            weather = agent.get_weather(location)
            if any(
                w in weather.lower()
                for w in ["storm", "hurricane", "tornado", "severe"]
            ):
                return f"Severe weather alert: {weather}"
        return None

    scheduler.add_alert(
        "severe_weather", lambda: True, severe_weather_alert, cooldown=7200
    )

    return scheduler
