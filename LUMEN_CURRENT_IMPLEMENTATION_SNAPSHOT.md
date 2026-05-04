# Lumen Current Implementation Snapshot

## Purpose

This file gives Codex a compact factual snapshot of the current app so it can reuse existing work rather than rebuild from scratch.

Use this file together with:

1. `LUMEN_CODEX_CONTEXT.md`
2. `ui-mockups-readme.md`

The actual repository remains the source of truth for implementation details. Codex should inspect the codebase before editing.

## Current App Foundation

The current app is a FastAPI web layer around the Lumen LangGraph agent engine.

Known foundations include:

- FastAPI backend.
- LangGraph workflow orchestration.
- Local model serving support through Ollama or LM Studio-style providers.
- Durable product records for workflow runs, referrals, review tasks, communication drafts, audit events, therapist profiles, intake records, and early clinical documentation.
- SQLAlchemy/Alembic persistence.
- Local SQLite default with PostgreSQL configuration path.
- Workflow event persistence and SSE streaming.
- Sample payload support.
- Model health endpoint/panel.
- Smoke test script.

## Existing UI Capabilities To Reuse

The current UI already includes or has included:

- Workflow runner for referral intake and session report workflows.
- Sample payload buttons from `samples/`.
- Model health panel backed by `/api/health/models`.
- Durable referral queue.
- Referral detail view.
- Human review inbox with approve, reject, request-change, and escalate persistence.
- Seeded therapist profile list.
- Therapist profile creation with availability blocks.
- Deterministic therapist matching.
- Appointment slot proposal from referral detail.
- Intake checklist.
- Consent records.
- Governed intake document uploads.
- Generic questionnaire response capture.
- Reminder drafts.
- Therapist prep brief drafts.
- Referral CSV batch imports.
- Source artifact storage for imported referrals.
- Row-level import errors.
- Integration health panels.
- Security posture and feedback metrics panels.
- Workflow events and diagnostic traces.

These should be reused where possible, but reorganised around the new simplified navigation.

## Existing API / Route Areas To Inspect

Codex should inspect current implementations around these route areas before making frontend assumptions:

- `GET /api/health/models`
- `GET /api/examples`
- `GET /api/referrals`
- `GET /api/referrals/{referral_id}`
- `GET /api/review-tasks?status=open`
- `POST /api/review-tasks/{task_id}/actions`
- `GET /api/therapists`
- `POST /api/therapists`
- `POST /api/referrals/{referral_id}/match`
- `POST /api/referrals/{referral_id}/appointment-proposals`
- `POST /api/appointments/{appointment_id}/confirm`
- `GET /api/intake/templates`
- `POST /api/referrals/{referral_id}/intake`
- `GET /api/referrals/{referral_id}/intake`
- `POST /api/referrals/{referral_id}/documents`
- `POST /api/referrals/{referral_id}/intake-reminder`
- `POST /api/intake/items/{item_id}/complete`
- `POST /api/consent-records/{consent_id}/complete`
- `POST /api/referrals/{referral_id}/questionnaires`
- `POST /api/referrals/{referral_id}/prep-brief`
- `GET /api/integrations/health`
- `POST /api/integrations/referral-batches`
- `GET /api/integrations/referral-batches`
- `GET /api/integrations/import-errors`
- `POST /api/integrations/email-referrals`
- `GET /api/security/context`
- `GET /api/security/posture`
- `GET /api/workflows`
- `POST /api/run-workflow`
- `GET /api/events/{job_id}`

Some routes may support broader clinical/report functionality. For this phase, Codex should not expand those areas unless needed to preserve compatibility.

## Existing Workflow Behaviour To Preserve

The current app has a workflow runner that can start a `new_referral` workflow.

Workflow events are persisted and streamed.

Human approvals are stored in `human_review_tasks`.

Approving resumable gates such as `match_approval` and `send_approval` may start follow-on workflow runs with the approved gate recorded in the request.

This functionality is valuable but should not remain the main user experience. It should be moved or visually demoted into:

- New Referral advanced trace
- System / Agents diagnostics

## Current Referral / Admin Capabilities To Reorganise

From referral detail, the app already has or is intended to have:

- Deterministic matching using therapist facts.
- Appointment slot proposals.
- Human confirmation of proposed slots.
- Intake creation from an active intake template.
- Intake item completion.
- Consent record completion.
- Intake document upload.
- Missing-item reminder drafts.
- Generic questionnaire capture.
- Therapist prep brief generation.

These map naturally into the new Workbench.

## Current Therapist / Clinical Capabilities To Hide Or Deprioritise

The current app may include:

- Clinical foundation page.
- Patient workspace.
- Session notes.
- Clinical library.
- Retrieval chunks.
- Report draft editing.
- Claim-to-evidence validation.
- Therapist sign-off.
- Markdown export.

Do not build around these in the next phase.

For this phase, either leave them in a lower-priority system/demo area, hide them from primary admin navigation, or avoid touching them unless necessary.

The active UI should not present the therapist session workflow as part of the MVP navigation.

## Current Integration Position

The app has some integration-related scaffolding:

- CSV referral batch import.
- Manual email referral capture.
- Import status/history.
- Row-level import errors.
- Integration health panels.

For this phase:

- Keep Integrations mostly unchanged.
- Add or retain calendar placeholder/status.
- Do not implement real Google Calendar API integration yet.
- Support manual/mock availability and manually recorded/demo appointments.
- Structure the UI so future Google Calendar setup can be connected later.
- Referral-specific communication should be shown in Workbench, not Integrations.

## Current Problems The Next UI Pass Should Address

The current app still feels too much like a technical workflow console.

The agentic work can feel like a black box.

The admin needs a clear view of:

- Where each referral is in the journey.
- What is blocking it.
- Who owns the next action.
- What agents have done.
- What needs human approval.
- What happened after approve/reject/request changes/escalate.
- What previous actions and transitions occurred.

The next UI pass should make the workflow operationally legible.

## Current UI Mapping To New Navigation

Suggested mapping:

| Current / Existing Area | New Location |
|---|---|
| Referral queue | Overview |
| Referral detail | Workbench |
| Review inbox | Overview `Needs attention` + Workbench task sections |
| Intake page/sections | Workbench for referral-specific items; Overview for blockers |
| Scheduling page/sections | Workbench for referral-specific slots; Therapists for availability/capacity |
| Therapist list/profile | Therapists |
| Integrations | Integrations |
| Model health | System / Agents |
| Workflow runner | New Referral + System / Agents |
| View trace | New Referral advanced trace + System / Agents diagnostics |
| Clinical/report pages | Hide/deprioritise for this phase |

## Known Scope Guardrails

For the next phase, Codex should avoid:

- Broad rewrites.
- Real Google Calendar API implementation.
- Real outbound email integration unless it is already low-risk and configured.
- Building therapist session workflow.
- Building report review workflow.
- Building live transcription.
- Treating technical workflow runner as the main user journey.
- Deleting useful diagnostic features rather than moving them.
- Adding complex new state without checking existing models/routes first.

## First Codebase Audit Questions For Codex

Before implementation, Codex should answer internally or in plan mode:

1. Which current frontend components can become Overview, Workbench, New Referral, Therapists, Integrations, and System / Agents?
2. Which components can be moved rather than rewritten?
3. Which routes already support Workbench actions?
4. Which referral statuses currently exist, and how do they map to the active status model?
5. Which review task types currently exist?
6. Which approval actions are durable and which are simulated?
7. Where is agent activity/audit data available?
8. Which current pages should be hidden, demoted, or moved into System / Agents?
9. What is the smallest safe patch that improves workflow clarity without breaking existing flows?

## Recommended First Implementation Patch

Recommended first patch:

1. Simplify navigation to the six active pages.
2. Rework Overview into a command centre with journey board, compact referral queue, needs-attention section, and health strip.
3. Rework/refocus Workbench as the selected-referral processing page.
4. Move referral creation/demo workflow into New Referral.
5. Move technical traces into expandable advanced trace and System / Agents.
6. Move review task visibility into Overview/Workbench.
7. Move scheduling/intake functions into Workbench/Therapists.
8. Add clear placeholder/status for Google Calendar setup.
9. Add or improve agent activity/audit timeline visibility.

The goal is to make the admin workflow understandable and trustworthy, not visually perfect.
