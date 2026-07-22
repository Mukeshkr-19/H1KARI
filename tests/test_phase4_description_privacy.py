"""Deterministic Phase 4 local description-adapter privacy and isolation regression.

Tests prove the local description adapter has no network/provider/cloud
fallback, import and construction perform no model invocation, the adapter
uses injected fakes only, and no raw content leaks through repr/str/errors.
All tests use injected fakes; no real model, OCR, camera, or subprocess is
ever invoked.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional

import pytest

from core.vision.contracts import (
    MAX_CONFIDENCE_MILLI,
    MAX_OBSERVATION_TEXT_LENGTH,
    MIN_CONFIDENCE_MILLI,
    VisionObservation,
    VisionObservationKind,
)
from core.vision.description import (
    BoundedLocalDescriptionRunner,
    DescriptionAdapterError,
    DescriptionAnalyzerResult,
    DescriptionResult,
    DescriptionStatus,
    LocalDescriptionAdapter,
)


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTION_MODULE = ROOT / "core" / "vision" / "description.py"
VISION_DIR = ROOT / "core" / "vision"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


VALID_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-data"
VALID_JPEG = b"\xff\xd8\xff" + b"fake-jpeg-data"


class _FakeRunner:
    """Injected fake description runner — never invokes a real binary."""

    def __init__(
        self,
        result: DescriptionAnalyzerResult | None = None,
        *,
        raise_on_call: bool = False,
    ) -> None:
        self._result = result or DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS, text="A bounded description."
        )
        self._raise = raise_on_call
        self.calls: list[tuple[bytes, str, float]] = []

    def __call__(
        self, image_bytes: bytes, *, mime_type: str, timeout: float
    ) -> DescriptionAnalyzerResult:
        if self._raise:
            raise RuntimeError("secret runner failure")
        self.calls.append((image_bytes, mime_type, timeout))
        return self._result


def _adapter(
    *,
    runner: Optional[object] = None,
    analyzer: Optional[object] = None,
    executable_path: str = "/usr/bin/describe",
) -> LocalDescriptionAdapter:
    return LocalDescriptionAdapter(
        executable_path=executable_path,
        runner=runner,
        analyzer=analyzer,
    )


# ===========================================================================
# Analysis (32-34): local description adapter isolation
# ===========================================================================


def test_local_description_adapter_has_no_network_provider_or_cloud_fallback() -> None:
    """(32) Description module has no network, provider, or cloud imports."""
    tree = ast.parse(DESCRIPTION_MODULE.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    forbidden = {
        "requests", "httpx", "urllib", "socket", "aiohttp",
        "openai", "anthropic", "boto3", "google",
        "cv2", "PIL", "Pillow", "torch", "transformers",
        "webbrowser", "pyautogui", "mss",
    }
    assert imports.isdisjoint(forbidden), f"forbidden imports: {imports & forbidden}"


def test_import_and_construction_perform_no_model_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(33) Importing and constructing the description adapter performs no model invocation."""
    invoked: list[str] = []

    def forbidden_popen(*_args, **_kwargs):
        invoked.append("subprocess.Popen")
        raise AssertionError("construction invoked a subprocess")

    monkeypatch.setattr("core.vision.description.subprocess.Popen", forbidden_popen)

    # Import should not invoke subprocess (module is already imported by the test file)
    import core.vision.description as desc_module
    assert invoked == []

    # Construction with injected runner should not invoke subprocess
    adapter = LocalDescriptionAdapter(
        executable_path="/fixed/describe",
        runner=_FakeRunner(),
    )
    assert invoked == []
    assert repr(adapter) == "LocalDescriptionAdapter()"

    # Construction with analyzer callable should not invoke subprocess
    adapter2 = LocalDescriptionAdapter(
        analyzer=lambda _img, *, mime, timeout: DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS, text="test"
        )
    )
    assert invoked == []
    assert repr(adapter2) == "LocalDescriptionAdapter()"


def test_tests_use_injected_fakes_and_never_run_real_model() -> None:
    """(34) This test suite uses injected fakes — the fake runner records calls but never invokes a binary."""
    fake = _FakeRunner()
    adapter = _adapter(runner=fake)
    result = adapter.analyze(VALID_PNG, mime_type="image/png")
    assert result.status is DescriptionStatus.SUCCESS
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == VALID_PNG
    assert fake.calls[0][1] == "image/png"


# ===========================================================================
# Privacy (35-39): no raw content in repr/str/errors/logs/audit
# ===========================================================================


def test_no_raw_content_in_repr_str_errors_or_status() -> None:
    """(35) DescriptionResult and DescriptionAnalyzerResult reprs are content-free."""
    secret = "private-description-sentinel"
    result = DescriptionResult(
        status=DescriptionStatus.SUCCESS, text=secret, confidence_milli=500
    )
    rep = repr(result)
    assert secret not in rep
    assert str(result) == rep
    assert "success" in rep

    analyzer_result = DescriptionAnalyzerResult(
        status=DescriptionStatus.SUCCESS, text=secret, confidence_milli=500
    )
    analyzer_rep = repr(analyzer_result)
    assert secret not in analyzer_rep
    assert str(analyzer_result) == analyzer_rep


def test_no_provider_model_paths_or_command_output_exposed() -> None:
    """(36) Adapter and runner reprs exclude provider, model, path, and command output."""
    adapter = _adapter()
    rep = repr(adapter)
    assert "describe" not in rep
    assert "tesseract" not in rep
    assert "/usr/bin" not in rep

    runner = BoundedLocalDescriptionRunner("/usr/bin/describe")
    runner_rep = repr(runner)
    assert "describe" not in runner_rep
    assert "/usr/bin" not in runner_rep
    assert runner_rep == "BoundedLocalDescriptionRunner()"


def test_no_storage_filesystem_persistence_telemetry_or_cloud_egress() -> None:
    """(37) Description module has no storage, filesystem persistence, telemetry, or cloud imports."""
    tree = ast.parse(DESCRIPTION_MODULE.read_text(encoding="utf-8"))
    imports: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
    forbidden_imports = {
        "sqlite3", "shelve", "pickle",
        "requests", "httpx", "urllib", "socket", "aiohttp",
        "logging",
        "openai", "anthropic", "boto3", "google",
    }
    assert imports.isdisjoint(forbidden_imports)
    assert "getLogger" not in calls
    assert "open" not in calls  # no file I/O


def test_no_legacy_screenshot_applescript_desktop_awareness_or_mac_control_imports() -> None:
    """(38) Description module does not import screenshot, AppleScript, or mac-control."""
    tree = ast.parse(DESCRIPTION_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {
                "core.desktop_awareness",
                "core.mac_control",
                "core.mac_integration",
            }
            assert node.module != "pyautogui"
            assert node.module != "mss"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"pyautogui", "mss", "PIL", "cv2"}


def test_no_helper_model_attribution_or_tool_metadata() -> None:
    """(39) Description module has no helper attribution, prompt reuse, or tool metadata."""
    source = DESCRIPTION_MODULE.read_text(encoding="utf-8")
    # Strip comments
    stripped = re.sub(r"#.*$", "", source, flags=re.MULTILINE)
    stripped = re.sub(r'""".*?"""', "", stripped, flags=re.DOTALL)
    stripped = re.sub(r"'''.*?'''", "", stripped, flags=re.DOTALL)
    assert "adapted from" not in stripped.lower()
    assert "copied from" not in stripped.lower()
    assert "based on" not in stripped.lower()
    # No logging or audit
    tree = ast.parse(source)
    imports: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
    assert "logging" not in imports
    assert "getLogger" not in calls
    assert "audit" not in imports
    assert "append_audit" not in calls


# ===========================================================================
# Behavioral: input validation, MIME magic, bounds, confidence
# ===========================================================================


def test_png_success_with_injected_runner() -> None:
    runner = _FakeRunner(
        DescriptionAnalyzerResult(status=DescriptionStatus.SUCCESS, text="A scene.")
    )
    adapter = _adapter(runner=runner)
    result = adapter.analyze(VALID_PNG, mime_type="image/png")
    assert result.status is DescriptionStatus.SUCCESS
    assert result.text == "A scene."


def test_jpeg_success_with_injected_runner() -> None:
    runner = _FakeRunner(
        DescriptionAnalyzerResult(status=DescriptionStatus.SUCCESS, text="A photo.")
    )
    adapter = _adapter(runner=runner)
    result = adapter.analyze(VALID_JPEG, mime_type="image/jpeg")
    assert result.status is DescriptionStatus.SUCCESS
    assert result.text == "A photo."


def test_mime_mismatch_png_bytes_with_jpeg_mime() -> None:
    runner = _FakeRunner()
    adapter = _adapter(runner=runner)
    result = adapter.analyze(VALID_PNG, mime_type="image/jpeg")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert runner.calls == []


def test_unsupported_mime_type() -> None:
    runner = _FakeRunner()
    adapter = _adapter(runner=runner)
    result = adapter.analyze(VALID_PNG, mime_type="image/gif")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert runner.calls == []


def test_empty_input_rejected() -> None:
    runner = _FakeRunner()
    adapter = _adapter(runner=runner)
    result = adapter.analyze(b"", mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert runner.calls == []


def test_oversized_input_rejected() -> None:
    runner = _FakeRunner()
    adapter = _adapter(runner=runner)
    payload = VALID_PNG + b"x" * (1_048_577 - len(VALID_PNG))
    result = adapter.analyze(payload, mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert runner.calls == []


def test_invalid_magic_bytes_rejected() -> None:
    runner = _FakeRunner()
    adapter = _adapter(runner=runner)
    result = adapter.analyze(b"NOTANIMAGE", mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert runner.calls == []


def test_runner_failure_returns_unavailable() -> None:
    runner = _FakeRunner(raise_on_call=True)
    adapter = _adapter(runner=runner)
    result = adapter.analyze(VALID_PNG, mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert "secret" not in repr(result)
    assert "failure" not in repr(result)


def test_runner_returns_failed_status() -> None:
    runner = _FakeRunner(DescriptionAnalyzerResult(status=DescriptionStatus.FAILED))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(VALID_PNG, mime_type="image/png")
    assert result.status is DescriptionStatus.FAILED


def test_runner_returns_wrong_type() -> None:
    class _WrongRunner:
        def __call__(self, image_bytes, *, mime_type, timeout):
            return "not a DescriptionAnalyzerResult"

    adapter = _adapter(runner=_WrongRunner())
    result = adapter.analyze(VALID_PNG, mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE


def test_runner_called_exactly_once() -> None:
    runner = _FakeRunner()
    adapter = _adapter(runner=runner)
    adapter.analyze(VALID_PNG, mime_type="image/png")
    assert len(runner.calls) == 1


def test_timeout_passed_to_runner() -> None:
    runner = _FakeRunner()
    adapter = _adapter(runner=runner)
    adapter.analyze(VALID_PNG, mime_type="image/png")
    assert len(runner.calls) == 1
    _, _, timeout = runner.calls[0]
    assert timeout == 30.0


def test_image_bytes_passed_to_runner() -> None:
    runner = _FakeRunner()
    adapter = _adapter(runner=runner)
    adapter.analyze(VALID_PNG, mime_type="image/png")
    assert runner.calls[0][0] == VALID_PNG


# ===========================================================================
# Confidence handling
# ===========================================================================


def test_confidence_preserved_when_provided() -> None:
    runner = _FakeRunner(
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS, text="A scene.", confidence_milli=750
        )
    )
    adapter = _adapter(runner=runner)
    result = adapter.analyze(VALID_PNG, mime_type="image/png")
    assert result.status is DescriptionStatus.SUCCESS
    assert result.confidence_milli == 750


def test_confidence_none_when_not_provided() -> None:
    """No invented confidence — subprocess stdout carries text only."""
    runner = _FakeRunner(
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS, text="A scene.", confidence_milli=None
        )
    )
    adapter = _adapter(runner=runner)
    result = adapter.analyze(VALID_PNG, mime_type="image/png")
    assert result.status is DescriptionStatus.SUCCESS
    assert result.confidence_milli is None


def test_confidence_out_of_range_rejected() -> None:
    runner = _FakeRunner(
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS, text="A scene.", confidence_milli=1001
        )
    )
    adapter = _adapter(runner=runner)
    result = adapter.analyze(VALID_PNG, mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE


def test_confidence_boolean_rejected() -> None:
    """Boolean confidence is rejected at the DescriptionAnalyzerResult boundary."""
    with pytest.raises(ValueError):
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS, text="A scene.", confidence_milli=True  # type: ignore[arg-type]
        )


# ===========================================================================
# Text bounds and control/Cf policies
# ===========================================================================


def test_text_bounds_match_backend_and_protocol() -> None:
    """(26) Description text bounds are 1-2000 code points, matching backend and protocol."""
    from core.vision.contracts import MIN_OBSERVATION_TEXT_LENGTH, MAX_OBSERVATION_TEXT_LENGTH
    assert MIN_OBSERVATION_TEXT_LENGTH == 1
    assert MAX_OBSERVATION_TEXT_LENGTH == 2000

    # DescriptionResult enforces these bounds on success
    DescriptionResult(status=DescriptionStatus.SUCCESS, text="x" * 2000)
    with pytest.raises(ValueError):
        DescriptionResult(status=DescriptionStatus.SUCCESS, text="")
    with pytest.raises(ValueError):
        DescriptionResult(status=DescriptionStatus.SUCCESS, text="x" * 2001)


def test_description_rejects_control_and_cf_characters() -> None:
    """(27) Description text rejects control characters and Unicode Cf."""
    with pytest.raises(ValueError):
        DescriptionResult(status=DescriptionStatus.SUCCESS, text="bad\x00")
    with pytest.raises(ValueError):
        DescriptionResult(status=DescriptionStatus.SUCCESS, text="bad\u200b")
    with pytest.raises(ValueError):
        DescriptionResult(status=DescriptionStatus.SUCCESS, text="bad\x7f")


def test_description_rejects_whitespace_only() -> None:
    with pytest.raises(ValueError):
        DescriptionResult(status=DescriptionStatus.SUCCESS, text="   ")
    with pytest.raises(ValueError):
        DescriptionResult(status=DescriptionStatus.SUCCESS, text="\u2003\u3000")


def test_description_rejects_newline_and_tab() -> None:
    """Description kind text rejects newline and tab (unlike OCR kind)."""
    with pytest.raises(ValueError):
        DescriptionResult(status=DescriptionStatus.SUCCESS, text="line\nbreak")
    with pytest.raises(ValueError):
        DescriptionResult(status=DescriptionStatus.SUCCESS, text="tab\ttext")


def test_non_success_result_must_not_carry_text() -> None:
    with pytest.raises(ValueError):
        DescriptionResult(status=DescriptionStatus.UNAVAILABLE, text="leaked text")


def test_non_success_result_must_not_carry_confidence() -> None:
    with pytest.raises(ValueError):
        DescriptionResult(
            status=DescriptionStatus.UNAVAILABLE, confidence_milli=500
        )


# ===========================================================================
# __call__ contract (DescriptionAnalyzer Protocol)
# ===========================================================================


def test_call_returns_vision_observation_tuple_on_success() -> None:
    runner = _FakeRunner(
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS, text="A quiet desk.", confidence_milli=700
        )
    )
    adapter = _adapter(runner=runner)
    observations = adapter(VALID_PNG, mime_type="image/png")
    assert len(observations) == 1
    obs = observations[0]
    assert isinstance(obs, VisionObservation)
    assert obs.kind is VisionObservationKind.DESCRIPTION
    assert obs.text == "A quiet desk."
    assert obs.confidence_milli == 700


def test_call_returns_empty_tuple_on_failure() -> None:
    runner = _FakeRunner(
        DescriptionAnalyzerResult(status=DescriptionStatus.FAILED)
    )
    adapter = _adapter(runner=runner)
    observations = adapter(VALID_PNG, mime_type="image/png")
    assert observations == ()


def test_call_returns_empty_tuple_on_unavailable() -> None:
    runner = _FakeRunner(raise_on_call=True)
    adapter = _adapter(runner=runner)
    observations = adapter(VALID_PNG, mime_type="image/png")
    assert observations == ()


# ===========================================================================
# Construction validation
# ===========================================================================


def test_relative_executable_path_rejected() -> None:
    with pytest.raises(DescriptionAdapterError):
        LocalDescriptionAdapter(executable_path="describe")
    with pytest.raises(DescriptionAdapterError):
        BoundedLocalDescriptionRunner("describe")


def test_analyzer_and_executable_mutually_exclusive() -> None:
    with pytest.raises(DescriptionAdapterError):
        LocalDescriptionAdapter(
            executable_path="/usr/bin/describe",
            analyzer=lambda _img, *, mime, timeout: DescriptionAnalyzerResult(
                status=DescriptionStatus.SUCCESS, text="test"
            ),
        )


def test_analyzer_and_runner_mutually_exclusive() -> None:
    with pytest.raises(DescriptionAdapterError):
        LocalDescriptionAdapter(
            executable_path="/usr/bin/describe",
            runner=_FakeRunner(),
            analyzer=lambda _img, *, mime, timeout: DescriptionAnalyzerResult(
                status=DescriptionStatus.SUCCESS, text="test"
            ),
        )


def test_no_executable_without_analyzer_rejected() -> None:
    with pytest.raises(DescriptionAdapterError):
        LocalDescriptionAdapter()


def test_non_callable_runner_rejected() -> None:
    with pytest.raises(DescriptionAdapterError):
        LocalDescriptionAdapter(
            executable_path="/usr/bin/describe",
            runner="not callable",  # type: ignore[arg-type]
        )


def test_non_callable_analyzer_rejected() -> None:
    with pytest.raises(DescriptionAdapterError):
        LocalDescriptionAdapter(analyzer="not callable")  # type: ignore[arg-type]


# ===========================================================================
# No forbidden imports (comprehensive)
# ===========================================================================


def test_no_forbidden_imports_in_description_module() -> None:
    """Description module imports only allowed stdlib and core.vision.contracts."""
    tree = ast.parse(DESCRIPTION_MODULE.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    forbidden = {
        "requests", "httpx", "urllib", "socket", "aiohttp",
        "openai", "anthropic", "boto3", "google",
        "cv2", "PIL", "Pillow", "torch", "transformers",
        "pyautogui", "mss", "applescript", "pyobjc", "Foundation",
        "webbrowser", "tempfile",
    }
    assert imports.isdisjoint(forbidden), f"forbidden: {imports & forbidden}"


def test_subprocess_allowed_for_local_runner() -> None:
    """subprocess is allowed for the bounded local runner, like the OCR adapter."""
    tree = ast.parse(DESCRIPTION_MODULE.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert "subprocess" in imports
    assert "threading" in imports


def test_no_tempfile_usage() -> None:
    """No tempfile imports — no temporary file persistence."""
    tree = ast.parse(DESCRIPTION_MODULE.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert "tempfile" not in imports
