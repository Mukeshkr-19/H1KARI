"""
HIKARI v2.0 - Memory System
Persistent conversation history, user preferences, learning
"""

import os
import json
import time
import hashlib
from typing import Optional, Dict, List, Any
from datetime import datetime
from pathlib import Path

from core.runtime_paths import legacy_data_dir

DATA_DIR = legacy_data_dir()
MEMORY_FILE = DATA_DIR / "memory.json"
PREFERENCES_FILE = DATA_DIR / "preferences.json"


class MemorySystem:
    """Persistent memory and personalization system"""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conversations: List[Dict] = []
        self.preferences: Dict[str, Any] = {}
        self.facts: Dict[str, Any] = {}
        self._load()

    def _load(self):
        """Load memory from disk"""
        try:
            if MEMORY_FILE.exists():
                with open(MEMORY_FILE, "r") as f:
                    data = json.load(f)
                self.conversations = data.get("conversations", [])
                self.facts = data.get("facts", {})
        except Exception as e:
            print(f"[MEMORY] Load error: {e}")

        try:
            if PREFERENCES_FILE.exists():
                with open(PREFERENCES_FILE, "r") as f:
                    self.preferences = json.load(f)
        except Exception as e:
            print(f"[MEMORY] Preferences load error: {e}")

    def _save(self):
        """Save memory to disk"""
        try:
            data = {
                "conversations": self.conversations[-500:],  # Keep last 500
                "facts": self.facts,
                "last_updated": datetime.now().isoformat(),
            }
            with open(MEMORY_FILE, "w") as f:
                json.dump(data, f, indent=2)

            with open(PREFERENCES_FILE, "w") as f:
                json.dump(self.preferences, f, indent=2)
        except Exception as e:
            print(f"[MEMORY] Save error: {e}")

    def add_conversation(self, user_input: str, ai_response: str, context: str = ""):
        """Add a conversation turn"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "user": user_input,
            "ai": ai_response,
            "context_hash": hashlib.md5(context.encode()).hexdigest()[:8]
            if context
            else "",
        }
        self.conversations.append(entry)
        self._save()

    def get_recent_conversations(self, limit: int = 10) -> List[Dict]:
        """Get recent conversation history"""
        return self.conversations[-limit:]

    def get_context_for_prompt(self, limit: int = 5) -> str:
        """Build context string from recent conversations"""
        recent = self.conversations[-limit:]
        if not recent:
            return ""
        context_parts = []
        for conv in recent:
            context_parts.append(f"User: {conv['user']}")
            context_parts.append(f"AI: {conv['ai']}")
        return "\n".join(context_parts)

    def store_fact(self, key: str, value: Any):
        """Store a learned fact about the user"""
        self.facts[key] = {
            "value": value,
            "learned_at": datetime.now().isoformat(),
        }
        self._save()

    def get_fact(self, key: str, default: Any = None) -> Any:
        """Retrieve a stored fact"""
        fact = self.facts.get(key)
        if fact:
            return fact.get("value", default)
        return default

    def set_preference(self, key: str, value: Any):
        """Set a user preference"""
        self.preferences[key] = value
        self._save()

    def set_name(self, name: str):
        """Set user's name (alias for preference)"""
        self.set_preference("name", name)

    def set_location(self, location: str):
        """Set user's location (alias for preference)"""
        self.set_preference("location", location)

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a user preference"""
        return self.preferences.get(key, default)

    def get_all_preferences(self) -> Dict[str, Any]:
        return dict(self.preferences)

    def search_conversations(self, query: str) -> List[Dict]:
        """Search past conversations"""
        results = []
        query_lower = query.lower()
        for conv in self.conversations:
            if query_lower in conv["user"].lower() or query_lower in conv["ai"].lower():
                results.append(conv)
        return results[-20:]  # Return last 20 matches

    def get_user_summary(self) -> Dict[str, Any]:
        """Generate a summary of what HIKARI knows about the user"""
        return {
            "total_conversations": len(self.conversations),
            "facts_learned": len(self.facts),
            "preferences": self.preferences,
            "recent_topics": self._extract_recent_topics(),
        }

    def _extract_recent_topics(self) -> List[str]:
        """Extract topics from recent conversations"""
        recent = self.conversations[-20:]
        topics = []
        for conv in recent:
            words = conv["user"].lower().split()
            if len(words) > 3:
                topics.append(conv["user"][:80])
        return topics

    def clear(self):
        """Clear all memory"""
        self.conversations = []
        self.facts = {}
        self.preferences = {}
        self._save()


# Singleton
_memory_instance: Optional[MemorySystem] = None


def get_memory() -> MemorySystem:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = MemorySystem()
    return _memory_instance
