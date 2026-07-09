"""
HIKARI v2.0 - User Profile System
Deep learning about the user: habits, routines, relationships, preferences, patterns
"""

import os
import json
import time
import re
from typing import Optional, Dict, Any, List, Set
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict, Counter

from core.runtime_paths import legacy_data_dir

DATA_DIR = legacy_data_dir()
PROFILE_FILE = DATA_DIR / "user_profile.json"
PATTERNS_FILE = DATA_DIR / "behavior_patterns.json"
RELATIONSHIPS_FILE = DATA_DIR / "relationships.json"


_NAME_CAPTURE = r"([A-Za-z][a-z]*(?:\s+[A-Za-z][a-z]*)*)"


class UserProfile:
    """Deep user profile that learns and adapts over time"""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.profile: Dict[str, Any] = {}
        self.patterns: Dict[str, Any] = {}
        self.relationships: Dict[str, Dict] = {}
        self.daily_log: List[Dict] = []
        self._load()

    def _load(self):
        """Load all profile data"""
        try:
            if PROFILE_FILE.exists():
                with open(PROFILE_FILE, "r") as f:
                    self.profile = json.load(f)
        except Exception as e:
            print(f"[PROFILE] Load error: {e}")

        try:
            if PATTERNS_FILE.exists():
                with open(PATTERNS_FILE, "r") as f:
                    self.patterns = json.load(f)
        except Exception as e:
            print(f"[PROFILE] Patterns load error: {e}")

        try:
            if RELATIONSHIPS_FILE.exists():
                with open(RELATIONSHIPS_FILE, "r") as f:
                    self.relationships = json.load(f)
        except Exception as e:
            print(f"[PROFILE] Relationships load error: {e}")

    def _save(self):
        """Save all profile data"""
        try:
            with open(PROFILE_FILE, "w") as f:
                json.dump(self.profile, f, indent=2)
            with open(PATTERNS_FILE, "w") as f:
                json.dump(self.patterns, f, indent=2)
            with open(RELATIONSHIPS_FILE, "w") as f:
                json.dump(self.relationships, f, indent=2)
        except Exception as e:
            print(f"[PROFILE] Save error: {e}")

    # --- Core Profile ---

    def set_name(self, name: str):
        """Set user's name"""
        self.profile["name"] = name
        self._save()

    def get_name(self) -> str:
        return self.profile.get("name", "user")

    def set_timezone(self, tz: str):
        self.profile["timezone"] = tz
        self._save()

    def set_location(self, location: str):
        self.profile["location"] = location
        self._save()

    def get_location(self) -> str:
        return self.profile.get("location", "")

    # --- Learning Preferences ---

    def learn_preference(
        self, category: str, key: str, value: Any, confidence: float = 1.0
    ):
        """Learn a preference with confidence score"""
        if "preferences" not in self.profile:
            self.profile["preferences"] = {}
        if category not in self.profile["preferences"]:
            self.profile["preferences"][category] = {}

        current = self.profile["preferences"][category].get(key, {})
        if isinstance(current, dict):
            # Update existing preference
            current["value"] = value
            current["confidence"] = min(
                1.0, current.get("confidence", 0) + confidence * 0.1
            )
            current["last_updated"] = datetime.now().isoformat()
            current["times_seen"] = current.get("times_seen", 0) + 1
        else:
            self.profile["preferences"][category][key] = {
                "value": value,
                "confidence": confidence,
                "learned_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "times_seen": 1,
            }
        self._save()

    def get_preference(self, category: str, key: str, default: Any = None) -> Any:
        """Get a learned preference"""
        pref = self.profile.get("preferences", {}).get(category, {}).get(key, {})
        if isinstance(pref, dict):
            return pref.get("value", default)
        return pref if pref else default

    def get_all_preferences(self) -> Dict[str, Any]:
        """Get all preferences with simplified format"""
        result = {}
        for category, prefs in self.profile.get("preferences", {}).items():
            result[category] = {}
            for key, value in prefs.items():
                if isinstance(value, dict):
                    result[category][key] = value.get("value")
                else:
                    result[category][key] = value
        return result

    # --- Learning Facts ---

    def learn_fact(self, fact: str, category: str = "general"):
        """Learn a fact about the user"""
        if "facts" not in self.profile:
            self.profile["facts"] = {}
        if category not in self.profile["facts"]:
            self.profile["facts"][category] = []

        fact_text = str(fact or "").strip()
        if not fact_text:
            return

        # Check if fact already exists
        for existing in self.profile["facts"][category]:
            if isinstance(existing, dict):
                existing_text = str(existing.get("fact", "")).strip()
            else:
                existing_text = str(existing or "").strip()
            if not existing_text:
                continue
            if (
                fact_text.lower() in existing_text.lower()
                or existing_text.lower() in fact_text.lower()
            ):
                return  # Already known

        self.profile["facts"][category].append(
            {
                "fact": fact_text,
                "learned_at": datetime.now().isoformat(),
                "confidence": 1.0,
            }
        )
        self._save()

    def get_facts(self, category: str = None) -> List[str]:
        """Get learned facts"""
        facts = self.profile.get("facts", {})
        if category:
            return [
                f.get("fact", str(f)) if isinstance(f, dict) else str(f)
                for f in facts.get(category, [])
            ]
        all_facts = []
        for cat_facts in facts.values():
            all_facts.extend(
                f.get("fact", str(f)) if isinstance(f, dict) else str(f)
                for f in cat_facts
            )
        return all_facts

    # --- Relationships ---

    def add_relationship(self, name: str, relationship: str, details: Dict = None):
        """Learn about a person in user's life"""
        normalized = " ".join((name or "").split()).lower()
        if not normalized:
            return
        display_name = " ".join(piece.capitalize() for piece in normalized.split())
        self.relationships[normalized] = {
            "name": display_name,
            "relationship": relationship,
            "details": details or {},
            "first_mentioned": self.relationships.get(normalized, {}).get(
                "first_mentioned", datetime.now().isoformat()
            ),
            "last_mentioned": datetime.now().isoformat(),
            "mention_count": self.relationships.get(normalized, {}).get(
                "mention_count", 0
            )
            + 1,
        }
        self._save()

    def update_relationship(self, name: str, details: Dict):
        """Update relationship details"""
        key = name.lower()
        if key in self.relationships:
            self.relationships[key]["details"].update(details)
            self.relationships[key]["last_mentioned"] = datetime.now().isoformat()
            self.relationships[key]["mention_count"] += 1
            self._save()

    def get_relationships(self) -> List[Dict]:
        return list(self.relationships.values())

    def find_person(self, name: str) -> Optional[Dict]:
        return self.relationships.get(name.lower())

    # --- Behavior Patterns ---

    def log_activity(self, activity_type: str, details: Dict = None):
        """Log user activity for pattern detection"""
        entry = {
            "type": activity_type,
            "time": datetime.now().isoformat(),
            "hour": datetime.now().hour,
            "day": datetime.now().strftime("%A"),
            "details": details or {},
        }
        self.daily_log.append(entry)

        # Keep only last 1000 entries
        if len(self.daily_log) > 1000:
            self.daily_log = self.daily_log[-1000:]

        # Update patterns
        self._update_patterns(activity_type, entry)
        self._save()

    def _update_patterns(self, activity_type: str, entry: Dict):
        """Update behavior patterns"""
        if "patterns" not in self.patterns:
            self.patterns["patterns"] = {}

        if activity_type not in self.patterns["patterns"]:
            self.patterns["patterns"][activity_type] = {
                "count": 0,
                "hours": [],
                "days": [],
                "last_seen": entry["time"],
            }

        pattern = self.patterns["patterns"][activity_type]
        pattern["count"] += 1
        pattern["hours"].append(entry["hour"])
        pattern["days"].append(entry["day"])
        pattern["last_seen"] = entry["time"]

        # Keep only recent data
        if len(pattern["hours"]) > 100:
            pattern["hours"] = pattern["hours"][-100:]
            pattern["days"] = pattern["days"][-100:]

    def get_patterns(self) -> Dict[str, Any]:
        """Get behavior patterns"""
        result = {}
        for activity, pattern in self.patterns.get("patterns", {}).items():
            hours = pattern.get("hours", [])
            days = pattern.get("days", [])

            # Find most common hour
            peak_hour = Counter(hours).most_common(1)[0][0] if hours else None
            # Find most common day
            peak_day = Counter(days).most_common(1)[0][0] if days else None

            result[activity] = {
                "count": pattern["count"],
                "peak_hour": peak_hour,
                "peak_day": peak_day,
                "last_seen": pattern.get("last_seen"),
            }
        return result

    def get_routine_summary(self) -> str:
        """Generate a summary of user's routines"""
        patterns = self.get_patterns()
        if not patterns:
            return "I'm still learning your routines."

        parts = ["Here's what I've noticed about your routines:"]
        for activity, info in patterns.items():
            if info["count"] >= 3:
                time_str = (
                    f"{info['peak_hour']}:00"
                    if info["peak_hour"] is not None
                    else "varies"
                )
                day_str = info["peak_day"] or "any day"
                parts.append(
                    f"- {activity}: usually around {time_str} on {day_str} ({info['count']} times)"
                )

        return "\n".join(parts)

    # --- Mood & State Tracking ---

    def log_mood(self, mood: str, intensity: float = 0.5, context: str = ""):
        """Log detected or reported mood"""
        if "mood_history" not in self.profile:
            self.profile["mood_history"] = []

        self.profile["mood_history"].append(
            {
                "mood": mood,
                "intensity": intensity,
                "context": context,
                "time": datetime.now().isoformat(),
            }
        )

        # Keep only last 200 entries
        if len(self.profile["mood_history"]) > 200:
            self.profile["mood_history"] = self.profile["mood_history"][-200:]
        self._save()

    def get_current_mood(self) -> Optional[str]:
        """Get most recent mood"""
        history = self.profile.get("mood_history", [])
        if history:
            return history[-1]["mood"]
        return None

    def get_mood_patterns(self) -> Dict[str, Any]:
        """Get mood patterns"""
        history = self.profile.get("mood_history", [])
        if not history:
            return {}

        moods = [h["mood"] for h in history]
        mood_counts = Counter(moods)

        return {
            "most_common": mood_counts.most_common(3),
            "recent": history[-5:],
            "total_entries": len(history),
        }

    # --- Intelligence ---

    def extract_info_from_conversation(
        self, user_input: str, ai_response: str, *, allow_primary_updates: bool = True
    ):
        """Automatically extract information from conversations"""
        lower = user_input.lower()

        # Extract names and relationships
        name_patterns = [
            (rf"my (?:wife|husband|partner) is {_NAME_CAPTURE}", "spouse"),
            (rf"my (?:mom|mother) is {_NAME_CAPTURE}", "mother"),
            (rf"my (?:dad|father) is {_NAME_CAPTURE}", "father"),
            (rf"my (?:brother|sister) is {_NAME_CAPTURE}", "sibling"),
            (rf"my (?:son|daughter) is {_NAME_CAPTURE}", "child"),
            (rf"my (?:friend|colleague|boss) {_NAME_CAPTURE}", "friend"),
            (rf"i work with {_NAME_CAPTURE}", "colleague"),
            (rf"i know {_NAME_CAPTURE}", "acquaintance"),
        ]

        for pattern, relationship in name_patterns:
            match = re.search(pattern, lower, re.I)
            if match:
                raw_name = match.group(1).strip()
                name = " ".join(piece.capitalize() for piece in raw_name.split())
                self.add_relationship(name, relationship)

        # Extract preferences
        pref_patterns = [
            (r"i (?:like|love|prefer|enjoy) (.+)", "likes"),
            (r"i (?:hate|dislike|don't like|can't stand) (.+)", "dislikes"),
            (r"my favorite (.+?) is (.+)", "favorites"),
            (r"i (?:live|work|study) in (.+)", "location"),
            (r"i use (.+?) for (.+)", "tools"),
        ]

        if allow_primary_updates:
            for pattern, category in pref_patterns:
                match = re.search(pattern, lower)
                if match:
                    if category == "favorites":
                        key = match.group(1).strip()
                        value = match.group(2).strip()
                        self.learn_preference(category, key, value)
                    elif category == "location":
                        location = match.group(1).strip()
                        self.set_location(location)
                    else:
                        value = match.group(1).strip()
                        self.learn_preference(category, value, True)

            fact_patterns = [
                r"i am (\d+) years? old",
                r"i work (?:as|at|for) (.+)",
                r"i study (.+)",
                r"i (?:have|own) a? (.+)",
            ]

            for pattern in fact_patterns:
                match = re.search(pattern, lower)
                if match:
                    self.learn_fact(user_input.strip(), "personal")

        # Log activity
        if any(w in lower for w in ["weather", "temperature"]):
            self.log_activity("weather_check")
        if any(w in lower for w in ["news", "headlines"]):
            self.log_activity("news_check")
        if any(w in lower for w in ["open", "launch"]):
            self.log_activity("app_launch", {"query": user_input})
        if any(w in lower for w in ["code", "program", "debug"]):
            self.log_activity("coding")
        if any(w in lower for w in ["file", "document", "read"]):
            self.log_activity("file_access")

    def get_context_for_ai(self) -> str:
        """Build rich context string for AI responses"""
        parts = []

        name = self.get_name()
        if name:
            parts.append(f"User's name: {name}")

        location = self.get_location()
        if location:
            parts.append(f"Location: {location}")

        # Add preferences
        prefs = self.get_all_preferences()
        if prefs:
            pref_strs = []
            for category, values in prefs.items():
                for key, value in values.items():
                    if value:
                        pref_strs.append(f"{category}: {key} = {value}")
            if pref_strs:
                parts.append(f"Preferences: {', '.join(pref_strs[:10])}")

        # Add facts
        facts = self.get_facts()
        if facts:
            parts.append(f"Known facts: {'; '.join(facts[:5])}")

        # Add relationships
        relationships = self.get_relationships()
        if relationships:
            rel_strs = [f"{r['name']} ({r['relationship']})" for r in relationships[:5]]
            parts.append(f"People: {', '.join(rel_strs)}")

        # Add mood
        mood = self.get_current_mood()
        if mood:
            parts.append(f"Current mood: {mood}")

        return "\n".join(parts)

    def get_summary(self) -> Dict[str, Any]:
        """Get complete profile summary"""
        return {
            "name": self.get_name(),
            "location": self.get_location(),
            "preferences": self.get_all_preferences(),
            "facts": self.get_facts(),
            "relationships": len(self.relationships),
            "patterns": self.get_patterns(),
            "mood": self.get_mood_patterns(),
            "total_interactions": len(self.daily_log),
        }
