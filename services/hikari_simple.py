#!/usr/bin/env python3
"""
HIKARI - Simple always-on daemon
Say 'hikari' to activate, 'bye' to sleep (keeps listening)
"""

import os, sys, time, subprocess, signal, json

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import speech_recognition as sr

state = "LISTENING"
r = sr.Recognizer()
r.energy_threshold = 300
r.dynamic_energy_threshold = True

print("🎯 HIKARI starting...")


def speak(text):
    global state
    state = "SPEAKING"
    print(f"🔊 {text}")
    subprocess.run(["say", "-r", "200", text], capture_output=True)
    state = "ACTIVE"


def process(text):
    try:
        from core.orchestrator import get_orchestrator

        return get_orchestrator().process_input(text, source="voice")
    except Exception as e:
        return f"Oops: {e}"


def is_wake(text):
    t = text.lower().strip()
    return t.startswith("hec") or t.startswith("hik") or "hikari" in t or "hector" in t


def is_stop(text):
    t = text.lower()
    return any(w in t for w in ["bye", "goodbye", "exit", "stop", "done"])


print("✅ Say 'hikari' to activate, 'bye' to sleep\n")

while True:
    print("💤 Listening..." if state == "LISTENING" else "👂 Ready...")
    try:
        with sr.Microphone() as source:
            r.adjust_for_ambient_noise(source, duration=0.5)
            audio = r.listen(source, timeout=5, phrase_time_limit=5)

        print("🎤 Got audio, recognizing...")
        text = r.recognize_google(audio).lower().strip()
        print(f"📝 '{text}'")

        if state == "LISTENING":
            if is_wake(text):
                print("🎉 WAKING UP!")
                state = "ACTIVE"
                speak("Go ahead!")
            else:
                print("(ignored)")

        elif state == "ACTIVE":
            print(f"You: {text}")
            if is_stop(text):
                print("💤 Sleeping...")
                speak("Talk to you later!")
                state = "LISTENING"
            else:
                resp = process(text)
                print(f"HIKARI: {resp}")
                speak(resp)

    except sr.WaitTimeoutError:
        pass
    except sr.UnknownValueError:
        print("(didn't catch that)")
    except KeyboardInterrupt:
        print("\nBye!")
        break
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)
