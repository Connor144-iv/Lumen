# Lumen Workflow and App Redesign Master

## Purpose

This file is the compact source of truth for redesigning Lumen from a technical workflow console into a coherent clinic operations product.

Lumen serves two main user groups:

1. **Clinic admin staff**, who manage referrals, triage, therapist matching, patient communication, scheduling, and intake completion.
2. **Therapists**, who need pre-session briefs, optional transcription, structured note drafts, post-session analysis, and report/sign-off support.

The immediate build priority is the **admin referral workflow**, but the product must be designed so the therapist workflow can connect cleanly later.

## Product Boundary

Lumen may:

- Ingest referrals and documents.
- Extract, normalise, classify, retrieve, draft, compare, and summarise.
- Recommend therapist matches and next actions.
- Draft patient communications.
- Draft clinical notes, summaries, and reports.
- Maintain audit logs and human review queues.

Lumen must not:

- Diagnose patients.
- Autonomously contact patients without approval.
- Autonomously book appointments without approval.
- Submit claims.
- Save clinical notes/reports as final without therapist sign-off.
- Change treatment plans without therapist approval.

---

# 1. Target Workflow A: Admin Referral Workflow

## Workflow Goal

The admin referral workflow starts when a new referral enters the clinic and ends when:

- The patient has confirmed attendance for their first appointment.
- The first appointment is confirmed in the calendar.
- All minimum required paperwork, consent, and intake materials are complete, or an authorised exception has been recorded.
- The assigned therapist has received a usable pre-session brief.

Final state: `first_session_ready`.

## Primary Actors

- **Clinic Admin:** owns the operational workflow.
- **Clinical Reviewer / Director:** handles risk, suitability, and escalation.
- **Therapist:** receives the confirmed case and prep brief.
- **Patient:** external user; in the prototype, patient actions can be simulated or handled through demo email/calendar accounts.

## High-Level Flow

1. Referral arrives.
2. System normalises and extracts structured data.
3. Admin reviews missing/ambiguous information.
4. System assists in retrieving missing information by drafting patient/referrer follow-up messages.
5. Risk and suitability review runs.
6. Elevated/unknown risk branches to clinical escalation review.
7. Standard cases move to therapist matching.
8. System recommends therapist and appointment slots using profile/capacity/calendar data.
9. Admin approves match and slot options.
10. System drafts first-contact message.
11. Admin approves and sends message.
12. Patient accepts, declines, or requests changes.
13. If accepted, system creates calendar appointment after admin confirmation.
14. Intake packet/checklist is generated and sent after admin approval.
15. Missing intake items trigger reminders drafted by the system and approved by admin.
16. Once required intake is complete, system generates therapist prep brief.
17. Case reaches `first_session_ready`.

## Detailed Admin Workflow

### Step 1: Referral Capture

Inputs may include:

- Webform.
- Email.
- CSV/XLSX import.
- Voicemail transcript.
- Manual entry.
- Doctoralia-style export.

For the prototype, use synthetic data, but include at least one credible integration-style path such as CSV import or demo email ingestion.

System actions:

- Create a durable referral record immediately.
- Preserve raw source text/file.
- Extract patient name, email, phone, date of birth if present, insurer, source channel, presenting concern, language, modality preference, availability text, and referral notes.
- Run duplicate detection using deterministic fields first.

Failure/exception path:

- If contact/identity data is weak, mark `needs_admin_review`.
- If duplicate risk is high, mark `duplicate_review_required`.

### Step 2: Admin Triage and Missing Information Handling

Admin opens the referral detail workbench.

The page must show:

- Raw referral source.
- Extracted structured fields.
- Confidence or validation indicators.
- Missing required fields.
- Duplicate candidates.
- Recommended next action.

System actions:

- Identify missing critical fields.
- Draft a message requesting only the missing information.
- Recommend recipient: patient, referrer, or internal admin.
- Store the draft as a review task.

Human gate:

- Admin must approve/edit before any message is sent.

Possible outcomes:

- Admin fills fields manually.
- Admin approves follow-up email.
- Admin marks field as unavailable with reason.
- Admin closes referral if impossible to process.

### Step 3: Risk and Suitability Review

Every referral passes through risk/suitability review before matching.

System checks for:

- Self-harm signals.
- Suicidality signals.
- Acute crisis.
- Safeguarding concerns.
- Inappropriate service fit.
- Ambiguous/unknown risk.

Outputs:

- Risk category.
- Urgency.
- Confidence.
- Source spans / evidence.
- Recommended handoff.

Decision logic:

- Standard risk: continue to matching.
- Unknown or elevated risk: pause normal workflow and open `clinical_escalation_review`.
- Urgent/unsuitable: block normal matching until clinician/director action.

### Step 4: Clinical Escalation Review Branch

This is not a separate product journey. It is a controlled branch/sub-workflow attached to the referral.

Status: `clinical_escalation_review`.

Reviewer actions:

- Approve continuation to matching.
- Request more information.
- Require clinician-led contact.
- Reject as unsuitable.
- Close with signposting/referral-outcome note.
- Escalate to director.

Rules:

- Normal matching/scheduling cannot proceed until the escalation branch is resolved.
- All reviewer actions are audit logged.
- If the patient is still accepted, the referral returns to the ordinary admin workflow at `ready_for_matching` or `needs_admin_review`, depending on what information is missing.

### Step 5: Therapist Matching

System ranks therapists using explicit stored data:

- Specialty.
- Presenting concern fit.
- Language.
- Modality.
- Insurance compatibility.
- Capacity.
- Calendar availability.
- Age group suitability.
- Existing caseload.

Output must show:

- Recommended therapist.
- Ranked alternatives.
- Excluded therapists with reasons.
- Constraints checked.
- Plain-English rationale.

Human gate:

- Admin/director approves the match.

### Step 6: Calendar Slot Proposal

For the class demo, use actual calendar integration with fictitious/demo accounts.

System actions:

- Read selected therapist calendar availability.
- Check appointment duration, modality, buffers, existing conflicts, and working hours.
- Propose 2-4 viable first-session slots.

Human gate:

- Admin approves which slots to offer.

Failure paths:

- No slots available: `scheduling_blocked`.
- Calendar API fails: allow manual slot entry.
- Slot becomes unavailable: regenerate options.

### Step 7: First Contact Draft and Send

System drafts first-contact message with:

- Acknowledgement.
- Clinic identity.
- Proposed therapist or general availability, depending on clinic preference.
- Proposed appointment slots.
- Required next steps.
- Intake/paperwork expectation.
- Safe wording only.

Forbidden content:

- Diagnosis.
- Clinical promise.
- Unsupported reassurance.
- Autonomous emergency handling beyond approved clinic wording.

Human gate:

- Admin reviews/edits/approves before sending.

Prototype target:

- Best demo: send to a fake patient Gmail/demo account.
- Acceptable fallback: simulate send while showing full email audit trail.

### Step 8: Patient Reply Handling

Patient reply may enter via:

- Demo email inbox.
- Manual admin input.
- Synthetic demo button.

System classifies reply as:

- Accepted slot.
- Declined.
- Requested alternative.
- Asked question.
- Unclear.
- No response.

Human gate:

- Admin confirms interpretation before appointment creation.

Outcomes:

- Accepted: proceed to appointment confirmation.
- Alternative requested: regenerate slots.
- Declined: close as `closed_declined`.
- No response: draft reminder.
- Unclear: admin review.

### Step 9: Appointment Confirmation

System actions:

- Re-check selected calendar slot.
- Create appointment record.
- Create real calendar event in demo therapist calendar.
- Draft confirmation email.
- Attach appointment to referral/patient record.

Human gate:

- Admin approves final calendar creation and confirmation message.

Status after completion: `appointment_confirmed`.

### Step 10: Intake Packet and Paperwork

After appointment confirmation, Lumen creates required intake checklist.

Possible required items:

- Privacy notice acknowledged.
- Telehealth consent if relevant.
- Clinical intake form.
- Pre-session screening questionnaire.
- Insurance information.
- Emergency contact if required.
- Cancellation policy acknowledgement.
- Transcription consent if therapist workflow uses transcription.

System actions:

- Determine required items from patient type, insurer, modality, clinic policy, risk review, and therapist requirements.
- Draft intake request.
- Track item completion.
- Draft reminders for missing items.

Human gate:

- Admin approves patient-facing intake request/reminders.

Completion rule:

- Minimum required intake items must be complete before the admin workflow can reach `first_session_ready`.
- Exceptions require authorised waiver/escalation reason.

### Step 11: Therapist Prep Brief

When appointment and intake are complete, Lumen generates prep brief.

Brief should include:

- Patient identity and appointment details.
- Referral source.
- Presenting concern summary.
- Risk review result.
- Intake completion summary.
- Screening questionnaire summary.
- Missing or waived items.
- Therapist match rationale.
- Suggested first-session focus, phrased cautiously.

Rules:

- No diagnosis.
- No invented clinical facts.
- No treatment plan.

Final admin workflow state: `first_session_ready`.

---

# 2. Target Workflow B: Therapist Session Workflow

## Workflow Goal

The therapist workflow starts from a confirmed upcoming session and ends when:

- The therapist has reviewed the prep brief.
- The session is completed.
- Transcription and/or manual notes are converted into a structured draft.
- Therapist reviews and approves the structured note.
- Protocol/insight/risk analysis runs.
- Any generated summary/report is reviewed and signed off.
- Approved outputs are saved to the governed patient workspace.

Final state: `session_documentation_complete` or `final_saved_to_patient_record`.

## Primary Actors

- **Therapist:** owns the session and final approval.
- **Clinical Reviewer / Director:** handles escalated post-session risk.
- **Admin:** may receive operational follow-up tasks but does not own clinical content.

## High-Level Flow

1. Therapist dashboard shows all upcoming sessions, with today/urgent items prioritised.
2. Therapist opens upcoming session and reviews prep brief, intake, consent, and history.
3. Consent check determines whether transcription is allowed.
4. Session starts.
5. Optional transcription runs if consent enabled.
6. Session ends.
7. Agent creates structured draft note from transcript and/or therapist notes.
8. Therapist reviews, edits, and approves structured note.
9. System runs protocol analysis and post-session risk review.
10. System drafts summary/report/evidence pack if requested.
11. Therapist reviews, edits, and signs off.
12. Final approved output is saved to governed patient workspace.

## Detailed Therapist Workflow

### Step 1: Therapist Dashboard

Dashboard shows:

- All upcoming sessions.
- Today’s sessions prioritised.
- First-session indicators.
- Prep brief status.
- Intake missing warnings.
- Consent status.
- Drafts requiring review/sign-off.
- Follow-up tasks.

### Step 2: Pre-Session Review

Therapist opens session workspace.

View includes:

- Appointment details.
- Patient overview.
- Referral summary.
- Presenting concern.
- Risk review result.
- Intake questionnaire results.
- Consent status.
- Previous notes/history, where applicable.
- Admin notes.
- Prep brief.

Rule:

- Minimum required intake should already be complete because this is required to finish the admin workflow.
- If an exception exists, it must be visible with authorising user and reason.

### Step 3: Consent Check and Session Start

Before transcription:

- Check transcription consent.
- Check modality-specific consent.
- Check whether any required intake exception affects session readiness.

Possible transcription states:

- `transcription_allowed`.
- `transcription_disabled_missing_consent`.
- `transcription_declined`.
- `transcription_disabled_exception`.

Therapist starts session.

### Step 4: Optional Transcription

If allowed, transcription can be enabled.

Rules:

- Therapist controls start/pause/stop.
- Transcript is not automatically saved as final clinical record.
- Transcript is used to assist draft generation.
- Transcript handling should be auditable.

### Step 5: Session Completion and Agent-Led Note Draft

After session ends:

Inputs may include:

- Transcript.
- Therapist rough notes.
- Session metadata.
- Selected note template.

System actions:

- Clean transcript if available.
- Extract session-relevant structure.
- Generate structured draft note.
- Highlight uncertain sections.
- Mark unsupported or ambiguous statements.

Human gate:

- Therapist reviews, edits, and approves.

Important principle:

- The workflow should be agent-led after session completion, but final clinical authority remains with the therapist.
- The key gate is `therapist_approved_note`.

### Step 6: Protocol and Insight Analysis

After therapist approves the structured note:

System actions:

- Match against selected protocol/template.
- Retrieve relevant protocol, prior notes, scores, and template rules.
- Produce protocol coverage map.
- Identify covered, partially covered, and missing elements.
- Extract scores/measures if present.
- Identify possible follow-up tasks.

Rules:

- No invented clinical facts.
- Unsupported inferences are flagged, not silently used.

### Step 7: Post-Session Risk Review

System runs risk review on therapist-approved note.

Outcomes:

- No risk signal: continue.
- Unknown/elevated risk: create clinical review task.
- Urgent risk: block ordinary finalisation until reviewed.

Human gate:

- Clinical reviewer/director resolves escalated risk branch.

### Step 8: Summary / Report / Evidence Pack Drafting

Therapist selects output type:

- Brief session summary.
- Structured progress note.
- Treatment review.
- Assessment report.
- Insurer/EAP evidence pack.
- Discharge summary.
- Next-session preparation note.

System actions:

- Generate source-grounded draft.
- Attach source references/evidence map.
- Flag unsupported claims.
- Apply selected template.

Human gate:

- Therapist reviews, edits, approves, or rejects.

### Step 9: Sign-Off and Save

After therapist sign-off:

System actions:

- Save final document to patient workspace.
- Store audit trail.
- Capture human edits.
- Create follow-up tasks if needed.
- Update next-session context.

Final state:

- `session_documentation_complete` for ordinary notes.
- `final_saved_to_patient_record` for signed formal outputs.

---

# 3. State Model

## Referral Primary Statuses

Use one primary status per referral.

| Status | Meaning | Owner |
|---|---|---|
| `new_referral` | Referral captured but not processed | System/Admin |
| `normalising` | Extraction/normalisation running | System |
| `needs_admin_review` | Missing/ambiguous admin data | Admin |
| `waiting_for_missing_info` | Follow-up sent; waiting for response | Admin/System |
| `needs_clinical_review` | Risk/suitability review required | Clinician/Director |
| `clinical_escalation_review` | Escalated risk/suitability branch active | Clinician/Director |
| `ready_for_matching` | Required fields/risk gates sufficient to match | System/Admin |
| `match_recommended` | Therapist recommendation generated | System |
| `match_approved` | Human approved therapist match | Admin/Director |
| `slot_options_ready` | Calendar slot options generated | System |
| `awaiting_patient_contact` | Message draft ready or pending approval | Admin |
| `contact_sent` | Patient contacted | System/Admin |
| `awaiting_patient_reply` | Waiting for patient response | Patient/Admin |
| `appointment_confirmed` | Patient accepted and calendar event created | Admin/System |
| `intake_packet_sent` | Intake request sent | Admin/System |
| `intake_incomplete` | Required paperwork outstanding | Admin/Patient |
| `intake_complete` | Required paperwork complete or waived | Admin/System |
| `prep_brief_ready` | Therapist prep brief generated | System/Therapist |
| `first_session_ready` | End state for admin referral workflow | Admin/Therapist |
| `closed_declined` | Patient declined | Admin |
| `closed_no_response` | Closed after no response | Admin |
| `closed_not_suitable` | Clinic declined/referral unsuitable | Clinician/Director |

## Referral Secondary Flags

Secondary flags can coexist with statuses:

- `missing_dob`
- `missing_contact`
- `insurance_unclear`
- `duplicate_candidate`
- `risk_unknown`
- `risk_elevated`
- `calendar_conflict`
- `patient_question_pending`
- `intake_exception_recorded`

## Therapist Session Statuses

| Status | Meaning | Owner |
|---|---|---|
| `session_scheduled` | Appointment exists | Admin/System |
| `prep_brief_available` | Prep brief ready | System |
| `ready_for_session` | Intake/consent sufficient | Therapist |
| `in_session` | Session active | Therapist |
| `transcription_enabled` | Transcription active | Therapist/System |
| `session_completed` | Session ended | Therapist |
| `note_draft_generated` | Agent-generated note draft ready | System |
| `therapist_note_review_required` | Therapist must review/edit note | Therapist |
| `therapist_approved_note` | Note approved as source of truth | Therapist |
| `protocol_analysis_complete` | Protocol/insight analysis complete | System |
| `post_session_risk_review` | Risk review running or complete | System/Clinician |
| `clinical_review_required` | Escalated post-session risk | Clinician/Director |
| `report_draft_ready` | Summary/report/evidence pack ready | System |
| `therapist_signoff_required` | Final output requires sign-off | Therapist |
| `session_documentation_complete` | Ordinary session documentation complete | Therapist/System |
| `final_saved_to_patient_record` | Final signed output saved | System |

## Human Review Task Types

| Task Type | Trigger | Required Actor |
|---|---|---|
| `admin_missing_info_review` | Missing/ambiguous admin data | Admin |
| `missing_info_message_approval` | Draft request for missing info | Admin |
| `duplicate_resolution` | Duplicate candidate detected | Admin |
| `clinical_risk_review` | Risk elevated/unknown | Clinician/Director |
| `suitability_review` | Possible service mismatch | Clinician/Director |
| `match_approval` | Therapist recommendation generated | Admin/Director |
| `slot_offer_approval` | Calendar slots generated | Admin |
| `send_approval` | Patient-facing message drafted | Admin |
| `appointment_confirmation_approval` | Patient accepted slot | Admin |
| `intake_reminder_approval` | Missing paperwork reminder drafted | Admin |
| `intake_exception_approval` | Required item waived | Admin/Director |
| `therapist_note_approval` | Agent-generated note draft ready | Therapist |
| `post_session_risk_review` | Risk from approved note elevated/unknown | Clinician/Director |
| `report_signoff` | Report/summary/evidence pack ready | Therapist |

## Non-Negotiable Gate Rules

- No patient-facing communication is sent without human approval.
- No appointment is created without human approval after patient acceptance.
- No referral with unknown/elevated risk proceeds to matching without clinical review.
- No admin workflow completes unless required intake is complete or an authorised exception is recorded.
- No transcript/agent draft becomes a final note without therapist approval.
- No clinical report/evidence pack is saved as final without therapist sign-off.
- All gate actions must be audit logged.

---

# 4. App / Page Redesign

The app should be organised around operational jobs, not technical modules.

## Admin Navigation

### 1. Overview

Purpose:

- Operational dashboard.
- Show referral counts, pending reviews, intake blockers, upcoming first sessions, and integration health.

Should include:

- New referrals.
- Items requiring admin action.
- Clinical escalations pending.
- Appointments awaiting confirmation.
- Intake incomplete.
- First sessions ready.

### 2. Referral Queue

Purpose:

- Main intake list.

Features:

- Filters by status, source, risk flag, missing fields, assigned therapist, date.
- Cards or table rows with patient/referral summary.
- Clear next action per referral.

### 3. Referral Detail / Admin Workbench

Purpose:

- Central page for processing one referral.

Sections:

- Raw referral.
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

Key actions:

- Edit extracted fields.
- Request missing info.
- Run/refresh risk review.
- Approve clinical escalation outcome.
- Generate therapist match.
- Approve match.
- Generate slots.
- Approve/send contact email.
- Record/sync patient reply.
- Confirm appointment.
- Send intake packet.
- Mark/upload intake item.
- Generate prep brief.

### 4. Review Inbox

Purpose:

- Human gates and approvals.

Sections:

- Admin tasks.
- Clinical/director tasks.
- Message approvals.
- Match approvals.
- Intake exceptions.

Actions:

- Approve.
- Reject.
- Request changes.
- Escalate.
- Edit draft.
- Add reason.

### 5. Calendar / Scheduling

Purpose:

- View therapist availability and appointment proposals.

Features:

- Demo calendar integration.
- Therapist availability.
- Proposed slots.
- Confirmed first appointments.
- Calendar conflict handling.

### 6. Intake Tracker

Purpose:

- Track paperwork across patients/referrals.

Features:

- Required items.
- Completed/missing/waived status.
- Reminder drafts.
- Due dates.
- Upload or mark complete.

### 7. Therapists

Purpose:

- Therapist profile and matching data.

Features:

- Name/email.
- Specialties.
- Languages.
- Modalities.
- Insurers.
- Capacity/week.
- Availability/calendar link.
- Active/inactive.

## Therapist Navigation

### 1. Therapist Dashboard

Purpose:

- Session-centered work queue.

Features:

- All upcoming sessions.
- Today’s sessions prioritised.
- First-session labels.
- Prep brief available/missing.
- Consent/transcription status.
- Drafts requiring review.
- Reports requiring sign-off.

### 2. Session Workspace

Purpose:

- Main therapist page for one session.

Sections:

- Appointment details.
- Prep brief.
- Intake and consent status.
- Patient history.
- Transcription controls.
- Notes editor.
- Generated structured draft.
- Protocol analysis.
- Risk review.
- Report/summary draft.
- Sign-off actions.

### 3. Patient Workspace

Purpose:

- Governed longitudinal record.

Features:

- Referrals.
- Appointments.
- Intake documents.
- Approved notes.
- Approved summaries/reports.
- Risk/escalation history.
- Source documents.

### 4. Report Review

Purpose:

- Therapist review/sign-off queue.

Features:

- Draft reports.
- Unsupported claim warnings.
- Evidence/source panel.
- Edit/sign/reject.

### 5. Protocol Library

Purpose:

- Approved templates/protocols.

Features:

- Upload protocols/templates.
- Versioning.
- Active/inactive.
- Retrieval source status.

## System / Demo Navigation

### 1. Integrations

Purpose:

- Configure/show synthetic/demo integrations.

Features:

- CSV import.
- Demo email inbox/outbound account.
- Demo calendar integration.
- Integration health.

### 2. System Health

Purpose:

- Developer/admin system status.

Features:

- Model health.
- Database health.
- Workflow health.
- Audit events.

### 3. Workflow Trace

Purpose:

- Developer/demo-only trace of agent execution.

Important redesign point:

- The current `Run workflow` page should become a developer/demo page, not the primary operational interface.
- Real users should trigger workflows from Referral Detail, Review Inbox, Calendar, Intake, Session Workspace, or Patient Workspace.

---

# 5. Build Phases

## Build Priority

Start with the admin referral workflow. Design should preserve future therapist workflow compatibility, but do not attempt to fully rebuild both workflows in the first implementation pass.

## Phase 1: Audit Current App Against Target Workflow

Codex should first inspect the current repository and produce a change plan before implementation.

Audit questions:

- Which current pages map cleanly to target pages?
- Which existing backend routes/services can be reused?
- Which statuses already exist?
- Which statuses/actions are missing?
- Which current features are prototype-only and should move to developer/demo space?
- What is the safest first implementation phase?

## Phase 2: Admin Referral State Model

Implement/refine:

- Referral statuses.
- Review task types.
- Transition logic.
- Audit events.
- End-state checks.

Do not build complex UI until state transitions are coherent.

## Phase 3: Referral Queue and Detail Workbench

Implement/refine:

- Referral queue filters.
- Referral detail as central workbench.
- Raw source + extracted fields.
- Missing info detection and message drafting.
- Risk/suitability section.
- Match recommendation section.
- Communication draft section.
- Intake checklist section.
- Activity/audit timeline.

## Phase 4: Human Review Inbox

Implement/refine:

- Stored review tasks.
- Approve/reject/request changes/escalate.
- Draft editing.
- Reviewer identity/timestamp.
- Resume workflow after approval.

## Phase 5: Calendar and Email Demo Integration

Implement/refine:

- Demo calendar availability read.
- Slot proposal.
- Calendar event creation after approval.
- Outbound email via fake/demo patient account where possible.
- Fallback simulation with audit trail.

## Phase 6: Intake Completion and Prep Brief

Implement/refine:

- Intake checklist.
- Required/completed/waived states.
- Reminder draft and approval.
- Hard gate before `first_session_ready`.
- Prep brief generation.

## Phase 7: Therapist Workflow Scaffold

After admin workflow is coherent:

- Therapist dashboard.
- Session workspace.
- Optional transcription path.
- Agent-generated structured note draft.
- Therapist approval/sign-off.
- Protocol/report draft support.

---

# 6. Demo Story

The ideal class demo should show:

1. Synthetic referral arrives.
2. Lumen extracts details and flags missing data.
3. Lumen drafts missing-info request; admin approves.
4. Missing data is received/simulated.
5. Risk review passes or escalates and is resolved.
6. Lumen recommends therapist.
7. Admin approves therapist match.
8. Lumen reads demo calendar and proposes slots.
9. Admin approves and sends first-contact email to fake patient account.
10. Patient accepts a slot.
11. Lumen creates real demo calendar event.
12. Lumen sends confirmation.
13. Intake packet is generated and sent.
14. Intake items are completed/simulated.
15. Prep brief is generated.
16. Admin workflow ends at `first_session_ready`.
17. Therapist dashboard shows upcoming session and prep brief.
18. Therapist completes session with optional transcription.
19. Lumen generates structured note draft.
20. Therapist approves note.
21. Lumen generates summary/report draft.
22. Therapist signs off.
23. Final output is saved to patient workspace.

