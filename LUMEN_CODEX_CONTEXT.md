# Lumen Codex Context

## Purpose

This is the active Codex context file for the next implementation phase.

Use this file together with:

1. `LUMEN_CURRENT_IMPLEMENTATION_SNAPSHOT.md`
2. `ui-mockups-readme.md`

Treat those three files as the active working context. Older planning, PRD, phase, and technical blueprint files are historical background only unless the user explicitly says otherwise.

## Current Product Position

Lumen is a governed AI workflow platform for small mental-health clinics.

The immediate product goal is not to build the full long-term clinic product. The immediate goal is to make the **admin referral workflow** coherent, visible, and demonstrable.

The current workflow should move a referral from initial capture to:

`first_session_ready`

That means:

- The referral has been captured.
- Structured details have been extracted.
- Missing or ambiguous information has been reviewed.
- Risk/suitability has been checked.
- Elevated or unknown risk has been escalated and resolved before matching.
- A therapist match has been recommended and approved.
- Appointment slots have been proposed and approved.
- Patient contact has been drafted and approved.
- Patient reply has been received or simulated and confirmed.
- Appointment has been confirmed or simulated.
- Intake checklist has been generated.
- Required intake is complete or formally waived.
- Therapist prep brief has been generated.

## Product Boundary

Lumen may:

- Ingest referrals, documents, forms, notes, and structured files.
- Extract, normalize, classify, retrieve, draft, compare, and summarize.
- Recommend therapist matches and next actions.
- Propose appointment slots based on explicit availability rules.
- Draft patient-facing communications.
- Maintain review tasks, source evidence, workflow events, and audit logs.
- Generate therapist prep briefs after appointment and intake readiness.

Lumen must not:

- Diagnose patients.
- Autonomously contact patients.
- Autonomously book appointments.
- Let elevated or unknown risk proceed to ordinary matching without clinical review.
- Complete the admin referral workflow unless required intake is complete or an authorised exception is recorded.
- Hide agent activity, failed transitions, or human gate decisions from the admin.

Operating principle:

> AI prepares. Humans decide.

## Active Scope For The Next Phase

The next implementation phase is UI/workflow streamlining for the admin referral product.

Focus on:

- Clear admin navigation.
- Referral journey visibility.
- Referral state coherence.
- Explicit human gates.
- Visible agent activity and audit timeline.
- Workbench-based referral processing.
- Simplified operational UI rather than a developer workflow console.
- Preserving existing backend functionality where possible.

Do not implement the therapist session workflow in this phase.

Do not build report drafting, live transcription, therapist session notes, patient workspace, report review, or protocol library UI unless existing code must be moved or hidden to simplify the admin interface.

The only therapist-related output in active scope is the **prep brief handoff** at the end of the admin referral workflow.

## Active UI Direction

Use `ui-mockups-readme.md` as the task-specific UI brief.

The simplified MVP navigation should be:

1. Overview
2. Workbench
3. New Referral
4. Therapists
5. Integrations
6. System / Agents

Remove these as standalone MVP pages:

- Review Inbox
- Intake & Scheduling

Their useful functions should be absorbed as follows:

- Cross-referral review/task visibility -> Overview under `Needs attention`
- Referral-specific approval/action handling -> Workbench
- Therapist availability and capacity -> Therapists
- Referral-specific scheduling, intake, documents, waivers, prep brief -> Workbench
- Integration status/imports -> Integrations
- Developer traces, model health, agent tests -> System / Agents

Remove global top-right `View trace` as an operational action. Trace/debug functions belong in:

- New Referral, after running a referral
- System / Agents, for diagnostics and workflow debugging

## Target Page Responsibilities

### Overview

The Overview is the admin command centre.

It should show:

- Top operational metrics
- Referral journey board
- Compact referral queue
- Needs attention section
- Operational system health strip

It should answer:

- How much work is in the system?
- Where are referrals stuck?
- What needs attention now?
- Are there clinical or operational blockers?
- Are the supporting systems healthy?

The Overview should route the user into Workbench for detailed action.

### Workbench

The Workbench is the main page for processing one referral.

It should show:

- Referral header
- Current stage, blocker, owner, next action
- Risk status and open human gates
- Journey progress
- Extracted referral information
- Missing or uncertain fields
- Blockers and review tasks
- Communication thread
- Therapist matching
- Scheduling
- Intake/checklist/documents/waivers
- Prep brief preview
- Agent activity and audit timeline

It should answer:

- What is the exact state of this referral?
- What is blocking progress?
- What should the admin do next?
- What has Lumen prepared?
- What has been approved, rejected, changed, or escalated?
- What happened previously?

### New Referral

The New Referral page is for creating, importing, or demo-running a referral.

It should include:

- Manual referral entry
- Demo examples
- Run referral workflow action
- Initial result summary
- Link to open the referral in Workbench
- Advanced trace section hidden behind an expandable area

Trace output and raw payloads should not dominate this page.

### Therapists

The Therapists page should become a capacity and matching dashboard.

It should show:

- Active/inactive therapists
- Specialties
- Languages
- Modalities
- Insurers
- Weekly capacity
- Current assigned count
- Next available slot
- Matching data completeness
- Availability grid
- Manual bookings / blocked time
- Assigned patients/referrals
- Recent matching history

Do not use raw JSON as the main availability interface.

### Integrations

Keep Integrations mostly unchanged for now.

It should cover:

- CSV/XLSX/manual batch import
- Import status/history
- Row-level import errors
- Email capture status
- Outbound email status
- Calendar/manual availability status
- Integration health cards
- Google Calendar placeholder/status

Referral-specific communication belongs in Workbench, not Integrations.

Detailed system diagnostics belong in System / Agents.

### System / Agents

Rename `System (dev)` to `System / Agents` or `Agent Control`.

It should cover:

- Agent registry
- Assigned model per agent
- Agent enabled/disabled state
- Last run
- Health
- Success/error counts
- Model health
- Recent workflow runs
- Developer/demo test bench
- Guardrails and thresholds
- Audit/debug logs

It should not be part of everyday admin referral processing.

## Calendar / Scheduling Strategy For This Phase

The user has a Google Calendar account and is creating fictitious therapist appointments for the class demo.

However, the Google Cloud project and real Google Calendar API integration are not ready.

Therefore:

- Do not implement real Google Calendar API integration in this UI pass.
- Create a clear placeholder/status area for future Google Calendar setup.
- Use manual/mock therapist availability and manually recorded/demo appointments for now.
- Keep scheduling logic and UI structured so a future Google Calendar adapter can replace or augment the manual/mock data.
- Proposed slots should come from therapist availability/manual bookings for MVP.
- Appointment creation may be simulated or recorded locally with audit trail.
- All booking actions still require human approval.

## Admin Referral Status Model

Use one primary status wherever possible.

Primary statuses:

- `new_referral`
- `normalising`
- `needs_admin_review`
- `waiting_for_missing_info`
- `needs_clinical_review`
- `clinical_escalation_review`
- `ready_for_matching`
- `match_recommended`
- `match_approved`
- `slot_options_ready`
- `awaiting_patient_contact`
- `contact_sent`
- `awaiting_patient_reply`
- `appointment_confirmed`
- `intake_packet_sent`
- `intake_incomplete`
- `intake_complete`
- `prep_brief_ready`
- `first_session_ready`
- `closed_declined`
- `closed_no_response`
- `closed_not_suitable`

Secondary flags can coexist with the primary status:

- `missing_dob`
- `missing_contact`
- `insurance_unclear`
- `duplicate_candidate`
- `risk_unknown`
- `risk_elevated`
- `calendar_conflict`
- `patient_question_pending`
- `intake_exception_recorded`

## Required Human Review Task Types

Use or map toward these task types:

- `admin_missing_info_review`
- `missing_info_message_approval`
- `duplicate_resolution`
- `clinical_risk_review`
- `suitability_review`
- `match_approval`
- `slot_offer_approval`
- `send_approval`
- `appointment_confirmation_approval`
- `intake_reminder_approval`
- `intake_exception_approval`

For this active phase, do not add therapist-session review task types unless existing code already contains them and they need to be hidden or moved out of the admin path.

## Human Gate Rules

No patient-facing message may be marked sent without human approval.

No appointment may be marked confirmed or created without human approval after patient acceptance.

No referral with unknown or elevated risk may proceed to matching without clinical review.

No admin referral may reach `first_session_ready` unless intake is complete or formally waived.

All gate actions should be audit logged.

Review actions should include, where supported:

- Approve
- Reject
- Request changes
- Escalate
- Edit draft
- Add reason

## Agent Activity Visibility

The current problem is that the agentic work feels like a black box.

The UI should make agent activity visible at the operational level without exposing raw developer traces.

Workbench should show an activity/audit timeline with:

- Agent actions
- Human decisions
- Status transitions
- Drafts created
- Approvals
- Rejections
- Request-changes outcomes
- Escalations
- Errors
- Blocked transitions

System / Agents should contain deeper diagnostic traces.

## Implementation Principles For Codex

Preserve working functionality unless there is a clear reason to replace it.

Avoid broad rewrites.

Prefer explicit state transitions over hidden side effects.

Use existing backend routes, models, and services where possible.

Keep demo/synthetic data support.

Make changes phaseable and testable.

Move technical workflow-runner features out of the main operational path rather than deleting useful diagnostic functionality.

Prioritise clarity over visual perfection.

The UI should feel like a clinic operations dashboard, not an engineering demo.

## Recommended First Patch Scope

Unless repository inspection reveals a safer order, the first implementation patch should focus on:

1. Navigation simplification.
2. Overview as the command centre.
3. Workbench as the selected-referral processing page.
4. New Referral as the place to create/demo-run referrals.
5. Moving Review Inbox functions into Overview/Workbench.
6. Moving Intake & Scheduling functions into Workbench/Therapists.
7. Moving trace/debug functionality into New Referral/System / Agents.
8. Adding clear Google Calendar placeholder/status while keeping manual/mock availability for now.
9. Adding or improving agent activity/audit timeline visibility.

Do not attempt a full real email/calendar integration in this patch.

Do not implement the therapist session workflow in this patch.
