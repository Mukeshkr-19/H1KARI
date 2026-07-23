# Optional Local Vision Provisioning

Phase 4 supports an optional offline image-description engine. H1KARI does not
install or download a model automatically, and the default installation remains
fully usable without one.

## Candidate

- upstream model: `Qwen/Qwen3-VL-4B-Instruct`
- local artifact candidate: `mlx-community/Qwen3-VL-4B-Instruct-4bit`
- runtime candidate: MLX-VLM on compatible Apple Silicon
- confidence: always unavailable unless a future engine supplies a separately
  measured, calibrated value

The artifact is a community conversion. Before activation, a release reviewer
must pin an immutable revision, review its license and files, and create a local
manifest. Model weights and manifests containing local machine paths are not
tracked in this repository.

## Activation boundary

Two ignored local environment values enable discovery during WebSocket server
startup:

```text
HIKARI_VISION_MODEL_DIR=/absolute/reviewed/model-directory
HIKARI_VISION_MANIFEST_PATH=/absolute/reviewed/model-manifest.json
```

Both values are required. Relative paths, symlinks, paths owned by another user,
group-writable paths, world-writable paths, malformed manifests, mutable
revisions, size mismatches, and hash mismatches fail closed. Importing H1KARI,
running CLI help, doctor, text mode, or constructing the unconfigured Phase 4
subsystem performs no model import, process launch, or download.

## Manifest contract

The UTF-8 JSON manifest is bounded to 1 MiB and has this exact structure:

```json
{
  "schema_version": 1,
  "model_id": "Qwen/Qwen3-VL-4B-Instruct",
  "artifact_id": "mlx-community/Qwen3-VL-4B-Instruct-4bit",
  "revision": "40-lowercase-hex-characters",
  "files": [
    {
      "path": "config.json",
      "size_bytes": 123,
      "sha256": "64-lowercase-hex-characters"
    }
  ]
}
```

Every entry must be a relative regular file beneath the model directory. The
manifest must include `config.json`, `tokenizer_config.json`, and at least one
`.safetensors` file. At most 512 files and 20 GiB total declared bytes are
accepted. Every declared size and SHA-256 is checked before activation.

## Runtime boundary

Each description uses a new process created with Python's `spawn` context. The
child process:

- sets offline and telemetry-disable environment flags before optional imports;
- imports Pillow and MLX-VLM only inside the child;
- accepts one PNG or JPEG no larger than 1 MiB;
- rejects multiple frames and more than 16,777,216 decoded pixels;
- applies orientation, converts to RGB, strips metadata, and bounds the longest
  side to 2,048 pixels;
- uses a fixed prompt that treats visible text as untrusted data;
- uses temperature zero and at most 384 new tokens;
- returns one plain-text description with no fabricated confidence;
- is terminated on cancellation or after the 30-second hard deadline.

No temporary image, OCR output, description, model path, worker output, or raw
exception is written to logs or persistent application state. Private Local
never falls back to cloud and has no connection to OmniRoute or 9Router. Cloud
Vision is a separate explicitly acknowledged mode documented in
`docs/LOCAL_ROUTER_GATEWAYS.md`.

## Product behavior

The **Describe Image** control is selectable. If no verified engine is available,
the server returns `capability_unavailable` before camera capture begins. A mobile
device performs explicit capture and bounded transfer; analysis runs on the
paired desktop. The mobile client receives only the validated observation, not
model files or desktop authority.

## Release acceptance

An installer remains out of scope until the pinned artifact passes deterministic
offline tests for useful descriptions, hallucination bounds, OCR limitations,
latency, peak memory, cancellation, timeout, zero network attempts, cleanup, and
prompt-injection resistance on each advertised hardware tier.
