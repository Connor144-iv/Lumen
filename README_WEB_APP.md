# Lumen Web Application

FastAPI web layer around the Lumen LangGraph agent engine, with durable product records for workflow runs, referrals, review tasks, communication drafts, audit events, therapist profiles, intake records, and early clinical documentation.

## Setup

```powershell
cd <repo-root>\Lumen
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Configure local model serving. Ollama defaults are:

```powershell
$env:LUMEN_LLM_PROVIDER="ollama"
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:LUMEN_SMALL_MODEL="qwen3:8b"
$env:LUMEN_MEDIUM_MODEL="qwen3:14b-q4_K_M"
$env:LUMEN_COMMUNICATION_MODEL="mistral:7b-instruct"
```

For LM Studio or OpenAI-compatible local serving:

```powershell
$env:LUMEN_LLM_PROVIDER="lmstudio"
$env:LMSTUDIO_BASE_URL="http://localhost:1234/v1"
$env:LUMEN_SMALL_MODEL="local-model-name"
$env:LUMEN_MEDIUM_MODEL="local-model-name"
$env:LUMEN_COMMUNICATION_MODEL="local-model-name"
```

## Database

By default, Lumen creates `lumen_dev.db` in the project root. For PostgreSQL:

```powershell
$env:LUMEN_APP_DATABASE_URL="postgresql+psycopg://user:password@localhost:5432/lumen"
```

Alembic is configured for migration-based deployments:

```powershell
alembic upgrade head
```

The app also calls `create_all` on startup so local development works without a manual migration step.

## Run

```powershell
cd <repo-root>\Lumen
.\.venv\Scripts\Activate.ps1
.\scripts\run_lumen_local.ps1
```

Open `http://127.0.0.1:8000`.

The UI includes:

- Workflow runner for referral intake and session report workflows.
- Sample payload buttons from `samples/`.
- Model health panel backed by `/api/health/models`.
- Durable referral queue and referral detail view.
- Human review inbox with approve, reject, request-change, and escalate persistence.
- Seeded therapist profile list for matching context.
- Therapist profile creation with availability blocks.
- Deterministic therapist matching and appointment slot proposal from referral detail.
- Intake checklist, consent records, governed intake document uploads, generic questionnaire response capture, reminder drafts, and therapist prep brief drafts.
- Clinical foundation page for patient workspace, session notes, clinical library sources, score records, and retrieval chunks.
- Report draft editing with claim-to-evidence validation, unsupported-claim blocking, therapist sign-off, and Markdown export.
- Referral CSV batch imports with source artifact storage, imported referral records, and row-level import errors.
- Integration health, security posture, and feedback metrics panels.

## Admin Referral MVP Redesign Status

The first admin-referral redesign patch keeps the existing backend services but aligns the app around the clinic admin workflow:

- Referral statuses now map toward the target admin workflow terms, with legacy statuses translated for existing local data.
- Referral list/detail responses include derived `secondary_flags`, `next_action`, and human-readable labels for queue filtering and workbench triage.
- Match recommendations, slot proposals, and intake reminders now create review tasks using the existing review inbox.
- Missing-information drafts, clinical escalation checks, simulated patient replies, appointment confirmations, and intake waivers now use explicit review tasks before advancing the admin state gates.
- Missing-information replies are recorded as referral history and can update extracted referral fields.
- Duplicate resolution, suitability review, first-contact message approval, and intake-packet send approval are now explicit admin review gates.
- The Intake page includes a global tracker for active referral intake status and blockers.
- `first_session_ready` is only reached after a confirmed appointment, complete-or-waived intake, and a generated therapist prep brief.
- The main navigation emphasizes the Referral Queue and Admin Workbench. The previous Run Workflow page is preserved as a developer/demo workflow trace runner under System.

The Gmail and Google Calendar MVP is now part of the admin-gated workflow:

- Approved patient-facing communication drafts send through Gmail when Google Workspace is enabled. Demo patient email is restricted to `lumenpatientdemo@gmail.com` unless `LUMEN_OUTBOUND_PATIENT_EMAIL_OVERRIDE` is set.
- Slot proposals use Google Calendar busy time, local appointments, 60-minute sessions, a 10-minute buffer, and the 20-hour weekly therapist patient-contact cap.
- Approved appointment confirmations create Google Calendar events and store the event ID on the local appointment record.
- Approved reschedule tasks update the linked Google Calendar event before the local appointment time changes.
- The Therapists page shows Google-backed busy periods, next available slots, weekly contact capacity, active appointments, sync issues, and drag/drop scheduling targets.

Provider-backed patient reply ingestion beyond the current simulated/manual reply capture remains a later enhancement.

## Smoke Test

Start the server, then run:

```powershell
python scripts\smoke_test.py --base-url http://127.0.0.1:8000
```

The script checks configured model availability, submits the standard referral sample, submits the session report sample, and prints the final job statuses.

## API Highlights

```http
GET /api/health/models
GET /api/examples
GET /api/referrals
GET /api/referrals/{referral_id}
GET /api/review-tasks?status=open
POST /api/review-tasks/{task_id}/actions
GET /api/therapists
GET /api/therapists/calendar-capacity
POST /api/therapists
POST /api/referrals/{referral_id}/match
POST /api/referrals/{referral_id}/appointment-proposals
POST /api/referrals/{referral_id}/missing-info-draft
POST /api/referrals/{referral_id}/missing-info-replies
POST /api/referrals/{referral_id}/clinical-review
POST /api/referrals/{referral_id}/duplicate-review
POST /api/referrals/{referral_id}/suitability-review
POST /api/referrals/{referral_id}/contact-draft
POST /api/referrals/{referral_id}/patient-replies
POST /api/appointments/proposals
POST /api/appointments/{appointment_id}/reschedule-request
GET /api/intake/templates
GET /api/intake/tracker
POST /api/referrals/{referral_id}/intake
GET /api/referrals/{referral_id}/intake
POST /api/referrals/{referral_id}/documents
POST /api/referrals/{referral_id}/intake-packet-draft
POST /api/referrals/{referral_id}/intake-reminder
POST /api/intake/items/{item_id}/complete
POST /api/intake/items/{item_id}/exception-request
POST /api/consent-records/{consent_id}/complete
POST /api/consent-records/{consent_id}/exception-request
POST /api/referrals/{referral_id}/questionnaires
POST /api/referrals/{referral_id}/prep-brief
GET /api/patients/{patient_id}/workspace
POST /api/referrals/{referral_id}/session-notes
POST /api/session-notes/{note_id}/approve
GET /api/clinical-library
POST /api/clinical-library
GET /api/retrieval/search
POST /api/referrals/{referral_id}/reports/draft
PUT /api/report-drafts/{report_id}
POST /api/report-drafts/{report_id}/sign-off
GET /api/report-drafts/{report_id}/export
POST /api/report-drafts/{report_id}/feedback
GET /api/feedback/metrics
GET /api/integrations/health
POST /api/integrations/referral-batches
GET /api/integrations/referral-batches
GET /api/integrations/import-errors
POST /api/integrations/email-referrals
GET /api/security/context
GET /api/security/posture
GET /api/workflows
```

Start a workflow:

```http
POST /api/run-workflow
Content-Type: application/json

{
  "workflow_type": "new_referral",
  "tenant_id": "demo-clinic",
  "source_channel": "webform",
  "raw_text": "Referral for an adult patient requesting online therapy in Portuguese."
}
```

Workflow events are persisted in `workflow_events` and streamed over:

```http
GET /api/events/{job_id}
```

Human approvals are stored in `human_review_tasks`; approving resumable gates such as `match_approval`, `send_approval`, and `therapist_signoff` starts a follow-on workflow run with the approved gate recorded in the request.

## Phase 4 and 5 Workflow

From a referral detail view:

1. Run deterministic matching to rank active therapists using explicit profile facts: capacity, insurance, language, modality, specialty text, and availability.
2. Propose appointment slots from the selected therapist's availability blocks.
3. Confirm a proposed slot only after human review.
4. Start intake to create required checklist items and consent records from the active intake template.
5. Upload governed intake documents or mark checklist items and consent records complete as staff receive them.
6. Draft a missing-item reminder; the draft stays pending until staff approve it.
7. Save a generic screening questionnaire response.
8. Generate a therapist prep brief from referral facts, risk status, intake status, scores, and proposed appointments.

## Phase 6 Clinical Foundation

The clinical page adds the first governed documentation substrate:

- Patient workspace aggregation for referrals, documents, session notes, and score records.
- Therapist-authored session notes with explicit approval state.
- Clinical library records for protocols, report templates, insurer rules, and references.
- Text chunk indexing for session notes, clinical library records, and uploaded text/CSV/JSON intake documents.
- Keyword retrieval endpoint as an MVP stand-in for the planned pgvector-backed retrieval store.

## Phase 7 Report Draft Slice

The clinical page can create a retrieval-backed report draft from a referral after evidence exists in session notes, uploaded text, or the clinical library. Drafts include a claim-to-evidence map, block creation if no retrieval evidence is found, and require explicit sign-off before their status changes to `signed_off`.

Edited drafts are revalidated before sign-off. Unsupported bullets in the evidence-grounded clinical summary block sign-off until a therapist adds evidence labels or removes the unsupported claim. Signed reports can be exported as Markdown.

## Phase 8-11 MVP Slices

- Phase 8: CSV referral batch import creates normal referral records and stores row-level import errors for staff review. Manual email referral capture is available for webhook or polling adapters.
- Phase 9: Security posture and user context endpoints expose the active role, permissions, audit count, review backlog, retention setting, and model data policy.
- Phase 10: Integration health and governance metrics give operators a basic pilot-readiness view.
- Phase 11: Draft feedback records capture therapist edits and opt-in practice-memory eligibility for later evaluation.

These remain MVP controls. Patient-facing communication and final clinical records stay gated by human review.
