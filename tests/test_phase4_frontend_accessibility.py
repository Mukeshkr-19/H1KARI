"""Deterministic Python accessibility tests for Phase 4 frontend components."""

from __future__ import annotations

import pathlib
import pytest

FRONTEND_COMPONENTS_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "hikari-frontend"
    / "src"
    / "components"
)


def test_phase4_pairing_panel_accessibility():
    content = (FRONTEND_COMPONENTS_DIR / "Phase4PairingPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert 'role="status"' in content
    assert 'aria-live="polite"' in content
    assert 'role="alert"' in content
    assert 'tabIndex={-1}' in content
    assert 'htmlFor="pairing-challenge-code-input"' in content
    assert '"pairing-code-hint pairing-device-label-desc"' in content
    assert 'pattern="[0-9A-F]{6,10}"' in content
    assert 'maxLength={10}' in content
    # Privacy check: Confirm entered challenge code variable is NOT placed inside role="status" text
    status_block = content.split('role="status"')[1].split("</div>")[0]
    assert "{code}" not in status_block


def test_phase4_handoff_panel_accessibility():
    content = (FRONTEND_COMPONENTS_DIR / "HandoffOfferPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert 'role="status"' in content
    assert 'aria-live="polite"' in content
    assert 'role="alert"' in content
    assert 'tabIndex={-1}' in content
    assert 'htmlFor="handoff-acknowledge-checkbox"' in content
    assert '"handoff-preview-details"' in content
    # No authority language check
    assert "grant permission" not in content.lower()
    assert "authorize scope" not in content.lower()


def test_phase4_visual_transfer_panel_accessibility():
    content = (FRONTEND_COMPONENTS_DIR / "VisualTransferPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert 'role="status"' in content
    assert 'aria-live="polite"' in content
    assert 'role="alert"' in content
    assert 'tabIndex={-1}' in content
    assert 'htmlFor="visual-transfer-file-input"' in content
    assert 'accept="image/png,image/jpeg"' in content
    # Privacy check: Ensure filename variable is NOT in role="status" text
    status_block = content.split('role="status"')[1].split("</div>")[0]
    assert "{file.name}" not in status_block
    assert "{state.fileRef.name}" not in status_block
