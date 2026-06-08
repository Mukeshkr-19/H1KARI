"""
HIKARI v3 - Enhanced Personality Engine
Learns user preferences, adapts communication style, remembers everything
"""

import os
import json
import re
import time
import copy
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from core.runtime_paths import legacy_data_dir

class PersonalityEngine:
    """Adaptive personality that evolves with the user"""

    def __init__(self):
        self.data_dir = legacy_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_file = self.data_dir / "personality_profile.json"

        self.traits = {
            "formality": 0.5,      # 0 = casual, 1 = formal
            "verbosity": 0.5,      # 0 = short responses, 1 = detailed
            "humor": 0.5,          # 0 = serious, 1 = humorous
            "empathy": 0.7,        # How much emotional support
            "enthusiasm": 0.6,     # Energy level
            "helpfulness": 0.9,    # Proactiveness
        }

        self.user_prefs = {
            "name": None,
            "language": "en",
            "timezone": None,
            "favorite_topics": [],
            "communication_style": "friendly",
            "health_concerns": [],
            "stress_triggers": [],
            "mood_boosters": [],
        }

        self.interaction_count = 0
        self._disk_user_prefs_snapshot: Optional[Dict[str, Any]] = None
        self._load()

    @staticmethod
    def _empty_user_prefs() -> Dict[str, Any]:
        return {
            "name": None,
            "language": "en",
            "timezone": None,
            "favorite_topics": [],
            "communication_style": "friendly",
            "health_concerns": [],
            "stress_triggers": [],
            "mood_boosters": [],
        }

    def quarantine_loaded_personal_prefs(self) -> None:
        """Keep style traits in memory but do not use legacy personal prefs from disk."""
        if self._disk_user_prefs_snapshot is not None:
            return
        self._disk_user_prefs_snapshot = copy.deepcopy(self.user_prefs)
        self.user_prefs = self._empty_user_prefs()

    def _load(self):
        """Load personality data"""
        if self.profile_file.exists():
            try:
                with open(self.profile_file, "r") as f:
                    data = json.load(f)
                    self.traits.update(data.get("traits", {}))
                    self.user_prefs.update(data.get("user_prefs", {}))
                    self.interaction_count = data.get("interaction_count", 0)
            except:
                pass

    def _save(self, *, persist_personal_prefs: bool = True):
        """Save personality data; Brain v2 mode preserves on-disk personal prefs unchanged."""
        try:
            if persist_personal_prefs:
                prefs_to_write = self.user_prefs
            elif self._disk_user_prefs_snapshot is not None:
                prefs_to_write = self._disk_user_prefs_snapshot
            else:
                prefs_to_write = self._empty_user_prefs()
            data = {
                "traits": self.traits,
                "user_prefs": prefs_to_write,
                "interaction_count": self.interaction_count,
                "last_updated": datetime.now().isoformat(),
            }
            with open(self.profile_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def learn_from_interaction(
        self,
        user_input: str,
        response: str,
        context: str = "",
        *,
        allow_name_update: bool = True,
        store_personal_facts: bool = True,
    ):
        """Learn from each interaction (style traits only when store_personal_facts=False)."""
        self.interaction_count += 1
        text = user_input.lower()

        if any(w in text for w in ["please", "thank you", "would appreciate"]):
            self.traits["formality"] = min(1.0, self.traits["formality"] + 0.05)
        elif any(w in text for w in ["yeah", "cool", "awesome", "dude", "man"]):
            self.traits["formality"] = max(0.0, self.traits["formality"] - 0.05)

        if len(user_input.split()) > 30:
            self.traits["verbosity"] = min(1.0, self.traits["verbosity"] + 0.02)
        elif len(user_input.split()) < 5:
            self.traits["verbosity"] = max(0.0, self.traits["verbosity"] - 0.02)

        if any(w in text for w in ["haha", "lol", "funny", "joke", "lmao"]):
            self.traits["humor"] = min(1.0, self.traits["humor"] + 0.1)

        if store_personal_facts and allow_name_update:
            self._extract_facts(user_input)

        if store_personal_facts:
            self._save(persist_personal_prefs=True)

    def _extract_facts(self, text: str):
        """Extract facts about the user"""
        # Name detection
        if "my name is" in text:
            words = text.split()
            idx = words.index("is")
            if idx + 1 < len(words):
                name = words[idx + 1].strip(".,!").title()
                if len(name) > 1 and len(name) < 20 and name.lower() not in (
                    "baby",
                    "babe",
                    "bro",
                    "dude",
                ):
                    self.user_prefs["name"] = name

        # Preference detection
        if "i prefer" in text or "i like" in text:
            self.user_prefs["favorite_topics"].append(text)
        if "i hate" in text or "i don't like" in text:
            pass  # Could track dislikes too

        # Health concerns
        if any(w in text for w in ["headache", "sick", "tired", "hurt", "pain"]):
            self.user_prefs["health_concerns"].append(text)

        # Stress triggers
        if any(w in text for w in ["stressed", "anxious", "worried", "frustrated"]):
            self.user_prefs["stress_triggers"].append(text)

        # Mood boosters
        if any(w in text for w in ["happy", "excited", "love", "great"]):
            self.user_prefs["mood_boosters"].append(text)

    def get_greeting(self) -> str:
        """Get personalized greeting based on time and traits"""
        hour = datetime.now().hour
        name = self.user_prefs.get("name", "")

        greeting_templates = {
            "morning": ["Good morning", "Morning", "Hey, good morning"],
            "afternoon": ["Good afternoon", "Hey there", "What's up"],
            "evening": ["Good evening", "Hey", "Evening"],
            "night": ["Hey", "Hi there", "Hello"],
        }

        if hour < 12:
            time_of_day = "morning"
        elif hour < 17:
            time_of_day = "afternoon"
        elif hour < 21:
            time_of_day = "evening"
        else:
            time_of_day = "night"

        templates = greeting_templates[time_of_day]
        greeting = templates[int(self.traits["enthusiasm"] * len(templates)) % len(templates)]

        if name:
            greeting += f" {name}"

        return greeting

    def format_response(self, response: str) -> str:
        """Format response based on personality traits"""
        # Adjust verbosity
        if self.traits["verbosity"] < 0.3 and len(response) > 200:
            # Shorten response
            sentences = response.split(". ")
            response = ". ".join(sentences[:2]) + "."

        # Add enthusiasm
        if self.traits["enthusiasm"] > 0.7:
            if not response.endswith(("!", "?")):
                response += "!"

        # Add humor if appropriate
        if self.traits["humor"] > 0.6 and "!" not in response:
            # Could add a light touch here
            pass

        return response

    def get_user_context(self) -> str:
        """Get context string for AI responses"""
        context_parts = []

        if self.user_prefs.get("name"):
            context_parts.append(f"User's name: {self.user_prefs['name']}")

        if self.traits["formality"] > 0.7:
            context_parts.append("User prefers formal communication")
        elif self.traits["formality"] < 0.3:
            context_parts.append("User prefers casual communication")

        if self.user_prefs.get("favorite_topics"):
            topics = self.user_prefs["favorite_topics"][-3:]
            context_parts.append(f"User interested in: {', '.join(topics)}")

        if self.user_prefs.get("health_concerns"):
            context_parts.append("User may be dealing with health issues")

        return " | ".join(context_parts) if context_parts else ""

    def get_prompt_context(
        self,
        speaker_context=None,
        *,
        include_personal_facts: bool = True,
        brain_v2_authority: bool = False,
    ) -> str:
        """Build prompt context; omit legacy personal prefs when Brain v2 is authoritative."""
        parts = []
        if speaker_context is not None:
            line = speaker_context.prompt_context(brain_v2_authority=brain_v2_authority)
            if line:
                parts.append(line)
            if speaker_context.is_guest_speaker():
                return " ".join(parts)
        if include_personal_facts and not brain_v2_authority:
            primary = self.get_user_context()
            if primary:
                parts.append(primary)
        return " | ".join(parts)


class EmotionalIntelligence:
    """Detects and responds to user emotions"""

    def __init__(self, personality: PersonalityEngine):
        self.personality = personality
        self.emotion_log = []
        self.current_mood = "neutral"
        self.mood_history = []

    @staticmethod
    def _has_word(text: str, word: str) -> bool:
        return bool(re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE))

    def detect_emotion(self, text: str) -> Dict[str, float]:
        """Detect emotion from text"""
        text = text.lower()

        emotions = {
            "happy": 0.0,
            "sad": 0.0,
            "angry": 0.0,
            "frustrated": 0.0,
            "anxious": 0.0,
            "excited": 0.0,
            "tired": 0.0,
            "sick": 0.0,
        }

        # Happy indicators
        if any(w in text for w in ["happy", "great", "awesome", "love", "excited", "wonderful", "fantastic", "good"]):
            emotions["happy"] += 0.8
        if any(w in text for w in ["lol", "haha", "funny", "lmao", "joy"]):
            emotions["happy"] += 0.5

        # Sad indicators
        if any(w in text for w in ["sad", "unhappy", "depressed", "down", "disappointed", "upset"]):
            emotions["sad"] += 0.8
        if any(w in text for w in ["cry", "tears", "miss", "lonely"]):
            emotions["sad"] += 0.5

        # Angry indicators (word boundaries — avoid matching "mad" inside a name)
        if any(
            self._has_word(text, w)
            for w in ["angry", "mad", "furious", "annoyed", "irritated", "hate"]
        ):
            emotions["angry"] += 0.8
        if any(w in text for w in ["stupid", "damn", "shit", "fuck"]):
            emotions["angry"] += 0.5

        # Frustrated indicators
        if any(w in text for w in ["frustrated", "stuck", "can't", "impossible", "ugh"]):
            emotions["frustrated"] += 0.7

        # Anxious indicators
        if any(w in text for w in ["worried", "anxious", "nervous", "scared", "fear", "stress"]):
            emotions["anxious"] += 0.8

        # Excited indicators
        if any(w in text for w in ["excited", "can't wait", "amazing", "incredible"]):
            emotions["excited"] += 0.7

        # Tired indicators
        if any(w in text for w in ["tired", "exhausted", "sleepy", "fatigue", "drained"]):
            emotions["tired"] += 0.8

        # Sick indicators (word boundaries — avoid "will" → "ill")
        if re.search(
            r"\b(?:sick|headache|hurt|pain|ill|nauseous|fever)\b", text, re.IGNORECASE
        ):
            emotions["sick"] += 0.8

        return emotions

    def get_dominant_emotion(self, emotions: Dict[str, float]) -> tuple:
        """Get the strongest emotion"""
        if not emotions:
            return "neutral", 0.0

        max_emotion = max(emotions, key=emotions.get)
        max_score = emotions[max_emotion]

        if max_score < 0.3:
            return "neutral", max_score

        return max_emotion, max_score

    @staticmethod
    def _is_memory_update_statement(user_input: str) -> bool:
        t = (user_input or "").lower()
        if not t:
            return False
        if "remember" in t:
            return True
        if re.search(r"\bmy\s+(sister|brother|mother|father)\b", t):
            return True
        if "full name is" in t:
            return True
        if "flight" in t and re.search(
            r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|\d{1,2}(?:st|nd|rd|th)?)\b",
            t,
        ):
            return True
        if re.search(r"\bi\s+am\s+in\b", t):
            return True
        return False

    def adapt_response(
        self,
        response: str,
        emotion: str,
        score: float,
        user_input: str = "",
    ) -> str:
        """Adapt response based on detected emotion"""
        if score < 0.3:
            return response
        if self._is_memory_update_statement(user_input):
            return response

        if emotion == "sad":
            if not any(w in response.lower() for w in ["sorry", "hope", "better"]):
                response = "I'm sorry to hear that. " + response
            if self.personality.traits["empathy"] > 0.6:
                response += " I'm here if you need to talk."

        elif emotion == "angry":
            response = "I understand you're frustrated. " + response
            if self.personality.traits["formality"] < 0.5:
                response = "Hey, I get it. " + response

        elif emotion == "anxious":
            response = "Take a breath. " + response
            response += " Everything's going to be okay."

        elif emotion == "tired":
            response = "I can tell you're tired. " + response
            if self.personality.traits["helpfulness"] > 0.7:
                response += " Let me know if you need me to handle anything."

        elif emotion == "sick":
            response = "Feel better soon! " + response
            response = response.replace("!", ".")  # Less enthusiastic
            if "taking it easy" not in response.lower():
                response += " Take it easy today."

        elif emotion == "excited":
            if self.personality.traits["humor"] > 0.5:
                response = "I can feel the excitement! " + response

        elif emotion == "happy":
            if self.personality.traits["humor"] > 0.5:
                response += " 🎉"

        return response

    def log_emotion(self, emotion: str, score: float, context: str = ""):
        """Log emotion for tracking"""
        self.emotion_log.append({
            "timestamp": datetime.now().isoformat(),
            "emotion": emotion,
            "score": score,
            "context": context[:100] if context else "",
        })

        # Keep last 100 entries
        if len(self.emotion_log) > 100:
            self.emotion_log = self.emotion_log[-100:]

        # Update mood history
        if score > 0.5:
            self.current_mood = emotion

    def get_mood_summary(self) -> str:
        """Get summary of user's recent mood"""
        if not self.emotion_log:
            return "I haven't noticed any strong emotions yet."

        recent = self.emotion_log[-20:]
        emotion_counts = defaultdict(int)

        for entry in recent:
            if entry["score"] > 0.4:
                emotion_counts[entry["emotion"]] += 1

        if not emotion_counts:
            return "You've been pretty neutral lately."

        dominant = max(emotion_counts, key=emotion_counts.get)
        return f"You've been feeling {dominant} lately. I'm here for you."


# Singleton instances
_personality = None
_emotional_iq = None

def get_personality() -> PersonalityEngine:
    global _personality
    if _personality is None:
        _personality = PersonalityEngine()
    return _personality

def get_emotional_iq() -> EmotionalIntelligence:
    global _emotional_iq
    if _emotional_iq is None:
        _emotional_iq = EmotionalIntelligence(get_personality())
    return _emotional_iq
