"""Synthetic test suite for Home Assistant contracts and boundaries (Phase 6 Part C)."""

import pytest

from core.action_policy import Actor, ActorContext
from core.phase6_ecosystem.home_assistant import (
    HAActionOutcome,
    HAActionReason,
    HomeAssistantActionProposal,
    HomeAssistantCapabilityManifest,
    HomeAssistantConfirmation,
    HomeAssistantContractEvaluator,
    HomeAssistantEntityRef,
    HomeAssistantObservation,
    HomeAssistantServiceRef,
)


def _make_sample_manifest() -> HomeAssistantCapabilityManifest:
    return HomeAssistantCapabilityManifest(
        allowed_domains=frozenset({"light", "switch", "sensor", "climate", "lock"}),
        allowed_entities=frozenset({
            "light.living_room",
            "switch.coffee_maker",
            "sensor.temperature",
            "climate.thermostat",
            "lock.front_door",
        }),
        allowed_services=frozenset({"turn_on", "turn_off", "get_state", "lock", "unlock"}),
        sensitive_domains=frozenset({"lock"}),
    )


def test_wildcard_rejection_in_entity_and_service_refs():
    with pytest.raises(ValueError, match="wildcard"):
        HomeAssistantEntityRef("light", "light.*")

    with pytest.raises(ValueError, match="wildcard"):
        HomeAssistantServiceRef("light", "*")

    with pytest.raises(ValueError, match="wildcard"):
        HomeAssistantCapabilityManifest(
            allowed_domains=frozenset({"*"}),
            allowed_entities=frozenset({"light.living_room"}),
            allowed_services=frozenset({"turn_on"}),
        )


def test_safe_read_proposal_allows_without_confirmation():
    manifest = _make_sample_manifest()
    evaluator = HomeAssistantContractEvaluator(manifest)
    owner_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")

    entity = HomeAssistantEntityRef("sensor", "sensor.temperature")
    service = HomeAssistantServiceRef("sensor", "get_state")

    res = evaluator.prepare_action("p_01", entity, service, {}, owner_ctx, "nonce_01", 1000.0)
    assert res.outcome == HAActionOutcome.ALLOW
    assert res.reason == HAActionReason.OK


def test_state_changing_action_requires_owner_confirmation():
    manifest = _make_sample_manifest()
    evaluator = HomeAssistantContractEvaluator(manifest)
    owner_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")

    entity = HomeAssistantEntityRef("light", "light.living_room")
    service = HomeAssistantServiceRef("light", "turn_on")

    # 1. Prepare -> REQUIRE_CONFIRMATION
    prep_res = evaluator.prepare_action("p_02", entity, service, {}, owner_ctx, "nonce_02", 1000.0)
    assert prep_res.outcome == HAActionOutcome.REQUIRE_CONFIRMATION
    assert prep_res.proposal is not None

    # 2. Authorize without confirmation -> DENY
    auth_no_conf = evaluator.authorize_execution(prep_res.proposal, None, owner_ctx, 1005.0)
    assert auth_no_conf.outcome == HAActionOutcome.DENY
    assert auth_no_conf.reason == HAActionReason.STATE_CHANGING_UNCONFIRMED

    # 3. Authorize with valid owner confirmation -> ALLOW
    conf = HomeAssistantConfirmation("p_02", "nonce_02", "owner_1", 1006.0)
    auth_conf = evaluator.authorize_execution(prep_res.proposal, conf, owner_ctx, 1006.0)
    assert auth_conf.outcome == HAActionOutcome.ALLOW
    assert auth_conf.reason == HAActionReason.OK

    replay = evaluator.authorize_execution(prep_res.proposal, conf, owner_ctx, 1007.0)
    assert replay.outcome is HAActionOutcome.DENY
    assert replay.reason is HAActionReason.REPLAY_DETECTED


def test_sensitive_entity_denies_non_owner_actor():
    manifest = _make_sample_manifest()
    evaluator = HomeAssistantContractEvaluator(manifest)
    guest_ctx = ActorContext(actor=Actor.GUEST, actor_id="guest_1", session_id="s1")

    lock_entity = HomeAssistantEntityRef("lock", "lock.front_door")
    lock_service = HomeAssistantServiceRef("lock", "unlock")

    res = evaluator.prepare_action("p_03", lock_entity, lock_service, {}, guest_ctx, "nonce_03", 1000.0)
    assert res.outcome == HAActionOutcome.DENY
    assert res.reason == HAActionReason.UNAUTHORIZED_ACTOR


def test_replay_nonce_rejection():
    manifest = _make_sample_manifest()
    evaluator = HomeAssistantContractEvaluator(manifest)
    owner_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")

    entity = HomeAssistantEntityRef("sensor", "sensor.temperature")
    service = HomeAssistantServiceRef("sensor", "get_state")

    # First prepare succeeds
    res1 = evaluator.prepare_action("p_04", entity, service, {}, owner_ctx, "nonce_reuse", 1000.0)
    assert res1.outcome == HAActionOutcome.ALLOW

    # Reused nonce is denied
    res2 = evaluator.prepare_action("p_05", entity, service, {}, owner_ctx, "nonce_reuse", 1001.0)
    assert res2.outcome == HAActionOutcome.DENY
    assert res2.reason == HAActionReason.REPLAY_DETECTED


def test_stale_or_expired_proposal_rejection():
    manifest = _make_sample_manifest()
    evaluator = HomeAssistantContractEvaluator(manifest, proposal_ttl_seconds=300.0)
    owner_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")

    entity = HomeAssistantEntityRef("light", "light.living_room")
    service = HomeAssistantServiceRef("light", "turn_on")

    prep = evaluator.prepare_action("p_06", entity, service, {}, owner_ctx, "nonce_exp", 1000.0)
    conf = HomeAssistantConfirmation("p_06", "nonce_exp", "owner_1", 1350.0)

    # Authorized at 1350.0 (past 1000 + 300 TTL) -> EXPIRED_PROPOSAL
    res = evaluator.authorize_execution(prep.proposal, conf, owner_ctx, 1350.0)
    assert res.outcome == HAActionOutcome.DENY
    assert res.reason == HAActionReason.EXPIRED_PROPOSAL


def test_observation_requires_explicit_evidence_for_success():
    with pytest.raises(ValueError, match="result_evidence"):
        HomeAssistantObservation("obs_1", "p_01", success=True, result_evidence=None, observed_at=1000.0)

    obs_valid = HomeAssistantObservation("obs_2", "p_01", success=True, result_evidence="sha256.valid", observed_at=1000.0)
    assert obs_valid.success is True


def test_content_free_repr_ha():
    manifest = _make_sample_manifest()
    evaluator = HomeAssistantContractEvaluator(manifest)
    entity = HomeAssistantEntityRef("sensor", "sensor.temperature")
    service = HomeAssistantServiceRef("sensor", "get_state")

    assert repr(manifest) == "HomeAssistantCapabilityManifest()"
    assert repr(evaluator) == "HomeAssistantContractEvaluator()"
    assert repr(entity) == "HomeAssistantEntityRef()"
    assert repr(service) == "HomeAssistantServiceRef()"


def test_confirmation_actor_must_match_authenticated_owner() -> None:
    evaluator = HomeAssistantContractEvaluator(_make_sample_manifest())
    owner = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")
    proposal = evaluator.prepare_action(
        "p_actor", HomeAssistantEntityRef("light", "light.living_room"),
        HomeAssistantServiceRef("light", "turn_on"), {}, owner, "nonce_actor", 1000.0,
    ).proposal
    confirmation = HomeAssistantConfirmation("p_actor", "nonce_actor", "other_owner", 1001.0)
    result = evaluator.authorize_execution(proposal, confirmation, owner, 1001.0)
    assert result.outcome is HAActionOutcome.DENY
    assert result.reason is HAActionReason.UNAUTHORIZED_ACTOR


def test_service_domain_must_match_entity_domain() -> None:
    evaluator = HomeAssistantContractEvaluator(_make_sample_manifest())
    owner = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")
    result = evaluator.prepare_action(
        "p_domain", HomeAssistantEntityRef("light", "light.living_room"),
        HomeAssistantServiceRef("lock", "unlock"), {}, owner, "nonce_domain", 1000.0,
    )
    assert result.outcome is HAActionOutcome.DENY
    assert result.reason is HAActionReason.DOMAIN_NOT_ALLOWED


def test_direct_unprepared_proposal_fails_closed() -> None:
    evaluator = HomeAssistantContractEvaluator(_make_sample_manifest())
    owner = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")
    proposal = HomeAssistantActionProposal(
        "p_direct", HomeAssistantEntityRef("light", "light.living_room"),
        HomeAssistantServiceRef("light", "turn_on"), {}, True,
        1000.0, 1100.0, "nonce_direct",
    )
    confirmation = HomeAssistantConfirmation("p_direct", "nonce_direct", "owner_1", 1001.0)
    result = evaluator.authorize_execution(proposal, confirmation, owner, 1001.0)
    assert result.reason is HAActionReason.STALE_PROPOSAL
