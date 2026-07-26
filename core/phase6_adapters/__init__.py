"""HIKARI Phase 6 isolated optional adapter layer.

This package provides fail-closed, disabled-by-default adapters for live
transports and optional capabilities.  Every adapter requires explicit
configuration and injected dependencies; default construction leaves each
adapter unavailable.  No real network, storage, model, or subprocess operations
are performed by this package.
"""

from core.phase6_adapters.contracts import (
    AdapterException,
    AdapterOutcome,
    AdapterReason,
    AdapterState,
)
from core.phase6_adapters.encrypted_sync import (
    EncryptedSyncAdapter,
    EncryptedSyncAdapterConfig,
)
from core.phase6_adapters.home_assistant import (
    HomeAssistantAdapter,
    HomeAssistantAdapterConfig,
)
from core.phase6_adapters.measured_routing import (
    MeasuredRoutingAdapter,
    MeasuredRoutingAdapterConfig,
)
from core.phase6_adapters.remote_worker import (
    RemoteWorkerCoordinator,
    RemoteWorkerCoordinatorConfig,
)
from core.phase6_adapters.skill_staging import (
    SkillStagingAdapter,
    SkillStagingAdapterConfig,
)

__all__ = [
    "AdapterException",
    "AdapterOutcome",
    "AdapterReason",
    "AdapterState",
    "EncryptedSyncAdapter",
    "EncryptedSyncAdapterConfig",
    "HomeAssistantAdapter",
    "HomeAssistantAdapterConfig",
    "MeasuredRoutingAdapter",
    "MeasuredRoutingAdapterConfig",
    "RemoteWorkerCoordinator",
    "RemoteWorkerCoordinatorConfig",
    "SkillStagingAdapter",
    "SkillStagingAdapterConfig",
]
