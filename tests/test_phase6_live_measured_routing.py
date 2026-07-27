"""Tests for the live measured routing observation source backend.

Uses temporary directory and synthetic measurements only.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.phase6_adapters import AdapterException, AdapterReason
from core.phase6_ecosystem.model_evaluation import (
    ModelCandidate,
    ModelCapability,
    ModelMeasurement,
    PrivacyClass,
)
from core.phase6_live.measured_routing import SqliteMeasuredRoutingSource


def _candidate(candidate_id: str = "local1") -> ModelCandidate:
    return ModelCandidate(
        candidate_id=candidate_id,
        provider_type="local_model",
        model_name="local-model",
        privacy_class=PrivacyClass.LOCAL_ONLY,
        capabilities=(ModelCapability.TEXT_GEN,),
        provenance_id="p1",
    )


def _measurement(candidate_id: str = "local1", measured_at: float = 0.0) -> ModelMeasurement:
    if measured_at <= 0.0:
        measured_at = time.time()
    return ModelMeasurement(
        candidate_id=candidate_id,
        quality_score=0.9,
        safety_score=0.9,
        latency_ms=100.0,
        cost_usd=0.0,
        memory_mb=1000.0,
        reliability_score=0.9,
        measured_at=measured_at,
    )


def test_source_disabled_without_db() -> None:
    source = SqliteMeasuredRoutingSource()
    assert source.observe("local1") is None
    with pytest.raises(AdapterException) as exc:
        source.record_measurement(_candidate(), _measurement())
    assert exc.value.reason is AdapterReason.MISSING_DEPENDENCY


def test_record_and_observe(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    source = SqliteMeasuredRoutingSource(db)
    source.record_measurement(_candidate(), _measurement(), scenario_id="s1")
    observed = source.observe("local1")
    assert observed is not None
    assert observed.quality_score == pytest.approx(0.9)


def test_observe_returns_latest(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    source = SqliteMeasuredRoutingSource(db)
    source.record_measurement(_candidate(), _measurement("local1"), scenario_id="s1")
    later = ModelMeasurement(
        candidate_id="local1",
        quality_score=0.5,
        safety_score=0.9,
        latency_ms=100.0,
        cost_usd=0.0,
        memory_mb=1000.0,
        reliability_score=0.9,
        measured_at=time.time() + 1.0,
    )
    source.record_measurement(_candidate(), later, scenario_id="s1")
    observed = source.observe("local1")
    assert observed is not None
    assert observed.quality_score == pytest.approx(0.5)


def test_canary_evidence_persistence(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    source = SqliteMeasuredRoutingSource(db)
    from core.phase6_live.measured_routing import CanaryEvidence
    evidence = CanaryEvidence("proposal1", "s1", "local1", "confirmed", 1.0, 0.9, 0.9, "v1")
    assert source.record_canary_evidence("proposal1", "s1", "local1", "confirmed", evidence) is True
    assert source.get_canary_evidence("proposal1") == evidence.to_dict()


def test_unknown_canary_evidence_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    source = SqliteMeasuredRoutingSource(db)
    assert source.get_canary_evidence("unknown") is None


def test_repr_content_free() -> None:
    assert "SqliteMeasuredRoutingSource()" in repr(SqliteMeasuredRoutingSource())


def test_constructor_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError):
        SqliteMeasuredRoutingSource(max_records_per_candidate=0)
    with pytest.raises(ValueError):
        SqliteMeasuredRoutingSource(max_records_per_candidate=True)
    with pytest.raises(ValueError):
        SqliteMeasuredRoutingSource(max_total_records=-1)
    with pytest.raises(ValueError):
        SqliteMeasuredRoutingSource(retention_age_seconds=float("nan"))
    with pytest.raises(ValueError):
        SqliteMeasuredRoutingSource(retention_age_seconds=float("inf"))
    with pytest.raises(ValueError):
        SqliteMeasuredRoutingSource(max_history_scenarios=0)
    with pytest.raises(ValueError):
        SqliteMeasuredRoutingSource(max_records_per_candidate=1.5)


def test_get_latest_with_scenario(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    source = SqliteMeasuredRoutingSource(db)
    source.record_measurement(_candidate(), _measurement(), scenario_id="s1")
    assert source.get_latest("local1", "s1") is not None


def test_empty_scenario_id_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    source = SqliteMeasuredRoutingSource(db)
    with pytest.raises(AdapterException) as exc:
        source.record_measurement(_candidate(), _measurement(), scenario_id="")
    assert exc.value.reason is AdapterReason.INVALID_INPUT


def test_canary_evidence_immutable(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    source = SqliteMeasuredRoutingSource(db)
    from core.phase6_live.measured_routing import CanaryEvidence
    evidence = CanaryEvidence("proposal1", "s1", "local1", "confirmed", 1.0, 0.9, 0.9, "v1")
    assert source.record_canary_evidence("proposal1", "s1", "local1", "confirmed", evidence) is True
    # Duplicate is idempotent.
    assert source.record_canary_evidence("proposal1", "s1", "local1", "confirmed", evidence) is True
    # Conflicting state is rejected.
    assert source.record_canary_evidence("proposal1", "s2", "local1", "failed", evidence) is False


def test_capacity_bounds_prune_oldest(tmp_path: Path) -> None:
    """Exact-cap remains at cap; overflow prunes oldest by measured_at."""
    db = tmp_path / "routing.db"
    now = 1000.0
    source = SqliteMeasuredRoutingSource(db, clock=lambda: now, max_total_records=3, max_records_per_candidate=3, max_history_scenarios=3)
    cand = _candidate("local1")
    source.record_measurement(cand, _measurement("local1", measured_at=now - 3.0), scenario_id="s1")
    source.record_measurement(cand, _measurement("local1", measured_at=now - 2.0), scenario_id="s2")
    source.record_measurement(cand, _measurement("local1", measured_at=now - 1.0), scenario_id="s3")
    # Exact-cap state must retain all three records.
    assert source.get_latest("local1", "s1") is not None
    assert source.get_latest("local1", "s2") is not None
    assert source.get_latest("local1", "s3") is not None
    # Cap+1 prunes the oldest scenario/record.
    source.record_measurement(cand, _measurement("local1", measured_at=now), scenario_id="s4")
    assert source.get_latest("local1", "s1") is None
    assert source.get_latest("local1", "s4") is not None


def test_exact_cap_boundaries(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    now = 2000.0
    source = SqliteMeasuredRoutingSource(
        db, clock=lambda: now, max_total_records=2, max_records_per_candidate=2, max_history_scenarios=2
    )
    cand = _candidate("local1")
    source.record_measurement(cand, _measurement("local1", measured_at=now - 2), scenario_id="a")
    # One below cap
    assert source.get_latest("local1", "a") is not None
    source.record_measurement(cand, _measurement("local1", measured_at=now - 1), scenario_id="b")
    # Exact cap
    assert source.get_latest("local1", "a") is not None
    assert source.get_latest("local1", "b") is not None
    source.record_measurement(cand, _measurement("local1", measured_at=now), scenario_id="c")
    # Cap+1 prunes exactly one oldest
    assert source.get_latest("local1", "a") is None
    assert source.get_latest("local1", "b") is not None
    assert source.get_latest("local1", "c") is not None


def test_invalid_metric_values_fail_closed(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    source = SqliteMeasuredRoutingSource(db)
    cand = _candidate("local1")
    # ModelMeasurement constructor rejects NaN/inf/negative/bool values for
    # performance fields; this is fail-closed behavior.
    for bad in (float("nan"), float("inf"), -1.0, True):
        with pytest.raises(ValueError):
            ModelMeasurement(
                candidate_id="local1",
                quality_score=0.9,
                safety_score=0.9,
                latency_ms=bad,  # type: ignore[arg-type]
                cost_usd=0.0,
                memory_mb=1000.0,
                reliability_score=0.9,
                measured_at=time.time(),
            )


def test_future_and_stale_measurements_rejected(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    now = 1000.0
    source = SqliteMeasuredRoutingSource(db, clock=lambda: now, retention_age_seconds=10)
    cand = _candidate("local1")
    stale = _measurement("local1", measured_at=now - 20.0)
    future = _measurement("local1", measured_at=now + 20.0)
    with pytest.raises(ValueError, match="stale_measurement_rejected"):
        source.record_measurement(cand, stale, scenario_id="s1")
    with pytest.raises(ValueError, match="future_measurement_rejected"):
        source.record_measurement(cand, future, scenario_id="s1")


def test_invalid_id_characters_rejected(tmp_path: Path) -> None:
    db = tmp_path / "routing.db"
    source = SqliteMeasuredRoutingSource(db)
    with pytest.raises(ValueError):
        ModelCandidate(
            candidate_id="bad/candidate",
            provider_type="local_model",
            model_name="local-model",
            privacy_class=PrivacyClass.LOCAL_ONLY,
            capabilities=(),
            provenance_id="p1",
        )
    # Also exercise backend scenario_id validation.
    with pytest.raises(ValueError):
        source.record_measurement(_candidate("local1"), _measurement("local1"), scenario_id="bad/scenario")
