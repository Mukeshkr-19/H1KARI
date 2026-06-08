"""
HIKARI v2.0 - Authentication System

Public-repo note:
- No biometric artifacts should be committed.
- Speaker verification enrollment is stored under the private brain legacy-data dir.
"""

import os
import hashlib
import json
import time
from typing import Dict, Any
from pathlib import Path
from dotenv import load_dotenv

from core.runtime_paths import legacy_data_dir

load_dotenv()

DATA_DIR = legacy_data_dir()
VOICE_PRINT_DIR = DATA_DIR / "voice_prints"
AUTH_FILE = DATA_DIR / "auth.json"


class VoiceAuth:
    """
    Backwards-compatible wrapper for speaker verification.

    Historically this file had a placeholder “signature” implementation.
    We now delegate to `core.speaker_auth.SpeakerAuth` (ECAPA embeddings).
    """

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        VOICE_PRINT_DIR.mkdir(parents=True, exist_ok=True)
        self._speaker = None
        try:
            from core.speaker_auth import SpeakerAuth

            self._speaker = SpeakerAuth()
        except Exception:
            self._speaker = None

    def verify_speaker(self, audio) -> bool:
        """Return True iff the speaker matches the enrolled voice."""
        if self._speaker is None:
            return False
        if not self._speaker.is_enrolled():
            return False
        emb = self._speaker.embedding_from_speech_recognition_audio(audio)
        res = self._speaker.verify_embedding(emb)
        return bool(res.ok)

    def get_status(self) -> Dict[str, Any]:
        if self._speaker is None:
            return {"enrolled": False, "available": False}
        return {"enrolled": self._speaker.is_enrolled(), "available": True}


class CodenameAuth:
    """Codename-based authentication fallback"""

    def __init__(self, codename: str | None = None):
        codename = codename or os.getenv("CODENAME", "change-me")
        self.codename_hash = hashlib.sha256(codename.encode()).hexdigest()
        self.attempts = 0
        self.max_attempts = 5
        self.lockout_time = 300  # 5 minutes
        self.last_attempt = 0
        self.locked = False

    def verify(self, input_codename: str) -> bool:
        """Verify codename input"""
        if self.locked:
            if time.time() - self.last_attempt < self.lockout_time:
                print("[AUTH] Codename auth locked - too many attempts")
                return False
            else:
                self.locked = False
                self.attempts = 0

        input_hash = hashlib.sha256(input_codename.strip().encode()).hexdigest()
        self.last_attempt = time.time()
        self.attempts += 1

        if input_hash == self.codename_hash:
            print("[AUTH] Codename verified")
            self.attempts = 0
            return True

        print(f"[AUTH] Invalid codename (attempt {self.attempts}/{self.max_attempts})")
        if self.attempts >= self.max_attempts:
            self.locked = True
            print("[AUTH] Codename auth locked for 5 minutes")

        return False


class SecurityPolicy:
    """Enforces security policies for agent actions"""

    def __init__(self):
        self.allowed_paths = set()
        self.blocked_actions = set()
        self.audit_log = []

    def allow_path(self, path: str):
        """Whitelist a path for agent access"""
        self.allowed_paths.add(path)

    def block_action(self, action: str):
        """Block a specific action"""
        self.blocked_actions.add(action)

    def check_action(self, agent: str, action: str, target: str = "") -> bool:
        """Check if an action is allowed"""
        if action in self.blocked_actions:
            self._log("BLOCKED", agent, action, target)
            return False

        self._log("ALLOWED", agent, action, target)
        return True

    def _log(self, status: str, agent: str, action: str, target: str):
        self.audit_log.append(
            {
                "status": status,
                "agent": agent,
                "action": action,
                "target": target,
                "time": time.time(),
            }
        )
        if len(self.audit_log) > 500:
            self.audit_log = self.audit_log[-500:]

    def get_audit_log(self, limit: int = 20) -> list:
        return self.audit_log[-limit:]
