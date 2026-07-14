"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { VoiceCompanionOverlay, type CompanionCaption } from "@/components/VoiceCompanionOverlay";
import { CompanionSettings } from "@/components/CompanionSettings";
import {
  type CompanionState,
  type CompanionType,
  type Presentation,
} from "@/utils/companion/constants";
import { loadCompanionPrefs, saveCompanionPrefs } from "@/utils/companion/storage";
import protocolSchema from "../../../protocol/hikari-v1.json";

/** Off by default; set NEXT_PUBLIC_HIKARI_VOICE_COMPANION=1 at build time to enable overlay UI. */
const VOICE_COMPANION_UI_ENABLED =
  process.env.NEXT_PUBLIC_HIKARI_VOICE_COMPANION === "1";

interface Message {
  id: string;
  text: string;
  role: "user" | "ai";
  timestamp: Date;
}

interface AgentStatus {
  name: string;
  active: boolean;
  actions: number;
}

type TabType = "chat" | "agents" | "files" | "settings";
type ClientMessageType = keyof typeof protocolSchema.client_to_server;
type ServerMessageType = keyof typeof protocolSchema.server_to_client;
type ServerMessage = Record<string, unknown> & { type: ServerMessageType };

const PROTOCOL_VERSION = protocolSchema.version;

function encodeClientMessage(
  type: ClientMessageType,
  fields: Record<string, unknown> = {},
): string {
  const required = protocolSchema.client_to_server[type].required;
  for (const field of Object.keys(required)) {
    if (!(field in fields)) {
      throw new Error(`Missing protocol field: ${field}`);
    }
  }
  return JSON.stringify({ type, ...fields });
}

function parseServerMessage(raw: string): ServerMessage | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (
      typeof value === "object" &&
      value !== null &&
      "type" in value &&
      typeof value.type === "string" &&
      value.type in protocolSchema.server_to_client
    ) {
      const type = value.type as ServerMessageType;
      const required = protocolSchema.server_to_client[type] as readonly string[];
      if (required.every((field) => field in value)) {
        return value as ServerMessage;
      }
    }
  } catch {
    return null;
  }
  return null;
}

function stringField(message: ServerMessage, field: string): string {
  const value = message[field];
  return typeof value === "string" ? value : "";
}

type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onstart: (() => void) | null;
  onresult: ((event: SpeechRecognitionResultEvent) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop?: () => void;
  abort?: () => void;
};

type SpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

type SpeechRecognitionResultEvent = {
  results: {
    [index: number]: {
      [index: number]: {
        transcript: string;
      };
    };
  };
};

type SpeechRecognitionWindow = Window & {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
};

function terminateSpeechRecognition(recognition: BrowserSpeechRecognition): void {
  if (typeof recognition.abort === "function") {
    try {
      recognition.abort();
      return;
    } catch {
      // fall through to stop when abort fails
    }
  }
  if (typeof recognition.stop === "function") {
    try {
      recognition.stop();
    } catch {
      // preserve generation invalidation even if termination throws
    }
  }
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isConnected, setIsConnected] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [activeTab, setActiveTab] = useState<TabType>("chat");
  const [pairingCode, setPairingCode] = useState("");
  const [isPaired, setIsPaired] = useState(false);
  const [agents] = useState<AgentStatus[]>([
    { name: "voice", active: true, actions: 0 },
    { name: "research", active: true, actions: 0 },
    { name: "files", active: true, actions: 0 },
    { name: "system", active: true, actions: 0 },
    { name: "code", active: true, actions: 0 },
    { name: "memory", active: true, actions: 0 },
  ]);
  const [serverUrl, setServerUrl] = useState("");
  const [orbState, setOrbState] = useState<"idle" | "listening" | "thinking" | "speaking">("idle");
  const [companionState, setCompanionState] = useState<CompanionState>("hidden");
  const [companionCaption, setCompanionCaption] = useState<CompanionCaption | null>(null);
  const [companionType, setCompanionType] = useState<CompanionType>("cat");
  const [presentation, setPresentation] = useState<Presentation>("non-binary");
  const [voiceSessionActive, setVoiceSessionActive] = useState(false);
  const [voiceTurnActive, setVoiceTurnActive] = useState(false);
  const [recognitionCaptureActive, setRecognitionCaptureActive] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const voiceTurnActiveRef = useRef(false);
  const voiceSessionActiveRef = useRef(false);
  const voiceErrorResetRef = useRef<number | null>(null);
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const voiceCaptureGenerationRef = useRef(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  useEffect(() => {
    const prefs = loadCompanionPrefs();
    setCompanionType(prefs.companionType);
    setPresentation(prefs.presentation);
  }, []);

  const resetVoiceCompanion = useCallback(() => {
    if (voiceErrorResetRef.current !== null) {
      window.clearTimeout(voiceErrorResetRef.current);
      voiceErrorResetRef.current = null;
    }
    voiceSessionActiveRef.current = false;
    voiceTurnActiveRef.current = false;
    setVoiceSessionActive(false);
    setVoiceTurnActive(false);
    setCompanionState("hidden");
    setCompanionCaption(null);
    setOrbState("idle");
    setIsListening(false);
    setIsTyping(false);
  }, []);

  const releaseRecognitionInstance = useCallback(
    (recognition: BrowserSpeechRecognition | null) => {
      if (recognition && recognitionRef.current === recognition) {
        recognitionRef.current = null;
        setRecognitionCaptureActive(false);
      }
    },
    [],
  );

  const cancelVoiceCapture = useCallback(() => {
    voiceCaptureGenerationRef.current += 1;
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    setRecognitionCaptureActive(false);
    if (recognition) {
      terminateSpeechRecognition(recognition);
    }
    resetVoiceCompanion();
  }, [resetVoiceCompanion]);

  const canStartMicrophoneCapture = useCallback((): boolean => {
    if (!isConnected) {
      return false;
    }
    if (voiceSessionActiveRef.current || voiceTurnActiveRef.current) {
      return false;
    }
    if (recognitionRef.current !== null || isListening) {
      return false;
    }
    return true;
  }, [isConnected, isListening]);

  const beginVoiceCapture = useCallback(() => {
    if (voiceErrorResetRef.current !== null) {
      window.clearTimeout(voiceErrorResetRef.current);
      voiceErrorResetRef.current = null;
    }
    voiceSessionActiveRef.current = true;
    voiceTurnActiveRef.current = false;
    setVoiceSessionActive(true);
    setVoiceTurnActive(false);
    setCompanionCaption(null);
    setCompanionState("listening");
  }, []);

  const submitVoiceRequest = useCallback(
    (transcript: string, captureToken: number): boolean => {
      if (
        captureToken !== voiceCaptureGenerationRef.current ||
        !voiceSessionActiveRef.current
      ) {
        return false;
      }
      const trimmed = transcript.trim();
      if (!trimmed) {
        cancelVoiceCapture();
        return false;
      }
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        cancelVoiceCapture();
        return false;
      }
      try {
        ws.send(encodeClientMessage("voice", { text: trimmed }));
      } catch {
        cancelVoiceCapture();
        return false;
      }
      addMessage(trimmed, "user");
      voiceTurnActiveRef.current = true;
      setVoiceTurnActive(true);
      setIsTyping(true);
      setOrbState("thinking");
      return true;
    },
    [cancelVoiceCapture],
  );

  const syncCompanionPrefs = useCallback(
    (type: CompanionType, pres: Presentation) => {
      saveCompanionPrefs({ companionType: type, presentation: pres });
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          encodeClientMessage("companion_preferences", {
            companion_type: type,
            presentation: pres,
          }),
        );
      }
    },
    [],
  );

  const applyCompanionUpdate = useCallback((companion: Record<string, unknown>) => {
    const prefs = companion.preferences as
      | { companion_type?: string; presentation?: string }
      | undefined;
    if (prefs?.companion_type === "cat" || prefs?.companion_type === "dog" || prefs?.companion_type === "bird") {
      setCompanionType(prefs.companion_type);
    }
    if (
      prefs?.presentation === "male" ||
      prefs?.presentation === "female" ||
      prefs?.presentation === "non-binary"
    ) {
      setPresentation(prefs.presentation);
    }

    if (!voiceSessionActiveRef.current) {
      return;
    }

    const state = companion.state as CompanionState | undefined;
    if (state === "idle" || state === "hidden") {
      resetVoiceCompanion();
      return;
    }
    if (state) setCompanionState(state);
    const cap = companion.caption as CompanionCaption | undefined;
    if (state === "listening") {
      setCompanionCaption(null);
    } else if (cap?.text) {
      setCompanionCaption(cap);
    }
    if (state === "listening") {
      setOrbState("listening");
    } else if (state === "thinking") {
      setOrbState("thinking");
    } else if (state === "speaking") {
      setOrbState("speaking");
    }
  }, [resetVoiceCompanion]);

  const connect = useCallback(() => {
    if (!serverUrl) return;

    const ws = new WebSocket(serverUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      if (pairingCode) {
        ws.send(encodeClientMessage("pair", {
          code: pairingCode,
          device_type: "web",
          protocol_version: PROTOCOL_VERSION,
        }));
      }
    };

    ws.onmessage = (event) => {
      const data = parseServerMessage(event.data);
      if (!data) return;
      if (data.type === "paired") {
        setIsPaired(true);
        if (VOICE_COMPANION_UI_ENABLED) {
          resetVoiceCompanion();
          const prefs = loadCompanionPrefs();
          syncCompanionPrefs(prefs.companionType, prefs.presentation);
        }
        addMessage("Connected to HIKARI! Ask me anything.", "ai");
      } else if (data.type === "companion_update" && data.companion) {
        applyCompanionUpdate(data.companion as Record<string, unknown>);
      } else if (data.type === "response") {
        setIsTyping(false);
        addMessage(stringField(data, "text"), "ai");
        if (voiceTurnActiveRef.current || voiceSessionActiveRef.current) {
          voiceTurnActiveRef.current = false;
          setVoiceTurnActive(false);
        } else {
          setOrbState("idle");
        }
      } else if (data.type === "pair_error" || data.type === "pair_locked") {
        alert(stringField(data, "message") || "Pairing failed");
      } else if (data.type === "protocol_error") {
        alert(stringField(data, "message") || "Unsupported server protocol");
      }
    };

    ws.onclose = () => {
      cancelVoiceCapture();
      setIsConnected(false);
      setIsPaired(false);
      setTimeout(connect, 3000);
    };
  }, [serverUrl, pairingCode, applyCompanionUpdate, syncCompanionPrefs, resetVoiceCompanion, cancelVoiceCapture]);

  const addMessage = (text: string, role: "user" | "ai") => {
    setMessages((prev) => [
      ...prev,
      {
        id: Math.random().toString(36).substr(2, 9),
        text,
        role,
        timestamp: new Date(),
      },
    ]);
  };

  const sendMessage = () => {
    if (!input.trim() || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    cancelVoiceCapture();
    addMessage(input, "user");
    wsRef.current.send(encodeClientMessage("message", { text: input }));
    setIsTyping(true);
    setOrbState("thinking");
    setInput("");
  };

  const startListening = () => {
    if (!VOICE_COMPANION_UI_ENABLED) {
      const speechWindow = window as SpeechRecognitionWindow;
      const SpeechRecognition =
        speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;

      if (!SpeechRecognition) {
        alert("Speech recognition not supported in this browser");
        return;
      }

      const recognition = new SpeechRecognition();
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.lang = "en-US";

      recognition.onstart = () => {
        setIsListening(true);
        setOrbState("listening");
      };

      recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        setInput(transcript);
        setIsListening(false);
        setOrbState("idle");

        if (transcript.trim()) {
          setTimeout(() => {
            setInput(transcript);
            if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
              addMessage(transcript, "user");
              wsRef.current.send(encodeClientMessage("message", { text: transcript }));
              setIsTyping(true);
              setOrbState("thinking");
            }
          }, 100);
        }
      };

      recognition.onerror = () => {
        setIsListening(false);
        setOrbState("idle");
      };

      recognition.onend = () => {
        setIsListening(false);
        setOrbState("idle");
      };

      recognition.start();
      return;
    }

    if (!canStartMicrophoneCapture()) {
      return;
    }

    const speechWindow = window as SpeechRecognitionWindow;
    const SpeechRecognition =
      speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;

    if (!SpeechRecognition) {
      alert("Speech recognition not supported in this browser");
      return;
    }

    const recognition = new SpeechRecognition();
    recognitionRef.current = recognition;
    setRecognitionCaptureActive(true);
    voiceCaptureGenerationRef.current += 1;
    const captureToken = voiceCaptureGenerationRef.current;
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";

    recognition.onstart = () => {
      if (captureToken !== voiceCaptureGenerationRef.current) {
        return;
      }
      setIsListening(true);
      setOrbState("listening");
      beginVoiceCapture();
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        try {
          wsRef.current.send(encodeClientMessage("voice", { listening: true }));
        } catch {
          cancelVoiceCapture();
        }
      }
    };

    recognition.onresult = (event) => {
      if (captureToken !== voiceCaptureGenerationRef.current) {
        return;
      }
      const transcript = event.results[0][0].transcript;
      setInput(transcript);
      setIsListening(false);
      submitVoiceRequest(transcript, captureToken);
    };

    recognition.onerror = () => {
      if (captureToken !== voiceCaptureGenerationRef.current) {
        return;
      }
      setIsListening(false);
      releaseRecognitionInstance(recognition);
      if (voiceSessionActiveRef.current) {
        setOrbState("idle");
        setCompanionState("error");
        setCompanionCaption({
          role: "system",
          text: "Voice input error",
          is_final: true,
          timestamp: new Date().toISOString(),
        });
        voiceErrorResetRef.current = window.setTimeout(() => {
          cancelVoiceCapture();
          voiceErrorResetRef.current = null;
        }, 1500);
      } else {
        setOrbState("idle");
      }
    };

    recognition.onend = () => {
      if (captureToken !== voiceCaptureGenerationRef.current) {
        return;
      }
      setIsListening(false);
      releaseRecognitionInstance(recognition);
      if (!voiceTurnActiveRef.current && voiceSessionActiveRef.current) {
        cancelVoiceCapture();
      }
    };

    recognition.start();
  };

  const getOrbGradient = () => {
    switch (orbState) {
      case "listening":
        return "radial-gradient(circle, #f59e0b, #d97706, #92400e)";
      case "thinking":
        return "radial-gradient(circle, #8b5cf6, #6d28d9, #4c1d95)";
      case "speaking":
        return "radial-gradient(circle, #10b981, #059669, #047857)";
      default:
        return "radial-gradient(circle, #667eea, #764ba2, #5b21b6)";
    }
  };

  const getOrbAnimation = () => {
    switch (orbState) {
      case "listening":
        return "pulse-listening 1s ease-in-out infinite";
      case "thinking":
        return "pulse-thinking 0.5s ease-in-out infinite";
      case "speaking":
        return "pulse-speaking 0.3s ease-in-out infinite";
      default:
        return "pulse-idle 3s ease-in-out infinite";
    }
  };

  const microphoneDisabled = VOICE_COMPANION_UI_ENABLED
    ? !isConnected ||
      voiceSessionActive ||
      voiceTurnActive ||
      recognitionCaptureActive ||
      isListening
    : !isConnected || isListening;

  if (!isPaired) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen p-6">
        <div className="w-20 h-20 rounded-full mb-8" style={{ background: getOrbGradient(), animation: "pulse-idle 3s ease-in-out infinite" }} />
        <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-purple-400 to-blue-500 bg-clip-text text-transparent">
          HIKARI
        </h1>
        <p className="text-gray-400 mb-8 text-center max-w-sm">
          Connect to your HIKARI assistant
        </p>

        <div className="w-full max-w-sm space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-2">Server URL</label>
            <input
              type="text"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              placeholder="ws://192.168.1.100:8765"
              className="w-full bg-[#1a1a2e] border border-gray-700 rounded-xl px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 transition"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-2">Pairing Code</label>
            <input
              type="text"
              value={pairingCode}
              onChange={(e) => setPairingCode(e.target.value.toUpperCase())}
              placeholder="ABC123"
              maxLength={6}
              className="w-full bg-[#1a1a2e] border border-gray-700 rounded-xl px-4 py-3 text-white text-center text-2xl tracking-[0.5em] placeholder-gray-600 focus:outline-none focus:border-purple-500 transition"
            />
          </div>
          <button
            onClick={connect}
            disabled={!serverUrl || !pairingCode}
            className="w-full bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3 rounded-xl transition-all duration-200"
          >
            Connect
          </button>
          {isConnected && !isPaired && (
            <p className="text-center text-yellow-400 text-sm animate-pulse">
              Connecting...
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen max-h-screen">
      {/* Header */}
      <header className="flex items-center justify-between px-4 py-3 border-b border-gray-800 bg-[#0a0a0f]/80 backdrop-blur-xl">
        <div className="flex items-center gap-3">
          <div
            className="w-10 h-10 rounded-full"
            style={{ background: getOrbGradient(), animation: getOrbAnimation() }}
          />
          <div>
            <h1 className="text-lg font-bold bg-gradient-to-r from-purple-400 to-blue-500 bg-clip-text text-transparent">
              HIKARI
            </h1>
            <div className="flex items-center gap-1.5">
              <div className={`w-2 h-2 rounded-full ${isConnected ? "bg-green-400" : "bg-red-400"}`} />
              <span className="text-xs text-gray-500">
                {isConnected ? "Connected" : "Disconnected"}
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={startListening}
            disabled={microphoneDisabled}
            aria-disabled={microphoneDisabled}
            className={`p-2.5 rounded-full transition-all ${
              isListening
                ? "bg-red-500/20 text-red-400 animate-pulse"
                : microphoneDisabled
                  ? "bg-gray-800/50 text-gray-600 cursor-not-allowed"
                  : "bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700"
            }`}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          </button>
        </div>
      </header>

      {/* Tab Content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === "chat" && (
          <div className="flex flex-col h-full">
            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {messages.length === 0 && (
                <div className="flex flex-col items-center justify-center h-full text-center">
                  <div
                    className="w-24 h-24 rounded-full mb-6 opacity-50"
                    style={{ background: getOrbGradient(), animation: "pulse-idle 3s ease-in-out infinite" }}
                  />
                  <h2 className="text-xl font-semibold text-gray-300 mb-2">How can I help?</h2>
                  <p className="text-gray-500 text-sm max-w-xs">
                    Ask me anything - weather, news, files, coding, or just chat
                  </p>
                  <div className="flex flex-wrap gap-2 mt-6 justify-center">
                    {["What's the weather?", "Latest news", "System status", "Morning briefing"].map((q) => (
                      <button
                        key={q}
                        onClick={() => {
                          setInput(q);
                        }}
                        className="px-3 py-1.5 bg-gray-800/50 border border-gray-700 rounded-full text-sm text-gray-400 hover:text-white hover:border-purple-500 transition"
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`max-w-[85%] px-4 py-3 rounded-2xl text-sm leading-relaxed ${
                      msg.role === "user"
                        ? "bg-gradient-to-r from-purple-600 to-blue-600 text-white rounded-br-md"
                        : "bg-[#1a1a2e] border border-gray-800 text-gray-200 rounded-bl-md"
                    }`}
                  >
                    {msg.text}
                  </div>
                </div>
              ))}

              {isTyping && (
                <div className="flex justify-start">
                  <div className="bg-[#1a1a2e] border border-gray-800 px-4 py-3 rounded-2xl rounded-bl-md">
                    <div className="flex gap-1.5">
                      <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                      <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                      <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="p-4 border-t border-gray-800 bg-[#0a0a0f]/80 backdrop-blur-xl">
              <div className="flex gap-2">
                <input
                  ref={inputRef}
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && sendMessage()}
                  placeholder="Ask me anything..."
                  className="flex-1 bg-[#1a1a2e] border border-gray-700 rounded-full px-5 py-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 transition"
                />
                <button
                  onClick={sendMessage}
                  disabled={!input.trim() || !isConnected}
                  className="bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-5 py-3 rounded-full transition-all"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        )}

        {activeTab === "agents" && (
          <div className="p-4 space-y-3 overflow-y-auto h-full">
            <h2 className="text-xl font-bold mb-4">Agent Swarm</h2>
            {agents.map((agent) => (
              <div
                key={agent.name}
                className="flex items-center justify-between p-4 bg-[#1a1a2e] border border-gray-800 rounded-xl"
              >
                <div className="flex items-center gap-3">
                  <div className={`w-3 h-3 rounded-full ${agent.active ? "bg-green-400" : "bg-gray-600"}`} />
                  <div>
                    <p className="font-medium capitalize">{agent.name}</p>
                    <p className="text-xs text-gray-500">{agent.actions} actions</p>
                  </div>
                </div>
                <div className="text-xs text-gray-500">{agent.active ? "Active" : "Inactive"}</div>
              </div>
            ))}
          </div>
        )}

        {activeTab === "files" && (
          <div className="p-4 overflow-y-auto h-full">
            <h2 className="text-xl font-bold mb-4">File Access</h2>
            <p className="text-gray-400 text-sm">
              Ask HIKARI to read, search, or list files using voice or text commands.
            </p>
            <div className="mt-4 space-y-2">
              <p className="text-xs text-gray-500">Quick commands:</p>
              {["List my Documents", "Search for project files", "Read my resume"].map((cmd) => (
                <button
                  key={cmd}
                  onClick={() => {
                    setInput(cmd);
                    setActiveTab("chat");
                  }}
                  className="w-full text-left px-4 py-3 bg-[#1a1a2e] border border-gray-800 rounded-lg text-sm text-gray-300 hover:border-purple-500 transition"
                >
                  {cmd}
                </button>
              ))}
            </div>
          </div>
        )}

        {activeTab === "settings" && (
          <div className="p-4 space-y-4 overflow-y-auto h-full">
            <h2 className="text-xl font-bold mb-4">Settings</h2>
            {VOICE_COMPANION_UI_ENABLED && (
              <CompanionSettings
                companionType={companionType}
                presentation={presentation}
                onChange={(type, pres) => {
                  setCompanionType(type);
                  setPresentation(pres);
                }}
                onSyncServer={syncCompanionPrefs}
              />
            )}
            <div className="space-y-3">
              <div className="p-4 bg-[#1a1a2e] border border-gray-800 rounded-xl">
                <p className="font-medium mb-1">Server</p>
                <p className="text-sm text-gray-400">{serverUrl || "Not configured"}</p>
              </div>
              <div className="p-4 bg-[#1a1a2e] border border-gray-800 rounded-xl">
                <p className="font-medium mb-1">Pairing Code</p>
                <p className="text-sm text-gray-400">{pairingCode || "Not set"}</p>
              </div>
              <div className="p-4 bg-[#1a1a2e] border border-gray-800 rounded-xl">
                <p className="font-medium mb-1">Connection</p>
                <p className="text-sm text-gray-400">{isConnected ? "Connected" : "Disconnected"}</p>
              </div>
              <button
                onClick={() => {
                  wsRef.current?.close();
                  setIsPaired(false);
                  setIsConnected(false);
                }}
                className="w-full py-3 bg-red-500/10 border border-red-500/30 text-red-400 rounded-xl hover:bg-red-500/20 transition"
              >
                Disconnect
              </button>
            </div>
          </div>
        )}
      </div>

      <VoiceCompanionOverlay
        visible={
          VOICE_COMPANION_UI_ENABLED &&
          isPaired &&
          voiceSessionActive &&
          companionState !== "hidden" &&
          companionState !== "idle"
        }
        state={companionState}
        companionType={companionType}
        presentation={presentation}
        caption={companionCaption}
      />

      {/* Bottom Navigation */}
      <nav className="flex border-t border-gray-800 bg-[#0a0a0f]/90 backdrop-blur-xl">
        {[
          { id: "chat" as TabType, label: "Chat", icon: "M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" },
          { id: "agents" as TabType, label: "Agents", icon: "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" },
          { id: "files" as TabType, label: "Files", icon: "M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" },
          { id: "settings" as TabType, label: "Settings", icon: "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex-1 flex flex-col items-center gap-1 py-3 transition ${
              activeTab === tab.id
                ? "text-purple-400"
                : "text-gray-500 hover:text-gray-300"
            }`}
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={tab.icon} />
            </svg>
            <span className="text-[10px]">{tab.label}</span>
          </button>
        ))}
      </nav>

      {/* Animations */}
      <style jsx global>{`
        @keyframes pulse-idle {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.05); opacity: 0.9; }
        }
        @keyframes pulse-listening {
          0%, 100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.4); }
          50% { transform: scale(1.1); box-shadow: 0 0 0 10px rgba(245, 158, 11, 0); }
        }
        @keyframes pulse-thinking {
          0%, 100% { transform: scale(1) rotate(0deg); }
          50% { transform: scale(1.08) rotate(5deg); }
        }
        @keyframes pulse-speaking {
          0%, 100% { transform: scale(1); }
          25% { transform: scale(1.05); }
          50% { transform: scale(1.1); }
          75% { transform: scale(1.05); }
        }
      `}</style>
    </div>
  );
}
