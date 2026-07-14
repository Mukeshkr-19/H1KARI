"""Phase 0 governance and provenance closure contracts."""

from __future__ import annotations

from pathlib import Path
import re

from core.router import PROVIDER_CONFIGS
from core.voice_status import FASTER_WHISPER_REVISION, SPEECHBRAIN_ECAPA_REVISION
from tests.privacy_scan import EXPLICIT_ROOT_FILES


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_public_governance_and_provenance_records_exist():
    required = {
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "GOVERNANCE.md",
        "THIRD_PARTY_NOTICES.md",
        "docs/MODEL_PROVENANCE.md",
        "docs/PROVIDER_PROVENANCE.md",
    }

    assert not {path for path in required if not (REPO_ROOT / path).is_file()}
    assert required & set(EXPLICIT_ROOT_FILES) == {
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "GOVERNANCE.md",
        "THIRD_PARTY_NOTICES.md",
    }


def test_router_has_one_configuration_authority_and_documented_providers():
    assert not (REPO_ROOT / "config" / "providers.yaml").exists()
    record = (REPO_ROOT / "docs" / "PROVIDER_PROVENANCE.md").read_text(
        encoding="utf-8"
    )

    for name in PROVIDER_CONFIGS:
        assert name.casefold() in record.casefold()

    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY" not in env_example


def test_voice_model_revisions_are_exact_and_documented():
    record = (REPO_ROOT / "docs" / "MODEL_PROVENANCE.md").read_text(
        encoding="utf-8"
    )

    for revision in (FASTER_WHISPER_REVISION, SPEECHBRAIN_ECAPA_REVISION):
        assert re.fullmatch(r"[0-9a-f]{40}", revision)
        assert revision in record


def test_dead_wake_word_prototype_is_not_shipped():
    assert not (REPO_ROOT / "services" / "hikari_always_on.py").exists()


def test_project_license_remains_an_explicit_owner_decision():
    assert not (REPO_ROOT / "LICENSE").exists()
    governance = (REPO_ROOT / "GOVERNANCE.md").read_text(encoding="utf-8")
    notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "owner decision" in governance
    assert "No project license has been selected" in notices
