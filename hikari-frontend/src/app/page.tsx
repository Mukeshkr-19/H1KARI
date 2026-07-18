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

type DocumentConfirmation = {
  taskId: string;
  path: string;
  provider: string;
  fallbackProvider: string;
};

const ROOT_DOCUMENT_TASK_KEY = "hikari.rootDocumentTaskId";
const DOCUMENT_TASK_ID_MAX = 64;
const DOCUMENT_STATUSES = [
  "queued",
  "running",
  "interrupted",
  "verifying",
  "completed",
  "failed",
  "cancelled",
] as const;
type DocumentStatus = (typeof DOCUMENT_STATUSES)[number];
const TERMINAL_DOCUMENT_STATUSES = new Set<DocumentStatus>([
  "completed",
  "failed",
  "cancelled",
]);

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

function boundedString(
  message: ServerMessage,
  field: string,
  maxLength: number,
  allowEmpty = false,
): string | null {
  const value = message[field];
  return typeof value === "string" && value.length <= maxLength && (allowEmpty || value.length > 0)
    ? value
    : null;
}

function documentStatusField(message: ServerMessage): DocumentStatus | null {
  const value = message.status;
  return typeof value === "string" && DOCUMENT_STATUSES.includes(value as DocumentStatus)
    ? (value as DocumentStatus)
    : null;
}

function progressField(message: ServerMessage): number | null {
  const value = message.progress;
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 100
    ? value
    : null;
}

const LOCAL_CAPTION_MAX = 500;

type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onstart: (() => void) | null;
  onresult: ((event: SpeechRecognitionResultEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop?: () => void;
  abort?: () => void;
};

type SpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

type SpeechRecognitionResultEvent = {
  resultIndex: number;
  results: {
    length: number;
    [index: number]: {
      isFinal: boolean;
      [index: number]: {
        transcript: string;
      };
    };
  };
};

function aggregateSpeechRecognitionTranscript(
  event: SpeechRecognitionResultEvent,
): { transcript: string; complete: boolean } {
  let transcript = "";
  let complete = event.results.length > 0;
  for (let index = 0; index < event.results.length; index += 1) {
    const result = event.results[index];
    transcript += result?.[0]?.transcript ?? "";
    if (!result?.isFinal) {
      complete = false;
    }
  }
  return { transcript, complete };
}

type SpeechRecognitionErrorEvent = {
  error: string;
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

function boundCaptionText(text: string): string {
  const trimmed = text.trim();
  if (trimmed.length <= LOCAL_CAPTION_MAX) {
    return trimmed;
  }
  return trimmed.slice(0, LOCAL_CAPTION_MAX);
}

function speechRecognitionErrorMessage(errorCode: string): string {
  switch (errorCode) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone permission is blocked. Type your message instead.";
    case "no-speech":
      return "No speech was detected. Try again or type your message.";
    case "audio-capture":
      return "No microphone is available. Type your message instead.";
    case "network":
      return "Voice recognition is temporarily unavailable. Type your message instead.";
    default:
      return "Voice input failed. Type your message instead.";
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
  const [interfaceError, setInterfaceError] = useState("");
  const [documentPath, setDocumentPath] = useState("");
  const [documentProvider, setDocumentProvider] = useState("");
  const [documentFallbackProvider, setDocumentFallbackProvider] = useState("");
  const [documentTaskId, setDocumentTaskId] = useState("");
  const [documentConfirmation, setDocumentConfirmation] = useState<DocumentConfirmation | null>(null);
  const [documentPreparePending, setDocumentPreparePending] = useState(false);
  const [documentAwaitingConfirmation, setDocumentAwaitingConfirmation] = useState(false);
  const [documentStatus, setDocumentStatus] = useState("Ready");
  const [documentStatusCode, setDocumentStatusCode] = useState<DocumentStatus | "">("");
  const [documentProgress, setDocumentProgress] = useState(0);
  const [documentCheckpoint, setDocumentCheckpoint] = useState("");
  const [documentExplanation, setDocumentExplanation] = useState("");
  const [documentExplanationProvider, setDocumentExplanationProvider] = useState("");
  const [documentError, setDocumentError] = useState("");
  const [documentFollowUp, setDocumentFollowUp] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const voiceTurnActiveRef = useRef(false);
  const voiceSessionActiveRef = useRef(false);
  const voiceErrorResetRef = useRef<number | null>(null);
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const voiceCaptureGenerationRef = useRef(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const documentTaskIdRef = useRef("");
  const documentTaskIdsSeenRef = useRef(new Set<string>());
  const documentPreparePendingRef = useRef(false);
  const documentPrepareRequestRef = useRef<Omit<DocumentConfirmation, "taskId"> | null>(null);
  const dashboardHeadingRef = useRef<HTMLHeadingElement>(null);
  const confirmationHeadingRef = useRef<HTMLHeadingElement>(null);
  const documentErrorHeadingRef = useRef<HTMLHeadingElement>(null);

  const rememberDocumentTask = useCallback((taskId: string) => {
    documentTaskIdRef.current = taskId;
    setDocumentTaskId(taskId);
    window.localStorage.setItem(ROOT_DOCUMENT_TASK_KEY, taskId);
  }, []);

  const forgetDocumentTask = useCallback(() => {
    documentTaskIdRef.current = "";
    setDocumentTaskId("");
    window.localStorage.removeItem(ROOT_DOCUMENT_TASK_KEY);
  }, []);

  const failDocumentPrepare = useCallback((message: string) => {
    documentPreparePendingRef.current = false;
    documentPrepareRequestRef.current = null;
    setDocumentPreparePending(false);
    setDocumentAwaitingConfirmation(false);
    setDocumentConfirmation(null);
    setDocumentError(message);
    setDocumentStatus("Document request failed");
    setDocumentStatusCode("failed");
    setDocumentProgress(0);
    setDocumentCheckpoint("");
    setActiveTab("files");
  }, []);

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

  useEffect(() => {
    if (documentAwaitingConfirmation) confirmationHeadingRef.current?.focus();
  }, [documentAwaitingConfirmation]);

  useEffect(() => {
    if (documentError) documentErrorHeadingRef.current?.focus();
  }, [documentError]);

  useEffect(() => {
    if (isPaired) dashboardHeadingRef.current?.focus();
  }, [isPaired]);

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
    if (VOICE_COMPANION_UI_ENABLED) {
      setCompanionState("listening");
    }
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
        if (VOICE_COMPANION_UI_ENABLED) {
          ws.send(encodeClientMessage("voice", { text: trimmed }));
        } else {
          ws.send(encodeClientMessage("message", { text: trimmed }));
        }
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
        setInterfaceError("");
        const savedTaskId = window.localStorage.getItem(ROOT_DOCUMENT_TASK_KEY) ?? "";
        if (savedTaskId.length > 0 && savedTaskId.length <= DOCUMENT_TASK_ID_MAX) {
          documentTaskIdRef.current = savedTaskId;
          documentTaskIdsSeenRef.current.add(savedTaskId);
          setDocumentTaskId(savedTaskId);
          ws.send(encodeClientMessage("task_status", { task_id: savedTaskId }));
          setDocumentStatus("Reconnecting to document task");
        } else if (savedTaskId) {
          window.localStorage.removeItem(ROOT_DOCUMENT_TASK_KEY);
        }
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
          if (!VOICE_COMPANION_UI_ENABLED) {
            resetVoiceCompanion();
          } else {
            voiceTurnActiveRef.current = false;
            setVoiceTurnActive(false);
          }
        } else {
          setOrbState("idle");
        }
      } else if (data.type === "document_confirmation_required") {
        if (!documentPreparePendingRef.current) return;
        const taskId = boundedString(data, "task_id", DOCUMENT_TASK_ID_MAX);
        const path = boundedString(data, "path", 4096);
        const provider = boundedString(data, "provider", 64);
        const fallbackProvider = boundedString(data, "fallback_provider", 64, true);
        if (
          !taskId || !path || !provider || fallbackProvider === null ||
          documentTaskIdsSeenRef.current.has(taskId)
        ) {
          failDocumentPrepare(
            "Document confirmation was invalid. Check the path and provider, then try again.",
          );
          return;
        }
        const request = documentPrepareRequestRef.current;
        if (
          !request || request.path !== path || request.provider !== provider ||
          request.fallbackProvider !== fallbackProvider
        ) {
          failDocumentPrepare(
            "Document confirmation did not match your request. Try again.",
          );
          return;
        }
        documentPreparePendingRef.current = false;
        documentPrepareRequestRef.current = null;
        setDocumentPreparePending(false);
        setDocumentConfirmation({ taskId, path, provider, fallbackProvider });
        setDocumentAwaitingConfirmation(true);
        documentTaskIdsSeenRef.current.add(taskId);
        rememberDocumentTask(taskId);
        setDocumentStatus("Waiting for your confirmation");
        setDocumentStatusCode("queued");
        setDocumentProgress(0);
        setDocumentCheckpoint("confirmation_required");
        setDocumentExplanation("");
        setDocumentError("");
        setActiveTab("files");
      } else if (data.type === "task_update") {
        const taskId = boundedString(data, "task_id", DOCUMENT_TASK_ID_MAX);
        const rootTaskId = boundedString(data, "root_task_id", DOCUMENT_TASK_ID_MAX);
        const status = documentStatusField(data);
        const progress = progressField(data);
        const checkpoint = boundedString(data, "checkpoint", 128, true);
        if (
          taskId && rootTaskId && status && progress !== null && checkpoint !== null &&
          rootTaskId === documentTaskIdRef.current
        ) {
          setDocumentStatus(status.replaceAll("_", " "));
          setDocumentStatusCode(status);
          setDocumentProgress(progress);
          setDocumentCheckpoint(checkpoint);
          setDocumentAwaitingConfirmation(false);
          setDocumentError("");
        }
      } else if (data.type === "document_explanation") {
        const taskId = boundedString(data, "task_id", DOCUMENT_TASK_ID_MAX);
        const rootTaskId = boundedString(data, "root_task_id", DOCUMENT_TASK_ID_MAX);
        const text = boundedString(data, "text", 20000);
        const provider = boundedString(data, "provider", 64);
        if (
          taskId && rootTaskId && text && provider &&
          rootTaskId === documentTaskIdRef.current
        ) {
          setDocumentExplanation(text);
          setDocumentExplanationProvider(provider);
          setDocumentStatus("Explanation ready");
          setDocumentStatusCode("completed");
          setDocumentProgress(100);
          setDocumentCheckpoint("completed");
          setDocumentAwaitingConfirmation(false);
          setDocumentError("");
          setActiveTab("files");
        }
      } else if (data.type === "document_error") {
        const taskId = boundedString(data, "task_id", DOCUMENT_TASK_ID_MAX);
        const rootTaskId = boundedString(data, "root_task_id", DOCUMENT_TASK_ID_MAX);
        const code = boundedString(data, "code", 128);
        const message = boundedString(data, "message", 1000);
        if (!(taskId && rootTaskId && code && message)) {
          return;
        }
        if (documentPreparePendingRef.current) {
          failDocumentPrepare(message);
          return;
        }
        if (rootTaskId === documentTaskIdRef.current) {
          setDocumentError(message);
          setDocumentStatus("Document request failed");
          setDocumentStatusCode("failed");
          setDocumentAwaitingConfirmation(false);
          setActiveTab("files");
          if (code === "task_not_found" || code === "actor_not_authorized") {
            forgetDocumentTask();
          }
        }
      } else if (data.type === "companion_preferences_error") {
        setInterfaceError(
          boundedString(data, "message", 1000) ?? "Companion preferences could not be saved.",
        );
      } else if (data.type === "pair_error" || data.type === "pair_locked") {
        setInterfaceError(stringField(data, "message") || "Pairing failed");
      } else if (data.type === "protocol_error") {
        setInterfaceError(stringField(data, "message") || "Unsupported server protocol");
      } else if (data.type === "error") {
        setInterfaceError(boundedString(data, "message", 1000) ?? "Server request failed");
        setIsTyping(false);
        setOrbState("idle");
        if (
          voiceSessionActiveRef.current ||
          voiceTurnActiveRef.current ||
          recognitionRef.current
        ) {
          cancelVoiceCapture();
        }
        if (documentPreparePendingRef.current) {
          failDocumentPrepare("Document request failed");
        }
      }
    };

    ws.onclose = () => {
      if (documentPreparePendingRef.current) {
        failDocumentPrepare(
          "Connection lost while preparing the document. Reconnect and try again.",
        );
      }
      cancelVoiceCapture();
      setIsConnected(false);
      setIsPaired(false);
      setTimeout(connect, 3000);
    };
  }, [serverUrl, pairingCode, applyCompanionUpdate, syncCompanionPrefs, resetVoiceCompanion, cancelVoiceCapture, forgetDocumentTask, rememberDocumentTask, failDocumentPrepare]);

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
    if (!canStartMicrophoneCapture()) {
      return;
    }

    const speechWindow = window as SpeechRecognitionWindow;
    const SpeechRecognition =
      speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;

    if (!SpeechRecognition) {
      setInterfaceError("Speech recognition is not supported in this browser. Type your message instead.");
      inputRef.current?.focus();
      return;
    }

    const recognition = new SpeechRecognition();
    recognitionRef.current = recognition;
    setRecognitionCaptureActive(true);
    voiceCaptureGenerationRef.current += 1;
    const captureToken = voiceCaptureGenerationRef.current;
    let captureSubmitted = false;
    recognition.continuous = false;
    recognition.interimResults = VOICE_COMPANION_UI_ENABLED;
    recognition.lang = "en-US";

    recognition.onstart = () => {
      if (captureToken !== voiceCaptureGenerationRef.current) {
        return;
      }
      setInterfaceError("");
      setIsListening(true);
      setOrbState("listening");
      beginVoiceCapture();
      if (VOICE_COMPANION_UI_ENABLED && wsRef.current?.readyState === WebSocket.OPEN) {
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
      if (captureSubmitted) {
        return;
      }
      const { transcript, complete } = aggregateSpeechRecognitionTranscript(event);

      if (VOICE_COMPANION_UI_ENABLED && transcript) {
        setCompanionCaption({
          role: "user",
          text: boundCaptionText(transcript),
          is_final: complete,
          timestamp: new Date().toISOString(),
        });
      }

      if (!complete) {
        return;
      }

      captureSubmitted = true;
      setInput(transcript);
      setIsListening(false);
      submitVoiceRequest(transcript, captureToken);
    };

    recognition.onerror = (event) => {
      if (captureToken !== voiceCaptureGenerationRef.current) {
        return;
      }
      setIsListening(false);
      releaseRecognitionInstance(recognition);
      const message = speechRecognitionErrorMessage(
        typeof event?.error === "string" ? event.error : "",
      );
      setInterfaceError(message);
      inputRef.current?.focus();
      setCompanionCaption(null);
      if (VOICE_COMPANION_UI_ENABLED && voiceSessionActiveRef.current) {
        setOrbState("idle");
        setCompanionState("error");
        setCompanionCaption({
          role: "system",
          text: boundCaptionText(message),
          is_final: true,
          timestamp: new Date().toISOString(),
        });
        voiceErrorResetRef.current = window.setTimeout(() => {
          cancelVoiceCapture();
          voiceErrorResetRef.current = null;
        }, 1500);
      } else {
        cancelVoiceCapture();
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

  const handleMicrophoneClick = () => {
    if (isListening || recognitionCaptureActive) {
      cancelVoiceCapture();
      return;
    }
    startListening();
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

  const microphoneCapturing = isListening || recognitionCaptureActive;
  const microphoneDisabled =
    !isConnected ||
    voiceTurnActive ||
    (voiceSessionActive && !microphoneCapturing);

  const sendDocumentMessage = (
    type: "document_prepare" | "document_confirm" | "document_follow_up" | "document_cancel",
    fields: Record<string, unknown>,
  ): boolean => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setDocumentError("Connect to HIKARI before using the document reader.");
      return false;
    }
    try {
      ws.send(encodeClientMessage(type, fields));
      setDocumentError("");
      return true;
    } catch {
      setDocumentError("The document request could not be sent.");
      return false;
    }
  };

  const prepareDocument = () => {
    const path = documentPath.trim();
    const provider = documentProvider.trim();
    const fallbackProvider = documentFallbackProvider.trim();
    if (!path || !provider) return;
    const fields: Record<string, unknown> = { path, provider };
    if (fallbackProvider) fields.fallback_provider = fallbackProvider;
    // Set the request guard before send: a fast local server can reply in the
    // same turn, before React state updates are committed.
    documentPreparePendingRef.current = true;
    documentPrepareRequestRef.current = { path, provider, fallbackProvider };
    setDocumentPreparePending(true);
    setDocumentAwaitingConfirmation(false);
    setDocumentConfirmation(null);
    setDocumentStatus("Checking document access");
    setDocumentStatusCode("");
    setDocumentProgress(0);
    setDocumentCheckpoint("preparing");
    setDocumentExplanation("");
    if (!sendDocumentMessage("document_prepare", fields)) {
      documentPreparePendingRef.current = false;
      documentPrepareRequestRef.current = null;
      setDocumentPreparePending(false);
    }
  };

  const confirmDocument = () => {
    if (!documentAwaitingConfirmation || !documentConfirmation) return;
    const fields: Record<string, unknown> = {
      task_id: documentConfirmation.taskId,
      provider: documentConfirmation.provider,
    };
    if (documentConfirmation.fallbackProvider) {
      fields.fallback_provider = documentConfirmation.fallbackProvider;
    }
    if (sendDocumentMessage("document_confirm", fields)) {
      setDocumentAwaitingConfirmation(false);
      setDocumentStatus("Reading and explaining document");
      setDocumentStatusCode("running");
      setDocumentCheckpoint("confirmed");
    }
  };

  const cancelDocument = () => {
    if (!documentTaskId) return;
    if (sendDocumentMessage("document_cancel", { task_id: documentTaskId })) {
      documentPreparePendingRef.current = false;
      documentPrepareRequestRef.current = null;
      setDocumentPreparePending(false);
      setDocumentAwaitingConfirmation(false);
      setDocumentStatus("Cancelling document request");
    }
  };

  const sendDocumentFollowUp = () => {
    const text = documentFollowUp.trim();
    const provider = documentConfirmation?.provider ?? documentProvider.trim();
    const fallbackProvider = documentConfirmation?.fallbackProvider ?? documentFallbackProvider.trim();
    if (!documentTaskId || !text || !provider) return;
    const fields: Record<string, unknown> = {
      task_id: documentTaskId,
      text,
      provider,
    };
    if (fallbackProvider) {
      fields.fallback_provider = fallbackProvider;
    }
    if (sendDocumentMessage("document_follow_up", fields)) {
      setDocumentFollowUp("");
      setDocumentStatus("Answering follow-up");
      setDocumentStatusCode("running");
      setDocumentProgress(0);
      setDocumentCheckpoint("follow_up");
    }
  };

  const documentRequestLocked = documentPreparePending || documentAwaitingConfirmation;
  const canCancelDocument = Boolean(documentTaskId) &&
    (!documentStatusCode || !TERMINAL_DOCUMENT_STATUSES.has(documentStatusCode));

  if (!isPaired) {
    return (
      <main
        className="flex flex-col items-center justify-center min-h-screen p-6"
        aria-labelledby="pairing-title"
      >
        <div
          className="w-20 h-20 rounded-full mb-8"
          style={{ background: getOrbGradient(), animation: "pulse-idle 3s ease-in-out infinite" }}
          aria-hidden="true"
        />
        <h1 id="pairing-title" className="text-4xl font-bold mb-2 bg-gradient-to-r from-purple-400 to-blue-500 bg-clip-text text-transparent">
          HIKARI
        </h1>
        <p className="text-gray-400 mb-8 text-center max-w-sm">
          Connect to your HIKARI assistant
        </p>

        <div className="w-full max-w-sm space-y-4">
          <div>
            <label htmlFor="server-url" className="block text-sm text-gray-400 mb-2">Server URL</label>
            <input
              id="server-url"
              type="text"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              placeholder="ws://192.168.1.100:8765"
              className="w-full bg-[#1a1a2e] border border-gray-700 rounded-xl px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 transition"
            />
          </div>
          <div>
            <label htmlFor="pairing-code" className="block text-sm text-gray-400 mb-2">Pairing Code</label>
            <input
              id="pairing-code"
              type="text"
              inputMode="text"
              autoComplete="one-time-code"
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
            <p
              className="text-center text-yellow-400 text-sm animate-pulse"
              role="status"
              aria-live="polite"
            >
              Connecting...
            </p>
          )}
          {interfaceError && (
            <p className="text-red-300 text-sm" role="alert">
              {interfaceError}
            </p>
          )}
        </div>
      </main>
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
            aria-hidden="true"
          />
          <div>
            <h1
              ref={dashboardHeadingRef}
              tabIndex={-1}
              className="text-lg font-bold bg-gradient-to-r from-purple-400 to-blue-500 bg-clip-text text-transparent"
            >
              HIKARI
            </h1>
            <div className="flex items-center gap-1.5" role="status" aria-live="polite">
              <div aria-hidden="true" className={`w-2 h-2 rounded-full ${isConnected ? "bg-green-400" : "bg-red-400"}`} />
              <span className="text-xs text-gray-500">
                {isConnected ? "Connected" : "Disconnected"}
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleMicrophoneClick}
            disabled={microphoneDisabled}
            aria-disabled={microphoneDisabled}
            aria-label={
              microphoneCapturing ? "Stop listening" : "Start voice input"
            }
            className={`inline-flex min-h-11 min-w-11 items-center justify-center rounded-full p-2.5 transition-all ${
              microphoneCapturing
                ? "bg-red-500/20 text-red-400 animate-pulse"
                : microphoneDisabled
                  ? "bg-gray-800/50 text-gray-600 cursor-not-allowed"
                  : "bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700"
            }`}
          >
            <svg aria-hidden="true" focusable="false" className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          </button>
        </div>
      </header>

      {/* Tab Content */}
      <main className="flex-1 overflow-hidden">
        {interfaceError && (
          <div className="mx-4 mt-3 rounded-lg border border-red-500/40 bg-red-950/40 p-3 text-sm text-red-200" role="alert">
            {interfaceError}
          </div>
        )}
        {activeTab === "chat" && (
          <div className="flex flex-col h-full">
            {/* Messages */}
            <section
              className="flex-1 overflow-y-auto p-4 space-y-4"
              role="log"
              aria-label="Conversation"
              aria-live="polite"
              aria-relevant="additions text"
            >
              {messages.length === 0 && (
                <div className="flex flex-col items-center justify-center h-full text-center">
                  <div
                    className="w-24 h-24 rounded-full mb-6 opacity-50"
                    style={{ background: getOrbGradient(), animation: "pulse-idle 3s ease-in-out infinite" }}
                    aria-hidden="true"
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
                  aria-label={msg.role === "user" ? "You" : "HIKARI"}
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
                <div className="flex justify-start" role="status" aria-label="HIKARI is typing">
                  <div className="bg-[#1a1a2e] border border-gray-800 px-4 py-3 rounded-2xl rounded-bl-md">
                    <div className="flex gap-1.5" aria-hidden="true">
                      <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                      <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                      <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </section>

            {/* Input */}
            <div className="p-4 border-t border-gray-800 bg-[#0a0a0f]/80 backdrop-blur-xl">
              <div className="flex gap-2">
                <label htmlFor="chat-input" className="sr-only">Message HIKARI</label>
                <input
                  id="chat-input"
                  ref={inputRef}
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && sendMessage()}
                  placeholder="Ask me anything..."
                  className="flex-1 bg-[#1a1a2e] border border-gray-700 rounded-full px-5 py-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 transition"
                />
                <button
                  type="button"
                  onClick={sendMessage}
                  disabled={!input.trim() || !isConnected}
                  aria-label="Send message"
                  className="bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-5 py-3 rounded-full transition-all"
                >
                  <svg aria-hidden="true" focusable="false" className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
                  <div aria-hidden="true" className={`w-3 h-3 rounded-full ${agent.active ? "bg-green-400" : "bg-gray-600"}`} />
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
          <div className="p-4 overflow-y-auto h-full space-y-5">
            <div>
              <h2 className="text-xl font-bold">Explain a document</h2>
              <p className="mt-1 text-gray-400 text-sm">
                Enter the path to a UTF-8 text file already stored on this HIKARI computer.
                The file is not uploaded by this page.
              </p>
            </div>

            <div className="space-y-4 rounded-xl border border-gray-800 bg-[#1a1a2e] p-4">
              <div>
                <label htmlFor="document-path" className="block text-sm font-medium text-gray-200 mb-2">
                  Path on the HIKARI computer
                </label>
                <input
                  id="document-path"
                  type="text"
                  value={documentPath}
                  onChange={(event) => setDocumentPath(event.target.value)}
                  disabled={documentRequestLocked}
                  placeholder="/path/to/notes.txt"
                  autoComplete="off"
                  className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none"
                />
              </div>
              <div>
                <label htmlFor="document-provider" className="block text-sm font-medium text-gray-200 mb-2">
                  Primary provider
                </label>
                <input
                  id="document-provider"
                  type="text"
                  value={documentProvider}
                  onChange={(event) => setDocumentProvider(event.target.value)}
                  disabled={documentRequestLocked}
                  placeholder="Provider name"
                  autoComplete="off"
                  className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none"
                />
              </div>
              <div>
                <label htmlFor="document-fallback-provider" className="block text-sm font-medium text-gray-200 mb-2">
                  Fallback provider <span className="font-normal text-gray-500">(optional)</span>
                </label>
                <input
                  id="document-fallback-provider"
                  type="text"
                  value={documentFallbackProvider}
                  onChange={(event) => setDocumentFallbackProvider(event.target.value)}
                  disabled={documentRequestLocked}
                  placeholder="Fallback provider name"
                  autoComplete="off"
                  className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none"
                />
              </div>
              <button
                type="button"
                onClick={prepareDocument}
                disabled={documentRequestLocked || !isConnected || !documentPath.trim() || !documentProvider.trim()}
                className="w-full rounded-lg bg-purple-600 px-4 py-2.5 font-semibold text-white hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Review document request
              </button>
            </div>

            {documentAwaitingConfirmation && documentConfirmation && (
              <section
                className="rounded-xl border border-yellow-500/40 bg-yellow-950/20 p-4"
                aria-labelledby="document-confirmation-heading"
              >
                <h3
                  id="document-confirmation-heading"
                  ref={confirmationHeadingRef}
                  tabIndex={-1}
                  className="text-lg font-semibold text-yellow-200"
                >
                  Confirm document access
                </h3>
                <p className="mt-2 text-sm text-gray-200">
                  HIKARI will read <strong className="break-all">{documentConfirmation.path}</strong> and
                  send its text to <strong>{documentConfirmation.provider}</strong>
                  {documentConfirmation.fallbackProvider ? (
                    <> with <strong>{documentConfirmation.fallbackProvider}</strong> as fallback.</>
                  ) : (
                    <> with no fallback provider.</>
                  )}
                </p>
                <div className="mt-4 flex flex-wrap gap-3">
                  <button
                    type="button"
                    onClick={confirmDocument}
                    className="rounded-lg bg-green-600 px-4 py-2 font-semibold text-white hover:bg-green-500"
                  >
                    Confirm
                  </button>
                  <button
                    type="button"
                    onClick={cancelDocument}
                    className="rounded-lg border border-gray-600 px-4 py-2 font-semibold text-gray-100 hover:bg-gray-800"
                  >
                    Cancel
                  </button>
                </div>
              </section>
            )}

            {documentError && (
              <section className="rounded-xl border border-red-500/50 bg-red-950/30 p-4" role="alert">
                <h3 ref={documentErrorHeadingRef} tabIndex={-1} className="font-semibold text-red-200">
                  Document request error
                </h3>
                <p className="mt-1 text-sm text-red-100">{documentError}</p>
              </section>
            )}

            {documentTaskId && (
              <section className="rounded-xl border border-gray-800 bg-[#1a1a2e] p-4" aria-labelledby="document-progress-heading">
                <h3 id="document-progress-heading" className="font-semibold">Document progress</h3>
                <label htmlFor="document-progress" className="mt-3 block text-sm text-gray-300">
                  {documentStatus}
                </label>
                <progress
                  id="document-progress"
                  value={documentProgress}
                  max={100}
                  className="mt-2 h-3 w-full"
                >
                  {documentProgress}%
                </progress>
                <p className="mt-2 text-sm text-gray-400" role="status" aria-live="polite" aria-atomic="true">
                  {documentStatus}. {documentProgress}% complete
                  {documentCheckpoint ? `; checkpoint: ${documentCheckpoint}` : ""}.
                </p>
                {canCancelDocument && (
                  <button
                    type="button"
                    onClick={cancelDocument}
                    className="mt-3 rounded-lg border border-gray-600 px-4 py-2 font-semibold text-gray-100 hover:bg-gray-800"
                  >
                    Cancel document task
                  </button>
                )}
              </section>
            )}

            {documentExplanation && documentTaskId && (
              <section className="rounded-xl border border-green-500/30 bg-green-950/20 p-4" aria-labelledby="document-explanation-heading">
                <h3 id="document-explanation-heading" className="font-semibold text-green-200">
                  Explanation from {documentExplanationProvider}
                </h3>
                <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-gray-100">
                  {documentExplanation}
                </p>
                <div className="mt-5">
                  <label htmlFor="document-follow-up" className="block text-sm font-medium text-gray-200 mb-2">
                    Follow-up question about this document
                  </label>
                  <textarea
                    id="document-follow-up"
                    value={documentFollowUp}
                    onChange={(event) => setDocumentFollowUp(event.target.value)}
                    rows={3}
                    className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white focus:border-purple-500 focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={sendDocumentFollowUp}
                    disabled={!documentFollowUp.trim() || !(documentConfirmation?.provider ?? documentProvider.trim())}
                    className="mt-3 rounded-lg bg-purple-600 px-4 py-2 font-semibold text-white hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Ask follow-up
                  </button>
                </div>
              </section>
            )}
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
      </main>

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
      <nav aria-label="Primary" className="flex border-t border-gray-800 bg-[#0a0a0f]/90 backdrop-blur-xl">
        {[
          { id: "chat" as TabType, label: "Chat", icon: "M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" },
          { id: "agents" as TabType, label: "Agents", icon: "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" },
          { id: "files" as TabType, label: "Files", icon: "M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" },
          { id: "settings" as TabType, label: "Settings", icon: "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" },
        ].map((tab) => (
          <button
            type="button"
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            aria-current={activeTab === tab.id ? "page" : undefined}
            className={`flex-1 flex flex-col items-center gap-1 py-3 transition ${
              activeTab === tab.id
                ? "text-purple-400"
                : "text-gray-500 hover:text-gray-300"
            }`}
          >
            <svg aria-hidden="true" focusable="false" className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
