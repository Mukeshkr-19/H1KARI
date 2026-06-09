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

# Hide dock icon when running as service
if "--daemon" in sys.argv or "--bg" in sys.argv or "--tray" in sys.argv:
    try:
        from AppKit import NSApplication
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(2)
    except:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["OBJC_DISABLE_INITIALIZE_BRIDGE"] = "1"

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
    from core.server import WebSocketServer

    orchestrator = get_orchestrator()
    WebSocketServer(orchestrator, host=host, port=port).start()

def run_interactive():
    """Run in interactive text mode"""
    print_banner()

    from core.cli_status import get_startup_panel
    from core.orchestrator import get_orchestrator

    orchestrator = get_orchestrator()

    print(get_startup_panel())
    print()

    print("Ready. Type a message, or 'exit' to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue

            if user_input.lower() in ["exit", "quit", "bye"]:
                orchestrator.finalize_session()
                print("\nHIKARI: Goodbye.")
                break

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

def main():
    parser = argparse.ArgumentParser(
        description="HIKARI personal AI assistant",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Run interactive text mode. This is the default.",
    )
    parser.add_argument(
        "--daemon",
        "--bg",
        dest="daemon",
        action="store_true",
        help="Run always-listening background mode.",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="Run HIKARI from the macOS menu bar.",
    )
    parser.add_argument(
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
        "--tasks-list",
        action="store_true",
        help=(
            "List recent task intents from the task database (read-only). "
            "Tasks are separate from Brain v2 memory; scheduling is not wired up yet."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show internal initialization, routing, scheduler, and memory logs.",
    )

    args = parser.parse_args()

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

    if args.verbose:
        os.environ["HIKARI_VERBOSE"] = "1"
        os.environ["HIKARI_QUIET"] = "0"

    if args.memory_status:
        from core.memory_status import format_memory_status_report

        print(format_memory_status_report())
        raise SystemExit(0)

    if args.tasks_list:
        from core.tasks.cli import run_tasks_list_cli

        raise SystemExit(run_tasks_list_cli())

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
        args.brain_v2_reconcile_status,
        args.brain_v2_repair_plan,
        args.brain_v2_live_qa_checklist,
    )
    if any(brain_v2_actions):
        from core.brain_v2.cli import (
            run_brain_v2_cli,
            run_brain_v2_cli_edit_metadata,
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
        if args.brain_v2_retire:
            raise SystemExit(run_brain_v2_cli("retire", args.brain_v2_retire))
        if args.brain_v2_supersede:
            if not args.brain_v2_statement:
                parser.error("--brain-v2-supersede requires --brain-v2-statement")
            raise SystemExit(
                run_brain_v2_cli_supersede(
                    args.brain_v2_supersede,
                    args.brain_v2_statement,
                    candidate_type=args.brain_v2_memory_type,
                )
            )
        if args.brain_v2_edit_metadata:
            raise SystemExit(
                run_brain_v2_cli_edit_metadata(
                    args.brain_v2_edit_metadata,
                    candidate_type=args.brain_v2_memory_type,
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

    if args.daemon:
        run_daemon()
        return

    run_interactive()

if __name__ == "__main__":
    main()
