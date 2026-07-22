"""Deterministic tests for the Phase 4 local image-description adapter.

Uses injected fakes only. No real executable, model, network, or capture is
invoked. Confirms production description remains unavailable until a reviewed
local engine is selected.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

from core.vision.contracts import (
    MAX_OBSERVATION_TEXT_LENGTH,
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


REPO_ROOT = Path(__file__).resolve().parents[1]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


@pytest.fixture
def valid_png() -> bytes:
    return PNG_MAGIC + b"fake-png-data"


@pytest.fixture
def valid_jpeg() -> bytes:
    return JPEG_MAGIC + b"fake-jpeg-data"


class _FakeAnalyzer:
    def __init__(self, result: DescriptionAnalyzerResult | Exception):
        self.result = result
        self.calls: list[tuple[bytes, str, float]] = []

    def __call__(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        timeout: float,
    ) -> DescriptionAnalyzerResult:
        self.calls.append((image_bytes, mime_type, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _adapter(analyzer: _FakeAnalyzer) -> LocalDescriptionAdapter:
    return LocalDescriptionAdapter(analyzer=analyzer)


def test_construction_requires_absolute_path_or_injected_callable():
    with pytest.raises(DescriptionAdapterError):
        LocalDescriptionAdapter()
    with pytest.raises(DescriptionAdapterError):
        LocalDescriptionAdapter(executable_path="relative-bin")
    with pytest.raises(DescriptionAdapterError):
        BoundedLocalDescriptionRunner("relative-bin")
    with pytest.raises(DescriptionAdapterError):
        LocalDescriptionAdapter(
            executable_path="/abs/model",
            analyzer=_FakeAnalyzer(
                DescriptionAnalyzerResult(status=DescriptionStatus.SUCCESS, text="x")
            ),
        )


def test_import_and_construction_perform_no_execution(monkeypatch):
    calls: list[object] = []

    def boom(*_args, **_kwargs):
        calls.append("popen")
        raise AssertionError("subprocess must not run during construction")

    monkeypatch.setattr("core.vision.description.subprocess.Popen", boom)
    adapter = LocalDescriptionAdapter(executable_path="/abs/local-describer")
    runner = BoundedLocalDescriptionRunner("/abs/local-describer")
    assert calls == []
    assert repr(adapter) == "LocalDescriptionAdapter()"
    assert repr(runner) == "BoundedLocalDescriptionRunner()"


def test_no_forbidden_imports_or_capture_behavior():
    path = REPO_ROOT / "core" / "vision" / "description.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden_modules = {
        "socket",
        "http",
        "httpx",
        "urllib",
        "requests",
        "aiohttp",
        "asyncio",
        "webbrowser",
        "tempfile",
        "cv2",
        "PIL",
        "Pillow",
        "pyautogui",
        "mss",
        "applescript",
        "pyobjc",
    }
    assert forbidden_modules.isdisjoint(imported)
    assert "subprocess" in imported
    assert "threading" in imported

    # Behavioral imports from quarantined local-capture surfaces must stay absent.
    from_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            from_imports.add(node.module)
    for module_name in from_imports:
        assert "screenshot" not in module_name
        assert "desktop_awareness" not in module_name
        assert "mac_control" not in module_name
        assert "browser_automation" not in module_name
        assert "providers" not in module_name

    source = path.read_text(encoding="utf-8")
    assert "shell=False" in source
    assert "env={}" in source


def test_source_documents_fail_closed_without_reviewed_engine():
    source = (REPO_ROOT / "core" / "vision" / "description.py").read_text(
        encoding="utf-8"
    )
    assert "reviewed" in source.lower()
    assert "fail-closed" in source.lower() or "fail closed" in source.lower()


def test_module_import_does_not_create_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    if "core.vision.description" in sys.modules:
        del sys.modules["core.vision.description"]
    importlib.import_module("core.vision.description")
    assert set(tmp_path.iterdir()) == before


def test_cloud_provider_needles_absent_from_code_identifiers():
    path = REPO_ROOT / "core" / "vision" / "description.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
        elif isinstance(node, ast.FunctionDef):
            names.add(node.name.lower())
        elif isinstance(node, ast.ClassDef):
            names.add(node.name.lower())
        elif isinstance(node, ast.arg):
            names.add(node.arg.lower())
    forbidden = {
        "openai",
        "anthropic",
        "huggingface",
        "moondream",
        "llava",
        "florence",
        "screenshot",
        "camera",
        "upload",
        "telemetry",
    }
    assert forbidden.isdisjoint(names)

def test_valid_png_with_injected_fake(valid_png):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS,
            text="A quiet desk with a notebook",
            confidence_milli=812,
        )
    )
    adapter = _adapter(analyzer)
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is DescriptionStatus.SUCCESS
    assert result.text == "A quiet desk with a notebook"
    assert result.confidence_milli == 812
    assert len(analyzer.calls) == 1
    assert analyzer.calls[0][0] == valid_png
    assert analyzer.calls[0][1] == "image/png"
    assert analyzer.calls[0][2] == 30.0


def test_valid_jpeg_with_injected_fake(valid_jpeg):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS,
            text="Sunlit window ledge",
            confidence_milli=None,
        )
    )
    adapter = _adapter(analyzer)
    result = adapter.analyze(valid_jpeg, mime_type="image/jpeg")
    assert result.status is DescriptionStatus.SUCCESS
    assert result.text == "Sunlit window ledge"
    assert result.confidence_milli is None


def test_mime_mismatch_does_not_invoke_analyzer(valid_png):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(status=DescriptionStatus.SUCCESS, text="secret")
    )
    adapter = _adapter(analyzer)
    result = adapter.analyze(valid_png, mime_type="image/jpeg")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert analyzer.calls == []
    assert result.text == ""


def test_unsupported_mime(valid_png):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(status=DescriptionStatus.SUCCESS, text="secret")
    )
    adapter = _adapter(analyzer)
    result = adapter.analyze(valid_png, mime_type="image/gif")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert analyzer.calls == []


def test_empty_and_oversized_input(valid_png):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(status=DescriptionStatus.SUCCESS, text="ok")
    )
    adapter = _adapter(analyzer)
    assert adapter.analyze(b"", mime_type="image/png").status is (
        DescriptionStatus.UNAVAILABLE
    )
    oversized = valid_png + b"x" * (1_048_577 - len(valid_png))
    assert adapter.analyze(oversized, mime_type="image/png").status is (
        DescriptionStatus.UNAVAILABLE
    )
    assert analyzer.calls == []


def test_maximum_input_accepted(valid_png):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(status=DescriptionStatus.SUCCESS, text="ok")
    )
    adapter = _adapter(analyzer)
    payload = valid_png + b"x" * (1_048_576 - len(valid_png))
    result = adapter.analyze(payload, mime_type="image/png")
    assert result.status is DescriptionStatus.SUCCESS


def test_timeout_and_failure_mapping(valid_png):
    failed = _adapter(
        _FakeAnalyzer(DescriptionAnalyzerResult(status=DescriptionStatus.FAILED))
    )
    assert failed.analyze(valid_png, mime_type="image/png").status is (
        DescriptionStatus.FAILED
    )

    unavailable = _adapter(
        _FakeAnalyzer(DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE))
    )
    assert unavailable.analyze(valid_png, mime_type="image/png").status is (
        DescriptionStatus.UNAVAILABLE
    )


def test_output_exact_lower_and_upper_bounds(valid_png):
    lower = _adapter(
        _FakeAnalyzer(
            DescriptionAnalyzerResult(status=DescriptionStatus.SUCCESS, text="a")
        )
    )
    assert lower.analyze(valid_png, mime_type="image/png").text == "a"

    upper_text = "b" * MAX_OBSERVATION_TEXT_LENGTH
    upper = _adapter(
        _FakeAnalyzer(
            DescriptionAnalyzerResult(
                status=DescriptionStatus.SUCCESS, text=upper_text
            )
        )
    )
    result = upper.analyze(valid_png, mime_type="image/png")
    assert result.status is DescriptionStatus.SUCCESS
    assert result.text == upper_text


def test_max_plus_one_output_rejected(valid_png):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS,
            text="c" * (MAX_OBSERVATION_TEXT_LENGTH + 1),
        )
    )
    result = _adapter(analyzer).analyze(valid_png, mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert result.text == ""


@pytest.mark.parametrize(
    "bad_text",
    [
        "hello\x00world",
        "hello\x7fworld",
        "hello\x01world",
        "safe\u200btext",
        "bad\u202etext",
        "has\nnewline",
        "has\ttab",
        "   ",
        "\u2003\u3000",
        "",
    ],
)
def test_control_cf_newline_tab_blank_rejected(valid_png, bad_text: str):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(status=DescriptionStatus.SUCCESS, text=bad_text)
    )
    result = _adapter(analyzer).analyze(valid_png, mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert result.text == ""


def test_unicode_code_point_bounds(valid_png):
    text = "日本語の机" + ("あ" * (MAX_OBSERVATION_TEXT_LENGTH - 5))
    assert len(text) == MAX_OBSERVATION_TEXT_LENGTH
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(status=DescriptionStatus.SUCCESS, text=text)
    )
    result = _adapter(analyzer).analyze(valid_png, mime_type="image/png")
    assert result.status is DescriptionStatus.SUCCESS
    assert result.text == text


def test_measured_confidence_and_unavailable_confidence(valid_png):
    measured = _adapter(
        _FakeAnalyzer(
            DescriptionAnalyzerResult(
                status=DescriptionStatus.SUCCESS,
                text="Measured scene",
                confidence_milli=640,
            )
        )
    ).analyze(valid_png, mime_type="image/png")
    assert measured.confidence_milli == 640

    unmeasured = _adapter(
        _FakeAnalyzer(
            DescriptionAnalyzerResult(
                status=DescriptionStatus.SUCCESS,
                text="Unscored scene",
                confidence_milli=None,
            )
        )
    ).analyze(valid_png, mime_type="image/png")
    assert unmeasured.confidence_milli is None


def test_no_fabricated_confidence_on_subprocess_style_success(valid_png):
    # Executable path mode with injected runner that returns text only.
    class _Runner:
        def __call__(self, image_bytes, *, mime_type, timeout):
            return DescriptionAnalyzerResult(
                status=DescriptionStatus.SUCCESS,
                text="Local scene",
                confidence_milli=None,
            )

    adapter = LocalDescriptionAdapter(
        executable_path="/abs/local-describer",
        runner=_Runner(),
    )
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is DescriptionStatus.SUCCESS
    assert result.confidence_milli is None


def test_invalid_confidence_rejected(valid_png):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS,
            text="Scene",
            confidence_milli=1001,
        )
    )
    result = _adapter(analyzer).analyze(valid_png, mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert result.confidence_milli is None


def test_wrong_analyzer_return_type(valid_png):
    class _Bad:
        def __call__(self, image_bytes, *, mime_type, timeout):
            return {"text": "leaked"}

    adapter = LocalDescriptionAdapter(analyzer=_Bad())  # type: ignore[arg-type]
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert "leaked" not in repr(result)


def test_analyzer_exception_secret_not_leaked(valid_png):
    secret = "PRIVATE_MODEL_PATH_/private/secret/model.bin"
    analyzer = _FakeAnalyzer(RuntimeError(secret))
    adapter = _adapter(analyzer)
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is DescriptionStatus.UNAVAILABLE
    assert secret not in repr(result)
    assert secret not in str(result)
    assert result.text == ""
    observations = adapter(valid_png, mime_type="image/png")
    assert observations == ()
    assert secret not in repr(observations)


def test_repr_str_privacy(valid_png):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS,
            text="Secret private description",
            confidence_milli=900,
        )
    )
    adapter = _adapter(analyzer)
    result = adapter.analyze(valid_png, mime_type="image/png")
    for value in (repr(result), str(result), repr(adapter), str(adapter)):
        assert "Secret" not in value
        assert "private" not in value
        assert "900" not in value
        assert "/abs" not in value
    assert repr(result) == "DescriptionResult(status='success')"
    assert repr(adapter) == "LocalDescriptionAdapter()"


def test_description_analyzer_contract_compatible_with_vision_observation(valid_png):
    analyzer = _FakeAnalyzer(
        DescriptionAnalyzerResult(
            status=DescriptionStatus.SUCCESS,
            text="A wooden chair beside a lamp",
            confidence_milli=777,
        )
    )
    adapter = _adapter(analyzer)
    observations = adapter(valid_png, mime_type="image/png")
    assert len(observations) == 1
    observation = observations[0]
    assert isinstance(observation, VisionObservation)
    assert observation.kind is VisionObservationKind.DESCRIPTION
    assert observation.text == "A wooden chair beside a lamp"
    assert observation.confidence_milli == 777


def test_description_analyzer_contract_empty_on_failure(valid_png):
    adapter = _adapter(
        _FakeAnalyzer(DescriptionAnalyzerResult(status=DescriptionStatus.FAILED))
    )
    assert adapter(valid_png, mime_type="image/png") == ()
