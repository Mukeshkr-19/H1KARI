"""Deterministic contracts for optional offline MLX vision provisioning."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from core.vision.description import DescriptionStatus, LocalDescriptionAdapter
from core.vision.mlx_worker import (
    LocalVisionConfig,
    SpawnedMlxDescriptionRunner,
    VisionProvisioningError,
    create_optional_mlx_description_adapter_from_environment,
    load_verified_local_vision_config,
    _reverify_worker_config,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "core" / "vision" / "mlx_worker.py"
REVISION = "a" * 40


def _provisioned_tree(tmp_path: Path) -> tuple[Path, Path]:
    model_dir = tmp_path / "model"
    model_dir.mkdir(mode=0o700, parents=True)
    files = {
        "config.json": b"{}",
        "tokenizer_config.json": b"{}",
        "model.safetensors": b"bounded-test-weights",
    }
    entries = []
    for relative, payload in files.items():
        path = model_dir / relative
        path.write_bytes(payload)
        path.chmod(0o600)
        entries.append(
            {
                "path": relative,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": "Qwen/Qwen3-VL-4B-Instruct",
                "artifact_id": "mlx-community/Qwen3-VL-4B-Instruct-4bit",
                "revision": REVISION,
                "files": entries,
            }
        ),
        encoding="utf-8",
    )
    manifest.chmod(0o600)
    return model_dir, manifest


def test_verified_manifest_returns_content_free_config(tmp_path: Path) -> None:
    model_dir, manifest = _provisioned_tree(tmp_path)
    config = load_verified_local_vision_config(model_dir, manifest)

    assert config.model_dir == model_dir
    assert config.revision == REVISION
    assert repr(config) == "LocalVisionConfig()"
    assert str(model_dir) not in repr(config)


def test_manifest_hash_revision_and_path_traversal_fail_closed(tmp_path: Path) -> None:
    model_dir, manifest = _provisioned_tree(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))

    data["revision"] = "main"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(VisionProvisioningError):
        load_verified_local_vision_config(model_dir, manifest)

    model_dir, manifest = _provisioned_tree(tmp_path / "second")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["files"][0]["path"] = "../config.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(VisionProvisioningError):
        load_verified_local_vision_config(model_dir, manifest)

    model_dir, manifest = _provisioned_tree(tmp_path / "third")
    (model_dir / "model.safetensors").write_bytes(b"tampered")
    with pytest.raises(VisionProvisioningError):
        load_verified_local_vision_config(model_dir, manifest)


def test_symlinked_model_file_is_rejected(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    model_dir, manifest = _provisioned_tree(tmp_path)
    weight = model_dir / "model.safetensors"
    external = tmp_path / "external.safetensors"
    external.write_bytes(weight.read_bytes())
    external.chmod(0o600)
    weight.unlink()
    weight.symlink_to(external)

    with pytest.raises(VisionProvisioningError):
        load_verified_local_vision_config(model_dir, manifest)


def test_unlisted_model_file_is_rejected(tmp_path: Path) -> None:
    model_dir, manifest = _provisioned_tree(tmp_path)
    extra = model_dir / "unreviewed.json"
    extra.write_text("{}", encoding="utf-8")
    extra.chmod(0o600)

    with pytest.raises(VisionProvisioningError):
        load_verified_local_vision_config(model_dir, manifest)


def test_worker_reverifies_artifacts_after_bootstrap(tmp_path: Path) -> None:
    model_dir, manifest = _provisioned_tree(tmp_path)
    config = load_verified_local_vision_config(model_dir, manifest)
    assert _reverify_worker_config(config).revision == REVISION

    (model_dir / "model.safetensors").write_bytes(b"changed-after-bootstrap")
    with pytest.raises(VisionProvisioningError):
        _reverify_worker_config(config)


def test_optional_environment_is_disabled_without_complete_verified_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("HIKARI_VISION_MODEL_DIR", raising=False)
    monkeypatch.delenv("HIKARI_VISION_MANIFEST_PATH", raising=False)
    assert create_optional_mlx_description_adapter_from_environment() is None

    monkeypatch.setenv("HIKARI_VISION_MODEL_DIR", str(tmp_path))
    assert create_optional_mlx_description_adapter_from_environment() is None


def test_verified_environment_constructs_adapter_without_loading_model(
    monkeypatch,
    tmp_path: Path,
) -> None:
    model_dir, manifest = _provisioned_tree(tmp_path)
    monkeypatch.setenv("HIKARI_VISION_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("HIKARI_VISION_MANIFEST_PATH", str(manifest))
    before = {name for name in sys.modules if name.startswith(("mlx", "PIL"))}

    adapter = create_optional_mlx_description_adapter_from_environment()

    after = {name for name in sys.modules if name.startswith(("mlx", "PIL"))}
    assert isinstance(adapter, LocalDescriptionAdapter)
    assert before == after


class _ReceiveConnection:
    def __init__(self, message, ready: bool = True) -> None:
        self.message = message
        self.ready = ready

    def poll(self, _timeout: float) -> bool:
        return self.ready

    def recv(self):
        return self.message

    def close(self) -> None:
        return None


class _SendConnection:
    def close(self) -> None:
        return None


class _Process:
    def __init__(self) -> None:
        self.started = False
        self.terminated = False
        self.alive = False

    def start(self) -> None:
        self.started = True
        self.alive = True

    def join(self, timeout: float) -> None:
        self.alive = False

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False


class _Context:
    def __init__(self, message, ready: bool = True) -> None:
        self.receive = _ReceiveConnection(message, ready)
        self.send = _SendConnection()
        self.process = _Process()

    def Pipe(self, *, duplex: bool):
        assert duplex is False
        return self.receive, self.send

    def Process(self, **kwargs):
        assert kwargs["daemon"] is True
        assert callable(kwargs["target"])
        return self.process


def test_runner_accepts_only_structured_success_and_never_fabricates_confidence(
    tmp_path: Path,
) -> None:
    config = LocalVisionConfig(model_dir=tmp_path.resolve(), revision=REVISION)
    context = _Context(("success", "Visible objects on a table"))
    runner = SpawnedMlxDescriptionRunner(config, process_context=context)

    result = runner(b"\x89PNG\r\n\x1a\n", mime_type="image/png", timeout=3.0)

    assert result.status is DescriptionStatus.SUCCESS
    assert result.text == "Visible objects on a table"
    assert result.confidence_milli is None
    assert context.process.started is True
    assert repr(runner) == "SpawnedMlxDescriptionRunner()"


def test_runner_timeout_terminates_worker(tmp_path: Path) -> None:
    config = LocalVisionConfig(model_dir=tmp_path.resolve(), revision=REVISION)
    context = _Context(None, ready=False)
    runner = SpawnedMlxDescriptionRunner(config, process_context=context)

    result = runner(b"\xff\xd8\xff", mime_type="image/jpeg", timeout=3.0)

    assert result.status is DescriptionStatus.UNAVAILABLE
    assert context.process.terminated is True


def test_import_and_construction_have_no_model_or_process_effects(
    tmp_path: Path,
) -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_imports = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "mlx_vlm" not in top_imports
    assert "PIL" not in top_imports
    assert 'get_context("spawn")' in source
    assert 'HF_HUB_OFFLINE"] = "1"' in source
    assert "TRANSFORMERS_OFFLINE" in source
    assert "requests" not in source
    assert "urlopen" not in source
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "download" not in called_names
    assert stat.S_IMODE(tmp_path.stat().st_mode) & 0o022 == 0
