from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.action_policy import Actor, ActorContext
from core.brain_service import BrainService


class FakeCoordinator:
    def __init__(self):
        self.recorded = []
        active = SimpleNamespace(
            memory_id="memory-1",
            candidate_id="candidate-1",
            episode_id="episode-1",
            statement="Owner prefers Topic A.",
            source_segment_ids=["segment-1"],
            metadata={
                "supersedes": "memory-0",
                "predecessor_evidence_segment_ids": ["segment-0"],
                "correction_audit": [{"action": "supersede"}],
            },
        )
        self.store = SimpleNamespace(get_active_accepted_memories=lambda limit: [active])
        self.packet = SimpleNamespace(
            hits=[
                SimpleNamespace(
                    source="working_memory",
                    text="unreviewed",
                    score=1.0,
                    metadata={},
                ),
                SimpleNamespace(
                    source="source_linked",
                    text="formatted",
                    score=0.8,
                    metadata={"memory_id": "memory-1"},
                ),
                SimpleNamespace(
                    source="source_linked",
                    text="inactive",
                    score=0.7,
                    metadata={"memory_id": "inactive-memory"},
                ),
            ]
        )

    def build_context_packet(self, query):
        return self.packet

    def record_turn(self, *args, **kwargs):
        self.recorded.append((args, kwargs))
        return "episode-1"


def _actor(role: Actor) -> ActorContext:
    return ActorContext(f"{role.value}-a", role, "session-1")


def test_reviewed_recall_filters_unreviewed_and_preserves_provenance():
    service = BrainService(FakeCoordinator())
    hits = service.recall_reviewed(_actor(Actor.OWNER), "what do I prefer?")
    assert len(hits) == 1
    assert hits[0].text == "Owner prefers Topic A."
    assert hits[0].memory_id == "memory-1"
    assert hits[0].candidate_id == "candidate-1"
    assert hits[0].episode_id == "episode-1"
    assert hits[0].source_segment_ids == ("segment-1",)
    assert hits[0].predecessor_memory_ids == ("memory-0",)
    assert hits[0].predecessor_evidence_segment_ids == ("segment-0",)
    assert hits[0].correction_actions == ("supersede",)


def test_corrected_hit_without_predecessor_evidence_is_excluded():
    coordinator = FakeCoordinator()
    active = coordinator.store.get_active_accepted_memories(1)[0]
    active.metadata.pop("predecessor_evidence_segment_ids")
    service = BrainService(coordinator)
    assert service.recall_reviewed(_actor(Actor.OWNER), "query") == ()


@pytest.mark.parametrize("role", [Actor.GUEST, Actor.UNKNOWN, Actor.SYSTEM])
def test_non_owner_recall_fails_closed(role):
    service = BrainService(FakeCoordinator())
    with pytest.raises(PermissionError):
        service.recall_reviewed(_actor(role), "private query")


def test_guest_access_does_not_initialize_brain(monkeypatch):
    initialized = False

    def construct():
        nonlocal initialized
        initialized = True
        raise AssertionError("must not initialize")

    monkeypatch.setattr("core.brain_service.BrainV2Coordinator", construct)
    service = BrainService()
    with pytest.raises(PermissionError):
        service.recall_reviewed(_actor(Actor.GUEST), "private query")
    assert initialized is False


def test_owner_recording_uses_server_actor_and_guest_cannot_record():
    coordinator = FakeCoordinator()
    service = BrainService(coordinator)
    actor = _actor(Actor.OWNER)
    assert service.record_turn(actor, "session-1", "hello") == "episode-1"
    args, kwargs = coordinator.recorded[0]
    assert args[:2] == ("session-1", "hello")
    assert kwargs["speaker_label"] == "owner-a"
    assert kwargs["metadata"]["actor"] == "owner"
    with pytest.raises(PermissionError):
        service.record_turn(_actor(Actor.GUEST), "session-1", "private disclosure")
    with pytest.raises(PermissionError):
        service.record_turn(actor, "different-session", "hello")


def test_owner_initialization_is_service_owned_and_idempotent(monkeypatch):
    coordinator = FakeCoordinator()
    construct = MagicMock(return_value=coordinator)
    monkeypatch.setattr("core.brain_service.BrainV2Coordinator", construct)
    service = BrainService()

    assert service.initialize_owner(_actor(Actor.OWNER), setting=False) is coordinator
    assert service.initialize_owner(_actor(Actor.OWNER), setting=True) is coordinator
    construct.assert_called_once_with(setting=False)
    assert service.owns(coordinator)


def test_guest_cannot_initialize_live_coordinator(monkeypatch):
    construct = MagicMock(side_effect=AssertionError("must not initialize"))
    monkeypatch.setattr("core.brain_service.BrainV2Coordinator", construct)
    with pytest.raises(PermissionError):
        BrainService().initialize_owner(_actor(Actor.GUEST))
    construct.assert_not_called()
