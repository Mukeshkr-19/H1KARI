import {
  DEFAULT_COMPANION,
  DEFAULT_PRESENTATION,
  STORAGE_KEY,
  type CompanionType,
  type Presentation,
  isCompanionType,
  isPresentation,
} from "./constants";

export type CompanionUiPrefs = {
  companionType: CompanionType;
  presentation: Presentation;
};

export function loadCompanionPrefs(): CompanionUiPrefs {
  if (typeof window === "undefined") {
    return { companionType: DEFAULT_COMPANION, presentation: DEFAULT_PRESENTATION };
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return { companionType: DEFAULT_COMPANION, presentation: DEFAULT_PRESENTATION };
    }
    const data = JSON.parse(raw) as { companionType?: string; presentation?: string };
    return {
      companionType: isCompanionType(data.companionType ?? "")
        ? data.companionType
        : DEFAULT_COMPANION,
      presentation: isPresentation(data.presentation ?? "")
        ? data.presentation
        : DEFAULT_PRESENTATION,
    };
  } catch {
    return { companionType: DEFAULT_COMPANION, presentation: DEFAULT_PRESENTATION };
  }
}

export function saveCompanionPrefs(prefs: CompanionUiPrefs): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      companionType: prefs.companionType,
      presentation: prefs.presentation,
    }),
  );
}
