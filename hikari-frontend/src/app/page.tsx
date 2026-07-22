"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { VoiceCompanionOverlay, type CompanionCaption } from "@/components/VoiceCompanionOverlay";
import { CompanionSettings } from "@/components/CompanionSettings";
import {
  DEFAULT_SPEAK_RESPONSES,
  DEFAULT_SPEECH_RATE,
  type CompanionState,
  type CompanionType,
  type Presentation,
} from "@/utils/companion/constants";
import { loadCompanionPrefs, saveCompanionPrefs } from "@/utils/companion/storage";
import {
  SpeechOutputController,
  createBrowserSpeechEngine,
  parseSpeechControlIntent,
} from "@/utils/companion/speechOutput";
import {
  boundVoiceTranscript,
  parseVoiceDocumentIntent,
} from "@/utils/companion/voiceDocumentIntent";
import { ProductivityActionPreview } from "@/components/ProductivityActionPreview";
import { ApprovalScopeSelector } from "@/components/ApprovalScopeSelector";
import { EmailDraftProposal } from "@/components/EmailDraftProposal";
import { CalendarProposalForm } from "@/components/CalendarProposalForm";
import { ResearchProposalForm } from "@/components/ResearchProposalForm";
import { ReminderProposalForm } from "@/components/ReminderProposalForm";
import { ScheduledJobsPanel } from "@/components/ScheduledJobsPanel";
import { ScheduledJobCreateForm } from "@/components/ScheduledJobCreateForm";
import { Phase4PairingPanel } from "@/components/Phase4PairingPanel";
import { HandoffOfferPanel } from "@/components/HandoffOfferPanel";
import { VisualTransferPanel } from "@/components/VisualTransferPanel";
import { VisionAnalysisPanel } from "@/components/VisionAnalysisPanel";
import { CameraCapturePanel } from "@/components/CameraCapturePanel";
import {
  createInitialPairingState,
  isPairingErrorCode,
  isPairingPending,
  isPairingTerminal,
  reducePairing,
  type PairingState,
} from "@/utils/phase4/pairing";
import {
  createInitialHandoffState,
  isHandoffErrorCode,
  isHandoffPending,
  isHandoffTerminal,
  reduceHandoff,
  type HandoffState,
} from "@/utils/phase4/handoff";
import {
  createInitialVisualTransferState,
  isVisualTransferErrorCode,
  isVisualTransferPending,
  isVisualTransferTerminal,
  reduceVisualTransfer,
  inspectImageDimensions,
  validateImageFile,
  type VisualTransferState,
} from "@/utils/phase4/visualTransfer";
import { createCanonicalRequestId } from "@/utils/phase4/identifiers";
import {
  createInitialVisionAnalysisState,
  isVisionAnalysisErrorCode,
  isVisionAnalysisPending,
  reduceVisionAnalysis,
  type VisionAnalysisState,
  type VisionCapability,
} from "@/utils/phase4/visionAnalysis";
import {
  encodeHandoffAccept,
  encodeHandoffCancel,
  encodeHandoffReject,
  encodePairingCancel,
  encodePairingConfirm,
  encodePairingPrepare,
  encodeVisualTransferBegin,
  encodeVisualTransferCancel,
  encodeVisionAnalysisCancel,
  encodeVisionAnalysisPrepare,
  parsePhase4ServerMessage,
  type Phase4ServerMessage,
} from "@/utils/phase4/phase4Protocol";
import {
  createInitialProposalLifecycleState,
  reduceProposalLifecycle,
  type ProposalLifecycleState,
  type ProposalLifecycleStatus,
} from "@/utils/productivity/actionLifecycle";
import {
  createApprovalScopeStateFromAllowed,
  isApprovalScopeConfirmReady,
  resetApprovalScopeState,
  type ApprovalScopeState,
} from "@/utils/productivity/approvalScopes";
import {
  createEmailDraftRequestId,
  createEmptyEmailDraftFields,
  emailDraftResponseMatchesRequest,
  validateEmailDraftFields,
  type EmailDraftFieldName,
  type EmailDraftFields,
  type EmailDraftValidationCode,
} from "@/utils/productivity/emailDraftProposal";
import {
  createEmptyCalendarDraftFields,
  createEmptyCalendarReadFields,
  validateCalendarDraftFields,
  validateCalendarReadFields,
  type CalendarDraftFields,
  type CalendarFieldName,
  type CalendarFormMode,
  type CalendarReadFields,
  type CalendarValidationCode,
} from "@/utils/productivity/calendarProposal";
import {
  createEmptyResearchFields,
  createResearchRequestId,
  researchResponseMatchesRequest,
  validateResearchFields,
  type ResearchFieldName,
  type ResearchFields,
  type ResearchValidationCode,
} from "@/utils/productivity/researchProposal";
import {
  createEmptyReminderFields,
  validateReminderFields,
  type ReminderFieldName,
  type ReminderFields,
  type ReminderValidationCode,
} from "@/utils/productivity/reminderProposal";
import {
  encodeProductivityCancel,
  encodeProductivityConfirm,
  encodeProductivityCalendarDraftPrepare,
  encodeProductivityCalendarReadPrepare,
  encodeProductivityEmailDraftPrepare,
  encodeProductivityResearchPrepare,
  encodeProductivityReminderPrepare,
  parseProductivityServerMessage,
  type ProductivityCalendarResult,
  type ProductivityResearchResult,
  type ProductivityServerMessage,
  type ProductivityUpdateStatus,
} from "@/utils/productivity/productivityProtocol";
import {
  mapPreviewErrorMessage,
  type ProductivityPreviewErrorCode,
} from "@/utils/productivity/actionPreview";
import {
  clearScheduledJobPendingControl,
  replaceScheduledJobInList,
  setScheduledJobPendingControl,
  type ScheduledJobControl,
  type ScheduledJobErrorCode,
  type ScheduledJobView,
} from "@/utils/productivity/scheduledJobs";
import {
  createEmptyScheduleProposalFields,
  createScheduleRequestId,
  validateScheduleProposalFields,
  type ScheduleAction,
  type ScheduleFieldName,
  type ScheduleProposalFields,
  type ScheduleValidationCode,
} from "@/utils/productivity/scheduleProposal";
import {
  encodeScheduledJobCreate,
  encodeScheduledJobCancel,
  encodeScheduledJobPause,
  encodeScheduledJobResume,
  encodeScheduledJobsList,
  parseScheduledJobsServerMessage,
  type ScheduledJobsServerMessage,
  type ScheduledJobResearchResultMessage,
  type ScheduledJobCalendarResultMessage,
} from "@/utils/productivity/scheduledJobsProtocol";
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

/** Server message types handled by strict dedicated parsers before legacy fallback. */
const STRICT_DEDICATED_SERVER_MESSAGE_TYPES = new Set<string>([
  "productivity_confirmation_required",
  "productivity_update",
  "productivity_error",
  "productivity_research_result",
  "productivity_calendar_result",
  "scheduled_jobs",
  "scheduled_job_update",
  "scheduled_job_error",
  "scheduled_job_research_result",
  "scheduled_job_calendar_result",
  "pairing_challenge",
  "pairing_confirmed",
  "pairing_update",
  "pairing_error",
  "handoff_offer",
  "handoff_update",
  "handoff_error",
  "visual_transfer_ready",
  "visual_transfer_update",
  "visual_transfer_complete",
  "visual_transfer_error",
  "vision_analysis_ready",
  "vision_analysis_update",
  "vision_observation",
  "vision_analysis_error",
]);

function parseWebSocketFrameType(raw: string): string | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (
      typeof value === "object" &&
      value !== null &&
      "type" in value &&
      typeof (value as { type: unknown }).type === "string"
    ) {
      return (value as { type: string }).type;
    }
  } catch {
    return null;
  }
  return null;
}

function isStrictDedicatedServerMessageType(type: string): boolean {
  return STRICT_DEDICATED_SERVER_MESSAGE_TYPES.has(type);
}

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

const TERMINAL_PRODUCTIVITY_STATUSES = new Set<ProposalLifecycleStatus>([
  "completed",
  "failed",
  "cancelled",
]);

function productivityLiveStatus(status: ProposalLifecycleStatus): string {
  switch (status) {
    case "preview":
      return "Review the proposed action.";
    case "confirming":
      return "Waiting for confirmation…";
    case "approved":
      return "Action approved.";
    case "executing":
      return "Action in progress.";
    case "completed":
      return "Action completed.";
    case "failed":
      return "Action failed.";
    case "cancelling":
      return "Cancelling action…";
    case "cancelled":
      return "Action cancelled.";
    default:
      return "";
  }
}

function applyProductivityUpdateStatus(
  state: ProposalLifecycleState,
  proposalId: string,
  status: ProductivityUpdateStatus,
): ProposalLifecycleState {
  if (state.status === "idle" || state.proposalId !== proposalId) {
    return state;
  }
  if (state.status === status) {
    return state;
  }
  switch (status) {
    case "preview":
      return state;
    case "confirming":
      return reduceProposalLifecycle(state, { type: "confirm", proposalId });
    case "approved": {
      const confirming = reduceProposalLifecycle(state, {
        type: "confirm",
        proposalId,
      });
      return reduceProposalLifecycle(confirming, { type: "approve", proposalId });
    }
    case "executing": {
      const confirming = reduceProposalLifecycle(state, {
        type: "confirm",
        proposalId,
      });
      const approved = reduceProposalLifecycle(confirming, {
        type: "approve",
        proposalId,
      });
      return reduceProposalLifecycle(approved, { type: "execute", proposalId });
    }
    case "completed": {
      const confirming = reduceProposalLifecycle(state, {
        type: "confirm",
        proposalId,
      });
      const approved = reduceProposalLifecycle(confirming, {
        type: "approve",
        proposalId,
      });
      const executing = reduceProposalLifecycle(approved, {
        type: "execute",
        proposalId,
      });
      return reduceProposalLifecycle(executing, { type: "complete", proposalId });
    }
    case "failed":
      return reduceProposalLifecycle(state, {
        type: "fail",
        proposalId,
        error: "unavailable",
      });
    case "cancelling":
      return reduceProposalLifecycle(state, { type: "cancel", proposalId });
    case "cancelled": {
      const cancelling = reduceProposalLifecycle(state, {
        type: "cancel",
        proposalId,
      });
      return reduceProposalLifecycle(cancelling, {
        type: "cancelled",
        proposalId,
      });
    }
    default:
      return state;
  }
}

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
      const spec = protocolSchema.server_to_client[type];
      if (!Array.isArray(spec)) {
        return null;
      }
      const required = spec;
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
  const [speakResponses, setSpeakResponses] = useState(DEFAULT_SPEAK_RESPONSES);
  const [speechRate, setSpeechRate] = useState(DEFAULT_SPEECH_RATE);
  const [isSpeakingAloud, setIsSpeakingAloud] = useState(false);
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
  const [productivityLifecycle, setProductivityLifecycle] = useState<ProposalLifecycleState>(
    createInitialProposalLifecycleState,
  );
  const [approvalScopeState, setApprovalScopeState] = useState<ApprovalScopeState>(
    resetApprovalScopeState,
  );
  const [emailDraftFields, setEmailDraftFields] = useState<EmailDraftFields>(
    createEmptyEmailDraftFields,
  );
  const [emailDraftPending, setEmailDraftPending] = useState(false);
  const [emailDraftValidationCode, setEmailDraftValidationCode] = useState<
    EmailDraftValidationCode | undefined
  >(undefined);
  const [emailDraftValidationField, setEmailDraftValidationField] = useState<
    EmailDraftFieldName | undefined
  >(undefined);
  const [emailDraftPrepareError, setEmailDraftPrepareError] = useState<
    ProductivityPreviewErrorCode | undefined
  >(undefined);
  const [calendarMode, setCalendarMode] = useState<CalendarFormMode>("read");
  const [calendarReadFields, setCalendarReadFields] = useState<CalendarReadFields>(
    createEmptyCalendarReadFields,
  );
  const [calendarDraftFields, setCalendarDraftFields] = useState<CalendarDraftFields>(
    createEmptyCalendarDraftFields,
  );
  const [calendarPending, setCalendarPending] = useState(false);
  const [calendarValidationCode, setCalendarValidationCode] = useState<
    CalendarValidationCode | undefined
  >(undefined);
  const [calendarValidationField, setCalendarValidationField] = useState<
    CalendarFieldName | undefined
  >(undefined);
  const [calendarPrepareError, setCalendarPrepareError] = useState<
    ProductivityPreviewErrorCode | undefined
  >(undefined);
  const [researchFields, setResearchFields] = useState<ResearchFields>(
    createEmptyResearchFields,
  );
  const [researchPending, setResearchPending] = useState(false);
  const [researchValidationCode, setResearchValidationCode] = useState<
    ResearchValidationCode | undefined
  >(undefined);
  const [researchValidationField, setResearchValidationField] = useState<
    ResearchFieldName | undefined
  >(undefined);
  const [researchPrepareError, setResearchPrepareError] = useState<
    ProductivityPreviewErrorCode | undefined
  >(undefined);
  const [reminderFields, setReminderFields] = useState<ReminderFields>(
    createEmptyReminderFields,
  );
  const [reminderPending, setReminderPending] = useState(false);
  const [reminderValidationCode, setReminderValidationCode] = useState<
    ReminderValidationCode | undefined
  >(undefined);
  const [reminderValidationField, setReminderValidationField] = useState<
    ReminderFieldName | undefined
  >(undefined);
  const [reminderPrepareError, setReminderPrepareError] = useState<
    ProductivityPreviewErrorCode | undefined
  >(undefined);
  const [productivityResearchResult, setProductivityResearchResult] = useState<
    ProductivityResearchResult | null
  >(null);
  const [productivityCalendarResult, setProductivityCalendarResult] = useState<
    ProductivityCalendarResult | null
  >(null);
  const [scheduledJobs, setScheduledJobs] = useState<ReadonlyArray<ScheduledJobView>>([]);
  const [scheduledJobsError, setScheduledJobsError] = useState<
    ScheduledJobErrorCode | undefined
  >(undefined);
  const [scheduledJobsStatus, setScheduledJobsStatus] = useState<string | undefined>(
    undefined,
  );
  const [scheduleFields, setScheduleFields] = useState<ScheduleProposalFields>(
    createEmptyScheduleProposalFields,
  );
  const [schedulePending, setSchedulePending] = useState(false);
  const [scheduleValidationCode, setScheduleValidationCode] = useState<
    ScheduleValidationCode | undefined
  >(undefined);
  const [scheduleValidationField, setScheduleValidationField] = useState<
    ScheduleFieldName | undefined
  >(undefined);
  const [scheduledResearchResult, setScheduledResearchResult] = useState<
    ScheduledJobResearchResultMessage | null
  >(null);
  const [scheduledCalendarResult, setScheduledCalendarResult] = useState<
    ScheduledJobCalendarResultMessage | null
  >(null);

  // Phase 4 Pairing state & refs
  const [pairingState, setPairingState] = useState<PairingState>(createInitialPairingState);
  const pairingStateRef = useRef<PairingState>(pairingState);
  const [, setPairingPending] = useState(false);
  const pairingPendingRef = useRef(false);

  // Phase 4 Handoff state & refs
  const [handoffState, setHandoffState] = useState<HandoffState>(createInitialHandoffState);
  const handoffStateRef = useRef<HandoffState>(handoffState);
  const [, setHandoffPending] = useState(false);
  const handoffPendingRef = useRef(false);
  const handoffRequestIdRef = useRef<string | null>(null);

  // Phase 4 Visual Transfer state & refs
  const [visualTransferState, setVisualTransferState] = useState<VisualTransferState>(
    createInitialVisualTransferState
  );
  const visualTransferStateRef = useRef<VisualTransferState>(visualTransferState);
  const [, setVisualTransferPending] = useState(false);
  const visualTransferPendingRef = useRef(false);
  const visualTransferBytesRef = useRef<ArrayBuffer | null>(null);

  const [visionAnalysisState, setVisionAnalysisState] = useState<VisionAnalysisState>(
    createInitialVisionAnalysisState,
  );
  const visionAnalysisStateRef = useRef<VisionAnalysisState>(visionAnalysisState);
  const visionRequestIdRef = useRef<string | null>(null);

  // Accessible heading refs
  const pairingHeadingRef = useRef<HTMLHeadingElement>(null);
  const handoffHeadingRef = useRef<HTMLHeadingElement>(null);
  const visualTransferHeadingRef = useRef<HTMLHeadingElement>(null);

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
  const documentAwaitingConfirmationRef = useRef(false);
  const documentStatusCodeRef = useRef<DocumentStatus | "">("");
  const documentTaskVoiceOriginRef = useRef(false);
  const speakResponsesRef = useRef(DEFAULT_SPEAK_RESPONSES);
  const speechOutputRef = useRef<SpeechOutputController | null>(null);
  if (speechOutputRef.current === null) {
    speechOutputRef.current = new SpeechOutputController(createBrowserSpeechEngine());
  }
  const dashboardHeadingRef = useRef<HTMLHeadingElement>(null);
  const confirmationHeadingRef = useRef<HTMLHeadingElement>(null);
  const documentErrorHeadingRef = useRef<HTMLHeadingElement>(null);
  const productivityHeadingRef = useRef<HTMLHeadingElement>(null);
  const emailDraftHeadingRef = useRef<HTMLHeadingElement>(null);
  const calendarHeadingRef = useRef<HTMLHeadingElement>(null);
  const calendarPrepareErrorHeadingRef = useRef<HTMLHeadingElement>(null);
  const researchHeadingRef = useRef<HTMLHeadingElement>(null);
  const researchPrepareErrorHeadingRef = useRef<HTMLHeadingElement>(null);
  const reminderHeadingRef = useRef<HTMLHeadingElement>(null);
  const reminderPrepareErrorHeadingRef = useRef<HTMLHeadingElement>(null);
  const researchResultHeadingRef = useRef<HTMLHeadingElement>(null);
  const calendarResultHeadingRef = useRef<HTMLHeadingElement>(null);
  const productivityLifecycleRef = useRef<ProposalLifecycleState>(
    createInitialProposalLifecycleState(),
  );
  const approvalScopeStateRef = useRef<ApprovalScopeState>(resetApprovalScopeState());
  const emailDraftPendingRef = useRef(false);
  const emailDraftRequestIdRef = useRef<string | null>(null);
  const calendarPendingRef = useRef(false);
  const calendarRequestIdRef = useRef<string | null>(null);
  const researchPendingRef = useRef(false);
  const researchRequestIdRef = useRef<string | null>(null);
  const reminderPendingRef = useRef(false);
  const reminderRequestIdRef = useRef<string | null>(null);
  const scheduledJobsRef = useRef<ReadonlyArray<ScheduledJobView>>([]);
  const schedulePendingRef = useRef(false);
  const scheduleRequestIdRef = useRef<string | null>(null);

  const isProductivityPreparePending = useCallback(
    () =>
      emailDraftPendingRef.current ||
      calendarPendingRef.current ||
      researchPendingRef.current ||
      reminderPendingRef.current,
    [],
  );

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
    documentTaskVoiceOriginRef.current = false;
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
    setSpeakResponses(prefs.speakResponses);
    setSpeechRate(prefs.speechRate);
    speakResponsesRef.current = prefs.speakResponses;
    speechOutputRef.current?.setRate(prefs.speechRate);
  }, []);

  useEffect(() => {
    speakResponsesRef.current = speakResponses;
  }, [speakResponses]);

  useEffect(() => {
    const speech = speechOutputRef.current;
    if (!speech) return;
    speech.setCallbacks({
      onFailure: (message) => {
        setInterfaceError(message);
      },
      onSpeakingChange: (speaking) => {
        setIsSpeakingAloud(speaking);
      },
    });
    return () => {
      speech.dispose();
    };
  }, []);

  useEffect(() => {
    if (documentAwaitingConfirmation) confirmationHeadingRef.current?.focus();
  }, [documentAwaitingConfirmation]);

  useEffect(() => {
    documentAwaitingConfirmationRef.current = documentAwaitingConfirmation;
  }, [documentAwaitingConfirmation]);

  useEffect(() => {
    productivityLifecycleRef.current = productivityLifecycle;
  }, [productivityLifecycle]);

  useEffect(() => {
    approvalScopeStateRef.current = approvalScopeState;
  }, [approvalScopeState]);

  useEffect(() => {
    scheduledJobsRef.current = scheduledJobs;
  }, [scheduledJobs]);

  useEffect(() => {
    if (
      productivityLifecycle.status === "preview" ||
      productivityLifecycle.status === "failed"
    ) {
      productivityHeadingRef.current?.focus();
    }
  }, [productivityLifecycle.status, productivityLifecycle.proposalId, productivityLifecycle.error]);

  useEffect(() => {
    documentStatusCodeRef.current = documentStatusCode;
  }, [documentStatusCode]);

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

  const endVoiceCaptureSession = useCallback(() => {
    voiceCaptureGenerationRef.current += 1;
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    setRecognitionCaptureActive(false);
    if (recognition) {
      terminateSpeechRecognition(recognition);
    }
    resetVoiceCompanion();
  }, [resetVoiceCompanion]);

  const cancelVoiceCapture = useCallback(() => {
    speechOutputRef.current?.cancel();
    endVoiceCaptureSession();
  }, [endVoiceCaptureSession]);

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
    speechOutputRef.current?.cancel();
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

  const sendDocumentMessage = useCallback((
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
  }, []);

  const prepareDocumentRequest = useCallback((
    path: string,
    provider: string,
    fallbackProvider = "",
  ): boolean => {
    const trimmedPath = path.trim();
    const trimmedProvider = provider.trim();
    const trimmedFallback = fallbackProvider.trim();
    if (!trimmedPath || !trimmedProvider) return false;
    const fields: Record<string, unknown> = {
      path: trimmedPath,
      provider: trimmedProvider,
    };
    if (trimmedFallback) fields.fallback_provider = trimmedFallback;
    documentPreparePendingRef.current = true;
    documentPrepareRequestRef.current = {
      path: trimmedPath,
      provider: trimmedProvider,
      fallbackProvider: trimmedFallback,
    };
    setDocumentPath(trimmedPath);
    setDocumentProvider(trimmedProvider);
    setDocumentFallbackProvider(trimmedFallback);
    setDocumentPreparePending(true);
    setDocumentAwaitingConfirmation(false);
    setDocumentConfirmation(null);
    setDocumentStatus("Checking document access");
    setDocumentStatusCode("");
    setDocumentProgress(0);
    setDocumentCheckpoint("preparing");
    setDocumentExplanation("");
    if (!sendDocumentMessage("document_prepare", fields)) {
      const connected = wsRef.current?.readyState === WebSocket.OPEN;
      failDocumentPrepare(
        connected
          ? "The document request could not be sent."
          : "Connect to HIKARI before using the document reader.",
      );
      return false;
    }
    return true;
  }, [failDocumentPrepare, sendDocumentMessage]);

  const confirmDocumentRequest = useCallback((): boolean => {
    if (!documentAwaitingConfirmationRef.current) return false;
    if (!documentConfirmation) return false;
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
      return true;
    }
    return false;
  }, [documentConfirmation, sendDocumentMessage]);

  const cancelDocumentRequest = useCallback((): boolean => {
    const taskId = documentTaskIdRef.current;
    if (!taskId) return false;
    if (sendDocumentMessage("document_cancel", { task_id: taskId })) {
      documentTaskVoiceOriginRef.current = false;
      documentPreparePendingRef.current = false;
      documentPrepareRequestRef.current = null;
      setDocumentPreparePending(false);
      setDocumentAwaitingConfirmation(false);
      setDocumentStatus("Cancelling document request");
      return true;
    }
    return false;
  }, [sendDocumentMessage]);

  const followUpDocumentRequest = useCallback((
    taskId: string,
    text: string,
  ): boolean => {
    const provider = documentConfirmation?.provider ?? documentProvider.trim();
    const fallbackProvider =
      documentConfirmation?.fallbackProvider ?? documentFallbackProvider.trim();
    if (!taskId || !text.trim() || !provider) return false;
    if (taskId !== documentTaskIdRef.current) return false;
    const fields: Record<string, unknown> = {
      task_id: taskId,
      text: text.trim(),
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
      return true;
    }
    return false;
  }, [
    documentConfirmation,
    documentProvider,
    documentFallbackProvider,
    sendDocumentMessage,
  ]);

  const submitVoiceRequest = useCallback(
    (transcript: string, captureToken: number): boolean => {
      if (
        captureToken !== voiceCaptureGenerationRef.current ||
        !voiceSessionActiveRef.current
      ) {
        return false;
      }
      const trimmed = boundVoiceTranscript(transcript);
      if (!trimmed) {
        cancelVoiceCapture();
        return false;
      }

      const speechControl = parseSpeechControlIntent(trimmed);
      if (speechControl.type !== "none") {
        const speech = speechOutputRef.current;
        if (speechControl.type === "stop") {
          speech?.cancel();
          endVoiceCaptureSession();
          return true;
        }
        if (speechControl.type === "repeat") {
          const last = speech?.getLastVoiceResponse() ?? "";
          endVoiceCaptureSession();
          if (speakResponsesRef.current && last) {
            speech?.speak(last);
          }
          return true;
        }
        if (speechControl.type === "slower") {
          const wasSpeaking = speech?.isSpeaking() ?? false;
          const nextRate = speech?.slower() ?? DEFAULT_SPEECH_RATE;
          setSpeechRate(nextRate);
          saveCompanionPrefs({
            companionType,
            presentation,
            speakResponses: speakResponsesRef.current,
            speechRate: nextRate,
          });
          const last = speech?.getLastVoiceResponse() ?? "";
          endVoiceCaptureSession();
          if (speakResponsesRef.current && last && wasSpeaking) {
            speech?.speak(last);
          }
          return true;
        }
      }

      const statusCode = documentStatusCodeRef.current;
      const canCancelDocument = Boolean(documentTaskIdRef.current) &&
        (!statusCode || !TERMINAL_DOCUMENT_STATUSES.has(statusCode));
      const intent = parseVoiceDocumentIntent(trimmed, {
        awaitingConfirmation: documentAwaitingConfirmationRef.current,
        documentTaskId: documentTaskIdRef.current,
        canCancelDocument,
      });

      if (intent.type === "reject") {
        setDocumentError(intent.message);
        setActiveTab("files");
        cancelVoiceCapture();
        return true;
      }

      if (intent.type === "prepare") {
        documentTaskVoiceOriginRef.current = true;
        setActiveTab("files");
        prepareDocumentRequest(
          intent.path,
          intent.provider,
          intent.fallbackProvider,
        );
        cancelVoiceCapture();
        return true;
      }

      if (intent.type === "confirm") {
        documentTaskVoiceOriginRef.current = true;
        setActiveTab("files");
        const confirmed = confirmDocumentRequest();
        cancelVoiceCapture();
        if (!confirmed) {
          setDocumentError("Document confirmation could not be sent.");
        }
        return true;
      }

      if (intent.type === "cancel") {
        setActiveTab("files");
        const cancelled = cancelDocumentRequest();
        cancelVoiceCapture();
        if (!cancelled) {
          setDocumentError("Document cancellation could not be sent.");
        }
        return true;
      }

      if (intent.type === "follow_up") {
        documentTaskVoiceOriginRef.current = true;
        setActiveTab("files");
        const sent = followUpDocumentRequest(intent.taskId, intent.text);
        cancelVoiceCapture();
        if (!sent) {
          setDocumentError("Document follow-up could not be sent.");
        }
        return true;
      }

      speechOutputRef.current?.cancel();
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
    [
      cancelVoiceCapture,
      companionType,
      endVoiceCaptureSession,
      presentation,
      prepareDocumentRequest,
      confirmDocumentRequest,
      cancelDocumentRequest,
      followUpDocumentRequest,
    ],
  );

  const syncCompanionPrefs = useCallback(
    (type: CompanionType, pres: Presentation) => {
      saveCompanionPrefs({
        companionType: type,
        presentation: pres,
        speakResponses: speakResponsesRef.current,
        speechRate: speechOutputRef.current?.getRate() ?? DEFAULT_SPEECH_RATE,
      });
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

  const persistSpeakResponses = useCallback((enabled: boolean) => {
    setSpeakResponses(enabled);
    speakResponsesRef.current = enabled;
    saveCompanionPrefs({
      companionType,
      presentation,
      speakResponses: enabled,
      speechRate: speechOutputRef.current?.getRate() ?? speechRate,
    });
  }, [companionType, presentation, speechRate]);

  const stopSpeaking = useCallback(() => {
    speechOutputRef.current?.cancel();
  }, []);

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

  const clearEmailDraftForm = useCallback(() => {
    emailDraftPendingRef.current = false;
    emailDraftRequestIdRef.current = null;
    setEmailDraftPending(false);
    setEmailDraftFields(createEmptyEmailDraftFields());
    setEmailDraftValidationCode(undefined);
    setEmailDraftValidationField(undefined);
    setEmailDraftPrepareError(undefined);
  }, []);

  const clearCalendarForm = useCallback(() => {
    calendarPendingRef.current = false;
    calendarRequestIdRef.current = null;
    setCalendarPending(false);
    setCalendarReadFields(createEmptyCalendarReadFields());
    setCalendarDraftFields(createEmptyCalendarDraftFields());
    setCalendarValidationCode(undefined);
    setCalendarValidationField(undefined);
    setCalendarPrepareError(undefined);
  }, []);

  const clearResearchForm = useCallback(() => {
    researchPendingRef.current = false;
    researchRequestIdRef.current = null;
    setResearchPending(false);
    setResearchFields(createEmptyResearchFields());
    setResearchValidationCode(undefined);
    setResearchValidationField(undefined);
    setResearchPrepareError(undefined);
  }, []);

  const clearReminderForm = useCallback(() => {
    reminderPendingRef.current = false;
    reminderRequestIdRef.current = null;
    setReminderPending(false);
    setReminderFields(createEmptyReminderFields());
    setReminderValidationCode(undefined);
    setReminderValidationField(undefined);
    setReminderPrepareError(undefined);
  }, []);

  const clearProductivityLifecycle = useCallback(() => {
    const idle = createInitialProposalLifecycleState();
    productivityLifecycleRef.current = idle;
    setProductivityLifecycle(idle);
    const resetScope = resetApprovalScopeState();
    approvalScopeStateRef.current = resetScope;
    setApprovalScopeState(resetScope);
    setProductivityResearchResult(null);
    setProductivityCalendarResult(null);
    clearEmailDraftForm();
    clearCalendarForm();
    clearResearchForm();
    clearReminderForm();
  }, [clearEmailDraftForm, clearCalendarForm, clearResearchForm, clearReminderForm]);

  const clearScheduledJobsState = useCallback(() => {
    scheduledJobsRef.current = Object.freeze([]);
    setScheduledJobs(Object.freeze([]));
    setScheduledJobsError(undefined);
    setScheduledJobsStatus(undefined);
    schedulePendingRef.current = false;
    scheduleRequestIdRef.current = null;
    setSchedulePending(false);
    setScheduleFields(createEmptyScheduleProposalFields());
    setScheduleValidationCode(undefined);
    setScheduleValidationField(undefined);
    setScheduledResearchResult(null);
    setScheduledCalendarResult(null);
  }, []);

  const reportScheduledJobCreateError = useCallback((code: ScheduledJobErrorCode) => {
    setScheduledJobsError(code);
    setScheduledJobsStatus("Scheduled job was not created.");
  }, []);

  const applyScheduledJobsMessage = useCallback((message: ScheduledJobsServerMessage) => {
    if (message.type === "scheduled_job_research_result") {
      if (!scheduledJobsRef.current.some((job) => job.jobId === message.job_id)) {
        return;
      }
      setScheduledResearchResult(message);
      setScheduledCalendarResult(null);
      setScheduledJobsStatus("Scheduled research completed.");
      setActiveTab("files");
      return;
    }
    if (message.type === "scheduled_job_calendar_result") {
      if (!scheduledJobsRef.current.some((job) => job.jobId === message.job_id)) {
        return;
      }
      setScheduledCalendarResult(message);
      setScheduledResearchResult(null);
      setScheduledJobsStatus("Scheduled calendar read completed.");
      setActiveTab("files");
      return;
    }
    if (message.type === "scheduled_jobs") {
      scheduledJobsRef.current = message.jobs;
      setScheduledJobs(message.jobs);
      setScheduledJobsError(undefined);
      setScheduledJobsStatus("List loaded.");
      return;
    }
    if (message.type === "scheduled_job_update") {
      if (
        schedulePendingRef.current &&
        message.request_id === scheduleRequestIdRef.current
      ) {
        schedulePendingRef.current = false;
        scheduleRequestIdRef.current = null;
        setSchedulePending(false);
        setScheduleFields(createEmptyScheduleProposalFields());
        setScheduleValidationCode(undefined);
        setScheduleValidationField(undefined);
        clearProductivityLifecycle();
      }
      const next = replaceScheduledJobInList(scheduledJobsRef.current, message.job);
      if (!next) {
        return;
      }
      scheduledJobsRef.current = next;
      setScheduledJobs(next);
      setScheduledJobsError(undefined);
      setScheduledJobsStatus("Correlated update received.");
      return;
    }
    if (
      schedulePendingRef.current &&
      message.request_id === scheduleRequestIdRef.current
    ) {
      schedulePendingRef.current = false;
      scheduleRequestIdRef.current = null;
      setSchedulePending(false);
      reportScheduledJobCreateError(message.code);
      return;
    }
    const current = scheduledJobsRef.current.find((job) => job.jobId === message.job_id);
    if (!current) {
      return;
    }
    const cleared = clearScheduledJobPendingControl(current, message.job_id);
    if (cleared && cleared !== current) {
      const next = replaceScheduledJobInList(scheduledJobsRef.current, cleared);
      if (next) {
        scheduledJobsRef.current = next;
        setScheduledJobs(next);
      }
    }
    setScheduledJobsError(message.code);
  }, [clearProductivityLifecycle, reportScheduledJobCreateError]);

  const submitScheduledJobCreate = useCallback(() => {
    if (schedulePendingRef.current || isProductivityPreparePending()) {
      return;
    }
    const state = productivityLifecycleRef.current;
    const action = state.proposal?.actionLabel;
    if (
      state.status !== "preview" ||
      !state.proposalId ||
      (action !== "browser.research" && action !== "calendar.read")
    ) {
      return;
    }
    const boundFields = Object.freeze({
      ...scheduleFields,
      action: action as ScheduleAction,
    });
    const validation = validateScheduleProposalFields(
      boundFields,
      () => BigInt(Date.now()) * BigInt(1_000),
    );
    if (!validation.ok) {
      setScheduleValidationCode(validation.code);
      setScheduleValidationField(validation.field);
      return;
    }
    const requestId = createScheduleRequestId();
    const encoded = requestId
      ? encodeScheduledJobCreate(
          requestId,
          state.proposalId,
          boundFields,
          () => BigInt(Date.now()) * BigInt(1_000),
        )
      : null;
    const ws = wsRef.current;
    if (!encoded || !ws || ws.readyState !== WebSocket.OPEN) {
      setScheduleValidationCode("clock_unavailable");
      setScheduleValidationField("nextRunAt");
      return;
    }
    schedulePendingRef.current = true;
    scheduleRequestIdRef.current = requestId;
    setSchedulePending(true);
    setScheduleValidationCode(undefined);
    setScheduleValidationField(undefined);
    ws.send(JSON.stringify(encoded));
  }, [isProductivityPreparePending, scheduleFields]);

  const resetScheduledJobCreate = useCallback(() => {
    if (schedulePendingRef.current) {
      return;
    }
    const action = productivityLifecycleRef.current.proposal?.actionLabel;
    setScheduleFields(Object.freeze({
      ...createEmptyScheduleProposalFields(),
      action: action === "calendar.read" ? "calendar.read" : "browser.research",
    }));
    setScheduleValidationCode(undefined);
    setScheduleValidationField(undefined);
  }, []);

  const sendScheduledJobControl = useCallback((
    jobId: string,
    control: ScheduledJobControl,
  ) => {
    const current = scheduledJobsRef.current.find((job) => job.jobId === jobId);
    if (!current) {
      return;
    }
    const pending = setScheduledJobPendingControl(current, jobId, control);
    if (!pending) {
      return;
    }
    const encoded =
      control === "pause"
        ? encodeScheduledJobPause(jobId)
        : control === "resume"
          ? encodeScheduledJobResume(jobId)
          : encodeScheduledJobCancel(jobId);
    const ws = wsRef.current;
    if (!encoded || !ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }
    const next = replaceScheduledJobInList(scheduledJobsRef.current, pending);
    if (!next) {
      return;
    }
    scheduledJobsRef.current = next;
    setScheduledJobs(next);
    setScheduledJobsError(undefined);
    setScheduledJobsStatus(
      control === "pause"
        ? "Pause requested."
        : control === "resume"
          ? "Resume requested."
          : "Cancel requested.",
    );
    ws.send(JSON.stringify(encoded));
  }, []);

  const pauseScheduledJob = useCallback((jobId: string) => {
    sendScheduledJobControl(jobId, "pause");
  }, [sendScheduledJobControl]);

  const resumeScheduledJob = useCallback((jobId: string) => {
    sendScheduledJobControl(jobId, "resume");
  }, [sendScheduledJobControl]);

  const cancelScheduledJob = useCallback((jobId: string) => {
    sendScheduledJobControl(jobId, "cancel");
  }, [sendScheduledJobControl]);

  const applyProductivityMessage = useCallback((message: ProductivityServerMessage) => {
    const state = productivityLifecycleRef.current;
    if (message.type === "productivity_confirmation_required") {
      const emailPrepareMatch =
        emailDraftPendingRef.current &&
        emailDraftResponseMatchesRequest(
          emailDraftRequestIdRef.current,
          message.request_id,
        );
      const calendarPrepareMatch =
        calendarPendingRef.current &&
        emailDraftResponseMatchesRequest(
          calendarRequestIdRef.current,
          message.request_id,
        );
      const researchPrepareMatch =
        researchPendingRef.current &&
        researchResponseMatchesRequest(
          researchRequestIdRef.current,
          message.request_id,
        );
      const reminderPrepareMatch =
        reminderPendingRef.current &&
        emailDraftResponseMatchesRequest(
          reminderRequestIdRef.current,
          message.request_id,
        );
      if (message.request_id !== undefined) {
        if (
          !emailPrepareMatch &&
          !calendarPrepareMatch &&
          !researchPrepareMatch &&
          !reminderPrepareMatch
        ) {
          return;
        }
      } else if (isProductivityPreparePending()) {
        return;
      }
      const next = reduceProposalLifecycle(state, {
        type: "preview",
        proposal: {
          proposalId: message.proposal_id,
          heading: message.heading,
          actionLabel: message.action,
          riskLabel: message.risk_label,
          targets: message.targets.map((entry) => ({ ...entry })),
          payload: message.payload.map((entry) => ({ ...entry })),
          expirationLabel: `Expires at ${message.expires_at}`,
        },
      });
      if (next === state) {
        return;
      }
      const scopeState = createApprovalScopeStateFromAllowed(message.allowed_scopes);
      if (!scopeState) {
        return;
      }
      if (emailPrepareMatch) {
        emailDraftPendingRef.current = false;
        emailDraftRequestIdRef.current = null;
        setEmailDraftPending(false);
        setEmailDraftPrepareError(undefined);
        setEmailDraftValidationCode(undefined);
        setEmailDraftValidationField(undefined);
      } else if (calendarPrepareMatch) {
        calendarPendingRef.current = false;
        calendarRequestIdRef.current = null;
        setCalendarPending(false);
        setCalendarPrepareError(undefined);
        setCalendarValidationCode(undefined);
        setCalendarValidationField(undefined);
      } else if (researchPrepareMatch) {
        researchPendingRef.current = false;
        researchRequestIdRef.current = null;
        setResearchPending(false);
        setResearchPrepareError(undefined);
        setResearchValidationCode(undefined);
        setResearchValidationField(undefined);
      } else if (reminderPrepareMatch) {
        reminderPendingRef.current = false;
        reminderRequestIdRef.current = null;
        setReminderPending(false);
        setReminderPrepareError(undefined);
        setReminderValidationCode(undefined);
        setReminderValidationField(undefined);
      }
      setProductivityResearchResult(null);
      setProductivityCalendarResult(null);
      setScheduledResearchResult(null);
      setScheduledCalendarResult(null);
      if (message.action === "browser.research" || message.action === "calendar.read") {
        setScheduleFields(Object.freeze({
          ...createEmptyScheduleProposalFields(),
          action: message.action,
        }));
      }
      productivityLifecycleRef.current = next;
      setProductivityLifecycle(next);
      approvalScopeStateRef.current = scopeState;
      setApprovalScopeState(scopeState);
      setActiveTab("files");
      return;
    }
    if (message.type === "productivity_update") {
      const next = applyProductivityUpdateStatus(
        state,
        message.proposal_id,
        message.status,
      );
      if (next === state) {
        return;
      }
      productivityLifecycleRef.current = next;
      setProductivityLifecycle(next);
      if (TERMINAL_PRODUCTIVITY_STATUSES.has(next.status)) {
        const resetScope = resetApprovalScopeState();
        approvalScopeStateRef.current = resetScope;
        setApprovalScopeState(resetScope);
        clearEmailDraftForm();
        clearCalendarForm();
        clearResearchForm();
        clearReminderForm();
      }
      return;
    }
    if (message.type === "productivity_research_result") {
      if (state.proposalId !== message.proposal_id) {
        return;
      }
      const next = applyProductivityUpdateStatus(
        state,
        message.proposal_id,
        "completed",
      );
      if (next === state && state.status !== "completed") {
        return;
      }
      productivityLifecycleRef.current = next;
      setProductivityLifecycle(next);
      setProductivityResearchResult(message);
      setProductivityCalendarResult(null);
      const resetScope = resetApprovalScopeState();
      approvalScopeStateRef.current = resetScope;
      setApprovalScopeState(resetScope);
      clearEmailDraftForm();
      clearCalendarForm();
      clearResearchForm();
      clearReminderForm();
      setActiveTab("files");
      return;
    }
    if (message.type === "productivity_calendar_result") {
      if (state.proposalId !== message.proposal_id) {
        return;
      }
      const next = applyProductivityUpdateStatus(
        state,
        message.proposal_id,
        "completed",
      );
      if (next === state && state.status !== "completed") {
        return;
      }
      productivityLifecycleRef.current = next;
      setProductivityLifecycle(next);
      setProductivityCalendarResult(message);
      setProductivityResearchResult(null);
      const resetScope = resetApprovalScopeState();
      approvalScopeStateRef.current = resetScope;
      setApprovalScopeState(resetScope);
      clearEmailDraftForm();
      clearCalendarForm();
      clearResearchForm();
      clearReminderForm();
      setActiveTab("files");
      return;
    }
    if (
      emailDraftPendingRef.current &&
      emailDraftResponseMatchesRequest(
        emailDraftRequestIdRef.current,
        message.request_id,
      )
    ) {
      emailDraftPendingRef.current = false;
      emailDraftRequestIdRef.current = null;
      setEmailDraftPending(false);
      setEmailDraftPrepareError(message.code);
      setEmailDraftValidationCode(undefined);
      setEmailDraftValidationField(undefined);
      setActiveTab("files");
      return;
    }
    if (
      calendarPendingRef.current &&
      emailDraftResponseMatchesRequest(
        calendarRequestIdRef.current,
        message.request_id,
      )
    ) {
      calendarPendingRef.current = false;
      calendarRequestIdRef.current = null;
      setCalendarPending(false);
      setCalendarPrepareError(message.code);
      setCalendarValidationCode(undefined);
      setCalendarValidationField(undefined);
      setActiveTab("files");
      return;
    }
    if (
      researchPendingRef.current &&
      researchResponseMatchesRequest(
        researchRequestIdRef.current,
        message.request_id,
      )
    ) {
      researchPendingRef.current = false;
      researchRequestIdRef.current = null;
      setResearchPending(false);
      setResearchPrepareError(message.code);
      setResearchValidationCode(undefined);
      setResearchValidationField(undefined);
      setActiveTab("files");
      return;
    }
    if (
      reminderPendingRef.current &&
      emailDraftResponseMatchesRequest(
        reminderRequestIdRef.current,
        message.request_id,
      )
    ) {
      reminderPendingRef.current = false;
      reminderRequestIdRef.current = null;
      setReminderPending(false);
      setReminderPrepareError(message.code);
      setReminderValidationCode(undefined);
      setReminderValidationField(undefined);
      setActiveTab("files");
      return;
    }
    if (message.request_id !== undefined) {
      return;
    }
    const next = reduceProposalLifecycle(state, {
      type: "fail",
      proposalId: message.proposal_id,
      error: message.code,
    });
    if (next === state) {
      return;
    }
    productivityLifecycleRef.current = next;
    setProductivityLifecycle(next);
    const resetScope = resetApprovalScopeState();
    approvalScopeStateRef.current = resetScope;
    setApprovalScopeState(resetScope);
    clearEmailDraftForm();
    clearCalendarForm();
    clearResearchForm();
    clearReminderForm();
  }, [
    clearEmailDraftForm,
    clearCalendarForm,
    clearResearchForm,
    clearReminderForm,
    isProductivityPreparePending,
  ]);

  const confirmProductivityAction = useCallback(() => {
    const state = productivityLifecycleRef.current;
    if (state.status !== "preview" || !state.proposalId) {
      return;
    }
    const scopeState = approvalScopeStateRef.current;
    if (!isApprovalScopeConfirmReady(scopeState)) {
      return;
    }
    const next = reduceProposalLifecycle(state, {
      type: "confirm",
      proposalId: state.proposalId,
    });
    if (next === state) {
      return;
    }
    const encoded = encodeProductivityConfirm(state.proposalId, scopeState);
    const ws = wsRef.current;
    if (!encoded || !ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }
    productivityLifecycleRef.current = next;
    setProductivityLifecycle(next);
    ws.send(JSON.stringify(encoded));
  }, []);

  const cancelProductivityAction = useCallback(() => {
    const state = productivityLifecycleRef.current;
    if (!state.proposalId) {
      return;
    }
    const next = reduceProposalLifecycle(state, {
      type: "cancel",
      proposalId: state.proposalId,
    });
    if (next === state) {
      return;
    }
    const encoded = encodeProductivityCancel(state.proposalId);
    const ws = wsRef.current;
    if (!encoded || !ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }
    productivityLifecycleRef.current = next;
    setProductivityLifecycle(next);
    const resetScope = resetApprovalScopeState();
    approvalScopeStateRef.current = resetScope;
    setApprovalScopeState(resetScope);
    clearEmailDraftForm();
    clearCalendarForm();
    clearResearchForm();
    clearReminderForm();
    ws.send(JSON.stringify(encoded));
  }, [clearEmailDraftForm, clearCalendarForm, clearResearchForm, clearReminderForm]);

  const submitEmailDraftPrepare = useCallback(() => {
    if (isProductivityPreparePending()) {
      return;
    }
    const validated = validateEmailDraftFields(emailDraftFields);
    if (!validated.ok) {
      setEmailDraftValidationCode(validated.code);
      setEmailDraftValidationField(validated.field);
      setEmailDraftPrepareError(undefined);
      return;
    }
    const requestId = createEmailDraftRequestId();
    const encoded = encodeProductivityEmailDraftPrepare({
      type: "productivity_email_draft_prepare",
      request_id: requestId,
      recipient: validated.fields.recipient,
      subject: validated.fields.subject,
      body: validated.fields.body,
    });
    const ws = wsRef.current;
    if (!encoded || !ws || ws.readyState !== WebSocket.OPEN) {
      setEmailDraftPrepareError("unavailable");
      return;
    }
    setEmailDraftFields(validated.fields);
    setEmailDraftValidationCode(undefined);
    setEmailDraftValidationField(undefined);
    setEmailDraftPrepareError(undefined);
    emailDraftRequestIdRef.current = requestId;
    emailDraftPendingRef.current = true;
    setEmailDraftPending(true);
    setActiveTab("files");
    ws.send(JSON.stringify(encoded));
  }, [emailDraftFields, isProductivityPreparePending]);

  const resetEmailDraftForm = useCallback(() => {
    if (isProductivityPreparePending()) {
      return;
    }
    clearEmailDraftForm();
  }, [clearEmailDraftForm, isProductivityPreparePending]);

  const submitCalendarPrepare = useCallback(() => {
    if (isProductivityPreparePending()) {
      return;
    }
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setCalendarPrepareError("unavailable");
      return;
    }
    if (calendarMode === "read") {
      const validated = validateCalendarReadFields(calendarReadFields);
      if (!validated.ok) {
        setCalendarValidationCode(validated.code);
        setCalendarValidationField(validated.field);
        setCalendarPrepareError(undefined);
        return;
      }
      const requestId = createEmailDraftRequestId();
      const encoded = encodeProductivityCalendarReadPrepare({
        type: "productivity_calendar_read_prepare",
        request_id: requestId,
        start: validated.fields.start,
        end: validated.fields.end,
        ...(validated.fields.calendarName !== undefined
          ? { calendar_name: validated.fields.calendarName }
          : {}),
      });
      if (!encoded) {
        setCalendarPrepareError("unavailable");
        return;
      }
      setCalendarReadFields({
        start: validated.fields.start,
        end: validated.fields.end,
        calendarName: validated.fields.calendarName ?? "",
      });
      setCalendarValidationCode(undefined);
      setCalendarValidationField(undefined);
      setCalendarPrepareError(undefined);
      calendarRequestIdRef.current = requestId;
      calendarPendingRef.current = true;
      setCalendarPending(true);
      setActiveTab("files");
      ws.send(JSON.stringify(encoded));
      return;
    }
    const validated = validateCalendarDraftFields(calendarDraftFields);
    if (!validated.ok) {
      setCalendarValidationCode(validated.code);
      setCalendarValidationField(validated.field);
      setCalendarPrepareError(undefined);
      return;
    }
    const requestId = createEmailDraftRequestId();
    const encoded = encodeProductivityCalendarDraftPrepare({
      type: "productivity_calendar_draft_prepare",
      request_id: requestId,
      title: validated.fields.title,
      start: validated.fields.start,
      end: validated.fields.end,
      calendar_name: validated.fields.calendarName,
      ...(validated.fields.location !== undefined
        ? { location: validated.fields.location }
        : {}),
      ...(validated.fields.notes !== undefined
        ? { notes: validated.fields.notes }
        : {}),
    });
    if (!encoded) {
      setCalendarPrepareError("unavailable");
      return;
    }
    setCalendarDraftFields({
      title: validated.fields.title,
      start: validated.fields.start,
      end: validated.fields.end,
      calendarName: validated.fields.calendarName,
      location: validated.fields.location ?? "",
      notes: validated.fields.notes ?? "",
    });
    setCalendarValidationCode(undefined);
    setCalendarValidationField(undefined);
    setCalendarPrepareError(undefined);
    calendarRequestIdRef.current = requestId;
    calendarPendingRef.current = true;
    setCalendarPending(true);
    setActiveTab("files");
    ws.send(JSON.stringify(encoded));
  }, [calendarMode, calendarReadFields, calendarDraftFields, isProductivityPreparePending]);

  const resetCalendarForm = useCallback(() => {
    if (isProductivityPreparePending()) {
      return;
    }
    clearCalendarForm();
  }, [clearCalendarForm, isProductivityPreparePending]);

  const submitResearchPrepare = useCallback(() => {
    if (isProductivityPreparePending()) {
      return;
    }
    const validated = validateResearchFields(researchFields);
    if (!validated.ok) {
      setResearchValidationCode(validated.code);
      setResearchValidationField(validated.field);
      setResearchPrepareError(undefined);
      return;
    }
    const requestId = createResearchRequestId();
    const encoded = encodeProductivityResearchPrepare({
      type: "productivity_research_prepare",
      request_id: requestId,
      query: validated.fields.query,
      ...(validated.fields.domains !== undefined
        ? { domains: [...validated.fields.domains] }
        : {}),
      ...(validated.fields.maxResults !== undefined
        ? { max_results: validated.fields.maxResults }
        : {}),
    });
    const ws = wsRef.current;
    if (!encoded || !ws || ws.readyState !== WebSocket.OPEN) {
      setResearchPrepareError("unavailable");
      return;
    }
    setResearchFields({
      query: validated.fields.query,
      domainsText: validated.fields.domains?.join("\n") ?? "",
      maxResults: String(validated.fields.maxResults),
    });
    setResearchValidationCode(undefined);
    setResearchValidationField(undefined);
    setResearchPrepareError(undefined);
    researchRequestIdRef.current = requestId;
    researchPendingRef.current = true;
    setResearchPending(true);
    setActiveTab("files");
    ws.send(JSON.stringify(encoded));
  }, [researchFields, isProductivityPreparePending]);

  const resetResearchForm = useCallback(() => {
    if (isProductivityPreparePending()) {
      return;
    }
    clearResearchForm();
  }, [clearResearchForm, isProductivityPreparePending]);

  const submitReminderPrepare = useCallback(() => {
    if (isProductivityPreparePending()) {
      return;
    }
    const validated = validateReminderFields(reminderFields);
    if (!validated.ok) {
      setReminderValidationCode(validated.code);
      setReminderValidationField(validated.field);
      setReminderPrepareError(undefined);
      return;
    }
    const requestId = createEmailDraftRequestId();
    const encoded = encodeProductivityReminderPrepare({
      type: "productivity_reminder_prepare",
      request_id: requestId,
      title: validated.fields.title,
      remind_at: validated.fields.remindAt,
      ...(validated.fields.notes !== undefined
        ? { notes: validated.fields.notes }
        : {}),
      ...(validated.fields.listName !== undefined
        ? { list_name: validated.fields.listName }
        : {}),
    });
    const ws = wsRef.current;
    if (!encoded || !ws || ws.readyState !== WebSocket.OPEN) {
      setReminderPrepareError("unavailable");
      return;
    }
    setReminderFields({
      title: validated.fields.title,
      remindAt: validated.fields.remindAt,
      notes: validated.fields.notes ?? "",
      listName: validated.fields.listName ?? "",
    });
    setReminderValidationCode(undefined);
    setReminderValidationField(undefined);
    setReminderPrepareError(undefined);
    reminderRequestIdRef.current = requestId;
    reminderPendingRef.current = true;
    setReminderPending(true);
    setActiveTab("files");
    ws.send(JSON.stringify(encoded));
  }, [reminderFields, isProductivityPreparePending]);

  const resetReminderForm = useCallback(() => {
    if (isProductivityPreparePending()) {
      return;
    }
    clearReminderForm();
  }, [clearReminderForm, isProductivityPreparePending]);

  const resetPhase4State = useCallback(() => {
    const initPair = createInitialPairingState();
    pairingStateRef.current = initPair;
    setPairingState(initPair);
    pairingPendingRef.current = false;
    setPairingPending(false);

    const initHandoff = createInitialHandoffState();
    handoffStateRef.current = initHandoff;
    setHandoffState(initHandoff);
    handoffPendingRef.current = false;
    handoffRequestIdRef.current = null;
    setHandoffPending(false);

    const initVisual = createInitialVisualTransferState();
    visualTransferStateRef.current = initVisual;
    setVisualTransferState(initVisual);
    visualTransferPendingRef.current = false;
    visualTransferBytesRef.current = null;
    setVisualTransferPending(false);

    const initVision = createInitialVisionAnalysisState();
    visionAnalysisStateRef.current = initVision;
    setVisionAnalysisState(initVision);
    visionRequestIdRef.current = null;
  }, []);

  const applyPhase4ServerMessage = useCallback(
    (msg: Phase4ServerMessage) => {
      switch (msg.type) {
        case "pairing_challenge": {
          if (
            pairingStateRef.current.requestId &&
            msg.request_id !== pairingStateRef.current.requestId
          ) {
            return;
          }
          pairingPendingRef.current = false;
          setPairingPending(false);
          const next = reducePairing(pairingStateRef.current, {
            type: "RECEIVE_CHALLENGE",
            requestId: msg.request_id,
            challengeId: msg.challenge_id,
          });
          pairingStateRef.current = next;
          setPairingState(next);
          pairingHeadingRef.current?.focus();
          break;
        }

        case "pairing_confirmed": {
          if (pairingStateRef.current.requestId !== msg.request_id) {
            return;
          }
          const challengeId = pairingStateRef.current.challengeId;
          if (!challengeId) return;
          pairingPendingRef.current = false;
          setPairingPending(false);
          const next = reducePairing(pairingStateRef.current, {
            type: "CONFIRM_SUCCESS",
            challengeId,
            deviceId: msg.device_id,
          });
          pairingStateRef.current = next;
          setPairingState(next);
          setIsPaired(true);
          break;
        }

        case "pairing_update": {
          if (pairingStateRef.current.requestId !== msg.request_id) {
            return;
          }
          pairingPendingRef.current = false;
          setPairingPending(false);
          let next = pairingStateRef.current;
          if (msg.status === "cancelled") {
            next = reducePairing(next, { type: "CANCEL_COMPLETE" });
          } else if (msg.status === "expired") {
            next = reducePairing(next, { type: "EXPIRE" });
          } else if (msg.status === "revoked") {
            next = reducePairing(next, { type: "REVOKE" });
          }
          pairingStateRef.current = next;
          setPairingState(next);
          break;
        }

        case "pairing_error": {
          if (pairingStateRef.current.requestId !== msg.request_id) {
            return;
          }
          pairingPendingRef.current = false;
          setPairingPending(false);
          const next = reducePairing(pairingStateRef.current, {
            type: "FAIL",
            errorCode: isPairingErrorCode(msg.code) ? msg.code : "unavailable",
          });
          pairingStateRef.current = next;
          setPairingState(next);
          break;
        }

        case "handoff_offer": {
          if (isHandoffPending(handoffStateRef.current.status)) {
            return;
          }
          handoffPendingRef.current = false;
          setHandoffPending(false);
          const next = reduceHandoff(handoffStateRef.current, {
            type: "RECEIVE_OFFER",
            handoffId: msg.handoff_id,
            taskId: msg.task_id,
            summary: msg.summary,
          });
          if (next !== handoffStateRef.current) {
            handoffStateRef.current = next;
            setHandoffState(next);
            setActiveTab("files");
            handoffHeadingRef.current?.focus();
          }
          break;
        }

        case "handoff_update": {
          if (
            handoffStateRef.current.handoffId !== msg.handoff_id ||
            (handoffRequestIdRef.current !== null && handoffRequestIdRef.current !== msg.request_id)
          ) {
            return;
          }
          handoffRequestIdRef.current = null;
          handoffPendingRef.current = false;
          setHandoffPending(false);
          let next = handoffStateRef.current;
          if (msg.status === "accepted") {
            next = reduceHandoff(next, { type: "ACCEPT_COMPLETE", handoffId: msg.handoff_id });
          } else if (msg.status === "rejected") {
            next = reduceHandoff(next, { type: "REJECT_COMPLETE", handoffId: msg.handoff_id });
          } else if (msg.status === "cancelled") {
            next = reduceHandoff(next, { type: "CANCEL_COMPLETE" });
          } else if (msg.status === "expired") {
            next = reduceHandoff(next, { type: "EXPIRE" });
          }
          handoffStateRef.current = next;
          setHandoffState(next);
          break;
        }

        case "handoff_error": {
          if (
            handoffRequestIdRef.current !== msg.request_id ||
            (msg.handoff_id !== undefined && handoffStateRef.current.handoffId !== msg.handoff_id)
          ) {
            return;
          }
          handoffRequestIdRef.current = null;
          handoffPendingRef.current = false;
          setHandoffPending(false);
          const next = reduceHandoff(handoffStateRef.current, {
            type: "FAIL",
            errorCode: isHandoffErrorCode(msg.code) ? msg.code : "unavailable",
          });
          handoffStateRef.current = next;
          setHandoffState(next);
          break;
        }

        case "visual_transfer_ready": {
          if (
            visualTransferStateRef.current.requestId &&
            visualTransferStateRef.current.requestId !== msg.request_id
          ) {
            return;
          }
          visualTransferPendingRef.current = false;
          setVisualTransferPending(false);
          const next = reduceVisualTransfer(visualTransferStateRef.current, {
            type: "SET_READY",
            requestId: msg.request_id,
            transferId: msg.transfer_id,
          });
          visualTransferStateRef.current = next;
          setVisualTransferState(next);
          const binary = visualTransferBytesRef.current;
          if (binary && wsRef.current?.readyState === WebSocket.OPEN) {
            const transferring = reduceVisualTransfer(next, {
              type: "START_TRANSFERRING",
              transferId: msg.transfer_id,
            });
            visualTransferStateRef.current = transferring;
            setVisualTransferState(transferring);
            wsRef.current.send(binary);
            visualTransferBytesRef.current = null;
          }
          visualTransferHeadingRef.current?.focus();
          break;
        }

        case "visual_transfer_update": {
          if (visualTransferStateRef.current.transferId !== msg.transfer_id) {
            return;
          }
          visualTransferPendingRef.current = false;
          setVisualTransferPending(false);
          let next = visualTransferStateRef.current;
          if (msg.status === "receiving") {
            next = reduceVisualTransfer(next, { type: "START_TRANSFERRING", transferId: msg.transfer_id });
          } else if (msg.status === "validating") {
            next = reduceVisualTransfer(next, { type: "VALIDATE", transferId: msg.transfer_id });
          } else if (msg.status === "completed") {
            next = reduceVisualTransfer(next, { type: "TRANSFER_COMPLETE", transferId: msg.transfer_id });
          } else if (msg.status === "cancelled") {
            next = reduceVisualTransfer(next, { type: "CANCEL_COMPLETE" });
          } else if (msg.status === "failed") {
            next = reduceVisualTransfer(next, { type: "FAIL" });
          }
          visualTransferStateRef.current = next;
          setVisualTransferState(next);
          break;
        }

        case "visual_transfer_complete": {
          if (visualTransferStateRef.current.transferId !== msg.transfer_id) {
            return;
          }
          visualTransferPendingRef.current = false;
          setVisualTransferPending(false);
          const next = reduceVisualTransfer(visualTransferStateRef.current, {
            type: "TRANSFER_COMPLETE",
            transferId: msg.transfer_id,
          });
          visualTransferStateRef.current = next;
          setVisualTransferState(next);
          break;
        }

        case "visual_transfer_error": {
          const matchesTransfer =
            msg.transfer_id !== undefined && visualTransferStateRef.current.transferId === msg.transfer_id;
          const matchesBegin =
            msg.transfer_id === undefined && visualTransferStateRef.current.requestId === msg.request_id;
          if (!matchesTransfer && !matchesBegin) {
            return;
          }
          visualTransferBytesRef.current = null;
          visualTransferPendingRef.current = false;
          setVisualTransferPending(false);
          const next = reduceVisualTransfer(visualTransferStateRef.current, {
            type: "FAIL",
            errorCode: isVisualTransferErrorCode(msg.code) ? msg.code : "unavailable",
          });
          visualTransferStateRef.current = next;
          setVisualTransferState(next);
          break;
        }

        case "vision_analysis_ready": {
          if (visionRequestIdRef.current !== msg.request_id) return;
          const next = reduceVisionAnalysis(visionAnalysisStateRef.current, {
            type: "READY_RECEIVED",
            requestId: msg.request_id,
            analysisId: msg.analysis_id,
          });
          if (next === visionAnalysisStateRef.current) return;
          visionAnalysisStateRef.current = next;
          setVisionAnalysisState(next);
          break;
        }

        case "vision_analysis_update": {
          if (visionAnalysisStateRef.current.analysisId !== msg.analysis_id) return;
          let next = visionAnalysisStateRef.current;
          if (msg.state === "analyzing") {
            next = reduceVisionAnalysis(next, {
              type: "ANALYSIS_STARTED",
              requestId: visionAnalysisStateRef.current.requestId ?? msg.request_id,
              analysisId: msg.analysis_id,
            });
          } else if (msg.state === "cancelled") {
            next = reduceVisionAnalysis(next, {
              type: "CANCEL_CONFIRMED",
              analysisId: msg.analysis_id,
            });
          } else if (msg.state === "expired") {
            next = reduceVisionAnalysis(next, { type: "EXPIRED", analysisId: msg.analysis_id });
          }
          if (next !== visionAnalysisStateRef.current) {
            visionAnalysisStateRef.current = next;
            setVisionAnalysisState(next);
          }
          break;
        }

        case "vision_observation": {
          if (
            visionAnalysisStateRef.current.analysisId !== msg.analysis_id ||
            visionAnalysisStateRef.current.requestId !== msg.request_id
          ) return;
          const next = reduceVisionAnalysis(visionAnalysisStateRef.current, {
            type: "OBSERVATION_RECEIVED",
            requestId: msg.request_id,
            analysisId: msg.analysis_id,
            observations: msg.observations,
          });
          if (next !== visionAnalysisStateRef.current) {
            visionAnalysisStateRef.current = next;
            setVisionAnalysisState(next);
          }
          break;
        }

        case "vision_analysis_error": {
          const state = visionAnalysisStateRef.current;
          const matchesPrepare = state.analysisId === null && state.requestId === msg.request_id;
          const matchesAnalysis = msg.analysis_id !== undefined && state.analysisId === msg.analysis_id;
          if (!matchesPrepare && !matchesAnalysis) return;
          const next = reduceVisionAnalysis(state, {
            type: "SAFE_ERROR",
            analysisId: msg.analysis_id,
            errorCode: isVisionAnalysisErrorCode(msg.code) ? msg.code : "unavailable",
          });
          if (next !== state) {
            visionAnalysisStateRef.current = next;
            setVisionAnalysisState(next);
          }
          break;
        }
      }
    },
    [],
  );

  const startPairing = useCallback(() => {
    if (pairingPendingRef.current || isPairingPending(pairingStateRef.current.status)) {
      return;
    }
    const requestId = createCanonicalRequestId("pair");
    pairingPendingRef.current = true;
    setPairingPending(true);

    const next = reducePairing(pairingStateRef.current, {
      type: "START_PREPARING",
      requestId,
    });
    pairingStateRef.current = next;
    setPairingState(next);

    const encoded = encodePairingPrepare(requestId);
    if (encoded && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(encoded));
    }
  }, []);

  const confirmPairingCode = useCallback((code: string) => {
    if (pairingPendingRef.current || pairingStateRef.current.status !== "challenge") {
      return;
    }
    const { requestId, challengeId } = pairingStateRef.current;
    if (!requestId || !challengeId) {
      return;
    }
    pairingPendingRef.current = true;
    setPairingPending(true);

    const next = reducePairing(pairingStateRef.current, {
      type: "SUBMIT_CONFIRM",
      challengeId,
    });
    pairingStateRef.current = next;
    setPairingState(next);

    const encoded = encodePairingConfirm(requestId, challengeId, code);
    if (encoded && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(encoded));
    }
  }, []);

  const cancelPairingAction = useCallback(() => {
    if (pairingStateRef.current.status === "idle" || isPairingTerminal(pairingStateRef.current.status)) {
      return;
    }
    const { requestId, challengeId } = pairingStateRef.current;
    if (!requestId || !challengeId) return;
    const next = reducePairing(pairingStateRef.current, { type: "CANCEL" });
    pairingStateRef.current = next;
    setPairingState(next);
    pairingPendingRef.current = true;
    setPairingPending(true);

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      const encoded = encodePairingCancel(requestId, challengeId);
      if (encoded) {
        wsRef.current.send(JSON.stringify(encoded));
      }
    }
  }, []);

  const acceptHandoffAction = useCallback(() => {
    if (
      handoffPendingRef.current ||
      handoffStateRef.current.status !== "offered" ||
      !handoffStateRef.current.acknowledged
    ) {
      return;
    }
    const handoffId = handoffStateRef.current.handoffId;
    if (!handoffId) return;

    const requestId = createCanonicalRequestId("hoff");
    handoffRequestIdRef.current = requestId;
    handoffPendingRef.current = true;
    setHandoffPending(true);

    const next = reduceHandoff(handoffStateRef.current, {
      type: "ACCEPT",
      handoffId,
    });
    handoffStateRef.current = next;
    setHandoffState(next);

    const encoded = encodeHandoffAccept(requestId, handoffId);
    if (encoded && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(encoded));
    }
  }, []);

  const rejectHandoffAction = useCallback(() => {
    if (handoffPendingRef.current || handoffStateRef.current.status !== "offered") {
      return;
    }
    const handoffId = handoffStateRef.current.handoffId;
    if (!handoffId) return;

    const requestId = createCanonicalRequestId("hoff");
    handoffRequestIdRef.current = requestId;
    handoffPendingRef.current = true;
    setHandoffPending(true);

    const next = reduceHandoff(handoffStateRef.current, {
      type: "REJECT",
      handoffId,
    });
    handoffStateRef.current = next;
    setHandoffState(next);

    const encoded = encodeHandoffReject(requestId, handoffId);
    if (encoded && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(encoded));
    }
  }, []);

  const cancelHandoffAction = useCallback(() => {
    if (
      handoffPendingRef.current ||
      handoffStateRef.current.status === "idle" ||
      isHandoffTerminal(handoffStateRef.current.status)
    ) {
      return;
    }
    const handoffId = handoffStateRef.current.handoffId;
    if (!handoffId) return;

    const requestId = createCanonicalRequestId("hoff");
    handoffRequestIdRef.current = requestId;
    const next = reduceHandoff(handoffStateRef.current, { type: "CANCEL" });
    handoffStateRef.current = next;
    setHandoffState(next);
    handoffPendingRef.current = true;
    setHandoffPending(true);

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      const encoded = encodeHandoffCancel(requestId, handoffId);
      if (encoded) {
        wsRef.current.send(JSON.stringify(encoded));
      }
    }
  }, []);

  const toggleHandoffAck = useCallback((acknowledged: boolean) => {
    const next = reduceHandoff(handoffStateRef.current, {
      type: "TOGGLE_ACKNOWLEDGE",
      acknowledged,
    });
    handoffStateRef.current = next;
    setHandoffState(next);
  }, []);

  const selectVisualTransferFileAction = useCallback((file: Blob) => {
    if (visualTransferPendingRef.current || isVisualTransferPending(visualTransferStateRef.current.status)) {
      return;
    }
    const next = reduceVisualTransfer(visualTransferStateRef.current, {
      type: "SELECT_FILE",
      file,
    });
    visualTransferStateRef.current = next;
    setVisualTransferState(next);
  }, []);

  const beginVisualTransferAction = useCallback(async (file: Blob) => {
    if (
      visualTransferPendingRef.current ||
      visualTransferStateRef.current.status !== "selected" ||
      !visualTransferStateRef.current.fileRef
    ) {
      return;
    }
    const val = validateImageFile(file);
    if (!val.valid) return;

    const handoffId = handoffStateRef.current.handoffId;
    if (handoffStateRef.current.status !== "accepted" || !handoffId) return;
    let binary: ArrayBuffer;
    try {
      binary = await file.arrayBuffer();
    } catch {
      return;
    }
    if (binary.byteLength !== file.size) return;
    const dimensions = inspectImageDimensions(binary, file.type);
    if (!dimensions) return;

    const requestId = createCanonicalRequestId("vtx");
    visualTransferPendingRef.current = true;
    visualTransferBytesRef.current = binary;
    setVisualTransferPending(true);

    const next = reduceVisualTransfer(visualTransferStateRef.current, {
      type: "BEGIN_TRANSFER",
      requestId,
    });
    visualTransferStateRef.current = next;
    setVisualTransferState(next);

    const encoded = encodeVisualTransferBegin(
      requestId,
      handoffId,
      file.type,
      file.size,
      dimensions.width,
      dimensions.height,
      visionAnalysisStateRef.current.status === "awaiting_image"
        ? visionAnalysisStateRef.current.analysisId ?? undefined
        : undefined,
    );
    if (encoded && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(encoded));
    }
  }, []);

  const startVisionAnalysisAction = useCallback((capability: VisionCapability) => {
    const handoffId = handoffStateRef.current.handoffId;
    if (
      handoffStateRef.current.status !== "accepted" ||
      !handoffId ||
      isVisionAnalysisPending(visionAnalysisStateRef.current.status)
    ) return;
    if (visionAnalysisStateRef.current.status !== "idle") {
      const reset = createInitialVisionAnalysisState();
      visionAnalysisStateRef.current = reset;
      setVisionAnalysisState(reset);
    }
    const requestId = createCanonicalRequestId("vis");
    const encoded = encodeVisionAnalysisPrepare(requestId, handoffId, capability);
    if (!encoded || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const next = reduceVisionAnalysis(visionAnalysisStateRef.current, {
      type: "PREPARE_REQUESTED",
      requestId,
      capability,
      handoffId,
    });
    visionAnalysisStateRef.current = next;
    visionRequestIdRef.current = requestId;
    setVisionAnalysisState(next);
    wsRef.current.send(JSON.stringify(encoded));
  }, []);

  const cancelVisionAnalysisAction = useCallback(() => {
    const state = visionAnalysisStateRef.current;
    if (!isVisionAnalysisPending(state.status) || state.cancelPending || !state.analysisId) return;
    const requestId = createCanonicalRequestId("vis");
    const encoded = encodeVisionAnalysisCancel(requestId, state.analysisId);
    if (!encoded || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const next = reduceVisionAnalysis(state, { type: "CANCEL_REQUESTED" });
    visionAnalysisStateRef.current = next;
    setVisionAnalysisState(next);
    wsRef.current.send(JSON.stringify(encoded));
  }, []);

  const cancelVisualTransferAction = useCallback(() => {
    if (
      visualTransferStateRef.current.status === "idle" ||
      isVisualTransferTerminal(visualTransferStateRef.current.status)
    ) {
      return;
    }
    const transferId = visualTransferStateRef.current.transferId;
    if (!transferId) {
      if (visualTransferStateRef.current.status === "selected") {
        let local = reduceVisualTransfer(visualTransferStateRef.current, { type: "CANCEL" });
        local = reduceVisualTransfer(local, { type: "CANCEL_COMPLETE" });
        visualTransferStateRef.current = local;
        setVisualTransferState(local);
        visualTransferBytesRef.current = null;
      }
      return;
    }
    const requestId = createCanonicalRequestId("vtx");

    const next = reduceVisualTransfer(visualTransferStateRef.current, { type: "CANCEL" });
    visualTransferStateRef.current = next;
    setVisualTransferState(next);
    visualTransferPendingRef.current = true;
    visualTransferBytesRef.current = null;
    setVisualTransferPending(true);

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      const encoded = encodeVisualTransferCancel(requestId, transferId);
      if (encoded) {
        wsRef.current.send(JSON.stringify(encoded));
      }
    }
  }, []);

  useEffect(() => {
    if (calendarPrepareError) {
      calendarPrepareErrorHeadingRef.current?.focus();
    }
  }, [calendarPrepareError]);

  useEffect(() => {
    if (researchPrepareError) {
      researchPrepareErrorHeadingRef.current?.focus();
    }
  }, [researchPrepareError]);

  useEffect(() => {
    if (reminderPrepareError) {
      reminderPrepareErrorHeadingRef.current?.focus();
    }
  }, [reminderPrepareError]);

  useEffect(() => {
    if (productivityResearchResult) {
      researchResultHeadingRef.current?.focus();
    }
  }, [productivityResearchResult]);

  useEffect(() => {
    if (productivityCalendarResult) {
      calendarResultHeadingRef.current?.focus();
    }
  }, [productivityCalendarResult]);

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

    const initializePairedConnection = () => {
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
      ws.send(JSON.stringify(encodeScheduledJobsList()));
      addMessage("Connected to HIKARI! Ask me anything.", "ai");
    };

    ws.onmessage = (event) => {
      const phase4Message = parsePhase4ServerMessage(event.data);
      if (phase4Message) {
        applyPhase4ServerMessage(phase4Message);
        if (phase4Message.type === "pairing_confirmed") {
          initializePairedConnection();
        }
        return;
      }
      const productivityMessage = parseProductivityServerMessage(event.data);
      if (productivityMessage) {
        applyProductivityMessage(productivityMessage);
        return;
      }
      const scheduledJobsMessage = parseScheduledJobsServerMessage(event.data);
      if (scheduledJobsMessage) {
        applyScheduledJobsMessage(scheduledJobsMessage);
        return;
      }
      const frameType = parseWebSocketFrameType(event.data);
      if (
        frameType !== null &&
        isStrictDedicatedServerMessageType(frameType)
      ) {
        return;
      }
      const data = parseServerMessage(event.data);
      if (!data) return;
      if (data.type === "paired") {
        initializePairedConnection();
      } else if (data.type === "companion_update" && data.companion) {
        applyCompanionUpdate(data.companion as Record<string, unknown>);
      } else if (data.type === "response") {
        setIsTyping(false);
        const responseText = stringField(data, "text");
        const fromVoice =
          voiceTurnActiveRef.current || voiceSessionActiveRef.current;
        addMessage(responseText, "ai");
        if (fromVoice && responseText) {
          speechOutputRef.current?.rememberVoiceResponse(responseText);
          if (speakResponsesRef.current) {
            speechOutputRef.current?.speak(responseText);
          }
        }
        if (fromVoice) {
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
          const shouldSpeak = documentTaskVoiceOriginRef.current;
          documentTaskVoiceOriginRef.current = false;
          if (shouldSpeak) {
            speechOutputRef.current?.rememberVoiceResponse(text);
            if (speakResponsesRef.current) {
              speechOutputRef.current?.speak(text);
            }
          }
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
        setInterfaceError("Unsupported server protocol");
      } else if (data.type === "error") {
        setInterfaceError("Server request failed");
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
      clearProductivityLifecycle();
      clearScheduledJobsState();
      resetPhase4State();
      speechOutputRef.current?.cancel();
      speechOutputRef.current?.clearLastVoiceResponse();
      cancelVoiceCapture();
      setIsConnected(false);
      setIsPaired(false);
      setTimeout(connect, 3000);
    };
  }, [serverUrl, pairingCode, applyCompanionUpdate, syncCompanionPrefs, resetVoiceCompanion, cancelVoiceCapture, forgetDocumentTask, rememberDocumentTask, failDocumentPrepare, applyProductivityMessage, clearProductivityLifecycle, applyScheduledJobsMessage, clearScheduledJobsState, applyPhase4ServerMessage, resetPhase4State]);

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

  const prepareDocument = () => {
    documentTaskVoiceOriginRef.current = false;
    documentTaskVoiceOriginRef.current = false;
    prepareDocumentRequest(
      documentPath,
      documentProvider,
      documentFallbackProvider,
    );
  };

  const confirmDocument = () => {
    confirmDocumentRequest();
  };

  const cancelDocument = () => {
    cancelDocumentRequest();
  };

  const sendDocumentFollowUp = () => {
    if (!documentTaskId) return;
    documentTaskVoiceOriginRef.current = false;
    followUpDocumentRequest(documentTaskId, documentFollowUp);
  };

  const documentRequestLocked = documentPreparePending || documentAwaitingConfirmation;
  const canCancelDocument = Boolean(documentTaskId) &&
    (!documentStatusCode || !TERMINAL_DOCUMENT_STATUSES.has(documentStatusCode));
  const productivityProposal = productivityLifecycle.proposal;
  const productivityPreparePending =
    emailDraftPending || calendarPending || researchPending || reminderPending;
  const productivityPending =
    productivityLifecycle.status === "confirming" ||
    productivityLifecycle.status === "cancelling" ||
    productivityLifecycle.status === "completed" ||
    productivityLifecycle.status === "failed" ||
    productivityLifecycle.status === "cancelled";
  const productivityConfirmDisabled =
    productivityLifecycle.status !== "preview" ||
    !isApprovalScopeConfirmReady(approvalScopeState);
  const productivityCancelDisabled = !(
    productivityLifecycle.status === "preview" ||
    productivityLifecycle.status === "approved" ||
    productivityLifecycle.status === "executing"
  );

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
              placeholder="ABC123DEF4"
              maxLength={10}
              className="w-full bg-[#1a1a2e] border border-gray-700 rounded-xl px-4 py-3 text-white text-center text-2xl tracking-[0.5em] placeholder-gray-600 focus:outline-none focus:border-purple-500 transition"
            />
          </div>
          <button
            onClick={connect}
            disabled={!serverUrl || isConnected}
            className="w-full bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3 rounded-xl transition-all duration-200"
          >
            Connect
          </button>
          {isConnected && (
            <Phase4PairingPanel
              state={pairingState}
              onStartPairing={startPairing}
              onConfirm={confirmPairingCode}
              onCancel={cancelPairingAction}
              headingRef={pairingHeadingRef}
            />
          )}
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
            <Phase4PairingPanel
              state={pairingState}
              onStartPairing={startPairing}
              onConfirm={confirmPairingCode}
              onCancel={cancelPairingAction}
              headingRef={pairingHeadingRef}
            />

            <HandoffOfferPanel
              state={handoffState}
              onAccept={acceptHandoffAction}
              onReject={rejectHandoffAction}
              onCancel={cancelHandoffAction}
              onToggleAcknowledge={toggleHandoffAck}
              headingRef={handoffHeadingRef}
            />

            <VisualTransferPanel
              state={visualTransferState}
              onSelectFile={selectVisualTransferFileAction}
              onBeginTransfer={beginVisualTransferAction}
              onCancel={cancelVisualTransferAction}
              headingRef={visualTransferHeadingRef}
            />
            <VisionAnalysisPanel
              state={visionAnalysisState}
              onStartAnalysis={startVisionAnalysisAction}
              onCancelAnalysis={cancelVisionAnalysisAction}
            />
            {handoffState.status === "accepted" &&
              visionAnalysisState.status === "awaiting_image" && (
                <CameraCapturePanel
                  onFrameCaptured={selectVisualTransferFileAction}
                />
              )}
            {productivityProposal && (
              <>
                <ProductivityActionPreview
                  proposal={{
                    proposalId: productivityProposal.proposalId,
                    heading: productivityProposal.heading,
                    actionLabel: productivityProposal.actionLabel,
                    riskLabel: productivityProposal.riskLabel,
                    targets: productivityProposal.targets,
                    payload: productivityProposal.payload,
                    expirationLabel: productivityProposal.expirationLabel,
                  }}
                  pending={productivityPending}
                  confirmDisabled={productivityConfirmDisabled}
                  cancelDisabled={productivityCancelDisabled}
                  liveStatus={productivityLiveStatus(productivityLifecycle.status)}
                  error={
                    productivityLifecycle.status === "failed"
                      ? productivityLifecycle.error
                      : undefined
                  }
                  onConfirm={confirmProductivityAction}
                  onCancel={cancelProductivityAction}
                  headingRef={productivityHeadingRef}
                />
                {productivityLifecycle.status === "preview" ||
                productivityLifecycle.status === "confirming" ||
                productivityLifecycle.status === "approved" ||
                productivityLifecycle.status === "executing" ||
                productivityLifecycle.status === "cancelling" ? (
                  <ApprovalScopeSelector
                    state={approvalScopeState}
                    onChange={(next) => {
                      approvalScopeStateRef.current = next;
                      setApprovalScopeState(next);
                    }}
                    disabled={productivityPending}
                  />
                ) : null}
              </>
            )}

            {productivityLifecycle.status === "preview" &&
            productivityProposal &&
            (productivityProposal.actionLabel === "browser.research" ||
              productivityProposal.actionLabel === "calendar.read") ? (
              <ScheduledJobCreateForm
                fields={Object.freeze({
                  ...scheduleFields,
                  action: productivityProposal.actionLabel as ScheduleAction,
                })}
                pending={schedulePending}
                disabled={productivityPreparePending}
                actionLocked
                validationCode={scheduleValidationCode}
                validationField={scheduleValidationField}
                onChange={(next) => {
                  setScheduleFields(Object.freeze({
                    ...next,
                    action: productivityProposal.actionLabel as ScheduleAction,
                  }));
                  setScheduleValidationCode(undefined);
                  setScheduleValidationField(undefined);
                  setScheduledJobsError(undefined);
                }}
                onSubmit={submitScheduledJobCreate}
                onReset={resetScheduledJobCreate}
              />
            ) : null}

            {productivityResearchResult ? (
              <section
                className="rounded-lg border border-gray-700 bg-gray-950/40 p-4"
                aria-labelledby="productivity-research-result-heading"
              >
                <h2
                  id="productivity-research-result-heading"
                  ref={researchResultHeadingRef}
                  tabIndex={-1}
                  className="text-sm font-semibold text-gray-100"
                >
                  Research results
                </h2>
                <p
                  className="mt-2 text-sm text-gray-300"
                  role="status"
                  aria-live="polite"
                >
                  Research completed.
                </p>
                {productivityResearchResult.items.length === 0 ? (
                  <p className="mt-3 text-sm text-gray-400">No results.</p>
                ) : (
                  <ul className="mt-3 list-disc space-y-3 pl-5">
                    {productivityResearchResult.items.map((item) => (
                      <li key={`${item.domain}:${item.url}`} className="text-sm text-gray-200">
                        <a
                          href={item.url}
                          className="font-medium text-sky-300 underline underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                          rel="noopener noreferrer"
                          target="_blank"
                        >
                          {item.title}
                        </a>
                        <span className="ml-2 text-gray-400">({item.domain})</span>
                        {item.snippet ? (
                          <p className="mt-1 whitespace-pre-wrap text-gray-300">{item.snippet}</p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            ) : null}

            {productivityCalendarResult ? (
              <section
                className="rounded-lg border border-gray-700 bg-gray-950/40 p-4"
                aria-labelledby="productivity-calendar-result-heading"
              >
                <h2
                  id="productivity-calendar-result-heading"
                  ref={calendarResultHeadingRef}
                  tabIndex={-1}
                  className="text-sm font-semibold text-gray-100"
                >
                  Calendar results
                </h2>
                <p
                  className="mt-2 text-sm text-gray-300"
                  role="status"
                  aria-live="polite"
                >
                  Calendar read completed.
                </p>
                {productivityCalendarResult.events.length === 0 ? (
                  <p className="mt-3 text-sm text-gray-400">No events.</p>
                ) : (
                  <ul className="mt-3 list-disc space-y-3 pl-5">
                    {productivityCalendarResult.events.map((event) => (
                      <li
                        key={`${event.calendar}:${event.start}:${event.end}:${event.title}`}
                        className="text-sm text-gray-200"
                      >
                        <p className="font-medium text-gray-100">{event.title}</p>
                        <p className="mt-1 text-gray-400">
                          {event.start} – {event.end}
                        </p>
                        <p className="mt-1 text-gray-400">Calendar: {event.calendar}</p>
                        {event.location ? (
                          <p className="mt-1 whitespace-pre-wrap text-gray-300">
                            Location: {event.location}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            ) : null}

            <EmailDraftProposal
              fields={emailDraftFields}
              pending={productivityPreparePending}
              disabled={
                productivityPreparePending ||
                productivityLifecycle.status === "preview" ||
                productivityLifecycle.status === "confirming" ||
                productivityLifecycle.status === "approved" ||
                productivityLifecycle.status === "executing" ||
                productivityLifecycle.status === "cancelling"
              }
              validationCode={emailDraftValidationCode}
              validationField={emailDraftValidationField}
              prepareError={emailDraftPrepareError}
              onChange={(next) => {
                setEmailDraftFields(next);
                setEmailDraftValidationCode(undefined);
                setEmailDraftValidationField(undefined);
                setEmailDraftPrepareError(undefined);
              }}
              onSubmit={submitEmailDraftPrepare}
              onReset={resetEmailDraftForm}
              headingRef={emailDraftHeadingRef}
            />

            <div>
              <CalendarProposalForm
                mode={calendarMode}
                readFields={calendarReadFields}
                draftFields={calendarDraftFields}
                pending={productivityPreparePending}
                disabled={
                  productivityPreparePending ||
                  productivityLifecycle.status === "preview" ||
                  productivityLifecycle.status === "confirming" ||
                  productivityLifecycle.status === "approved" ||
                  productivityLifecycle.status === "executing" ||
                  productivityLifecycle.status === "cancelling"
                }
                validationCode={calendarValidationCode}
                validationField={calendarValidationField}
                onModeChange={(nextMode) => {
                  if (isProductivityPreparePending()) {
                    return;
                  }
                  setCalendarMode(nextMode);
                  setCalendarValidationCode(undefined);
                  setCalendarValidationField(undefined);
                  setCalendarPrepareError(undefined);
                }}
                onReadChange={(next) => {
                  setCalendarReadFields(next);
                  setCalendarValidationCode(undefined);
                  setCalendarValidationField(undefined);
                  setCalendarPrepareError(undefined);
                }}
                onDraftChange={(next) => {
                  setCalendarDraftFields(next);
                  setCalendarValidationCode(undefined);
                  setCalendarValidationField(undefined);
                  setCalendarPrepareError(undefined);
                }}
                onSubmit={submitCalendarPrepare}
                onReset={resetCalendarForm}
                headingRef={calendarHeadingRef}
              />
              {calendarPrepareError ? (
                <div
                  className="mt-4 rounded-lg border border-red-800 bg-red-950/40 p-3"
                  role="alert"
                >
                  <h3
                    ref={calendarPrepareErrorHeadingRef}
                    tabIndex={-1}
                    className="text-sm font-semibold text-red-200"
                  >
                    Calendar prepare failed
                  </h3>
                  <p className="mt-1 text-sm text-red-100">
                    {mapPreviewErrorMessage(calendarPrepareError)}
                  </p>
                </div>
              ) : null}
            </div>

            <div>
              <ResearchProposalForm
                fields={researchFields}
                pending={productivityPreparePending}
                disabled={
                  productivityPreparePending ||
                  productivityLifecycle.status === "preview" ||
                  productivityLifecycle.status === "confirming" ||
                  productivityLifecycle.status === "approved" ||
                  productivityLifecycle.status === "executing" ||
                  productivityLifecycle.status === "cancelling"
                }
                validationCode={researchValidationCode}
                validationField={researchValidationField}
                onChange={(next) => {
                  setResearchFields(next);
                  setResearchValidationCode(undefined);
                  setResearchValidationField(undefined);
                  setResearchPrepareError(undefined);
                }}
                onSubmit={submitResearchPrepare}
                onReset={resetResearchForm}
                headingRef={researchHeadingRef}
              />
              {researchPrepareError ? (
                <div
                  className="mt-4 rounded-lg border border-red-800 bg-red-950/40 p-3"
                  role="alert"
                >
                  <h3
                    ref={researchPrepareErrorHeadingRef}
                    tabIndex={-1}
                    className="text-sm font-semibold text-red-200"
                  >
                    Research prepare failed
                  </h3>
                  <p className="mt-1 text-sm text-red-100">
                    {mapPreviewErrorMessage(researchPrepareError)}
                  </p>
                </div>
              ) : null}
            </div>

            <div>
              <ReminderProposalForm
                fields={reminderFields}
                pending={productivityPreparePending}
                disabled={
                  productivityPreparePending ||
                  productivityLifecycle.status === "preview" ||
                  productivityLifecycle.status === "confirming" ||
                  productivityLifecycle.status === "approved" ||
                  productivityLifecycle.status === "executing" ||
                  productivityLifecycle.status === "cancelling"
                }
                validationCode={reminderValidationCode}
                validationField={reminderValidationField}
                onChange={(next) => {
                  setReminderFields(next);
                  setReminderValidationCode(undefined);
                  setReminderValidationField(undefined);
                  setReminderPrepareError(undefined);
                }}
                onSubmit={submitReminderPrepare}
                onReset={resetReminderForm}
                headingRef={reminderHeadingRef}
              />
              {reminderPrepareError ? (
                <div
                  className="mt-4 rounded-lg border border-red-800 bg-red-950/40 p-3"
                  role="alert"
                >
                  <h3
                    ref={reminderPrepareErrorHeadingRef}
                    tabIndex={-1}
                    className="text-sm font-semibold text-red-200"
                  >
                    Reminder prepare failed
                  </h3>
                  <p className="mt-1 text-sm text-red-100">
                    {mapPreviewErrorMessage(reminderPrepareError)}
                  </p>
                </div>
              ) : null}
            </div>

            {scheduledResearchResult ? (
              <section
                className="rounded-lg border border-gray-700 bg-gray-950/40 p-4"
                aria-labelledby="scheduled-research-result-heading"
              >
                <h2 id="scheduled-research-result-heading" className="text-sm font-semibold text-gray-100">
                  Scheduled research results
                </h2>
                <p className="mt-2 text-sm text-gray-300" role="status" aria-live="polite">
                  Scheduled research completed.
                </p>
                {scheduledResearchResult.items.length === 0 ? (
                  <p className="mt-3 text-sm text-gray-400">No results.</p>
                ) : (
                  <ul className="mt-3 list-disc space-y-3 pl-5">
                    {scheduledResearchResult.items.map((item) => (
                      <li key={`${item.domain}:${item.url}`} className="text-sm text-gray-200">
                        <a
                          href={item.url}
                          className="font-medium text-sky-300 underline underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                          rel="noopener noreferrer"
                          target="_blank"
                        >
                          {item.title}
                        </a>
                        <span className="ml-2 text-gray-400">({item.domain})</span>
                        {item.snippet ? (
                          <p className="mt-1 whitespace-pre-wrap text-gray-300">{item.snippet}</p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            ) : null}

            {scheduledCalendarResult ? (
              <section
                className="rounded-lg border border-gray-700 bg-gray-950/40 p-4"
                aria-labelledby="scheduled-calendar-result-heading"
              >
                <h2 id="scheduled-calendar-result-heading" className="text-sm font-semibold text-gray-100">
                  Scheduled calendar results
                </h2>
                <p className="mt-2 text-sm text-gray-300" role="status" aria-live="polite">
                  Scheduled calendar read completed.
                </p>
                {scheduledCalendarResult.events.length === 0 ? (
                  <p className="mt-3 text-sm text-gray-400">No events.</p>
                ) : (
                  <ul className="mt-3 list-disc space-y-3 pl-5">
                    {scheduledCalendarResult.events.map((event) => (
                      <li key={`${event.calendar}:${event.start}:${event.end}:${event.title}`} className="text-sm text-gray-200">
                        <p className="font-medium text-gray-100">{event.title}</p>
                        <p className="mt-1 text-gray-400">{event.start} – {event.end}</p>
                        <p className="mt-1 text-gray-400">Calendar: {event.calendar}</p>
                        {event.location ? (
                          <p className="mt-1 whitespace-pre-wrap text-gray-300">Location: {event.location}</p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            ) : null}

            <ScheduledJobsPanel
              jobs={scheduledJobs}
              statusMessage={scheduledJobsStatus}
              error={scheduledJobsError}
              onPause={pauseScheduledJob}
              onResume={resumeScheduledJob}
              onCancel={cancelScheduledJob}
            />

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
                speakResponses={speakResponses}
                speechRate={speechRate}
                isSpeaking={isSpeakingAloud}
                onChange={(type, pres) => {
                  setCompanionType(type);
                  setPresentation(pres);
                }}
                onSpeakResponsesChange={persistSpeakResponses}
                onStopSpeaking={stopSpeaking}
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
                  speechOutputRef.current?.cancel();
                  speechOutputRef.current?.clearLastVoiceResponse();
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
        isSpeakingAloud={isSpeakingAloud}
        onStopSpeaking={stopSpeaking}
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
