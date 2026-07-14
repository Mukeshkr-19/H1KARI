"""
HIKARI v2.0 - Skill System
Extensible skill framework for adding new capabilities
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from datetime import datetime
import ast
import operator

from core.quiet import debug


class Skill(ABC):
    """Base class for all skills"""

    def __init__(self, name: str, description: str, version: str = "1.0.0"):
        self.name = name
        self.description = description
        self.version = version
        self.enabled = True
        self.usage_count = 0
        self.last_used = None

    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """Execute the skill"""
        pass

    @abstractmethod
    def can_handle(self, user_input: str) -> float:
        """Return confidence (0-1) that this skill can handle the input"""
        pass

    def on_use(self):
        """Called when skill is used"""
        self.usage_count += 1
        self.last_used = datetime.now().isoformat()

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "enabled": self.enabled,
            "usage_count": self.usage_count,
            "last_used": self.last_used,
        }


class SkillRegistry:
    """Manages all registered skills"""

    def __init__(self):
        self.skills: Dict[str, Skill] = {}

    def register(self, skill: Skill):
        """Register a skill"""
        self.skills[skill.name] = skill
        debug(f"[SKILL] Registered: {skill.name} v{skill.version}")

    def unregister(self, name: str):
        """Unregister a skill"""
        if name in self.skills:
            del self.skills[name]
            debug(f"[SKILL] Unregistered: {name}")

    def find_best_skill(self, user_input: str) -> Optional[Skill]:
        """Find the best skill for the input"""
        best_skill = None
        best_score = 0

        for skill in self.skills.values():
            if not skill.enabled:
                continue
            score = skill.can_handle(user_input)
            if score > best_score:
                best_score = score
                best_skill = skill

        return best_skill if best_score > 0.5 else None

    def execute_skill(self, name: str, **kwargs) -> Any:
        """Execute a specific skill"""
        if name not in self.skills:
            return f"Skill '{name}' not found"

        skill = self.skills[name]
        if not skill.enabled:
            return f"Skill '{name}' is disabled"

        skill.on_use()
        return skill.execute(**kwargs)

    def get_all_skills(self) -> List[Dict]:
        return [s.get_status() for s in self.skills.values()]

    def enable(self, name: str):
        if name in self.skills:
            self.skills[name].enabled = True

    def disable(self, name: str):
        if name in self.skills:
            self.skills[name].enabled = False


# Built-in skills
class TimerSkill(Skill):
    """Set timers and alarms"""

    def __init__(self):
        super().__init__("timer", "Set timers and alarms")
        self._timers = []

    def execute(self, **kwargs) -> Any:
        minutes = kwargs.get("minutes", 0)
        if minutes:
            import threading

            def timer_done():
                print(f"[TIMER] {minutes} minute timer done!")

            threading.Timer(minutes * 60, timer_done).start()
            return f"Timer set for {minutes} minutes"
        return "How many minutes?"

    def can_handle(self, user_input: str) -> float:
        if any(
            w in user_input.lower()
            for w in ["timer", "alarm", "remind me in", "wake me"]
        ):
            return 0.9
        return 0.1


class CalculatorSkill(Skill):
    """Quick calculations"""

    MAX_EXPRESSION_LENGTH = 200
    MAX_AST_NODES = 80

    _OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def __init__(self):
        super().__init__("calculator", "Quick math calculations")

    def execute(self, **kwargs) -> Any:
        expression = kwargs.get("expression", "")
        try:
            result = self._eval_math(expression)
            return f"{expression} = {result}"
        except (SyntaxError, ValueError):
            return "Invalid expression"
        except Exception:
            return "Couldn't calculate that"

    def _eval_math(self, expression: str):
        if not isinstance(expression, str) or len(expression) > self.MAX_EXPRESSION_LENGTH:
            raise ValueError("expression too large")
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > self.MAX_AST_NODES:
            raise ValueError("expression too complex")
        return self._eval_node(tree.body)

    def _eval_node(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in self._OPS:
            return self._OPS[type(node.op)](
                self._eval_node(node.left), self._eval_node(node.right)
            )
        if isinstance(node, ast.UnaryOp) and type(node.op) in self._OPS:
            return self._OPS[type(node.op)](self._eval_node(node.operand))
        raise ValueError("unsupported expression")

    def can_handle(self, user_input: str) -> float:
        import re

        if re.search(r"\d+\s*[\+\-\*/]\s*\d+", user_input):
            return 0.9
        if any(
            w in user_input.lower()
            for w in ["calculate", "what is", "times", "divided by"]
        ):
            return 0.7
        return 0.2


class JokeSkill(Skill):
    """Tell jokes"""

    def __init__(self):
        super().__init__("joke", "Tell jokes and funny content")
        self.jokes = [
            "Why do programmers prefer dark mode? Because light attracts bugs!",
            "Why was the JavaScript developer sad? Because he didn't Node how to Express himself!",
            "What's a computer's favorite snack? Microchips!",
            "Why did the developer go broke? Because he used up all his cache!",
            "How do trees access the internet? They log in!",
            "Why do Java developers wear glasses? Because they can't C#!",
            "What's a robot's favorite type of music? Heavy metal!",
        ]

    def execute(self, **kwargs) -> Any:
        import random

        return random.choice(self.jokes)

    def can_handle(self, user_input: str) -> float:
        if any(
            w in user_input.lower() for w in ["joke", "funny", "make me laugh", "humor"]
        ):
            return 0.95
        return 0.1


def register_builtin_skills(registry: SkillRegistry):
    """Register all built-in skills"""
    registry.register(TimerSkill())
    registry.register(CalculatorSkill())
    registry.register(JokeSkill())
