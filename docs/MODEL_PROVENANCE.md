# Model Provenance

Status: Phase 0 reviewed runtime-download record

Reviewed: 2026-07-14

H1KARI tracks no model weights. Voice initialization is download-free; model
downloads occur only when the user later starts the selected voice feature.

| Runtime use | Exact source | Reviewed identity | License evidence | Size and data boundary | Phase 0 disposition |
|---|---|---|---|---|---|
| Core transcription | OpenAI Whisper `base` | `openai-whisper==20250625`; expected SHA-256 `ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e` | [code and weights: MIT](https://github.com/openai/whisper) | 74M parameters; downloaded to the user's cache; audio remains local when this backend is selected | approved for opt-in download; do not bundle without repeating artifact verification |
| Daemon transcription | `Systran/faster-whisper-base` | revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`; `faster-whisper==1.2.1` | [model repository: MIT](https://huggingface.co/Systran/faster-whisper-base) | approximately 148 MB; converted from OpenAI Whisper base; downloaded to the user's cache | runtime calls are pinned to the reviewed revision; no H1KARI redistribution |
| Speaker verification | `speechbrain/spkrec-ecapa-voxceleb` | revision `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286` | [model card: Apache-2.0](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb); [VoxCeleb metadata: CC BY-SA 4.0](https://www.robots.ox.ac.uk/~vgg/data/voxceleb/vox1.html) | approximately 89 MB; trained on VoxCeleb 1 and 2; enrollment stores a local embedding, not raw audio | opt-in enrollment only; never sole authorization for a high-risk action; no bundled weights or training data |

## Optional local vision candidate

H1KARI selects `Qwen/Qwen3-VL-4B-Instruct` with the community
`mlx-community/Qwen3-VL-4B-Instruct-4bit` conversion as the candidate local
Apple-Silicon description engine. The upstream model and conversion cards state
Apache-2.0; MLX-VLM is MIT licensed. The conversion remains a separately
reviewed supply-chain artifact rather than an official Qwen build.

No weights, MLX runtime, or model downloader are included in H1KARI. Production
activation requires an explicit absolute model directory and a local manifest
containing an immutable 40-character revision plus the size and SHA-256 of every
file. H1KARI verifies that manifest before constructing the optional adapter.
Inference imports MLX only inside a disposable spawn worker with offline and
telemetry-disable flags. Missing or invalid provisioning leaves description
unavailable without affecting pairing, handoff, OCR, or camera controls.

The candidate must pass the local acceptance benchmark before H1KARI publishes
an installer or claims a hardware tier. See `docs/LOCAL_VISION_PROVISIONING.md`.

## Runtime controls

- `hikari.py --init-plan` and `--init` disclose the selected backend, first-use
  download, and possible Google audio egress without loading a model.
- `hikari.py --voice-status` reports package, cache, reviewed model identity, and
  offline readiness without reading the enrollment file contents.
- Text startup avoids voice-model downloads and audio egress.
- Removing the model cache disables offline readiness; selecting text startup or
  not running enrollment prevents voice-model use.

## Safety limits

Whisper accuracy varies by language and environment and is not a safety-critical
authority. Speaker verification is probabilistic biometric processing: it can
support convenience and guest isolation, but it cannot authorize destructive,
financial, privileged, or safety-critical actions by itself.


## Local Silero VAD asset (dependency-provided, not repository-vendored)

| Runtime use | Exact source | Reviewed identity | License evidence | Size and data boundary | Phase 0 disposition |
|---|---|---|---|---|---|
| Local VAD probabilities | `silero_vad_v6.onnx` from pinned `faster-whisper==1.2.1`, or operator override via `HIKARI_SILERO_VAD_PATH` | Asset is outside Git; installed package metadata reports MIT for faster-whisper | Exact model-source license/digest must still be recorded before redistributing the binary | Local ONNX inference only; no cloud VAD | approved for local dependency use; do not copy into the repository/release bundle without artifact review |

If the dependency asset, explicit model, or ONNX Runtime is unavailable,
explicit CoreAudio mode fails closed. Automatic foreground selection may use the
reviewed utterance-only path. Energy fallback is explicit degraded/test behavior
and is never reported as Silero.

## local-wake speech embedding asset (dependency-provided, not repository-vendored)

| Runtime use | Exact source | Locally observed identity | License evidence | Data boundary | Disposition |
|---|---|---|---|---|---|
| Optional Hikari wake similarity scoring | `lwake/models/speech-embedding.onnx` installed by `local-wake==0.1.2` | 2,435,104 bytes; SHA-256 `0140c2b1455c016783f9b859e16f47b5187c69079503cd053f406b86f9094560` | installed package metadata and package source license report MIT for local-wake code; package README describes a conversion of a pretrained Google speech-embedding model, but a separate authoritative model license, original artifact identity, and conversion recipe were not confirmed in this offline review | HIKARI passes existing 16 kHz PCM frames to local scoring; it does not call local-wake microphone helpers, download a model, persist audio, or ship reference samples | approved only for explicit local use from the installed dependency; do not vendor or redistribute the ONNX asset, and do not claim release readiness, until model provenance and redistribution rights are independently verified |

Private positive and negative calibration WAVs live under the operator's HIKARI
runtime home and are never repository assets. Missing, malformed, insufficient,
or non-separating samples leave the detector silent and inactive.
