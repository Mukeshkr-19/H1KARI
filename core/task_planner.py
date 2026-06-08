"""
HIKARI Task Planner - Conversational planning before executing tasks
Inspired by JARVIS planner (ethanplusai/jarvis)

Features:
- Intent detection (build, fix, research, chat)
- Clarifying questions before execution
- Plan confirmation flow
- Smart defaults
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

log = logging.getLogger("hikari.planner")

DESKTOP_PATH = ""


BYPASS_PHRASES = [
    "just do it",
    "figure it out",
    "just go",
    "skip planning",
    "don't ask",
    "stop asking",
    "yep just go",
    "just build it",
    "wing it",
    "surprise me",
    "do your thing",
    "go ahead",
    "yes",
    "yeah",
    "yep",
    "proceed",
]

SMART_DEFAULTS = {
    "build": {
        "tech_stack": "React + Tailwind",
        "design": "Modern, clean aesthetic",
    },
    "fix": {
        "approach": "Diagnose and fix in-place",
    },
    "research": {
        "depth": "comprehensive",
        "output": "summary",
    },
    "refactor": {
        "goal": "readability",
    },
}


@dataclass
class PlanningDecision:
    """Result of analyzing whether a request needs planning."""

    needs_planning: bool
    task_type: str
    confidence: float
    missing_info: List[str] = field(default_factory=list)


@dataclass
class Plan:
    """A plan being built through conversation."""

    task_type: str
    original_request: str
    answers: Dict[str, str] = field(default_factory=dict)
    pending_questions: List[Dict[str, str]] = field(default_factory=list)
    current_question_index: int = 0
    confirmed: bool = False
    skipped: bool = False

    @property
    def is_complete(self) -> bool:
        return self.skipped or self.current_question_index >= len(
            self.pending_questions
        )

    def current_question(self) -> Optional[Dict[str, str]]:
        if self.current_question_index < len(self.pending_questions):
            return self.pending_questions[self.current_question_index]
        return None


QUESTION_MAP = {
    "build": [
        {
            "key": "tech_stack",
            "q": "Any tech preferences? (React, Python, etc)",
            "default": "React + Tailwind",
        },
        {"key": "details", "q": "Any specific features or sections?", "default": None},
    ],
    "fix": [
        {"key": "error", "q": "What error are you seeing?", "default": None},
        {"key": "expected", "q": "What should it do instead?", "default": None},
    ],
    "research": [
        {"key": "depth", "q": "Quick overview or deep dive?", "default": "quick"},
        {"key": "output", "q": "Summary or detailed report?", "default": "summary"},
    ],
    "refactor": [
        {"key": "target", "q": "Which file or module?", "default": None},
        {
            "key": "goal",
            "q": "Goal: performance, readability, or structure?",
            "default": "readability",
        },
    ],
}


class TaskPlanner:
    """Manages the planning conversation before executing tasks."""

    def __init__(self):
        self.active_plan: Optional[Plan] = None

    @property
    def is_planning(self) -> bool:
        return self.active_plan is not None and not self.active_plan.confirmed

    async def start_planning(self, user_request: str) -> Dict[str, Any]:
        """Analyze request and determine what questions to ask."""

        classification = self._classify_request(user_request)
        task_type = classification.get("task_type", "chat")

        # If it's just chat, no planning needed
        if task_type == "chat":
            return {
                "task_type": "chat",
                "needs_questions": False,
                "confirmation": None,
            }

        # Build question list for this task type
        questions = list(QUESTION_MAP.get(task_type, []))

        self.active_plan = Plan(
            task_type=task_type,
            original_request=user_request,
            answers={},
            pending_questions=questions,
        )

        first_question = None
        if questions:
            first_question = questions[0]["q"]

        return {
            "task_type": task_type,
            "needs_questions": len(questions) > 0,
            "first_question": first_question,
        }

    async def process_answer(self, answer: str) -> Dict[str, Any]:
        """Process user's answer to a clarifying question."""

        plan = self.active_plan
        if not plan:
            return {"next_question": None, "plan_complete": False}

        answer_lower = answer.lower().strip()

        # Check for bypass
        if any(phrase in answer_lower for phrase in BYPASS_PHRASES):
            plan.skipped = True
            for q in plan.pending_questions[plan.current_question_index :]:
                if q.get("default") and q["key"] not in plan.answers:
                    plan.answers[q["key"]] = q["default"]
            return await self._get_confirmation()

        # Record answer
        current_q = plan.current_question()
        if current_q:
            plan.answers[current_q["key"]] = answer
            plan.current_question_index += 1

        # Check for more questions
        next_q = plan.current_question()
        if next_q:
            return {
                "next_question": next_q["q"],
                "plan_complete": False,
            }

        return await self._get_confirmation()

    async def handle_confirmation(self, answer: str) -> Dict[str, Any]:
        """Handle yes/no response to confirmation."""

        plan = self.active_plan
        if not plan:
            return {"confirmed": False, "cancelled": True}

        answer_lower = answer.lower().strip()

        yes_phrases = ["yes", "yeah", "yep", "do it", "proceed", "go", "confirmed"]
        no_phrases = ["no", "nope", "cancel", "stop", "nevermind", "forget it"]

        if any(phrase in answer_lower for phrase in yes_phrases):
            plan.confirmed = True
            return {"confirmed": True, "cancelled": False}

        if any(phrase in answer_lower for phrase in no_phrases):
            self.active_plan = None
            return {"confirmed": False, "cancelled": True}

        # Modify - add to answers and re-confirm
        plan.answers["modification"] = answer
        return await self._get_confirmation()

    async def _get_confirmation(self) -> Dict[str, Any]:
        """Generate confirmation summary."""

        plan = self.active_plan
        if not plan:
            return {"next_question": None, "plan_complete": False}

        # Build summary
        parts = []

        action_verb = {
            "build": "Create",
            "fix": "Fix",
            "research": "Research",
            "refactor": "Refactor",
        }.get(plan.task_type, "Work on")

        parts.append(action_verb)

        if plan.answers.get("details"):
            parts.append(plan.answers["details"])
        else:
            # Clean up request
            clean = plan.original_request.lower()
            for prefix in [
                "yeah ",
                "i just want to ",
                "can you ",
                "i want to ",
                "i need to ",
            ]:
                if clean.startswith(prefix):
                    clean = clean[len(prefix) :]
            parts.append(clean[:100])

        if plan.answers.get("tech_stack"):
            parts.append(f"using {plan.answers['tech_stack']}")

        summary = " ".join(parts) + ". Shall I proceed?"

        return {
            "next_question": None,
            "plan_complete": True,
            "needs_confirmation": True,
            "confirmation_summary": summary,
        }

    def _classify_request(self, text: str) -> Dict[str, Any]:
        """Classify request type using keyword matching."""

        text_lower = text.lower()

        # Build requests
        build_words = [
            "build",
            "create",
            "make",
            "set up",
            "scaffold",
            "generate",
            "new project",
            "new app",
        ]
        for word in build_words:
            if word in text_lower:
                return {"task_type": "build", "confidence": 0.8}

        # Fix requests
        fix_words = [
            "fix",
            "debug",
            "repair",
            "patch",
            "resolve",
            "broken",
            "error",
            "bug",
            "not working",
        ]
        for word in fix_words:
            if word in text_lower:
                return {"task_type": "fix", "confidence": 0.8}

        # Research requests
        research_words = [
            "research",
            "look into",
            "investigate",
            "analyze",
            "compare",
            "find out",
            "search for",
        ]
        for word in research_words:
            if word in text_lower:
                return {"task_type": "research", "confidence": 0.8}

        # Refactor requests
        refactor_words = [
            "refactor",
            "clean up",
            "restructure",
            "reorganize",
            "optimize",
        ]
        for word in refactor_words:
            if word in text_lower:
                return {"task_type": "refactor", "confidence": 0.8}

        # Default to chat
        return {"task_type": "chat", "confidence": 0.9}

    def get_final_prompt(self) -> str:
        """Get the finalized prompt from the plan."""

        plan = self.active_plan
        if not plan:
            return ""

        parts = [plan.original_request]

        if plan.answers.get("tech_stack"):
            parts.append(f"Tech stack: {plan.answers['tech_stack']}")

        if plan.answers.get("details"):
            parts.append(f"Details: {plan.answers['details']}")

        if plan.answers.get("target"):
            parts.append(f"Target: {plan.answers['target']}")

        return " | ".join(parts)

    def reset(self):
        """Clear the active plan."""
        self.active_plan = None


# Singleton
_planner_instance = None


def get_task_planner():
    """Get the task planner singleton."""
    global _planner_instance
    if _planner_instance is None:
        _planner_instance = TaskPlanner()
    return _planner_instance
