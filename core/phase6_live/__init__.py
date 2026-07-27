"""HIKARI Phase 6 live, optional, disabled-by-default backend implementations.

This package provides real implementations for the injected interfaces in
``core.phase6_adapters``.  Every backend remains disabled unless explicitly
constructed with a configuration object; no module-level side effects occur.
"""

from core.phase6_live.encrypted_sync import (
    SqliteDeviceRegistry,
    SqliteEncryptedSyncStorage,
    SqliteNonceRegistry,
    SqliteTransactionRegistry,
)
from core.phase6_live.home_assistant import HTTPConnectionFactory, LiveHomeAssistantTransport
from core.phase6_live.measured_routing import SqliteMeasuredRoutingSource
from core.phase6_live.remote_worker import (
    SqliteRemoteWorkerNonceStore,
    SqliteRemoteWorkerState,
)
from core.phase6_live.skill_staging import LiveArchiveEntryReader

__all__ = [
    "HTTPConnectionFactory",
    "LiveHomeAssistantTransport",
    "SqliteDeviceRegistry",
    "SqliteEncryptedSyncStorage",
    "SqliteMeasuredRoutingSource",
    "SqliteNonceRegistry",
    "SqliteRemoteWorkerNonceStore",
    "SqliteRemoteWorkerState",
    "SqliteTransactionRegistry",
    "LiveArchiveEntryReader",
]
