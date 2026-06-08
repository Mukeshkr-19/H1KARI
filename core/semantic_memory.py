"""
HIKARI v2.0 - Semantic Memory System
Advanced conversation memory with semantic search, context retrieval, and deep learning
"""

import os
import json
import math
import hashlib
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from core.runtime_paths import legacy_data_dir

DATA_DIR = legacy_data_dir()
SEMANTIC_MEMORY_FILE = DATA_DIR / "semantic_memory.json"


class SemanticMemory:
    """Advanced memory with semantic search and context understanding"""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conversations: List[Dict] = []
        self.topics: Dict[str, List[int]] = defaultdict(
            list
        )  # topic -> conversation indices
        self.entities: Dict[str, List[int]] = defaultdict(
            list
        )  # entity -> conversation indices
        self.context_windows: Dict[str, List[str]] = defaultdict(
            list
        )  # context -> recent conversations
        self._load()

    def _load(self):
        try:
            if SEMANTIC_MEMORY_FILE.exists():
                with open(SEMANTIC_MEMORY_FILE, "r") as f:
                    data = json.load(f)
                self.conversations = data.get("conversations", [])
                self.topics = defaultdict(list, data.get("topics", {}))
                self.entities = defaultdict(list, data.get("entities", {}))
                self.context_windows = defaultdict(
                    list, data.get("context_windows", {})
                )
        except Exception as e:
            print(f"[SEMANTIC_MEMORY] Load error: {e}")

    def _save(self):
        try:
            data = {
                "conversations": self.conversations[-1000:],
                "topics": dict(self.topics),
                "entities": dict(self.entities),
                "context_windows": {
                    k: v[-50:] for k, v in self.context_windows.items()
                },
                "last_updated": datetime.now().isoformat(),
            }
            with open(SEMANTIC_MEMORY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[SEMANTIC_MEMORY] Save error: {e}")

    def add_conversation(
        self,
        user_input: str,
        ai_response: str,
        context: str = "",
        metadata: Dict = None,
    ):
        """Add a conversation with rich metadata and semantic indexing"""
        entry = {
            "id": hashlib.md5(
                f"{user_input}{datetime.now().isoformat()}".encode()
            ).hexdigest()[:12],
            "timestamp": datetime.now().isoformat(),
            "user": user_input,
            "ai": ai_response,
            "context": context,
            "metadata": metadata or {},
            "topics": self._extract_topics(user_input),
            "entities": self._extract_entities(user_input),
            "sentiment": self._estimate_sentiment(user_input),
            "importance": self._estimate_importance(user_input, ai_response),
        }

        self.conversations.append(entry)

        # Index by topics
        for topic in entry["topics"]:
            self.topics[topic].append(len(self.conversations) - 1)

        # Index by entities
        for entity in entry["entities"]:
            self.entities[entity].append(len(self.conversations) - 1)

        # Update context windows
        for topic in entry["topics"]:
            self.context_windows[topic].append(entry["id"])
            if len(self.context_windows[topic]) > 50:
                self.context_windows[topic] = self.context_windows[topic][-50:]

        self._save()
        return entry["id"]

    def search_by_topic(self, topic: str, limit: int = 10) -> List[Dict]:
        """Search conversations by topic"""
        indices = self.topics.get(topic, [])
        results = []
        for idx in indices[-limit:]:
            if 0 <= idx < len(self.conversations):
                results.append(self.conversations[idx])
        return results

    def search_by_entity(self, entity: str, limit: int = 10) -> List[Dict]:
        """Search conversations mentioning a specific entity"""
        indices = self.entities.get(entity, [])
        results = []
        for idx in indices[-limit:]:
            if 0 <= idx < len(self.conversations):
                results.append(self.conversations[idx])
        return results

    def search_semantic(self, query: str, limit: int = 10) -> List[Dict]:
        """Semantic search across all conversations"""
        query_lower = query.lower()
        query_words = set(query_lower.split())
        query_topics = self._extract_topics(query)
        query_entities = self._extract_entities(query)

        scores = []
        for i, conv in enumerate(self.conversations):
            score = 0.0

            # Word overlap
            conv_words = set(conv["user"].lower().split())
            overlap = len(query_words & conv_words)
            if overlap > 0:
                score += overlap / max(len(query_words), 1) * 0.3

            # Topic matching
            conv_topics = set(conv.get("topics", []))
            topic_overlap = len(set(query_topics) & conv_topics)
            if topic_overlap > 0:
                score += topic_overlap / max(len(query_topics), 1) * 0.4

            # Entity matching
            conv_entities = set(conv.get("entities", []))
            entity_overlap = len(set(query_entities) & conv_entities)
            if entity_overlap > 0:
                score += entity_overlap / max(len(query_entities), 1) * 0.3

            if score > 0:
                scores.append((score, conv))

        # Sort by score and return top results
        scores.sort(key=lambda x: x[0], reverse=True)
        return [conv for score, conv in scores[:limit]]

    def get_context_for_topic(self, topic: str, limit: int = 5) -> str:
        """Get recent context for a specific topic"""
        topic_convs = self.search_by_topic(topic, limit)
        if not topic_convs:
            return ""

        context_parts = []
        for conv in topic_convs:
            context_parts.append(f"User: {conv['user']}")
            context_parts.append(f"AI: {conv['ai']}")

        return "\n".join(context_parts)

    def get_conversation_summary(self, time_range: str = "day") -> Dict[str, Any]:
        """Get summary of conversations in a time range"""
        now = datetime.now()
        if time_range == "day":
            cutoff = now - timedelta(days=1)
        elif time_range == "week":
            cutoff = now - timedelta(weeks=1)
        elif time_range == "month":
            cutoff = now - timedelta(days=30)
        else:
            cutoff = now - timedelta(days=1)

        recent = [
            c
            for c in self.conversations
            if datetime.fromisoformat(c["timestamp"]) >= cutoff
        ]

        if not recent:
            return {"count": 0, "topics": [], "entities": []}

        all_topics = []
        all_entities = []
        for conv in recent:
            all_topics.extend(conv.get("topics", []))
            all_entities.extend(conv.get("entities", []))

        from collections import Counter

        topic_counts = Counter(all_topics)
        entity_counts = Counter(all_entities)

        return {
            "count": len(recent),
            "top_topics": topic_counts.most_common(5),
            "top_entities": entity_counts.most_common(5),
            "avg_importance": sum(c.get("importance", 0) for c in recent) / len(recent),
        }

    def get_important_conversations(self, limit: int = 10) -> List[Dict]:
        """Get most important conversations"""
        sorted_convs = sorted(
            self.conversations, key=lambda c: c.get("importance", 0), reverse=True
        )
        return sorted_convs[:limit]

    def forget_old_conversations(self, keep_days: int = 90):
        """Remove conversations older than specified days"""
        cutoff = datetime.now() - timedelta(days=keep_days)
        original_count = len(self.conversations)

        self.conversations = [
            c
            for c in self.conversations
            if datetime.fromisoformat(c["timestamp"]) >= cutoff
        ]

        removed = original_count - len(self.conversations)
        if removed > 0:
            print(f"[SEMANTIC_MEMORY] Forgotten {removed} old conversations")
            self._save()

    def _extract_topics(self, text: str) -> List[str]:
        """Extract topics from text"""
        lower = text.lower()
        topics = []

        topic_keywords = {
            "weather": [
                "weather",
                "temperature",
                "rain",
                "sunny",
                "cloudy",
                "forecast",
            ],
            "news": ["news", "headlines", "updates", "what's happening"],
            "coding": [
                "code",
                "program",
                "debug",
                "python",
                "javascript",
                "function",
                "bug",
            ],
            "health": ["sick", "ill", "fever", "tired", "health", "doctor", "medicine"],
            "work": ["work", "project", "meeting", "deadline", "task", "job"],
            "personal": ["family", "friend", "relationship", "personal", "life"],
            "technology": [
                "tech",
                "ai",
                "machine learning",
                "software",
                "hardware",
                "computer",
            ],
            "entertainment": ["movie", "music", "game", "fun", "entertainment", "show"],
            "food": ["food", "eat", "lunch", "dinner", "breakfast", "recipe", "cook"],
            "travel": ["travel", "trip", "vacation", "flight", "hotel", "destination"],
        }

        for topic, keywords in topic_keywords.items():
            if any(kw in lower for kw in keywords):
                topics.append(topic)

        return topics

    def _extract_entities(self, text: str) -> List[str]:
        """Extract entities (names, places, things) from text"""
        entities = []
        words = text.split()

        # Extract capitalized words (potential names/places)
        for word in words:
            cleaned = word.strip(".,!?\"'()[]{}")
            if cleaned and cleaned[0].isupper() and len(cleaned) > 1:
                entities.append(cleaned)

        return list(set(entities))[:10]

    def _estimate_sentiment(self, text: str) -> float:
        """Estimate sentiment (-1 to 1)"""
        lower = text.lower()
        positive_words = [
            "good",
            "great",
            "awesome",
            "happy",
            "love",
            "excellent",
            "wonderful",
            "thanks",
            "thank",
        ]
        negative_words = [
            "bad",
            "terrible",
            "awful",
            "hate",
            "sad",
            "angry",
            "worst",
            "horrible",
            "sick",
        ]

        pos_count = sum(1 for w in positive_words if w in lower)
        neg_count = sum(1 for w in negative_words if w in lower)

        total = pos_count + neg_count
        if total == 0:
            return 0.0

        return (pos_count - neg_count) / total

    def _estimate_importance(self, user_input: str, ai_response: str) -> float:
        """Estimate conversation importance"""
        importance = 0.5  # Base importance

        # Longer conversations are more important
        if len(user_input) > 50:
            importance += 0.1
        if len(ai_response) > 100:
            importance += 0.1

        # Questions are more important
        if "?" in user_input:
            importance += 0.1

        # Emotional content is more important
        emotional_words = [
            "love",
            "hate",
            "angry",
            "sad",
            "happy",
            "excited",
            "worried",
            "sick",
        ]
        if any(w in user_input.lower() for w in emotional_words):
            importance += 0.2

        return min(1.0, importance)

    def get_all_topics(self) -> List[str]:
        """Get all tracked topics"""
        return list(self.topics.keys())

    def get_all_entities(self) -> List[str]:
        """Get all tracked entities"""
        return list(self.entities.keys())

    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics"""
        return {
            "total_conversations": len(self.conversations),
            "total_topics": len(self.topics),
            "total_entities": len(self.entities),
            "topics": list(self.topics.keys())[:20],
            "entities": list(self.entities.keys())[:20],
        }


# Import timedelta
from datetime import timedelta
