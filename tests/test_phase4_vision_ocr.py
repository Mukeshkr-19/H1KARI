"""Deterministic tests for the Phase 4 bounded local OCR adapter.

Covers ``core.vision.ocr`` using injected fake runners only; no live
Tesseract binary is ever invoked.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from core.vision.ocr import (
    BoundedTesseractRunner,
    CommandResult,
    LocalOcrAdapter,
    OcrAdapterError,
    OcrResult,
    OcrStatus,
)


@pytest.fixture
def valid_png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"fake-png-data"


@pytest.fixture
def valid_jpeg() -> bytes:
    return b"\xff\xd8\xff" + b"fake-jpeg-data"


@pytest.fixture
def fake_runner():
    class _FakeRunner:
        def __init__(self, result: CommandResult):
            self.result = result
            self.calls: list[tuple[tuple, dict]] = []

        def __call__(self, argv, *, timeout, stdin):
            self.calls.append((tuple(argv), {"timeout": timeout, "stdin": stdin}))
            return self.result

    return _FakeRunner


def _adapter(runner=None, executable_path: str = "/usr/bin/tesseract") -> LocalOcrAdapter:
    return LocalOcrAdapter(executable_path=executable_path, runner=runner)


def test_relative_executable_path_is_rejected_without_path_search():
    with pytest.raises(OcrAdapterError):
        LocalOcrAdapter(executable_path="tesseract")
    with pytest.raises(OcrAdapterError):
        BoundedTesseractRunner("tesseract")


# --------------------------------------------------------------------------
# Success paths for PNG and JPEG
# --------------------------------------------------------------------------


def test_png_success(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"hello"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is OcrStatus.SUCCESS
    assert result.text == "hello"


def test_jpeg_success(fake_runner, valid_jpeg):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"hello"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_jpeg, mime_type="image/jpeg")
    assert result.status is OcrStatus.SUCCESS
    assert result.text == "hello"


def test_empty_ocr_output_returns_success(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b""))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is OcrStatus.SUCCESS
    assert result.text == ""


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


def test_mime_mismatch_png_bytes_with_jpeg_mime(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"hello"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_png, mime_type="image/jpeg")
    assert result.status is OcrStatus.UNAVAILABLE
    assert runner.calls == []


def test_mime_mismatch_jpeg_bytes_with_png_mime(fake_runner, valid_jpeg):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"hello"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_jpeg, mime_type="image/png")
    assert result.status is OcrStatus.UNAVAILABLE
    assert runner.calls == []


def test_unsupported_mime_type(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"hello"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_png, mime_type="image/gif")
    assert result.status is OcrStatus.UNAVAILABLE
    assert runner.calls == []


def test_empty_input(fake_runner):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"hello"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(b"", mime_type="image/png")
    assert result.status is OcrStatus.UNAVAILABLE
    assert runner.calls == []


def test_input_not_bytes(fake_runner):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"hello"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze("not bytes", mime_type="image/png")  # type: ignore[arg-type]
    assert result.status is OcrStatus.UNAVAILABLE
    assert runner.calls == []


def test_maximum_input_size_accepted(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"hello"))
    adapter = _adapter(runner=runner)
    payload = valid_png + b"x" * (1_048_576 - len(valid_png))
    result = adapter.analyze(payload, mime_type="image/png")
    assert result.status is OcrStatus.SUCCESS


def test_maximum_plus_one_input_size_rejected(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"hello"))
    adapter = _adapter(runner=runner)
    payload = valid_png + b"x" * (1_048_577 - len(valid_png))
    result = adapter.analyze(payload, mime_type="image/png")
    assert result.status is OcrStatus.UNAVAILABLE
    assert runner.calls == []


def test_invalid_magic_bytes(fake_runner):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"hello"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(b"NOTANIMAGE", mime_type="image/png")
    assert result.status is OcrStatus.UNAVAILABLE
    assert runner.calls == []


# --------------------------------------------------------------------------
# Output validation
# --------------------------------------------------------------------------


def test_maximum_output_length_accepted(fake_runner, valid_png):
    text = "a" * 20_000
    runner = fake_runner(CommandResult(returncode=0, stdout=text.encode("utf-8")))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is OcrStatus.SUCCESS
    assert result.text == text


def test_maximum_plus_one_output_length_rejected(fake_runner, valid_png):
    text = "a" * 20_001
    runner = fake_runner(CommandResult(returncode=0, stdout=text.encode("utf-8")))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is OcrStatus.UNAVAILABLE


def test_malformed_utf8_rejected(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"\xff\xfe\x00\x00"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is OcrStatus.UNAVAILABLE


@pytest.mark.parametrize(
    "bad_text",
    [
        "hello\x00world",
        "hello\x7fworld",
        "hello\x01world",
        "safe\u200btext",
        "bad\u202etext",
    ],
)
def test_control_and_cf_characters_rejected(fake_runner, valid_png, bad_text: str):
    runner = fake_runner(CommandResult(returncode=0, stdout=bad_text.encode("utf-8")))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is OcrStatus.UNAVAILABLE


def test_newline_and_tab_allowed(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"line1\nline2\ttap"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is OcrStatus.SUCCESS
    assert result.text == "line1\nline2\ttap"


# --------------------------------------------------------------------------
# Runner failure modes
# --------------------------------------------------------------------------


def test_nonzero_exit_returns_failed(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=1, stdout=b"error details"))
    adapter = _adapter(runner=runner)
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is OcrStatus.FAILED
    assert "error details" not in repr(result)


def test_runner_exception_returns_unavailable(fake_runner, valid_png):
    class _RaisingRunner:
        def __call__(self, argv, *, timeout, stdin):
            raise RuntimeError("secret runner boom")

    adapter = _adapter(runner=_RaisingRunner())
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is OcrStatus.UNAVAILABLE
    assert "secret" not in repr(result)
    assert "boom" not in repr(result)


def test_wrong_runner_result_type(fake_runner, valid_png):
    class _WrongRunner:
        def __call__(self, argv, *, timeout, stdin):
            return "not a CommandResult"

    adapter = _adapter(runner=_WrongRunner())
    result = adapter.analyze(valid_png, mime_type="image/png")
    assert result.status is OcrStatus.UNAVAILABLE


def test_runner_called_exactly_once(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"ok"))
    adapter = _adapter(runner=runner)
    adapter.analyze(valid_png, mime_type="image/png")
    assert len(runner.calls) == 1


def test_exact_argv_passed(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"ok"))
    adapter = _adapter(runner=runner, executable_path="/opt/tesseract")
    adapter.analyze(valid_png, mime_type="image/png")
    assert len(runner.calls) == 1
    argv = runner.calls[0][0]
    assert argv == ("/opt/tesseract", "stdin", "stdout")


def test_image_bytes_passed_via_stdin(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"ok"))
    adapter = _adapter(runner=runner)
    adapter.analyze(valid_png, mime_type="image/png")
    assert len(runner.calls) == 1
    _, kwargs = runner.calls[0]
    assert kwargs["stdin"] is valid_png


def test_timeout_value_passed(fake_runner, valid_png):
    runner = fake_runner(CommandResult(returncode=0, stdout=b"ok"))
    adapter = _adapter(runner=runner)
    adapter.analyze(valid_png, mime_type="image/png")
    assert len(runner.calls) == 1
    _, kwargs = runner.calls[0]
    assert kwargs["timeout"] == 15.0


# --------------------------------------------------------------------------
# Production runner construction and import safety
# --------------------------------------------------------------------------


def test_import_and_construction_perform_no_execution():
    import core.vision.ocr as ocr_module

    assert callable(ocr_module.LocalOcrAdapter)
    assert callable(ocr_module.BoundedTesseractRunner)

    runner = BoundedTesseractRunner("/usr/bin/tesseract")
    assert runner is not None


# --------------------------------------------------------------------------
# Content-free reprs
# --------------------------------------------------------------------------


def test_ocr_result_repr_is_content_free():
    result = OcrResult(status=OcrStatus.SUCCESS, text="secret text")
    rep = repr(result)
    assert "secret" not in rep
    assert "text" not in rep
    assert "success" in rep


def test_command_result_repr_is_content_free():
    result = CommandResult(returncode=0, stdout=b"secret stdout")
    rep = repr(result)
    assert "secret" not in rep
    assert "stdout" not in rep
    assert "returncode=0" in rep


def test_ocr_adapter_error_repr_is_content_free():
    err = OcrAdapterError()
    rep = repr(err)
    assert "OcrAdapterError()" == rep
    assert "secret" not in rep


def test_adapter_repr_is_content_free():
    adapter = _adapter()
    rep = repr(adapter)
    assert "tesseract" not in rep


# --------------------------------------------------------------------------
# No temp files, network, or forbidden side effects
# --------------------------------------------------------------------------


def test_no_forbidden_imports():
    from core.vision import ocr as ocr_module

    forbidden = {
        "requests",
        "httpx",
        "urllib",
        "socket",
        "asyncio",
        "webbrowser",
        "cv2",
        "PIL",
        "Pillow",
        "pytesseract",
        "easyocr",
        "pyautogui",
        "mss",
        "applescript",
        "pyobjc",
        "Foundation",
    }
    path = Path(ocr_module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported), f"forbidden imports: {forbidden & imported}"


def test_subprocess_is_allowed_for_local_runner():
    from core.vision import ocr as ocr_module

    path = Path(ocr_module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert "subprocess" in imported
    assert "threading" in imported


def test_no_tempfile_usage():
    from core.vision import ocr as ocr_module

    path = Path(ocr_module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert "tempfile" not in imported
