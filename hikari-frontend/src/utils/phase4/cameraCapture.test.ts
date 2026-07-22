/**
 * Pure Phase 4 camera capture primitives and reducer unit tests using deterministic fakes.
 */

import assert from "node:assert/strict";
import test from "node:test";
import {
  createInitialCameraCaptureState,
  isCameraCaptureActive,
  isCameraCapturePending,
  reduceCameraCapture,
  stopStreamTracks,
  validateCapturedFrame,
} from "./cameraCapture";

interface FakeTrack {
  readonly stopCount: number;
  readonly stop: () => void;
}

function createFakeTrack(): FakeTrack {
  let stopCount = 0;
  return {
    get stopCount() {
      return stopCount;
    },
    stop() {
      stopCount++;
    },
  };
}

function createFakeStream(trackCount = 1): { tracks: FakeTrack[]; getTracks: () => FakeTrack[] } {
  const tracks = Array.from({ length: trackCount }, () => createFakeTrack());
  return {
    tracks,
    getTracks: () => tracks,
  };
}

function createFakeBlob(sizeBytes: number, type = "image/jpeg"): Blob {
  return {
    size: sizeBytes,
    type,
  } as unknown as Blob;
}

test("createInitialCameraCaptureState returns frozen initial state", () => {
  const init = createInitialCameraCaptureState();
  assert.equal(init.status, "idle");
  assert.equal(init.token, 0);
  assert.equal(init.streamRef, null);
  assert.equal(init.capturedFrame, null);
  assert.equal(init.errorCode, null);
  assert.equal(Object.isFrozen(init), true);
});

test("stopStreamTracks calls stop on every track", () => {
  const stream = createFakeStream(2);
  stopStreamTracks(stream as unknown as MediaStream);
  assert.equal(stream.tracks[0].stopCount, 1);
  assert.equal(stream.tracks[1].stopCount, 1);
});

test("reduceCameraCapture happy path lifecycle", () => {
  let state = createInitialCameraCaptureState();

  // 1. START_REQUESTED
  state = reduceCameraCapture(state, { type: "START_REQUESTED", token: 1 });
  assert.equal(state.status, "requesting");
  assert.equal(state.token, 1);
  assert.equal(isCameraCapturePending(state.status), true);

  // 2. PERMISSION_GRANTED
  const stream = createFakeStream(1);
  state = reduceCameraCapture(state, {
    type: "PERMISSION_GRANTED",
    token: 1,
    stream: stream as unknown as MediaStream,
  });
  assert.equal(state.status, "active");
  assert.equal(isCameraCaptureActive(state.status), true);

  // 3. CAPTURE_REQUESTED
  state = reduceCameraCapture(state, { type: "CAPTURE_REQUESTED" });
  assert.equal(state.status, "capturing");

  // 4. FRAME_CAPTURED
  const blob = createFakeBlob(500000, "image/jpeg");
  state = reduceCameraCapture(state, {
    type: "FRAME_CAPTURED",
    token: 1,
    frame: blob,
  });
  assert.equal(state.status, "captured");
  assert.equal(state.capturedFrame, blob);
  assert.equal(stream.tracks[0].stopCount, 1);

  // 5. STOP_REQUESTED
  state = reduceCameraCapture(state, { type: "STOP_REQUESTED" });
  assert.equal(state.status, "stopped");
});

test("late or stale permission responses stop the late stream and preserve state", () => {
  let state = createInitialCameraCaptureState();
  state = reduceCameraCapture(state, { type: "START_REQUESTED", token: 2 });

  // User resets before permission resolves
  state = reduceCameraCapture(state, { type: "RESET" });
  assert.equal(state.status, "idle");
  assert.equal(state.token, 3);

  // Late stream arrives with old token 2
  const lateStream = createFakeStream(1);
  const next = reduceCameraCapture(state, {
    type: "PERMISSION_GRANTED",
    token: 2,
    stream: lateStream as unknown as MediaStream,
  });
  assert.strictEqual(next, state);
  assert.equal(lateStream.tracks[0].stopCount, 1);
});

test("capture failure maps to a safe error and stops the stream", () => {
  let state = createInitialCameraCaptureState();
  state = reduceCameraCapture(state, { type: "START_REQUESTED", token: 1 });
  const stream = createFakeStream(1);
  state = reduceCameraCapture(state, {
    type: "PERMISSION_GRANTED",
    token: 1,
    stream: stream as unknown as MediaStream,
  });
  state = reduceCameraCapture(state, { type: "CAPTURE_REQUESTED" });

  state = reduceCameraCapture(state, {
    type: "CAPTURE_FAILED",
    token: 1,
    errorCode: "dimensions_exceeded",
  });
  assert.equal(state.status, "failed");
  assert.equal(state.errorCode, "dimensions_exceeded");
  assert.equal(stream.tracks[0].stopCount, 1);
});

test("validateCapturedFrame bounds byte size and mime type", () => {
  const validJpeg = createFakeBlob(100000, "image/jpeg");
  assert.equal(validateCapturedFrame(validJpeg), validJpeg);

  const validPng = createFakeBlob(100000, "image/png");
  assert.equal(validateCapturedFrame(validPng), validPng);

  const overlong = createFakeBlob(1048577, "image/jpeg");
  assert.equal(validateCapturedFrame(overlong), null);

  const invalidType = createFakeBlob(100000, "image/gif");
  assert.equal(validateCapturedFrame(invalidType), null);
});
