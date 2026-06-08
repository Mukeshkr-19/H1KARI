"""
HIKARI - Memory Skills
Unified persistent memory system via skills (not AI agents)
"""

import os
import json
import re
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path

from core.runtime_paths import legacy_data_dir
from skills.skill_system import Skill

MEMORY_DIR = legacy_data_dir() / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_FILE = MEMORY_DIR / "memories.json"
NOTES_DIR = MEMORY_DIR / "notes"
CONVERSATION_FILE = MEMORY_DIR / "conversation.json"
TOPICS_FILE = MEMORY_DIR / "topics.json"

NOTES_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(filepath, default=dict):
    """Load JSON file safely"""
    try:
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return default()


def _save_json(filepath, data):
    """Save JSON file"""
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)


class MemorySkill(Skill):
    """
    Unified memory skill - stores and retrieves persistent memories.
    NOT session-based - lives in private legacy-data/memory/
    """

    def __init__(self):
        super().__init__(
            name="memory",
            description="Store and retrieve persistent memories about the user",
            version="1.0.0",
        )
        self.memories = _load_json(MEMORY_FILE, list)
        self.topics = _load_json(TOPICS_FILE, dict)

    def execute(self, **kwargs) -> Any:
        """Execute memory operation"""
        action = kwargs.get("action", "recall")
        query = kwargs.get("query", "")

        if action == "store":
            return self._store_memory(kwargs)
        elif action == "recall":
            return self._recall_memory(query)
        elif action == "search":
            return self._search_memory(query)
        elif action == "topics":
            return self._get_topics()
        elif action == "what_do_you_know":
            return self._what_do_you_know()
        else:
            return self._recall_memory(query)

    def _store_memory(self, kwargs) -> str:
        """Store a new memory"""
        content = kwargs.get("content", "")
        category = kwargs.get("category", "general")

        if not content:
            return "What should I remember?"

        memory = {
            "id": len(self.memories) + 1,
            "content": content,
            "category": category,
            "timestamp": datetime.now().isoformat(),
            "source": kwargs.get("source", "voice"),
        }

        self.memories.append(memory)
        _save_json(MEMORY_FILE, self.memories)

        # Update topic tracking
        self._update_topics(content, category)

        self.on_use()
        return f"Got it! I've remembered: {content}"

    def _recall_memory(self, query: str) -> str:
        """Recall memories related to query"""
        if not query:
            # Return recent memories
            recent = self.memories[-5:] if self.memories else []
            if not recent:
                return (
                    "I don't have any memories yet. Tell me something about yourself!"
                )
            lines = ["Here's what I know about you:"]
            for m in recent:
                lines.append(f"• {m['content']} ({m['category']})")
            return "\n".join(lines)

        # Search for relevant memories
        matches = []
        query_lower = query.lower()
        for m in self.memories:
            if query_lower in m["content"].lower():
                matches.append(m)

        if not matches:
            return f"I don't have any memories about '{query}'. Tell me more!"

        lines = [f"Memories about '{query}':"]
        for m in matches[-5:]:
            lines.append(f"• {m['content']} ({m['timestamp'][:10]})")
        return "\n".join(lines)

    def _search_memory(self, query: str) -> str:
        """Deep search through all memories"""
        return self._recall_memory(query)

    def _update_topics(self, content: str, category: str):
        """Track topics mentioned in content"""
        # Extract potential topics (simple noun-like detection)
        words = re.findall(r"\b[A-Z][a-z]+\b", content)
        for word in words[:3]:  # Take first 3
            word_lower = word.lower()
            if word_lower not in self.topics:
                self.topics[word_lower] = {
                    "count": 0,
                    "first_seen": datetime.now().isoformat(),
                }
            self.topics[word_lower]["count"] += 1
            self.topics[word_lower]["last_seen"] = datetime.now().isoformat()

        _save_json(TOPICS_FILE, self.topics)

    def _get_topics(self) -> str:
        """Get all tracked topics"""
        if not self.topics:
            return "No topics tracked yet."

        sorted_topics = sorted(
            self.topics.items(), key=lambda x: x[1]["count"], reverse=True
        )
        lines = ["Topics I know about you:"]
        for topic, data in sorted_topics[:10]:
            lines.append(f"• {topic}: mentioned {data['count']} times")
        return "\n".join(lines)

    def _what_do_you_know(self) -> str:
        """Summary of all known information"""
        lines = ["Here's everything I know about you:"]

        # Group by category
        by_category = {}
        for m in self.memories:
            cat = m.get("category", "general")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(m["content"])

        for cat, contents in by_category.items():
            lines.append(f"\n{cat.upper()} ({len(contents)} memories):")
            for c in contents[:3]:
                lines.append(f"  • {c}")
            if len(contents) > 3:
                lines.append(f"  ... and {len(contents) - 3} more")

        return "\n".join(lines)

    def can_handle(self, user_input: str) -> float:
        """Only explicit memory commands — broad phrases like 'i have' must reach the main brain + profile."""
        input_lower = user_input.lower()
        memory_keywords = [
            "remember that",
            "remember this",
            "don't forget",
            "save this",
            "note that",
            "what do you remember",
            "what have you remembered",
            "search memory",
            "list my memories",
            "delete memory",
            "forget that",
        ]
        for kw in memory_keywords:
            if kw in input_lower:
                return 0.92
        return 0.1


class NoteTakingSkill(Skill):
    """
    Note-taking skill - creates and manages notes in files.
    Accesses user's files for persistent storage.
    """

    def __init__(self):
        super().__init__(
            name="notes",
            description="Create and manage persistent notes",
            version="1.0.0",
        )
        self.notes_index = _load_json(NOTES_DIR / "index.json", dict)

    def execute(self, **kwargs) -> Any:
        """Execute note operation"""
        action = kwargs.get("action", "list")
        title = kwargs.get("title", "")
        content = kwargs.get("content", "")

        if action == "create" or action == "add":
            return self._create_note(title, content)
        elif action == "read":
            return self._read_note(title)
        elif action == "list":
            return self._list_notes()
        elif action == "delete":
            return self._delete_note(title)
        else:
            return self._list_notes()

    def _create_note(self, title: str, content: str) -> str:
        """Create a new note"""
        if not title:
            return "What's the note title?"
        if not content:
            return "What's the note content?"

        # Sanitize filename
        safe_title = re.sub(r"[^\w\s-]", "", title).strip()[:50]
        if not safe_title:
            safe_title = f"note_{len(self.notes_index) + 1}"

        filename = f"{safe_title}.txt"
        filepath = NOTES_DIR / filename

        # Save note
        note_data = {
            "title": title,
            "content": content,
            "created": datetime.now().isoformat(),
            "filename": filename,
        }

        with open(filepath, "w") as f:
            f.write(f"# {title}\n")
            f.write(f"Created: {note_data['created']}\n\n")
            f.write(content)

        self.notes_index[safe_title] = note_data
        _save_json(NOTES_DIR / "index.json", self.notes_index)

        self.on_use()
        return f"Note '{title}' saved!"

    def _read_note(self, title: str) -> str:
        """Read a specific note"""
        if not title:
            return "Which note do you want to read?"

        safe_title = re.sub(r"[^\w\s-]", "", title).strip()[:50]

        if safe_title not in self.notes_index:
            # Try fuzzy search
            for key in self.notes_index:
                if safe_title.lower() in key.lower():
                    safe_title = key
                    break

        if safe_title not in self.notes_index:
            return f"Note '{title}' not found."

        filepath = NOTES_DIR / self.notes_index[safe_title]["filename"]
        try:
            with open(filepath, "r") as f:
                return f.read()
        except Exception:
            return "Error reading note."

    def _list_notes(self) -> str:
        """List all notes"""
        if not self.notes_index:
            return "No notes yet. Say 'take a note' to create one."

        lines = ["Your notes:"]
        for name, data in self.notes_index.items():
            lines.append(f"• {data['title']} ({data['created'][:10]})")
        return "\n".join(lines)

    def _delete_note(self, title: str) -> str:
        """Delete a note"""
        if not title:
            return "Which note to delete?"

        safe_title = re.sub(r"[^\w\s-]", "", title).strip()[:50]

        if safe_title in self.notes_index:
            filepath = NOTES_DIR / self.notes_index[safe_title]["filename"]
            try:
                os.remove(filepath)
            except:
                pass
            del self.notes_index[safe_title]
            _save_json(NOTES_DIR / "index.json", self.notes_index)
            return f"Deleted note '{title}'."

        return f"Note '{title}' not found."

    def can_handle(self, user_input: str) -> float:
        """Check if input is about notes"""
        note_keywords = [
            "note",
            "take a note",
            "write this down",
            "jot this",
            "create note",
            "add note",
            "list notes",
            "show notes",
            "read note",
            "delete note",
            "remove note",
        ]
        input_lower = user_input.lower()

        for kw in note_keywords:
            if kw in input_lower:
                return 0.9
        return 0.1


class ConversationTrackerSkill(Skill):
    """
    Tracks what the user is talking about - maintains conversation context.
    This is NOT session-based - persists across sessions.
    """

    def __init__(self):
        super().__init__(
            name="conversation",
            description="Track conversation topics and context",
            version="1.0.0",
        )
        self.conversation = _load_json(CONVERSATION_FILE, dict)
        self.current_topic = self.conversation.get("current_topic", None)
        self.topic_history = self.conversation.get("topic_history", [])
        self.topic_details = self.conversation.get("topic_details", {})

    def execute(self, **kwargs) -> Any:
        """Execute conversation tracking"""
        action = kwargs.get("action", "track")
        user_input = kwargs.get("user_input", "")

        if action == "track":
            return self._track_topic(user_input)
        elif action == "what_topic":
            return self._what_topic()
        elif action == "context":
            return self._get_context()
        elif action == "clear":
            return self._clear_topic()
        else:
            return self._what_topic()

    def _track_topic(self, user_input: str) -> str:
        """Analyze input and track the topic"""
        if not user_input:
            return ""

        # Simple topic extraction
        user_lower = user_input.lower()

        # Detect topic changes
        topic_indicators = {
            "weather": ["weather", "temperature", "rain", "sunny", "forecast"],
            "news": ["news", "headlines", "happened", "today"],
            "code": ["code", "programming", "function", "bug", "error", "python"],
            "files": ["file", "folder", "directory", "read", "open"],
            "music": ["song", "music", "play", "spotify", "album"],
            "food": ["food", "eat", "restaurant", "lunch", "dinner", "breakfast"],
            "travel": ["flight", "hotel", "travel", "trip", "vacation"],
            "work": ["meeting", "project", "email", "boss", "deadline"],
            "family": ["wife", "husband", "kids", "mom", "dad", "family"],
            "health": ["sick", "doctor", "medicine", "pain", "feel"],
        }

        new_topic = None
        for topic, keywords in topic_indicators.items():
            if any(kw in user_lower for kw in keywords):
                new_topic = topic
                break

        if new_topic and new_topic != self.current_topic:
            if self.current_topic:
                self.topic_history.append(
                    {"topic": self.current_topic, "ended": datetime.now().isoformat()}
                )

            self.current_topic = new_topic

            if new_topic not in self.topic_details:
                self.topic_details[new_topic] = {
                    "started": datetime.now().isoformat(),
                    "mentions": 0,
                    "details": [],
                }

            self.topic_details[new_topic]["mentions"] += 1
            self.topic_details[new_topic]["details"].append(user_input[:100])

            # Keep history manageable
            if len(self.topic_history) > 20:
                self.topic_history = self.topic_history[-20:]

            self._save()

        elif new_topic == self.current_topic and self.current_topic:
            self.topic_details[self.current_topic]["mentions"] += 1
            self._save()

        return f"Tracking: {self.current_topic}" if self.current_topic else ""

    def _what_topic(self) -> str:
        """Return current topic"""
        if not self.current_topic:
            return "We're not currently on a specific topic. What would you like to talk about?"

        details = self.topic_details.get(self.current_topic, {})
        return f"We're talking about: {self.current_topic} (mentioned {details.get('mentions', 0)} times)"

    def _get_context(self) -> str:
        """Get full conversation context"""
        lines = ["Conversation context:"]

        if self.current_topic:
            lines.append(f"Current: {self.current_topic}")

        if self.topic_history:
            lines.append(
                f"History: {' → '.join([t['topic'] for t in self.topic_history[-5:]])}"
            )

        if self.topic_details:
            lines.append("\nTopics covered:")
            for topic, details in list(self.topic_details.items())[:5]:
                lines.append(f"  • {topic}: {details['mentions']} mentions")

        return "\n".join(lines)

    def _clear_topic(self) -> str:
        """Clear current topic"""
        self.current_topic = None
        self._save()
        return "Conversation context cleared."

    def _save(self):
        """Save conversation state"""
        self.conversation = {
            "current_topic": self.current_topic,
            "topic_history": self.topic_history,
            "topic_details": self.topic_details,
            "last_updated": datetime.now().isoformat(),
        }
        _save_json(CONVERSATION_FILE, self.conversation)

    def can_handle(self, user_input: str) -> float:
        """Check if input is about conversation tracking"""
        track_keywords = [
            "what are we talking about",
            "what topic",
            "conversation",
            "context",
            "track topic",
            "clear context",
        ]
        input_lower = user_input.lower()

        for kw in track_keywords:
            if kw in input_lower:
                return 0.8
        return 0.1


def register_memory_skills(registry):
    """Register all memory skills"""
    registry.register(MemorySkill())
    registry.register(NoteTakingSkill())
    registry.register(ConversationTrackerSkill())
