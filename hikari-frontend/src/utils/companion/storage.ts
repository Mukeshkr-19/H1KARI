import {
  DEFAULT_COMPANION,
  DEFAULT_PRESENTATION,
  DEFAULT_SPEAK_RESPONSES,
  DEFAULT_SPEECH_RATE,
  STORAGE_KEY,
  type CompanionType,
  type Presentation,
  isCompanionType,
  isPresentation,
} from "./constants";
import { clampSpeechRate } from "./speechOutput";

export type CompanionUiPrefs = {
  companionType: CompanionType;
  presentation: Presentation;
  speakResponses: boolean;
  speechRate: number;
};

function defaultPrefs(): CompanionUiPrefs {
  return {
    companionType: DEFAULT_COMPANION,
    presentation: DEFAULT_PRESENTATION,
    speakResponses: DEFAULT_SPEAK_RESPONSES,
    speechRate: DEFAULT_SPEECH_RATE,
  };
}

export function loadCompanionPrefs(): CompanionUiPrefs {
  if (typeof window === "undefined") {
    return defaultPrefs();
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return defaultPrefs();
    }
    const data = JSON.parse(raw) as {
      companionType?: string;
      presentation?: string;
      speakResponses?: unknown;
      speechRate?: unknown;
    };
    const companionType = data.companionType ?? "";
    const presentation = data.presentation ?? "";
    const speakResponses =
      typeof data.speakResponses === "boolean"
        ? data.speakResponses
        : DEFAULT_SPEAK_RESPONSES;
    const speechRate =
      typeof data.speechRate === "number"
        ? clampSpeechRate(data.speechRate)
        : DEFAULT_SPEECH_RATE;
    return {
      companionType: isCompanionType(companionType)
        ? companionType
        : DEFAULT_COMPANION,
      presentation: isPresentation(presentation)
        ? presentation
        : DEFAULT_PRESENTATION,
      speakResponses,
      speechRate,
    };
  } catch {
    return defaultPrefs();
  }
}

export function saveCompanionPrefs(prefs: CompanionUiPrefs): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      companionType: prefs.companionType,
      presentation: prefs.presentation,
      speakResponses: prefs.speakResponses === true,
      speechRate: clampSpeechRate(prefs.speechRate),
    }),
  );
}
