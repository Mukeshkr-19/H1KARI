# Phase 6 Command Center Accessibility & WCAG 2.1 AA Checklist

This checklist verifies accessibility compliance for the Phase 6 Command Center UI components (`hikari-frontend/src/components/phase6/`).

---

## 1. Landmark & Heading Structure (WCAG 1.3.1)
- [x] Main container uses `<main aria-label="H1KARI Phase 6 Command Center">`.
- [x] Header uses `<header>` landmark.
- [x] Sections use `<section aria-labelledby="...">` landmarks.
- [x] Logical heading hierarchy (`<h2>` for main title, `<h3>` for panel titles, `<h4>` for sub-sections).

## 2. Status & Color Independence (WCAG 1.4.1)
- [x] Status is never communicated by color alone. Every badge includes both an SVG/symbol icon and explicit text (e.g., `[Ready] Ready`, `[Failed] Failed`, `[Approval] Awaiting Owner Approval`).
- [x] High contrast text colors for light and dark modes (`text-slate-900`, `dark:text-slate-100`).

## 3. Keyboard Navigation & Focus (WCAG 2.1.1, 2.4.7)
- [x] All interactive buttons have explicit focus rings (`focus-visible:ring-2 focus-visible:ring-sky-500`).
- [x] Native `<button>` elements are used for all clickable actions.
- [x] Logical tab order across all 9 domain panels.
- [x] Confirm and Reject/Cancel buttons are both fully keyboard accessible.

## 4. Interaction Targets & Spacing (WCAG 2.5.5)
- [x] Minimum touch/click target size: 44px height and width (`min-h-[44px] min-w-[44px]`).
- [x] Adequate spacing between adjacent action controls to prevent mis-taps.

## 5. Screen Reader Announcements & Live Regions (WCAG 4.1.3)
- [x] Global screen reader live region `<div role="status" aria-live="polite" aria-atomic="true">` for status and error updates.
- [x] Agent run step progress bar includes `role="progressbar"`, `aria-valuenow`, `aria-valuemin`, and `aria-valuemax`.
- [x] Tabular repository hit results use `<table>`, `<caption className="sr-only">`, `<th scope="col">`, and `<td>`.

## 6. Confirmation & Risk Disclosures (WCAG 3.3.4)
- [x] Home Assistant confirmation panel explicitly displays:
  1. **WHAT**: Service action (`domain.service`)
  2. **TARGET**: Target entity (`entity_id`)
  3. **EFFECT**: Human-readable effect summary
  4. **EXPIRY**: Expiration time
- [x] Cancel/Reject button is given equal visual weight, target size, and focusability as the Confirm button.

## 7. Responsive Zoom & Motion (WCAG 1.4.10, 2.3.3)
- [x] Flexible Tailwind grid layout supporting 200% to 400% browser zoom without horizontal overflow.
- [x] Reduced motion query overrides (`motion-reduce:transition-none`).
- [x] Safe fallback banner displayed when backend status is `unavailable`.
