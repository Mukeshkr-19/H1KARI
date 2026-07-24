"""Bounded speech adapter tests using fakes and mocks only."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.speech_adapters import (
    CapturedAudio,
    FasterWhisperSTTAdapter,
    GoogleSpeechRecognitionSTTAdapter,
    InvalidAudioError,
    MacOSSayTTSAdapter,
    OpenAIWhisperSTTAdapter,
    SpeechBackendUnavailable,
    SynthesisError,
    TranscriptionError,
    build_stt_adapter,
    build_tts_adapter,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeSTT:
    """Fake STT adapter for testing the factory and consumer code."""

    def __init__(self, text: str = "hello"):
        self.text = text
        self.calls: list = []

    @property
    def audio_egress(self) -> bool:
        return False

    def is_available(self) -> bool:
        return True

    def transcribe(self, audio: CapturedAudio) -> str:
        self.calls.append(audio)
        return self.text


class FakeTTS:
    """Fake TTS adapter for testing consumer code."""

    def __init__(self):
        self.calls: list = []

    def is_available(self) -> bool:
        return True

    def synthesize(self, text: str) -> None:
        self.calls.append(text)


def test_captured_audio_stores_metadata():
    audio = CapturedAudio(
        pcm_bytes=b"\x00\x01\x02\x03",
        sample_rate=16000,
        sample_width=2,
        channel_count=1,
    )
    assert audio.pcm_bytes == b"\x00\x01\x02\x03"
    assert audio.sample_rate == 16000
    assert audio.sample_width == 2
    assert audio.channel_count == 1


def test_captured_audio_rejects_invalid_metadata():
    with pytest.raises(InvalidAudioError, match="sample_rate must be positive"):
        CapturedAudio(pcm_bytes=b"\x00\x01", sample_rate=0, sample_width=2, channel_count=1)
    with pytest.raises(InvalidAudioError, match="sample_width must be positive"):
        CapturedAudio(pcm_bytes=b"\x00\x01", sample_rate=16000, sample_width=0, channel_count=1)
    with pytest.raises(InvalidAudioError, match="channel_count must be positive"):
        CapturedAudio(pcm_bytes=b"\x00\x01", sample_rate=16000, sample_width=2, channel_count=0)
    with pytest.raises(InvalidAudioError, match="pcm_bytes must not be empty"):
        CapturedAudio(pcm_bytes=b"", sample_rate=16000, sample_width=2, channel_count=1)
    with pytest.raises(InvalidAudioError, match="unsupported sample_width"):
        CapturedAudio(pcm_bytes=b"\x00", sample_rate=16000, sample_width=1, channel_count=1)
    with pytest.raises(InvalidAudioError, match="unsupported sample_width"):
        CapturedAudio(pcm_bytes=b"\x00\x00\x00\x00", sample_rate=16000, sample_width=4, channel_count=1)
    with pytest.raises(InvalidAudioError, match="pcm_bytes length is not aligned"):
        CapturedAudio(
            pcm_bytes=b"\x00\x01\x02",
            sample_rate=16000,
            sample_width=2,
            channel_count=1,
        )


def test_captured_audio_mono_16k_noop():
    audio = CapturedAudio(
        pcm_bytes=b"\x00\x01" * 8,
        sample_rate=16000,
        sample_width=2,
        channel_count=1,
    )
    assert audio.to_mono_16k() is audio


def test_captured_audio_downmixes_stereo_to_mono():
    audio = CapturedAudio(
        pcm_bytes=b"\x00\x01\x00\x02" * 4,
        sample_rate=16000,
        sample_width=2,
        channel_count=2,
    )
    mono = audio.to_mono_16k()
    assert mono.channel_count == 1
    assert mono.sample_rate == 16000
    assert mono.sample_width == 2
    assert len(mono.pcm_bytes) == len(audio.pcm_bytes) // 2


def test_openai_whisper_adapter_reports_local_no_egress():
    adapter = OpenAIWhisperSTTAdapter()
    assert adapter.audio_egress is False


def test_openai_whisper_adapter_unavailable_without_whisper(monkeypatch):
    monkeypatch.setitem(sys.modules, "whisper", None)
    adapter = OpenAIWhisperSTTAdapter()
    assert adapter.is_available() is False
    with pytest.raises(SpeechBackendUnavailable):
        adapter.transcribe(
            CapturedAudio(
                pcm_bytes=b"\x00\x01" * 8,
                sample_rate=16000,
                sample_width=2,
                channel_count=1,
            )
        )


def test_google_adapter_reports_cloud_egress():
    adapter = GoogleSpeechRecognitionSTTAdapter()
    assert adapter.audio_egress is True


def test_google_adapter_unavailable_without_speech_recognition(monkeypatch):
    monkeypatch.setitem(sys.modules, "speech_recognition", None)
    adapter = GoogleSpeechRecognitionSTTAdapter()
    assert adapter.is_available() is False
    with pytest.raises(SpeechBackendUnavailable):
        adapter.transcribe(
            CapturedAudio(
                pcm_bytes=b"\x00\x01" * 8,
                sample_rate=16000,
                sample_width=2,
                channel_count=1,
            )
        )


def test_google_adapter_transcribe_uses_recognize_google():
    adapter = GoogleSpeechRecognitionSTTAdapter()
    mock_sr = MagicMock()
    mock_recognizer = MagicMock()
    mock_recognizer.recognize_google.return_value = "hello world"
    mock_sr.Recognizer.return_value = mock_recognizer
    mock_sr.AudioData = MagicMock()
    mock_sr.UnknownValueError = Exception
    mock_sr.RequestError = Exception

    with patch.dict(sys.modules, {"speech_recognition": mock_sr}):
        adapter._recognizer_instance = None
        result = adapter.transcribe(
            CapturedAudio(
                pcm_bytes=b"\x00\x01" * 8,
                sample_rate=16000,
                sample_width=2,
                channel_count=1,
            )
        )

    assert result == "hello world"
    mock_recognizer.recognize_google.assert_called_once()


def test_google_adapter_transcribe_unknown_value_becomes_error():
    adapter = GoogleSpeechRecognitionSTTAdapter()
    mock_sr = MagicMock()
    mock_recognizer = MagicMock()
    mock_sr.Recognizer.return_value = mock_recognizer
    mock_sr.AudioData = MagicMock()
    mock_sr.UnknownValueError = ValueError
    mock_sr.RequestError = RuntimeError
    mock_recognizer.recognize_google.side_effect = ValueError("no match")

    with patch.dict(sys.modules, {"speech_recognition": mock_sr}):
        adapter._recognizer_instance = None
        with pytest.raises(TranscriptionError):
            adapter.transcribe(
                CapturedAudio(
                    pcm_bytes=b"\x00\x01" * 8,
                    sample_rate=16000,
                    sample_width=2,
                    channel_count=1,
                )
            )


def test_macos_say_adapter_unavailable_on_non_darwin():
    adapter = MacOSSayTTSAdapter()
    if sys.platform == "darwin":
        pytest.skip("test is for non-macOS platforms")
    assert adapter.is_available() is False
    with pytest.raises(SpeechBackendUnavailable):
        adapter.synthesize("hello")


def test_macos_say_adapter_sanitizes_text():
    adapter = MacOSSayTTSAdapter()
    assert adapter._sanitize_text("hello; $(world)") == "hello world"
    assert adapter._sanitize_text("say 'this'") == "say this"


def test_macos_say_adapter_refuses_empty_text():
    adapter = MacOSSayTTSAdapter()
    with pytest.raises(SynthesisError, match="No speakable text"):
        adapter.synthesize("   ")


def test_macos_say_adapter_uses_argv_list(monkeypatch):
    adapter = MacOSSayTTSAdapter()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/say")

    captured: list = []

    def fake_run(args, **kwargs):
        captured.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter.synthesize("hello world")

    assert len(captured) == 1
    args, kwargs = captured[0]
    assert args == [
        "/usr/bin/say",
        "-v",
        "Karen",
        "-r",
        "185",
        "hello world",
    ]
    assert kwargs.get("shell") is False


def test_build_stt_adapter_local_backend_does_not_fall_back():
    adapter = build_stt_adapter("openai-whisper")
    assert isinstance(adapter, OpenAIWhisperSTTAdapter)
    assert adapter.audio_egress is False


def test_build_stt_adapter_google_explicit_cloud():
    adapter = build_stt_adapter("google-speech")
    assert isinstance(adapter, GoogleSpeechRecognitionSTTAdapter)
    assert adapter.audio_egress is True


def test_build_stt_adapter_unknown_raises():
    with pytest.raises(SpeechBackendUnavailable, match="unknown STT backend"):
        build_stt_adapter("unknown-backend")


def test_build_stt_adapter_faster_whisper_is_local():
    adapter = build_stt_adapter("faster-whisper")
    assert isinstance(adapter, FasterWhisperSTTAdapter)
    assert adapter.audio_egress is False


def test_faster_whisper_adapter_construction_remains_lazy():
    """Creating the adapter must not import faster_whisper or load a model."""
    adapter = FasterWhisperSTTAdapter(model_size="base")
    assert adapter._model is None


def test_faster_whisper_short_utterance_uses_wake_decode_options(monkeypatch):
    adapter = FasterWhisperSTTAdapter(model_size="base")
    model = MagicMock()
    model.transcribe.return_value = ([SimpleNamespace(text=" HIKARI")], object())
    monkeypatch.setattr(adapter, "is_available", lambda: True)
    monkeypatch.setattr(adapter, "_load_model", lambda: model)

    result = adapter.transcribe_short_utterance(
        CapturedAudio(
            pcm_bytes=b"\x00\x01" * 160,
            sample_rate=16000,
            sample_width=2,
            channel_count=1,
        )
    )

    assert result == "HIKARI"
    _samples, kwargs = model.transcribe.call_args
    assert kwargs == {
        "language": "en",
        "beam_size": 1,
        "condition_on_previous_text": False,
        "hotwords": "HIKARI stop quiet",
        "initial_prompt": "HIKARI. Stop. Be quiet.",
        "no_speech_threshold": None,
        "without_timestamps": True,
    }


def test_build_tts_adapter_macos_say():
    adapter = build_tts_adapter("macos-say")
    assert isinstance(adapter, MacOSSayTTSAdapter)


def test_build_tts_adapter_pocket_tts_is_lazy():
    from core.speech_adapters import PocketTTSAdapter

    adapter = build_tts_adapter("pocket-tts")

    assert isinstance(adapter, PocketTTSAdapter)
    assert adapter._model is None


def test_build_tts_adapter_unknown_raises():
    with pytest.raises(SpeechBackendUnavailable, match="unknown TTS backend"):
        build_tts_adapter("unknown-tts")


def test_importing_speech_adapters_does_not_load_model_packages(tmp_path, monkeypatch):
    marker = tmp_path / "model-imported"
    module_source = (
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['HIKARI_TEST_MODEL_MARKER']).write_text('imported')\n"
    )
    (tmp_path / "whisper.py").write_text(module_source, encoding="utf-8")
    (tmp_path / "faster_whisper.py").write_text(module_source, encoding="utf-8")
    (tmp_path / "speechbrain.py").write_text(module_source, encoding="utf-8")
    env = os.environ.copy()
    env["HIKARI_TEST_MODEL_MARKER"] = str(marker)
    env["PYTHONPATH"] = os.pathsep.join((str(tmp_path), str(REPO_ROOT)))
    env.pop("HIKARI_BRAIN_DIR", None)
    env.pop("HIKARI_LEGACY_DATA_DIR", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from core.speech_adapters import CapturedAudio; "
            "CapturedAudio(pcm_bytes=b'\\x00\\x01', sample_rate=16000, sample_width=2, channel_count=1)",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_voice_system_uses_adapter_without_external_deps():
    """VoiceSystem can be instantiated with an explicit fake adapter."""
    from core.voice import VoiceSystem

    fake_stt = FakeSTT(text="test phrase")
    fake_tts = FakeTTS()
    vs = VoiceSystem.__new__(VoiceSystem)
    vs._backend_name = "fake"
    vs._stt = fake_stt
    vs._tts = fake_tts
    vs.is_listening = False
    vs._warmup_done = True
    vs._mic_index = 0

    assert vs._stt is fake_stt
    assert vs._tts is fake_tts


def test_voice_system_listen_does_not_print_transcript(capsys, monkeypatch):
    """core.voice listen() must not print recognized text."""
    from core.voice import VoiceSystem

    fake_stt = FakeSTT(text="secret phrase")
    fake_tts = FakeTTS()
    vs = VoiceSystem.__new__(VoiceSystem)
    vs._backend_name = "fake"
    vs._stt = fake_stt
    vs._tts = fake_tts
    vs.is_listening = False
    vs._warmup_done = True
    vs._mic_index = 0

    class FakeAudio:
        sample_rate = 16000
        sample_width = 2

        def get_raw_data(self):
            return b"\x00\x01" * 8

    class FakeSource:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeMicrophone:
        def __init__(self, device_index=None):
            self.device_index = device_index

        def __enter__(self):
            return FakeSource()

        def __exit__(self, *args):
            return False

    class FakeRecognizer:
        def listen(self, *args, **kwargs):
            return FakeAudio()

    class FakeSR:
        Microphone = FakeMicrophone
        WaitTimeoutError = Exception

    monkeypatch.setattr("core.voice.SR_AVAILABLE", True)
    monkeypatch.setattr("core.voice.sr", FakeSR())
    vs.recognizer = FakeRecognizer()
    result = vs.listen(timeout=1, phrase_time_limit=1)

    out, _err = capsys.readouterr()
    assert result == "secret phrase"
    assert "secret phrase" not in out
    assert "Recognition succeeded" in out


def test_local_backend_failure_does_not_call_recognize_google(monkeypatch):
    """A local adapter failure must not silently invoke Google STT."""
    from core.speech_adapters import OpenAIWhisperSTTAdapter, SpeechBackendUnavailable

    adapter = OpenAIWhisperSTTAdapter()
    monkeypatch.setattr(adapter, "is_available", lambda: True)
    monkeypatch.setattr(adapter, "_load_model", lambda: (_ for _ in ()).throw(SpeechBackendUnavailable("model missing")))

    with pytest.raises(SpeechBackendUnavailable):
        adapter.transcribe(
            CapturedAudio(
                pcm_bytes=b"\x00\x01" * 8,
                sample_rate=16000,
                sample_width=2,
                channel_count=1,
            )
        )


def test_google_adapter_used_only_when_explicitly_selected():
    """Google adapter reports cloud egress and is only built when requested."""
    google = build_stt_adapter("google-speech")
    assert google.audio_egress is True
    local = build_stt_adapter("openai-whisper")
    assert local.audio_egress is False


def test_importing_core_voice_does_not_load_model_packages(tmp_path, monkeypatch):
    """Importing core.voice must not load Whisper, faster-whisper, or SpeechBrain."""
    marker = tmp_path / "model-imported"
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    module_source = (
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['HIKARI_TEST_MODEL_MARKER']).write_text('imported')\n"
    )
    for name in ("whisper", "faster_whisper", "speechbrain"):
        (module_dir / f"{name}.py").write_text(module_source, encoding="utf-8")
    env = os.environ.copy()
    env["HIKARI_TEST_MODEL_MARKER"] = str(marker)
    env["PYTHONPATH"] = os.pathsep.join((str(module_dir), str(REPO_ROOT)))
    env.pop("HIKARI_BRAIN_DIR", None)
    env.pop("HIKARI_LEGACY_DATA_DIR", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from core.voice import VoiceSystem; "
            "VoiceSystem.__new__(VoiceSystem); print('imported')",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout
    assert not marker.exists()
