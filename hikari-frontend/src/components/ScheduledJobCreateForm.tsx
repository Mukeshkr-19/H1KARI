"use client";

import { useEffect, useId, useRef, type FormEvent, type Ref } from "react";
import {
  mapScheduleValidationMessage,
  type ScheduleFieldName,
  type ScheduleProposalFields,
  type ScheduleValidationCode,
} from "@/utils/productivity/scheduleProposal";

export type ScheduledJobCreateFormProps = Readonly<{
  fields: ScheduleProposalFields;
  pending: boolean;
  disabled?: boolean;
  actionLocked?: boolean;
  validationCode?: ScheduleValidationCode;
  validationField?: ScheduleFieldName;
  onChange: (fields: ScheduleProposalFields) => void;
  onSubmit: () => void;
  onReset: () => void;
  headingRef?: Ref<HTMLHeadingElement>;
}>;

export function ScheduledJobCreateForm({
  fields,
  pending,
  disabled = false,
  actionLocked = false,
  validationCode,
  validationField,
  onChange,
  onSubmit,
  onReset,
  headingRef,
}: ScheduledJobCreateFormProps) {
  const instanceId = useId();
  const headingId = `${instanceId}-heading`;
  const descriptionId = `${instanceId}-description`;
  const actionId = `${instanceId}-action`;
  const nextRunAtId = `${instanceId}-next-run-at`;
  const maxAttemptsId = `${instanceId}-max-attempts`;
  const quietEnabledId = `${instanceId}-quiet-enabled`;
  const quietGroupId = `${instanceId}-quiet-group`;
  const quietStartId = `${instanceId}-quiet-start`;
  const quietEndId = `${instanceId}-quiet-end`;
  const quietTimezoneId = `${instanceId}-quiet-timezone`;
  const validationId = `${instanceId}-validation`;

  const actionRef = useRef<HTMLSelectElement>(null);
  const nextRunAtRef = useRef<HTMLInputElement>(null);
  const maxAttemptsRef = useRef<HTMLSelectElement>(null);
  const quietEnabledRef = useRef<HTMLInputElement>(null);
  const quietStartRef = useRef<HTMLInputElement>(null);
  const quietEndRef = useRef<HTMLInputElement>(null);
  const quietTimezoneRef = useRef<HTMLInputElement>(null);

  const locked = pending || disabled;
  const validationMessage = validationCode
    ? mapScheduleValidationMessage(validationCode)
    : "";

  useEffect(() => {
    if (!validationCode || !validationField) {
      return;
    }
    const refs = {
      action: actionRef,
      nextRunAt: nextRunAtRef,
      maxAttempts: maxAttemptsRef,
      quietHoursEnabled: quietEnabledRef,
      quietStartMinute: quietStartRef,
      quietEndMinute: quietEndRef,
      quietTimezone: quietTimezoneRef,
    };
    refs[validationField].current?.focus();
  }, [validationCode, validationField]);

  const describedBy = (field: ScheduleFieldName): string | undefined =>
    validationField === field ? validationId : undefined;

  const change = (updates: Partial<ScheduleProposalFields>) => {
    onChange(Object.freeze({ ...fields, ...updates }));
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!locked) {
      onSubmit();
    }
  };

  return (
    <section
      className="rounded-xl border border-gray-800 bg-[#1a1a2e] p-4"
      aria-labelledby={headingId}
      aria-describedby={descriptionId}
    >
      <h2
        id={headingId}
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold text-gray-100"
      >
        Schedule a one-shot job
      </h2>
      <p id={descriptionId} className="mt-1 text-sm text-gray-400">
        Prepare one browser research or calendar read job for review. Nothing is
        scheduled from this form.
      </p>

      <form className="mt-4 space-y-4" onSubmit={handleSubmit} noValidate>
        <div>
          <label
            htmlFor={actionId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Action
          </label>
          <select
            id={actionId}
            ref={actionRef}
            value={fields.action}
            disabled={locked || actionLocked}
            aria-invalid={validationField === "action"}
            aria-describedby={describedBy("action")}
            onChange={(event) =>
              change({
                action: event.target.value as ScheduleProposalFields["action"],
              })
            }
            className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          >
            <option value="browser.research">Browser research</option>
            <option value="calendar.read">Calendar read</option>
          </select>
        </div>

        <div>
          <label
            htmlFor={nextRunAtId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Run once at
          </label>
          <input
            id={nextRunAtId}
            ref={nextRunAtRef}
            type="text"
            inputMode="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="2026-07-21T09:00:00-04:00"
            value={fields.nextRunAt}
            disabled={locked}
            aria-invalid={validationField === "nextRunAt"}
            aria-describedby={describedBy("nextRunAt")}
            onChange={(event) => change({ nextRunAt: event.target.value })}
            className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 font-mono text-sm text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <div>
          <label
            htmlFor={maxAttemptsId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Maximum attempts
          </label>
          <select
            id={maxAttemptsId}
            ref={maxAttemptsRef}
            value={fields.maxAttempts}
            disabled={locked}
            aria-invalid={validationField === "maxAttempts"}
            aria-describedby={describedBy("maxAttempts")}
            onChange={(event) => change({ maxAttempts: event.target.value })}
            className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          >
            {[1, 2, 3, 4, 5].map((attempts) => (
              <option key={attempts} value={String(attempts)}>
                {attempts}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor={quietEnabledId}
            className="flex items-center gap-3 text-sm font-medium text-gray-200"
          >
            <input
              id={quietEnabledId}
              ref={quietEnabledRef}
              type="checkbox"
              checked={fields.quietHoursEnabled}
              disabled={locked}
              aria-invalid={validationField === "quietHoursEnabled"}
              aria-describedby={describedBy("quietHoursEnabled")}
              onChange={(event) =>
                change(
                  event.target.checked
                    ? { quietHoursEnabled: true }
                    : {
                        quietHoursEnabled: false,
                        quietStartMinute: "",
                        quietEndMinute: "",
                        quietTimezone: "",
                      },
                )
              }
              className="h-4 w-4 rounded border-gray-600 bg-[#0f0f1a] text-purple-600 focus:ring-purple-500"
            />
            Use quiet hours
          </label>
        </div>

        {fields.quietHoursEnabled ? (
          <fieldset
            id={quietGroupId}
            className="space-y-4 rounded-lg border border-gray-700 p-3"
            disabled={locked}
          >
            <legend className="px-1 text-sm font-semibold text-gray-200">
              Quiet-hours window
            </legend>
            <p className="text-xs text-gray-400">
              Enter minutes after local midnight, from 0 through 1439.
            </p>

            <div>
              <label
                htmlFor={quietStartId}
                className="mb-2 block text-sm font-medium text-gray-200"
              >
                Start minute
              </label>
              <input
                id={quietStartId}
                ref={quietStartRef}
                type="number"
                min={0}
                max={1439}
                step={1}
                value={fields.quietStartMinute}
                aria-invalid={validationField === "quietStartMinute"}
                aria-describedby={describedBy("quietStartMinute")}
                onChange={(event) =>
                  change({ quietStartMinute: event.target.value })
                }
                className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white focus:border-purple-500 focus:outline-none"
              />
            </div>

            <div>
              <label
                htmlFor={quietEndId}
                className="mb-2 block text-sm font-medium text-gray-200"
              >
                End minute
              </label>
              <input
                id={quietEndId}
                ref={quietEndRef}
                type="number"
                min={0}
                max={1439}
                step={1}
                value={fields.quietEndMinute}
                aria-invalid={validationField === "quietEndMinute"}
                aria-describedby={describedBy("quietEndMinute")}
                onChange={(event) =>
                  change({ quietEndMinute: event.target.value })
                }
                className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white focus:border-purple-500 focus:outline-none"
              />
            </div>

            <div>
              <label
                htmlFor={quietTimezoneId}
                className="mb-2 block text-sm font-medium text-gray-200"
              >
                IANA timezone
              </label>
              <input
                id={quietTimezoneId}
                ref={quietTimezoneRef}
                type="text"
                autoComplete="off"
                spellCheck={false}
                placeholder="America/New_York"
                value={fields.quietTimezone}
                aria-invalid={validationField === "quietTimezone"}
                aria-describedby={describedBy("quietTimezone")}
                onChange={(event) =>
                  change({ quietTimezone: event.target.value })
                }
                className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 font-mono text-sm text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none"
              />
            </div>
          </fieldset>
        ) : null}

        {validationMessage ? (
          <div
            className="rounded-lg border border-red-500/40 bg-red-950/20 p-3"
            role="alert"
          >
            <h3 className="text-sm font-semibold text-red-200">
              Check this field
            </h3>
            <p id={validationId} className="mt-1 text-sm text-red-100">
              {validationMessage}
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap gap-3">
          <button
            type="submit"
            disabled={locked || fields.nextRunAt.length < 1}
            className="rounded-lg bg-purple-600 px-4 py-2.5 font-semibold text-white hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Prepare scheduled job
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
      </form>
    </section>
  );
}
