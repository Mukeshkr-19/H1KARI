export const COMPANION_TYPES = ["cat", "dog", "bird"] as const;
export type CompanionType = (typeof COMPANION_TYPES)[number];

export const PRESENTATIONS = ["male", "female", "non-binary"] as const;
export type Presentation = (typeof PRESENTATIONS)[number];

export const COMPANION_STATES = [
  "hidden",
  "idle",
  "listening",
  "thinking",
  "speaking",
  "error",
] as const;
export type CompanionState = (typeof COMPANION_STATES)[number];

export const STORAGE_KEY = "hikari.companion.ui";

export const DEFAULT_COMPANION: CompanionType = "cat";
export const DEFAULT_PRESENTATION: Presentation = "non-binary";
export const DEFAULT_SPEAK_RESPONSES = false;

export {
  SPEECH_RATE_DEFAULT as DEFAULT_SPEECH_RATE,
  SPEECH_RATE_MAX,
  SPEECH_RATE_MIN,
  SPEECH_RATE_STEP,
} from "./speechOutput";

export function isCompanionType(value: string): value is CompanionType {
  return (COMPANION_TYPES as readonly string[]).includes(value);
}

export function isPresentation(value: string): value is Presentation {
  return (PRESENTATIONS as readonly string[]).includes(value);
}
