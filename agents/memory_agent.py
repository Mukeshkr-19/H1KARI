"""
HIKARI v2.0 - Memory Agent
Manages conversation history, user preferences, and learning
"""

from typing import TYPE_CHECKING, Optional, Dict, Any, List
from datetime import datetime

from agents.base import BaseAgent

if TYPE_CHECKING:
    from core.brain import HikariBrain
from core.command_intent import is_casual_call_me
from core.family_memory import (
    answer_family_question,
    ingest_family_statement,
    is_household_memory_query,
)
from core.memory import MemorySystem


class MemoryAgent(BaseAgent):
    """Handles memory operations and user personalization"""

    def __init__(self, memory: MemorySystem, *, eager_legacy_brain: bool = False):
        super().__init__("memory", "Conversation history, preferences, and learning")
        self.memory = memory
        self._legacy_brain_allowed = bool(eager_legacy_brain)
        self._brain: Optional["HikariBrain"] = None
        if self._legacy_brain_allowed:
            from core.brain import HikariBrain

            self._brain = HikariBrain()

        self.register_tool("remember", self.remember)
        self.register_tool("recall", self.recall)
        self.register_tool("forget", self.forget)
        self.register_tool("search_memory", self.search_memory)
        self.register_tool("get_summary", self.get_summary)

    def _get_brain(self) -> "HikariBrain":
        if not self._legacy_brain_allowed:
            raise RuntimeError(
                "legacy HikariBrain is not available while Brain v2 policy is enabled"
            )
        if self._brain is None:
            from core.brain import HikariBrain

            self._brain = HikariBrain()
        return self._brain

    @property
    def brain(self) -> "HikariBrain":
        return self._get_brain()

    @brain.setter
    def brain(self, value: Optional["HikariBrain"]) -> None:
        self._brain = value

    def _is_personal_memory_routing(self, user_input: str) -> bool:
        if self._legacy_brain_allowed:
            brain = self._get_brain()
            return bool(brain.is_personal_memory_question(user_input))
        from core.brain_v2.recall_intent import should_skip_external_research

        return should_skip_external_research(user_input)

    def handle(self, user_input: str, context: str = "") -> Optional[str]:
        lowered = user_input.lower()

        if is_casual_call_me(user_input):
            return None

        if self._is_personal_memory_routing(user_input):
            if self._legacy_brain_allowed:
                ans = self._get_brain().answer(user_input)
                if ans:
                    return ans.text
            return None

        if is_household_memory_query(user_input):
            ans = answer_family_question(user_input, self.memory)
            if ans:
                return ans

        # Handle name updates
        if any(
            w in lowered
            for w in ["my name is", "call me", "update my name", "change my name"]
        ):
            import re

            match = re.search(
                r"(?:my name is|call me|update my name to|change my name to)\s+(\w+)",
                lowered,
            )
            if match:
                name = match.group(1).capitalize()
                self.memory.set_name(name)
                return f"Got it, {name}. I'll remember that from now on."

        # Handle location updates (city only — not whole sentences)
        if any(
            w in lowered
            for w in [
                "i am in",
                "i live in",
                "i'm in",
                "im in",
                "update my location",
                "change my location",
            ]
        ):
            return self.remember(user_input)

        # Handle remember commands
        if any(
            w in lowered
            for w in ["remember that", "remember this", "note that", "save this"]
        ) or ("remember" in lowered and "my sister" in lowered):
            fact = self._extract_remember_fact(user_input, lowered)
            if not fact and "my sister" in lowered:
                fact = user_input.strip()
            return self.remember(fact or user_input)

        # Handle "what do you know about me"
        if any(
            w in lowered
            for w in [
                "what do you know",
                "what do you remember",
                "what have i told you",
                "whats my name",
                "what's my name",
                "who am i",
            ]
        ):
            return self.get_summary()

        # Handle forget commands
        if any(w in lowered for w in ["forget", "remove from memory"]):
            key = (
                lowered.replace("forget", "").replace("remove from memory", "").strip()
            )
            return self.forget(key)

        # Handle memory search
        if any(
            w in lowered for w in ["search memory", "did i ask", "did we talk about"]
        ):
            query = (
                lowered.replace("search memory", "")
                .replace("did i ask", "")
                .replace("did we talk about", "")
                .strip()
            )
            return self.search_memory(query)

        return None

    def can_handle(self, user_input: str) -> float:
        lowered = user_input.lower()

        if is_casual_call_me(user_input):
            return 0.05

        if self._is_personal_memory_routing(user_input):
            return 0.98

        if is_household_memory_query(user_input):
            return 0.98

        if any(
            w in lowered
            for w in [
                "remember",
                "recall",
                "forget",
                "memory",
                "what do you know",
                "what do you remember",
            ]
        ):
            return 0.9

        if "flight" in lowered and "when" in lowered:
            return 0.96
        if any(
            w in lowered
            for w in [
                "my name",
                "who am i",
                "what's my name",
                "whats my name",
                "where do i live",
                "my location",
                "where am i",
                "update my",
                "change my",
                "set my",
            ]
        ):
            return 0.95
        # Handle casual location/name statements
        if any(
            w in lowered
            for w in [
                "i am in",
                "i live in",
                "i'm in",
                "im in",
                "call me",
                "my name is",
            ]
        ):
            return 0.9
        return 0.1

    def _extract_remember_fact(self, user_input: str, lowered: str) -> str:
        """Pull fact text from 'remember this/that' — including prefix before the phrase."""
        markers = [
            "remember that",
            "remember this",
            "note that",
            "save this",
            "save that",
            "don't forget",
        ]
        for marker in markers:
            if marker in lowered:
                idx = lowered.find(marker)
                after = user_input[idx + len(marker) :].strip().lstrip(":.,")
                if after:
                    return after
                before = user_input[:idx].strip().rstrip(".,;:")
                if before:
                    return before
        return ""

    def remember(self, fact: str) -> str:
        """Store a fact about the user"""
        if not fact:
            return "What would you like me to remember?"

        if len(fact.strip()) < 4:
            return (
                "I didn't catch what to save — say the full detail, "
                'e.g. "My flight to North City is July 3rd morning."'
            )

        key = fact[:50].lower().strip()
        self.memory.store_fact(key, fact)
        rel = ingest_family_statement(fact, self.memory)
        if self._legacy_brain_allowed:
            try:
                from core import neural_memory_bridge

                if neural_memory_bridge.init_neural_memory():
                    neural_memory_bridge.learn_from_text(fact)
                    ingest_family_statement(fact, self.memory, neural_memory_bridge)
            except Exception:
                pass
        if rel:
            from core.family_memory import get_family_record

            rec = get_family_record(self.memory, rel)
            name = rec.get("full_name") or rec.get("name")
            if name:
                return f"Got it. I'll remember your {rel} {name}."
        return f"I'll remember that: {fact}"

    def recall(self, key: str) -> str:
        """Recall a specific fact"""
        value = self.memory.get_fact(key)
        if value:
            return f"You told me: {value}"
        return f"I don't have that in memory."

    def forget(self, key: str) -> str:
        """Forget a specific fact"""
        if key in self.memory.facts:
            del self.memory.facts[key]
            self.memory._save()
            return f"Forgotten: {key}"
        return f"I don't have '{key}' in memory."

    def search_memory(self, query: str) -> str:
        """Search past conversations"""
        if not query:
            return "What should I search for?"

        results = self.memory.search_conversations(query)
        if results:
            parts = [f"Found {len(results)} matches:"]
            for r in results[-5:]:
                parts.append(f"\nYou: {r['user']}")
                parts.append(f"Me: {r['ai']}")
            return "\n".join(parts)
        return f"No memories found for '{query}'."

    def get_summary(self) -> str:
        """Get summary of what HIKARI knows"""
        summary = self.memory.get_user_summary()

        parts = ["Here's what I know about you:", ""]
        if summary["preferences"]:
            parts.append("Preferences:")
            for k, v in summary["preferences"].items():
                parts.append(f"  - {k}: {v}")
            parts.append("")

        if summary["facts_learned"] > 0:
            parts.append(f"Facts learned: {summary['facts_learned']}")
            parts.append(f"Total conversations: {summary['total_conversations']}")

        if summary["recent_topics"]:
            parts.append("\nRecent topics:")
            for topic in summary["recent_topics"][-5:]:
                parts.append(f"  - {topic}")

        return "\n".join(parts)
