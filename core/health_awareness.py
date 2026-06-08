"""
HIKARI v2.0 - Health Awareness System
Detects when user is sick, adjusts behavior, provides health support
"""

import json
import time
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

from core.runtime_paths import legacy_data_dir

DATA_DIR = legacy_data_dir()
HEALTH_FILE = DATA_DIR / "health_data.json"


class HealthAwareness:
    """Monitors and adapts to user's health state"""

    SICK_INDICATORS = [
        "sick",
        "ill",
        "fever",
        "cold",
        "flu",
        "cough",
        "sore throat",
        "headache",
        "tired",
        "exhausted",
        "weak",
        "dizzy",
        "nausea",
        "pain",
        "hurt",
        "unwell",
        "not feeling well",
        "dont feel well",
        "don't feel well",
        "aching",
        "chills",
        "congested",
        "stuffy nose",
        "runny nose",
        "sneezing",
        "fatigue",
        "body ache",
        "muscle pain",
    ]

    RECOVERY_INDICATORS = [
        "feeling better",
        "recovered",
        "back to normal",
        "much better",
        "all better",
        "good again",
        "improving",
        "getting better",
    ]

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.health_data: Dict[str, Any] = {}
        self.sick_episodes: List[Dict] = []
        self.current_episode: Optional[Dict] = None
        self._load()

    def _load(self):
        try:
            if HEALTH_FILE.exists():
                with open(HEALTH_FILE, "r") as f:
                    self.health_data = json.load(f)
                self.sick_episodes = self.health_data.get("sick_episodes", [])
                self.current_episode = self.health_data.get("current_episode")
        except Exception as e:
            print(f"[HEALTH] Load error: {e}")

    def _save(self):
        try:
            self.health_data["sick_episodes"] = self.sick_episodes
            self.health_data["current_episode"] = self.current_episode
            self.health_data["last_updated"] = datetime.now().isoformat()
            with open(HEALTH_FILE, "w") as f:
                json.dump(self.health_data, f, indent=2)
        except Exception as e:
            print(f"[HEALTH] Save error: {e}")

    def detect_health_state(
        self, text: str, voice_features: Dict = None
    ) -> Dict[str, Any]:
        """Detect user's health state from text and voice"""
        lower = text.lower()
        state = {
            "is_sick": False,
            "sick_type": None,
            "severity": 0.0,
            "is_recovering": False,
            "recommendations": [],
        }

        # Check for sick indicators
        sick_matches = [w for w in self.SICK_INDICATORS if w in lower]
        if sick_matches:
            state["is_sick"] = True
            state["severity"] = min(1.0, len(sick_matches) * 0.2)

            # Determine type of illness
            if any(w in lower for w in ["fever", "chills", "hot", "temperature"]):
                state["sick_type"] = "fever"
            elif any(
                w in lower for w in ["cough", "sore throat", "congested", "stuffy"]
            ):
                state["sick_type"] = "respiratory"
            elif any(w in lower for w in ["headache", "migraine"]):
                state["sick_type"] = "headache"
            elif any(w in lower for w in ["tired", "exhausted", "fatigue", "weak"]):
                state["sick_type"] = "fatigue"
            elif any(w in lower for w in ["nausea", "stomach", "vomit"]):
                state["sick_type"] = "stomach"
            else:
                state["sick_type"] = "general"

            # Add voice-based severity
            if voice_features:
                energy = voice_features.get("rms", 0.5)
                if energy < 0.1:
                    state["severity"] = min(1.0, state["severity"] + 0.3)

            # Generate recommendations
            state["recommendations"] = self._get_recommendations(state["sick_type"])

        # Check for recovery
        recovery_matches = [w for w in self.RECOVERY_INDICATORS if w in lower]
        if recovery_matches:
            state["is_recovering"] = True

        return state

    def start_sick_episode(self, sick_type: str, severity: float):
        """Start tracking a sick episode"""
        self.current_episode = {
            "started_at": datetime.now().isoformat(),
            "sick_type": sick_type,
            "initial_severity": severity,
            "severity_history": [
                {"time": datetime.now().isoformat(), "severity": severity}
            ],
            "check_ins": 0,
            "notes": [],
        }
        self._save()
        print(f"[HEALTH] Sick episode started: {sick_type} (severity: {severity:.2f})")

    def update_episode(self, severity: float, notes: str = ""):
        """Update current sick episode"""
        if not self.current_episode:
            return

        self.current_episode["severity_history"].append(
            {
                "time": datetime.now().isoformat(),
                "severity": severity,
            }
        )
        self.current_episode["check_ins"] += 1

        if notes:
            self.current_episode["notes"].append(
                {
                    "time": datetime.now().isoformat(),
                    "note": notes,
                }
            )

        # Check if recovered
        if severity < 0.2 and len(self.current_episode["severity_history"]) >= 3:
            recent = self.current_episode["severity_history"][-3:]
            if all(s["severity"] < 0.2 for s in recent):
                self.end_episode()

        self._save()

    def end_episode(self):
        """End current sick episode"""
        if self.current_episode:
            self.current_episode["ended_at"] = datetime.now().isoformat()
            self.current_episode["duration_hours"] = (
                datetime.fromisoformat(self.current_episode["ended_at"])
                - datetime.fromisoformat(self.current_episode["started_at"])
            ).total_seconds() / 3600

            self.sick_episodes.append(self.current_episode)
            self.current_episode = None
            self._save()
            print("[HEALTH] Sick episode ended - recovered!")

    def _get_recommendations(self, sick_type: str) -> List[str]:
        """Get health recommendations"""
        recommendations = {
            "fever": [
                "Rest and stay hydrated",
                "Take your temperature regularly",
                "Consider fever reducers if needed",
                "I'll keep things light for you",
            ],
            "respiratory": [
                "Drink warm fluids",
                "Rest your voice",
                "Use a humidifier if available",
                "I can handle tasks while you rest",
            ],
            "headache": [
                "Rest in a dark, quiet room",
                "Stay hydrated",
                "Consider pain relief if needed",
                "I'll keep responses brief",
            ],
            "fatigue": [
                "Get plenty of rest",
                "Don't push yourself too hard",
                "I can take care of things for you",
                "Let me know if you need anything",
            ],
            "stomach": [
                "Stick to bland foods",
                "Stay hydrated with small sips",
                "Rest as much as possible",
                "I'll keep things simple for you",
            ],
            "general": [
                "Rest and take it easy",
                "Stay hydrated",
                "Let me handle things for you",
                "Just say the codename if you need anything",
            ],
        }
        return recommendations.get(sick_type, recommendations["general"])

    def get_health_summary(self) -> Dict[str, Any]:
        """Get health summary"""
        summary = {
            "currently_sick": self.current_episode is not None,
            "total_episodes": len(self.sick_episodes),
            "current_episode": self.current_episode,
        }

        if self.sick_episodes:
            avg_duration = sum(
                e.get("duration_hours", 0) for e in self.sick_episodes
            ) / len(self.sick_episodes)
            summary["avg_episode_duration_hours"] = round(avg_duration, 1)
            summary["most_common_type"] = Counter(
                e.get("sick_type", "unknown") for e in self.sick_episodes
            ).most_common(1)[0]

        return summary

    def should_check_in(self) -> bool:
        """Decide if HIKARI should check on user's health"""
        if not self.current_episode:
            return False

        last_check = self.current_episode.get("check_ins", 0)
        started = datetime.fromisoformat(self.current_episode["started_at"])
        hours_since_start = (datetime.now() - started).total_seconds() / 3600

        # Check in every 4 hours during sick episode
        return hours_since_start > (last_check * 4)

    def get_check_in_message(self) -> str:
        """Get a health check-in message"""
        if not self.current_episode:
            return ""

        sick_type = self.current_episode.get("sick_type", "general")
        messages = {
            "fever": "How's your fever? Feeling any better?",
            "respiratory": "How's your cough/throat? Need anything?",
            "headache": "Is your headache any better?",
            "fatigue": "How's your energy level? Still feeling tired?",
            "stomach": "How's your stomach feeling?",
            "general": "How are you feeling? Any better than before?",
        }
        return messages.get(sick_type, messages["general"])
