#!/usr/bin/env python3
"""
HIKARI v3 - Main Entry Point

Usage:
    python3 hikari.py                 # Interactive text mode
    python3 hikari.py --daemon        # Always listening (no wake word needed)
    python3 hikari.py --tray          # System tray icon mode
    python3 hikari.py --install       # Install as login item (starts on boot)
    python3 hikari.py --install-cli   # Install hikari/Hikari shell commands
"""

import os
import sys
import re
import argparse
import subprocess
from pathlib import Path
from textwrap import dedent

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["OBJC_DISABLE_INITIALIZE_BRIDGE"] = "1"


def hide_dock_icon():
    """Hide the dock icon for validated background UI modes on macOS."""
    try:
        from AppKit import NSApplication

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(2)
    except Exception:
        pass

def print_banner():
    banner = dedent(
        """
        +------------------------------------------------------------+
        |                                                            |
        |   _   _ ___ _  __    _    ____  ___                       |
        |  | | | |_ _| |/ /   / \\  |  _ \\|_ _|                      |
        |  | |_| || || ' /   / _ \\ | |_) || |                       |
        |  |  _  || || . \\  / ___ \\|  _ < | |                       |
        |  |_| |_|___|_|\\_\\/_/   \\_\\_| \\_\\___|                      |
        |                                                            |
        |          Your 24/7 AI Assistant - Always Listening         |
        |                                                            |
        +------------------------------------------------------------+
        """
    ).strip("\n")

    print(f"{banner}\n")

def run_daemon():
    """Run as always-listening service (no wake word needed)"""
    print("[*] Starting HIKARI in background mode...")
    print("[*] Just speak and I'll respond - no wake word needed!")
    print("[*] Say 'exit' to stop listening\n")

    from services.hikari_service import HIKARI_Daemon
    HIKARI_Daemon().run()


def _format_conversation_sessions(records) -> str:
    if not records:
        return "No saved conversations."
    lines = ["Saved conversations:"]
    for record in records:
        state = "archived" if record.archived else "active"
        lines.append(
            f"- {record.session_id}  {record.title}  "
            f"({record.turn_count} turns, {state})"
        )
    return "\n".join(lines)


def _prepare_local_conversation(orchestrator, *, session_id=None, new_session=False):
    from core.conversation_sessions import (
        ConversationSessionError,
        create_conversation_session_store,
    )

    if not callable(getattr(orchestrator, "configure_conversation_session", None)):
        return None, None, 0
    try:
        store = create_conversation_session_store()
        if new_session:
            record = store.create(owner_id="local-owner")
        elif session_id:
            record = store.get(owner_id="local-owner", session_id=session_id)
            if record is None or record.archived:
                raise ConversationSessionError("conversation not found")
        else:
            record = store.latest(owner_id="local-owner")
            if record is None:
                record = store.create(owner_id="local-owner")
        restored = orchestrator.configure_conversation_session(store, record.session_id)
        return store, record, restored
    except ConversationSessionError:
        if session_id:
            raise SystemExit("Conversation session is unavailable.") from None
        print(
            "[!] Saved chat sessions are temporarily unavailable; using a temporary chat.",
            file=sys.stderr,
        )
        return None, None, 0


def _switch_after_removal(orchestrator, store):
    record = store.latest(owner_id="local-owner")
    if record is None:
        record = store.create(owner_id="local-owner")
    restored = orchestrator.configure_conversation_session(store, record.session_id)
    return record, restored


def _handle_conversation_command(command, orchestrator, store):
    """Handle local slash commands; return (handled, possibly-new store)."""
    from core.conversation_sessions import ConversationSessionError

    text = command.strip()
    if not text.startswith("/"):
        return False, store
    name, _, argument = text.partition(" ")
    name = name.casefold()
    argument = argument.strip()
    if name in {"/help", "/session-help"}:
        print(
            "\nChat commands:\n"
            "  /sessions [all]       List saved conversations\n"
            "  /new [title]           Start a new conversation\n"
            "  /resume SESSION_ID     Resume an active conversation\n"
            "  /rename TITLE          Rename the current conversation\n"
            "  /archive               Archive the current conversation\n"
            "  /unarchive SESSION_ID  Restore an archived conversation\n"
            "  /delete SESSION_ID DELETE  Permanently delete a conversation\n"
        )
        return True, store
    if store is None:
        print("\nHIKARI: Saved chat sessions are unavailable.\n")
        return True, store
    try:
        if name == "/sessions":
            records = store.list_sessions(
                owner_id="local-owner",
                include_archived=argument.casefold() == "all",
                limit=50,
            )
            print(f"\n{_format_conversation_sessions(records)}\n")
            return True, store
        if name == "/new":
            title = argument or "New conversation"
            record = store.create(owner_id="local-owner", title=title)
            orchestrator.configure_conversation_session(store, record.session_id)
            print(f"\nHIKARI: Started {record.title} ({record.session_id}).\n")
            return True, store
        if name == "/resume":
            record = store.get(owner_id="local-owner", session_id=argument)
            if record is None or record.archived:
                raise ConversationSessionError("conversation not found")
            restored = orchestrator.configure_conversation_session(store, record.session_id)
            print(
                f"\nHIKARI: Resumed {record.title} "
                f"({restored} recent turns restored).\n"
            )
            return True, store
        if name == "/rename":
            if not argument:
                raise ConversationSessionError("title is required")
            active = orchestrator.active_conversation_session_id()
            if not store.rename(
                owner_id="local-owner", session_id=active, title=argument
            ):
                raise ConversationSessionError("conversation not found")
            print("\nHIKARI: Conversation renamed.\n")
            return True, store
        if name == "/archive":
            active = orchestrator.active_conversation_session_id()
            if not store.archive(owner_id="local-owner", session_id=active):
                raise ConversationSessionError("conversation not found")
            record, restored = _switch_after_removal(orchestrator, store)
            print(
                f"\nHIKARI: Conversation archived. Resumed {record.title} "
                f"({restored} recent turns restored).\n"
            )
            return True, store
        if name == "/unarchive":
            if not store.unarchive(owner_id="local-owner", session_id=argument):
                raise ConversationSessionError("conversation not found")
            record = store.get(owner_id="local-owner", session_id=argument)
            orchestrator.configure_conversation_session(store, argument)
            print(f"\nHIKARI: Restored {record.title}.\n")
            return True, store
        if name == "/delete":
            pieces = argument.split()
            if len(pieces) != 2 or pieces[1] != "DELETE":
                print(
                    "\nHIKARI: Use /delete SESSION_ID DELETE to confirm permanent deletion.\n"
                )
                return True, store
            target = pieces[0]
            current = orchestrator.active_conversation_session_id()
            if not store.delete(owner_id="local-owner", session_id=target):
                raise ConversationSessionError("conversation not found")
            if target == current:
                record, restored = _switch_after_removal(orchestrator, store)
                print(
                    f"\nHIKARI: Conversation deleted. Resumed {record.title} "
                    f"({restored} recent turns restored).\n"
                )
            else:
                print("\nHIKARI: Conversation deleted.\n")
            return True, store
    except ConversationSessionError:
        print("\nHIKARI: That conversation operation could not be completed.\n")
        return True, store
    print("\nHIKARI: Unknown chat command. Type /help for available commands.\n")
    return True, store


def run_voice(backend: str | None, *, session_id=None, new_session=False) -> int:
    """Run explicit foreground voice mode through the bounded voice adapter."""
    print_banner()
    from core.orchestrator import get_orchestrator

    orchestrator = get_orchestrator()
    _prepare_local_conversation(
        orchestrator,
        session_id=session_id,
        new_session=new_session,
    )
    return orchestrator.run_voice_loop(backend)

def run_tray():
    """Run as system tray icon"""
    print("[*] Starting HIKARI in system tray mode...")
    print("[*] Look for icon in menu bar\n")

    try:
        import rumps
        from services.hikari_tray import HIKARI_Tray
        HIKARI_Tray().run()
    except ImportError:
        print("[!] rumps not installed. Install with: pip install rumps")
        print("[*] Or run --daemon instead")

def run_server(host: str, port: int):
    """Run the WebSocket/HTTP server for phone and web clients"""
    print_banner()
    print(f"[*] Starting HIKARI server on {host}:{port}...")

    from core.orchestrator import get_orchestrator
    try:
        from core.jobs.bootstrap import create_scheduled_job_subsystem
    except ImportError:
        create_scheduled_job_subsystem = None
    try:
        from core.jobs.bootstrap import create_scheduled_job_runtime
    except ImportError:
        create_scheduled_job_runtime = None
    from core.productivity.bootstrap import (
        create_email_draft_preparation,
        create_productivity_runtime,
    )
    try:
        from core.productivity.bootstrap import create_productivity_execution_coordinator
    except ImportError:
        create_productivity_execution_coordinator = None
    try:
        from core.productivity.bootstrap import create_calendar_preparation
    except ImportError:
        create_calendar_preparation = None
    try:
        from core.productivity.bootstrap import create_research_preparation
    except ImportError:
        create_research_preparation = None
    try:
        from core.productivity.bootstrap import create_reminder_preparation
    except ImportError:
        create_reminder_preparation = None
    from core.server import WebSocketServer
    from core.action_policy import (
        ActionContext,
        ActionRisk,
        Actor,
        DataScope,
        PolicyOutcome,
        evaluate_action,
    )
    from core.handoff import FrozenHandoffPreview
    from core.phase1_runtime import create_phase1_runtime
    from core.phase4 import create_phase4_subsystem
    from core.tasks import TaskRecordContext, TaskStatus

    orchestrator = get_orchestrator()
    phase1_runtime = None
    phase4_subsystem = None
    try:
        phase1_runtime = create_phase1_runtime()

        def phase4_task_lookup(actor, task_id):
            context = TaskRecordContext(
                speaker_label=actor.actor_id,
                session_id=actor.session_id,
                source=actor.source,
                actor=actor.actor.value,
                is_guest=actor.actor is Actor.GUEST,
            )
            record = phase1_runtime.tasks.get_task(task_id, context=context)
            if record is None or record.status is not TaskStatus.COMPLETED:
                return None
            summary = record.result_summary or record.raw_text
            return FrozenHandoffPreview(task_id=record.task_id, summary=summary)

        def phase4_acceptance_policy(actor, _preview):
            decision = evaluate_action(
                ActionContext(
                    action="task.handoff.accept",
                    actor=actor.actor,
                    data_scope=DataScope.SESSION,
                    risk=ActionRisk.READ_ONLY,
                    user_initiated=True,
                    confirmation_granted=True,
                )
            )
            return actor.actor is Actor.OWNER and decision.outcome is PolicyOutcome.ALLOW

        phase4_subsystem = create_phase4_subsystem(
            task_lookup=phase4_task_lookup,
            acceptance_policy=phase4_acceptance_policy,
        )
    except Exception:
        phase4_subsystem = None
        print("[!] Phase 4 device handoff is temporarily unavailable.", file=sys.stderr)
    productivity_runtime = None
    productivity_execution_coordinator = None
    email_draft_factory = None
    email_draft_registry = None
    calendar_read_factory = None
    calendar_draft_factory = None
    calendar_registry = None
    research_factory = None
    research_registry = None
    reminder_factory = None
    reminder_registry = None
    try:
        productivity_runtime = create_productivity_runtime()
        if create_productivity_execution_coordinator is not None:
            productivity_execution_coordinator = (
                create_productivity_execution_coordinator(productivity_runtime)
            )
        email_draft_factory, email_draft_registry = create_email_draft_preparation()
    except Exception:
        productivity_runtime = None
        productivity_execution_coordinator = None
        email_draft_factory = None
        email_draft_registry = None
        print(
            "[!] Productivity actions are temporarily unavailable.",
            file=sys.stderr,
        )
    try:
        if create_calendar_preparation is None:
            raise RuntimeError("calendar bootstrap unavailable")
        (
            calendar_read_factory,
            calendar_draft_factory,
            calendar_registry,
        ) = create_calendar_preparation()
    except Exception:
        calendar_read_factory = None
        calendar_draft_factory = None
        calendar_registry = None
        print(
            "[!] Calendar preparation is temporarily unavailable.",
            file=sys.stderr,
        )
    try:
        if create_research_preparation is None:
            raise RuntimeError("research bootstrap unavailable")
        research_factory, research_registry = create_research_preparation()
    except Exception:
        research_factory = None
        research_registry = None
        print(
            "[!] Research preparation is temporarily unavailable.",
            file=sys.stderr,
        )
    try:
        if create_reminder_preparation is None:
            raise RuntimeError("reminder bootstrap unavailable")
        reminder_factory, reminder_registry = create_reminder_preparation()
    except Exception:
        reminder_factory = None
        reminder_registry = None
        print(
            "[!] Reminder preparation is temporarily unavailable.",
            file=sys.stderr,
        )
    scheduled_job_subsystem = None
    try:
        if create_scheduled_job_subsystem is not None:
            scheduled_job_subsystem = create_scheduled_job_subsystem()
            scheduled_job_runtime = scheduled_job_subsystem.runtime
        else:
            if create_scheduled_job_runtime is None:
                raise RuntimeError("scheduled-job bootstrap unavailable")
            scheduled_job_runtime = create_scheduled_job_runtime()
    except Exception:
        scheduled_job_runtime = None
        print(
            "[!] Scheduled jobs are temporarily unavailable.",
            file=sys.stderr,
        )
    server_kwargs = dict(
        host=host,
        port=port,
        productivity_runtime=productivity_runtime,
        scheduled_job_runtime=scheduled_job_runtime,
        email_draft_factory=email_draft_factory,
        email_draft_registry=email_draft_registry,
    )
    if scheduled_job_subsystem is not None:
        server_kwargs["scheduled_job_subsystem"] = scheduled_job_subsystem
    if productivity_execution_coordinator is not None:
        server_kwargs["productivity_execution_coordinator"] = (
            productivity_execution_coordinator
        )
    if calendar_read_factory is not None:
        server_kwargs["calendar_read_factory"] = calendar_read_factory
    if calendar_draft_factory is not None:
        server_kwargs["calendar_draft_factory"] = calendar_draft_factory
    if calendar_registry is not None:
        server_kwargs["calendar_registry"] = calendar_registry
    if research_factory is not None:
        server_kwargs["research_factory"] = research_factory
    if research_registry is not None:
        server_kwargs["research_registry"] = research_registry
    if reminder_factory is not None:
        server_kwargs["reminder_factory"] = reminder_factory
    if reminder_registry is not None:
        server_kwargs["reminder_registry"] = reminder_registry
    if phase4_subsystem is not None:
        server_kwargs["phase1_runtime"] = phase1_runtime
        server_kwargs["pairing_runtime"] = phase4_subsystem.pairing_runtime
        server_kwargs["handoff_transport"] = phase4_subsystem.handoff_transport
        server_kwargs["visual_transfer_runtime"] = (
            phase4_subsystem.visual_transfer_runtime
        )
        server_kwargs["vision_runtime"] = phase4_subsystem.vision_runtime
    WebSocketServer(
        orchestrator,
        **server_kwargs,
    ).start()

def run_interactive(*, session_id=None, new_session=False):
    """Run in interactive text mode"""
    print_banner()

    # Importing readline enables native terminal line editing and in-session
    # history for input().  HIKARI deliberately never writes that history to disk.
    try:
        import readline  # noqa: F401
    except ImportError:
        pass

    from core.orchestrator import get_orchestrator

    orchestrator = get_orchestrator()

    session_store, session_record, restored = _prepare_local_conversation(
        orchestrator,
        session_id=session_id,
        new_session=new_session,
    )

    print("Ready. Type a message, or 'exit' to quit.\n")
    if session_record is not None:
        print(
            f"Chat: {session_record.title} ({session_record.session_id})"
            f" — {restored} recent turns restored. Type /help for chat commands.\n"
        )

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue

            if user_input.lower() in ["exit", "quit", "bye"]:
                orchestrator.finalize_session()
                print("\nHIKARI: Goodbye.")
                break

            handled, session_store = _handle_conversation_command(
                user_input,
                orchestrator,
                session_store,
            )
            if handled:
                continue

            response = orchestrator.process_input(user_input, source="text")
            if response:
                print(f"\nHIKARI: {response}\n")

        except KeyboardInterrupt:
            orchestrator.finalize_session()
            print("\nHIKARI: Shutting down.")
            break
        except EOFError:
            orchestrator.finalize_session()
            break

def install_service():
    """Install HIKARI as login item (runs on Mac startup)"""
    python_path = subprocess.run(["which", "python3"], capture_output=True, text=True).stdout.strip()

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.hikari.ai</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{os.path.abspath(__file__)}</string>
        <string>--daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>"""

    plist_path = os.path.expanduser("~/Library/LaunchAgents/com.hikari.ai.plist")
    os.makedirs(os.path.dirname(plist_path), exist_ok=True)

    with open(plist_path, "w") as f:
        f.write(plist)

    subprocess.run(["launchctl", "load", plist_path])
    print("[+] HIKARI installed as login item!")
    print("[+] Restart your Mac to start HIKARI automatically.")

def run_repo_script(script_name: str):
    """Run a repo-local script."""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", script_name)
    raise SystemExit(subprocess.run(["bash", script_path]).returncode)


def run_document_cli(args, runtime=None) -> int:
    """Run the local-owner document flow with an explicit egress confirmation."""
    providers = tuple(args.document_provider or ())
    selected_path = args.explain_document
    task_id = args.document_task

    if selected_path is not None and (
        not isinstance(selected_path, str)
        or not selected_path
        or len(selected_path) > 4096
        or "\x00" in selected_path
        or Path(selected_path).suffix.lower() != ".txt"
    ):
        print("Invalid document path.", file=sys.stderr)
        return 2
    if task_id is not None and (
        not isinstance(task_id, str) or not task_id or len(task_id) > 64
    ):
        print("Invalid document task ID.", file=sys.stderr)
        return 2
    if bool(selected_path) == bool(task_id):
        print("Choose exactly one document path or task ID.", file=sys.stderr)
        return 2
    if args.document_follow_up is not None and (
        not task_id
        or not isinstance(args.document_follow_up, str)
        or not args.document_follow_up.strip()
        or len(args.document_follow_up) > 2000
    ):
        print("Invalid document follow-up.", file=sys.stderr)
        return 2
    if (
        len(providers) > 8
        or len(set(providers)) != len(providers)
        or any(
            not isinstance(provider, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,79}", provider)
            for provider in providers
        )
    ):
        print("Invalid document provider selection.", file=sys.stderr)
        return 2
    if args.confirm_document not in (None, "READ_AND_SEND"):
        print("Invalid document confirmation token.", file=sys.stderr)
        return 2
    if args.confirm_document is not None and not providers:
        print("Document action requires at least one --document-provider.", file=sys.stderr)
        return 2
    if providers and args.confirm_document is None:
        print("Document providers require --confirm-document READ_AND_SEND.", file=sys.stderr)
        return 2
    if args.document_follow_up and args.confirm_document != "READ_AND_SEND":
        print("Document follow-up requires --confirm-document READ_AND_SEND.", file=sys.stderr)
        return 2

    from core.phase1_runtime import create_phase1_runtime, owner_contexts

    runtime = runtime or create_phase1_runtime()
    actor, context = owner_contexts(source="cli")

    if selected_path:
        print(f"Selected document: {selected_path}")
    if providers:
        print(f"Selected providers: {', '.join(providers)}")

    if selected_path:
        result = runtime.documents.prepare(selected_path, actor=actor, context=context)
        if result.error_code:
            print(f"Document request failed: {result.error_code}", file=sys.stderr)
            return 1
        task_id = result.task_id
        print(f"Document task: {task_id}")
        if args.confirm_document is None:
            print("Confirmation required before reading or sending the document.")
            return 0

    if task_id and not selected_path and args.confirm_document == "READ_AND_SEND":
        task = runtime.tasks.get_task(task_id, context=context)
        if task is not None and task.selected_path:
            print(f"Selected document: {task.selected_path}")

    if args.document_follow_up:
        result = runtime.documents.follow_up(
            task_id,
            args.document_follow_up,
            providers,
            actor=actor,
            context=context,
        )
    elif args.confirm_document is not None:
        result = runtime.documents.confirm_and_explain(
            task_id, providers, actor=actor, context=context
        )
    else:
        result = runtime.documents.reconnect(task_id, actor=actor, context=context)

    if result.error_code:
        print(f"Document request failed: {result.error_code}", file=sys.stderr)
        return 1
    if result.explanation is not None:
        print(result.explanation)
        if result.provider:
            print(f"Provider: {result.provider}")
    else:
        print(f"Document task {result.task_id}: {result.status}")
    return 0

def main():
    parser = argparse.ArgumentParser(
        description="HIKARI personal AI assistant",
    )
    runtime_modes = parser.add_mutually_exclusive_group()
    runtime_modes.add_argument(
        "--text",
        action="store_true",
        help="Run interactive text mode. This is the default.",
    )
    runtime_modes.add_argument(
        "--voice",
        action="store_true",
        help="Run explicit foreground microphone mode.",
    )
    runtime_modes.add_argument(
        "--daemon",
        "--bg",
        dest="daemon",
        action="store_true",
        help="Run always-listening background mode.",
    )
    runtime_modes.add_argument(
        "--tray",
        action="store_true",
        help="Run HIKARI from the macOS menu bar.",
    )
    runtime_modes.add_argument(
        "--server",
        action="store_true",
        help="Run the WebSocket/HTTP server for phone and web clients.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host for --server mode. Default: 0.0.0.0.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for --server mode. Default: 8765.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install HIKARI as a macOS login item.",
    )
    parser.add_argument(
        "--install-cli",
        action="store_true",
        help="Install hikari/Hikari shell commands into a PATH directory.",
    )
    parser.add_argument(
        "--uninstall-cli",
        action="store_true",
        help="Remove hikari/Hikari shell commands installed for this repo.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run a quick HIKARI workspace health/status check.",
    )
    parser.add_argument(
        "--doctor-full",
        action="store_true",
        help="Run doctor plus CLI, text, test, lint, and build checks.",
    )
    parser.add_argument(
        "--memory-status",
        action="store_true",
        help="Show neural memory connection and brain stats (read-only, no conversation dump).",
    )
    parser.add_argument(
        "--voice-status",
        action="store_true",
        help=(
            "Show installed voice backends, expected model caches, offline readiness, "
            "and audio-egress policy without loading models."
        ),
    )
    runtime_modes.add_argument(
        "--init",
        action="store_true",
        help="Initialize the private HIKARI runtime layout without downloading models.",
    )
    runtime_modes.add_argument(
        "--init-plan",
        action="store_true",
        help="Preview runtime initialization without writing files.",
    )
    runtime_modes.add_argument(
        "--runtime-backup",
        action="store_true",
        help="Back up initialized private runtime state without following symlinks.",
    )
    runtime_modes.add_argument(
        "--migration-plan",
        action="store_true",
        help="Inspect legacy runtime layout and print a no-write migration plan.",
    )
    runtime_modes.add_argument(
        "--rollback-init",
        metavar="TOKEN",
        help="Remove only empty paths created by --init; token must be exactly ROLLBACK.",
    )
    parser.add_argument(
        "--backup-destination",
        metavar="PATH",
        help="Optional destination for --runtime-backup; parent must already exist.",
    )
    parser.add_argument(
        "--startup-mode",
        choices=("text", "voice"),
        help="Required with --init or --init-plan.",
    )
    parser.add_argument(
        "--voice-backend",
        choices=("openai-whisper", "faster-whisper", "google-speech"),
        help="Required for voice startup; records download and audio-egress disclosure.",
    )
    session_selection = parser.add_mutually_exclusive_group()
    session_selection.add_argument(
        "--new",
        "--new-session",
        dest="new_session",
        action="store_true",
        help="Start text or foreground voice mode in a new saved conversation.",
    )
    session_selection.add_argument(
        "--session",
        metavar="SESSION_ID",
        help="Resume an existing saved conversation in text or foreground voice mode.",
    )
    parser.add_argument(
        "--sessions",
        action="store_true",
        help="List saved local-owner conversations without starting HIKARI.",
    )
    parser.add_argument(
        "--brain-v2-status",
        action="store_true",
        help="Show Brain v2 episode DB counts and review queue status.",
    )
    parser.add_argument(
        "--brain-v2-pending",
        action="store_true",
        help="List pending Brain v2 memory candidates awaiting review.",
    )
    parser.add_argument(
        "--brain-v2-show",
        metavar="CANDIDATE_ID",
        help="Show Brain v2 candidate details and source segment ids.",
    )
    parser.add_argument(
        "--brain-v2-accept",
        metavar="CANDIDATE_ID",
        help=(
            "Accept a Brain v2 candidate into source-linked memory only by default. "
            "Pass --confirm-promote PROMOTE (exact, case-sensitive) to also write "
            "to the live neural memory database."
        ),
    )
    parser.add_argument(
        "--confirm-promote",
        metavar="TOKEN",
        help=(
            "Required with --brain-v2-accept for neural promotion: token must be "
            "exactly PROMOTE (case-sensitive). Without it, --brain-v2-accept never "
            "promotes. Cannot be used with --brain-v2-accept-no-promote."
        ),
    )
    parser.add_argument(
        "--brain-v2-accept-no-promote",
        metavar="CANDIDATE_ID",
        help=(
            "Accept into Brain v2 source-linked memory only; never writes to the "
            "live neural DB (explicit safe default; same as --brain-v2-accept "
            "without --confirm-promote)."
        ),
    )
    parser.add_argument(
        "--brain-v2-reject",
        metavar="CANDIDATE_ID",
        help="Reject a Brain v2 memory candidate.",
    )
    parser.add_argument(
        "--brain-v2-memories",
        action="store_true",
        help="List accepted Brain v2 source-linked memories.",
    )
    parser.add_argument(
        "--brain-v2-consolidate",
        action="store_true",
        help=(
            "Consolidate raw Brain v2 episodes that have transcript segments but no "
            "structured episode yet (non-destructive; no neural promotion)."
        ),
    )
    parser.add_argument(
        "--brain-v2-retag-accepted",
        action="store_true",
        help=(
            "Re-infer candidate_type and structured metadata for accepted Brain v2 "
            "memories from their statements (metadata only; no neural promotion)."
        ),
    )
    parser.add_argument(
        "--brain-v2-review",
        action="store_true",
        help=(
            "Interactive guided review of pending Brain v2 candidates. "
            "Safe default: [a] accept without neural promotion; [p] promote only "
            "after typing PROMOTE (exact, case-sensitive)."
        ),
    )
    parser.add_argument(
        "--brain-v2-eval",
        action="store_true",
        help=(
            "Run Brain v2 eval suite on an isolated temp DB with synthetic fixtures only "
            "(does not read or write the live brain DB)."
        ),
    )
    parser.add_argument(
        "--brain-live-qa",
        action="store_true",
        help=(
            "Run full-orchestrator Brain live QA (real process_input, isolated temp DB). "
            "Use before trusting unit tests alone."
        ),
    )
    parser.add_argument(
        "--brain-v2-conflicts",
        action="store_true",
        help=(
            "Report conflicts between accepted Brain v2 memories and neural profile "
            "summary lines (read-only; redacted by default; set "
            "HIKARI_BRAIN_V2_CONFLICTS_PRIVATE=1 for local private statement review)."
        ),
    )
    parser.add_argument(
        "--brain-v2-retire",
        metavar="MEMORY_ID",
        help="Retire an accepted Brain v2 memory (preserves audit/history; no hard delete).",
    )
    parser.add_argument(
        "--brain-v2-supersede",
        metavar="MEMORY_ID",
        help="Supersede an accepted memory with a corrected statement (requires --statement).",
    )
    parser.add_argument(
        "--brain-v2-statement",
        metavar="TEXT",
        help="Corrected statement for --brain-v2-supersede.",
    )
    parser.add_argument(
        "--brain-v2-edit-metadata",
        metavar="MEMORY_ID",
        help="Edit safe metadata on an accepted memory (type only; statement unchanged).",
    )
    parser.add_argument(
        "--brain-v2-memory-type",
        metavar="TYPE",
        help="Optional candidate_type for --brain-v2-supersede or --brain-v2-edit-metadata.",
    )
    parser.add_argument(
        "--brain-v2-memory-history",
        metavar="MEMORY_ID",
        help="Show correction/supersession history for an accepted memory id.",
    )
    parser.add_argument(
        "--brain-v2-repair-show",
        metavar="MEMORY_ID",
        help="Read-only detail for an accepted memory (statement, evidence, audit).",
    )
    parser.add_argument(
        "--repair-preview",
        action="store_true",
        help=(
            "With --brain-v2-retire, --brain-v2-supersede, or --brain-v2-edit-metadata: "
            "show what would change without writing."
        ),
    )
    parser.add_argument(
        "--confirm-repair",
        metavar="TOKEN",
        help=(
            "Required on the live Brain v2 DB for repair apply: RETIRE, SUPERSEDE, or EDIT "
            "(exact, case-sensitive). Back up the private brain directory first."
        ),
    )
    parser.add_argument(
        "--brain-v2-reconcile-status",
        action="store_true",
        help="Read-only reconciliation report (redacted statements by default).",
    )
    parser.add_argument(
        "--brain-v2-repair-plan",
        action="store_true",
        help="Generate a repair plan from reconciliation findings (no auto-apply).",
    )
    parser.add_argument(
        "--brain-v2-live-qa-checklist",
        action="store_true",
        help="Print private operator QA steps (generic only; no live memory content).",
    )
    parser.add_argument(
        "--brain-v2-readiness",
        action="store_true",
        help=(
            "Redacted Brain v2 sign-off report: reviewed-memory authority, legacy personal "
            "quarantine state, preserved legacy row count (no content), and actionable "
            "conflict categories only (read-only)."
        ),
    )
    parser.add_argument(
        "--brain-v2-wiki-preview",
        action="store_true",
        help=(
            "Read-only preview of private wiki pages compiled from active accepted "
            "Brain v2 memories (no file writes)."
        ),
    )
    parser.add_argument(
        "--brain-v2-wiki-writeback",
        action="store_true",
        help=(
            "Compile active accepted Brain v2 memories into private markdown wiki pages "
            "under the live brain wiki directory (or HIKARI_WIKI_DIR)."
        ),
    )
    parser.add_argument(
        "--tasks-list",
        action="store_true",
        help=(
            "List recent task intents from the task database (read-only). "
            "Tasks are separate from Brain v2 memory."
        ),
    )
    parser.add_argument(
        "--explain-document",
        metavar="PATH",
        help="Prepare a local text document for explanation; reading requires confirmation.",
    )
    parser.add_argument(
        "--document-task",
        metavar="TASK_ID",
        help="Reconnect to a prepared document task.",
    )
    parser.add_argument(
        "--document-follow-up",
        metavar="TEXT",
        help="Ask a follow-up about --document-task after explicit confirmation.",
    )
    parser.add_argument(
        "--document-provider",
        metavar="PROVIDER",
        action="append",
        help="Allowed provider for this confirmed document action; may be repeated.",
    )
    parser.add_argument(
        "--confirm-document",
        metavar="READ_AND_SEND",
        help="Exact token required before a document is read or sent to a provider.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="With --tasks-list, include tasks from all speakers/sessions.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show internal initialization, routing, scheduler, and memory logs.",
    )

    args = parser.parse_args()

    init_requested = args.init or args.init_plan
    if init_requested and args.startup_mode is None:
        parser.error("--init and --init-plan require --startup-mode")
    if not init_requested and args.startup_mode:
        parser.error("--startup-mode requires --init or --init-plan")
    if args.voice_backend and not (init_requested or args.voice):
        parser.error("--voice-backend requires --voice, --init, or --init-plan")
    if (args.new_session or args.session) and any(
        (
            args.daemon,
            args.tray,
            args.server,
            args.install,
            args.install_cli,
            args.uninstall_cli,
            args.doctor,
            args.doctor_full,
            args.sessions,
        )
    ):
        parser.error("--new and --session are available only in text or foreground voice mode")
    if args.backup_destination and not args.runtime_backup:
        parser.error("--backup-destination requires --runtime-backup")
    if args.startup_mode == "voice" and args.voice_backend is None:
        parser.error("voice startup requires --voice-backend")
    if args.startup_mode == "text" and args.voice_backend is not None:
        parser.error("--voice-backend cannot be used with text startup")

    if args.confirm_promote is not None and not args.brain_v2_accept:
        parser.error("--confirm-promote requires --brain-v2-accept")
    if args.confirm_promote is not None and args.brain_v2_accept_no_promote:
        parser.error(
            "--confirm-promote cannot be used with --brain-v2-accept-no-promote "
            "(use --brain-v2-accept with --confirm-promote PROMOTE instead)"
        )
    if args.brain_v2_accept and args.confirm_promote is not None:
        if args.confirm_promote != "PROMOTE":
            print(
                "Invalid --confirm-promote token (expected exactly PROMOTE, case-sensitive).",
                file=sys.stderr,
            )
            raise SystemExit(1)

    document_requested = (
        args.explain_document is not None or args.document_task is not None
    )
    other_action_requested = any(
        bool(getattr(args, name))
        for name in (
            "text", "voice", "daemon", "tray", "server", "install", "install_cli",
            "uninstall_cli", "doctor", "doctor_full", "memory_status",
            "voice_status", "init", "init_plan", "runtime_backup",
            "sessions",
            "migration_plan", "rollback_init", "brain_v2_status",
            "brain_v2_pending", "brain_v2_show", "brain_v2_accept",
            "brain_v2_accept_no_promote", "brain_v2_reject",
            "brain_v2_memories", "brain_v2_consolidate",
            "brain_v2_retag_accepted", "brain_v2_review", "brain_v2_eval",
            "brain_live_qa", "brain_v2_conflicts", "brain_v2_retire",
            "brain_v2_supersede", "brain_v2_edit_metadata",
            "brain_v2_memory_history", "brain_v2_repair_show",
            "brain_v2_reconcile_status", "brain_v2_repair_plan",
            "brain_v2_live_qa_checklist", "brain_v2_readiness",
            "brain_v2_wiki_preview", "brain_v2_wiki_writeback", "tasks_list",
        )
    )
    if document_requested and other_action_requested:
        parser.error("document mode cannot be combined with another action or runtime mode")
    if args.explain_document and args.document_task:
        parser.error("--explain-document and --document-task cannot be combined")
    if args.document_follow_up is not None and args.document_task is None:
        parser.error("--document-follow-up requires --document-task")
    if (
        args.document_provider is not None or args.confirm_document is not None
    ) and not document_requested:
        parser.error(
            "--document-provider and --confirm-document require "
            "--explain-document or --document-task"
        )

    _repair_apply_flags = (
        args.brain_v2_retire,
        args.brain_v2_supersede,
        args.brain_v2_edit_metadata,
    )
    if args.confirm_repair is not None and not any(_repair_apply_flags):
        parser.error(
            "--confirm-repair requires --brain-v2-retire, --brain-v2-supersede, "
            "or --brain-v2-edit-metadata"
        )
    if args.repair_preview and not any(_repair_apply_flags):
        parser.error(
            "--repair-preview requires --brain-v2-retire, --brain-v2-supersede, "
            "or --brain-v2-edit-metadata"
        )
    if args.repair_preview and args.confirm_repair:
        parser.error("--repair-preview cannot be combined with --confirm-repair")

    if args.daemon or args.tray:
        hide_dock_icon()

    if args.verbose:
        os.environ["HIKARI_VERBOSE"] = "1"
        os.environ["HIKARI_QUIET"] = "0"

    if args.migration_plan:
        from core.runtime_setup import format_migration_plan, runtime_migration_plan

        print(format_migration_plan(runtime_migration_plan()))
        raise SystemExit(0)

    if args.runtime_backup:
        from core.runtime_setup import backup_runtime_home

        destination = Path(args.backup_destination) if args.backup_destination else None
        try:
            backup_path = backup_runtime_home(destination=destination)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Runtime backup failed: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(f"Runtime backup complete: {backup_path}")
        raise SystemExit(0)

    if args.rollback_init:
        from core.runtime_setup import rollback_initialization

        try:
            removed = rollback_initialization(args.rollback_init)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Runtime rollback failed: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(f"Runtime initialization rolled back: {len(removed)} paths removed")
        raise SystemExit(0)

    if init_requested:
        from core.runtime_setup import (
            format_initialization,
            initialization_plan,
            initialize_runtime_home,
        )

        if args.init_plan:
            result = initialization_plan(args.startup_mode, args.voice_backend)
            print(format_initialization(result, applied=False))
            raise SystemExit(1 if result["blockers"] else 0)
        try:
            result = initialize_runtime_home(args.startup_mode, args.voice_backend)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Runtime initialization failed: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(format_initialization(result, applied=True))
        raise SystemExit(0)

    if args.memory_status:
        from core.memory_status import format_memory_status_report

        print(format_memory_status_report())
        raise SystemExit(0)

    if args.voice_status:
        from core.voice_status import format_voice_status

        print(format_voice_status())
        raise SystemExit(0)

    if args.tasks_list:
        from core.tasks.cli import run_tasks_list_cli

        raise SystemExit(
            run_tasks_list_cli(include_all_scopes=bool(args.all))
        )

    if document_requested:
        raise SystemExit(run_document_cli(args))

    # Isolated Brain v2 modes — do not import core.brain_v2.cli (pulls neural config).
    if args.brain_v2_eval:
        from core.brain_v2.eval import run_brain_v2_eval

        eval_result = run_brain_v2_eval()
        print(eval_result.report)
        raise SystemExit(eval_result.exit_code)

    if args.brain_live_qa:
        import subprocess

        script = Path(__file__).resolve().parent / "scripts" / "brain_live_qa.py"
        raise SystemExit(
            subprocess.call([sys.executable, str(script)], cwd=str(script.parent.parent))
        )

    if args.brain_v2_conflicts:
        from core.brain_v2.conflicts import run_brain_v2_conflicts

        raise SystemExit(run_brain_v2_conflicts())

    if args.brain_v2_readiness:
        from core.brain_v2.readiness import run_brain_v2_readiness

        raise SystemExit(run_brain_v2_readiness())

    if args.brain_v2_wiki_preview:
        from core.brain_v2.wiki_writeback import cmd_wiki_preview

        raise SystemExit(cmd_wiki_preview())

    if args.brain_v2_wiki_writeback:
        from core.brain_v2.wiki_writeback import cmd_wiki_writeback

        raise SystemExit(cmd_wiki_writeback())

    brain_v2_actions = (
        args.brain_v2_status,
        args.brain_v2_pending,
        args.brain_v2_show,
        args.brain_v2_accept,
        args.brain_v2_accept_no_promote,
        args.brain_v2_reject,
        args.brain_v2_memories,
        args.brain_v2_consolidate,
        args.brain_v2_retag_accepted,
        args.brain_v2_review,
        args.brain_v2_retire,
        args.brain_v2_supersede,
        args.brain_v2_edit_metadata,
        args.brain_v2_memory_history,
        args.brain_v2_repair_show,
        args.brain_v2_reconcile_status,
        args.brain_v2_repair_plan,
        args.brain_v2_live_qa_checklist,
    )
    if any(brain_v2_actions):
        from core.brain_v2.cli import (
            run_brain_v2_cli,
            run_brain_v2_cli_edit_metadata,
            run_brain_v2_cli_retire,
            run_brain_v2_cli_supersede,
        )

        if args.brain_v2_status:
            raise SystemExit(run_brain_v2_cli("status"))
        if args.brain_v2_pending:
            raise SystemExit(run_brain_v2_cli("pending"))
        if args.brain_v2_memories:
            raise SystemExit(run_brain_v2_cli("memories"))
        if args.brain_v2_review:
            raise SystemExit(run_brain_v2_cli("review"))
        if args.brain_v2_show:
            raise SystemExit(run_brain_v2_cli("show", args.brain_v2_show))
        if args.brain_v2_accept:
            raise SystemExit(
                run_brain_v2_cli(
                    "accept",
                    args.brain_v2_accept,
                    confirm_promote=args.confirm_promote,
                )
            )
        if args.brain_v2_accept_no_promote:
            raise SystemExit(
                run_brain_v2_cli("accept_no_promote", args.brain_v2_accept_no_promote)
            )
        if args.brain_v2_reject:
            raise SystemExit(run_brain_v2_cli("reject", args.brain_v2_reject))
        if args.brain_v2_consolidate:
            raise SystemExit(run_brain_v2_cli("consolidate"))
        if args.brain_v2_retag_accepted:
            raise SystemExit(run_brain_v2_cli("retag_accepted"))
        if args.brain_v2_repair_show:
            raise SystemExit(run_brain_v2_cli("repair_show", args.brain_v2_repair_show))
        if args.brain_v2_retire:
            raise SystemExit(
                run_brain_v2_cli_retire(
                    args.brain_v2_retire,
                    preview=args.repair_preview,
                    confirm_repair=args.confirm_repair,
                )
            )
        if args.brain_v2_supersede:
            if not args.brain_v2_statement:
                parser.error("--brain-v2-supersede requires --brain-v2-statement")
            raise SystemExit(
                run_brain_v2_cli_supersede(
                    args.brain_v2_supersede,
                    args.brain_v2_statement,
                    candidate_type=args.brain_v2_memory_type,
                    preview=args.repair_preview,
                    confirm_repair=args.confirm_repair,
                )
            )
        if args.brain_v2_edit_metadata:
            raise SystemExit(
                run_brain_v2_cli_edit_metadata(
                    args.brain_v2_edit_metadata,
                    candidate_type=args.brain_v2_memory_type,
                    preview=args.repair_preview,
                    confirm_repair=args.confirm_repair,
                )
            )
        if args.brain_v2_memory_history:
            raise SystemExit(run_brain_v2_cli("memory_history", args.brain_v2_memory_history))
        if args.brain_v2_reconcile_status:
            raise SystemExit(run_brain_v2_cli("reconcile_status"))
        if args.brain_v2_repair_plan:
            raise SystemExit(run_brain_v2_cli("repair_plan"))
        if args.brain_v2_live_qa_checklist:
            raise SystemExit(run_brain_v2_cli("live_qa_checklist"))

    if args.doctor or args.doctor_full:
        from core.doctor import run_doctor

        raise SystemExit(run_doctor(full=args.doctor_full))

    if args.sessions:
        from core.conversation_sessions import (
            ConversationSessionError,
            create_conversation_session_store,
        )
        from core.runtime_paths import hikari_home

        try:
            session_db = hikari_home() / "conversations" / "sessions.db"
            if not session_db.is_file():
                print("No saved conversations.")
                return
            store = create_conversation_session_store(db_path=session_db)
            records = store.list_sessions(
                owner_id="local-owner",
                include_archived=True,
                limit=100,
            )
            print(_format_conversation_sessions(records))
            return
        except ConversationSessionError:
            raise SystemExit("Saved conversation listing is unavailable.") from None

    if args.install:
        install_service()
        return

    if args.install_cli:
        run_repo_script("install-hikari-cli.sh")

    if args.uninstall_cli:
        run_repo_script("uninstall-hikari-cli.sh")

    if args.tray:
        run_tray()
        return

    if args.server:
        run_server(args.host, args.port)
        return

    if args.voice:
        if args.session or args.new_session:
            raise SystemExit(
                run_voice(
                    args.voice_backend,
                    session_id=args.session,
                    new_session=args.new_session,
                )
            )
        raise SystemExit(run_voice(args.voice_backend))

    if args.daemon:
        run_daemon()
        return

    if args.session or args.new_session:
        run_interactive(session_id=args.session, new_session=args.new_session)
    else:
        run_interactive()

if __name__ == "__main__":
    main()
