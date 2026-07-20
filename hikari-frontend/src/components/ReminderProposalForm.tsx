"use client";

import { useEffect, useId, useRef, type Ref } from "react";
import {
  mapReminderValidationMessage,
  type ReminderFieldName,
  type ReminderFields,
  type ReminderValidationCode,
} from "@/utils/productivity/reminderProposal";

export type ReminderProposalFormProps = Readonly<{
  fields: ReminderFields;
  pending: boolean;
  disabled?: boolean;
  validationCode?: ReminderValidationCode;
  validationField?: ReminderFieldName;
  onChange: (fields: ReminderFields) => void;
  onSubmit: () => void;
  onReset: () => void;
  headingRef?: Ref<HTMLHeadingElement>;
}>;

export function ReminderProposalForm({
  fields,
  pending,
  disabled = false,
  validationCode,
  validationField,
  onChange,
  onSubmit,
  onReset,
  headingRef,
}: ReminderProposalFormProps) {
  const instanceId = useId();
  const headingId = `${instanceId}-heading`;
  const titleId = `${instanceId}-title`;
  const remindAtId = `${instanceId}-remind-at`;
  const notesId = `${instanceId}-notes`;
  const listNameId = `${instanceId}-list-name`;
  const validationMessageId = `${instanceId}-validation-message`;
  const titleRef = useRef<HTMLInputElement>(null);
  const remindAtRef = useRef<HTMLInputElement>(null);
  const notesRef = useRef<HTMLTextAreaElement>(null);
  const listNameRef = useRef<HTMLInputElement>(null);

  const locked = pending || disabled;
  const validationMessage = validationCode
    ? mapReminderValidationMessage(validationCode)
    : "";
  const activeField =
    validationField === "title" ||
    validationField === "remindAt" ||
    validationField === "notes" ||
    validationField === "listName"
      ? validationField
      : undefined;

  useEffect(() => {
    if (!validationCode || !activeField) {
      return;
    }
    if (activeField === "title") {
      titleRef.current?.focus();
      return;
    }
    if (activeField === "remindAt") {
      remindAtRef.current?.focus();
      return;
    }
    if (activeField === "notes") {
      notesRef.current?.focus();
      return;
    }
    listNameRef.current?.focus();
  }, [validationCode, activeField]);

  const submitDisabled =
    locked || fields.title.length < 1 || fields.remindAt.length < 1;

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
        Reminder proposal
      </h2>
      <p className="mt-1 text-sm text-gray-400">
        Review a reminder before anything is created. Nothing is scheduled from
        this form.
      </p>

      <div className="mt-4 space-y-4">
        <div>
          <label
            htmlFor={titleId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Title
          </label>
          <input
            id={titleId}
            ref={titleRef}
            type="text"
            autoComplete="off"
            value={fields.title}
            disabled={locked}
            aria-invalid={activeField === "title"}
            aria-describedby={
              activeField === "title" ? validationMessageId : undefined
            }
            onChange={(event) =>
              onChange(
                Object.freeze({
                  ...fields,
                  title: event.target.value,
                }),
              )
            }
            className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <div>
          <label
            htmlFor={remindAtId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Remind at
          </label>
          <input
            id={remindAtId}
            ref={remindAtRef}
            type="text"
            inputMode="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="2026-07-20T09:00:00-04:00"
            value={fields.remindAt}
            disabled={locked}
            aria-invalid={activeField === "remindAt"}
            aria-describedby={
              activeField === "remindAt" ? validationMessageId : undefined
            }
            onChange={(event) =>
              onChange(
                Object.freeze({
                  ...fields,
                  remindAt: event.target.value,
                }),
              )
            }
            className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 font-mono text-sm text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <div>
          <label
            htmlFor={notesId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Notes (optional)
          </label>
          <textarea
            id={notesId}
            ref={notesRef}
            rows={5}
            autoComplete="off"
            value={fields.notes}
            disabled={locked}
            aria-invalid={activeField === "notes"}
            aria-describedby={
              activeField === "notes" ? validationMessageId : undefined
            }
            onChange={(event) =>
              onChange(
                Object.freeze({
                  ...fields,
                  notes: event.target.value,
                }),
              )
            }
            className="w-full resize-y rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <div>
          <label
            htmlFor={listNameId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            List name (optional)
          </label>
          <input
            id={listNameId}
            ref={listNameRef}
            type="text"
            autoComplete="off"
            value={fields.listName}
            disabled={locked}
            aria-invalid={activeField === "listName"}
            aria-describedby={
              activeField === "listName" ? validationMessageId : undefined
            }
            onChange={(event) =>
              onChange(
                Object.freeze({
                  ...fields,
                  listName: event.target.value,
                }),
              )
            }
            className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>
      </div>

      {validationMessage ? (
        <div
          className="mt-4 rounded-lg border border-red-500/40 bg-red-950/20 p-3"
          role="alert"
        >
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
          Prepare reminder
        </button>
        <button
          type="button"
          onClick={onReset}
          disabled={pending}
          className="rounded-lg border border-gray-600 px-4 py-2.5 font-semibold text-gray-100 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Reset
        </button>
      </div>
    </section>
  );
}
