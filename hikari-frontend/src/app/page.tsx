"use client";

import { useState, useEffect, useRef, useCallback } from "react";

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

type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onstart: (() => void) | null;
  onresult: ((event: SpeechRecognitionResultEvent) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
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

  const wsRef = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const connect = useCallback(() => {
    if (!serverUrl) return;

    const ws = new WebSocket(serverUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      if (pairingCode) {
        ws.send(JSON.stringify({
          type: "pair",
          code: pairingCode,
          device_type: "web",
        }));
      }
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "paired") {
        setIsPaired(true);
        addMessage("Connected to HIKARI! Ask me anything.", "ai");
      } else if (data.type === "response") {
        setIsTyping(false);
        setOrbState("speaking");
        addMessage(data.text, "ai");
        setTimeout(() => setOrbState("idle"), 2000);
      } else if (data.type === "pair_error") {
        alert("Invalid pairing code");
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      setIsPaired(false);
      setTimeout(connect, 3000);
    };
  }, [serverUrl, pairingCode]);

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

    addMessage(input, "user");
    wsRef.current.send(JSON.stringify({ type: "message", text: input }));
    setIsTyping(true);
    setOrbState("thinking");
    setInput("");
  };

  const startListening = () => {
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

      // Auto-send
      if (transcript.trim()) {
        setTimeout(() => {
          setInput(transcript);
          // We need to send after state update
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            addMessage(transcript, "user");
            wsRef.current.send(JSON.stringify({ type: "message", text: transcript }));
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
            className={`p-2.5 rounded-full transition-all ${
              isListening
                ? "bg-red-500/20 text-red-400 animate-pulse"
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
