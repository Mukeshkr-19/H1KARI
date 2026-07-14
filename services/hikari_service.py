#!/usr/bin/env python3
"""
HIKARI v3 - Simple Always Listening Service
Listens for audio, detects speech using Google (no wake word needed),
just responds when it hears anything spoken
"""

import os
import sys
import time
import signal
import re
import subprocess

# Setup path
HIKARI_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIKARI_DIR)

print("""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   ██╗  ██╗██╗  ██╗ ██████╗ ██╗    ██╗                    ║
║   ██║ ██╔╝██║  ██║██╔═══██╗██║    ██║                    ║
║   █████╔╝ ███████║██║   ██║██║ █╗ ██║                    ║
║   ██╔═██╗ ██╔══██║██║   ██║██║███╗██║                    ║
║   ██║  ██╗██║  ██║╚██████╔╝╚███╔███╔╝                    ║
║   ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝  ╚══╝╚══╝                    ║
║                                                          ║
║         Your 24/7 AI Assistant - Always Listening         ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
""")

from core.orchestrator import get_orchestrator

class HIKARI_Daemon:
    def __init__(self):
        self.running = True
        self.orchestrator = None
        self.is_awake = False  # Start awake - no wake word needed

        # Handle signals
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        print("[HIKARI] Loading brain...")
        self.orchestrator = get_orchestrator()
        print("[HIKARI] Brain loaded! I'm always awake and ready.")

    def _shutdown(self, signum, frame):
        print("\n[HIKARI] Shutting down gracefully...")
        self.running = False

    def speak(self, text):
        """Text to speech using macOS say"""
        try:
            clean = re.sub(r"[^\w\s:,.!?']", "", str(text))
            if sys.platform == "darwin":
                subprocess.run(
                    ["say", "-r", "180", clean],
                    timeout=30,
                    check=False,
                )
            else:
                print(f"[TTS] {clean}")
        except Exception as e:
            print(f"[TTS Error] {e}")

    def listen_and_respond(self):
        """Listen for speech and respond - no wake word needed"""
        print("\n[HIKARI] Listening... (say something!)")

        try:
            import speech_recognition as sr

            r = sr.Recognizer()
            r.energy_threshold = 3000  # Lower threshold for better detection
            r.dynamic_energy_threshold = True
            r.pause_threshold = 1.0

            with sr.Microphone() as source:
                r.adjust_for_ambient_noise(source, duration=0.5)

                while self.running:
                    print("[HIKARI] Listening...", end="\r", flush=True)

                    try:
                        audio = r.listen(source, timeout=5, phrase_time_limit=10)

                        # Recognize speech
                        print("\n[HIKARI] Processing...")
                        text = r.recognize_google(audio).lower().strip()

                        if text and len(text) > 0:
                            print(f"[YOU] {text}")

                            # Check for exit command
                            if any(w in text for w in ["exit", "quit", "goodbye", "stop"]):
                                self.speak("Goodbye! Call me when you need me.")
                                break

                            # Process through HIKARI
                            response = self.orchestrator.process_input(text, source="voice")

                            if response:
                                print(f"[HIKARI] {response}")
                                self.speak(response)

                            print("\n[HIKARI] Listening... (say something or 'exit' to stop)")

                    except sr.WaitTimeoutError:
                        continue
                    except sr.UnknownValueError:
                        print("\n[HIKARI] Couldn't understand - try again")
                        continue
                    except Exception as e:
                        print(f"\n[HIKARI] Error: {e}")
                        continue

        except ImportError:
            print("[HIKARI] SpeechRecognition not available!")
            print("[HIKARI] Run: pip install SpeechRecognition")
            print("[HIKARI] Also install: brew install portaudio")
        except Exception as e:
            print(f"[HIKARI] Error: {e}")

    def run(self):
        """Run the daemon"""
        print("\n[HIKARI] Service running! Speak to interact.")
        print("[HIKARI] Say 'exit' to stop listening.\n")

        self.listen_and_respond()

        print("[HIKARI] Service stopped.")

if __name__ == "__main__":
    daemon = HIKARI_Daemon()
    daemon.run()
