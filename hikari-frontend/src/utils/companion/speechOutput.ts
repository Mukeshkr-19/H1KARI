/** Browser spoken-output controller. Transcripts and spoken text are untrusted. */

export const SPEECH_TEXT_MAX = 2000;
export const SPEECH_RATE_MIN = 0.7;
export const SPEECH_RATE_MAX = 1.4;
export const SPEECH_RATE_DEFAULT = 1.0;
export const SPEECH_RATE_STEP = 0.1;
export const SPEECH_FAILURE_MESSAGE =
  "Spoken output is unavailable. Continuing with text.";

export type SpeechControlIntent =
  | { type: "stop" }
  | { type: "repeat" }
  | { type: "slower" }
  | { type: "none" };

export type SpeechUtteranceHandlers = {
  onend: () => void;
  onerror: () => void;
};

export type SpeechEngine = {
  speak: (text: string, rate: number, handlers: SpeechUtteranceHandlers) => void;
  cancel: () => void;
};

export type SpeechOutputCallbacks = {
  onFailure?: (message: string) => void;
  onSpeakingChange?: (speaking: boolean) => void;
};

const STOP_PATTERN = /^stop speaking\s*[.!]?\s*$/i;
const REPEAT_PATTERN = /^repeat response\s*[.!]?\s*$/i;
const SLOWER_PATTERN = /^speak slower\s*[.!]?\s*$/i;

export function boundSpeechText(raw: string): string {
  return raw.trim().slice(0, SPEECH_TEXT_MAX);
}

export function clampSpeechRate(rate: number): number {
  if (!Number.isFinite(rate)) {
    return SPEECH_RATE_DEFAULT;
  }
  const stepped = Math.round(rate / SPEECH_RATE_STEP) * SPEECH_RATE_STEP;
  const clamped = Math.min(SPEECH_RATE_MAX, Math.max(SPEECH_RATE_MIN, stepped));
  return Math.round(clamped * 10) / 10;
}

export function parseSpeechControlIntent(transcript: string): SpeechControlIntent {
  const normalized = boundSpeechText(transcript).replace(/\s+/g, " ").trim();
  if (!normalized) {
    return { type: "none" };
  }
  if (STOP_PATTERN.test(normalized)) {
    return { type: "stop" };
  }
  if (REPEAT_PATTERN.test(normalized)) {
    return { type: "repeat" };
  }
  if (SLOWER_PATTERN.test(normalized)) {
    return { type: "slower" };
  }
  return { type: "none" };
}

export function createBrowserSpeechEngine(): SpeechEngine | null {
  if (typeof window === "undefined") {
    return null;
  }
  const synthesis = window.speechSynthesis;
  if (!synthesis || typeof window.SpeechSynthesisUtterance !== "function") {
    return null;
  }
  return {
    speak(text, rate, handlers) {
      const utterance = new window.SpeechSynthesisUtterance(text);
      utterance.rate = rate;
      utterance.onend = () => {
        handlers.onend();
      };
      utterance.onerror = () => {
        handlers.onerror();
      };
      synthesis.speak(utterance);
    },
    cancel() {
      synthesis.cancel();
    },
  };
}

export class SpeechOutputController {
  private rate = SPEECH_RATE_DEFAULT;
  private heldText = "";
  private lastVoiceResponse = "";
  private speaking = false;
  private generation = 0;
  private callbacks: SpeechOutputCallbacks = {};

  constructor(private readonly engine: SpeechEngine | null = null) {}

  setCallbacks(callbacks: SpeechOutputCallbacks): void {
    this.callbacks = callbacks;
  }

  getRate(): number {
    return this.rate;
  }

  setRate(rate: number): number {
    this.rate = clampSpeechRate(rate);
    return this.rate;
  }

  slower(): number {
    return this.setRate(this.rate - SPEECH_RATE_STEP);
  }

  isSpeaking(): boolean {
    return this.speaking;
  }

  getHeldText(): string {
    return this.heldText;
  }

  getLastVoiceResponse(): string {
    return this.lastVoiceResponse;
  }

  rememberVoiceResponse(text: string): void {
    this.lastVoiceResponse = boundSpeechText(text);
  }

  clearLastVoiceResponse(): void {
    this.lastVoiceResponse = "";
  }

  speak(text: string): boolean {
    const bounded = boundSpeechText(text);
    if (!bounded) {
      return false;
    }
    this.cancel();
    if (!this.engine) {
      this.callbacks.onFailure?.(SPEECH_FAILURE_MESSAGE);
      return false;
    }
    this.heldText = bounded;
    this.speaking = true;
    const generation = this.generation;
    this.callbacks.onSpeakingChange?.(true);
    try {
      this.engine.speak(bounded, this.rate, {
        onend: () => {
          if (generation !== this.generation) return;
          this.discardHeldText();
          this.speaking = false;
          this.callbacks.onSpeakingChange?.(false);
        },
        onerror: () => {
          if (generation !== this.generation) return;
          this.discardHeldText();
          this.speaking = false;
          this.callbacks.onSpeakingChange?.(false);
          this.callbacks.onFailure?.(SPEECH_FAILURE_MESSAGE);
        },
      });
      return true;
    } catch {
      this.discardHeldText();
      this.speaking = false;
      this.callbacks.onSpeakingChange?.(false);
      this.callbacks.onFailure?.(SPEECH_FAILURE_MESSAGE);
      return false;
    }
  }

  cancel(): void {
    this.generation += 1;
    try {
      this.engine?.cancel();
    } catch {
      // Ignore engine cancel failures; held text is still discarded.
    }
    const wasSpeaking = this.speaking;
    this.discardHeldText();
    this.speaking = false;
    if (wasSpeaking) {
      this.callbacks.onSpeakingChange?.(false);
    }
  }

  dispose(): void {
    this.cancel();
    this.clearLastVoiceResponse();
  }

  private discardHeldText(): void {
    this.heldText = "";
  }
}
