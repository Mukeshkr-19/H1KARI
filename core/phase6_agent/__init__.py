"""Phase 6 bounded agent execution foundations.

Injected execution kernel with fail-closed authorization. This package is not
wired into the live orchestrator, server, protocol, frontend, or voice path.
"""

from core.phase6_agent.contracts import (
    ActionObservation,
    ActionOutcome,
    ApprovalReference,
    AuthorizationDecision,
    AuthorizationOutcome,
    AuthorizedAction,
    ConfidenceCategory,
    CorrectionRequest,
    FailureCode,
    LoopBudget,
    LoopEvent,
    LoopRequest,
    LoopResult,
    LoopState,
    ProposedAction,
    RemoteWorkerAuthorityEnvelope,
    RemoteWorkerResult,
)
from core.phase6_agent.loop import BoundedAgentLoop
from core.phase6_agent.remote_worker import (
    RemoteEnvelopeValidation,
    RemoteEvidenceAcceptance,
    RemoteValidationOutcome,
    accept_remote_result,
    consume_nonce,
    validate_remote_envelope,
)

__all__ = [
    "ActionObservation",
    "ActionOutcome",
    "ApprovalReference",
    "AuthorizationDecision",
    "AuthorizationOutcome",
    "AuthorizedAction",
    "BoundedAgentLoop",
    "ConfidenceCategory",
    "CorrectionRequest",
    "FailureCode",
    "LoopBudget",
    "LoopEvent",
    "LoopRequest",
    "LoopResult",
    "LoopState",
    "ProposedAction",
    "RemoteEnvelopeValidation",
    "RemoteEvidenceAcceptance",
    "RemoteValidationOutcome",
    "RemoteWorkerAuthorityEnvelope",
    "RemoteWorkerResult",
    "accept_remote_result",
    "consume_nonce",
    "validate_remote_envelope",
]
