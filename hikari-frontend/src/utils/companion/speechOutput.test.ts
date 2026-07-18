import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { DEFAULT_SPEAK_RESPONSES, STORAGE_KEY } from "./constants";
import { loadCompanionPrefs, saveCompanionPrefs } from "./storage";
import {
  SPEECH_FAILURE_MESSAGE,
  SPEECH_RATE_DEFAULT,
  SPEECH_RATE_MAX,
  SPEECH_RATE_MIN,
  SPEECH_RATE_STEP,
  SPEECH_TEXT_MAX,
  SpeechOutputController,
  boundSpeechText,
  clampSpeechRate,
  parseSpeechControlIntent,
  type SpeechEngine,
  type SpeechUtteranceHandlers,
} from "./speechOutput";

function createMockEngine(): SpeechEngine & {
  lastText: string;
  lastRate: number;
  handlers: SpeechUtteranceHandlers | null;
  cancelCount: number;
} {
  const engine = {
    lastText: "",
    lastRate: 0,
    handlers: null as SpeechUtteranceHandlers | null,
    cancelCount: 0,
    speak(text: string, rate: number, handlers: SpeechUtteranceHandlers) {
      engine.lastText = text;
      engine.lastRate = rate;
      engine.handlers = handlers;
    },
    cancel() {
      engine.cancelCount += 1;
    },
  };
  return engine;
}

function withMockLocalStorage(run: () => void): void {
  const store = new Map<string, string>();
  const previousWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window: unknown }).window = {
    localStorage: {
      getItem(key: string) {
        return store.has(key) ? store.get(key)! : null;
      },
      setItem(key: string, value: string) {
        store.set(key, value);
      },
      removeItem(key: string) {
        store.delete(key);
      },
    },
  };
  try {
    run();
  } finally {
    if (previousWindow === undefined) {
      delete (globalThis as { window?: unknown }).window;
    } else {
      (globalThis as { window: unknown }).window = previousWindow;
    }
  }
}

describe("speechOutput", () => {
  it("defaults spoken output off", () => {
    assert.equal(DEFAULT_SPEAK_RESPONSES, false);
    withMockLocalStorage(() => {
      assert.equal(loadCompanionPrefs().speakResponses, false);
    });
  });

  it("persists preference and rate only", () => {
    withMockLocalStorage(() => {
      saveCompanionPrefs({
        companionType: "cat",
        presentation: "non-binary",
        speakResponses: true,
        speechRate: 0.9,
      });
      const raw = window.localStorage.getItem(STORAGE_KEY);
      assert.ok(raw);
      const parsed = JSON.parse(raw!) as Record<string, unknown>;
      assert.deepEqual(Object.keys(parsed).sort(), [
        "companionType",
        "presentation",
        "speakResponses",
        "speechRate",
      ]);
      assert.equal(parsed.speakResponses, true);
      assert.equal(parsed.speechRate, 0.9);
      assert.equal("text" in parsed, false);
      assert.equal("caption" in parsed, false);
      assert.equal("transcript" in parsed, false);
      assert.equal("path" in parsed, false);
      assert.equal("response" in parsed, false);
    });
  });

  it("defaults spoken rate within documented bounds", () => {
    assert.equal(SPEECH_RATE_DEFAULT, 1.0);
    assert.ok(SPEECH_RATE_MIN < SPEECH_RATE_DEFAULT);
    assert.ok(SPEECH_RATE_MAX > SPEECH_RATE_DEFAULT);
    assert.equal(clampSpeechRate(SPEECH_RATE_MIN - 1), SPEECH_RATE_MIN);
    assert.equal(clampSpeechRate(SPEECH_RATE_MAX + 1), SPEECH_RATE_MAX);
  });

  it("bounds spoken text length", () => {
    const raw = `  ${"a".repeat(SPEECH_TEXT_MAX + 40)}  `;
    assert.equal(boundSpeechText(raw).length, SPEECH_TEXT_MAX);
  });

  it("parses speech-control commands", () => {
    assert.deepEqual(parseSpeechControlIntent("stop speaking"), { type: "stop" });
    assert.deepEqual(parseSpeechControlIntent("repeat response"), { type: "repeat" });
    assert.deepEqual(parseSpeechControlIntent("speak slower"), { type: "slower" });
    assert.deepEqual(parseSpeechControlIntent("what is the weather"), { type: "none" });
  });

  it("speaks through the engine and discards held text on completion", () => {
    const engine = createMockEngine();
    const controller = new SpeechOutputController(engine);
    controller.rememberVoiceResponse("Hello from voice");
    assert.equal(controller.speak("Hello from voice"), true);
    assert.equal(engine.lastText, "Hello from voice");
    assert.equal(controller.getHeldText(), "Hello from voice");
    assert.equal(controller.isSpeaking(), true);
    engine.handlers?.onend();
    assert.equal(controller.getHeldText(), "");
    assert.equal(controller.isSpeaking(), false);
    assert.equal(controller.getLastVoiceResponse(), "Hello from voice");
  });

  it("cancels immediately and discards held text", () => {
    const engine = createMockEngine();
    const controller = new SpeechOutputController(engine);
    controller.speak("Speaking now");
    const cancelsBefore = engine.cancelCount;
    controller.cancel();
    assert.ok(engine.cancelCount > cancelsBefore);
    assert.equal(controller.getHeldText(), "");
    assert.equal(controller.isSpeaking(), false);
  });

  it("cancels prior speech when a new utterance starts", () => {
    const engine = createMockEngine();
    const controller = new SpeechOutputController(engine);
    controller.speak("first");
    controller.speak("second");
    assert.ok(engine.cancelCount >= 1);
    assert.equal(engine.lastText, "second");
  });

  it("ignores stale completion from a cancelled utterance", () => {
    const engine = createMockEngine();
    const controller = new SpeechOutputController(engine);
    controller.speak("first");
    const firstHandlers = engine.handlers;
    controller.speak("second");

    firstHandlers?.onend();

    assert.equal(controller.getHeldText(), "second");
    assert.equal(controller.isSpeaking(), true);
  });

  it("clears controller-held text and last response on dispose", () => {
    const engine = createMockEngine();
    const controller = new SpeechOutputController(engine);
    controller.rememberVoiceResponse("keep for repeat");
    controller.speak("keep for repeat");
    controller.dispose();
    assert.equal(controller.getHeldText(), "");
    assert.equal(controller.getLastVoiceResponse(), "");
    assert.equal(controller.isSpeaking(), false);
  });

  it("repeat uses only the bounded in-memory response", () => {
    const engine = createMockEngine();
    const controller = new SpeechOutputController(engine);
    const long = "r".repeat(SPEECH_TEXT_MAX + 20);
    controller.rememberVoiceResponse(long);
    assert.equal(controller.getLastVoiceResponse().length, SPEECH_TEXT_MAX);
    assert.equal(controller.speak(controller.getLastVoiceResponse()), true);
    assert.equal(engine.lastText.length, SPEECH_TEXT_MAX);
  });

  it("speak slower remains within bounds", () => {
    const controller = new SpeechOutputController(createMockEngine());
    controller.setRate(SPEECH_RATE_MIN);
    assert.equal(controller.slower(), SPEECH_RATE_MIN);
    controller.setRate(SPEECH_RATE_DEFAULT);
    const next = controller.slower();
    assert.equal(next, SPEECH_RATE_DEFAULT - SPEECH_RATE_STEP);
    assert.ok(next >= SPEECH_RATE_MIN);
  });

  it("reports a bounded failure without exception details", () => {
    const failures: string[] = [];
    const controller = new SpeechOutputController(null);
    controller.setCallbacks({
      onFailure: (message) => {
        failures.push(message);
      },
    });
    assert.equal(controller.speak("hello"), false);
    assert.deepEqual(failures, [SPEECH_FAILURE_MESSAGE]);
    assert.equal(failures[0]?.includes("Error"), false);
    assert.equal(failures[0]?.includes("Exception"), false);
  });

  it("surfaces engine errors as the same bounded message", () => {
    const engine = createMockEngine();
    const failures: string[] = [];
    const controller = new SpeechOutputController(engine);
    controller.setCallbacks({
      onFailure: (message) => {
        failures.push(message);
      },
    });
    controller.speak("hello");
    engine.handlers?.onerror();
    assert.deepEqual(failures, [SPEECH_FAILURE_MESSAGE]);
    assert.equal(controller.getHeldText(), "");
  });
});
