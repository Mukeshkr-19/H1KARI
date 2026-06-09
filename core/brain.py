"""
Single public memory facade for HIKARI.

Brain v2 is the personal-memory authority when its policy is enabled. The
SQLite neural graph remains a quarantined legacy surface for explicit legacy
mode and maintenance tooling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

if TYPE_CHECKING:
    from core.neural_memory.models import MemoryNode, NodeType


def _neural_model_types():
    from core.neural_memory.models import MemoryNode, NodeType

    return MemoryNode, NodeType


def _NT():
    return _neural_model_types()[1]

_BAD_PERSON_NAMES = frozenset(
    {
        "who",
        "what",
        "where",
        "when",
        "why",
        "how",
        "having",
        "trouble",
        "glad",
        "baby",
        "user",
    }
)

_RELATION_ALIASES: Dict[str, Tuple[str, ...]] = {
    "sister": ("sister",),
    "brother": ("brother",),
    "father": ("father", "dad", "dads"),
    "mother": ("mother", "mom", "moms"),
    "parents": ("parents", "parent", "father", "dad", "mother", "mom"),
    "partner": ("girlfriend", "gf", "partner", "boyfriend"),
}

_CONFIDENCE_FLOOR = 0.45


from core.brain_statements import is_declarative_memory_statement  # noqa: F401 — re-export


@dataclass(frozen=True)
class BrainAnswer:
    text: str
    confidence: float = 0.0


@dataclass(frozen=True)
class BrainMemoryItem:
    """One ranked memory selected for the orchestrator context packet."""

    layer: str
    node_type: str
    name: str
    content: str
    score: float
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrainContextPacket:
    """Compact Brain v2 retrieval result for generation/tool context."""

    query: str
    items: List[BrainMemoryItem] = field(default_factory=list)
    confidence: float = 0.0
    strategies: List[str] = field(default_factory=list)

    def by_layer(self) -> Dict[str, List[BrainMemoryItem]]:
        grouped: Dict[str, List[BrainMemoryItem]] = {}
        for item in self.items:
            grouped.setdefault(item.layer, []).append(item)
        return grouped

    def to_prompt(self, limit: int = 8) -> str:
        if not self.items:
            return ""
        lines = ["[Brain context]"]
        for layer, items in self.by_layer().items():
            lines.append(f"{layer}:")
            for item in items[:limit]:
                detail = item.content or item.name
                if len(detail) > 140:
                    detail = detail[:137].rstrip() + "..."
                lines.append(f"- {item.name}: {detail}")
        return "\n".join(lines)


class HikariBrain:
    """Read-through interface over neural memory with structured personal-fact recall."""

    def __init__(self, neural_bridge=None):
        if neural_bridge is None:
            from core import neural_memory_bridge

            neural_bridge = neural_memory_bridge
        self.neural = neural_bridge

    def initialize(self) -> bool:
        try:
            return bool(self.neural.init_neural_memory())
        except Exception:
            return False

    def remember_turn(self, user_input: str, response: str, metadata: Optional[dict] = None) -> None:
        if not user_input:
            return
        try:
            if self.initialize():
                self.neural.remember(user_input, response or "", metadata or {})
        except Exception:
            pass

    def remember_fact(self, text: str) -> bool:
        """Store declarative personal facts in neural memory (structured compiler path)."""
        if not (text or "").strip():
            return False
        try:
            if not self.initialize():
                return False
            result = self.neural.learn_from_text(text.strip())
            return bool(result and result.get("success", True))
        except Exception:
            return False

    def build_context_packet(self, query: str, limit: int = 10) -> BrainContextPacket:
        """Return a compact, layered, scored memory packet for the orchestrator.

        This is the first Brain v2 contract: callers get selected memory with a
        layer label instead of raw neural retrieval dumps.
        """
        if not (query or "").strip() or not self.initialize():
            return BrainContextPacket(query=query or "")

        try:
            raw_packet = self.neural.get_memory_context(query)
            raw_nodes = getattr(raw_packet, "relevant_nodes", []) or []
            confidence = float(getattr(raw_packet, "confidence", 0.0) or 0.0)
            strategies = list(getattr(raw_packet, "retrieval_strategies_used", []) or [])
        except Exception:
            try:
                raw_nodes = list(self.neural.smart_query(query))
            except Exception:
                raw_nodes = []
            confidence = 0.0
            strategies = ["smart_query_fallback"] if raw_nodes else []

        items: List[BrainMemoryItem] = []
        for node in self._usable_nodes(raw_nodes):
            item = self._context_item_for_node(node, query, confidence)
            if item:
                items.append(item)

        items.sort(key=lambda item: item.score, reverse=True)
        return BrainContextPacket(
            query=query,
            items=items[:limit],
            confidence=min(confidence, 1.0),
            strategies=strategies,
        )

    def build_prompt_context(self, query: str, limit: int = 8) -> str:
        """Prompt-safe memory context built from the Brain v2 packet."""
        return self.build_context_packet(query, limit=limit).to_prompt(limit=limit)

    def is_memory_statement(self, text: str) -> bool:
        """Declarative facts to store — not questions."""
        return is_declarative_memory_statement(text)

    def is_personal_memory_question(self, text: str) -> bool:
        raw = (text or "").strip()
        q = raw.lower().rstrip("?").strip()
        if not q:
            return False
        if re.match(r"^(my\s+)?(dad|mom|mother|father|sister|brother|gf|girlfriend|partner)s?$", q):
            return True
        if not self._looks_like_question(raw):
            return False
        if self._asks_user_name(q) or "who am i" in q:
            return True
        if "return" in q and any(w in q for w in ("when", "whens", "when's", "flight")):
            return True
        if "flight" in q and any(w in q for w in ("when", "what", "whens", "when's")):
            return True
        if re.search(r"\bfull\s+name\b", q) and "sister" in q:
            return True
        if re.search(r"\b(?:mom|mother|dad|father)\s+live", q):
            return True
        if "where am i" in q or "where do i live" in q or "where are my parents" in q:
            return True
        if self._relation_from_query(q):
            return True
        if re.search(r"\bwhere\s+does\s+my\s+\w+\s+stud", q):
            return True
        if re.search(r"\b(?:do\s+(?:you|u)\s+)?know\s+my\s+(?:dad|father|mom|mother|gf|girlfriend|partner)\b", q):
            return True
        return False

    def has_local_person_match(self, text: str) -> bool:
        """True when 'who is <Name>' matches a stored PERSON/FACT node."""
        remainder = self._who_is_remainder((text or "").strip())
        if not remainder or remainder.lower().startswith("my "):
            return False
        if not self.initialize():
            return False
        return self._match_person_record(remainder) is not None

    def answer(self, query: str) -> Optional[BrainAnswer]:
        if not self.is_personal_memory_question(query):
            return None
        if not self.initialize():
            return None

        lowered = (query or "").strip().lower()
        nodes = self._gather_nodes(lowered)

        if self._asks_user_name(lowered) or "who am i" in lowered:
            ans = self._answer_identity(nodes, lowered)
            return self._finalize(ans)

        if "where do i live" in lowered:
            return self._finalize(self._answer_permanent_home(nodes))

        if "where am i" in lowered:
            return self._finalize(self._answer_current_location(nodes))

        if "where are my parents" in lowered:
            return self._finalize(self._answer_parents_location(nodes))

        if "return" in lowered or "flight" in lowered:
            return self._finalize(self._answer_flight(nodes))

        remainder = self._who_is_remainder(query)
        if remainder and not remainder.lower().startswith("my "):
            hit = self._match_person_record(remainder, nodes)
            if hit:
                return self._finalize(hit)

        relation = self._relation_from_query(lowered)
        if relation:
            ans = self._answer_relation(relation, nodes, lowered)
            return self._finalize(ans)

        return None

    def get_current_location(self) -> Optional[str]:
        if not self.initialize():
            return None
        nodes = self._usable_nodes(self.neural.smart_query("current_location"))
        for node in self._nodes_by_kind(nodes, "current_location"):
            loc = self._node_meta(node).get("location")
            if loc:
                return self._clean_display_location(str(loc))
        return None

    def summarize_user(self) -> Optional[str]:
        """Structured identity summary — no raw PERSON dump."""
        if not self.initialize():
            return None
        nodes = self._gather_nodes(
            "who am i where do i live where am i sister brother father mother girlfriend partner education"
        )
        lines: List[str] = []

        identity = self._nodes_by_kind(nodes, "identity")
        if identity:
            name = self._best_identity_name(identity)
            if name and name.lower() not in _BAD_PERSON_NAMES:
                lines.append(f"- Name: {name}")
            official = self._best_official_name(identity)
            if official and official != name:
                lines.append(f"- Official name: {official}")

        for node in self._nodes_by_kind(nodes, "education"):
            school = self._node_meta(node).get("school")
            if school:
                lines.append(f"- Education: {school}")

        for node in self._nodes_by_kind(nodes, "permanent_home"):
            loc = self._node_meta(node).get("location")
            if loc:
                lines.append(f"- Home: {loc}")

        for node in self._nodes_by_kind(nodes, "current_location"):
            meta = self._node_meta(node)
            loc = meta.get("location")
            ctx = meta.get("time_context")
            if loc:
                line = f"- Currently in: {loc}"
                if ctx:
                    line += f" ({ctx})"
                lines.append(line)

        for rel, label in (
            ("sister", "Sister"),
            ("brother", "Brother"),
            ("father", "Dad"),
            ("mother", "Mom"),
            ("partner", "Partner"),
        ):
            rec = self._relation_record(nodes, rel)
            if not rec.get("person_name"):
                continue
            name = rec.get("full_name") or rec["person_name"]
            extra = []
            if rec.get("location"):
                extra.append(f"lives in {rec['location']}")
            if rec.get("school"):
                extra.append(f"studies at {rec['school']}")
            suffix = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"- {label}: {name}{suffix}")

        if not lines:
            return None
        return "What I know about you:\n" + "\n".join(lines)

    def _finalize(self, ans: Optional[BrainAnswer]) -> Optional[BrainAnswer]:
        if not ans or ans.confidence < _CONFIDENCE_FLOOR:
            return None
        return ans

    def _gather_nodes(self, query: str) -> List[MemoryNode]:
        terms = set(re.findall(r"[a-zA-Z][a-zA-Z']+", query))
        relation = self._relation_from_query(query)
        if relation:
            terms.update(_RELATION_ALIASES[relation])
        if "where" in query:
            terms.update(("permanent_home", "current_location", "location", "live", "lives"))
        if "flight" in query:
            terms.update(("flight", "travel", "depart", "arrive"))
        search_text = " ".join(sorted(terms)) or query
        hits = []
        try:
            packet = self.neural.get_memory_context(search_text)
            hits.extend(getattr(packet, "relevant_nodes", []) or [])
        except Exception:
            pass
        try:
            hits.extend(list(self.neural.smart_query(search_text)))
        except Exception:
            pass
        unique: dict[tuple[str, str], MemoryNode] = {}
        for node in hits:
            key = (
                str(getattr(node, "node_type", "") or ""),
                str(getattr(node, "name", "") or "").lower(),
            )
            unique[key] = node
        return self._usable_nodes(unique.values())

    def _usable_nodes(self, nodes: Iterable) -> List[MemoryNode]:
        usable = []
        for node in nodes:
            name = (getattr(node, "name", "") or "").strip()
            content = (getattr(node, "content", "") or "").strip()
            if not name and not content:
                continue
            if name.lower() in _BAD_PERSON_NAMES:
                continue
            if self._looks_like_qa_artifact(f"{name} {content}".lower()):
                continue
            meta = self._node_meta(node)
            if (
                getattr(node, "node_type", "") == _NT().PERSON.value
                and not meta.get("kind")
                and not meta.get("relation")
                and float(getattr(node, "salience", 0) or 0) < 0.9
            ):
                continue
            usable.append(node)
        return usable

    def _node_meta(self, node: MemoryNode) -> Dict[str, Any]:
        meta = getattr(node, "metadata", None) or {}
        return meta if isinstance(meta, dict) else {}

    def _context_item_for_node(
        self, node: MemoryNode, query: str, packet_confidence: float
    ) -> Optional[BrainMemoryItem]:
        meta = self._node_meta(node)
        layer = self._layer_for_node(node, meta)
        name = (getattr(node, "name", "") or "").strip()
        content = (getattr(node, "content", "") or name).strip()
        if not name and not content:
            return None
        score, reason = self._context_score(node, meta, query, packet_confidence)
        if score < 0.18:
            return None
        return BrainMemoryItem(
            layer=layer,
            node_type=getattr(node, "node_type", "") or "",
            name=name or content[:60],
            content=content,
            score=score,
            reason=reason,
            metadata=meta,
        )

    def _layer_for_node(self, node: MemoryNode, meta: Dict[str, Any]) -> str:
        kind = str(meta.get("kind") or "").lower()
        node_type = getattr(node, "node_type", "") or ""
        if kind in {
            "identity",
            "relation",
            "permanent_home",
            "current_location",
            "education",
        }:
            return "semantic"
        if kind in {"travel"}:
            return "episodic"
        if node_type in {_NT().EPISODE.value, _NT().CONVERSATION.value, _NT().EVENT.value}:
            return "episodic"
        if node_type in {
            _NT().SKILL.value,
            _NT().TOOL.value,
            _NT().RULE.value,
            _NT().ROUTINE.value,
            _NT().PREFERENCE.value,
        }:
            return "procedural"
        if node_type in {_NT().PERSON.value, _NT().LOCATION.value, _NT().FACT.value}:
            return "semantic"
        return "working"

    def _context_score(
        self,
        node: MemoryNode,
        meta: Dict[str, Any],
        query: str,
        packet_confidence: float,
    ) -> Tuple[float, str]:
        q_tokens = {
            token
            for token in re.findall(r"[a-zA-Z][a-zA-Z']+", (query or "").lower())
            if len(token) > 2
        }
        haystack = " ".join(
            [
                str(getattr(node, "name", "") or ""),
                str(getattr(node, "content", "") or ""),
                " ".join(str(v) for v in meta.values() if isinstance(v, (str, int, float))),
            ]
        ).lower()
        overlap = sum(1 for token in q_tokens if token in haystack)
        task_score = min(1.0, overlap / max(1, min(len(q_tokens), 5)))
        salience = float(getattr(node, "salience", 0.5) or 0.5)
        layer = self._layer_for_node(node, meta)
        layer_boost = 0.12 if layer in {"semantic", "procedural"} else 0.06
        relation = meta.get("relation")
        if relation and self._relation_from_query((query or "").lower()) == relation:
            task_score = max(task_score, 0.9)
        score = (
            task_score * 0.45
            + salience * 0.3
            + min(packet_confidence, 1.0) * 0.15
            + layer_boost
        )
        reason = "task" if task_score >= 0.5 else "salience"
        if relation:
            reason = f"{reason}:{relation}"
        return min(score, 1.0), reason

    def _nodes_by_kind(self, nodes: List[MemoryNode], kind: str) -> List[MemoryNode]:
        matched = [n for n in nodes if self._node_meta(n).get("kind") == kind]
        matched.sort(
            key=lambda n: (
                float(getattr(n, "salience", 0) or 0),
                str(getattr(n, "updated_at", "") or ""),
            ),
            reverse=True,
        )
        return matched

    def _answer_permanent_home(self, nodes: List[MemoryNode]) -> Optional[BrainAnswer]:
        for node in self._nodes_by_kind(nodes, "permanent_home"):
            loc = self._node_meta(node).get("location")
            if loc:
                loc = self._clean_display_location(str(loc))
                return BrainAnswer(f"You live in {loc}.", 0.9)
            m = re.search(r"live[s]?\s+in\s+([A-Za-z][\w\s]+)", node.content or "", re.I)
            if m:
                return BrainAnswer(f"You live in {m.group(1).strip().title()}.", 0.82)
        for node in nodes:
            text = f"{node.name} {node.content}".lower()
            if "live in" in text and "currently" not in text:
                m = re.search(r"live[s]?\s+in\s+([A-Za-z][\w\s]+)", text, re.I)
                if m:
                    return BrainAnswer(f"You live in {m.group(1).strip().title()}.", 0.7)
        return None

    def _answer_current_location(self, nodes: List[MemoryNode]) -> Optional[BrainAnswer]:
        for node in self._nodes_by_kind(nodes, "current_location"):
            meta = self._node_meta(node)
            loc = meta.get("location")
            ctx = meta.get("time_context")
            if loc:
                loc = self._clean_display_location(str(loc))
                if ctx:
                    return BrainAnswer(
                        f"You're currently in {loc} for {ctx}.", 0.92
                    )
                return BrainAnswer(f"You're currently in {loc}.", 0.9)
        return None

    def _answer_parents_location(self, nodes: List[MemoryNode]) -> Optional[BrainAnswer]:
        parts = []
        for rel, label in (("father", "dad"), ("mother", "mom")):
            rec = self._relation_record(nodes, rel)
            if not rec:
                continue
            name = rec.get("person_name") or rec.get("name")
            loc = rec.get("location")
            if name and loc:
                parts.append(f"Your {label} {name} lives in {loc}")
            elif name:
                parts.append(f"Your {label} is {name}")
        if parts:
            return BrainAnswer(". ".join(parts) + ".", 0.85)
        return None

    def _relation_record(self, nodes: List[MemoryNode], relation: str) -> Dict[str, Any]:
        for node in nodes:
            meta = self._node_meta(node)
            if meta.get("kind") == "relation" and meta.get("relation") == relation:
                return meta
        for node in nodes:
            if (node.name or "").lower() == f"user:{relation}":
                return self._node_meta(node)
            text = f"{node.name} {node.content}".lower()
            if f"user's {relation}" in text or f"{relation} is" in text:
                meta = dict(self._node_meta(node))
                m = re.search(
                    rf"{relation}\s+is\s+([A-Za-z][\w\s]+?)(?:\s+and\s+lives?\s+in\s+([A-Za-z][\w\s]+))?",
                    node.content or "",
                    re.I,
                )
                if m:
                    meta.setdefault("person_name", m.group(1).strip().title())
                    if m.group(2):
                        meta.setdefault("location", m.group(2).strip().title())
                return meta
        return {}

    def _answer_identity(
        self, nodes: List[MemoryNode], query: str = ""
    ) -> Optional[BrainAnswer]:
        identity_nodes = self._nodes_by_kind(nodes, "identity")
        if any(word in query for word in ("official", "offical", "legal", "real")):
            official = self._best_official_name(identity_nodes)
            if official:
                return BrainAnswer(f"Your official name is {official}.", 0.9)
        name = self._best_identity_name(identity_nodes)
        if name and name.lower() not in _BAD_PERSON_NAMES:
            return BrainAnswer(f"Your name is {name}.", 0.88)
        for node in nodes:
            if getattr(node, "node_type", "") == _NT().PERSON.value:
                if (node.content or "").lower().startswith("primary user"):
                    return BrainAnswer(f"Your name is {node.name}.", 0.75)
        return None

    def _best_identity_name(self, nodes: List[MemoryNode]) -> Optional[str]:
        for node in nodes:
            meta = self._node_meta(node)
            if meta.get("name_type") == "official":
                continue
            name = meta.get("person_name") or (
                node.name if getattr(node, "node_type", "") == _NT().PERSON.value else None
            )
            if name and str(name).lower() not in _BAD_PERSON_NAMES:
                return str(name)
        return None

    def _best_official_name(self, nodes: List[MemoryNode]) -> Optional[str]:
        for node in nodes:
            meta = self._node_meta(node)
            name = meta.get("official_name")
            if name and str(name).lower() not in _BAD_PERSON_NAMES:
                return str(name)
        return None

    def _answer_flight(self, nodes: List[MemoryNode]) -> Optional[BrainAnswer]:
        for node in nodes:
            if (node.name or "").lower() == "user:travel_return":
                meta = self._node_meta(node)
                dep = meta.get("return_depart_date", "")
                dep_t = meta.get("return_depart_time", "")
                arr = meta.get("return_arrival_date", "")
                arr_t = meta.get("return_arrival_time", "")
                dest = meta.get("return_destination", "")
                if dep:
                    line = f"Your return flight is {dep}"
                    if dep_t:
                        line += f" {dep_t}"
                    if dest and arr:
                        line += (
                            f", and you expect to reach {dest} on {arr}"
                            + (f" {arr_t}" if arr_t else "")
                            + " local time"
                        )
                    return BrainAnswer(line + ".", 0.92)
            meta = self._node_meta(node)
            if meta.get("kind") == "travel":
                dep = meta.get("return_depart_date", "")
                if dep:
                    return self._answer_flight([node])
        for node in nodes:
            if "return flight" in (node.content or "").lower():
                return BrainAnswer((node.content or "").rstrip(".") + ".", 0.75)
        return None

    def _match_person_record(
        self, name_query: str, nodes: Optional[List[MemoryNode]] = None
    ) -> Optional[BrainAnswer]:
        q = (name_query or "").strip().rstrip("?.!")
        if len(q) < 2:
            return None
        pool = nodes if nodes is not None else self._gather_nodes(q)
        q_low = q.lower()
        for node in pool:
            meta = self._node_meta(node)
            for field in ("person_name", "relation"):
                if field == "relation" and meta.get(field) == "partner":
                    pname = meta.get("person_name", "")
                    if pname and (pname.lower() == q_low or pname.lower().startswith(q_low)):
                        return self._partner_answer_from_meta(meta, pname)
            pname = meta.get("person_name") or (
                node.name if getattr(node, "node_type", "") == _NT().PERSON.value else ""
            )
            if not pname:
                continue
            pl = pname.lower()
            if pl == q_low or pl.startswith(q_low) or q_low.startswith(pl.split()[0]):
                if meta.get("relation") == "partner":
                    return self._partner_answer_from_meta(meta, pname)
                if meta.get("relation") in ("father", "mother"):
                    rel = meta.get("relation")
                    label = "dad" if rel == "father" else "mom"
                    loc = meta.get("location")
                    if loc:
                        return BrainAnswer(f"Your {label} is {pname}. He lives in {loc}.", 0.88)
                    return BrainAnswer(f"Your {label} is {pname}.", 0.85)
                return BrainAnswer(f"{pname} is in your saved contacts.", 0.7)
            if node.name and node.name.lower() == q_low:
                if meta.get("relation") == "partner":
                    return self._partner_answer_from_meta(meta, node.name)
        return None

    def _partner_answer_from_meta(self, meta: Dict[str, Any], name: str) -> BrainAnswer:
        parts = [f"{name} is your girlfriend."]
        if meta.get("location"):
            parts.append(f" She lives in {meta['location']}.")
        if meta.get("school") and not meta.get("school_private"):
            parts.append(f" She studies at {meta['school']}.")
        return BrainAnswer("".join(parts), 0.88)

    def _answer_relation(
        self, relation: str, nodes: List[MemoryNode], query: str
    ) -> Optional[BrainAnswer]:
        label = (
            "dad"
            if relation == "father"
            else "mom"
            if relation == "mother"
            else "girlfriend"
            if relation == "partner"
            else relation
        )
        if re.search(r"\bfull\s+name\b", query) and relation == "sister":
            rec = self._relation_record(nodes, "sister")
            full = rec.get("full_name") or rec.get("person_name")
            if full:
                return BrainAnswer(f"Your sister's full name is {full}.", 0.92)

        if re.search(r"\b(?:name|who)\b", query):
            rec = self._relation_record(nodes, relation)
            person = rec.get("full_name") or rec.get("person_name")
            if not person:
                person = self._best_person_for_relation(nodes, _RELATION_ALIASES[relation])
            if person:
                if relation in ("sister", "brother"):
                    extras = []
                    if rec.get("school"):
                        extras.append(f"Studies at {rec['school']}.")
                    if rec.get("location"):
                        extras.append(f"Lives in {rec['location']}.")
                    suffix = " " + " ".join(extras) if extras else ""
                    return BrainAnswer(f"Your {label} is {person}.{suffix}", 0.88)
                return BrainAnswer(f"Your {label}'s name is {person}.", 0.88)

        if relation in ("mother", "father") and re.search(
            r"\b(?:live|lives|living)\b.*\b(?:dad|father|parents)\b", query
        ):
            mom = self._relation_record(nodes, "mother")
            dad = self._relation_record(nodes, "father")
            if mom.get("location") and dad.get("location") and mom["location"] == dad["location"]:
                return BrainAnswer(
                    f"Yes — your mom {mom.get('person_name', '')} lives with your dad "
                    f"in {mom['location']}.".replace("  ", " ").strip(),
                    0.9,
                )

        if relation in ("father", "mother", "parents"):
            rec = self._relation_record(nodes, relation if relation != "parents" else "father")
            mom = self._relation_record(nodes, "mother") if relation == "parents" else {}
            label = "dad" if relation == "father" else "mom" if relation == "mother" else "parent"
            if relation == "parents":
                chunks = []
                for rel, lbl, r in (("father", "dad", rec), ("mother", "mom", mom)):
                    if r.get("person_name"):
                        line = f"Your {lbl} is {r['person_name']}"
                        if r.get("location"):
                            line += f" (lives in {r['location']})"
                        chunks.append(line)
                if chunks:
                    return BrainAnswer(". ".join(chunks) + ".", 0.88)
            if rec.get("person_name"):
                line = f"Your {label} is {rec['person_name']}"
                if rec.get("location"):
                    pronoun = "She" if relation == "mother" else "He"
                    line += f". {pronoun} lives in {rec['location']}"
                return BrainAnswer(line + ".", 0.9)

        if relation == "partner":
            rec = self._relation_record(nodes, "partner")
            if rec.get("person_name"):
                if "did" in query and "what" in query:
                    return BrainAnswer(
                        f"I know {rec['person_name']} is your girlfriend. What did she do?",
                        0.9,
                    )
                return self._partner_answer_from_meta(
                    rec, rec["person_name"]
                )
            return BrainAnswer(
                "I don't have your partner saved yet. Tell me their name and I'll remember it.", 0.55
            )

        rec = self._relation_record(nodes, relation)
        person = rec.get("full_name") or rec.get("person_name")
        if not person:
            person = self._best_person_for_relation(nodes, _RELATION_ALIASES[relation])
        facts = self._facts_for_terms(nodes, _RELATION_ALIASES[relation], person)
        label = relation
        if "stud" in query:
            study = self._best_study_fact(facts)
            if study and person:
                return BrainAnswer(f"{person} {study}.", 0.82)
            return None
        if person:
            extras = []
            study = self._best_study_fact(facts)
            loc = self._best_location_fact(facts)
            if study:
                extras.append(study)
            if loc:
                extras.append(loc)
            suffix = (
                " " + " ".join(self._sentence(x) for x in extras[:2]) if extras else ""
            )
            return BrainAnswer(f"Your {label} is {person}.{suffix}", 0.85)
        return BrainAnswer(
            f"I don't have your {label} saved yet. Tell me their name and I'll remember it.",
            0.55,
        )

    def _looks_like_question(self, text: str) -> bool:
        q = (text or "").strip().lower()
        if q.endswith("?"):
            return True
        return bool(
            re.match(
                r"^(who|whos|who's|what|whats|what's|where|when|whens|when's|do|does|did|is|are|am|can|could|tell me)\b",
                q,
            )
        )

    def _who_is_remainder(self, text: str) -> Optional[str]:
        m = re.match(r"^\s*who\s+is\s+(.+?)\s*\??\s*$", (text or "").strip(), re.I)
        return m.group(1).strip() if m else None

    def _relation_from_query(self, query: str) -> Optional[str]:
        q = (query or "").strip().lower().rstrip("?")
        if re.match(r"^(dad|father)s?$", q):
            return "father"
        if re.match(r"^(mom|mother)s?$", q):
            return "mother"
        if re.match(r"^(gf|girlfriend|partner|boyfriend)s?$", q):
            return "partner"
        for relation, aliases in _RELATION_ALIASES.items():
            if any(re.search(rf"\b{re.escape(alias)}s?\b", query) for alias in aliases):
                return relation
        return None

    def _asks_user_name(self, query: str) -> bool:
        return bool(
            re.search(
                r"\b(what'?s|whats|what is)\s+my\s+(?:official\s+|offical\s+|legal\s+|real\s+)?name\b",
                query,
            )
        )

    def _looks_like_qa_artifact(self, text: str) -> bool:
        return any(
            bad in text
            for bad in (
                "who is my",
                "whos my",
                "what is my",
                "i didn't catch",
                "e.g.",
                "try asking",
                "searched for",
            )
        )

    def _best_person_for_relation(self, nodes: list, aliases: tuple[str, ...]) -> Optional[str]:
        best = None
        best_score = -1.0
        for node in nodes:
            meta = self._node_meta(node)
            if meta.get("person_name") and meta.get("relation") in (
                "sister",
                "brother",
                "father",
                "mother",
                "partner",
            ):
                if any(alias in (meta.get("relation") or "") for alias in aliases):
                    score = float(getattr(node, "salience", 0.5) or 0.5) + 0.35
                    if meta.get("full_name"):
                        score += 0.15
                    if score > best_score:
                        best = meta.get("full_name") or meta["person_name"]
                        best_score = score
            if getattr(node, "node_type", "") == _NT().PERSON.value:
                content = (getattr(node, "content", "") or "").lower()
                if "full name" in content or "user's" in content:
                    if any(alias in content for alias in aliases):
                        score = float(getattr(node, "salience", 0.5) or 0.5) + 0.4
                        if score > best_score:
                            best = getattr(node, "name", None)
                            best_score = score
            text = f"{getattr(node, 'name', '')} {getattr(node, 'content', '')}".lower()
            if not any(alias in text for alias in aliases):
                continue
            m = re.search(
                rf"(?:user's|my)\s+(?:{'|'.join(aliases)})\s+is\s+([A-Za-z][a-zA-Z]+(?:\s+[A-Za-z][a-zA-Z]+)?)",
                text,
                re.I,
            )
            if m:
                cand = m.group(1).strip().title()
                if cand.lower() not in _BAD_PERSON_NAMES and len(cand.split()) <= 4:
                    score = float(getattr(node, "salience", 0.5) or 0.5) + 0.2
                    if score > best_score:
                        best = cand
                        best_score = score
        return best

    def _facts_for_terms(
        self, nodes: list, terms: tuple[str, ...], person: Optional[str] = None
    ) -> list[str]:
        facts = []
        person_l = (person or "").lower()
        for node in nodes:
            text = (getattr(node, "content", "") or getattr(node, "name", "") or "").strip()
            low = text.lower()
            if self._looks_like_qa_artifact(low):
                continue
            if any(term in low for term in terms) or (person_l and person_l in low):
                facts.append(text)
        return facts

    def _best_study_fact(self, facts: list[str]) -> Optional[str]:
        for fact in facts:
            m = re.search(r"\b(studies(?:\s+[A-Za-z]+)?\s+(?:at|in)\s+[^.]+)", fact, re.I)
            if m:
                return m.group(1).strip()
            m = re.search(r"\bschool:\s*([^.;]+)", fact, re.I)
            if m:
                return f"studies at {m.group(1).strip()}"
        return None

    def _best_location_fact(self, facts: list[str]) -> Optional[str]:
        for fact in facts:
            m = re.search(r"\b(lives?\s+(?:in|at)\s+[^.]+)", fact, re.I)
            if m:
                return re.split(r"\bgot it\b", m.group(1), flags=re.I)[0].strip()
        return None

    def _sentence(self, text: str) -> str:
        cleaned = (text or "").strip().rstrip(".")
        if not cleaned:
            return ""
        return cleaned[0].upper() + cleaned[1:] + "."

    def _clean_display_location(self, location: str) -> str:
        parts = []
        stop = {
            "studing",
            "studying",
            "studies",
            "study",
            "flew",
            "fly",
            "flying",
            "came",
            "come",
            "moved",
            "back",
            "because",
            "for",
        }
        for token in (location or "").split():
            cleaned = token.strip(".,;:!?'\"")
            if cleaned.lower() in stop:
                break
            if cleaned:
                parts.append(cleaned)
        return " ".join(parts) or location
