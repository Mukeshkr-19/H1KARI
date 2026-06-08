"""
HIKARI v2.0 - File Agent
Secure file system access, document reading, searching
"""

import os
import re
import sys
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

from agents.base import BaseAgent

# Default whitelisted directories
DEFAULT_WHITELIST = [
    str(Path.home() / "Documents"),
    str(Path.home() / "Desktop"),
    str(Path.home() / "Downloads"),
    str(Path.home() / "Projects"),
]

# Allowed file extensions for reading
ALLOWED_EXTENSIONS = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".csv",
    ".log",
    ".xml",
    ".toml",
    ".ini",
    ".cfg",
    ".pdf",
    ".docx",
    ".rtf",
}


class FileAgent(BaseAgent):
    """Secure file system access agent"""

    def __init__(self, whitelist: List[str] = None):
        super().__init__("files", "File system access and document analysis")
        self.whitelist = [Path(p).resolve() for p in (whitelist or DEFAULT_WHITELIST)]
        self.max_read_size = 100_000  # 100KB max read
        self.access_log = []

        self.register_tool("read_file", self.read_file)
        self.register_tool("search_files", self.search_files)
        self.register_tool("list_files", self.list_files)
        self.register_tool("file_info", self.file_info)

    def handle(self, user_input: str, context: str = "") -> Optional[str]:
        lowered = user_input.lower()

        if any(w in lowered for w in ["read", "open file", "show me", "show file"]):
            path = self._extract_path(lowered)
            if path:
                return self.read_file(path)
        if any(w in lowered for w in ["search file", "find file", "look for"]):
            query = self._extract_query(lowered)
            if query:
                return self.search_files(query)
        if any(w in lowered for w in ["list files", "what files", "show files"]):
            path = self._extract_path(lowered) or str(Path.home() / "Documents")
            return self.list_files(path)

        return None

    def can_handle(self, user_input: str) -> float:
        lowered = user_input.lower()
        if any(
            w in lowered
            for w in ["file", "document", "read", "open file", "search in", "find in"]
        ):
            return 0.85
        return 0.15

    def _is_path_allowed(self, path: str) -> tuple[bool, str]:
        """Check if path is within whitelisted directories"""
        try:
            resolved = Path(path).resolve()
        except Exception as e:
            return False, f"Invalid path: {e}"

        for allowed in self.whitelist:
            try:
                resolved.relative_to(allowed)
                return True, str(resolved)
            except ValueError:
                continue

        return False, f"Access denied: {resolved} is not in whitelisted directories"

    def read_file(self, path: str) -> str:
        """Read a file securely"""
        allowed, resolved = self._is_path_allowed(path)
        if not allowed:
            return resolved

        try:
            file_path = Path(resolved)
            if not file_path.exists():
                return f"File not found: {resolved}"
            if not file_path.is_file():
                return f"Not a file: {resolved}"

            ext = file_path.suffix.lower()
            if ext not in ALLOWED_EXTENSIONS and ext not in [".pdf", ".docx"]:
                return f"File type {ext} not supported for reading"

            # Check file size
            size = file_path.stat().st_size
            if size > self.max_read_size:
                return f"File too large ({size / 1024:.1f}KB). Max: {self.max_read_size / 1024:.1f}KB"

            content = file_path.read_text(encoding="utf-8", errors="replace")
            self._log_access("read", resolved)

            lines = content.split("\n")
            if len(lines) > 100:
                preview = "\n".join(lines[:100])
                return f"File: {resolved}\n(Showing first 100 of {len(lines)} lines)\n\n{preview}"
            return f"File: {resolved}\n\n{content}"

        except PermissionError:
            return f"Permission denied: {resolved}"
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def search_files(self, query: str, search_dir: str = None) -> str:
        """Search for files by name or content"""
        allowed, resolved = self._is_path_allowed(
            search_dir or str(Path.home() / "Documents")
        )
        if not allowed:
            return resolved

        results = []
        search_path = Path(resolved)

        try:
            # Search by filename
            for pattern in [f"*{query}*", f"*{query.lower()}*"]:
                for file_path in search_path.rglob(pattern):
                    if (
                        file_path.is_file()
                        and file_path.suffix.lower() in ALLOWED_EXTENSIONS
                    ):
                        results.append(str(file_path.relative_to(search_path)))
                        if len(results) >= 20:
                            break
                if len(results) >= 20:
                    break

            if results:
                return f"Found {len(results)} files matching '{query}':\n" + "\n".join(
                    f"- {r}" for r in results[:20]
                )
            return f"No files found matching '{query}' in {resolved}"

        except PermissionError:
            return f"Permission denied searching: {resolved}"
        except Exception as e:
            return f"Search error: {str(e)}"

    def list_files(self, path: str) -> str:
        """List files in a directory"""
        allowed, resolved = self._is_path_allowed(path)
        if not allowed:
            return resolved

        try:
            dir_path = Path(resolved)
            if not dir_path.exists():
                return f"Directory not found: {resolved}"
            if not dir_path.is_dir():
                return f"Not a directory: {resolved}"

            entries = []
            for entry in sorted(dir_path.iterdir()):
                prefix = "📁 " if entry.is_dir() else "📄 "
                size = ""
                if entry.is_file():
                    size = f" ({entry.stat().st_size / 1024:.1f}KB)"
                entries.append(f"{prefix}{entry.name}{size}")

            self._log_access("list", resolved)
            return f"Contents of {resolved}:\n" + "\n".join(entries[:50])

        except PermissionError:
            return f"Permission denied: {resolved}"
        except Exception as e:
            return f"Error listing directory: {str(e)}"

    def file_info(self, path: str) -> str:
        """Get file information"""
        allowed, resolved = self._is_path_allowed(path)
        if not allowed:
            return resolved

        try:
            file_path = Path(resolved)
            if not file_path.exists():
                return f"Not found: {resolved}"

            stat = file_path.stat()
            info = [
                f"Name: {file_path.name}",
                f"Path: {resolved}",
                f"Type: {'Directory' if file_path.is_dir() else 'File'}",
                f"Size: {stat.st_size / 1024:.1f}KB",
                f"Modified: {datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')}",
                f"Created: {datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M')}",
            ]
            return "\n".join(info)
        except Exception as e:
            return f"Error: {str(e)}"

    def _extract_path(self, text: str) -> str:
        """Extract file path from text"""
        # Look for quoted paths
        match = re.search(r'["\'](.+?)["\']', text)
        if match:
            return match.group(1)
        return ""

    def _extract_query(self, text: str) -> str:
        """Extract search query"""
        for prefix in ["search for", "find", "look for", "search"]:
            if prefix in text:
                return text.split(prefix)[-1].strip()
        return ""

    def _log_access(self, action: str, path: str):
        """Log file access for audit"""
        self.access_log.append(
            {
                "action": action,
                "path": path,
                "time": datetime.now().isoformat(),
            }
        )
        if len(self.access_log) > 100:
            self.access_log = self.access_log[-100:]

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        status.update(
            {
                "whitelist": [str(p) for p in self.whitelist],
                "recent_access": self.access_log[-5:],
            }
        )
        return status
