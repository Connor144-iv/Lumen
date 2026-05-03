# Codex Task: Lumen Admin Referral MVP Redesign

## Context

Lumen is a prototype multi-agent workflow platform for small mental-health clinics. The app currently has several useful foundations, but the user flow is confused and too close to a technical workflow console.

The immediate goal is to redesign and implement the **admin referral workflow** first. The therapist workflow should be kept in mind for future compatibility, but should not be fully implemented in this first task unless explicitly requested later.

Reference file:

- `LUMEN_WORKFLOW_AND_APP_REDESIGN_MASTER.md`

Use that file as the product source of truth.

## Non-Negotiable Product Rules

Lumen may ingest, extract, classify, retrieve, draft, recommend, and summarise.

Lumen must not:

- Diagnose patients.
- Send patient-facing messages without human approval.
- Book appointments without human approval.
- Let elevated/unknown risk continue to matching without clinical review.
- Mark the admin referral workflow complete unless required intake is complete or an authorised exception is recorded.
- Save clinical notes/reports as final without therapist sign-off.

## Immediate Objective

Inspect the current codebase and produce a concrete implementation plan for converting the existing app into the target admin referral workflow.

Do **not** start with a large rewrite.

First, audit what exists and map it to the target workflow.

## Target Admin Workflow Summary

The admin referral workflow should move through this high-level sequence:

1. Referral captured.
2. Referral normalised and structured fields extracted.
3. Admin reviews missing/ambiguous data.
4. Agent drafts missing-information request when needed.
5. Admin approves/sends missing-info request.
6. Risk/suitability review runs.
7. Elevated/unknown risk goes to clinical escalation review.
8. Standard case moves to therapist matching.
9. System recommends therapist.
10. Admin/director approves match.
11. System reads demo calendar and proposes slots.
12. Admin approves slot options.
13. System drafts first-contact email.
14. Admin approves/sends email.
15. Patient reply is received/simulated.
16. Admin confirms reply interpretation.
17. System creates appointment in demo calendar after approval.
18. Intake checklist is generated.
19. Intake request/reminders are drafted and approved.
20. Required intake is completed or waived with authorisation.
21. Therapist prep brief is generated.
22. Workflow ends at `first_session_ready`.

## Required Referral Statuses

Use one primary status per referral where possible:

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

Secondary flags can include:

- `missing_dob`
- `missing_contact`
- `insurance_unclear`
- `duplicate_candidate`
- `risk_unknown`
- `risk_elevated`
- `calendar_conflict`
- `patient_question_pending`
- `intake_exception_recorded`

## Required Review Task Types

Implement or map toward these review task types:

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

## Target Admin Pages

The operational user experience should be organised around these pages:

### Overview

Dashboard showing referral counts, review tasks, intake blockers, first sessions ready, and integration health.

### Referral Queue

Main list of referrals with status filters, risk/missing-field indicators, source channel, and next action.

### Referral Detail / Admin Workbench

This is the main page for processing one referral. It should show:

- Raw referral source.
- Extracted fields.
- Missing information.
- Duplicate candidates.
- Risk/suitability result.
- Therapist match recommendation.
- Slot proposals.
- Communication drafts.
- Patient reply history.
- Intake checklist.
- Prep brief preview.
- Audit/activity timeline.

### Review Inbox

Human gates and approvals:

- Approve.
- Reject.
- Request changes.
- Escalate.
- Edit draft.
- Add reason.

### Calendar / Scheduling

Demo calendar integration, therapist availability, proposed slots, confirmed appointments, conflict handling.

### Intake Tracker

Required paperwork, completed/missing/waived status, reminders, due dates, uploads/manual completion.

### Therapists

Therapist profiles and matching fields: specialties, languages, modalities, insurers, capacity, availability/calendar link, active/inactive.

### System / Developer Area

The existing `Run workflow` / workflow trace page should become a developer/demo area, not the main operational user interface.

## First Codex Task

Perform an audit and return a concrete change plan.

The audit should answer:

1. What current frontend pages/components map to the target pages?
2. What backend routes/services/models already support the target workflow?
3. What statuses currently exist and what status changes are required?
4. What review-task concepts already exist and what is missing?
5. What workflow actions are currently fake/in-memory/demo-only?
6. What can be reused safely?
7. What should be moved to developer/demo-only UI?
8. What is the smallest safe implementation phase to begin with?

## Implementation Plan Format Required From Codex

Return the plan in this structure:

1. **Current State Summary**
2. **Reusable Existing Pieces**
3. **Major Gaps**
4. **Proposed Backend Changes**
5. **Proposed Frontend Changes**
6. **Data/State Model Changes**
7. **Risks and Constraints**
8. **Recommended Build Phases**
9. **First Implementation Patch Scope**

## First Implementation Patch Should Probably Focus On

Unless the audit finds a better route, the first patch should focus on:

- Clean referral status model.
- Referral Queue improvements.
- Referral Detail/Admin Workbench skeleton.
- Review task model/actions, even if initially simple.
- Moving `Run workflow` out of the main user journey.

Do not attempt full calendar/email integration in the first patch unless the current code already makes it low-risk.

## Guardrails For Codex

- Preserve working functionality unless there is a clear reason to replace it.
- Avoid broad rewrites.
- Keep changes phaseable and testable.
- Prefer explicit state transitions over hidden side effects.
- Keep demo/synthetic data support.
- Add comments only where they clarify non-obvious workflow/state logic.
- If uncertain, produce a plan rather than guessing implementation details.

