"""Private, lazy composition for Phase 4 pairing and handoff control planes."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.handoff import (
    AcceptancePolicy,
    HandoffRuntime,
    HandoffService,
    HandoffStore,
    HandoffTransportAdapter,
    TaskLookup,
)
from core.pairing import PairingRuntime, create_pairing_runtime
from core.pairing.runtime import ChallengeDisplaySink
from core.runtime_paths import hikari_home
from core.visual_transfer import (
    VisualTransferBuffer,
    VisualTransferRuntime,
    VisualTransferService,
)
from core.vision import (
    DescriptionAnalyzer,
    VisionAnalysisService,
    VisionRuntime,
    create_optional_mlx_description_adapter_from_environment,
)
from core.vision.ocr import LocalOcrAdapter


class Phase4BootstrapError(RuntimeError):
    """Fixed public bootstrap failure without paths or exception details."""

    def __init__(self) -> None:
        super().__init__("phase 4 bootstrap failed")

    def __repr__(self) -> str:
        return "Phase4BootstrapError()"


@dataclass(frozen=True)
class Phase4Subsystem:
    pairing_runtime: PairingRuntime
    handoff_runtime: HandoffRuntime
    handoff_transport: HandoffTransportAdapter
    visual_transfer_runtime: VisualTransferRuntime
    vision_runtime: VisionRuntime


def _handoff_id() -> str:
    return f"handoff-{secrets.token_hex(16)}"


def _transfer_id() -> str:
    return f"transfer-{secrets.token_hex(16)}"


def _analysis_id() -> str:
    return f"analysis-{secrets.token_hex(16)}"


def _default_tesseract_path() -> Path | None:
    for candidate in (
        Path("/opt/homebrew/bin/tesseract"),
        Path("/usr/local/bin/tesseract"),
        Path("/usr/bin/tesseract"),
    ):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def create_phase4_subsystem(
    *,
    task_lookup: TaskLookup,
    acceptance_policy: AcceptancePolicy,
    clock: Callable[[], float] | None = None,
    handoff_db_path: Path | str | None = None,
    pairing_db_path: Path | str | None = None,
    handoff_id_factory: Callable[[], str] | None = None,
    transfer_id_factory: Callable[[], str] | None = None,
    analysis_id_factory: Callable[[], str] | None = None,
    ocr_executable_path: Path | str | None = None,
    description_analyzer: DescriptionAnalyzer | None = None,
    challenge_id_factory: Callable[[], str] | None = None,
    device_id_factory: Callable[[], str] | None = None,
    secret_code_factory: Callable[[], str] | None = None,
    digest_key: bytes | None = None,
    display_sink: ChallengeDisplaySink | None = None,
) -> Phase4Subsystem:
    """Construct Phase 4 state only when explicitly called by server startup."""
    try:
        if not callable(task_lookup) or not callable(acceptance_policy):
            raise TypeError("phase 4 callables are required")
        clock_fn = clock or time.time
        handoff_path = (
            hikari_home() / "phase4" / "handoffs.db"
            if handoff_db_path is None
            else Path(handoff_db_path).expanduser().resolve()
        )
        handoff_store = HandoffStore(
            handoff_path,
            clock=clock_fn,
            handoff_id_factory=handoff_id_factory or _handoff_id,
        )
        handoff_service = HandoffService(
            handoff_store,
            task_lookup=task_lookup,
            acceptance_policy=acceptance_policy,
        )
        handoff_runtime = HandoffRuntime(handoff_service)
        handoff_transport = HandoffTransportAdapter(handoff_runtime)
        visual_buffer = VisualTransferBuffer(clock=clock_fn)
        visual_service = VisualTransferService(
            buffer=visual_buffer,
            clock=clock_fn,
            transfer_id_factory=transfer_id_factory or _transfer_id,
            handoff_accepted=handoff_service.is_accepted_for_session,
        )
        vision_service = VisionAnalysisService(
            clock=clock_fn,
            analysis_id_factory=analysis_id_factory or _analysis_id,
        )
        selected_ocr_path = (
            _default_tesseract_path()
            if ocr_executable_path is None
            else Path(ocr_executable_path)
        )
        ocr_adapter = (
            None
            if selected_ocr_path is None
            else LocalOcrAdapter(executable_path=selected_ocr_path)
        )
        selected_description_analyzer = description_analyzer
        if selected_description_analyzer is None:
            selected_description_analyzer = (
                create_optional_mlx_description_adapter_from_environment()
            )
        pairing_runtime = create_pairing_runtime(
            db_path=pairing_db_path,
            clock=clock_fn,
            challenge_id_factory=challenge_id_factory,
            device_id_factory=device_id_factory,
            secret_code_factory=secret_code_factory,
            digest_key=digest_key,
            display_sink=display_sink,
        )
        return Phase4Subsystem(
            pairing_runtime=pairing_runtime,
            handoff_runtime=handoff_runtime,
            handoff_transport=handoff_transport,
            visual_transfer_runtime=VisualTransferRuntime(
                service=visual_service,
                clock=clock_fn,
            ),
            vision_runtime=VisionRuntime(
                service=vision_service,
                ocr_adapter=ocr_adapter,
                description_analyzer=selected_description_analyzer,
                handoff_accepted=handoff_service.is_accepted_for_session,
            ),
        )
    except Phase4BootstrapError:
        raise
    except Exception:
        raise Phase4BootstrapError() from None


__all__ = (
    "Phase4BootstrapError",
    "Phase4Subsystem",
    "create_phase4_subsystem",
)
