"""Static guards for third-party source attribution."""

import hashlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTED_PATHS = (
    "core/task_planner.py",
    "core/action_system.py",
    "core/desktop_awareness.py",
    "core/mac_integration.py",
)


def test_jarvis_adaptations_retain_complete_notice_and_release_boundary():
    notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    inventory = (REPO_ROOT / "docs/PROVENANCE_INVENTORY.md").read_text(
        encoding="utf-8"
    )

    for path in ADAPTED_PATHS:
        assert path in notices
        assert path in inventory

    required_notice_text = (
        "JARVIS Voice AI Assistant",
        "Copyright (c) 2026 Ethan Rogers",
        'of this software and associated documentation files (the "Software"),',
        "copy, modify, and run the Software for personal, non-commercial purposes,",
        "1. PERSONAL USE:",
        "educational, and non-commercial purposes without restriction.",
        "2. COMMERCIAL USE PROHIBITED WITHOUT LICENSE:",
        "- Offering the Software as a hosted service (SaaS)",
        "3. ATTRIBUTION:",
        "include this license notice and the above copyright notice.",
        "4. COMMERCIAL LICENSING:",
        "partnership opportunities, visit: https://ethanplus.ai",
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,',
        "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.",
        "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE",
        "SOFTWARE.",
    )
    for text in required_notice_text:
        assert text in notices

    license_text = notices.split("```text\n", 1)[1].split("```", 1)[0]
    assert hashlib.sha256(license_text.encode("utf-8")).hexdigest() == (
        "f7678c8264db789d26a2c677d81920f7a220e94d59bde0845566925f0fbdf496"
    )

    assert "https://github.com/ethanplusai/jarvis" in notices
    assert "commercial H1KARI release containing them is blocked" in notices
    assert "clean-room" in notices
    assert "commercial release is blocked" in inventory
