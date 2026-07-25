"""Deterministic child-mode action guard.

This module classifies caller-supplied action descriptors as allowed, denied,
or requiring owner approval.  It uses no AI, no external classifiers, and no
I/O.  Matching is normalized to case and spacing before comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple

from core.phase5.contracts import Outcome


class ChildModeDecisionReason(StrEnum):
    """Stable reasons for child-mode classification decisions."""

    ALLOWED = "child_allowed"
    REQUIRE_OWNER_APPROVAL = "child_require_owner_approval"
    OWNER_MEMORY_BLOCKED = "child_owner_memory_blocked"
    PURCHASE_BLOCKED = "child_purchase_blocked"
    COMMUNICATION_BLOCKED = "child_communication_blocked"
    DANGEROUS_BLOCKED = "child_dangerous_blocked"
    WEAPON_HAZARD_BLOCKED = "child_weapon_hazard_blocked"
    AUDIT_BYPASS_BLOCKED = "child_audit_bypass_blocked"
    APPROVAL_BYPASS_BLOCKED = "child_approval_bypass_blocked"
    IDENTITY_BYPASS_BLOCKED = "child_identity_bypass_blocked"
    GRANT_CREATION_BLOCKED = "child_grant_creation_blocked"
    POLICY_WEAKENING_BLOCKED = "child_policy_weakening_blocked"
    BROWSING_DOWNLOAD_BLOCKED = "child_browsing_download_blocked"
    SECRET_CREDENTIAL_BLOCKED = "child_secret_credential_blocked"
    AMBIGUOUS_BLOCKED = "child_ambiguous_blocked"
    UNKNOWN_CATEGORY = "child_unknown_category"
    INVALID_DESCRIPTOR = "child_invalid_descriptor"


class ChildActionCategory(StrEnum):
    """High-level categories the guard recognizes.

    Categories in the ``SAFE`` group may be allowed when scoped to a child.
    Categories in the ``BLOCKED`` group are hard-denied.  Anything not in
    either group is treated as ambiguous.
    """

    # Safe, child-scoped categories
    EDUCATIONAL = "educational"
    LEARNING = "learning"
    STUDY = "study"
    HOMEWORK = "homework"
    QUESTION = "question"
    GUIDANCE = "guidance"
    CARE = "care"
    SUPPORT = "support"
    CHILD_SAFE = "child_safe"

    # Blocked categories
    OWNER_MEMORY = "owner_memory"
    PRIVATE_MEMORY = "private_memory"
    FINANCIAL = "financial"
    PURCHASE = "purchase"
    PAYMENT = "payment"
    EXTERNAL_CALL = "external_call"
    EXTERNAL_COMMUNICATION = "external_communication"
    MESSAGE = "message"
    EMAIL = "email"
    POSTING = "posting"
    DANGEROUS = "dangerous"
    SELF_HARM = "self_harm"
    HARM = "harm"
    WEAPON = "weapon"
    HAZARDOUS = "hazardous"
    AUDIT_BYPASS = "audit_bypass"
    APPROVAL_BYPASS = "approval_bypass"
    IDENTITY_BYPASS = "identity_bypass"
    AUTHENTICATION_BYPASS = "authentication_bypass"
    PERMISSION_GRANT = "permission_grant"
    GRANT_CREATION = "grant_creation"
    POLICY_WEAKENING = "policy_weakening"
    UNRESTRICTED_BROWSING = "unrestricted_browsing"
    DOWNLOAD = "download"
    SECRET_ACCESS = "secret_access"
    CREDENTIAL_ACCESS = "credential_access"


# Normalized token sets for each blocked reason.  A category matches a reason
# if its normalized form equals any token in the set.

_BLOCKED_OWNER_MEMORY = frozenset({"ownermemory", "privatememory", "ownerprivate", "private"})
_BLOCKED_FINANCIAL = frozenset({"purchase", "payment", "financial", "buy", "checkout"})
_BLOCKED_COMMUNICATION = frozenset({
    "externalcall",
    "externalcommunication",
    "message",
    "email",
    "posting",
    "post",
    "sendmessage",
    "phonecall",
    "textmessage",
})
_BLOCKED_DANGEROUS = frozenset({"dangerous", "selfharm", "harm", "suicide", "selfinjury"})
_BLOCKED_WEAPON_HAZARD = frozenset({"weapon", "hazardous", "explosive", "chemical", "toxic"})
_BLOCKED_AUDIT_BYPASS = frozenset(
    {"auditbypass", "bypassaudit", "auditdisable", "disableaudit"}
)
_BLOCKED_APPROVAL_BYPASS = frozenset(
    {"approvalbypass", "bypassapproval", "approvaldisable", "disableapproval"}
)
_BLOCKED_IDENTITY_BYPASS = frozenset({
    "identitybypass",
    "authenticationbypass",
    "authbypass",
    "bypassauthentication",
})
_BLOCKED_GRANT_CREATION = frozenset({"permissiongrant", "grantcreation", "creategrant", "helpergrant"})
_BLOCKED_POLICY_WEAKENING = frozenset({"policyweakening", "weakenpolicy", "weaken", "bypasspolicy"})
_BLOCKED_BROWSING_DOWNLOAD = frozenset({"unrestrictedbrowsing", "unrestricteddownload", "download", "browsing"})
_BLOCKED_SECRET_CREDENTIAL = frozenset({
    "secretaccess",
    "credentialaccess",
    "password",
    "token",
    "apikey",
    "secret",
    "secrets",
    "credential",
    "credentials",
    "key",
})

# Safe category tokens.  A descriptor is considered safe if any normalized token
# from its category is present in this set and no hard-deny rule fires.
_SAFE_TOKENS = frozenset({
    "educational",
    "learning",
    "learn",
    "study",
    "homework",
    "question",
    "guidance",
    "guide",
    "care",
    "support",
    "childsafe",
    "child_safe",
    "safe",
})


# Subject tokens that are considered owner-private.
_OWNER_PRIVATE_SUBJECTS = frozenset({"owner", "private", "adult", "parent", "personal"})


def _normalize(value: Optional[str]) -> str:
    if value is None:
        return ""
    # Lowercase, strip, collapse spaces/underscores/hyphens, then remove them.
    normalized = str(value).lower().strip()
    normalized = re.sub(r"[\s\-_]+", "", normalized)
    return normalized


def _tokenize(value: Optional[str]) -> list[str]:
    """Return normalized tokens from a human-written string."""
    if value is None:
        return []
    # Split on whitespace, underscores, hyphens, and non-alphanumeric delimiters,
    # then convert to normalized tokens.  Also split camelCase.
    parts = re.split(r"[\s\W_]+", value.lower().strip())
    tokens: list[str] = []
    for part in parts:
        if not part:
            continue
        tokens.append(_normalize(part))
        # Also split camelCase so "ChildSafe" becomes "child", "safe".
        tokens.extend(p for p in re.split(r"(?=[A-Z])", part) if p)
    return tokens


def _has_blocked_token(value: Optional[str], tokens: frozenset[str]) -> bool:
    normalized = _normalize(value)
    if not normalized:
        return False
    words = tuple(token for token in _tokenize(value) if token)
    candidates = {normalized, *words}
    candidates.update(
        "".join(words[index : index + width])
        for width in (2, 3)
        for index in range(max(0, len(words) - width + 1))
    )
    return not candidates.isdisjoint(tokens)


def _block_reason_for_category(category: str) -> Optional[ChildModeDecisionReason]:
    if _has_blocked_token(category, _BLOCKED_OWNER_MEMORY):
        return ChildModeDecisionReason.OWNER_MEMORY_BLOCKED
    if _has_blocked_token(category, _BLOCKED_FINANCIAL):
        return ChildModeDecisionReason.PURCHASE_BLOCKED
    if _has_blocked_token(category, _BLOCKED_COMMUNICATION):
        return ChildModeDecisionReason.COMMUNICATION_BLOCKED
    if _has_blocked_token(category, _BLOCKED_DANGEROUS):
        return ChildModeDecisionReason.DANGEROUS_BLOCKED
    if _has_blocked_token(category, _BLOCKED_WEAPON_HAZARD):
        return ChildModeDecisionReason.WEAPON_HAZARD_BLOCKED
    if _has_blocked_token(category, _BLOCKED_AUDIT_BYPASS):
        return ChildModeDecisionReason.AUDIT_BYPASS_BLOCKED
    if _has_blocked_token(category, _BLOCKED_APPROVAL_BYPASS):
        return ChildModeDecisionReason.APPROVAL_BYPASS_BLOCKED
    if _has_blocked_token(category, _BLOCKED_IDENTITY_BYPASS):
        return ChildModeDecisionReason.IDENTITY_BYPASS_BLOCKED
    if _has_blocked_token(category, _BLOCKED_GRANT_CREATION):
        return ChildModeDecisionReason.GRANT_CREATION_BLOCKED
    if _has_blocked_token(category, _BLOCKED_POLICY_WEAKENING):
        return ChildModeDecisionReason.POLICY_WEAKENING_BLOCKED
    if _has_blocked_token(category, _BLOCKED_BROWSING_DOWNLOAD):
        return ChildModeDecisionReason.BROWSING_DOWNLOAD_BLOCKED
    if _has_blocked_token(category, _BLOCKED_SECRET_CREDENTIAL):
        return ChildModeDecisionReason.SECRET_CREDENTIAL_BLOCKED
    return None


@dataclass(frozen=True)
class ChildActionDescriptor:
    """Caller-supplied description of a child-mode action.

    No content is rendered by ``__repr__``.
    """

    category: str
    action: Optional[str] = None
    subject: Optional[str] = None
    resource: Optional[str] = None
    metadata: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.category, str) or not self.category:
            raise ValueError("category is required")
        if self.action is not None and not isinstance(self.action, str):
            raise ValueError("action must be a string or None")
        if self.subject is not None and not isinstance(self.subject, str):
            raise ValueError("subject must be a string or None")
        if self.resource is not None and not isinstance(self.resource, str):
            raise ValueError("resource must be a string or None")
        if not isinstance(self.metadata, tuple):
            raise ValueError("metadata must be a tuple")
        if any(not isinstance(item, str) for item in self.metadata):
            raise ValueError("metadata must contain strings")

    def __repr__(self) -> str:
        return "ChildActionDescriptor(redacted)"


@dataclass(frozen=True)
class ChildModeDecision:
    """Result of classifying an action for child mode."""

    outcome: Outcome
    reason: ChildModeDecisionReason
    category: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, Outcome):
            raise ValueError("invalid outcome")
        if not isinstance(self.reason, ChildModeDecisionReason):
            raise ValueError("invalid reason")

    def __repr__(self) -> str:
        return f"ChildModeDecision(outcome={self.outcome.value!r}, reason={self.reason.value!r})"


def classify_child_action(descriptor: ChildActionDescriptor) -> ChildModeDecision:
    """Classify a single action descriptor for child mode.

    The classification is deterministic, normalized, and fail-closed:
    - Known safe categories are allowed.
    - Known blocked categories are denied with a specific reason.
    - Owner/private subjects and secret resources are denied.
    - Ambiguous categories require owner approval or are denied.
    """
    if not isinstance(descriptor, ChildActionDescriptor):
        return ChildModeDecision(
            outcome=Outcome.DENY,
            reason=ChildModeDecisionReason.INVALID_DESCRIPTOR,
        )

    category = _normalize(descriptor.category)
    if not category:
        return ChildModeDecision(
            outcome=Outcome.DENY,
            reason=ChildModeDecisionReason.INVALID_DESCRIPTOR,
        )

    # 1. Hard-deny by category
    reason = _block_reason_for_category(descriptor.category)
    if reason is not None:
        return ChildModeDecision(outcome=Outcome.DENY, reason=reason, category=descriptor.category)

    # 2. Hard-deny across every caller-supplied descriptor field.  Checking
    # complete phrases and tokens prevents a safe category from masking a
    # dangerous action such as "please send a message".
    if _has_blocked_token(descriptor.subject, _OWNER_PRIVATE_SUBJECTS):
        return ChildModeDecision(
            outcome=Outcome.DENY,
            reason=ChildModeDecisionReason.OWNER_MEMORY_BLOCKED,
            category=descriptor.category,
        )

    # 3. Hard-deny by resource (secrets/credentials)
    if _has_blocked_token(descriptor.resource, _BLOCKED_SECRET_CREDENTIAL):
        return ChildModeDecision(
            outcome=Outcome.DENY,
            reason=ChildModeDecisionReason.SECRET_CREDENTIAL_BLOCKED,
            category=descriptor.category,
        )

    # 4. Hard-deny by action token (catch variants that omit the category)
    for value in (
        descriptor.action,
        descriptor.resource,
        *descriptor.metadata,
    ):
        action_reason = _block_reason_for_category(value or "")
        if action_reason is not None:
            return ChildModeDecision(
                outcome=Outcome.DENY,
                reason=action_reason,
                category=descriptor.category,
            )

    # 5. Allow known safe categories (normalized token match)
    if any(token in _SAFE_TOKENS for token in _tokenize(descriptor.category) if token):
        return ChildModeDecision(
            outcome=Outcome.ALLOW,
            reason=ChildModeDecisionReason.ALLOWED,
            category=descriptor.category,
        )

    # 6. Ambiguous: require owner approval (or deny if strict)
    return ChildModeDecision(
        outcome=Outcome.REQUIRE_APPROVAL,
        reason=ChildModeDecisionReason.AMBIGUOUS_BLOCKED,
        category=descriptor.category,
    )


__all__ = [
    "ChildActionDescriptor",
    "ChildActionCategory",
    "ChildModeDecision",
    "ChildModeDecisionReason",
    "classify_child_action",
]
