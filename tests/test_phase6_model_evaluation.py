"""Synthetic test suite for measured model routing evaluation (Phase 6 Part E)."""

import pytest

from core.phase6_ecosystem.model_evaluation import (
    EvaluationScenario,
    ModelCandidate,
    ModelCapability,
    ModelEvaluationRejectionReason,
    ModelMeasurement,
    ModelRoutingEvaluator,
    PrivacyClass,
    RoutingPolicy,
)


def _make_candidate(
    candidate_id: str,
    provider_type: str = "local_model",
    privacy_class: PrivacyClass = PrivacyClass.LOCAL_ONLY,
    capabilities=(ModelCapability.TEXT_GEN, ModelCapability.CODE_GEN),
) -> ModelCandidate:
    return ModelCandidate(
        candidate_id=candidate_id,
        provider_type=provider_type,
        model_name=f"Model-{candidate_id}",
        privacy_class=privacy_class,
        capabilities=tuple(capabilities),
        provenance_id=f"prov_{candidate_id}",
    )


def test_eligible_local_winner_selection():
    evaluator = ModelRoutingEvaluator()
    scenario = EvaluationScenario(
        scenario_id="scen_01",
        required_capabilities=(ModelCapability.TEXT_GEN,),
        max_privacy_class=PrivacyClass.LOCAL_ONLY,
    )

    c_local = _make_candidate("cand_local", provider_type="local_model", privacy_class=PrivacyClass.LOCAL_ONLY)
    m_local = ModelMeasurement(
        candidate_id="cand_local",
        quality_score=0.9,
        safety_score=0.95,
        latency_ms=100.0,
        cost_usd=0.0,
        memory_mb=4096.0,
        reliability_score=0.99,
        measured_at=1000.0,
    )

    rec = evaluator.evaluate_candidates(scenario, [c_local], {"cand_local": m_local}, now=1050.0)

    assert rec.winning_candidate is not None
    assert rec.winning_candidate.candidate_id == "cand_local"
    assert rec.score_breakdown["final_score"] > 0.8
    assert rec.candidate_outcomes[0] == ("cand_local", ModelEvaluationRejectionReason.OK.value, rec.score_breakdown["final_score"])


def test_remote_denied_by_privacy_egress_policy():
    evaluator = ModelRoutingEvaluator()

    # Scenario forbids remote egress (max_privacy_class = LOCAL_ONLY)
    scenario = EvaluationScenario(
        scenario_id="scen_privacy",
        required_capabilities=(ModelCapability.TEXT_GEN,),
        max_privacy_class=PrivacyClass.LOCAL_ONLY,
    )

    c_remote = _make_candidate("cand_remote", provider_type="remote_provider", privacy_class=PrivacyClass.REMOTE_OK)
    m_remote = ModelMeasurement("cand_remote", 0.99, 0.99, 50.0, 0.001, 0.0, 0.99, 1000.0)

    rec = evaluator.evaluate_candidates(scenario, [c_remote], {"cand_remote": m_remote}, now=1050.0)

    assert rec.winning_candidate is None
    assert rec.candidate_outcomes[0][1] == ModelEvaluationRejectionReason.PRIVACY_EGRESS_FORBIDDEN.value


def test_safety_below_minimum_rejection():
    evaluator = ModelRoutingEvaluator(RoutingPolicy(min_safety_threshold=0.8))
    scenario = EvaluationScenario(
        scenario_id="scen_safety",
        required_capabilities=(ModelCapability.TEXT_GEN,),
        max_privacy_class=PrivacyClass.LOCAL_ONLY,
        min_safety_score=0.8,
    )

    c_unsafe = _make_candidate("cand_unsafe", provider_type="local_model", privacy_class=PrivacyClass.LOCAL_ONLY)
    m_unsafe = ModelMeasurement("cand_unsafe", quality_score=0.95, safety_score=0.5, latency_ms=100.0, cost_usd=0.0, memory_mb=100.0, reliability_score=0.9, measured_at=1000.0)

    rec = evaluator.evaluate_candidates(scenario, [c_unsafe], {"cand_unsafe": m_unsafe}, now=1050.0)

    assert rec.winning_candidate is None
    assert rec.candidate_outcomes[0][1] == ModelEvaluationRejectionReason.SAFETY_BELOW_MINIMUM.value


def test_stale_and_missing_measurement_rejection():
    evaluator = ModelRoutingEvaluator(RoutingPolicy(max_measurement_age_seconds=3600.0))
    scenario = EvaluationScenario(
        scenario_id="scen_stale",
        required_capabilities=(ModelCapability.TEXT_GEN,),
        max_privacy_class=PrivacyClass.LOCAL_ONLY,
    )

    c_stale = _make_candidate("cand_stale")
    m_stale = ModelMeasurement("cand_stale", 0.9, 0.9, 100.0, 0.0, 100.0, 0.9, measured_at=1000.0)

    c_missing = _make_candidate("cand_missing")

    # Evaluated at time 5000.0 (> 1000 + 3600 max age)
    rec = evaluator.evaluate_candidates(scenario, [c_stale, c_missing], {"cand_stale": m_stale}, now=5000.0)

    assert rec.winning_candidate is None
    outcome_map = dict((item[0], item[1]) for item in rec.candidate_outcomes)
    assert outcome_map["cand_stale"] == ModelEvaluationRejectionReason.MEASUREMENT_STALE.value
    assert outcome_map["cand_missing"] == ModelEvaluationRejectionReason.MEASUREMENT_MISSING.value


def test_deterministic_tie_breaking():
    evaluator = ModelRoutingEvaluator()
    scenario = EvaluationScenario("scen_tie", (ModelCapability.TEXT_GEN,), PrivacyClass.LOCAL_ONLY)

    c_b = _make_candidate("cand_b")
    c_a = _make_candidate("cand_a")

    m_b = ModelMeasurement("cand_b", 0.9, 0.9, 100.0, 0.0, 100.0, 0.9, 1000.0)
    m_a = ModelMeasurement("cand_a", 0.9, 0.9, 100.0, 0.0, 100.0, 0.9, 1000.0)

    rec = evaluator.evaluate_candidates(scenario, [c_b, c_a], {"cand_b": m_b, "cand_a": m_a}, now=1050.0)

    assert rec.winning_candidate is not None
    # Lower candidate_id ("cand_a") breaks tie deterministically
    assert rec.winning_candidate.candidate_id == "cand_a"


def test_resource_budget_exceeded():
    evaluator = ModelRoutingEvaluator()
    scenario = EvaluationScenario("scen_budget", (ModelCapability.TEXT_GEN,), PrivacyClass.LOCAL_ONLY, max_latency_ms=200.0, max_cost_usd=0.01)

    c_slow = _make_candidate("cand_slow")
    m_slow = ModelMeasurement("cand_slow", 0.9, 0.9, latency_ms=500.0, cost_usd=0.001, memory_mb=100.0, reliability_score=0.9, measured_at=1000.0)

    rec = evaluator.evaluate_candidates(scenario, [c_slow], {"cand_slow": m_slow}, now=1050.0)

    assert rec.winning_candidate is None
    assert rec.candidate_outcomes[0][1] == ModelEvaluationRejectionReason.LATENCY_EXCEEDED.value


def test_content_free_repr_model_eval():
    evaluator = ModelRoutingEvaluator()
    candidate = _make_candidate("c1")
    scenario = EvaluationScenario("s1", (ModelCapability.TEXT_GEN,), PrivacyClass.LOCAL_ONLY)
    measurement = ModelMeasurement("c1", 0.9, 0.9, 100.0, 0.0, 100.0, 0.9, 1000.0)

    assert repr(evaluator) == "ModelRoutingEvaluator()"
    assert repr(candidate) == "ModelCandidate()"
    assert repr(scenario) == "EvaluationScenario()"
    assert repr(measurement) == "ModelMeasurement()"


def test_measurement_identity_must_match_candidate() -> None:
    evaluator = ModelRoutingEvaluator()
    scenario = EvaluationScenario("scen_identity", (ModelCapability.TEXT_GEN,), PrivacyClass.LOCAL_ONLY)
    candidate = _make_candidate("cand_expected")
    wrong = ModelMeasurement("cand_other", 0.9, 0.9, 100.0, 0.0, 100.0, 0.9, 1000.0)
    result = evaluator.evaluate_candidates(
        scenario, (candidate,), {"cand_expected": wrong}, now=1001.0
    )
    assert result.winning_candidate is None
    assert result.candidate_outcomes[0][1] == ModelEvaluationRejectionReason.MEASUREMENT_MISMATCH


def test_memory_budget_and_future_measurement_fail_closed() -> None:
    evaluator = ModelRoutingEvaluator()
    scenario = EvaluationScenario(
        "scen_resource", (ModelCapability.TEXT_GEN,), PrivacyClass.LOCAL_ONLY,
        max_memory_mb=128.0,
    )
    candidate = _make_candidate("cand_resource")
    too_large = ModelMeasurement("cand_resource", 0.9, 0.9, 10.0, 0.0, 256.0, 0.9, 1000.0)
    result = evaluator.evaluate_candidates(
        scenario, (candidate,), {"cand_resource": too_large}, now=1001.0
    )
    assert result.candidate_outcomes[0][1] == ModelEvaluationRejectionReason.RESOURCE_EXCEEDED

    future = ModelMeasurement("cand_resource", 0.9, 0.9, 10.0, 0.0, 64.0, 0.9, 2000.0)
    result = evaluator.evaluate_candidates(
        scenario, (candidate,), {"cand_resource": future}, now=1001.0
    )
    assert result.candidate_outcomes[0][1] == ModelEvaluationRejectionReason.MEASUREMENT_STALE
