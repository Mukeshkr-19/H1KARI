"""Data models for Hikari Neural Memory."""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Any
import json


class NodeType(str, Enum):
    PERSON = "PERSON"
    PROJECT = "PROJECT"
    TOOL = "TOOL"
    PREFERENCE = "PREFERENCE"
    FACT = "FACT"
    SKILL = "SKILL"
    LOCATION = "LOCATION"
    TOPIC = "TOPIC"
    RESOURCE = "RESOURCE"
    EPISODE = "EPISODE"
    CONCEPT = "CONCEPT"
    RULE = "RULE"
    GOAL = "GOAL"
    ROUTINE = "ROUTINE"
    EVENT = "EVENT"
    CONVERSATION = "CONVERSATION"
    DOCUMENT = "DOCUMENT"


class EdgeType(str, Enum):
    KNOWS_ABOUT = "KNOWS_ABOUT"
    PREFERS = "PREFERS"
    WORKING_ON = "WORKING_ON"
    USES_TOOL = "USES_TOOL"
    LOCATED_AT = "LOCATED_AT"
    INTERESTED_IN = "INTERESTED_IN"
    HAS_SKILL = "HAS_SKILL"
    LINKED_TO = "LINKED_TO"
    PART_OF = "PART_OF"
    MEMBER_OF = "MEMBER_OF"
    AUTHORED = "AUTHORED"
    FOLLOWS = "FOLLOWS"
    CONTRADICTS = "CONTRADICTS"
    DERIVED_FROM = "DERIVED_FROM"
    DEPENDS_ON = "DEPENDS_ON"
    COLLABORATES_WITH = "COLLABORATES_WITH"
    INFLUENCED_BY = "INFLUENCED_BY"
    SUPPORTS = "SUPPORTS"
    OPPOSES = "OPPOSES"


class EpisodeType(str, Enum):
    CONVERSATION = "CONVERSATION"
    INTERACTION = "INTERACTION"
    DISCOVERY = "DISCOVERY"
    DECISION = "DECISION"
    PROBLEM = "PROBLEM"
    SOLUTION = "SOLUTION"


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    IN_PROGRESS = "IN_PROGRESS"
    UNKNOWN = "UNKNOWN"


@dataclass
class MemoryNode:
    id: Optional[int] = None
    node_type: str = NodeType.FACT.value
    name: str = ""
    alias: Optional[str] = None
    content: Optional[str] = None
    metadata: Optional[dict] = None
    salience: float = 0.5
    activation_count: int = 0
    last_accessed: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    user_id: str = "local_user"
    is_archived: bool = False
    is_pinned: bool = False

    def __post_init__(self):
        now = datetime.utcnow().isoformat()
        if self.last_accessed is None:
            self.last_accessed = now
        if self.created_at is None:
            self.created_at = now
        if self.updated_at is None:
            self.updated_at = now
        if self.metadata is None:
            self.metadata = {}

    def touch(self):
        self.last_accessed = datetime.utcnow().isoformat()
        self.activation_count += 1

    def boost_salience(self, amount: float = 0.1):
        self.salience = min(1.0, self.salience + amount)

    def decay_salience(self, amount: float = 0.01):
        self.salience = max(0.0, self.salience - amount)

    def to_db_tuple(self) -> tuple:
        return (
            self.node_type,
            self.name,
            self.alias,
            self.content,
            json.dumps(self.metadata),
            self.salience,
            self.activation_count,
            self.last_accessed,
            self.created_at,
            self.updated_at,
            self.user_id,
            int(self.is_archived),
            int(self.is_pinned),
        )

    @classmethod
    def from_db_row(cls, row: dict) -> "MemoryNode":
        metadata = row.get("metadata")
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}
        return cls(
            id=row.get("id"),
            node_type=row.get("node_type"),
            name=row.get("name"),
            alias=row.get("alias"),
            content=row.get("content"),
            metadata=metadata or {},
            salience=row.get("salience", 0.5),
            activation_count=row.get("activation_count", 0),
            last_accessed=row.get("last_accessed"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            user_id=row.get("user_id", "local_user"),
            is_archived=bool(row.get("is_archived", 0)),
            is_pinned=bool(row.get("is_pinned", 0)),
        )


@dataclass
class MemoryEdge:
    id: Optional[int] = None
    source_id: int = 0
    target_id: int = 0
    edge_type: str = EdgeType.LINKED_TO.value
    weight: float = 1.0
    context: Optional[str] = None
    bidirectional: bool = False
    last_accessed: Optional[str] = None
    created_at: Optional[str] = None
    user_id: str = "local_user"
    is_archived: bool = False

    def __post_init__(self):
        now = datetime.utcnow().isoformat()
        if self.last_accessed is None:
            self.last_accessed = now
        if self.created_at is None:
            self.created_at = now

    def strengthen(self, amount: float = 0.1):
        self.weight = min(1.0, self.weight + amount)

    def weaken(self, amount: float = 0.05):
        self.weight = max(0.0, self.weight - amount)

    def to_db_tuple(self) -> tuple:
        return (
            self.source_id,
            self.target_id,
            self.edge_type,
            self.weight,
            self.context,
            int(self.bidirectional),
            self.last_accessed,
            self.created_at,
            self.user_id,
            int(self.is_archived),
        )

    @classmethod
    def from_db_row(cls, row: dict) -> "MemoryEdge":
        return cls(
            id=row.get("id"),
            source_id=row.get("source_id"),
            target_id=row.get("target_id"),
            edge_type=row.get("edge_type"),
            weight=row.get("weight", 1.0),
            context=row.get("context"),
            bidirectional=bool(row.get("bidirectional", 0)),
            last_accessed=row.get("last_accessed"),
            created_at=row.get("created_at"),
            user_id=row.get("user_id", "local_user"),
            is_archived=bool(row.get("is_archived", 0)),
        )


@dataclass
class Episode:
    id: Optional[int] = None
    episode_type: str = EpisodeType.CONVERSATION.value
    title: Optional[str] = None
    summary: Optional[str] = None
    content: Optional[str] = None
    nodes: Optional[list[int]] = None
    sentiment: float = 0.0
    importance: float = 0.5
    outcome: str = Outcome.UNKNOWN.value
    session_id: Optional[str] = None
    turn_count: int = 1
    duration_seconds: Optional[int] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    user_id: str = "local_user"
    is_archived: bool = False

    def __post_init__(self):
        now = datetime.utcnow().isoformat()
        if self.started_at is None:
            self.started_at = now
        if self.nodes is None:
            self.nodes = []

    def to_db_tuple(self) -> tuple:
        return (
            self.episode_type,
            self.title,
            self.summary,
            self.content,
            json.dumps(self.nodes),
            self.sentiment,
            self.importance,
            self.outcome,
            self.session_id,
            self.turn_count,
            self.duration_seconds,
            self.started_at,
            self.ended_at,
            self.user_id,
            int(self.is_archived),
        )

    @classmethod
    def from_db_row(cls, row: dict) -> "Episode":
        nodes = row.get("nodes")
        if isinstance(nodes, str):
            nodes = json.loads(nodes) if nodes else []
        return cls(
            id=row.get("id"),
            episode_type=row.get("episode_type"),
            title=row.get("title"),
            summary=row.get("summary"),
            content=row.get("content"),
            nodes=nodes or [],
            sentiment=row.get("sentiment", 0.0),
            importance=row.get("importance", 0.5),
            outcome=row.get("outcome", Outcome.UNKNOWN.value),
            session_id=row.get("session_id"),
            turn_count=row.get("turn_count", 1),
            duration_seconds=row.get("duration_seconds"),
            started_at=row.get("started_at"),
            ended_at=row.get("ended_at"),
            user_id=row.get("user_id", "local_user"),
            is_archived=bool(row.get("is_archived", 0)),
        )


@dataclass
class Session:
    id: Optional[int] = None
    session_id: str = ""
    session_type: str = "CONVERSATION"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    turn_count: int = 0
    user_id: str = "local_user"
    metadata: Optional[dict] = None

    def __post_init__(self):
        if self.started_at is None:
            self.started_at = datetime.utcnow().isoformat()
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ContextPacket:
    query: str
    relevant_nodes: list[MemoryNode] = field(default_factory=list)
    relevant_edges: list[MemoryEdge] = field(default_factory=list)
    recent_episodes: list[Episode] = field(default_factory=list)
    user_profile: dict = field(default_factory=dict)
    session_context: dict = field(default_factory=dict)
    confidence: float = 0.0
    retrieval_strategies_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "relevant_nodes": [asdict(n) for n in self.relevant_nodes],
            "relevant_edges": [asdict(e) for e in self.relevant_edges],
            "recent_episodes": [asdict(e) for e in self.recent_episodes],
            "user_profile": self.user_profile,
            "session_context": self.session_context,
            "confidence": self.confidence,
            "retrieval_strategies": self.retrieval_strategies_used,
        }
