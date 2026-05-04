# Lumen Product North Star

## 1. Product Vision

Lumen is a governed AI workflow platform for small mental-health clinics. It is designed to become the controlled operational layer around therapy: the place where referrals are captured, structured, reviewed, matched, scheduled, prepared, documented, and audited.

Lumen is not a chatbot and it is not a replacement for therapists. It is a clinic operations system that uses agents, retrieval, deterministic workflow rules, and human approval gates to reduce administrative burden while preserving clinical responsibility.

The long-term product vision is:

> Lumen should become the operating system for a small therapy clinic's non-therapy work, helping staff move each patient safely from referral to first session, and later from session notes to governed documentation and reports.

The immediate product priority is the admin referral workflow: referral received -> structured -> reviewed -> risk-cleared -> matched -> contacted -> booked -> intake-complete -> therapist brief ready.

The desired final state for the first major workflow is `first_session_ready`.

---

## 2. Core Problem

Small mental-health clinics often have strong clinical capability and patient demand, but weak operational infrastructure. Referrals arrive through disconnected channels such as email, phone, WhatsApp, webforms, Doctoralia-style exports, insurer forms, EAP spreadsheets, and informal messages. Intake information is incomplete. Scheduling requires back-and-forth. Therapist matching relies on memory. Risk signals can be buried in unstructured text. Clinical documentation and reports are slow, inconsistent, and delayed.

The result is a clinic that is limited less by patient demand and more by operational throughput, documentation burden, and governance risk.

The core business problem Lumen addresses is:

> Clinics lose time, revenue, consistency, and control because patient administration and documentation are fragmented across inboxes, spreadsheets, calendars, PDFs, documents, and individual memory.

Lumen solves this by turning fragmented work into a controlled, stateful, auditable workflow.

---

## 3. Target Users

### Clinic admin staff

Admin staff own the operational journey from referral to first session. They need to see new referrals, missing information, risk blockers, review tasks, therapist match recommendations, slot proposals, communication drafts, patient replies, intake completion, and first-session readiness.

### Clinical reviewer / clinic director

Clinical reviewers or directors handle risk, suitability, escalation, match approval, and authorised exceptions. They need clear evidence, source spans, escalation reasons, and an audit trail.

### Therapists

Therapists need a clean handoff before the session and documentation support after the session. They should receive a prep brief, review intake and consent status, conduct therapy, then use Lumen to structure notes, compare against protocols, review risk, and sign off summaries/reports.

### Patient

The patient is an external participant, not the primary app user in the MVP. In the prototype, patient actions may be simulated or handled through demo email/calendar accounts. Over time, patients may receive approved communications, intake forms, reminders, and appointment confirmations.

---

## 4. Product Boundary

Lumen may:

- Ingest referrals, documents, notes, forms, and structured files.
- Extract, normalize, classify, retrieve, draft, compare, and summarize.
- Recommend therapist matches and next actions.
- Propose appointment slots based on explicit availability rules.
- Draft patient-facing communications.
- Draft clinical notes, summaries, reports, discharge documents, and evidence packs.
- Maintain human review queues, source evidence, workflow events, and audit logs.
- Learn from approved human edits and outcomes when governance allows.

Lumen must not:

- Diagnose patients.
- Autonomously contact patients.
- Autonomously book appointments.
- Submit claims.
- Save clinical notes, reports, or evidence packs as final without therapist sign-off.
- Change treatment plans without therapist approval.
- Allow elevated or unknown risk referrals to proceed to normal matching without clinical review.
- Complete the admin workflow unless required intake is complete or an authorised exception is recorded.

The central operating principle is:

> AI prepares; humans decide.

---

## 5. Product North Star Workflow

The product should be judged by whether it can make the patient/referral journey visible, controlled, and actionable.

The first major product journey is the admin referral workflow:

1. Referral arrives.
2. Referral is captured in a durable queue.
3. Raw source is preserved.
4. Structured fields are extracted.
5. Missing or ambiguous data is flagged.
6. Admin resolves missing information or approves a drafted request.
7. Risk and suitability review runs.
8. Elevated or unknown risk routes to clinical escalation.
9. Standard cases move to therapist matching.
10. Lumen recommends therapist and explains the rationale.
11. Admin/director approves the match.
12. Lumen proposes appointment slots.
13. Admin approves slot options.
14. Lumen drafts first-contact message.
15. Admin approves and sends.
16. Patient reply is received or simulated.
17. Admin confirms reply interpretation.
18. Appointment is created after approval.
19. Intake checklist is generated.
20. Intake request/reminders are drafted and approved.
21. Required intake is completed or formally waived.
22. Therapist prep brief is generated.
23. Workflow ends at `first_session_ready`.

This should become the backbone of the app interface. Users should always be able to answer:

- Where is this referral?
- What is blocking it?
- Who owns the next action?
- What has the system prepared?
- What needs human approval?
- What evidence supports the recommendation?
- What happened previously?

---

## 6. Core App Functions

### 6.1 Referral capture and normalization

Lumen should accept referrals from manual entry and synthetic demo data first, then progressively support CSV/XLSX import, email ingestion, webforms, voicemail transcripts, WhatsApp, and Doctoralia-style exports.

For each referral, Lumen should preserve the raw source and extract key fields:

- Patient name.
- Email.
- Phone.
- Date of birth.
- Insurer.
- Source channel.
- Referring entity.
- Presenting concern.
- Language preference.
- Modality preference.
- Availability text.
- Referral notes.
- Duplicate candidates.
- Extraction confidence.

### 6.2 Missing information handling

The system should identify missing or ambiguous information and create a clear admin task. It should draft targeted follow-up messages requesting only the missing information. No message should be sent without admin approval.

Possible actions:

- Admin fills fields manually.
- Admin approves a missing-information request.
- Admin edits the draft before sending.
- Admin marks a field unavailable with a reason.
- Admin closes the referral if it cannot be processed.

### 6.3 Risk, urgency, and suitability review

Every referral should pass through risk/suitability review before matching. The review should look for:

- Self-harm signals.
- Suicidality signals.
- Acute crisis.
- Safeguarding concerns.
- Service mismatch.
- Unknown or ambiguous risk.

Outputs should include:

- Risk category.
- Urgency.
- Confidence.
- Triggering evidence/source spans.
- Recommended handoff.

Decision rule:

- Standard risk -> continue to therapist matching.
- Elevated or unknown risk -> stop normal flow and create clinical escalation review.
- Urgent or unsuitable -> block matching until clinician/director action.

### 6.4 Clinical escalation branch

Clinical escalation is a branch within the referral workflow, not a separate product. It should pause the ordinary admin flow and route the case to a clinical reviewer or director.

Reviewer actions:

- Approve continuation to matching.
- Request more information.
- Require clinician-led contact.
- Reject as unsuitable.
- Close with signposting or referral-outcome note.
- Escalate to director.

All actions must be audit logged.

### 6.5 Therapist matching

Lumen should rank therapists using explicit stored data rather than free-form model judgment.

Matching factors:

- Specialty.
- Presenting concern fit.
- Language.
- Modality.
- Insurance compatibility.
- Capacity.
- Calendar availability.
- Age group suitability.
- Existing caseload.

Output should show:

- Recommended therapist.
- Ranked alternatives.
- Excluded therapists with reasons.
- Hard constraints checked.
- Plain-English rationale.
- Human approval requirement.

### 6.6 Scheduling and appointment support

Lumen should propose viable first-session slots using therapist availability, working hours, duration rules, buffers, modality, and existing conflicts.

It should not book autonomously.

Human approval gates:

- Admin approves slots to offer.
- Admin confirms the interpreted patient reply.
- Admin approves calendar creation and confirmation message.

Failure paths:

- No slots available -> scheduling blocked.
- Calendar API failure -> manual slot entry.
- Slot conflict -> regenerate options.

### 6.7 Patient communication drafting

Lumen should draft:

- Missing-information requests.
- First-contact emails/messages.
- Slot-offer messages.
- Appointment confirmations.
- Intake requests.
- Intake reminders.
- No-response follow-ups.
- Discharge/follow-up communications later.

Drafts must avoid:

- Diagnosis.
- Clinical promises.
- Unsupported reassurance.
- Autonomous crisis handling beyond approved clinic wording.

All patient-facing communication requires human approval before sending.

### 6.8 Patient reply handling

Patient replies may enter through demo email, manual input, or synthetic buttons.

Lumen should classify replies as:

- Accepted slot.
- Declined.
- Requested alternative.
- Asked question.
- Unclear.
- No response.

Admin must confirm interpretation before the system creates an appointment or changes the workflow state.

### 6.9 Intake and consent tracking

After appointment confirmation, Lumen should generate an intake checklist based on patient type, insurer, modality, risk review, clinic policy, and therapist requirements.

Possible items:

- Privacy notice acknowledged.
- Telehealth consent.
- Clinical intake form.
- Screening questionnaire.
- Insurance information.
- Emergency contact.
- Cancellation policy acknowledgement.
- Transcription consent if the therapist workflow uses transcription.

The workflow cannot reach `first_session_ready` unless required intake is complete or an authorised exception is recorded.

### 6.10 Therapist prep brief

When appointment and intake are ready, Lumen should generate a concise therapist prep brief.

The brief should include:

- Patient identity and appointment details.
- Referral source.
- Presenting concern summary.
- Risk review result.
- Intake completion summary.
- Screening questionnaire summary.
- Missing or waived items.
- Therapist match rationale.
- Cautious first-session focus.

The brief must not diagnose, invent clinical facts, or create a treatment plan.

### 6.11 Clinical documentation support

The therapist workflow should later support:

- Therapist dashboard.
- Session workspace.
- Consent-aware transcription controls.
- Therapist-authored notes.
- Structured note drafts.
- Therapist note approval.
- Protocol matching.
- Post-session risk review.
- Patient workspace.

No transcript or draft becomes a final clinical record without therapist approval.

### 6.12 Report and evidence drafting

Lumen should eventually support:

- Session summaries.
- Structured progress notes.
- Treatment reviews.
- Assessment reports.
- Insurer/EAP evidence packs.
- Discharge summaries.
- Next-session preparation notes.

Report drafts must be grounded in approved notes, protocol chunks, scores, templates, or source documents. Unsupported claims should be flagged or blocked. Final reports require therapist sign-off.

### 6.13 Governance and auditability

Governance is a core product feature, not a later add-on.

Lumen should maintain:

- Durable referral records.
- Patient records.
- Therapist profiles.
- Workflow runs.
- Workflow events.
- Human review tasks.
- Communication drafts.
- Appointment records.
- Intake records.
- Consent records.
- Document metadata.
- Audit logs.
- Source evidence.
- Human edits and approvals.

Every gate should answer:

- Who approved what?
- When?
- Based on which source output?
- What was edited?
- What was rejected?
- Why did the workflow continue or stop?

---

## 7. Agent Model

Lumen should use specialized agents rather than a generic assistant.

### Workflow Orchestrator & Governance Controller

Coordinates the workflow, routes tasks, enforces human gates, writes audit events, validates schemas, and prevents unsafe progression.

### Referral Intake Normalizer

Converts raw referral inputs into a clean structured referral record without adding clinical interpretation.

### Clinical Signal & Completeness Extractor

Extracts clinically and administratively relevant information, detects missing fields, and marks unknowns instead of inferring.

### Risk, Urgency & Suitability Reviewer

Screens for risk, urgency, safeguarding, and suitability issues. It should fail closed: uncertain or positive cases go to human clinical review.

### Therapist Matching & Capacity Planner

Ranks therapists using explicit rules and stored profile data, then provides a rationale.

### Patient Communication & Scheduling Drafter

Drafts patient-facing communication and scheduling messages, but never sends without approval.

### Consent & Pre-Session Intake Collector

Tracks required forms, consent, questionnaires, documents, reminders, and intake completion.

### Clinical Documentation & Protocol Matcher

Structures therapist notes, maps them to selected protocols, identifies missing protocol elements, and retrieves supporting context.

### Report, Treatment Review & Evidence Pack Writer

Drafts grounded summaries, reports, treatment reviews, discharge documents, and insurer/EAP evidence packs for therapist sign-off.

---

## 8. Human Review Gates

The app should treat human review as first-class workflow infrastructure, not as test checkboxes.

Required review task types:

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
- `therapist_note_approval`
- `post_session_risk_review`
- `report_signoff`

Core review actions:

- Approve.
- Reject.
- Request changes.
- Escalate.
- Edit draft.
- Add reason.

Non-negotiable gate rules:

- No patient-facing message is sent without human approval.
- No appointment is created without human approval after patient acceptance.
- No referral with unknown/elevated risk proceeds to matching without clinical review.
- No admin workflow completes unless required intake is complete or formally waived.
- No transcript or agent note draft becomes final without therapist approval.
- No clinical report/evidence pack is saved as final without therapist sign-off.
- All gate actions are audit logged.

---

## 9. Referral Status Model

Use one primary referral status wherever possible. Secondary flags can coexist with the primary status.

### Primary statuses

| Status | Meaning | Owner |
|---|---|---|
| `new_referral` | Referral captured but not processed | System/Admin |
| `normalising` | Extraction/normalisation running | System |
| `needs_admin_review` | Missing/ambiguous admin data | Admin |
| `waiting_for_missing_info` | Follow-up sent; waiting for response | Admin/System |
| `needs_clinical_review` | Risk/suitability review required | Clinician/Director |
| `clinical_escalation_review` | Escalated risk/suitability branch active | Clinician/Director |
| `ready_for_matching` | Required fields and risk gates sufficient to match | System/Admin |
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

### Secondary flags

- `missing_dob`
- `missing_contact`
- `insurance_unclear`
- `duplicate_candidate`
- `risk_unknown`
- `risk_elevated`
- `calendar_conflict`
- `patient_question_pending`
- `intake_exception_recorded`

---

## 10. Target App Structure

The app should be organized around operational jobs, not technical modules.

### Admin navigation

#### Overview

Operational dashboard showing new referrals, pending reviews, clinical escalations, appointments awaiting confirmation, intake blockers, first sessions ready, and integration health.

#### Referral Queue

Main intake list with filters by status, source, risk flag, missing fields, assigned therapist, and date. Each row/card should show a clear next action.

#### Referral Detail / Admin Workbench

The central page for processing one referral. It should show:

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

#### Review Inbox

Human gates and approvals across admin, clinical, communication, match, scheduling, and intake tasks.

#### Calendar / Scheduling

Therapist availability, proposed slots, confirmed appointments, calendar conflict handling, and demo calendar integration.

#### Intake Tracker

Required paperwork, completed/missing/waived status, reminder drafts, due dates, uploads, and manual completion.

#### Therapists

Therapist profiles and matching fields: specialties, languages, modalities, insurers, capacity, availability/calendar link, and active/inactive status.

### Therapist navigation

#### Therapist Dashboard

Upcoming sessions, today's sessions, first-session labels, prep brief status, intake warnings, consent/transcription status, drafts requiring review, and reports requiring sign-off.

#### Session Workspace

Appointment details, prep brief, intake/consent status, patient history, transcription controls, notes editor, generated structured draft, protocol analysis, risk review, report/summary draft, and sign-off actions.

#### Patient Workspace

Governed longitudinal record: referrals, appointments, intake documents, approved notes, approved summaries/reports, risk history, and source documents.

#### Report Review

Draft reports, unsupported-claim warnings, evidence/source panel, edit/reject/sign-off actions.

#### Protocol Library

Approved templates, protocols, source documents, versions, and retrieval status.

### System / demo navigation

#### Integrations

CSV import, demo email inbox/outbound account, demo calendar integration, and integration health.

#### System Health

Model health, database health, workflow health, audit events, and developer diagnostics.

#### Workflow Trace

Developer/demo-only view of agent execution. The current `Run workflow` concept belongs here, not as the main app experience.

---

## 11. Technical Direction

The technical architecture should separate deterministic operations from model-led tasks.

Deterministic software should handle:

- State transitions.
- Permissions.
- Role checks.
- Tenant isolation.
- Scheduling rules.
- Calendar conflict checks.
- Audit logging.
- Retention rules.
- Persistence.
- Workflow gate enforcement.

LLMs and ML models should handle:

- Language extraction.
- Referral normalization.
- Drafting.
- Summarization.
- Rationale generation.
- Protocol matching.
- Report drafting.
- Risk explanation, where supported by classifier outputs.

Recommended architecture:

- FastAPI backend.
- React/Vite frontend for production UI.
- LangGraph for controlled workflow orchestration.
- Pydantic schemas for typed handoffs.
- SQLAlchemy/Alembic for persistence.
- PostgreSQL as the target durable database.
- pgvector for retrieval in later phases.
- Object storage for uploaded documents.
- Local model serving via Ollama or LM Studio for prototype.
- EU-hosted production inference later.
- Audit logging as a core service.

The system should fail closed. If extraction, risk review, retrieval, citation validation, tenant checks, or schema validation fail, automation should pause and route to human review rather than continuing silently.

---

## 12. MVP Build Priority

The immediate product priority is not to build every future feature. It is to make one workflow coherent and demonstrable.

### Priority 1: Admin referral workflow coherence

Build a clear operational journey from referral capture to `first_session_ready`.

Must include:

- Clean referral status model.
- Referral queue.
- Referral detail/admin workbench.
- Missing information handling.
- Risk/suitability review state.
- Clinical escalation branch.
- Therapist matching.
- Human match approval.
- Slot proposal.
- Draft patient contact.
- Human send approval.
- Patient reply handling or simulation.
- Appointment confirmation.
- Intake checklist.
- Intake completion/waiver gate.
- Therapist prep brief.
- Activity/audit timeline.

### Priority 2: Human review workspace

Replace test checkboxes or hidden workflow gates with real review tasks, actions, reviewer identity, timestamps, draft editing, rejection reasons, and audit trail.

### Priority 3: Demo integrations

Add the smallest credible integration path:

- CSV/XLSX referral batch import.
- Demo email capture/send or simulated email with audit trail.
- Demo calendar read/write or manual fallback with conflict logic.

### Priority 4: Therapist workflow scaffold

After the admin workflow is coherent, scaffold therapist dashboard, session workspace, note approval, protocol analysis, report draft, and sign-off.

### Priority 5: RAG, security, deployment, and learning loops

Once the workflow is coherent, harden retrieval, governance, deployment, evaluation, and practice-memory loops.

---

## 13. What Good Looks Like

A good MVP does not need every integration or production security feature. It does need to clearly show the product concept.

A successful demo should show:

1. A synthetic referral arrives.
2. Lumen extracts details and flags missing data.
3. Lumen drafts a missing-information request.
4. Admin approves or edits the draft.
5. Missing data is received or simulated.
6. Risk review passes or escalates.
7. Clinical escalation is resolved if needed.
8. Lumen recommends a therapist.
9. Admin approves the match.
10. Lumen proposes appointment slots.
11. Admin approves and sends first-contact email.
12. Patient accepts a slot.
13. Admin confirms interpretation.
14. Lumen creates appointment or simulates creation with audit trail.
15. Intake checklist is generated.
16. Intake items are completed or waived.
17. Therapist prep brief is generated.
18. Referral reaches `first_session_ready`.
19. Therapist dashboard shows upcoming session and prep brief.

The product should feel like a clinic operations dashboard, not an engineering demo.

---

## 14. How To Use This Document With Codex

Codex should treat this file as the active product source of truth.

When asking Codex to make changes, provide this document first, then give a focused task. The older project files can be used for deeper background, but this document should define the current target.

Recommended Codex instruction:

> Use `LUMEN_PRODUCT_NORTH_STAR.md` as the product source of truth. Audit the current implementation against the admin referral workflow. Do not perform a broad rewrite. Identify what exists, what can be reused, what should move to developer/demo-only UI, and what smallest implementation patch will make the app better match the target workflow.

Codex should prioritize:

- Referral status coherence.
- Admin workflow UX.
- Review tasks and human gates.
- Clear next action per referral.
- Journey visibility.
- Auditability.
- Preservation of working functionality.

Codex should avoid:

- Large rewrites.
- Building the therapist workflow before the admin workflow is coherent.
- Treating the workflow runner as the main app.
- Adding complex integrations before the core state model is clear.
- Letting AI actions bypass human approval.

---

## 15. Guiding Product Sentence

> Lumen helps a small therapy clinic safely move each patient from fragmented referral to first-session readiness, then later from session notes to governed clinical documentation, by using AI to prepare work and humans to approve decisions.
