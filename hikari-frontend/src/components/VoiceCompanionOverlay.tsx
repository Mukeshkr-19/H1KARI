"use client";

import type { CompanionState, CompanionType, Presentation } from "@/utils/companion/constants";

export type CompanionCaption = {
  role: "user" | "assistant" | "system";
  text: string;
  is_final: boolean;
  timestamp: string;
};

const CAPTION_DISPLAY_MAX = 500;

type Props = {
  visible: boolean;
  state: CompanionState;
  companionType: CompanionType;
  presentation: Presentation;
  caption: CompanionCaption | null;
};

const COMPANION_LABEL: Record<CompanionType, string> = {
  cat: "Cat companion",
  dog: "Dog companion",
  bird: "Bird companion",
};

function CompanionGlyph({ type, state }: { type: CompanionType; state: CompanionState }) {
  const base =
    "relative flex h-24 w-24 items-center justify-center rounded-3xl border border-purple-500/30 bg-gradient-to-br from-[#1a1a2e] to-[#0f0f1a] text-5xl shadow-lg shadow-purple-900/20";
  const anim =
    state === "listening"
      ? "animate-[companion-pulse_1.2s_ease-in-out_infinite]"
      : state === "thinking"
        ? "animate-[companion-bounce_0.6s_ease-in-out_infinite]"
        : state === "speaking"
          ? "animate-[companion-talk_0.35s_ease-in-out_infinite]"
          : "";
  const glyph = type === "cat" ? "🐱" : type === "dog" ? "🐶" : "🐦";
  return (
    <div className={`${base} ${anim}`} aria-hidden>
      <span className={state === "speaking" ? "scale-110 transition-transform" : ""}>
        {glyph}
      </span>
      {state === "listening" && (
        <span className="absolute inset-0 rounded-3xl ring-2 ring-amber-400/50 ring-offset-2 ring-offset-[#0a0a0f]" />
      )}
      {state === "speaking" && (
        <span className="absolute -bottom-1 left-1/2 h-2 w-8 -translate-x-1/2 rounded-full bg-emerald-400/80 animate-pulse" />
      )}
    </div>
  );
}

function displayCaptionText(text: string): string {
  return text.length <= CAPTION_DISPLAY_MAX ? text : text.slice(0, CAPTION_DISPLAY_MAX);
}

export function VoiceCompanionOverlay({
  visible,
  state,
  companionType,
  presentation,
  caption,
}: Props) {
  if (!visible || state === "hidden") return null;

  const statusLabel =
    state === "listening"
      ? "Listening"
      : state === "thinking"
        ? "Thinking"
        : state === "speaking"
          ? "Speaking"
          : state === "error"
            ? "Error"
            : "Ready";

  const captionText = caption?.text ? displayCaptionText(caption.text) : "";

  return (
    <div
      className="pointer-events-none fixed bottom-24 left-0 right-0 z-40 flex justify-center px-4"
      role="region"
      aria-label="Voice companion"
    >
      <div className="flex max-w-md flex-col items-center gap-3 rounded-2xl border border-gray-800/80 bg-[#0a0a0f]/90 px-5 py-4 backdrop-blur-md">
        <p className="sr-only">
          {COMPANION_LABEL[companionType]}, {presentation} presentation, {statusLabel}
        </p>
        <CompanionGlyph type={companionType} state={state} />
        <p className="text-xs font-medium uppercase tracking-wide text-purple-300/90">
          {statusLabel}
        </p>
        {captionText ? (
          <p
            className="max-w-xs text-center text-sm text-gray-200"
            aria-live="polite"
            aria-atomic="true"
          >
            <span className="text-gray-500">{caption?.role}: </span>
            {captionText}
            {caption && !caption.is_final && (
              <span className="text-gray-500"> …</span>
            )}
          </p>
        ) : (
          <p className="text-xs text-gray-500">Live captions appear here during voice</p>
        )}
      </div>
      <style jsx global>{`
        @keyframes companion-pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.06); opacity: 0.92; }
        }
        @keyframes companion-bounce {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-6px); }
        }
        @keyframes companion-talk {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.08); }
        }
      `}</style>
    </div>
  );
}
