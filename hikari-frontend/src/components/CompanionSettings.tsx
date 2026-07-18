"use client";

import {
  COMPANION_TYPES,
  PRESENTATIONS,
  SPEECH_RATE_MAX,
  SPEECH_RATE_MIN,
  type CompanionType,
  type Presentation,
} from "@/utils/companion/constants";

type Props = {
  companionType: CompanionType;
  presentation: Presentation;
  speakResponses: boolean;
  speechRate: number;
  isSpeaking: boolean;
  onChange: (companionType: CompanionType, presentation: Presentation) => void;
  onSpeakResponsesChange: (enabled: boolean) => void;
  onStopSpeaking: () => void;
  onSyncServer?: (companionType: CompanionType, presentation: Presentation) => void;
};

export function CompanionSettings({
  companionType,
  presentation,
  speakResponses,
  speechRate,
  isSpeaking,
  onChange,
  onSpeakResponsesChange,
  onStopSpeaking,
  onSyncServer,
}: Props) {
  const apply = (type: CompanionType, pres: Presentation) => {
    onChange(type, pres);
    onSyncServer?.(type, pres);
  };

  return (
    <div className="space-y-4">
      <div className="p-4 bg-[#1a1a2e] border border-gray-800 rounded-xl">
        <h3 className="font-medium mb-2" id="companion-type-label">
          Companion
        </h3>
        <p className="text-xs text-gray-500 mb-3">
          Choose cat, dog, or bird. Custom pets are not supported.
        </p>
        <div
          className="flex flex-wrap gap-2"
          role="group"
          aria-labelledby="companion-type-label"
        >
          {COMPANION_TYPES.map((type) => (
            <button
              key={type}
              type="button"
              aria-pressed={companionType === type}
              onClick={() => apply(type, presentation)}
              className={`px-4 py-2 rounded-lg text-sm capitalize transition ${
                companionType === type
                  ? "bg-purple-600/30 border border-purple-500 text-white"
                  : "bg-gray-800/50 border border-gray-700 text-gray-400 hover:text-white"
              }`}
            >
              {type}
            </button>
          ))}
        </div>
      </div>
      <div className="p-4 bg-[#1a1a2e] border border-gray-800 rounded-xl">
        <h3 className="font-medium mb-2" id="presentation-label">
          Presentation
        </h3>
        <div
          className="flex flex-wrap gap-2"
          role="group"
          aria-labelledby="presentation-label"
        >
          {PRESENTATIONS.map((pres) => (
            <button
              key={pres}
              type="button"
              aria-pressed={presentation === pres}
              onClick={() => apply(companionType, pres)}
              className={`px-4 py-2 rounded-lg text-sm transition ${
                presentation === pres
                  ? "bg-purple-600/30 border border-purple-500 text-white"
                  : "bg-gray-800/50 border border-gray-700 text-gray-400 hover:text-white"
              }`}
            >
              {pres}
            </button>
          ))}
        </div>
      </div>
      <div className="p-4 bg-[#1a1a2e] border border-gray-800 rounded-xl">
        <h3 className="font-medium mb-2" id="speak-responses-label">
          Speak responses
        </h3>
        <p className="text-xs text-gray-500 mb-3">
          Off by default. When enabled, spoken output uses the browser speech engine for
          voice-session replies and voice-started document explanations. Browser or vendor
          processing and retention are not guaranteed to stay on this device. Captions and
          text remain available when spoken output is off or unavailable.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            role="switch"
            aria-checked={speakResponses}
            aria-labelledby="speak-responses-label"
            onClick={() => onSpeakResponsesChange(!speakResponses)}
            className={`px-4 py-2 rounded-lg text-sm transition ${
              speakResponses
                ? "bg-purple-600/30 border border-purple-500 text-white"
                : "bg-gray-800/50 border border-gray-700 text-gray-400 hover:text-white"
            }`}
          >
            {speakResponses ? "On" : "Off"}
          </button>
          <p className="text-xs text-gray-500">
            Rate {speechRate.toFixed(1)} (bounded {SPEECH_RATE_MIN.toFixed(1)}–
            {SPEECH_RATE_MAX.toFixed(1)}; say “speak slower” during voice)
          </p>
          <button
            type="button"
            onClick={onStopSpeaking}
            disabled={!isSpeaking}
            className="px-4 py-2 rounded-lg text-sm border border-gray-700 text-gray-300 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            Stop speaking
          </button>
        </div>
      </div>
      <p className="text-xs text-gray-500 px-1">
        Preferences save companion type, presentation, speak-responses, and speech rate in
        your browser. Spoken text, captions, transcripts, document paths, and responses are
        not stored in these preferences. Live captions are ephemeral.
      </p>
    </div>
  );
}
