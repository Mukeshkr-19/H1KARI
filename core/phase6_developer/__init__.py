"""Phase 6 developer-mode repository intelligence and policy planning.

All policy/evaluation types and functions are pure, immutable, and side-effect
free. Repository indexing reads files from a caller-supplied root but performs no
writes, no subprocess, no network, and no Git execution.
"""

from core.phase6_developer.contracts import (
    ChangeIntent,
    FileKind,
    FileSnapshot,
    GitOperationClass,
    GitOperationRequest,
    GitPolicyDecision,
    GitPolicyOutcome,
    GitPolicyReason,
    GitStateSnapshot,
    ImportRecord,
    ReferenceRecord,
    RelationshipEdge,
    RelationshipType,
    RepositoryHit,
    RepositoryIndex,
    RepositoryPolicy,
    RepositoryQuery,
    RepositoryQueryResult,
    RepositoryReason,
    RepositoryRoot,
    SandboxCommandRequest,
    SandboxOutcome,
    SandboxPolicyDecision,
    SandboxReason,
    ScoreBreakdown,
    SymbolRecord,
)
from core.phase6_developer.git_policy import (
    classify_git_operation,
    evaluate_git_policy,
)
from core.phase6_developer.query import evaluate_query
from core.phase6_developer.index import index_repository
from core.phase6_developer.repository import (
    RepositoryScanError,
    scan_repository,
)
from core.phase6_developer.sandbox import evaluate_sandbox_policy

__all__ = [
    # Contracts
    "ChangeIntent",
    "FileKind",
    "FileSnapshot",
    "GitOperationClass",
    "GitOperationRequest",
    "GitPolicyDecision",
    "GitPolicyOutcome",
    "GitPolicyReason",
    "GitStateSnapshot",
    "ImportRecord",
    "ReferenceRecord",
    "RelationshipEdge",
    "RelationshipType",
    "RepositoryHit",
    "RepositoryIndex",
    "RepositoryPolicy",
    "RepositoryQuery",
    "RepositoryQueryResult",
    "RepositoryReason",
    "RepositoryRoot",
    "SandboxCommandRequest",
    "SandboxOutcome",
    "SandboxPolicyDecision",
    "SandboxReason",
    "ScoreBreakdown",
    "SymbolRecord",
    # Repository
    "RepositoryScanError",
    "scan_repository",
    "index_repository",
    "evaluate_query",
    # Git
    "classify_git_operation",
    "evaluate_git_policy",
    # Sandbox
    "evaluate_sandbox_policy",
]
