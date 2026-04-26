# Lumen Web Application

FastAPI web layer around the Lumen LangGraph agent engine, with durable product records for workflow runs, referrals, review tasks, communication drafts, audit events, and therapist profiles.

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
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

The UI includes:

- Workflow runner for referral intake and session report workflows.
- Sample payload buttons from `samples/`.
- Model health panel backed by `/api/health/models`.
- Durable referral queue and referral detail view.
- Human review inbox with approve, reject, request-change, and escalate persistence.
- Seeded therapist profile list for matching context.

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
