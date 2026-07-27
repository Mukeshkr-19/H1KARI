"""Durable, SQLite-backed observation source for measured routing.

Implements the ``BenchmarkObservationInterface`` from
``core.phase6_adapters.measured_routing`` and additionally provides persistence
for bounded history, canary confirmation evidence, and rollback evidence.

The source is disabled by default (``db_path=None``) and performs no I/O until
explicitly constructed.

Time semantics:
- All durable timestamps use the injected epoch/wall clock (default time.time).
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.phase6_adapters.measured_routing import BenchmarkObservationInterface
from core.phase6_adapters.contracts import AdapterException, AdapterReason
from core.phase6_ecosystem.model_evaluation import (
    ModelCandidate,
    ModelMeasurement,
)
from core.phase6_live.base import safe_makedirs


Clock = Callable[[], float]


def _now_clock(clock: Optional[Clock]) -> float:
    if clock is None:
        return time.time()
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_clock_value")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("invalid_clock_value")
    return value


@dataclass(frozen=True)
class CanaryEvidence:
    """Typed, bounded canary evidence record."""

    proposal_id: str
    scenario_id: str
    candidate_id: str
    state: str
    recorded_at: float
    score: float
    safety_score: float
    policy_version: str

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "scenario_id": self.scenario_id,
            "candidate_id": self.candidate_id,
            "state": self.state,
            "recorded_at": self.recorded_at,
            "score": self.score,
            "safety_score": self.safety_score,
            "policy_version": self.policy_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CanaryEvidence":
        return cls(
            proposal_id=data["proposal_id"],
            scenario_id=data["scenario_id"],
            candidate_id=data["candidate_id"],
            state=data["state"],
            recorded_at=data["recorded_at"],
            score=data["score"],
            safety_score=data["safety_score"],
            policy_version=data["policy_version"],
        )


class SqliteMeasuredRoutingSource(BenchmarkObservationInterface):
    """Durable benchmark observation source backed by SQLite."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        clock: Optional[Clock] = None,
        max_records_per_candidate: int = 1000,
        max_total_records: int = 10_000,
        max_history_scenarios: int = 64,
        retention_age_seconds: float = 30 * 24 * 3600.0,
    ) -> None:
        self._db_path = db_path
        self._disabled = db_path is None
        self._clock = clock
        self._max_records_per_candidate = self._validate_int("max_records_per_candidate", max_records_per_candidate, 1, 1_000_000)
        self._max_total_records = self._validate_int("max_total_records", max_total_records, 1, 10_000_000)
        self._max_history_scenarios = self._validate_int("max_history_scenarios", max_history_scenarios, 1, 10_000)
        self._retention_age_seconds = self._validate_positive_float("retention_age_seconds", retention_age_seconds, 1.0, 365 * 24 * 3600.0)
        if not self._disabled:
            self._initialize()

    @staticmethod
    def _validate_int(name: str, value: object, min_val: int, max_val: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not min_val <= value <= max_val:
            raise ValueError(f"invalid_{name}")
        return value

    @staticmethod
    def _validate_positive_float(name: str, value: float, min_val: float, max_val: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not min_val <= value <= max_val:
            raise ValueError(f"invalid_{name}")
        return float(value)

    def _require_enabled(self) -> Path:
        if self._disabled or self._db_path is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return self._db_path

    def _now(self) -> float:
        return _now_clock(self._clock)

    def _initialize(self) -> None:
        path = self._require_enabled()
        safe_makedirs(path.parent)
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS routing_observation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    scenario_id TEXT NOT NULL,
                    quality_score REAL NOT NULL,
                    safety_score REAL NOT NULL,
                    latency_ms REAL NOT NULL,
                    cost_usd REAL NOT NULL,
                    memory_mb REAL NOT NULL,
                    reliability_score REAL NOT NULL,
                    measured_at REAL NOT NULL,
                    provenance_id TEXT NOT NULL,
                    privacy_class TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_obs_candidate ON routing_observation(candidate_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_obs_scenario ON routing_observation(scenario_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_obs_time ON routing_observation(measured_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS routing_canary (
                    proposal_id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    evidence_json TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
        path.chmod(0o600)

    _MAX_ID_LENGTH = 256
    _SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
    _MAX_FUTURE_SKEW_SECONDS = 5.0
    _ALLOWED_CANARY_STATES = frozenset({"proposed", "confirmed", "passed", "failed", "rollback_required", "expired", "cancelled"})

    @classmethod
    def _validate_safe_id(cls, value: str, name: str = "id") -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid_{name}")
        if len(value) > cls._MAX_ID_LENGTH:
            raise ValueError(f"{name}_too_long")
        if not cls._SAFE_ID_RE.fullmatch(value):
            raise ValueError(f"invalid_{name}_chars")

    @classmethod
    def _validate_canary_evidence_dict(cls, data: object, *, now: Optional[float] = None) -> dict:
        """Shared validator for canary evidence on write and read.

        Returns the validated dict. Raises ValueError on any violation.
        """
        if not isinstance(data, dict):
            raise ValueError("canary_evidence_must_be_dict")
        required = {"proposal_id", "scenario_id", "candidate_id", "state", "recorded_at", "score", "safety_score", "policy_version"}
        if set(data.keys()) != required:
            raise ValueError("canary_evidence_invalid_keys")

        for name in ("proposal_id", "scenario_id", "candidate_id"):
            cls._validate_safe_id(data[name], name)

        state = data["state"]
        if not isinstance(state, str) or state not in cls._ALLOWED_CANARY_STATES:
            raise ValueError("canary_invalid_state")

        recorded_at = data["recorded_at"]
        if isinstance(recorded_at, bool) or not isinstance(recorded_at, (int, float)) or not math.isfinite(recorded_at):
            raise ValueError("canary_invalid_recorded_at")
        if recorded_at < 0:
            raise ValueError("canary_invalid_recorded_at")
        if now is not None and recorded_at > now + cls._MAX_FUTURE_SKEW_SECONDS:
            raise ValueError("canary_future_recorded_at")

        for name in ("score", "safety_score"):
            value = data[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"canary_invalid_{name}")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"canary_invalid_{name}")

        policy_version = data["policy_version"]
        if not isinstance(policy_version, str) or not policy_version or "\x00" in policy_version or len(policy_version) > cls._MAX_ID_LENGTH:
            raise ValueError("canary_invalid_policy_version")

        return data

    def _validate_measurement(
        self,
        candidate: ModelCandidate,
        measurement: ModelMeasurement,
        *,
        scenario_id: str,
        now: float,
    ) -> None:
        if not isinstance(candidate, ModelCandidate) or not isinstance(measurement, ModelMeasurement):
            raise ValueError("invalid_measurement")
        if measurement.candidate_id != candidate.candidate_id:
            raise ValueError("candidate_id_mismatch")
        self._validate_safe_id(candidate.candidate_id, "candidate_id")
        self._validate_safe_id(candidate.provenance_id, "provenance_id")
        self._validate_safe_id(scenario_id, "scenario_id")
        if not hasattr(candidate.privacy_class, "name"):
            raise ValueError("invalid_privacy_class")
        privacy_name = candidate.privacy_class.name
        if not isinstance(privacy_name, str) or not privacy_name:
            raise ValueError("invalid_privacy_class")
        # Freshness/future-skew bounds.
        if isinstance(measurement.measured_at, bool) or not isinstance(measurement.measured_at, (int, float)) or not math.isfinite(measurement.measured_at) or measurement.measured_at < 0:
            raise ValueError("invalid_measured_at")
        if measurement.measured_at > now + self._MAX_FUTURE_SKEW_SECONDS:
            raise ValueError("future_measurement_rejected")
        if measurement.measured_at < now - self._retention_age_seconds:
            raise ValueError("stale_measurement_rejected")
        for name, value in (
            ("quality_score", measurement.quality_score),
            ("safety_score", measurement.safety_score),
            ("reliability_score", measurement.reliability_score),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"invalid_{name}")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"invalid_{name}")
        for name, value in (
            ("latency_ms", measurement.latency_ms),
            ("cost_usd", measurement.cost_usd),
            ("memory_mb", measurement.memory_mb),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"invalid_{name}")
            if value < 0:
                raise ValueError(f"invalid_{name}")

    def observe(self, candidate_id: str) -> Optional[ModelMeasurement]:
        """Return the most recent measurement for a candidate, if any."""
        if self._disabled:
            return None
        path = self._require_enabled()
        if not isinstance(candidate_id, str) or not candidate_id:
            return None
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT * FROM routing_observation
                WHERE candidate_id = ?
                ORDER BY measured_at DESC, id DESC
                LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_measurement(row)
        finally:
            conn.close()

    def record_measurement(
        self,
        candidate: ModelCandidate,
        measurement: ModelMeasurement,
        *,
        scenario_id: str = "",
    ) -> None:
        """Persist a benchmark measurement bound to a candidate and scenario.

        The inherited ``BenchmarkObservationInterface`` does not carry scenario
        correlation, so the live backend fails closed when no scenario is
        supplied. Callers must provide a non-empty, bounded scenario_id.

        Caps are enforced atomically within a single transaction. The
        ``max_history_scenarios`` setting limits the number of distinct scenario
        IDs retained; adding a record for an already-known scenario is allowed
        subject to total and per-candidate caps. When a new scenario would exceed
        the distinct-scenario cap, the oldest scenario (by earliest measured_at)
        is pruned deterministically before the insert commits.
        """
        path = self._require_enabled()
        if not isinstance(scenario_id, str) or not scenario_id:
            raise AdapterException(AdapterReason.INVALID_INPUT, "missing_scenario_id")
        self._validate_safe_id(scenario_id, "scenario_id")
        self._validate_measurement(candidate, measurement, scenario_id=scenario_id, now=self._now())

        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Distinct-scenario cap: prune oldest scenario if this is a new one.
                distinct_scenarios = conn.execute(
                    "SELECT COUNT(DISTINCT scenario_id) FROM routing_observation"
                ).fetchone()[0]
                known = conn.execute(
                    "SELECT 1 FROM routing_observation WHERE scenario_id = ? LIMIT 1", (scenario_id,)
                ).fetchone() is not None
                if not known and distinct_scenarios >= self._max_history_scenarios:
                    oldest = conn.execute(
                        """
                        SELECT scenario_id FROM routing_observation
                        ORDER BY measured_at ASC, id ASC LIMIT 1
                        """
                    ).fetchone()
                    if oldest is not None:
                        conn.execute("DELETE FROM routing_observation WHERE scenario_id = ?", (oldest["scenario_id"],))

                conn.execute(
                    """
                    INSERT INTO routing_observation (
                        candidate_id, scenario_id, quality_score, safety_score,
                        latency_ms, cost_usd, memory_mb, reliability_score,
                        measured_at, provenance_id, privacy_class
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.candidate_id,
                        scenario_id,
                        measurement.quality_score,
                        measurement.safety_score,
                        measurement.latency_ms,
                        measurement.cost_usd,
                        measurement.memory_mb,
                        measurement.reliability_score,
                        measurement.measured_at,
                        candidate.provenance_id,
                        candidate.privacy_class.name,
                    ),
                )
                # Post-condition: prune if still over any cap.
                self._enforce_bounds_in_transaction(conn)
                conn.commit()
            except AdapterException:
                conn.rollback()
                raise
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

    def get_latest(self, candidate_id: str, scenario_id: str) -> Optional[ModelMeasurement]:
        """Return the latest measurement for a candidate in a scenario."""
        if self._disabled:
            return None
        path = self._require_enabled()
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT * FROM routing_observation
                WHERE candidate_id = ? AND scenario_id = ?
                ORDER BY measured_at DESC, id DESC
                LIMIT 1
                """,
                (candidate_id, scenario_id),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_measurement(row)
        finally:
            conn.close()

    def record_canary_evidence(
        self,
        proposal_id: str,
        scenario_id: str,
        candidate_id: str,
        state: str,
        evidence: CanaryEvidence,
    ) -> bool:
        """Persist typed canary confirmation or rollback evidence (insert-once).

        Duplicate identical evidence is idempotent only if every field matches.
        Conflicting evidence for the same proposal_id is rejected.
        """
        path = self._require_enabled()
        self._validate_safe_id(proposal_id, "proposal_id")
        self._validate_safe_id(scenario_id, "scenario_id")
        self._validate_safe_id(candidate_id, "candidate_id")
        if not isinstance(state, str) or not state or state not in self._ALLOWED_CANARY_STATES:
            return False
        if not isinstance(evidence, CanaryEvidence):
            return False
        # Evidence envelope must match outer arguments exactly.
        if (
            evidence.proposal_id != proposal_id
            or evidence.scenario_id != scenario_id
            or evidence.candidate_id != candidate_id
            or evidence.state != state
        ):
            return False
        # Shared validator checks all security bounds.
        evidence_dict = evidence.to_dict()
        try:
            self._validate_canary_evidence_dict(evidence_dict, now=self._now())
        except ValueError:
            return False

        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            existing = conn.execute(
                "SELECT evidence_json FROM routing_canary WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if existing is not None:
                # Full equality check of every field.
                try:
                    parsed = json.loads(existing["evidence_json"])
                except Exception:
                    conn.rollback()
                    return False
                if parsed != evidence.to_dict():
                    conn.rollback()
                    return False
                conn.commit()
                return True
            conn.execute(
                """
                INSERT INTO routing_canary (proposal_id, scenario_id, candidate_id, state, recorded_at, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    scenario_id,
                    candidate_id,
                    state,
                    evidence.recorded_at,
                    json.dumps(evidence.to_dict(), sort_keys=True),
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def get_canary_evidence(self, proposal_id: str) -> Optional[dict]:
        """Return persisted canary evidence, if any, after strict schema validation."""
        if self._disabled:
            return None
        path = self._require_enabled()
        self._validate_safe_id(proposal_id, "proposal_id")
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT evidence_json FROM routing_canary WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                return None
            data = json.loads(row["evidence_json"])
            if not isinstance(data, dict):
                return None
            # Shared validator checks all security bounds on read.
            try:
                self._validate_canary_evidence_dict(data, now=self._now())
            except ValueError:
                return None
            return data
        finally:
            conn.close()

    def _enforce_bounds_in_transaction(self, conn: sqlite3.Connection) -> None:
        """Prune routing_observation while already inside a transaction.

        Postconditions enforced:
        - total records <= max_total_records
        - records per candidate <= max_records_per_candidate
        - distinct scenarios <= max_history_scenarios
        - records are not older than retention_age_seconds
        """
        # Total cap: prune only when strictly over the configured cap.
        # Exact-cap state must remain at the configured maximum.
        total = conn.execute("SELECT COUNT(*) FROM routing_observation").fetchone()[0]
        if total > self._max_total_records:
            prune_limit = total - self._max_total_records
            conn.execute(
                """
                DELETE FROM routing_observation
                WHERE id IN (
                    SELECT id FROM routing_observation
                    ORDER BY measured_at ASC, id ASC
                    LIMIT ?
                )
                """,
                (prune_limit,),
            )

        # Distinct-scenario cap: prune only excess scenarios past the configured cap.
        distinct_scenarios = conn.execute(
            "SELECT COUNT(DISTINCT scenario_id) FROM routing_observation"
        ).fetchone()[0]
        if distinct_scenarios > self._max_history_scenarios:
            excess = distinct_scenarios - self._max_history_scenarios
            for _ in range(excess):
                oldest = conn.execute(
                    """
                    SELECT scenario_id FROM routing_observation
                    ORDER BY measured_at ASC, id ASC LIMIT 1
                    """
                ).fetchone()
                if oldest is None:
                    break
                conn.execute("DELETE FROM routing_observation WHERE scenario_id = ?", (oldest["scenario_id"],))

        # Per-candidate cap.
        conn.execute(
            """
            DELETE FROM routing_observation
            WHERE id IN (
                SELECT id FROM routing_observation AS o
                WHERE (SELECT COUNT(*) FROM routing_observation WHERE candidate_id = o.candidate_id) > ?
                AND id NOT IN (
                    SELECT id FROM routing_observation
                    WHERE candidate_id = o.candidate_id
                    ORDER BY measured_at DESC, id DESC
                    LIMIT ?
                )
            )
            """,
            (self._max_records_per_candidate, self._max_records_per_candidate),
        )

        # Retention age (independent of capacity caps).
        cutoff_age = self._now() - self._retention_age_seconds
        conn.execute("DELETE FROM routing_observation WHERE measured_at < ?", (cutoff_age,))

        # Transaction postconditions before commit.
        total_after = conn.execute("SELECT COUNT(*) FROM routing_observation").fetchone()[0]
        if total_after > self._max_total_records:
            raise AdapterException(AdapterReason.INVALID_INPUT, "total_cap_postcondition")
        distinct_after = conn.execute(
            "SELECT COUNT(DISTINCT scenario_id) FROM routing_observation"
        ).fetchone()[0]
        if distinct_after > self._max_history_scenarios:
            raise AdapterException(AdapterReason.INVALID_INPUT, "scenario_cap_postcondition")

    def _enforce_bounds(self, path: Path) -> None:
        conn = sqlite3.connect(str(path), timeout=10.0)
        try:
            self._enforce_bounds_in_transaction(conn)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_measurement(row: sqlite3.Row) -> ModelMeasurement:
        return ModelMeasurement(
            candidate_id=row["candidate_id"],
            quality_score=row["quality_score"],
            safety_score=row["safety_score"],
            latency_ms=row["latency_ms"],
            cost_usd=row["cost_usd"],
            memory_mb=row["memory_mb"],
            reliability_score=row["reliability_score"],
            measured_at=row["measured_at"],
        )

    def __repr__(self) -> str:
        return "SqliteMeasuredRoutingSource()"
