"""Deterministic Python tests for Phase 4 frontend primitives and source security contracts."""

from __future__ import annotations

import pathlib
import pytest

FRONTEND_PHASE4_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "hikari-frontend"
    / "src"
    / "utils"
    / "phase4"
)

FRONTEND_COMPONENTS_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "hikari-frontend"
    / "src"
    / "components"
)

FORBIDDEN_PATTERNS = [
    "WebSocket",
    "fetch(",
    "XMLHttpRequest",
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "IndexedDB",
    "mediaDevices",
    "getUserMedia",
    "createObjectURL",
    "FileReader",
    "HTMLCanvasElement",
    "getContext('2d')",
    "getContext(\"2d\")",
    "setTimeout",
    "setInterval",
    "speechSynthesis",
    "SpeechRecognition",
    "console.log",
]


def test_phase4_files_exist():
    expected_utils = [
        "identifiers.ts",
        "identifiers.test.ts",
        "pairing.ts",
        "pairing.test.ts",
        "handoff.ts",
        "handoff.test.ts",
        "visualTransfer.ts",
        "visualTransfer.test.ts",
    ]
    for filename in expected_utils:
        assert (FRONTEND_PHASE4_DIR / filename).exists(), f"Missing {filename}"

    expected_components = [
        "Phase4PairingPanel.tsx",
        "HandoffOfferPanel.tsx",
        "VisualTransferPanel.tsx",
    ]
    for filename in expected_components:
        assert (FRONTEND_COMPONENTS_DIR / filename).exists(), f"Missing {filename}"


def test_phase4_source_contracts_no_forbidden_apis():
    all_files = list(FRONTEND_PHASE4_DIR.glob("*.ts")) + [
        FRONTEND_COMPONENTS_DIR / "Phase4PairingPanel.tsx",
        FRONTEND_COMPONENTS_DIR / "HandoffOfferPanel.tsx",
        FRONTEND_COMPONENTS_DIR / "VisualTransferPanel.tsx",
    ]

    for filepath in all_files:
        content = filepath.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_PATTERNS:
            assert forbidden not in content, (
                f"Forbidden pattern '{forbidden}' found in {filepath.name}"
            )


def test_phase4_visual_transfer_clears_file_ref_on_terminal():
    vt_source = (FRONTEND_PHASE4_DIR / "visualTransfer.ts").read_text(encoding="utf-8")
    assert "fileRef: null" in vt_source
    assert "TRANSFER_COMPLETE" in vt_source
    assert "CANCEL" in vt_source
    assert "FAIL" in vt_source


def test_phase4_handoff_uses_frozen_preview():
    handoff_source = (FRONTEND_PHASE4_DIR / "handoff.ts").read_text(encoding="utf-8")
    assert "Object.freeze" in handoff_source
    assert "acknowledged" in handoff_source
    assert "deviceId" not in handoff_source


def test_phase4_frontend_rejects_rewriting_and_uses_exact_correlations():
    pairing_source = (FRONTEND_PHASE4_DIR / "pairing.ts").read_text(encoding="utf-8")
    transfer_source = (FRONTEND_PHASE4_DIR / "visualTransfer.ts").read_text(
        encoding="utf-8"
    )
    panel_source = (FRONTEND_COMPONENTS_DIR / "VisualTransferPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert ".slice(" not in pairing_source
    assert ".trim()" not in pairing_source
    assert "action.requestId !== state.requestId" in transfer_source
    assert "isValidOpaqueId(action.transferId)" in transfer_source
    assert "files.length === 1" in panel_source
