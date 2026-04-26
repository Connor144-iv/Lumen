# Lumen Future-State Agentic Solution

## Context

This document designs the future-state agentic workflow for **Clínica Tejo Psicologia**, a semi-fictional small private mental-health clinic in Lisbon. The solution is designed to address the Phase 1 current-state pain points: fragmented referral intake, excessive clinician administrative burden, slow and inconsistent documentation, informal triage and matching, and weak governance of sensitive health data.

The proposed system is **Lumen**, a multi-agent AI workflow platform for small mental-health practices. Lumen does not replace therapists, diagnose patients, or autonomously contact patients. It coordinates intake, triage, scheduling support, documentation, report drafting, billing evidence preparation, and governance controls while keeping licensed clinicians in control of all clinical and patient-facing decisions.

---

## 1. Main Agents

The agent design is intentionally constrained. Each agent exists because it directly resolves one or more Phase 1 pain points. The system avoids generic “AI assistant” roles and instead uses specialized agents aligned to specific operational bottlenecks.

| Agent | Agent Name & Persona | Primary Objective | Target Pain Point Resolved |
|---:|---|---|---|
| 1 | **Workflow Orchestrator & Governance Controller** | Coordinates the end-to-end workflow, routes tasks between agents, enforces confidence thresholds, maintains audit logs, and prevents outputs from reaching patients or patient files without human approval. | **Pain Point 5:** Sensitive health data is spread across weakly governed systems. Also supports all other pain points by creating one controlled workflow layer. |
| 2 | **Referral Intake Normalizer** | Ingests referrals from email, phone voicemail, WhatsApp, Doctoralia, insurer emails, and EAP Excel files; converts them into one structured referral record. | **Pain Point 1:** Fragmented referral intake creates lost patients and lost revenue. |
| 3 | **Clinical Signal & Completeness Extractor** | Extracts patient demographics, presenting concern, language preference, insurer details, availability, modality preference, and missing fields from the referral record. | **Pain Point 1:** Missing or incomplete referral information. **Pain Point 2:** Clinicians waste time checking routine administrative details. |
| 4 | **Risk, Urgency & Suitability Reviewer** | Screens referral text and later session notes for urgency signals, safeguarding concerns, self-harm/suicidality indicators, and cases that may require escalation or a higher level of care. | **Pain Point 4:** Triage, matching, and risk review rely on informal judgement and memory. |
| 5 | **Therapist Matching & Capacity Planner** | Produces a ranked therapist-match recommendation using specialty, language, modality, availability, insurance compatibility, age group, and capacity rules. | **Pain Point 4:** Matching relies on memory and informal knowledge. **Pain Point 2:** Clinicians and directors spend time manually coordinating assignments. |
| 6 | **Patient Communication & Scheduling Drafter** | Drafts first-contact messages, appointment confirmations, reminder messages, and rescheduling options in the clinic’s preferred tone. It proposes slots but does not autonomously book without approval. | **Pain Point 2:** Clinicians spend high-value time on low-value coordination. **Pain Point 1:** Slow responses cause revenue leakage. |
| 7 | **Consent & Pre-Session Intake Collector** | Collects structured intake forms, consent records, insurance details, screening questionnaires, and missing documents before the first appointment. | **Pain Point 2:** Therapists collect basic information during clinical time. **Pain Point 5:** Sensitive intake data is spread across email/PDFs/local folders. |
| 8 | **Clinical Documentation & Protocol Matcher** | Converts therapist-authored notes, or optional consented transcription support, into structured session documentation; maps notes against therapist-specific protocols; highlights missing required data. | **Pain Point 3:** Clinical documentation is slow, inconsistent, and delayed. **Pain Point 4:** Protocol adherence and longitudinal review are difficult to audit. |
| 9 | **Report, Treatment Review & Evidence Pack Writer** | Drafts session summaries, assessment reports, treatment-plan review notes, discharge summaries, and insurer/EAP evidence packs for therapist review and sign-off. | **Pain Point 3:** Reports are slow and inconsistent. **Pain Point 2:** Therapists spend time formatting routine documents. **Pain Point 5:** Billing evidence and discharge data are inconsistently stored. |

### Human-in-the-loop boundaries

Lumen is designed as a controlled agentic system, not an autonomous clinical actor.

The system may autonomously ingest, extract, classify, retrieve, draft, compare, summarize, and prepare recommendations. It may not autonomously diagnose, contact a patient, submit a claim, save a clinical report, change a treatment plan, or escalate externally. Those actions require a therapist, clinic director, or administrator.

---

## 2. New Agentic Workflow — To-Be State

| Step # | Process Name | Primary Actor | Input / Trigger | Output / Handoff |
|---:|---|---|---|---|
| 1 | Multi-channel referral capture | **Referral Intake Normalizer** | New referral arrives by email, phone voicemail, WhatsApp, Doctoralia, insurer email, or EAP Excel batch. | Referral is captured in the unified Lumen referral queue and handed to the Clinical Signal & Completeness Extractor. |
| 2 | Referral normalization and deduplication | **Referral Intake Normalizer** | Raw referral text, voicemail transcript, WhatsApp audio/text, form submission, or Excel row. | A structured referral record is created with source channel, timestamp, patient identity fields, and confidence score. Handoff to Clinical Signal & Completeness Extractor. |
| 3 | Completeness check | **Clinical Signal & Completeness Extractor** | Structured referral record. | Missing fields are identified. If critical data is missing, a clarification task is generated for the Patient Communication & Scheduling Drafter. If sufficient, the record moves to risk review. |
| 4 | Clinical signal extraction | **Clinical Signal & Completeness Extractor** | Complete or partially complete referral record. | Presenting concern, language, modality preference, insurer, availability, age band, and referral reason are extracted. Handoff to Risk, Urgency & Suitability Reviewer. |
| 5 | Risk and urgency screening | **Risk, Urgency & Suitability Reviewer** | Referral text plus extracted clinical signals. | Risk score, triggering text spans, and urgency category. Low-risk cases continue to matching. Elevated-risk cases are routed immediately to a therapist or clinic director for human review. |
| 6 | Suitability and therapist matching | **Therapist Matching & Capacity Planner** | Risk-reviewed referral, therapist profiles, calendar availability, specialty tags, language capacity, insurance compatibility, and clinic rules. | Ranked therapist recommendation with rationale. Handoff to clinic director/admin for approval where required. |
| 7 | Human approval of match and next action | **Clinic Director / Admin / Therapist** | Recommended therapist match, risk category, missing-field status, and suitability notes. | Approved assignment, rejected assignment, or escalation decision. Approved cases move to patient outreach drafting. |
| 8 | First-contact message drafting | **Patient Communication & Scheduling Drafter** | Approved therapist assignment, patient contact details, clinic tone guide, and referral context. | Draft first-contact email/WhatsApp/SMS and proposed scheduling options. Handoff to admin or therapist for approval before sending. |
| 9 | Patient outreach approval and send | **Admin / Therapist** | Draft outreach message. | Human-approved message is sent to the patient. Patient response triggers scheduling. |
| 10 | Appointment slot proposal | **Patient Communication & Scheduling Drafter + deterministic scheduling rules** | Patient response, therapist calendar, modality preference, location/online preference, and clinic booking rules. | Proposed appointment slot(s), confirmation message, and reminder plan. Handoff to admin/therapist for final confirmation. |
| 11 | Booking confirmation | **Admin / Therapist** | Proposed slot and drafted confirmation message. | Appointment is booked in the clinic calendar. Confirmation is sent to the patient. Handoff to Consent & Pre-Session Intake Collector. |
| 12 | Pre-session intake collection | **Consent & Pre-Session Intake Collector** | Confirmed appointment and required clinic intake pack. | Patient receives structured intake forms, consent forms, insurance fields, and any required screening questionnaires. Completed data is stored in the governed patient workspace. |
| 13 | Pre-session preparation brief | **Consent & Pre-Session Intake Collector + Clinical Signal & Completeness Extractor** | Completed intake forms, referral context, screening results, and missing-field status. | Concise therapist preparation brief: presenting concern, key context, risk flags, missing information, and recommended first-session focus. Handoff to therapist. |
| 14 | First session and clinical work | **Therapist** | Patient appointment and preparation brief. | Therapy session is completed. Therapist writes notes normally. If explicit patient consent exists, optional live transcription can support note creation, but raw audio is not retained. |
| 15 | Post-session note structuring | **Clinical Documentation & Protocol Matcher** | Therapist-authored session note, optional consented transcript-derived working text, patient context, and selected clinical protocol. | Structured session note draft, protocol coverage map, extracted scores, missing protocol fields, and confidence scores. Handoff to risk review and report drafting. |
| 16 | Post-session risk screening | **Risk, Urgency & Suitability Reviewer** | Therapist-approved session note or structured note draft. | If no acute concern is detected, workflow continues. If risk is detected, therapist receives immediate review task with triggering text spans. |
| 17 | Protocol matching and longitudinal comparison | **Clinical Documentation & Protocol Matcher** | Current note, prior approved notes, patient goals, screening-score history, and therapist-specific protocol library. | Coverage map showing what was covered, what remains open, progression over time, and potential treatment-plan review prompts. Handoff to Report, Treatment Review & Evidence Pack Writer. |
| 18 | Session summary or assessment report drafting | **Report, Treatment Review & Evidence Pack Writer** | Structured note, protocol coverage map, test scores, relevant protocol sections, therapist templates, and prior approved reports. | Draft session summary, formal assessment report, or treatment-plan review note with source traceability. Handoff to therapist for review. |
| 19 | Therapist review, edit, and sign-off | **Therapist** | Draft note/report/treatment review with highlighted source evidence and confidence warnings. | Therapist approves, edits, or rejects the draft. Approved documents are saved to the patient record. Edits are captured for practice-memory learning. |
| 20 | Insurance/EAP evidence pack preparation | **Report, Treatment Review & Evidence Pack Writer** | Approved attendance records, approved reports, session summaries, insurer/EAP requirements, and patient authorization details. | Draft evidence pack or claim-support bundle. Handoff to admin/therapist for submission. The agent does not autonomously log into insurer portals in v1. |
| 21 | Discharge and follow-up drafting | **Report, Treatment Review & Evidence Pack Writer** | Discharge decision, final treatment notes, outcome scores, referral source, and clinic discharge template. | Draft discharge summary, follow-up reminder, and GP/EAP/referrer update where appropriate. Handoff to therapist for approval. |
| 22 | Governance, audit, and learning loop | **Workflow Orchestrator & Governance Controller** | Every agent action, human edit, approval, rejection, confidence score, and final signed output. | Append-only audit log, model-output traceability, data-retention controls, and updated practice memory for future drafting and retrieval. |

### Workflow logic in plain terms

The future-state workflow changes the clinic from a manually coordinated operation into a controlled agentic workflow. Referrals no longer sit across inboxes, phones, WhatsApp threads, and spreadsheets. They enter a single queue. The agents perform the repetitive work: extract data, detect missing information, flag risk, recommend routing, draft communications, prepare intake, structure notes, map notes to protocols, and prepare reports.

Humans remain responsible for the judgement-heavy and ethically sensitive moments: approving patient contact, confirming clinical suitability, reviewing risk flags, conducting therapy, signing documentation, approving reports, submitting evidence packs, and closing the patient relationship.

---

## 3. Business Moat

Lumen’s moat is not that it “uses AI.” A horizontal AI assistant could draft emails or summarize notes. The defensibility comes from combining vertical clinical workflow orchestration, first-party practice data, therapist-specific protocol retrieval, regulated EU hosting, and human-in-the-loop clinical safety into one workflow system.

### 3.1 Proprietary data flywheels

The strongest moat is the accumulation of practice-specific operational and clinical workflow data. Over time, Lumen becomes more useful because it learns the exact way each clinic works.

**Therapist protocol flywheel:** Each therapist uploads their own clinical protocols, templates, intake structures, assessment frameworks, and report formats. The Protocol Matcher and Report Writer use this material to produce outputs that match the therapist’s actual practice rather than a generic therapy template. The more protocols and approved reports a therapist adds, the more precise the system becomes.

**Practice-memory flywheel:** Every approved email, edited report, signed session summary, treatment-plan review, discharge note, and rejected draft becomes feedback. The system learns the clinic’s preferred language, report style, evidence requirements, routing rules, and documentation standards. This creates switching costs because a generic tool cannot instantly reproduce the clinic’s historical style and workflow memory.

**Routing-outcome flywheel:** Each patient assignment creates feedback on therapist fit, no-show risk, response time, appointment conversion, reassignment frequency, and treatment-continuation patterns. Over time, Lumen can improve matching recommendations from observed outcomes rather than relying only on static therapist profiles.

**Assessment-documentation flywheel:** Assessment reports are high-effort and highly patterned. As Lumen observes therapist-approved ADHD, anxiety, trauma, and other structured reports, it learns which source evidence belongs in which section and what level of detail the clinic expects. This improves report quality while keeping the therapist as final reviewer.

### 3.2 Domain-specific orchestration moat

The workflow is defensible because it crosses several operational boundaries that generic SaaS tools usually handle separately.

Lumen links referral intake, triage, therapist matching, scheduling, intake collection, note structuring, protocol matching, report drafting, billing evidence, discharge, and audit logging. Replicating this is harder than building a chatbot because the value is in the coordination layer: which agent should act, what information it is allowed to use, when it must stop, when it must escalate, and what evidence must be retained.

The system also includes clinically necessary boundaries. It does not diagnose. It does not autonomously contact patients. It does not save reports without sign-off. It does not send audio to third-party APIs. It uses confidence thresholds and source traceability to decide when a therapist must review an output. These constraints make the product slower to build but harder to copy responsibly.

### 3.3 Compliance and trust moat

Therapy data is sensitive health data. Lumen’s architecture treats compliance as a product feature, not as an afterthought.

The defensible position is based on EU-hosted processing, tenant-isolated patient records, audit logs, data minimisation, human approval gates, and a design that avoids US-owned inference endpoints for patient data. This matters because small clinics want the benefits of AI but cannot safely use generic public AI tools for raw health data, session notes, or patient communications.

A generic AI product may be technically capable of summarizing notes, but it is not automatically acceptable for GDPR-sensitive therapy workflows. Lumen’s moat is that its architecture, data flow, model-hosting approach, and HITL controls are designed around the regulated clinical context from the start.

### 3.4 Cost, speed, and capacity advantages

The economic value comes from reducing the time spent on administrative and documentation work without replacing clinical judgement.

**Speed advantage:** Referrals can move from scattered messages to a structured review queue in minutes. First-contact drafts and scheduling options can be prepared immediately after triage. Session notes and report drafts can be created shortly after the therapist writes the clinical note.

**Capacity advantage:** Clinicians spend less time on copying, formatting, searching, summarizing, and routine message writing. This creates more capacity for patient-facing work, supervision, treatment planning, and revenue-generating sessions.

**Accuracy and consistency advantage:** Documentation becomes more standardized because every report is checked against the therapist’s protocol, required data fields, and source evidence. Missing information is flagged earlier rather than discovered days later during report writing.

**Governance advantage:** Instead of patient data being duplicated across inboxes, spreadsheets, PDFs, local folders, and shared drives, the future-state workflow creates a single governed patient workspace with traceable agent actions and human approvals.

### 3.5 Why this is not an easily replicated wrapper

A simple wrapper around a large language model could draft an email or summarize a session note. It would not, by itself, solve the clinic’s actual operational problem.

The defensible product is the full agentic operating system for the clinic: channel ingestion, clinical extraction, risk screening, matching logic, calendar-aware scheduling, consent-aware intake, therapist-specific RAG, protocol matching, evidence-grounded report drafting, billing-support packaging, discharge support, audit logging, and EU-hosted privacy controls.

This creates a compound moat. Each individual function is useful, but the defensibility comes from the full workflow, the first-party data layer, the clinical guardrails, and the accumulation of therapist-specific practice memory over time.

---

## Phase 3 Design Foundation

The next phase should convert this future-state design into model and architecture choices. The highest-priority technical decisions are:

1. Which agents should be deterministic software rules rather than LLMs, especially scheduling, access control, retention, and audit logging.
2. Which agents require specialized fine-tuned models, especially risk flagging, clinical extraction, and report writing.
3. Which knowledge sources belong in RAG: therapist protocols, approved reports, patient history, public Portuguese clinical guidance, scoring rubrics, and clinic templates.
4. Which outputs require mandatory human approval: patient contact, clinical notes, reports, escalation decisions, claims evidence, and discharge communication.
5. Which data can be stored, which must be ephemeral, and which should never enter a training pipeline.
