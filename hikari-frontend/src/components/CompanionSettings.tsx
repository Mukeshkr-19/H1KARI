"use client";

import {
  COMPANION_TYPES,
  PRESENTATIONS,
  type CompanionType,
  type Presentation,
} from "@/utils/companion/constants";

type Props = {
  companionType: CompanionType;
  presentation: Presentation;
  onChange: (companionType: CompanionType, presentation: Presentation) => void;
  onSyncServer?: (companionType: CompanionType, presentation: Presentation) => void;
};

export function CompanionSettings({
  companionType,
  presentation,
  onChange,
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
      <p className="text-xs text-gray-500 px-1">
        Preferences are saved in your browser and may sync to your local HIKARI server or
        private config when connected. Live captions are ephemeral and are not stored here.
      </p>
    </div>
  );
}
