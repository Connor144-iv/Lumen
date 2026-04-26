# Lumen Complete Product Plan

## Purpose

This document converts the Phase 1 current-state analysis, Phase 2 agentic vision, Phase 3 backend blueprint, and current FastAPI/LangGraph prototype into a phased plan for reaching a clinic-ready Lumen product.

The goal is not to build every future-state feature at once. The goal is to move from a local workflow demonstrator to a governed product that a small mental-health clinic could safely use for referral intake, triage, therapist matching, patient communication drafting, intake collection, session documentation, report drafting, billing evidence support, discharge support, and auditability.

## Product North Star

Lumen should become the controlled workflow layer for a small clinic's non-therapy operational work.

It may:

- Ingest, normalize, extract, classify, retrieve, draft, compare, and summarize.
- Recommend next actions and therapist matches.
- Prepare patient-facing drafts and clinical-document drafts.
- Maintain audit logs, source evidence, and human review queues.

It must not:

- Diagnose patients.
- Autonomously contact patients.
- Autonomously book appointments.
- Submit claims.
- Save clinical reports or notes to the final patient record without approval.
- Change treatment plans without therapist sign-off.

## Current Starting Point

The current app already has useful foundations:

- FastAPI web server.
- Frontend workflow console.
- LangGraph workflow orchestration.
- Two workflow types: `new_referral` and `session_completed`.
- Typed Pydantic handoff schemas.
- LLM-backed structured agents.
- Human approval gates.
- SSE workflow event streaming.
- JSON export of workflow results.
- Fail-closed behavior for weak evidence or agent errors.

The main product gaps are:

- No durable database-backed product state.
- No real patient, therapist, referral, appointment, consent, or document records.
- No production human review workspace.
- No real channel ingestion beyond manual form/upload.
- No real scheduling/calendar integration.
- No governed clinical document store.
- No RAG ingestion pipeline for protocols, templates, patient history, or insurer rules.
- No clinic-grade authentication, role-based access, tenant isolation, retention, or deployment model.

## Delivery Principles

- Keep each phase deployable or demonstrable on its own.
- Prefer deterministic rules for operations, permissions, scheduling, retention, and persistence.
- Use LLMs only where language understanding or drafting is clearly useful.
- Preserve strict human-in-the-loop controls for patient contact and clinical outputs.
- Treat governance and auditability as core product features.
- Build the simplest version that fully proves each workflow before expanding scope.

## Phase Overview

| Phase | Name | Outcome |
|---:|---|---|
| 0 | Prototype Stabilization | The current app runs reliably as a baseline demo. |
| 1 | Product Data Foundation | Durable database-backed records replace in-memory state. |
| 2 | Referral Intake MVP | A clinic can manage referrals from capture to human-reviewed outreach draft. |
| 3 | Human Review Workspace | Admins, therapists, and directors can review, edit, approve, or reject work. |
| 4 | Therapist Matching and Scheduling Support | Matching uses real therapist profiles and availability rules. |
| 5 | Consent and Pre-Session Intake | Lumen collects required intake items and produces therapist prep briefs. |
| 6 | Clinical Documentation Foundation | Session notes, protocols, templates, and patient history become governed records. |
| 7 | RAG-Backed Reports and Evidence Packs | Reports and summaries are drafted with source traceability. |
| 8 | Integrations and Channel Expansion | Email, files, calendars, and messaging channels feed the same workflow. |
| 9 | Governance, Security, and Compliance Hardening | The system meets a defensible clinic handover baseline. |
| 10 | Production Deployment and Pilot | Lumen is deployed, monitored, and piloted with a clinic workflow. |
| 11 | Learning Loops and Product Maturity | Feedback, evaluation, and practice memory improve quality over time. |

---

## Phase 0: Prototype Stabilization

### Objective

Make the existing prototype reliable enough to use as the baseline for future work.

### Deliverables

- Confirm the configured model names and providers work across startup and workflow execution.
- Add a backend model health check for the configured small, medium, and communication models.
- Add friendly model/server error messages in the UI.
- Add example payloads for both workflow types.
- Add a small smoke-test script for `new_referral` and `session_completed`.
- Document the working local run path in `README_WEB_APP.md`.

### Recommended Work Items

- Add `/api/health/models`.
- Add server startup validation that can be run manually without blocking development.
- Add sample referrals and session notes under a `samples/` directory.
- Add a `scripts/smoke_test.ps1` or Python equivalent.
- Add basic tests for request parsing and workflow request validation.

### Exit Criteria

- A developer can clone/run the app and execute both workflow types.
- Missing model, unavailable model server, and invalid request states are visible and actionable.
- The current prototype remains functionally unchanged except for reliability and observability improvements.

---

## Phase 1: Product Data Foundation

### Objective

Replace in-memory job and workflow state with durable application records.

### Deliverables

- PostgreSQL-backed data model.
- SQLAlchemy models and Alembic migrations.
- Durable workflow job store.
- Durable audit event table.
- Core records for tenant, user, patient, referral, therapist, workflow run, approval task, document metadata, and communication draft.
- Environment configuration for local PostgreSQL.

### Recommended Data Model

- `tenants`
- `users`
- `roles`
- `patients`
- `therapists`
- `referrals`
- `workflow_runs`
- `workflow_events`
- `human_review_tasks`
- `documents`
- `communication_drafts`
- `appointments`
- `consent_records`
- `audit_log`

### Recommended Work Items

- Introduce a `backend/lumen_web/db.py` database module.
- Add repository/service functions rather than putting SQL directly in route handlers.
- Move `WorkflowJobManager` from process memory to database-backed persistence.
- Store raw workflow outputs as JSONB while also extracting important fields into relational columns.
- Add migration and seed data for one demo clinic, several therapists, and example patients.

### Exit Criteria

- Workflow runs survive server restart.
- The UI can reload a previous workflow result.
- Audit events are stored durably.
- There is a clear tenant boundary in every persisted record.

---

## Phase 2: Referral Intake MVP

### Objective

Build the first clinic-useful workflow: referral capture through triage, matching recommendation, and human-reviewed outreach draft.

### Deliverables

- Referral queue UI.
- Referral detail view.
- Structured referral record creation.
- Missing-field detection.
- Risk review output.
- Therapist match recommendation.
- Human approval requirement before outreach.
- First-contact draft generation.
- Referral status lifecycle.

### Referral Status Lifecycle

- `new`
- `normalizing`
- `needs_admin_review`
- `needs_clinical_review`
- `ready_for_matching`
- `match_pending_approval`
- `outreach_draft_pending`
- `ready_to_contact`
- `contacted`
- `closed`

### Recommended Work Items

- Persist each submitted referral as a record before running the workflow.
- Add duplicate detection scaffolding using deterministic fields first.
- Add a referral list with filters by status, source channel, risk, and assigned reviewer.
- Add a referral detail screen showing source text, structured fields, missing fields, risk flags, match recommendation, and communication draft.
- Use deterministic validation for contact fields, source channel, insurer, language, modality, and missing required fields where possible.

### Exit Criteria

- An admin can process a referral from raw text to an approved outreach draft.
- Elevated or unknown risk always routes to clinician/director review.
- No patient-facing communication can be sent or marked final without human approval.
- Every agent output is attached to the referral record with an audit trail.

---

## Phase 3: Human Review Workspace

### Objective

Turn human gates from test checkboxes into a real operational workspace.

### Deliverables

- Review task inbox.
- Role-aware task types for admin, therapist, and clinic director.
- Approve, reject, edit, request changes, and escalate actions.
- Diff capture between AI draft and human-edited final text.
- Review history per referral, patient, and workflow run.
- UI states that clearly distinguish draft, approved, rejected, and final outputs.

### Review Task Types

- Admin review for missing or ambiguous intake details.
- Clinical review for risk or suitability.
- Match approval for therapist assignment.
- Send approval for patient communication.
- Therapist sign-off for notes, reports, evidence packs, and discharge drafts.

### Recommended Work Items

- Add `human_review_tasks` as first-class records.
- Replace pre-approved local testing checkboxes with task actions.
- Add a draft editor for communication and report text.
- Store rejection reasons and edited final outputs.
- Add reviewer identity and timestamp to all approvals.

### Exit Criteria

- A workflow can pause and resume based on a real stored approval task.
- Human edits are captured and traceable.
- Human rejection prevents downstream automation.
- The audit trail clearly answers who approved what, when, and from which source output.

---

## Phase 4: Therapist Matching and Scheduling Support

### Objective

Make therapist matching and appointment slot proposal useful with real clinic data.

### Deliverables

- Therapist profile management.
- Specialty, age group, language, modality, insurance, and capacity rules.
- Deterministic matching engine.
- LLM-generated rationale based only on matching facts.
- Calendar availability abstraction.
- Proposed slot generation.
- Human-confirmed booking workflow.

### Recommended Work Items

- Add therapist profile CRUD.
- Add clinic-level matching rules.
- Add deterministic match scoring before LLM rationale.
- Add calendar import or manual availability blocks for MVP.
- Add appointment proposal records.
- Add conflict checks before confirming a slot.

### Exit Criteria

- Lumen can rank therapists using explicit stored data.
- The recommendation shows included and excluded therapists with reasons.
- Proposed appointment slots come from real availability data or explicit manual availability.
- Booking still requires human confirmation.

---

## Phase 5: Consent and Pre-Session Intake

### Objective

Move intake and consent collection into a governed workflow before the first session.

### Deliverables

- Intake packet templates.
- Consent record store.
- Required document checklist.
- Screening questionnaire scaffolding.
- Missing-item reminders as drafts.
- Therapist preparation brief.
- Intake status per patient/referral.

### Recommended Work Items

- Add configurable intake requirements by patient type, insurer, age band, modality, and referral source.
- Add document upload handling with metadata and virus/file-type checks.
- Add consent scope and expiry fields.
- Add PHQ-9/GAD-7 style structured questionnaire support as a generic form system.
- Generate a therapist prep brief from referral, intake fields, risk review, and missing items.

### Exit Criteria

- Staff can see what intake items are required, completed, missing, or expired.
- Patient-facing reminders are drafts until approved.
- Therapists receive a concise prep brief before the first session.
- Sensitive intake data is stored in the governed patient workspace rather than loose files.

---

## Phase 6: Clinical Documentation Foundation

### Objective

Create the governed document and note substrate required before serious report drafting.

### Deliverables

- Patient workspace.
- Session note records.
- Protocol library records.
- Report template records.
- Score records.
- Document upload and parsing pipeline.
- Object storage for raw files.
- pgvector-backed retrieval store.

### Recommended Work Items

- Add MinIO or S3-compatible object storage for uploaded files.
- Add document parsers for TXT, PDF, DOCX, and CSV/XLSX where needed.
- Add chunking and embedding pipeline using BGE-M3 or the selected embedding model.
- Add metadata filters for tenant, patient, therapist, document type, protocol, consent scope, and source version.
- Add UI for uploading protocols, templates, approved reports, and insurer rules.
- Add session note creation and therapist approval states.

### Exit Criteria

- Protocols, templates, notes, scores, and insurer rules can be uploaded and retrieved with tenant-scoped filters.
- RAG retrieval returns source chunks with evidence references.
- Weak retrieval evidence blocks report drafting rather than producing unsupported output.
- Patient history retrieval is patient-scoped and access controlled.

---

## Phase 7: RAG-Backed Reports and Evidence Packs

### Objective

Build the post-session workflow that reduces documentation burden while preserving therapist control.

### Deliverables

- Protocol coverage map from therapist-authored notes.
- Post-session risk review.
- Session summary draft.
- Treatment review draft.
- Assessment report draft.
- Discharge summary draft.
- Insurance/EAP evidence pack draft.
- Citation and unsupported-claim validator.
- Therapist review/sign-off UI.

### Recommended Work Items

- Require selected protocol/template before report generation.
- Use retrieved source chunks for all clinical claims.
- Add claim-to-evidence mapping in the report editor.
- Add unsupported claim warnings.
- Add report section templates.
- Add export to Markdown/PDF/DOCX after sign-off.
- Capture therapist edits for future practice memory.

### Exit Criteria

- Report drafts are grounded in approved notes, protocol chunks, scores, templates, or insurer rules.
- Unsupported claims block sign-off or are clearly flagged.
- Final reports are only saved after therapist sign-off.
- The clinic can produce at least one useful session summary and one formal report draft from realistic sample data.

---

## Phase 8: Integrations and Channel Expansion

### Objective

Connect the product to the real systems that create clinic workload.

### Deliverables

- Email referral ingestion.
- CSV/XLSX EAP or insurer batch ingestion.
- Calendar integration.
- Outbound email sending after approval.
- WhatsApp/SMS integration plan or implementation.
- Doctoralia/import workflow if direct API access is unavailable.
- Import error queue.

### Recommended Work Items

- Start with email and CSV/XLSX because they are high-value and lower complexity.
- Use provider webhooks where available, and polling/import fallback where not.
- Store imported source artifacts as documents.
- Require human approval before any outbound message leaves the system.
- Add integration health screens.

### Exit Criteria

- Referrals can enter Lumen without manual copy/paste for at least one real channel.
- Batch referrals can be imported and processed into the same queue.
- Calendar availability can be read or managed through the app.
- Approved outbound communication is sent and logged.

---

## Phase 9: Governance, Security, and Compliance Hardening

### Objective

Reach a defensible baseline for handling sensitive health data in a small clinic context.

### Deliverables

- Authentication.
- Role-based access control.
- Tenant isolation.
- Patient-level access rules.
- Audit log immutability policy.
- Data retention settings.
- Consent-aware retrieval and processing.
- Encryption strategy.
- Backup and restore process.
- Data export and deletion workflows.
- Security and privacy documentation.

### Recommended Work Items

- Add roles: admin, therapist, clinic director, compliance/admin owner.
- Apply tenant and role checks at the service layer.
- Add audit records for view, create, update, approval, export, send, and delete actions.
- Add environment separation for local, staging, and production.
- Add PII/PHI handling guidelines to developer docs.
- Add rate limits and upload limits.
- Add model-provider policy controls for what data can leave the deployment.

### Exit Criteria

- A clinic can understand where patient data is stored, who can access it, and what actions are logged.
- Access to patient records is role controlled.
- Clinical outputs cannot bypass approval gates.
- Backup and restore have been tested.
- The system has a documented GDPR-oriented data handling posture.

---

## Phase 10: Production Deployment and Clinic Pilot

### Objective

Deploy Lumen in a production-like EU environment and run a controlled clinic pilot.

### Deliverables

- Production deployment architecture.
- Staging environment.
- CI/CD pipeline.
- Database migrations in deployment flow.
- Observability dashboards.
- Error tracking.
- Operational runbook.
- Pilot onboarding materials.
- Pilot success metrics.

### Recommended Work Items

- Containerize the app and worker processes.
- Deploy FastAPI, frontend, PostgreSQL, object storage, Redis/queue, and model endpoints.
- Add OpenTelemetry-style tracing and structured logs.
- Add uptime, queue depth, model latency, workflow failure, and approval throughput metrics.
- Create pilot seed data and training docs.
- Run a pilot with synthetic data first, then tightly scoped real workflow data if governance is ready.

### Exit Criteria

- The system can be deployed reproducibly.
- Operators can see whether workflows, queues, models, and integrations are healthy.
- A clinic can process a controlled set of referrals end to end.
- Pilot feedback is captured as product backlog items.

---

## Phase 11: Learning Loops and Product Maturity

### Objective

Use approved human edits and workflow outcomes to improve quality without weakening governance.

### Deliverables

- Practice memory store.
- Approved/rejected draft feedback loop.
- Evaluation datasets.
- Extraction accuracy reports.
- Retrieval precision checks.
- Report citation fidelity checks.
- Human edit-rate metrics.
- Risk classifier evaluation plan.
- Optional fine-tuning pipeline for communication style, risk, and reports.

### Recommended Work Items

- Store human edits separately from final clinical records.
- Add opt-in controls for using approved outputs in practice memory.
- Build evaluation sets from synthetic and pseudonymized examples.
- Measure referral extraction accuracy, missing-field detection, match appropriateness, draft approval rate, report unsupported-claim rate, and average time saved.
- Fine-tune only after governance, consent, pseudonymization, and evaluation are in place.

### Exit Criteria

- Quality can be measured over time.
- Product changes are evaluated against concrete workflow metrics.
- Practice memory improves drafting while remaining tenant-isolated.
- Any fine-tuned model has documented data provenance and evaluation results.

---

## Clinic Handover Readiness Checklist

Lumen is not ready for clinic handover until all of the following are true:

- The app has durable storage for referrals, patients, documents, workflows, approvals, and audit logs.
- Users authenticate and have role-based access.
- Every patient-facing message requires human approval before sending.
- Every clinical note/report/evidence pack requires therapist sign-off before final storage.
- Risk-positive or risk-unknown referrals route to clinician/director review.
- Patient and tenant data are isolated.
- Uploaded clinical material is stored securely and retrievable only under the right scope.
- RAG-backed outputs include source evidence and block unsupported claims.
- The clinic can recover data from backups.
- There is a documented runbook for common failures.
- There is a documented privacy, retention, and data-processing posture.
- The product has been tested on synthetic and pilot data before broader real-patient use.

## Suggested Build Order For The Next Work Sessions

1. Stabilize prototype and add model health checks.
2. Add PostgreSQL, migrations, and durable workflow/audit storage.
3. Build the referral queue and referral detail view.
4. Replace testing checkboxes with persisted human review tasks.
5. Add therapist profiles and deterministic matching.
6. Add intake checklist and therapist preparation brief.
7. Add document storage, parsing, and pgvector retrieval.
8. Add report editor, citation validation, and therapist sign-off.
9. Add integrations, security hardening, deployment, and pilot instrumentation.

## Open Decisions

- Whether production inference will be local clinic-hosted, EU cloud-hosted, or hybrid.
- Which authentication provider to use.
- Which object storage provider to use for EU production.
- Which email/calendar providers the first clinic actually uses.
- Whether WhatsApp integration is essential for v1 or should remain a manual channel with copy/paste support.
- Which clinical questionnaires are required in the first clinic pilot.
- Which insurer/EAP evidence-pack formats matter first.
- What level of legal/compliance review is required before processing real patient data.

