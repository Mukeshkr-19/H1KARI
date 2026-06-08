"""
HIKARI v2.0 - Proactive Intelligence System
Anticipates needs, suggests actions, learns daily patterns
Like JARVIS knowing what Tony needs before he asks
"""

import os
import json
import time
import random
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict, Counter

from core.runtime_paths import legacy_data_dir

DATA_DIR = legacy_data_dir()
INTELLIGENCE_FILE = DATA_DIR / "proactive_intelligence.json"


class ProactiveIntelligence:
    """The brain that anticipates needs"""

    def __init__(self, user_profile=None, memory=None, scheduler=None):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.user_profile = user_profile
        self.memory = memory
        self.scheduler = scheduler
        self.intelligence_data: Dict[str, Any] = {}
        self.predictions: List[Dict] = []
        self.suggestions: List[Dict] = []
        self._load()

    def _load(self):
        try:
            if INTELLIGENCE_FILE.exists():
                with open(INTELLIGENCE_FILE, "r") as f:
                    self.intelligence_data = json.load(f)
        except Exception as e:
            print(f"[PROACTIVE] Load error: {e}")

    def _save(self):
        try:
            with open(INTELLIGENCE_FILE, "w") as f:
                json.dump(self.intelligence_data, f, indent=2)
        except Exception as e:
            print(f"[PROACTIVE] Save error: {e}")

    def analyze_and_predict(self) -> List[Dict]:
        """Analyze patterns and generate predictions"""
        predictions = []

        # Time-based predictions
        predictions.extend(self._time_based_predictions())

        # Pattern-based predictions
        predictions.extend(self._pattern_based_predictions())

        # Context-based predictions
        predictions.extend(self._context_based_predictions())

        # Health-based predictions
        predictions.extend(self._health_based_predictions())

        # Store predictions
        self.predictions = predictions
        return predictions

    def _time_based_predictions(self) -> List[Dict]:
        """Predict based on time of day"""
        now = datetime.now()
        hour = now.hour
        day = now.strftime("%A")
        predictions = []

        # Morning (6-9 AM)
        if 6 <= hour <= 9:
            predictions.append(
                {
                    "type": "morning_routine",
                    "priority": 0.9,
                    "message": self._generate_morning_greeting(),
                    "actions": ["weather", "calendar", "news", "reminders"],
                }
            )

        # Mid-morning (9-11 AM)
        if 9 <= hour <= 11:
            predictions.append(
                {
                    "type": "productivity_check",
                    "priority": 0.5,
                    "message": "Good morning! Ready to tackle today's tasks?",
                    "actions": ["todo", "calendar"],
                }
            )

        # Lunch time (11:30 AM - 1:30 PM)
        if 11.5 <= hour <= 13.5:
            predictions.append(
                {
                    "type": "lunch_reminder",
                    "priority": 0.6,
                    "message": "It's lunch time! Don't forget to eat.",
                    "actions": ["food_suggestions"],
                }
            )

        # Afternoon (2-4 PM)
        if 14 <= hour <= 16:
            predictions.append(
                {
                    "type": "afternoon_check",
                    "priority": 0.4,
                    "message": "How's your day going? Need anything?",
                    "actions": ["status_check"],
                }
            )

        # Evening (5-7 PM)
        if 17 <= hour <= 19:
            predictions.append(
                {
                    "type": "evening_wrap",
                    "priority": 0.6,
                    "message": "Evening! Want a summary of your day?",
                    "actions": ["day_summary", "tomorrow_preview"],
                }
            )

        # Night (9-11 PM)
        if 21 <= hour <= 23:
            predictions.append(
                {
                    "type": "wind_down",
                    "priority": 0.7,
                    "message": "Getting late. Time to wind down?",
                    "actions": ["relaxation", "tomorrow_prep"],
                }
            )

        # Weekend patterns
        if day in ["Saturday", "Sunday"]:
            predictions.append(
                {
                    "type": "weekend_mode",
                    "priority": 0.5,
                    "message": f"Happy {day}! Any plans?",
                    "actions": ["entertainment", "relaxation"],
                }
            )

        return predictions

    def _pattern_based_predictions(self) -> List[Dict]:
        """Predict based on learned patterns"""
        predictions = []
        if not self.user_profile:
            return predictions

        patterns = self.user_profile.get_patterns()
        for activity, info in patterns.items():
            if info.get("count", 0) >= 5:
                peak_hour = info.get("peak_hour")
                if peak_hour is not None:
                    current_hour = datetime.now().hour
                    if abs(current_hour - peak_hour) <= 1:
                        predictions.append(
                            {
                                "type": "pattern_match",
                                "priority": 0.7,
                                "message": f"Usually around this time you {activity}. Want to do that now?",
                                "activity": activity,
                            }
                        )

        return predictions

    def _context_based_predictions(self) -> List[Dict]:
        """Predict based on current context"""
        predictions = []
        if not self.memory:
            return predictions

        # Check recent conversations for unfinished tasks
        recent = self.memory.get_recent_conversations(5)
        for conv in recent:
            user_text = conv.get("user", "").lower()
            if any(
                w in user_text for w in ["remind me", "later", "tomorrow", "next time"]
            ):
                predictions.append(
                    {
                        "type": "follow_up",
                        "priority": 0.8,
                        "message": f"You mentioned something about '{conv['user'][:50]}...' earlier. Want to continue?",
                        "context": conv,
                    }
                )

        return predictions

    def _health_based_predictions(self) -> List[Dict]:
        """Predict based on health indicators"""
        predictions = []

        # Check if user has been asking about health
        if self.memory:
            recent = self.memory.search_conversations("sick")
            if recent:
                predictions.append(
                    {
                        "type": "health_check",
                        "priority": 0.9,
                        "message": "You mentioned not feeling well earlier. How are you doing now?",
                        "actions": ["health_tips", "medicine_reminder"],
                    }
                )

        return predictions

    def generate_suggestions(self) -> List[Dict]:
        """Generate proactive suggestions"""
        suggestions = []
        now = datetime.now()

        # Weather-based suggestions
        suggestions.append(
            {
                "type": "weather",
                "text": "Want me to check the weather?",
                "priority": 0.5,
                "time_sensitive": True,
            }
        )

        # News-based suggestions
        if now.hour >= 7:
            suggestions.append(
                {
                    "type": "news",
                    "text": "Catch up on today's news?",
                    "priority": 0.4,
                    "time_sensitive": True,
                }
            )

        # File-based suggestions
        suggestions.append(
            {
                "type": "files",
                "text": "Need help with any files?",
                "priority": 0.3,
                "time_sensitive": False,
            }
        )

        # Memory-based suggestions
        if self.memory and len(self.memory.conversations) > 10:
            suggestions.append(
                {
                    "type": "memory",
                    "text": "Want to review what we talked about recently?",
                    "priority": 0.3,
                    "time_sensitive": False,
                }
            )

        return suggestions

    def _generate_morning_greeting(self) -> str:
        """Generate personalized morning greeting"""
        name = ""
        if self.user_profile:
            name = self.user_profile.get_name()

        greetings = [
            f"Good morning{name and ' ' + name}! Ready for a great day?",
            f"Morning{name and ' ' + name}! Let's make today count.",
            f"Rise and shine{name and ' ' + name}! What's the plan today?",
            f"Good morning{name and ' ' + name}! I've got your back today.",
        ]
        return random.choice(greetings)

    def get_daily_briefing(self) -> str:
        """Generate comprehensive daily briefing"""
        parts = []

        # Greeting
        hour = datetime.now().hour
        if hour < 12:
            parts.append(self._generate_morning_greeting())
        elif hour < 17:
            parts.append("Good afternoon! Here's your update:")
        else:
            parts.append("Good evening! Here's your update:")

        # Date
        parts.append(f"\nToday is {datetime.now().strftime('%A, %B %d, %Y')}")

        # Weather
        if self.user_profile:
            location = self.user_profile.get_location()
            if location:
                from agents.research import ResearchAgent

                agent = ResearchAgent()
                weather = agent.get_weather(location)
                parts.append(f"\n{weather}")

        # News
        from agents.research import ResearchAgent

        agent = ResearchAgent()
        news = agent.get_news()
        parts.append(f"\n{news}")

        # Patterns
        if self.user_profile:
            routine = self.user_profile.get_routine_summary()
            parts.append(f"\n{routine}")

        # Emotional check-in
        if self.user_profile:
            mood = self.user_profile.get_current_mood()
            if mood:
                parts.append(
                    f"\nI noticed you've been feeling {mood} lately. How are you doing?"
                )

        return "\n".join(parts)

    def should_notify(self, prediction: Dict) -> bool:
        """Decide if a prediction should trigger a notification"""
        priority = prediction.get("priority", 0)
        if priority >= 0.8:
            return True
        if priority >= 0.6 and random.random() < 0.5:
            return True
        return False

    def get_insights(self) -> List[str]:
        """Generate insights about the user"""
        insights = []

        if self.user_profile:
            # Activity insights
            patterns = self.user_profile.get_patterns()
            if patterns:
                for activity, info in patterns.items():
                    if info["count"] >= 10:
                        insights.append(
                            f"You've {activity.replace('_', ' ')} {info['count']} times"
                        )

            # Relationship insights
            relationships = self.user_profile.get_relationships()
            if relationships:
                names = [r["name"] for r in relationships]
                insights.append(
                    f"I know about {len(relationships)} people in your life: {', '.join(names[:5])}"
                )

            # Preference insights
            prefs = self.user_profile.get_all_preferences()
            if prefs:
                total_prefs = sum(len(v) for v in prefs.values())
                insights.append(f"I've learned {total_prefs} preferences about you")

        return insights
