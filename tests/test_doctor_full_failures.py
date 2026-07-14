"""Full doctor command failures and timeouts must be stable and aggregated."""

from __future__ import annotations

import subprocess

from core import doctor


def test_full_command_plan_has_expected_commands_and_timeouts(monkeypatch):
    calls = []

    def record(name, command, cwd=doctor.REPO_ROOT, timeout=60, input_text=None):
        calls.append((name, command, cwd, timeout, input_text))
        return doctor.Check(name, "ok", "recorded")

    monkeypatch.setattr(doctor, "_command_check", record)

    checks = doctor._collect_full_command_checks()

    assert [check.name for check in checks] == [
        "CLI help",
        "Text status",
        "Python tests",
        "Frontend lint",
        "Frontend build",
    ]
    assert [call[3] for call in calls] == [20, 40, 120, 120, 180]
    assert calls[1][4] == "status\nexit\n"
    assert calls[3][2] == doctor.REPO_ROOT / "hikari-frontend"
    assert calls[4][2] == doctor.REPO_ROOT / "hikari-frontend"


def test_run_command_normalizes_timeout_bytes(monkeypatch):
    def time_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            ["slow-command"],
            3,
            output=b"partial output",
            stderr=b"partial error",
        )

    monkeypatch.setattr(doctor.subprocess, "run", time_out)

    result = doctor._run_command(["slow-command"], timeout=3)

    assert result == doctor.CommandResult(
        124,
        "partial output",
        "partial error",
        timed_out=True,
    )


def test_run_command_missing_executable_is_stable(monkeypatch):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("missing-tool")

    monkeypatch.setattr(doctor.subprocess, "run", missing)

    result = doctor._run_command(["missing-tool"])

    assert result.returncode == 127
    assert result.stderr == "missing-tool"
    assert result.timed_out is False


def test_command_check_reports_timeout(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_run_command",
        lambda *_args, **_kwargs: doctor.CommandResult(124, "", "", True),
    )

    check = doctor._command_check("Slow", ["slow"], timeout=9)

    assert check.status == "fail"
    assert check.detail == "Timed out after 9s: slow"


def test_command_check_reports_nonzero_exit_tail(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_run_command",
        lambda *_args, **_kwargs: doctor.CommandResult(
            7,
            "",
            "first line\nfinal failure",
        ),
    )

    check = doctor._command_check("Broken", ["tool", "--check"])

    assert check.status == "fail"
    assert check.detail == "tool --check exited 7: final failure"


def test_command_check_reports_success(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_run_command",
        lambda *_args, **_kwargs: doctor.CommandResult(0, "ok", ""),
    )

    check = doctor._command_check("Healthy", ["tool", "--check"])

    assert check == doctor.Check("Healthy", "ok", "Passed: tool --check")


def test_format_checks_aggregates_all_failures_and_warnings():
    report = doctor.format_checks(
        [
            doctor.Check("One", "fail", "broken"),
            doctor.Check("Two", "warn", "careful"),
            doctor.Check("Three", "fail", "also broken"),
        ]
    )

    assert "[FAIL] One: broken" in report
    assert "[FAIL] Three: also broken" in report
    assert report.endswith("FAILED: 2 failure(s), 1 warning(s)")


def test_run_doctor_exit_code_reflects_any_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor,
        "collect_checks",
        lambda full=False: [
            doctor.Check("Healthy", "ok", "good"),
            doctor.Check("Broken", "fail", "bad"),
        ],
    )

    assert doctor.run_doctor(full=True) == 1
    assert "FAILED: 1 failure(s), 0 warning(s)" in capsys.readouterr().out
