"""
HIKARI Build Executor - Build projects using OpenCode
Integrates OpenCode as HIKARI's coding companion (instead of Claude Code)

Features:
- Create new projects from natural language
- Fix/refactor existing projects
- Clarifying questions before building
- Opens Terminal with OpenCode
"""

import asyncio
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, List

log = logging.getLogger("hikari.build")

log = logging.getLogger("hikari.build")

DESKTOP = Path.home() / "Desktop"
PROJECTS_DIR = Path.home() / "HIKARI-projects"

PROJECTS_DIR.mkdir(exist_ok=True)

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
    "skip",
    "do it",
]

SMART_DEFAULTS = {
    "landing page": {
        "tech": "React + Tailwind CSS",
        "sections": "hero, features, pricing, footer",
    },
    "website": {"tech": "HTML + CSS + JS", "sections": "header, main, footer"},
    "api": {"tech": "Python FastAPI", "endpoints": "CRUD"},
    "app": {"tech": "React + Vite", "features": "basic"},
    "bot": {"tech": "Python", "features": "basic commands"},
}


class BuildPlan:
    """Build plan data class."""

    def __init__(
        self,
        task_type: str,
        original_request: str,
        project_name: str = "",
        project_path: str = "",
        tech_stack: str = "",
        details: str = "",
        target_file: str = "",
        confirmed: bool = False,
        skipped: bool = False,
    ):
        self.task_type = task_type
        self.original_request = original_request
        self.project_name = project_name
        self.project_path = project_path
        self.tech_stack = tech_stack
        self.details = details
        self.target_file = target_file
        self.confirmed = confirmed
        self.skipped = skipped


async def run_applescript(script: str, timeout: float = 10.0) -> Dict[str, Any]:
    """Run AppleScript."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {"success": proc.returncode == 0, "stdout": stdout.decode().strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}


def generate_project_name(request: str) -> str:
    """Generate project folder name from request."""
    request = request.lower()

    quoted = re.search(r'"([^"]+)"', request)
    if quoted:
        return quoted.group(1).strip().replace(" ", "-")

    called = re.search(r"(?:called|named)\s+(\S+)", request, re.IGNORECASE)
    if called:
        return called.group(1).lower().replace(" ", "-")

    words = re.sub(r"[^a-zA-Z0-9\s]", "", request).split()
    skip = {
        "a",
        "the",
        "an",
        "me",
        "build",
        "create",
        "make",
        "set",
        "up",
        "for",
        "with",
        "and",
        "to",
        "of",
        "i",
        "want",
        "need",
        "new",
        "project",
        "directory",
        "called",
        "web",
        "page",
        "site",
        "app",
        "simple",
        "basic",
        "my",
        "please",
    }
    meaningful = [w for w in words if w not in skip and len(w) > 2][:4]

    return "-".join(meaningful) if meaningful else "hikari-project"


def detect_tech_stack(request: str) -> str:
    """Detect tech stack from request."""
    request = request.lower()

    if "react" in request:
        return "React + Tailwind CSS"
    elif "vue" in request:
        return "Vue.js + Tailwind"
    elif "angular" in request:
        return "Angular"
    elif "python" in request or "fastapi" in request or "flask" in request:
        return "Python"
    elif "django" in request:
        return "Django"
    elif "node" in request or "express" in request:
        return "Node.js + Express"
    elif "html" in request:
        return "HTML + CSS + JavaScript"
    elif "rust" in request:
        return "Rust"
    elif "go" in request or "golang" in request:
        return "Go"

    for key, value in SMART_DEFAULTS.items():
        if key in request:
            return value.get("tech", "")

    return ""


def classify_request(request: str) -> str:
    """Classify the type of request."""
    request = request.lower()

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
        "issue",
    ]
    for word in fix_words:
        if word in request:
            return "fix"

    refactor_words = ["refactor", "clean up", "restructure", "reorganize", "optimize"]
    for word in refactor_words:
        if word in request:
            return "refactor"

    feature_words = ["add", "feature", "implement", "new", "enhance", "upgrade"]
    for word in feature_words:
        if word in request:
            return "feature"

    build_words = [
        "build",
        "create",
        "make",
        "scaffold",
        "generate",
        "new project",
        "new app",
    ]
    for word in build_words:
        if word in request:
            return "build"

    return "chat"


async def create_project(
    request: str, is_existing: bool = False, project_path: str = ""
) -> Dict[str, Any]:
    """Create a new project or work on existing one."""

    task_type = classify_request(request)
    project_name = ""

    if is_existing and project_path:
        project_path = os.path.expanduser(project_path)
        project_name = Path(project_path).name
    else:
        project_name = generate_project_name(request)
        project_path = str(PROJECTS_DIR / project_name)
        os.makedirs(project_path, exist_ok=True)

    tech_stack = detect_tech_stack(request)

    claude_md = f"""# Task

{request}

## Project
Name: {project_name}
Path: {project_path}

## Tech Stack
{tech_stack if tech_stack else "Use your best judgment"}

## Requirements
- Build completely working code
- If web app, make index.html work standalone
- No placeholder comments
- Clean, readable code
- Include instructions to run
"""

    claude_path = Path(project_path) / "CLAUDE.md"
    claude_path.write_text(claude_md)

    return {
        "task_type": task_type,
        "project_name": project_name,
        "project_path": project_path,
        "tech_stack": tech_stack,
    }


async def open_opencode(project_path: str) -> Dict[str, Any]:
    """Open OpenCode in the project directory."""

    escaped_path = project_path.replace('"', '\\"')

    script = f"""
tell application "Terminal"
    activate
    do script "cd '{escaped_path}' && opencode ."
end tell
"""

    result = await run_applescript(script)

    return {
        "success": result["success"],
        "confirmation": f"Opening OpenCode in {Path(project_path).name}. Watch the Terminal!",
    }


async def open_in_existing_project(project_path: str, request: str) -> Dict[str, Any]:
    """Open OpenCode in an existing project to fix/enhance."""

    project_path = os.path.expanduser(project_path)

    if not os.path.exists(project_path):
        return {
            "success": False,
            "confirmation": f"Project not found at {project_path}",
        }

    claude_path = Path(project_path) / "CLAUDE.md"
    existing = claude_path.read_text() if claude_path.exists() else ""

    new_task = f"""

## New Task

{request}

Please complete this task in the existing project.
"""

    claude_path.write_text(existing + new_task)

    return await open_opencode(project_path)


BUILD_QUESTIONS = [
    {
        "key": "tech",
        "q": "Any tech preferences? (React, Python, Vue, etc)",
        "default": "",
    },
    {"key": "details", "q": "Any specific features or sections?", "default": ""},
]


class BuildExecutor:
    """Execute builds using OpenCode."""

    def __init__(self):
        self.active_plan: Optional[Dict[str, Any]] = None

    def start_build_flow_sync(self, request: str) -> Dict[str, Any]:
        """Synchronous wrapper for build flow."""
        import asyncio

        return asyncio.run(self.start_build_flow(request))

    async def start_build_flow(
        self, request: str, is_existing: bool = False, path: str = ""
    ) -> Dict[str, Any]:
        """Start the build planning flow."""

        task_type = classify_request(request)

        if task_type == "chat":
            return {"type": "chat", "needs_planning": False}

        if is_existing and path:
            return await self._handle_existing_project(request, path)

        if any(bypass in request.lower() for bypass in BYPASS_PHRASES):
            return await self._execute_build(request, {})

        tech = detect_tech_stack(request)
        if tech:
            return await self._execute_build(request, {"tech": tech})

        return {
            "type": "planning",
            "task_type": task_type,
            "question": BUILD_QUESTIONS[0]["q"],
            "needs_question": True,
        }

    async def process_answer(self, answer: str) -> Dict[str, Any]:
        """Process answer to clarifying question."""

        answer_lower = answer.lower()

        if any(bypass in answer_lower for bypass in BYPASS_PHRASES):
            return await self._execute_build(
                self.active_plan["request"], self.active_plan.get("answers", {})
            )

        if not self.active_plan:
            return {"type": "chat", "message": "No active build plan."}

        answers = self.active_plan.get("answers", {})
        answers[BUILD_QUESTIONS[0]["key"]] = answer
        self.active_plan["answers"] = answers

        return await self._execute_build(self.active_plan["request"], answers)

    async def _handle_existing_project(self, request: str, path: str) -> Dict[str, Any]:
        """Handle work on existing project."""

        task_type = classify_request(request)

        result = await open_in_existing_project(path, request)

        return {
            "type": "executing",
            "task_type": task_type,
            "project_path": path,
            "confirmation": result["confirmation"],
            "action": "OpenCode opened in your project! Tell me what to do.",
        }

    async def _execute_build(
        self, request: str, answers: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute the build."""

        result = await create_project(request)

        open_result = await open_opencode(result["project_path"])

        return {
            "type": "executing",
            "task_type": result["task_type"],
            "project_name": result["project_name"],
            "project_path": result["project_path"],
            "confirmation": open_result["confirmation"],
        }

    async def open_existing(self, path: str, task: str = "") -> Dict[str, Any]:
        """Open existing project for work."""

        path = os.path.expanduser(path)

        if not os.path.exists(path):
            return {"success": False, "confirmation": f"Project not found: {path}"}

        if task:
            return await open_in_existing_project(path, task)

        return await open_opencode(path)

    def get_active_plan(self) -> Optional[Dict[str, Any]]:
        return self.active_plan

    def clear_plan(self):
        self.active_plan = None


_build_instance = None


def get_build_executor():
    global _build_instance
    if _build_instance is None:
        _build_instance = BuildExecutor()
    return _build_instance
