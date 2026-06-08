"""
HIKARI v3 - Voice Agent
Handles speech I/O, wake word detection, voice commands
"""

import os
import re
import sys
import json
import time
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path

from agents.base import BaseAgent
from core.voice import VoiceSystem, ClapDetector

WAKE_WORDS = ["hikari", "shikari", "hickory", "hey hikari", "okay hikari", "hi hikari"]


class VoiceAgent(BaseAgent):
    """Handles voice input and activation"""

    def __init__(self):
        super().__init__("voice", "Voice I/O and activation")
        self.voice_system = VoiceSystem()
        self.clap_detector = ClapDetector(clap_count=2)
        self.is_listening = False
        self.last_activation = 0

    def handle(self, user_input: str, context: str = "") -> Optional[str]:
        """Process voice input - strip wake word, return command"""
        if not user_input:
            return None

        lowered = user_input.lower().strip()

        # Strip wake word
        for wake in WAKE_WORDS:
            if lowered.startswith(wake):
                lowered = lowered.replace(wake, "", 1).strip()
                break

        # If only wake word was said, return None (already awake)
        if not lowered:
            return None

        self.last_activation = time.time()
        return lowered

    def listen(self, timeout: int = 10) -> Optional[str]:
        """Listen for one voice command"""
        text = self.voice_system.listen(timeout=timeout)
        if text:
            return self.handle(text)
        return None

    def speak(self, text: str):
        """Speak text using TTS"""
        self.voice_system.speak(text)

    def start_clap_detection(self):
        """Start background clap detection"""
        self.clap_detector.start(callback=self._on_clap)

    def _on_clap(self):
        """Handle clap detection"""
        print("[VOICE] Clap activation!")
        self.last_activation = time.time()

    def can_handle(self, user_input: str) -> float:
        lowered = user_input.lower()
        if any(w in lowered for w in WAKE_WORDS + ["listen", "hear", "speak"]):
            return 0.9
        return 0.3

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        status.update({
            "voice_system": self.voice_system.get_status(),
            "last_activation": self.last_activation,
        })
        return status