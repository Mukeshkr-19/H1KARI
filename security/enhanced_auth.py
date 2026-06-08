"""
HIKARI v2.0 - Enhanced Codename System
Multi-codename support, context-aware activation, sick-day modes
"""

import os
import json
import hashlib
import time
import random
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from pathlib import Path

from core.runtime_paths import legacy_data_dir

DATA_DIR = legacy_data_dir()
CODENAMES_FILE = DATA_DIR / "codenames.json"


class CodenameSystem:
    """Advanced codename authentication with multiple modes"""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.codenames: Dict[str, Dict] = {}
        self.attempts: Dict[str, int] = {}
        self.lockouts: Dict[str, float] = {}
        self.usage_history: List[Dict] = []
        self._load()
        self._init_default()

    def _load(self):
        try:
            if CODENAMES_FILE.exists():
                with open(CODENAMES_FILE, "r") as f:
                    data = json.load(f)
                self.codenames = data.get("codenames", {})
                self.usage_history = data.get("usage_history", [])
        except Exception as e:
            print(f"[CODENAME] Load error: {e}")

    def _save(self):
        try:
            data = {
                "codenames": self.codenames,
                "usage_history": self.usage_history[-100:],
                "last_updated": datetime.now().isoformat(),
            }
            with open(CODENAMES_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[CODENAME] Save error: {e}")

    def _init_default(self):
        """Initialize default codename if none exist"""
        if not self.codenames:
            self.add_codename(
                os.getenv("CODENAME", "change-me"),
                "primary",
                "Main activation codename",
            )

    def _hash_codename(self, codename: str) -> str:
        """Hash a codename for secure storage"""
        return hashlib.sha256(codename.encode()).hexdigest()

    def add_codename(
        self, codename: str, mode: str = "standard", description: str = ""
    ) -> bool:
        """Add a new codename with specific mode"""
        hashed = self._hash_codename(codename)
        self.codenames[hashed] = {
            "codename_hash": hashed,
            "mode": mode,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "usage_count": 0,
            "last_used": None,
            "active": True,
        }
        self._save()
        print(f"[CODENAME] Added codename (mode: {mode})")
        return True

    def verify_codename(
        self, input_codename: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Verify a codename input
        Returns: (success, mode, message)
        """
        hashed = self._hash_codename(input_codename.strip())

        # Check lockouts
        if hashed in self.lockouts:
            if time.time() < self.lockouts[hashed]:
                remaining = int(self.lockouts[hashed] - time.time())
                return (
                    False,
                    None,
                    f"Too many attempts. Try again in {remaining} seconds.",
                )
            else:
                del self.lockouts[hashed]
                self.attempts[hashed] = 0

        if hashed not in self.codenames:
            # Track failed attempts
            self.attempts[hashed] = self.attempts.get(hashed, 0) + 1
            if self.attempts[hashed] >= 5:
                self.lockouts[hashed] = time.time() + 300  # 5 minute lockout
                return False, None, "Too many failed attempts. Locked for 5 minutes."
            return False, None, None

        codename_data = self.codenames[hashed]

        if not codename_data.get("active", True):
            return False, None, "This codename has been deactivated."

        # Success
        codename_data["usage_count"] += 1
        codename_data["last_used"] = datetime.now().isoformat()
        self.attempts[hashed] = 0

        # Log usage
        self.usage_history.append(
            {
                "codename_hash": hashed,
                "mode": codename_data["mode"],
                "time": datetime.now().isoformat(),
            }
        )

        self._save()

        mode = codename_data["mode"]
        message = self._get_mode_message(mode)
        return True, mode, message

    def _get_mode_message(self, mode: str) -> str:
        """Get activation message based on codename mode"""
        messages = {
            "primary": "Welcome back! I'm ready to help.",
            "emergency": "Emergency mode activated. What do you need?",
            "quick": "Quick access mode. How can I help?",
            "silent": "Silent mode activated. I'll keep responses brief.",
            "sick_day": "I've got you covered. Rest up - I'll handle things.",
            "focus": "Focus mode on. Minimal distractions.",
            "party": "Let's keep things fun! What's up?",
        }
        return messages.get(mode, "Activated. How can I help?")

    def get_active_codenames(self) -> List[Dict]:
        """Get list of active codenames (without hashes)"""
        result = []
        for hashed, data in self.codenames.items():
            if data.get("active", True):
                result.append(
                    {
                        "mode": data["mode"],
                        "description": data.get("description", ""),
                        "usage_count": data.get("usage_count", 0),
                        "last_used": data.get("last_used"),
                    }
                )
        return result

    def deactivate_codename(self, codename: str):
        """Deactivate a codename"""
        hashed = self._hash_codename(codename)
        if hashed in self.codenames:
            self.codenames[hashed]["active"] = False
            self._save()

    def get_usage_stats(self) -> Dict[str, Any]:
        """Get codename usage statistics"""
        total_uses = sum(c.get("usage_count", 0) for c in self.codenames.values())
        modes_used = {}
        for usage in self.usage_history:
            mode = usage.get("mode", "unknown")
            modes_used[mode] = modes_used.get(mode, 0) + 1

        return {
            "total_codenames": len(self.codenames),
            "active_codenames": sum(
                1 for c in self.codenames.values() if c.get("active", True)
            ),
            "total_uses": total_uses,
            "modes_used": modes_used,
        }


class ContextAwareAuth:
    """Context-aware authentication that adapts to situation"""

    def __init__(self, codename_system: CodenameSystem):
        self.codename_system = codename_system
        self.context_history: List[Dict] = []
        self.trust_score = 0.5  # Start neutral

    def update_context(
        self, source: str, time_of_day: str, location: str = "", device: str = ""
    ):
        """Update authentication context"""
        self.context_history.append(
            {
                "source": source,
                "time": time_of_day,
                "location": location,
                "device": device,
                "timestamp": datetime.now().isoformat(),
            }
        )

        # Adjust trust based on context
        if source == "voice":
            self.trust_score = min(1.0, self.trust_score + 0.1)
        elif source == "text":
            self.trust_score = min(1.0, self.trust_score + 0.05)

        # Decay trust over time
        if len(self.context_history) > 1:
            last_time = datetime.fromisoformat(self.context_history[-2]["timestamp"])
            time_diff = (datetime.now() - last_time).total_seconds()
            if time_diff > 3600:  # 1 hour gap
                self.trust_score = max(0.3, self.trust_score - 0.1)

    def get_auth_requirements(self) -> Dict[str, Any]:
        """Get authentication requirements based on context"""
        requirements = {
            "trust_score": self.trust_score,
            "required_confidence": 0.7,
            "allow_codename": True,
            "require_voice_print": False,
        }

        # Lower requirements for trusted contexts
        if self.trust_score > 0.8:
            requirements["required_confidence"] = 0.5
        elif self.trust_score < 0.4:
            requirements["required_confidence"] = 0.9
            requirements["require_voice_print"] = True

        return requirements

    def should_prompt_for_auth(self) -> bool:
        """Decide if authentication prompt is needed"""
        if self.trust_score > 0.7:
            return False
        if len(self.context_history) == 0:
            return True
        return True
