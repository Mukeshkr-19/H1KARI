"""
HIKARI v2.0 - Code Agent
Programming assistance, debugging, code analysis
"""

import os
import re
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

from agents.base import BaseAgent


class CodeAgent(BaseAgent):
    """Handles coding tasks, debugging, and code analysis"""

    def __init__(self):
        super().__init__("code", "Programming assistance and code analysis")
        self.code_context = ""

        self.register_tool("analyze_code", self.analyze_code)
        self.register_tool("find_code", self.find_code)
        self.register_tool("explain_error", self.explain_error)

    def handle(self, user_input: str, context: str = "") -> Optional[str]:
        lowered = user_input.lower()

        if any(
            w in lowered
            for w in [
                "code",
                "program",
                "function",
                "debug",
                "script",
                "python",
                "javascript",
                "html",
                "css",
            ]
        ):
            return None  # Let the AI router handle this with smart model
        if any(
            w in lowered
            for w in ["error", "exception", "bug", "traceback", "stack trace"]
        ):
            return None  # Let AI handle
        if any(
            w in lowered
            for w in ["explain code", "what does this", "how does this work"]
        ):
            return None  # Let AI handle

        return None

    def can_handle(self, user_input: str) -> float:
        lowered = user_input.lower()
        code_keywords = [
            "code",
            "function",
            "class",
            "def",
            "import",
            "variable",
            "debug",
            "error",
            "exception",
            "bug",
            "traceback",
            "python",
            "javascript",
            "typescript",
            "html",
            "css",
            "react",
            "api",
            "endpoint",
            "server",
            "database",
            "sql",
            "algorithm",
            "data structure",
            "loop",
            "condition",
        ]
        if any(w in lowered for w in code_keywords):
            return 0.8
        return 0.2

    def analyze_code(self, code: str) -> str:
        """Analyze a code snippet"""
        if not code:
            return "Please provide the code you'd like me to analyze."

        lines = code.strip().split("\n")
        info = [
            f"Lines: {len(lines)}",
            f"Characters: {len(code)}",
        ]

        # Detect language
        if "def " in code or "import " in code or "class " in code:
            info.append("Language: Python (detected)")
        elif (
            "function " in code or "const " in code or "let " in code or "var " in code
        ):
            info.append("Language: JavaScript (detected)")
        elif "<html" in code.lower() or "<div" in code.lower():
            info.append("Language: HTML (detected)")

        return "\n".join(info)

    def find_code(self, query: str, search_dir: str = None) -> str:
        """Find code patterns in files"""
        search_path = Path(search_dir or os.getcwd())
        results = []

        try:
            for ext in ["*.py", "*.js", "*.ts", "*.jsx", "*.tsx"]:
                for file_path in search_path.rglob(ext):
                    if file_path.name.startswith("."):
                        continue
                    try:
                        content = file_path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                        if query.lower() in content.lower():
                            # Find matching lines
                            matching_lines = []
                            for i, line in enumerate(content.split("\n"), 1):
                                if query.lower() in line.lower():
                                    matching_lines.append(
                                        f"  Line {i}: {line.strip()[:80]}"
                                    )
                                    if len(matching_lines) >= 3:
                                        break
                            rel_path = file_path.relative_to(search_path)
                            results.append(f"\n{rel_path}:")
                            results.extend(matching_lines)
                            if len(results) >= 30:
                                break
                    except (PermissionError, UnicodeDecodeError):
                        continue

            if results:
                return f"Found '{query}' in:\n" + "\n".join(results[:30])
            return f"No code found matching '{query}'"

        except Exception as e:
            return f"Search error: {str(e)}"

    def explain_error(self, error: str) -> str:
        """Provide context for an error message"""
        if not error:
            return "Please share the error message you'd like help with."

        explanations = {
            "syntaxerror": "This is a syntax error - check for missing colons, parentheses, or quotes.",
            "nameerror": "This means a variable or function name is not defined. Check for typos.",
            "typeerror": "This happens when you use the wrong type. Check what type your variables are.",
            "indexerror": "You're trying to access an index that doesn't exist. Check your list/array bounds.",
            "keyerror": "You're trying to access a dictionary key that doesn't exist.",
            "attributeerror": "You're trying to access an attribute that doesn't exist on this object.",
            "importerror": "The module you're trying to import isn't installed or doesn't exist.",
            "modulenotfounderror": "The module isn't installed. Try: pip install <module_name>",
            "valueerror": "The value you passed has the right type but is inappropriate.",
            "filenotfounderror": "The file or directory you're looking for doesn't exist at that path.",
            "permissionerror": "You don't have permission to access this file or directory.",
            "connectionerror": "Network connection issue. Check your internet connection.",
            "timeouterror": "The operation took too long. The server might be slow or unreachable.",
        }

        error_lower = error.lower()
        for error_type, explanation in explanations.items():
            if error_type in error_lower:
                return f"Common cause: {explanation}\n\nFull error: {error[:500]}"

        return f"I'll help you debug this. Here's the error:\n{error[:500]}"
