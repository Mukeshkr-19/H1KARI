"""Tests for core.vision.service pure analysis lifecycle."""

from __future__ import annotations

import ast
import pathlib
from collections import deque

import pytest

from core.action_policy import Actor, ActorContext
from core.vision.contracts import (
    ANALYSIS_TTL_SECONDS,
    MAX_OBSERVATIONS,
    VisionAnalysisState,
    VisionCapability,
    VisionObservation,
    VisionObservationKind,
    VisionOutcomeCode,
)
from core.vision.service import VisionAnalysisService


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self._value = start

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += seconds

    def set(self, value: float) -> None:
        self._value = value


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def analysis_ids() -> deque[str]:
    return deque(
        [
            "analysis-1",
            "analysis-2",
            "analysis-3",
            "analysis-4",
            "analysis-5",
        ]
    )


@pytest.fixture
def service(clock: _Clock, analysis_ids: deque[str]) -> VisionAnalysisService:
    return VisionAnalysisService(
        clock=clock,
        analysis_id_factory=lambda: analysis_ids.popleft(),
    )


@pytest.fixture
def owner() -> ActorContext:
    return ActorContext(
        actor_id="local-owner",
        actor=Actor.OWNER,
        session_id="session-1",
        source="local",
    )


@pytest.fixture
def guest() -> ActorContext:
    return ActorContext(
        actor_id="guest",
        actor=Actor.GUEST,
        session_id="session-2",
        source="websocket",
    )


def _text(value: str = "OCR line", confidence: int = 900) -> VisionObservation:
    return VisionObservation(
        kind=VisionObservationKind.TEXT,
        text=value,
        confidence_milli=confidence,
    )


def _prepare(service: VisionAnalysisService, actor: ActorContext):
    return service.prepare(
        actor,
        "request-1",
        "handoff-1",
        VisionCapability.OCR,
    )


def test_valid_transition_path(
    service: VisionAnalysisService, owner: ActorContext, clock: _Clock
) -> None:
    prepared = _prepare(service, owner)
    assert prepared.code is VisionOutcomeCode.AWAITING_IMAGE
    assert prepared.state is VisionAnalysisState.AWAITING_IMAGE
    assert prepared.analysis_id == "analysis-1"

    attached = service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1")
    assert attached.code is VisionOutcomeCode.READY
    assert attached.state is VisionAnalysisState.AWAITING_IMAGE

    begun = service.begin_analysis(owner, "analysis-1")
    assert begun.code is VisionOutcomeCode.ANALYZING
    assert begun.state is VisionAnalysisState.ANALYZING

    completed = service.complete(owner, "analysis-1", (_text(),))
    assert completed.code is VisionOutcomeCode.COMPLETED
    assert completed.state is VisionAnalysisState.COMPLETED
    assert completed.observation_count == 1
    assert clock() == 1000.0


def test_invalid_transitions(service: VisionAnalysisService, owner: ActorContext) -> None:
    _prepare(service, owner)
    assert service.begin_analysis(owner, "analysis-1").code is (
        VisionOutcomeCode.INVALID_REQUEST
    )
    assert service.complete(owner, "analysis-1", (_text(),)).code is (
        VisionOutcomeCode.INVALID_REQUEST
    )

    service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1")
    assert service.complete(owner, "analysis-1", (_text(),)).code is (
        VisionOutcomeCode.INVALID_REQUEST
    )

    service.begin_analysis(owner, "analysis-1")
    assert service.attach_image(owner, "analysis-1", "handoff-1", "transfer-2").code is (
        VisionOutcomeCode.INVALID_REQUEST
    )


def test_duplicate_prepare_is_idempotent(
    service: VisionAnalysisService, owner: ActorContext
) -> None:
    first = _prepare(service, owner)
    second = _prepare(service, owner)
    assert first.analysis_id == second.analysis_id == "analysis-1"
    assert second.code is VisionOutcomeCode.AWAITING_IMAGE


def test_duplicate_prepare_conflict_on_different_capability(
    service: VisionAnalysisService, owner: ActorContext
) -> None:
    _prepare(service, owner)
    conflict = service.prepare(
        owner,
        "request-1",
        "handoff-1",
        VisionCapability.DESCRIBE,
    )
    assert conflict.code is VisionOutcomeCode.INVALID_REQUEST


def test_invalid_factory_id_fails_safely(clock: _Clock, owner: ActorContext) -> None:
    service = VisionAnalysisService(
        clock=clock,
        analysis_id_factory=lambda: "BAD ID",
    )
    outcome = service.prepare(
        owner,
        "request-1",
        "handoff-1",
        VisionCapability.OCR,
    )
    assert outcome.code is VisionOutcomeCode.UNAVAILABLE
    assert outcome.analysis_id is None


def test_factory_exception_fails_safely(clock: _Clock, owner: ActorContext) -> None:
    def boom() -> str:
        raise RuntimeError("PRIVATE_FACTORY_FAILURE")

    service = VisionAnalysisService(clock=clock, analysis_id_factory=boom)
    outcome = service.prepare(
        owner,
        "request-1",
        "handoff-1",
        VisionCapability.OCR,
    )
    assert outcome.code is VisionOutcomeCode.UNAVAILABLE
    assert "PRIVATE_FACTORY_FAILURE" not in repr(outcome)


def test_exact_expiry_boundaries(
    service: VisionAnalysisService, owner: ActorContext, clock: _Clock
) -> None:
    _prepare(service, owner)
    clock.advance(ANALYSIS_TTL_SECONDS - 1)
    assert service.status(owner, "analysis-1").code is VisionOutcomeCode.AWAITING_IMAGE

    clock.advance(1)
    expired = service.status(owner, "analysis-1")
    assert expired.code is VisionOutcomeCode.EXPIRED
    assert expired.state is VisionAnalysisState.EXPIRED

    assert service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1").code is (
        VisionOutcomeCode.EXPIRED
    )
    assert service.begin_analysis(owner, "analysis-1").code is VisionOutcomeCode.EXPIRED
    assert service.complete(owner, "analysis-1", (_text(),)).code is (
        VisionOutcomeCode.EXPIRED
    )


def test_expire_due(
    service: VisionAnalysisService, owner: ActorContext, clock: _Clock
) -> None:
    _prepare(service, owner)
    service.prepare(owner, "request-2", "handoff-2", VisionCapability.DESCRIBE)
    clock.advance(ANALYSIS_TTL_SECONDS)
    assert service.expire_due() == 2
    assert service.status(owner, "analysis-1").code is VisionOutcomeCode.EXPIRED
    assert service.status(owner, "analysis-2").code is VisionOutcomeCode.EXPIRED


def test_future_clock_values(
    service: VisionAnalysisService, owner: ActorContext, clock: _Clock
) -> None:
    clock.set(50_000.0)
    prepared = _prepare(service, owner)
    assert prepared.code is VisionOutcomeCode.AWAITING_IMAGE
    assert prepared.analysis_id == "analysis-1"


def test_cross_session_non_disclosure_and_non_mutation(
    service: VisionAnalysisService,
    owner: ActorContext,
    guest: ActorContext,
) -> None:
    _prepare(service, owner)
    service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1")

    missing = service.status(guest, "analysis-1")
    assert missing.code is VisionOutcomeCode.ANALYSIS_NOT_FOUND
    assert missing.analysis_id is None

    assert service.attach_image(guest, "analysis-1", "handoff-1", "transfer-9").code is (
        VisionOutcomeCode.ANALYSIS_NOT_FOUND
    )
    assert service.begin_analysis(guest, "analysis-1").code is (
        VisionOutcomeCode.ANALYSIS_NOT_FOUND
    )
    assert service.complete(guest, "analysis-1", (_text(),)).code is (
        VisionOutcomeCode.ANALYSIS_NOT_FOUND
    )
    assert service.cancel(guest, "analysis-1").code is (
        VisionOutcomeCode.ANALYSIS_NOT_FOUND
    )

    owner_status = service.status(owner, "analysis-1")
    assert owner_status.code is VisionOutcomeCode.READY
    assert owner_status.state is VisionAnalysisState.AWAITING_IMAGE


def test_cancel_and_duplicate_cancel(
    service: VisionAnalysisService, owner: ActorContext
) -> None:
    _prepare(service, owner)
    first = service.cancel(owner, "analysis-1")
    second = service.cancel(owner, "analysis-1")
    assert first.code is VisionOutcomeCode.CANCELLED
    assert second.code is VisionOutcomeCode.CANCELLED
    assert service.complete(owner, "analysis-1", (_text(),)).code is (
        VisionOutcomeCode.INVALID_REQUEST
    )


def test_cancel_after_completed_fails(
    service: VisionAnalysisService, owner: ActorContext
) -> None:
    _prepare(service, owner)
    service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1")
    service.begin_analysis(owner, "analysis-1")
    service.complete(owner, "analysis-1", (_text(),))
    assert service.cancel(owner, "analysis-1").code is VisionOutcomeCode.INVALID_REQUEST


def test_completion_ordering_and_idempotent_complete(
    service: VisionAnalysisService, owner: ActorContext
) -> None:
    _prepare(service, owner)
    service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1")
    service.begin_analysis(owner, "analysis-1")
    first = service.complete(owner, "analysis-1", (_text("one"), _text("two")))
    second = service.complete(owner, "analysis-1", (_text("changed"),))
    assert first.code is VisionOutcomeCode.COMPLETED
    assert first.observation_count == 2
    assert second.code is VisionOutcomeCode.COMPLETED
    assert second.observation_count == 2


def test_attach_idempotent_same_transfer(
    service: VisionAnalysisService, owner: ActorContext
) -> None:
    _prepare(service, owner)
    first = service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1")
    second = service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1")
    assert first.code is VisionOutcomeCode.READY
    assert second.code is VisionOutcomeCode.READY
    conflict = service.attach_image(owner, "analysis-1", "handoff-1", "transfer-2")
    assert conflict.code is VisionOutcomeCode.INVALID_REQUEST


def test_begin_idempotent(service: VisionAnalysisService, owner: ActorContext) -> None:
    _prepare(service, owner)
    service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1")
    first = service.begin_analysis(owner, "analysis-1")
    second = service.begin_analysis(owner, "analysis-1")
    assert first.code is VisionOutcomeCode.ANALYZING
    assert second.code is VisionOutcomeCode.ANALYZING


def test_observation_count_bounds(
    service: VisionAnalysisService, owner: ActorContext
) -> None:
    _prepare(service, owner)
    service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1")
    service.begin_analysis(owner, "analysis-1")
    too_many = tuple(_text(f"line-{index}") for index in range(MAX_OBSERVATIONS + 1))
    assert service.complete(owner, "analysis-1", too_many).code is (
        VisionOutcomeCode.INVALID_REQUEST
    )
    empty = service.complete(owner, "analysis-1", ())
    assert empty.code is VisionOutcomeCode.INVALID_REQUEST


def test_unicode_observation_handling(
    service: VisionAnalysisService, owner: ActorContext
) -> None:
    _prepare(service, owner)
    service.attach_image(owner, "analysis-1", "handoff-1", "transfer-1")
    service.begin_analysis(owner, "analysis-1")
    observations = (
        VisionObservation(
            kind=VisionObservationKind.TEXT,
            text="日本語\nOCR",
            confidence_milli=500,
        ),
        VisionObservation(
            kind=VisionObservationKind.DESCRIPTION,
            text="A quiet desk scene",
            confidence_milli=700,
        ),
    )
    completed = service.complete(owner, "analysis-1", observations)
    assert completed.code is VisionOutcomeCode.COMPLETED
    assert completed.observation_count == 2


def test_content_free_repr(service: VisionAnalysisService) -> None:
    assert repr(service) == "VisionAnalysisService()"
    assert str(service) == "VisionAnalysisService()"


def test_clock_exception_does_not_leak(clock: _Clock, owner: ActorContext) -> None:
    def bad_clock() -> float:
        raise RuntimeError("PRIVATE_CLOCK_PATH_/tmp/secret")

    service = VisionAnalysisService(
        clock=bad_clock,
        analysis_id_factory=lambda: "analysis-1",
    )
    outcome = service.prepare(
        owner,
        "request-1",
        "handoff-1",
        VisionCapability.OCR,
    )
    assert outcome.code is VisionOutcomeCode.UNAVAILABLE
    assert "PRIVATE_CLOCK" not in repr(outcome)
    assert "/tmp/secret" not in repr(outcome)


def test_invalid_actor_rejected(service: VisionAnalysisService) -> None:
    outcome = service.prepare(
        object(),  # type: ignore[arg-type]
        "request-1",
        "handoff-1",
        VisionCapability.OCR,
    )
    assert outcome.code is VisionOutcomeCode.INVALID_REQUEST


def test_service_has_no_forbidden_imports() -> None:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "vision"
        / "service.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "asyncio",
        "requests",
        "http",
        "urllib",
        "sqlite3",
        "secrets",
        "time",
        "pathlib",
        "os",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported)
    assert "threading" in imported
