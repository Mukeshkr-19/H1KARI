"""Private runtime composition for the Phase 1 document workflow."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from core.action_audit import ActionAuditStore
from core.action_policy import Actor, ActorContext
from core.document_service import DocumentService
from core.grants import GrantStore
from core.policy_service import PolicyService
from core.router import AIRouter, get_router
from core.runtime_paths import brain_dir, hikari_home
from core.tasks import TaskIntentService, TaskRecordContext


@dataclass(frozen=True)
class Phase1Runtime:
    documents: DocumentService
    policy: PolicyService
    tasks: TaskIntentService


def create_phase1_runtime(*, router: AIRouter | None = None) -> Phase1Runtime:
    """Build the document workflow with state kept under private HIKARI_HOME."""
    home = hikari_home()
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    private_brain = brain_dir()
    private_brain.mkdir(parents=True, exist_ok=True)
    private_brain.chmod(0o700)
    state_dir = home / "policy"
    grants = GrantStore(state_dir / "grants.db")
    audit = ActionAuditStore(state_dir / "actions.db")
    tasks = TaskIntentService()
    policy = PolicyService(grants, audit)
    documents = DocumentService(tasks, policy, router or get_router())
    return Phase1Runtime(documents=documents, policy=policy, tasks=tasks)


def owner_contexts(
    *,
    session_id: str | None = None,
    source: str = "text",
) -> tuple[ActorContext, TaskRecordContext]:
    """Derive matching local-owner contexts; no client identity fields are accepted."""
    session = session_id or secrets.token_hex(16)
    actor = ActorContext("local-owner", Actor.OWNER, session, source)
    task = TaskRecordContext(
        speaker_label=actor.actor_id,
        session_id=actor.session_id,
        source=actor.source,
        actor=actor.actor.value,
    )
    return actor, task
