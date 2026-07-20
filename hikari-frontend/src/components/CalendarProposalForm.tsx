"use client";

import { useEffect, useId, useRef, type Ref } from "react";
import {
  mapCalendarValidationMessage,
  type CalendarDraftFields,
  type CalendarFieldName,
  type CalendarFormMode,
  type CalendarReadFields,
  type CalendarValidationCode,
} from "@/utils/productivity/calendarProposal";

export type CalendarProposalFormProps = Readonly<{
  mode: CalendarFormMode;
  readFields: CalendarReadFields;
  draftFields: CalendarDraftFields;
  pending: boolean;
  disabled?: boolean;
  validationCode?: CalendarValidationCode;
  validationField?: CalendarFieldName;
  onModeChange: (mode: CalendarFormMode) => void;
  onReadChange: (fields: CalendarReadFields) => void;
  onDraftChange: (fields: CalendarDraftFields) => void;
  onSubmit: () => void;
  onReset: () => void;
  headingRef?: Ref<HTMLHeadingElement>;
}>;

export function CalendarProposalForm({
  mode,
  readFields,
  draftFields,
  pending,
  disabled = false,
  validationCode,
  validationField,
  onModeChange,
  onReadChange,
  onDraftChange,
  onSubmit,
  onReset,
  headingRef,
}: CalendarProposalFormProps) {
  const instanceId = useId();
  const headingId = `${instanceId}-heading`;
  const readStartId = `${instanceId}-read-start`;
  const readEndId = `${instanceId}-read-end`;
  const readCalendarNameId = `${instanceId}-read-calendar-name`;
  const draftTitleId = `${instanceId}-draft-title`;
  const draftStartId = `${instanceId}-draft-start`;
  const draftEndId = `${instanceId}-draft-end`;
  const draftCalendarNameId = `${instanceId}-draft-calendar-name`;
  const draftLocationId = `${instanceId}-draft-location`;
  const draftNotesId = `${instanceId}-draft-notes`;
  const validationMessageId = `${instanceId}-validation-message`;
  const readStartRef = useRef<HTMLInputElement>(null);
  const readEndRef = useRef<HTMLInputElement>(null);
  const readCalendarNameRef = useRef<HTMLInputElement>(null);
  const draftTitleRef = useRef<HTMLInputElement>(null);
  const draftStartRef = useRef<HTMLInputElement>(null);
  const draftEndRef = useRef<HTMLInputElement>(null);
  const draftCalendarNameRef = useRef<HTMLInputElement>(null);
  const draftLocationRef = useRef<HTMLInputElement>(null);
  const draftNotesRef = useRef<HTMLTextAreaElement>(null);

  const locked = pending || disabled;
  const validationMessage = validationCode
    ? mapCalendarValidationMessage(mode, validationCode)
    : "";
  const activeField =
    mode === "read"
      ? validationField === "calendarName" ||
          validationField === "start" ||
          validationField === "end"
        ? validationField
        : undefined
      : validationField === "title" ||
          validationField === "start" ||
          validationField === "end" ||
          validationField === "calendarName" ||
          validationField === "location" ||
          validationField === "notes"
        ? validationField
        : undefined;

  useEffect(() => {
    if (!validationCode || !activeField) {
      return;
    }
    const focusMap: Record<string, HTMLElement | null | undefined> =
      mode === "read"
        ? {
            start: readStartRef.current,
            end: readEndRef.current,
            calendarName: readCalendarNameRef.current,
          }
        : {
            title: draftTitleRef.current,
            start: draftStartRef.current,
            end: draftEndRef.current,
            calendarName: draftCalendarNameRef.current,
            location: draftLocationRef.current,
            notes: draftNotesRef.current,
          };
    focusMap[activeField]?.focus();
  }, [validationCode, activeField, mode]);

  const submitDisabled =
    locked ||
    (mode === "read"
      ? readFields.start.trim().length < 1 || readFields.end.trim().length < 1
      : draftFields.title.trim().length < 1 ||
        draftFields.start.trim().length < 1 ||
        draftFields.end.trim().length < 1 ||
        draftFields.calendarName.trim().length < 1);

  return (
    <section
      className="rounded-xl border border-gray-800 bg-[#1a1a2e] p-4"
      aria-labelledby={headingId}
    >
      <h2
        id={headingId}
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold text-gray-100"
      >
        Calendar proposal
      </h2>
      <p className="mt-1 text-sm text-gray-400">
        Review a calendar read or event draft before anything is accessed. Use an
        explicit timezone offset or Z on every date-time value.
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onModeChange("read")}
          disabled={locked}
          aria-pressed={mode === "read"}
          className={`rounded-lg px-3 py-2 text-sm font-semibold ${
            mode === "read"
              ? "bg-purple-600 text-white"
              : "border border-gray-600 text-gray-100 hover:bg-gray-800"
          } disabled:cursor-not-allowed disabled:opacity-50`}
        >
          Read calendar
        </button>
        <button
          type="button"
          onClick={() => onModeChange("draft")}
          disabled={locked}
          aria-pressed={mode === "draft"}
          className={`rounded-lg px-3 py-2 text-sm font-semibold ${
            mode === "draft"
              ? "bg-purple-600 text-white"
              : "border border-gray-600 text-gray-100 hover:bg-gray-800"
          } disabled:cursor-not-allowed disabled:opacity-50`}
        >
          Draft event
        </button>
      </div>

      {mode === "read" ? (
        <div className="mt-4 space-y-4">
          <div>
            <label
              htmlFor={readStartId}
              className="mb-2 block text-sm font-medium text-gray-200"
            >
              Start
            </label>
            <input
              id={readStartId}
              ref={readStartRef}
              type="text"
              inputMode="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="2026-07-18T09:00:00-04:00"
              value={readFields.start}
              disabled={locked}
              aria-invalid={activeField === "start"}
              aria-describedby={
                activeField === "start" ? validationMessageId : undefined
              }
              onChange={(event) =>
                onReadChange(
                  Object.freeze({
                    ...readFields,
                    start: event.target.value,
                  }),
                )
              }
              className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 font-mono text-sm text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          <div>
            <label
              htmlFor={readEndId}
              className="mb-2 block text-sm font-medium text-gray-200"
            >
              End
            </label>
            <input
              id={readEndId}
              ref={readEndRef}
              type="text"
              inputMode="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="2026-07-18T10:00:00-04:00"
              value={readFields.end}
              disabled={locked}
              aria-invalid={activeField === "end"}
              aria-describedby={
                activeField === "end" ? validationMessageId : undefined
              }
              onChange={(event) =>
                onReadChange(
                  Object.freeze({
                    ...readFields,
                    end: event.target.value,
                  }),
                )
              }
              className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 font-mono text-sm text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          <div>
            <label
              htmlFor={readCalendarNameId}
              className="mb-2 block text-sm font-medium text-gray-200"
            >
              Calendar name (optional)
            </label>
            <input
              id={readCalendarNameId}
              ref={readCalendarNameRef}
              type="text"
              autoComplete="off"
              value={readFields.calendarName}
              disabled={locked}
              aria-invalid={activeField === "calendarName"}
              aria-describedby={
                activeField === "calendarName" ? validationMessageId : undefined
              }
              onChange={(event) =>
                onReadChange(
                  Object.freeze({
                    ...readFields,
                    calendarName: event.target.value,
                  }),
                )
              }
              className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          <div>
            <label
              htmlFor={draftTitleId}
              className="mb-2 block text-sm font-medium text-gray-200"
            >
              Title
            </label>
            <input
              id={draftTitleId}
              ref={draftTitleRef}
              type="text"
              autoComplete="off"
              value={draftFields.title}
              disabled={locked}
              aria-invalid={activeField === "title"}
              aria-describedby={
                activeField === "title" ? validationMessageId : undefined
              }
              onChange={(event) =>
                onDraftChange(
                  Object.freeze({
                    ...draftFields,
                    title: event.target.value,
                  }),
                )
              }
              className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          <div>
            <label
              htmlFor={draftStartId}
              className="mb-2 block text-sm font-medium text-gray-200"
            >
              Start
            </label>
            <input
              id={draftStartId}
              ref={draftStartRef}
              type="text"
              inputMode="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="2026-07-18T09:00:00-04:00"
              value={draftFields.start}
              disabled={locked}
              aria-invalid={activeField === "start"}
              aria-describedby={
                activeField === "start" ? validationMessageId : undefined
              }
              onChange={(event) =>
                onDraftChange(
                  Object.freeze({
                    ...draftFields,
                    start: event.target.value,
                  }),
                )
              }
              className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 font-mono text-sm text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          <div>
            <label
              htmlFor={draftEndId}
              className="mb-2 block text-sm font-medium text-gray-200"
            >
              End
            </label>
            <input
              id={draftEndId}
              ref={draftEndRef}
              type="text"
              inputMode="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="2026-07-18T10:00:00-04:00"
              value={draftFields.end}
              disabled={locked}
              aria-invalid={activeField === "end"}
              aria-describedby={
                activeField === "end" ? validationMessageId : undefined
              }
              onChange={(event) =>
                onDraftChange(
                  Object.freeze({
                    ...draftFields,
                    end: event.target.value,
                  }),
                )
              }
              className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 font-mono text-sm text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          <div>
            <label
              htmlFor={draftCalendarNameId}
              className="mb-2 block text-sm font-medium text-gray-200"
            >
              Calendar name
            </label>
            <input
              id={draftCalendarNameId}
              ref={draftCalendarNameRef}
              type="text"
              autoComplete="off"
              value={draftFields.calendarName}
              disabled={locked}
              aria-invalid={activeField === "calendarName"}
              aria-describedby={
                activeField === "calendarName" ? validationMessageId : undefined
              }
              onChange={(event) =>
                onDraftChange(
                  Object.freeze({
                    ...draftFields,
                    calendarName: event.target.value,
                  }),
                )
              }
              className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          <div>
            <label
              htmlFor={draftLocationId}
              className="mb-2 block text-sm font-medium text-gray-200"
            >
              Location (optional)
            </label>
            <input
              id={draftLocationId}
              ref={draftLocationRef}
              type="text"
              autoComplete="off"
              value={draftFields.location}
              disabled={locked}
              aria-invalid={activeField === "location"}
              aria-describedby={
                activeField === "location" ? validationMessageId : undefined
              }
              onChange={(event) =>
                onDraftChange(
                  Object.freeze({
                    ...draftFields,
                    location: event.target.value,
                  }),
                )
              }
              className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          <div>
            <label
              htmlFor={draftNotesId}
              className="mb-2 block text-sm font-medium text-gray-200"
            >
              Notes (optional)
            </label>
            <textarea
              id={draftNotesId}
              ref={draftNotesRef}
              rows={6}
              autoComplete="off"
              value={draftFields.notes}
              disabled={locked}
              aria-invalid={activeField === "notes"}
              aria-describedby={
                activeField === "notes" ? validationMessageId : undefined
              }
              onChange={(event) =>
                onDraftChange(
                  Object.freeze({
                    ...draftFields,
                    notes: event.target.value,
                  }),
                )
              }
              className="w-full resize-y rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>
        </div>
      )}

      {validationMessage ? (
        <div className="mt-4 rounded-lg border border-red-500/40 bg-red-950/20 p-3" role="alert">
          <h3 className="text-sm font-semibold text-red-200">Check this field</h3>
          <p id={validationMessageId} className="mt-1 text-sm text-red-100">
            {validationMessage}
          </p>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={onSubmit}
          disabled={submitDisabled}
          className="rounded-lg bg-purple-600 px-4 py-2.5 font-semibold text-white hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {mode === "read" ? "Review calendar read" : "Review event draft"}
        </button>
        <button
          type="button"
          onClick={onReset}
          disabled={pending}
          className="rounded-lg border border-gray-600 px-4 py-2.5 font-semibold text-gray-100 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Clear form
        </button>
      </div>
    </section>
  );
}
