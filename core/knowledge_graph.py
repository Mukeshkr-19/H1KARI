"""
HIKARI v2.0 - Personal Knowledge Graph
Maps relationships between user's interests, projects, people, and concepts
"""

import json
import re
from typing import Optional, Dict, Any, List, Set, Tuple
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from core.runtime_paths import legacy_data_dir

DATA_DIR = legacy_data_dir()
KNOWLEDGE_GRAPH_FILE = DATA_DIR / "knowledge_graph.json"


class KnowledgeNode:
    """A node in the knowledge graph"""

    def __init__(
        self, node_id: str, node_type: str, label: str, properties: Dict = None
    ):
        self.id = node_id
        self.type = (
            node_type  # person, project, interest, skill, location, concept, event
        )
        self.label = label
        self.properties = properties or {}
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at
        self.connections: Dict[str, float] = {}  # node_id -> weight

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "properties": self.properties,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "connections": self.connections,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "KnowledgeNode":
        node = cls(data["id"], data["type"], data["label"], data.get("properties"))
        node.created_at = data.get("created_at", datetime.now().isoformat())
        node.updated_at = data.get("updated_at", node.created_at)
        node.connections = data.get("connections", {})
        return node


class KnowledgeGraph:
    """Personal knowledge graph that maps user's world"""

    NODE_TYPES = [
        "person",
        "project",
        "interest",
        "skill",
        "location",
        "concept",
        "event",
        "tool",
        "goal",
    ]

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.nodes: Dict[str, KnowledgeNode] = {}
        self.edges: List[Dict] = []
        self._load()

    def _load(self):
        try:
            if KNOWLEDGE_GRAPH_FILE.exists():
                with open(KNOWLEDGE_GRAPH_FILE, "r") as f:
                    data = json.load(f)
                for node_data in data.get("nodes", []):
                    node = KnowledgeNode.from_dict(node_data)
                    self.nodes[node.id] = node
                self.edges = data.get("edges", [])
        except Exception as e:
            print(f"[KNOWLEDGE] Load error: {e}")

    def _save(self):
        try:
            data = {
                "nodes": [n.to_dict() for n in self.nodes.values()],
                "edges": self.edges,
                "last_updated": datetime.now().isoformat(),
            }
            with open(KNOWLEDGE_GRAPH_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[KNOWLEDGE] Save error: {e}")

    def add_node(
        self, node_id: str, node_type: str, label: str, properties: Dict = None
    ) -> KnowledgeNode:
        """Add a node to the graph"""
        node_id = node_id.lower().replace(" ", "_")
        if node_id in self.nodes:
            # Update existing node
            node = self.nodes[node_id]
            node.label = label
            node.type = node_type
            if properties:
                node.properties.update(properties)
            node.updated_at = datetime.now().isoformat()
        else:
            node = KnowledgeNode(node_id, node_type, label, properties)
            self.nodes[node_id] = node

        self._save()
        return node

    def add_edge(
        self, from_id: str, to_id: str, relationship: str, weight: float = 1.0
    ):
        """Add a connection between nodes"""
        from_id = from_id.lower().replace(" ", "_")
        to_id = to_id.lower().replace(" ", "_")

        # Ensure nodes exist
        if from_id not in self.nodes:
            self.add_node(from_id, "concept", from_id.replace("_", " ").title())
        if to_id not in self.nodes:
            self.add_node(to_id, "concept", to_id.replace("_", " ").title())

        # Add edge
        edge = {
            "from": from_id,
            "to": to_id,
            "relationship": relationship,
            "weight": weight,
            "created_at": datetime.now().isoformat(),
        }
        self.edges.append(edge)

        # Update node connections
        self.nodes[from_id].connections[to_id] = weight
        self.nodes[to_id].connections[from_id] = weight

        self._save()

    def extract_from_conversation(self, user_input: str, ai_response: str = ""):
        """Automatically extract knowledge from conversations"""
        lower = user_input.lower()

        # Extract projects
        project_patterns = [
            r"(?:working on|building|creating|developing) (?:a |an |the )?(.+?)(?:\.|$|,)",
            r"my (?:project|app|website|startup) (?:is |called |named )?(.+?)(?:\.|$|,)",
        ]
        for pattern in project_patterns:
            match = re.search(pattern, lower)
            if match:
                project = match.group(1).strip()
                self.add_node(
                    project,
                    "project",
                    project.title(),
                    {"mentioned_in": user_input[:100]},
                )

        # Extract interests
        interest_patterns = [
            r"(?:interested in|into|love|enjoy|like) (.+?)(?:\.|$|,)",
            r"(?:hobby|passion) is (.+?)(?:\.|$|,)",
        ]
        for pattern in interest_patterns:
            match = re.search(pattern, lower)
            if match:
                interest = match.group(1).strip()
                self.add_node(interest, "interest", interest.title())

        # Extract skills/tools
        skill_patterns = [
            r"(?:use|using|learn|learning) (.+?)(?:\.|$|,)",
            r"(?:good at|skilled in|expert in) (.+?)(?:\.|$|,)",
        ]
        for pattern in skill_patterns:
            match = re.search(pattern, lower)
            if match:
                skill = match.group(1).strip()
                self.add_node(skill, "skill", skill.title())

        # Extract goals
        goal_patterns = [
            r"(?:want to|plan to|hope to|goal is to|trying to) (.+?)(?:\.|$|,)",
            r"(?:my goal|my aim) is (?:to )?(.+?)(?:\.|$|,)",
        ]
        for pattern in goal_patterns:
            match = re.search(pattern, lower)
            if match:
                goal = match.group(1).strip()
                self.add_node(goal, "goal", goal.title())

    def find_related(self, node_id: str, max_depth: int = 2) -> List[Dict]:
        """Find all nodes related to a given node"""
        node_id = node_id.lower().replace(" ", "_")
        if node_id not in self.nodes:
            return []

        visited = set()
        results = []
        queue = [(node_id, 0)]

        while queue:
            current_id, depth = queue.pop(0)
            if current_id in visited or depth > max_depth:
                continue
            visited.add(current_id)

            if current_id != node_id:
                node = self.nodes[current_id]
                results.append(
                    {
                        "id": node.id,
                        "type": node.type,
                        "label": node.label,
                        "depth": depth,
                    }
                )

            # Add connected nodes to queue
            for connected_id in self.nodes[current_id].connections:
                if connected_id not in visited:
                    queue.append((connected_id, depth + 1))

        return results

    def search(self, query: str) -> List[Dict]:
        """Search the knowledge graph"""
        query_lower = query.lower()
        results = []

        for node_id, node in self.nodes.items():
            if query_lower in node_id or query_lower in node.label.lower():
                results.append(
                    {
                        "id": node.id,
                        "type": node.type,
                        "label": node.label,
                        "properties": node.properties,
                    }
                )

        return results

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the knowledge graph"""
        type_counts = defaultdict(int)
        for node in self.nodes.values():
            type_counts[node.type] += 1

        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "types": dict(type_counts),
            "recent_nodes": [
                {"id": n.id, "type": n.type, "label": n.label}
                for n in sorted(
                    self.nodes.values(), key=lambda x: x.updated_at, reverse=True
                )[:10]
            ],
        }

    def get_insights(self) -> List[str]:
        """Generate insights from the knowledge graph"""
        insights = []
        type_counts = defaultdict(int)
        for node in self.nodes.values():
            type_counts[node.type] += 1

        if type_counts.get("person", 0) > 0:
            insights.append(f"I know about {type_counts['person']} people in your life")
        if type_counts.get("project", 0) > 0:
            insights.append(f"You have {type_counts['project']} projects I'm tracking")
        if type_counts.get("interest", 0) > 0:
            insights.append(f"You're interested in {type_counts['interest']} topics")
        if type_counts.get("goal", 0) > 0:
            insights.append(f"You have {type_counts['goal']} goals I'm aware of")

        # Find most connected nodes
        if self.nodes:
            most_connected = max(self.nodes.values(), key=lambda n: len(n.connections))
            if len(most_connected.connections) > 1:
                insights.append(
                    f"'{most_connected.label}' is your most connected topic"
                )

        return insights
