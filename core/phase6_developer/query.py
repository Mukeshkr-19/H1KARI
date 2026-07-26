"""Pure, deterministic repository query and ranking.

No I/O, no network, no subprocess, and no external state is accessed. The query
engine operates entirely on the immutable ``RepositoryIndex`` built by the read-
only repository adapter.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from core.phase6_developer.contracts import (
    FileKind,
    FileSnapshot,
    RelationshipType,
    RepositoryHit,
    RepositoryIndex,
    RepositoryQuery,
    RepositoryQueryResult,
    RepositoryReason,
    ScoreBreakdown,
    SymbolRecord,
)


# --- Tokenization -----------------------------------------------------------------------


_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")


def _tokenize(text: str) -> Tuple[str, ...]:
    return tuple(t.lower() for t in _SPLIT_RE.split(text) if t)


# --- Scoring helpers ----------------------------------------------------------------------


def _normalize(name: str) -> str:
    return name.lower().replace("_", " ")


def _score_tokens(query_tokens: Tuple[str, ...], target: str) -> int:
    if not query_tokens:
        return 0
    normalized_target = _normalize(target)
    target_tokens = _tokenize(normalized_target)
    return sum(1 for token in query_tokens if token in target_tokens)


def _has_all_tokens(query_tokens: Tuple[str, ...], target: str) -> bool:
    if not query_tokens:
        return False
    normalized_target = _normalize(target)
    target_tokens = set(_tokenize(normalized_target))
    return all(token in target_tokens for token in query_tokens)


# --- Query evaluation ---------------------------------------------------------------------


def _dedupe_key(hit: RepositoryHit) -> Tuple[str, int]:
    return (hit.relative_path, hit.line_start)


def _symbol_hit(
    symbol: SymbolRecord,
    file: FileSnapshot,
    score_breakdown: ScoreBreakdown,
) -> RepositoryHit:
    return RepositoryHit(
        relative_path=symbol.relative_path,
        symbol=symbol.name,
        line_start=symbol.line_start,
        line_end=symbol.line_end or symbol.line_start,
        excerpt=file.lines,
        score=score_breakdown.total(),
        score_breakdown=score_breakdown,
        provenance=f"{symbol.relative_path}:{symbol.line_start}",
    )


def _lexical_score(text: str, query_tokens: Tuple[str, ...]) -> ScoreBreakdown:
    normalized = _normalize(text)
    exact = 1 if text.lower() == " ".join(query_tokens) else 0
    if exact:
        return ScoreBreakdown(exact_match=1)
    token = _score_tokens(query_tokens, normalized)
    substring = 1 if " ".join(query_tokens) in normalized and query_tokens else 0
    return ScoreBreakdown(token_match=token, substring_match=substring)


def _evaluate_symbol_query(index: RepositoryIndex, query: RepositoryQuery) -> List[RepositoryHit]:
    hits: Dict[Tuple[str, int], RepositoryHit] = {}
    query_tokens = _tokenize(query.query_text)

    for symbol in index.symbols:
        file = next((f for f in index.files if f.relative_path == symbol.relative_path), None)
        if file is None:
            continue

        score = ScoreBreakdown()
        if symbol.name.lower() == query.query_text.lower():
            score = ScoreBreakdown(exact_match=1)
        elif _has_all_tokens(query_tokens, symbol.name):
            score = ScoreBreakdown(token_match=len(query_tokens))
        elif query_tokens and query.query_text.lower() in symbol.name.lower():
            score = ScoreBreakdown(substring_match=1)

        if score.total() > 0:
            hit = _symbol_hit(symbol, file, score)
            key = _dedupe_key(hit)
            if key not in hits or hits[key].score < hit.score:
                hits[key] = hit

    return list(hits.values())


def _evaluate_path_query(index: RepositoryIndex, query: RepositoryQuery) -> List[RepositoryHit]:
    hits: List[RepositoryHit] = []
    query_lower = query.query_text.lower()
    query_tokens = _tokenize(query.query_text)

    for file in index.files:
        score = ScoreBreakdown()
        if file.relative_path.lower() == query_lower:
            score = ScoreBreakdown(exact_match=1)
        elif query_tokens and all(token in file.relative_path.lower() for token in query_tokens):
            score = ScoreBreakdown(token_match=len(query_tokens))
        elif query_lower in file.relative_path.lower():
            score = ScoreBreakdown(substring_match=1)

        if score.total() > 0:
            hits.append(
                RepositoryHit(
                    relative_path=file.relative_path,
                    symbol=None,
                    line_start=1,
                    line_end=min(file.line_count, 12) or 1,
                    excerpt=file.lines,
                    score=score.total(),
                    score_breakdown=score,
                    provenance=file.relative_path,
                )
            )

    return hits


def _evaluate_file_type_query(index: RepositoryIndex, query: RepositoryQuery) -> List[RepositoryHit]:
    hits: List[RepositoryHit] = []
    kind_value = query.query_text.lower()

    try:
        target_kind = FileKind(kind_value)
    except ValueError:
        return hits

    for file in index.files:
        if file.kind is target_kind:
            score = ScoreBreakdown(exact_match=1)
            hits.append(
                RepositoryHit(
                    relative_path=file.relative_path,
                    symbol=None,
                    line_start=1,
                    line_end=min(file.line_count, 12) or 1,
                    excerpt=file.lines,
                    score=score.total(),
                    score_breakdown=score,
                    provenance=file.relative_path,
                )
            )

    return hits


def _evaluate_heading_query(index: RepositoryIndex, query: RepositoryQuery) -> List[RepositoryHit]:
    hits: Dict[Tuple[str, int], RepositoryHit] = {}
    query_tokens = _tokenize(query.query_text)

    for symbol in index.symbols:
        if symbol.kind != "heading":
            continue
        score = ScoreBreakdown()
        if symbol.name.lower() == query.query_text.lower():
            score = ScoreBreakdown(exact_match=1)
        elif _has_all_tokens(query_tokens, symbol.name):
            score = ScoreBreakdown(token_match=len(query_tokens))
        elif query.query_text.lower() in symbol.name.lower():
            score = ScoreBreakdown(substring_match=1)

        if score.total() > 0:
            file = next((f for f in index.files if f.relative_path == symbol.relative_path), None)
            if file is None:
                continue
            hit = _symbol_hit(symbol, file, score)
            key = _dedupe_key(hit)
            if key not in hits or hits[key].score < hit.score:
                hits[key] = hit

    return list(hits.values())


def _evaluate_relationship_query(index: RepositoryIndex, query: RepositoryQuery) -> List[RepositoryHit]:
    hits: Dict[Tuple[str, int], RepositoryHit] = {}
    query_lower = query.query_text.lower()
    query_tokens = _tokenize(query.query_text)

    for edge in index.edges:
        if query_lower not in edge.source_id.lower() and query_lower not in edge.target_id.lower():
            continue
        if query.query_text and not (_has_all_tokens(query_tokens, edge.source_id) or _has_all_tokens(query_tokens, edge.target_id)):
            if query_lower not in edge.source_id.lower() and query_lower not in edge.target_id.lower():
                continue

        file = next((f for f in index.files if f.relative_path == edge.provenance.split(":")[0]), None)
        if file is None:
            continue

        score = ScoreBreakdown(exact_match=1, relationship_bonus=1)
        hit = RepositoryHit(
            relative_path=file.relative_path,
            symbol=edge.target_id,
            line_start=1,
            line_end=min(file.line_count, 12) or 1,
            excerpt=file.lines,
            score=score.total(),
            score_breakdown=score,
            provenance=edge.provenance,
        )
        key = _dedupe_key(hit)
        if key not in hits or hits[key].score < hit.score:
            hits[key] = hit

    return list(hits.values())


def _evaluate_tests_query(index: RepositoryIndex, query: RepositoryQuery) -> List[RepositoryHit]:
    hits: Dict[Tuple[str, int], RepositoryHit] = {}
    query_tokens = _tokenize(query.query_text)
    query_lower = query.query_text.lower()

    test_symbols = [
        symbol for symbol in index.symbols
        if symbol.name.startswith("test_") or "test" in symbol.relative_path.lower()
    ]

    for symbol in test_symbols:
        file = next((f for f in index.files if f.relative_path == symbol.relative_path), None)
        if file is None:
            continue

        score = ScoreBreakdown()
        if query_lower and (query_lower in symbol.name.lower() or query_lower in symbol.relative_path.lower()):
            if symbol.name.lower() == query_lower:
                score = ScoreBreakdown(exact_match=1)
            elif _has_all_tokens(query_tokens, symbol.name):
                score = ScoreBreakdown(token_match=len(query_tokens))
            else:
                score = ScoreBreakdown(substring_match=1)

        if score.total() > 0:
            hit = _symbol_hit(symbol, file, score)
            key = _dedupe_key(hit)
            if key not in hits or hits[key].score < hit.score:
                hits[key] = hit

    return list(hits.values())


def _evaluate_lexical_query(index: RepositoryIndex, query: RepositoryQuery) -> List[RepositoryHit]:
    hits: List[RepositoryHit] = []
    query_tokens = _tokenize(query.query_text)
    query_lower = query.query_text.lower()

    for file in index.files:
        if file.is_binary:
            continue
        score_breakdown = _lexical_score(" ".join(file.lines), query_tokens)
        if score_breakdown.total() > 0:
            hits.append(
                RepositoryHit(
                    relative_path=file.relative_path,
                    symbol=None,
                    line_start=1,
                    line_end=min(file.line_count, 12) or 1,
                    excerpt=file.lines,
                    score=score_breakdown.total(),
                    score_breakdown=score_breakdown,
                    provenance=file.relative_path,
                )
            )
        elif not query.exact_only and query_lower:
            for line in file.lines:
                if query_lower in line.lower():
                    score_breakdown = ScoreBreakdown(substring_match=1)
                    hits.append(
                        RepositoryHit(
                            relative_path=file.relative_path,
                            symbol=None,
                            line_start=1,
                            line_end=min(file.line_count, 12) or 1,
                            excerpt=file.lines,
                            score=score_breakdown.total(),
                            score_breakdown=score_breakdown,
                            provenance=file.relative_path,
                        )
                    )
                    break

    return hits


# --- Public API -------------------------------------------------------------------------


def evaluate_query(index: RepositoryIndex, query: RepositoryQuery) -> RepositoryQueryResult:
    """Evaluate ``query`` against ``index`` and return ranked hits.

    The result is deterministic and bounded. Empty or whitespace-only queries
    return no hits. Malformed queries are treated as empty results.
    """
    if not isinstance(index, RepositoryIndex):
        return RepositoryQueryResult(hits=(), reason=RepositoryReason.MALFORMED_REQUEST)
    if not isinstance(query, RepositoryQuery):
        return RepositoryQueryResult(hits=(), reason=RepositoryReason.MALFORMED_REQUEST)

    query_text = query.query_text.strip()
    if not query_text:
        return RepositoryQueryResult(hits=(), reason=RepositoryReason.EMPTY_QUERY)

    kind = (query.kind or "symbol").lower()
    dispatch = {
        "symbol": _evaluate_symbol_query,
        "path": _evaluate_path_query,
        "file_type": _evaluate_file_type_query,
        "heading": _evaluate_heading_query,
        "relationship": _evaluate_relationship_query,
        "tests": _evaluate_tests_query,
        "lexical": _evaluate_lexical_query,
    }

    evaluator = dispatch.get(kind)
    if evaluator is None:
        return RepositoryQueryResult(hits=(), reason=RepositoryReason.MALFORMED_REQUEST)
    hits = evaluator(index, query)

    seen: Set[Tuple[str, int]] = set()
    unique: List[RepositoryHit] = []
    for hit in sorted(hits, key=lambda h: (-h.score, h.relative_path, h.line_start)):
        key = _dedupe_key(hit)
        if key not in seen:
            seen.add(key)
            unique.append(hit)

    if query.max_hits:
        unique = unique[: query.max_hits]

    return RepositoryQueryResult(hits=tuple(unique), reason=RepositoryReason.OK)
