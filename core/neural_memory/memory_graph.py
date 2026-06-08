"""Graph operations for Hikari Neural Memory."""

from typing import Optional, List, Set, Tuple, Callable
from collections import deque

from .storage import storage
from .models import MemoryNode, MemoryEdge


class MemoryGraph:
    def __init__(self):
        self.storage = storage

    def get_neighbors(
        self, node_id: int, edge_type: Optional[str] = None, max_depth: int = 1
    ) -> List[MemoryNode]:
        if max_depth == 1:
            return self.storage.get_neighbors(node_id, edge_type)

        visited: Set[int] = {node_id}
        queue = deque([(node_id, 0)])
        results = []

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            neighbors = self.storage.get_neighbors(current_id, edge_type)
            for neighbor in neighbors:
                if neighbor.id not in visited:
                    visited.add(neighbor.id)
                    results.append(neighbor)
                    if depth + 1 < max_depth:
                        queue.append((neighbor.id, depth + 1))

        return results

    def get_ego_network(
        self, node_id: int, depth: int = 2
    ) -> Tuple[List[MemoryNode], List[MemoryEdge]]:
        nodes = {node_id}
        edges = []

        queue = deque([(node_id, 0)])
        visited_depths = {node_id: 0}

        while queue:
            current_id, depth_level = queue.popleft()
            if depth_level >= depth:
                continue

            neighbors = self.storage.get_neighbors(current_id)
            neighbor_edges = self.storage.get_edges_for_node(current_id)

            for edge in neighbor_edges:
                if edge.id not in {e.id for e in edges}:
                    edges.append(edge)

            for neighbor in neighbors:
                if neighbor.id not in nodes:
                    nodes.add(neighbor.id)
                    visited_depths[neighbor.id] = depth_level + 1
                    if depth_level + 1 < depth:
                        queue.append((neighbor.id, depth_level + 1))

        all_nodes = [self.storage.get_node_by_id(nid) for nid in nodes]
        all_nodes = [n for n in all_nodes if n is not None]

        return all_nodes, edges

    def find_paths(
        self, source_id: int, target_id: int, max_length: int = 4
    ) -> List[List[int]]:
        if source_id == target_id:
            return [[source_id]]

        paths = []
        queue = deque([(source_id, [source_id])])
        visited = {source_id}

        while queue:
            current, path = queue.popleft()

            if len(path) > max_length:
                continue

            neighbors = self.storage.get_neighbors(current)
            for neighbor in neighbors:
                if neighbor.id == target_id:
                    paths.append(path + [neighbor.id])
                elif neighbor.id not in visited:
                    visited.add(neighbor.id)
                    queue.append((neighbor.id, path + [neighbor.id]))

        return paths

    def find_shortest_path(self, source_id: int, target_id: int) -> Optional[List[int]]:
        paths = self.find_paths(source_id, target_id, max_length=4)
        return min(paths, key=len) if paths else None

    def get_connected_components(self) -> List[List[int]]:
        visited: Set[int] = set()
        components = []

        all_nodes = self.storage.get_recent_nodes(limit=10000)
        node_ids = {n.id for n in all_nodes if n.id}

        for node_id in node_ids:
            if node_id not in visited:
                component = []
                queue = deque([node_id])

                while queue:
                    current = queue.popleft()
                    if current in visited:
                        continue
                    visited.add(current)
                    component.append(current)

                    neighbors = self.storage.get_neighbors(current)
                    for neighbor in neighbors:
                        if neighbor.id not in visited:
                            queue.append(neighbor.id)

                components.append(component)

        return components

    def get_bridges(self) -> List[Tuple[int, int]]:
        bridges = []
        all_edges = self.storage.fetch_all(
            "SELECT id, source_id, target_id FROM edges WHERE is_archived = 0", ()
        )

        for edge_row in all_edges:
            edge_id, src, tgt = (
                edge_row["id"],
                edge_row["source_id"],
                edge_row["target_id"],
            )
            self.storage.execute(
                "UPDATE edges SET is_archived = 1 WHERE id = ?", (edge_id,)
            )

            neighbors_src = {n.id for n in self.storage.get_neighbors(src)}
            neighbors_tgt = {n.id for n in self.storage.get_neighbors(tgt)}

            if tgt not in neighbors_src:
                bridges.append((src, tgt))

            self.storage.execute(
                "UPDATE edges SET is_archived = 0 WHERE id = ?", (edge_id,)
            )

        return bridges

    def get_page_rank(self, damping: float = 0.85, iterations: int = 20) -> dict:
        all_nodes = self.storage.get_recent_nodes(limit=10000)
        node_ids = [n.id for n in all_nodes if n.id]
        n = len(node_ids)

        if n == 0:
            return {}

        ranks = {nid: 1.0 / n for nid in node_ids}

        for _ in range(iterations):
            new_ranks = {}

            for node_id in node_ids:
                neighbors = self.storage.get_neighbors(node_id)
                rank_sum = 0.0

                for neighbor in neighbors:
                    neighbor_edges = self.storage.get_edges_for_node(neighbor.id)
                    out_degree = len(neighbor_edges)
                    if out_degree > 0:
                        edge = next(
                            (e for e in neighbor_edges if e.target_id == node_id), None
                        )
                        weight = edge.weight if edge else 1.0
                        rank_sum += ranks.get(neighbor.id, 0) * weight / out_degree

                new_ranks[node_id] = (1 - damping) / n + damping * rank_sum

            ranks = new_ranks

        return ranks

    def get_most_central_nodes(self, limit: int = 10) -> List[Tuple[int, float]]:
        ranks = self.get_page_rank()
        sorted_nodes = sorted(ranks.items(), key=lambda x: x[1], reverse=True)
        return sorted_nodes[:limit]

    def traverse_bfs(
        self, start_id: int, filter_fn: Callable[[MemoryNode], bool], max_depth: int = 3
    ) -> List[MemoryNode]:
        visited: Set[int] = {start_id}
        queue = deque([(start_id, 0)])
        results = []

        while queue:
            current_id, depth = queue.popleft()

            if depth > max_depth:
                continue

            node = self.storage.get_node_by_id(current_id)
            if node and filter_fn(node) and node.id not in {r.id for r in results}:
                results.append(node)

            neighbors = self.storage.get_neighbors(current_id)
            for neighbor in neighbors:
                if neighbor.id not in visited:
                    visited.add(neighbor.id)
                    queue.append((neighbor.id, depth + 1))

        return results

    def get_edge_types(self) -> List[str]:
        rows = self.storage.fetch_all(
            "SELECT DISTINCT edge_type FROM edges WHERE is_archived = 0", ()
        )
        return [row["edge_type"] for row in rows]

    def get_node_types(self) -> List[str]:
        rows = self.storage.fetch_all(
            "SELECT DISTINCT node_type FROM nodes WHERE is_archived = 0", ()
        )
        return [row["node_type"] for row in rows]


graph = MemoryGraph()
