"""Source contracts for Phase 3 scheduled-jobs frontend wiring."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "hikari-frontend" / "src" / "app" / "page.tsx"
PANEL = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "ScheduledJobsPanel.tsx"
)


def _page() -> str:
    assert PAGE.is_file()
    return PAGE.read_text(encoding="utf-8")


def _panel() -> str:
    assert PANEL.is_file()
    return PANEL.read_text(encoding="utf-8")


def _apply_block() -> str:
    text = _page()
    apply_start = text.index("const applyScheduledJobsMessage")
    apply_end = text.index("const sendScheduledJobControl")
    return text[apply_start:apply_end]


def test_page_parses_scheduled_jobs_before_schema_fallback():
    text = _page()
    assert "parseScheduledJobsServerMessage(event.data)" in text
    scheduled_idx = text.index("parseScheduledJobsServerMessage(event.data)")
    schema_idx = text.index("parseServerMessage(event.data)", scheduled_idx)
    assert scheduled_idx < schema_idx
    assert "applyScheduledJobsMessage(scheduledJobsMessage)" in text


def test_page_requests_scheduled_jobs_list_only_after_pairing():
    text = _page()
    assert "encodeScheduledJobsList()" in text
    initialize_idx = text.index("const initializePairedConnection = () => {")
    list_idx = text.index("encodeScheduledJobsList()", initialize_idx)
    paired_idx = text.index('data.type === "paired"')
    paired_call_idx = text.index("initializePairedConnection()", paired_idx)
    phase4_paired_idx = text.index('phase4Message.type === "pairing_confirmed"')
    phase4_call_idx = text.index("initializePairedConnection()", phase4_paired_idx)
    assert initialize_idx < list_idx < paired_idx < paired_call_idx
    assert phase4_paired_idx < phase4_call_idx
    assert "ws.send(JSON.stringify(encodeScheduledJobsList()))" in text
    # Not requested on bare websocket open.
    open_start = text.index("ws.onopen = () => {")
    open_end = initialize_idx
    assert "encodeScheduledJobsList" not in text[open_start:open_end]


def test_page_applies_sanitized_jobs_and_exact_id_updates():
    text = _page()
    assert 'message.type === "scheduled_jobs"' in text
    assert "setScheduledJobs(message.jobs)" in text
    assert "replaceScheduledJobInList(scheduledJobsRef.current, message.job)" in text
    assert "setScheduledJobPendingControl(" in text
    assert "clearScheduledJobPendingControl(" in text
    apply_block = _apply_block()
    assert "addMessage(" not in apply_block
    assert "localStorage" not in apply_block
    assert "sessionStorage" not in apply_block


def test_page_controls_use_encoders_and_ignore_duplicates():
    text = _page()
    assert "encodeScheduledJobPause(jobId)" in text
    assert "encodeScheduledJobResume(jobId)" in text
    assert "encodeScheduledJobCancel(jobId)" in text
    assert "setScheduledJobPendingControl(current, jobId, control)" in text
    assert "if (!pending) {\n      return;\n    }" in text or "if (!pending) {" in text
    assert "pauseScheduledJob" in text
    assert "resumeScheduledJob" in text
    assert "cancelScheduledJob" in text


def test_page_sets_bounded_live_status_phrases():
    text = _page()
    assert 'setScheduledJobsStatus("List loaded.")' in text
    assert 'setScheduledJobsStatus("Correlated update received.")' in text
    assert 'setScheduledJobsStatus(\n      control === "pause"\n        ? "Pause requested."\n        : control === "resume"\n          ? "Resume requested."\n          : "Cancel requested.",\n    )' in text or (
        '?"Pause requested."' in text.replace(" ", "")
        and '"Resume requested."' in text
        and '"Cancel requested."' in text
    )
    assert "statusMessage={scheduledJobsStatus}" in text


def test_page_ignores_stale_errors_before_setting_global_error():
    apply_block = _apply_block()
    # Correlation must precede any error write.
    find_idx = apply_block.index(
        "scheduledJobsRef.current.find((job) => job.jobId === message.job_id)"
    )
    error_idx = apply_block.index("setScheduledJobsError(message.code)")
    assert find_idx < error_idx
    # Stale/unknown IDs return before any error mutation.
    stale_start = apply_block.index("if (!current) {")
    stale_end = apply_block.index("}", stale_start) + 1
    stale_body = apply_block[stale_start:stale_end]
    assert "return;" in stale_body
    assert stale_start < error_idx
    assert "setScheduledJobsError(" not in stale_body
    assert "setScheduledJobsStatus(" not in stale_body
    assert "setScheduledJobs(" not in stale_body
    assert "addMessage(" not in apply_block
    # No error write appears before the correlated job is resolved.
    assert "setScheduledJobsError(message.code)" not in apply_block[:find_idx]


def test_page_correlated_errors_clear_only_matching_job_pending():
    apply_block = _apply_block()
    assert "clearScheduledJobPendingControl(current, message.job_id)" in apply_block
    assert "replaceScheduledJobInList(scheduledJobsRef.current, cleared)" in apply_block
    assert "setScheduledJobsError(message.code)" in apply_block
    # Only the correlated job is rewritten; no full-list wipe on error.
    assert "setScheduledJobs(Object.freeze([]))" not in apply_block
    assert "scheduledJobsRef.current = Object.freeze([])" not in apply_block
    # Error write happens only after correlation and pending clear attempt.
    clear_idx = apply_block.index("clearScheduledJobPendingControl(current, message.job_id)")
    error_idx = apply_block.index("setScheduledJobsError(message.code)")
    assert clear_idx < error_idx


def test_page_errors_use_safe_codes_only_and_clear_on_disconnect():
    text = _page()
    assert "setScheduledJobsError(message.code)" in text
    assert "clearScheduledJobsState" in text
    assert "clearScheduledJobsState();" in text
    close_start = text.index("ws.onclose = () => {")
    close_end = text.index("}, [serverUrl, pairingCode", close_start)
    close_block = text[close_start:close_end]
    assert "clearScheduledJobsState()" in close_block
    assert "clearProductivityLifecycle()" in close_block
    clear_fn = text[
        text.index("const clearScheduledJobsState") : text.index(
            "const applyScheduledJobsMessage"
        )
    ]
    assert "setScheduledJobsError(undefined)" in clear_fn
    assert "setScheduledJobsStatus(undefined)" in clear_fn
    assert "<ScheduledJobsPanel" in text
    assert "error={scheduledJobsError}" in text
    assert "jobs={scheduledJobs}" in text


def test_page_preserves_productivity_document_and_voice_clearing():
    text = _page()
    assert "clearProductivityLifecycle()" in text
    assert "parseProductivityServerMessage(event.data)" in text
    assert "forgetDocumentTask" in text
    assert "cancelVoiceCapture()" in text
    assert "speechOutputRef.current?.cancel()" in text


def test_panel_keeps_labelled_controls_and_live_regions():
    text = _panel()
    assert 'aria-labelledby={headingId}' in text
    assert 'aria-label="Scheduled job list"' in text
    assert 'role="status"' in text
    assert 'aria-live="polite"' in text
    assert 'role="alert"' in text
    assert 'type="button"' in text
    assert "disabled={pending}" in text
    assert "aria-label={`Pause ${job.actionLabel" in text
    assert "aria-label={`Resume ${job.actionLabel" in text
    assert "aria-label={`Cancel ${job.actionLabel" in text
