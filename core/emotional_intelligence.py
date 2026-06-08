"""
HIKARI v2.0 - Emotional Intelligence System
Detects mood from voice/text, adapts responses, provides empathetic interactions
"""

import re
import math
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from collections import defaultdict


class EmotionDetector:
    """Detect emotions from text and voice features"""

    # Emotion word dictionaries
    EMOTION_WORDS = {
        "happy": [
            "happy",
            "great",
            "awesome",
            "amazing",
            "wonderful",
            "fantastic",
            "excellent",
            "good",
            "love",
            "excited",
            "joy",
            "glad",
            "pleased",
            "thrilled",
            "delighted",
        ],
        "sad": [
            "sad",
            "unhappy",
            "depressed",
            "down",
            "blue",
            "miserable",
            "heartbroken",
            "lonely",
            "gloomy",
            "hopeless",
            "cry",
            "crying",
            "tears",
        ],
        "angry": [
            "angry",
            "furious",
            "mad",
            "annoyed",
            "frustrated",
            "irritated",
            "pissed",
            "rage",
            "hate",
            "hateful",
            "livid",
            "outraged",
        ],
        "anxious": [
            "anxious",
            "worried",
            "nervous",
            "stressed",
            "tense",
            "uneasy",
            "concerned",
            "fearful",
            "panicked",
            "overwhelmed",
            "pressure",
        ],
        "tired": [
            "tired",
            "exhausted",
            "sleepy",
            "fatigued",
            "drained",
            "worn out",
            "burnt out",
            "drowsy",
            "weak",
            "sick",
            "ill",
            "unwell",
            "fever",
            "cold",
            "flu",
        ],
        "confused": [
            "confused",
            "lost",
            "puzzled",
            "bewildered",
            "uncertain",
            "unsure",
            "dont understand",
            "don't understand",
            "what does",
            "how does",
        ],
        "excited": [
            "excited",
            "thrilled",
            "pumped",
            "stoked",
            "cant wait",
            "can't wait",
            "looking forward",
            "eager",
            "enthusiastic",
        ],
        "grateful": [
            "thank",
            "thanks",
            "grateful",
            "appreciate",
            "gratitude",
            "blessed",
            "thankful",
        ],
    }

    # Voice feature patterns for emotion detection
    VOICE_PATTERNS = {
        "tired": {"low_energy": True, "slow_pace": True, "low_pitch": True},
        "excited": {"high_energy": True, "fast_pace": True, "high_pitch": True},
        "angry": {"high_energy": True, "loud": True, "sharp_pitch": True},
        "sad": {"low_energy": True, "slow_pace": True, "monotone": True},
        "anxious": {"medium_energy": True, "fast_pace": True, "unstable_pitch": True},
    }

    def detect_from_text(self, text: str) -> Dict[str, float]:
        """Detect emotions from text"""
        lower = text.lower()
        scores = defaultdict(float)

        # Word-based detection
        for emotion, words in self.EMOTION_WORDS.items():
            for word in words:
                if word in lower:
                    scores[emotion] += 1.0

        # Punctuation and formatting signals
        if "!" in text:
            scores["excited"] += 0.3
            scores["happy"] += 0.2
        if "..." in text:
            scores["sad"] += 0.2
            scores["tired"] += 0.2
        if text.isupper():
            scores["angry"] += 0.5
            scores["excited"] += 0.3
        if "?" in text and len(text) < 50:
            scores["confused"] += 0.3

        # Negation handling
        negations = [
            "not",
            "don't",
            "doesn't",
            "isn't",
            "aren't",
            "wasn't",
            "weren't",
            "no",
            "never",
            "hardly",
        ]
        for neg in negations:
            if neg in lower:
                # Reduce positive emotions if negated
                scores["happy"] = max(0, scores["happy"] - 0.5)
                scores["excited"] = max(0, scores["excited"] - 0.5)
                scores["grateful"] = max(0, scores["grateful"] - 0.5)

        # Normalize scores
        total = sum(scores.values())
        if total > 0:
            for emotion in scores:
                scores[emotion] = scores[emotion] / total

        return dict(scores)

    def detect_from_voice(self, audio_features: Dict[str, float]) -> Dict[str, float]:
        """Detect emotions from voice features"""
        scores = defaultdict(float)

        energy = audio_features.get("rms", 0.5)
        pitch = audio_features.get("pitch_estimate", 200)
        zcr = audio_features.get("zero_crossing_rate", 0.1)
        energy_variance = audio_features.get("std_energy", 0)

        # Energy-based detection
        if energy < 0.1:
            scores["tired"] += 0.5
            scores["sad"] += 0.3
        elif energy > 0.5:
            scores["excited"] += 0.3
            scores["angry"] += 0.2

        # Pitch-based detection
        if pitch < 100:
            scores["sad"] += 0.3
            scores["tired"] += 0.2
        elif pitch > 300:
            scores["excited"] += 0.3
            scores["anxious"] += 0.2

        # Variance-based detection
        if energy_variance < 0.01:
            scores["tired"] += 0.2
            scores["sad"] += 0.2
        elif energy_variance > 0.1:
            scores["excited"] += 0.2
            scores["anxious"] += 0.2

        # Normalize
        total = sum(scores.values())
        if total > 0:
            for emotion in scores:
                scores[emotion] = scores[emotion] / total

        return dict(scores)

    def get_dominant_emotion(self, scores: Dict[str, float]) -> Tuple[str, float]:
        """Get the dominant emotion and its score"""
        if not scores:
            return "neutral", 0.0
        dominant = max(scores, key=scores.get)
        return dominant, scores[dominant]

    def is_sick_indicator(self, text: str, voice_scores: Dict = None) -> bool:
        """Check if user might be sick"""
        lower = text.lower()
        sick_words = [
            "sick",
            "ill",
            "fever",
            "cold",
            "flu",
            "cough",
            "headache",
            "sore throat",
            "tired",
            "exhausted",
            "weak",
            "dizzy",
            "nausea",
            "pain",
            "hurt",
            "unwell",
            "not feeling well",
            "don't feel well",
            "dont feel well",
        ]

        text_sick = any(w in lower for w in sick_words)
        voice_sick = False

        if voice_scores:
            voice_sick = voice_scores.get("tired", 0) > 0.4

        return text_sick or voice_sick


class ResponseAdapter:
    """Adapts AI responses based on detected emotion"""

    # Response templates for different emotions
    RESPONSE_MODIFIERS = {
        "happy": {
            "tone": "enthusiastic",
            "prefix": "",
            "style": "Keep the energy up! Be upbeat and positive.",
        },
        "sad": {
            "tone": "empathetic",
            "prefix": "",
            "style": "Be gentle, empathetic, and supportive. Keep responses warm and caring.",
        },
        "angry": {
            "tone": "calm",
            "prefix": "",
            "style": "Stay calm and helpful. Don't match their anger. Be patient and solution-focused.",
        },
        "anxious": {
            "tone": "reassuring",
            "prefix": "",
            "style": "Be reassuring and clear. Break things down simply. Reduce uncertainty.",
        },
        "tired": {
            "tone": "gentle",
            "prefix": "",
            "style": "Be brief and gentle. Don't overwhelm. Get straight to the point.",
        },
        "confused": {
            "tone": "patient",
            "prefix": "",
            "style": "Be very clear and patient. Explain step by step. Use simple language.",
        },
        "excited": {
            "tone": "matching",
            "prefix": "",
            "style": "Match their energy! Be enthusiastic and engaged.",
        },
        "grateful": {
            "tone": "warm",
            "prefix": "",
            "style": "Be warm and humble. Acknowledge their gratitude gracefully.",
        },
    }

    def adapt_system_prompt(
        self, base_prompt: str, emotion: str, emotion_score: float
    ) -> str:
        """Adapt the system prompt based on emotion"""
        if emotion == "neutral" or emotion_score < 0.3:
            return base_prompt

        modifier = self.RESPONSE_MODIFIERS.get(emotion, {})
        style = modifier.get("style", "")

        if style:
            return f"{base_prompt}\n\nIMPORTANT: The user seems to be feeling {emotion}. {style}"
        return base_prompt

    def get_empathetic_response(self, emotion: str, context: str = "") -> Optional[str]:
        """Generate an empathetic response before the main answer"""
        empathy_responses = {
            "sad": [
                "I'm here for you. Let me help with that.",
                "I understand things might be tough. Let's figure this out together.",
                "I've got your back. What can I do to help?",
            ],
            "angry": [
                "I hear you. Let's work through this calmly.",
                "I understand your frustration. Let me help fix this.",
                "Take a breath - I'm here to help sort this out.",
            ],
            "anxious": [
                "Everything's going to be okay. Let me help you with this.",
                "No worries, I've got this covered. Here's what we need to know:",
                "Let's take this one step at a time. I'm here to help.",
            ],
            "tired": [
                "You sound tired. I'll keep this brief.",
                "Let me make this easy for you.",
                "I'll keep things simple. Here you go:",
            ],
            "sick": [
                "Hope you're feeling okay. I'll keep things light.",
                "Take it easy - I've got this handled for you.",
                "Rest up - let me take care of this.",
            ],
        }

        responses = empathy_responses.get(emotion, [])
        if responses:
            import random

            return random.choice(responses)
        return None


class EmotionalMemory:
    """Tracks emotional patterns over time"""

    def __init__(self):
        self.emotion_log: List[Dict] = []
        self.emotion_patterns: Dict[str, List] = defaultdict(list)
        self.mood_trends: Dict[str, float] = {}

    def log_emotion(self, emotion: str, score: float, context: str = ""):
        """Log an emotional event"""
        entry = {
            "emotion": emotion,
            "score": score,
            "context": context,
            "time": datetime.now().isoformat(),
            "hour": datetime.now().hour,
            "day": datetime.now().strftime("%A"),
        }
        self.emotion_log.append(entry)
        self.emotion_patterns[emotion].append(entry)

        # Keep only recent data
        if len(self.emotion_log) > 500:
            self.emotion_log = self.emotion_log[-500:]
        if len(self.emotion_patterns[emotion]) > 100:
            self.emotion_patterns[emotion] = self.emotion_patterns[emotion][-100:]

    def get_emotional_state(self) -> Dict[str, Any]:
        """Get current emotional state summary"""
        if not self.emotion_log:
            return {"state": "unknown", "trend": "stable"}

        recent = self.emotion_log[-20:]
        emotions = [e["emotion"] for e in recent]

        from collections import Counter

        counts = Counter(emotions)
        dominant = counts.most_common(1)[0] if counts else ("neutral", 0)

        # Check trend
        older = self.emotion_log[-40:-20] if len(self.emotion_log) >= 40 else []
        older_emotions = [e["emotion"] for e in older]
        older_counts = Counter(older_emotions)

        trend = "stable"
        if dominant[0] in ["sad", "tired", "anxious"]:
            if older_counts.get(dominant[0], 0) < dominant[1]:
                trend = "worsening"
            else:
                trend = "improving"

        return {
            "state": dominant[0],
            "intensity": dominant[1] / max(len(recent), 1),
            "trend": trend,
            "recent_emotions": dict(counts),
        }

    def get_emotional_insights(self) -> List[str]:
        """Generate insights about emotional patterns"""
        insights = []
        state = self.get_emotional_state()

        if state["trend"] == "worsening":
            insights.append(
                f"I've noticed you've been feeling {state['state']} more lately. Is everything okay?"
            )
        elif state["trend"] == "improving":
            insights.append(
                f"Things seem to be looking up! Your mood has been improving."
            )

        # Time-based patterns
        for emotion, entries in self.emotion_patterns.items():
            if len(entries) >= 5:
                hours = [e["hour"] for e in entries]
                from collections import Counter

                peak_hour = Counter(hours).most_common(1)[0][0]
                if emotion in ["tired", "sad"]:
                    insights.append(f"You tend to feel {emotion} around {peak_hour}:00")

        return insights
