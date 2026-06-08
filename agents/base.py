"""
HIKARI v2.0 - Base Agent Class
All agents inherit from this
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from datetime import datetime


class BaseAgent(ABC):
    """Base class for all HIKARI agents"""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.is_active = True
        self.tools: Dict[str, callable] = {}
        self.last_action: Optional[str] = None
        self.action_count = 0

    @abstractmethod
    def handle(self, user_input: str, context: str = "") -> Optional[str]:
        """Process user input and return response"""
        pass

    def can_handle(self, user_input: str) -> float:
        """Return confidence score (0-1) that this agent can handle the input"""
        return 0.5  # Default neutral confidence

    def register_tool(self, name: str, func: callable):
        """Register a tool/capability"""
        self.tools[name] = func

    def execute_tool(self, name: str, **kwargs) -> Any:
        """Execute a registered tool"""
        if name in self.tools:
            self.last_action = f"{name}({kwargs})"
            self.action_count += 1
            return self.tools[name](**kwargs)
        return None

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "is_active": self.is_active,
            "action_count": self.action_count,
            "last_action": self.last_action,
            "tools": list(self.tools.keys()),
        }

    def __repr__(self):
        return f"Agent({self.name}, active={self.is_active})"
