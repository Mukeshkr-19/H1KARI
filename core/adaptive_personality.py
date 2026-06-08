"""
HIKARI v2.0 - Adaptive Personality System
Evolves with the user, remembers communication style, adapts tone and behavior
"""

import json
import random
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path
from collections import Counter

from core.runtime_paths import legacy_data_dir

DATA_DIR = legacy_data_dir()
PERSONALITY_FILE = DATA_DIR / "personality.json"


class AdaptivePersonality:
    """Personality that evolves based on user interactions"""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.personality: Dict[str, Any] = {}
        self.interaction_history: List[Dict] = []
        self._load()
        self._init_default()

    def _load(self):
        try:
            if PERSONALITY_FILE.exists():
                with open(PERSONALITY_FILE, "r") as f:
                    self.personality = json.load(f)
                self.interaction_history = self.personality.get(
                    "interaction_history", []
                )
        except Exception as e:
            print(f"[PERSONALITY] Load error: {e}")

    def _save(self):
        try:
            self.personality["interaction_history"] = self.interaction_history[-500:]
            with open(PERSONALITY_FILE, "w") as f:
                json.dump(self.personality, f, indent=2)
        except Exception as e:
            print(f"[PERSONALITY] Save error: {e}")

    def _init_default(self):
        """Initialize default personality traits"""
        if not self.personality.get("traits"):
            self.personality["traits"] = {
                "humor_level": 0.5,  # 0 = serious, 1 = very funny
                "formality": 0.3,  # 0 = casual, 1 = formal
                "verbosity": 0.4,  # 0 = brief, 1 = detailed
                "empathy": 0.7,  # 0 = logical, 1 = empathetic
                "proactiveness": 0.6,  # 0 = reactive, 1 = proactive
                "creativity": 0.5,  # 0 = conventional, 1 = creative
            }
            self.personality["communication_style"] = {
                "uses_emojis": False,
                "uses_exclamations": True,
                "uses_questions": True,
                "response_length": "medium",  # short, medium, long
                "greeting_style": "friendly",  # formal, friendly, casual
            }
            self.personality["learned_behaviors"] = {}
            self._save()

    def learn_from_interaction(
        self, user_input: str, ai_response: str, user_reaction: str = ""
    ):
        """Learn from how user reacts to responses"""
        interaction = {
            "user_input": user_input[:200],
            "ai_response": ai_response[:200],
            "user_reaction": user_reaction,
            "time": datetime.now().isoformat(),
        }
        self.interaction_history.append(interaction)

        # Analyze user reaction to adjust personality
        if user_reaction:
            lower_reaction = user_reaction.lower()

            # If user says "too long" or "brief" - reduce verbosity
            if any(
                w in lower_reaction for w in ["too long", "brief", "shorter", "tl;dr"]
            ):
                self._adjust_trait("verbosity", -0.1)

            # If user says "explain more" or "tell me more" - increase verbosity
            if any(
                w in lower_reaction
                for w in ["explain", "tell me more", "more detail", "elaborate"]
            ):
                self._adjust_trait("verbosity", 0.1)

            # If user laughs or says "funny" - increase humor
            if any(
                w in lower_reaction for w in ["haha", "lol", "funny", "hilarious", "😂"]
            ):
                self._adjust_trait("humor_level", 0.1)

            # If user says "be serious" - decrease humor
            if any(
                w in lower_reaction for w in ["be serious", "not funny", "stop joking"]
            ):
                self._adjust_trait("humor_level", -0.1)

            # If user says "thanks" frequently - maintain empathy
            if any(w in lower_reaction for w in ["thanks", "thank you", "appreciate"]):
                self._adjust_trait("empathy", 0.05)

        self._save()

    def _adjust_trait(self, trait: str, delta: float):
        """Adjust a personality trait"""
        traits = self.personality.get("traits", {})
        if trait in traits:
            traits[trait] = max(0.0, min(1.0, traits[trait] + delta))
            self.personality["traits"] = traits

            # Track behavior
            if trait not in self.personality.get("learned_behaviors", {}):
                self.personality["learned_behaviors"][trait] = []
            self.personality["learned_behaviors"][trait].append(
                {
                    "delta": delta,
                    "time": datetime.now().isoformat(),
                    "new_value": traits[trait],
                }
            )

    def get_response_style(self) -> Dict[str, Any]:
        """Get current response style based on personality"""
        traits = self.personality.get("traits", {})
        style = self.personality.get("communication_style", {})

        return {
            "humor": traits.get("humor_level", 0.5),
            "formality": traits.get("formality", 0.3),
            "verbosity": traits.get("verbosity", 0.4),
            "empathy": traits.get("empathy", 0.7),
            "proactiveness": traits.get("proactiveness", 0.6),
            "creativity": traits.get("creativity", 0.5),
            "uses_emojis": style.get("uses_emojis", False),
            "uses_exclamations": style.get("uses_exclamations", True),
            "response_length": style.get("response_length", "medium"),
        }

    def generate_personality_prompt(self) -> str:
        """Generate a personality prompt for the AI"""
        style = self.get_response_style()

        prompt_parts = ["Your personality and communication style:"]

        # Humor
        if style["humor"] > 0.7:
            prompt_parts.append("- Be witty and playful. Use humor naturally.")
        elif style["humor"] < 0.3:
            prompt_parts.append("- Stay serious and professional. Avoid jokes.")
        else:
            prompt_parts.append("- Use light humor when appropriate.")

        # Formality
        if style["formality"] > 0.7:
            prompt_parts.append("- Use formal, professional language.")
        elif style["formality"] < 0.3:
            prompt_parts.append("- Be casual and conversational. Use contractions.")
        else:
            prompt_parts.append("- Balance between casual and professional.")

        # Verbosity
        if style["verbosity"] > 0.7:
            prompt_parts.append("- Provide detailed, thorough explanations.")
        elif style["verbosity"] < 0.3:
            prompt_parts.append("- Keep responses brief and to the point.")
        else:
            prompt_parts.append(
                "- Provide moderate detail - enough to be helpful but not overwhelming."
            )

        # Empathy
        if style["empathy"] > 0.7:
            prompt_parts.append(
                "- Be warm, empathetic, and caring. Show genuine concern."
            )
        elif style["empathy"] < 0.3:
            prompt_parts.append(
                "- Be logical and direct. Focus on facts over feelings."
            )
        else:
            prompt_parts.append("- Be friendly and considerate while staying factual.")

        return "\n".join(prompt_parts)

    def get_personality_summary(self) -> Dict[str, Any]:
        """Get a summary of the current personality"""
        return {
            "traits": self.personality.get("traits", {}),
            "communication_style": self.personality.get("communication_style", {}),
            "total_interactions": len(self.interaction_history),
            "learned_behaviors_count": len(
                self.personality.get("learned_behaviors", {})
            ),
        }

    def reset_personality(self):
        """Reset personality to defaults"""
        self._init_default()
        self.interaction_history = []
        self._save()
