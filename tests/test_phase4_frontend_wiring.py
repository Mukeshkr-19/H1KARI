"""Deterministic Python tests for Phase 4 frontend control-plane integration and wiring contracts."""

from __future__ import annotations

import pathlib
import pytest

PAGE_TSX = (
    pathlib.Path(__file__).resolve().parents[1]
    / "hikari-frontend"
    / "src"
    / "app"
    / "page.tsx"
)

PHASE4_UTILS_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "hikari-frontend"
    / "src"
    / "utils"
    / "phase4"
)

PHASE4_COMPONENTS_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "hikari-frontend"
    / "src"
    / "components"
)


def test_page_tsx_has_dedicated_phase4_parser_ordering():
    content = PAGE_TSX.read_text(encoding="utf-8")

    # Verify Phase 4 parser is invoked BEFORE productivity and generic parsers
    p4_pos = content.find("parsePhase4ServerMessage(event.data)")
    prod_pos = content.find("parseProductivityServerMessage(event.data)")
    generic_pos = content.find("parseServerMessage(event.data)")

    assert p4_pos != -1, "parsePhase4ServerMessage call missing in page.tsx"
    assert prod_pos != -1, "parseProductivityServerMessage call missing in page.tsx"
    assert generic_pos != -1, "parseServerMessage call missing in page.tsx"
    assert p4_pos < prod_pos < generic_pos, (
        "Phase 4 server message parser must be invoked before productivity and generic parsers"
    )


def test_strict_dedicated_types_include_all_phase4_server_messages():
    content = PAGE_TSX.read_text(encoding="utf-8")
    expected_types = [
        "pairing_challenge",
        "pairing_confirmed",
        "pairing_update",
        "pairing_error",
        "handoff_offer",
        "handoff_update",
        "handoff_error",
        "visual_transfer_ready",
        "visual_transfer_update",
        "visual_transfer_complete",
        "visual_transfer_error",
    ]
    for msg_type in expected_types:
        assert msg_type in content, f"Missing {msg_type} in page.tsx dedicated message handling"


def test_page_tsx_renders_phase4_panels():
    content = PAGE_TSX.read_text(encoding="utf-8")
    assert "<Phase4PairingPanel" in content, "Phase4PairingPanel component missing from page.tsx JSX"
    assert "<HandoffOfferPanel" in content, "HandoffOfferPanel component missing from page.tsx JSX"
    assert "<VisualTransferPanel" in content, "VisualTransferPanel component missing from page.tsx JSX"


def test_no_forbidden_binary_storage_or_camera_apis_in_phase4():
    forbidden_terms = [
        "FileReader",
        "readAsDataURL",
        "readAsArrayBuffer",
        "createObjectURL",
        "getUserMedia",
        "mediaDevices",
        "HTMLCanvasElement",
        "getContext('2d')",
        "localStorage.setItem('pairing_code'",
        "sessionStorage.setItem('pairing_code'",
    ]

    all_files = list(PHASE4_UTILS_DIR.glob("*.ts")) + list(PHASE4_COMPONENTS_DIR.glob("Phase4*.tsx")) + list(PHASE4_COMPONENTS_DIR.glob("Handoff*.tsx")) + list(PHASE4_COMPONENTS_DIR.glob("Visual*.tsx"))
    for filepath in all_files:
        text = filepath.read_text(encoding="utf-8")
        for term in forbidden_terms:
            assert term not in text, f"Forbidden API or side-channel term '{term}' found in {filepath.name}"


def test_disconnect_clears_phase4_states():
    content = PAGE_TSX.read_text(encoding="utf-8")
    assert "resetPhase4State" in content or "createInitialPairingState" in content
    assert "createInitialHandoffState" in content
    assert "createInitialVisualTransferState" in content
