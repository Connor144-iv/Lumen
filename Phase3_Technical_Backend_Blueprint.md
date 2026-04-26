# Lumen Phase 3 — Technical Backend Blueprint

**Product:** Lumen, a multi-agent AI workflow platform for small Portuguese mental-health practices  
**Target deployment:** local-first prototype on a high-end Legion Pro laptop with NVIDIA GPU, then EU-hosted production deployment  
**Core design constraint:** Lumen may ingest, extract, classify, retrieve, draft, compare, and summarize, but it must not autonomously diagnose, contact patients, submit claims, save clinical reports, or change treatment plans without human approval.

## 1. Model Selection & Agent Skills Definition

The recommended architecture separates tasks that should be deterministic software from tasks that genuinely need language-model reasoning. The local prototype should use 7B–14B open-weight models in 4-bit quantization. Larger 32B–70B models should be reserved for EU-hosted production inference or offline evaluation.

| Agent Name | Recommended LLM | Core Skills/Tools Required | System Prompt Focus |
|---|---|---|---|
| **Workflow Orchestrator & Governance Controller** | **Local MVP:** `Qwen/Qwen3-14B` quantized 4-bit via LM Studio, Ollama, or llama.cpp. <br> **Production:** `Qwen/Qwen3-8B` or  served through vLLM/SGLang on EU GPU infrastructure. | LangGraph state graph, FastAPI event router, PostgreSQL transaction writes, audit-log writer, Pydantic schema validation, confidence-threshold routing, human-approval gates, RBAC/ABAC policy checks. | Coordinate the workflow, call only authorized tools, enforce human approval boundaries, and never perform clinical judgement directly. |
| **Referral Intake Normalizer** | **Text normalization:** `Qwen/Qwen3-8B` in constrained JSON mode. <br> **Speech:** `openai/whisper-large-v3` via faster-whisper; evaluate Portuguese-tuned Whisper variants before production. | Email ingestion, WhatsApp Business API webhook, voicemail upload, Doctoralia export parser, Excel/CSV parser, OCR for scanned referrals, ASR transcription, deduplication, entity normalization, Pydantic JSON extraction. | Convert every inbound referral channel into one clean referral record without adding clinical interpretation. |
| **Clinical Signal & Completeness Extractor** | **Local MVP:** `Qwen/Qwen3-8B` with guided JSON decoding. <br> **Specialized classifier layer:** fine-tuned `neuralmind/bert-base-portuguese-cased` or XLM-RoBERTa for presenting-concern classification once labelled data exists. | Structured extraction, missing-field detection, controlled vocabulary mapping, fuzzy matching for insurers, language/modality extraction, source-span citation, schema repair loop. | Extract only clinically and administratively relevant signals supported by source text; mark unknowns rather than inferring. |
| **Risk, Urgency & Suitability Reviewer** | **Primary model:** fine-tuned `neuralmind/bert-base-portuguese-cased` or `xlm-roberta-base` classifier. <br> **LLM support:** `Qwen/Qwen3-8B` only for generating a clinician-readable explanation from classifier spans, not for final risk classification. | Binary/multi-label risk classification, threshold calibration for high recall, trigger-span extraction, red-flag lexicon, post-session note scanning, fail-closed escalation, clinician alert generation. | Detect possible self-harm, suicidality, safeguarding, or acute-crisis signals and escalate uncertain or positive cases to a human clinician. |
| **Therapist Matching & Capacity Planner** | **Local MVP:** deterministic rules plus `Qwen/Qwen3-8B` for plain-language rationale. <br> **Later ML:** LightGBM/XGBoost learning-to-rank model trained on historical match outcomes, no-shows, reassignments, and treatment-continuation signals. | SQL over therapist profiles, calendar API, availability rules, specialty/language/modality matching, insurance compatibility, capacity constraints, ranking logic, contraindication checks. | Rank suitable therapists using explicit clinic rules and explain the recommendation without pretending to know therapeutic fit beyond available data. |
| **Patient Communication & Scheduling Drafter** | **Local MVP:** `mistralai/Mistral-7B-Instruct-v0.3` or `Qwen/Qwen3-8B`. <br> **Fine-tuned version:** LoRA adapter on approved clinic emails for Portuguese tone, brevity, and warmth. | Draft generation, tone guide retrieval, calendar-slot insertion, Google Calendar/Cal.com API, WhatsApp/email template rendering, no-show reminder templates, message safety filter, human-send approval. | Draft concise, respectful, clinic-specific patient communications while avoiding diagnosis, clinical promises, or autonomous sending. |
| **Consent & Pre-Session Intake Collector** | **Local MVP:** mostly deterministic form workflow; `Qwen/Qwen3-8B` for patient-friendly explanations and missing-document reminders. | Form builder, consent-record store, PHQ-9/GAD-7 or clinic-specific questionnaire ingestion, insurer field validation, document upload, consent expiry checks, pre-session brief generation. | Collect required intake and consent data, identify missing items, and preserve strict boundaries around consent and special-category health data. |
| **Clinical Documentation & Protocol Matcher** | **Local MVP:** `Qwen/Qwen3-14B` with RAG and strict structured output. <br> **Production:** `Qwen/Qwen3-8B`  if benchmarked improvement justifies GPU cost. | RAG retrieval from therapist protocols, session-note parsing, protocol-step coverage mapping, source-span grounding, longitudinal note comparison, score extraction, missing-protocol-field detection. | Map therapist-authored notes to the selected protocol, identify covered/missing elements, and never invent clinical facts or diagnoses. |
| **Report, Treatment Review & Evidence Pack Writer** | **Local MVP:** `Qwen/Qwen3-14B` with retrieval-grounded drafting. <br> **Production:** `Qwen/Qwen3-8B`or a fine-tuned model if quality is sufficient. <br> **Benchmark only:** GPT-4o/Claude may be used on synthetic or anonymized data, not raw patient data. | Long-form structured drafting, report template retrieval, insurer/EAP evidence rules, source citation enforcement, treatment-plan review summaries, discharge-summary drafting, output linting for unsupported claims. | Produce evidence-grounded drafts for therapist review, with every claim traceable to an approved note, protocol, score, template, or source document. |

**Recommended prototype stack**

- **Frontend:** Streamlit for academic prototype; React + Vite for production UI.
- **Backend:** FastAPI, Pydantic v2, LangGraph, SQLAlchemy, Alembic.
- **Model serving:** LM Studio or Ollama for local testing; vLLM or SGLang for production with structured outputs.
- **Primary database:** PostgreSQL 16+ with JSONB, row-level security, and tenant-scoped encryption.
- **Vector layer:** PostgreSQL + pgvector for MVP and early production; Pinecone/Weaviate only if scaling beyond what Postgres can serve.
- **Object storage:** MinIO or S3-compatible EU object storage for uploaded PDFs, DOCX files, scanned referrals, and generated documents.
- **Queueing:** Redis Queue or Celery for prototype; Temporal or Dramatiq for production-grade durable background jobs.
- **Observability:** OpenTelemetry traces, Prometheus metrics, Grafana dashboards, LangSmith-style prompt traces only if self-hosted or redacted.

## 2. Specialized ML Capabilities — Fine-Tuning and RAG

### 2.1 Target for Fine-Tuning

The strongest fine-tuning target is the **Risk, Urgency & Suitability Reviewer**.

A generic prompt is not defensible for risk detection because the required behaviour is asymmetric: a false negative is far more serious than a false positive. The system needs measurable recall, threshold calibration, held-out test metrics, and auditable trigger spans. Prompting a general LLM can produce plausible explanations, but it does not provide a stable decision boundary, reproducible sensitivity/specificity, or clear threshold control.

**Recommended fine-tuning design**

| Component | Technical choice |
|---|---|
| Base model | `neuralmind/bert-base-portuguese-cased` or `xlm-roberta-base` |
| Task | Multi-label classification: `no_flag`, `self_harm_signal`, `suicidality_signal`, `acute_crisis_signal`, `safeguarding_signal`, `requires_clinician_review` |
| Training method | Supervised fine-tuning with weighted loss or focal loss to favour recall on high-risk classes |
| Calibration | Temperature scaling or isotonic regression on a validation set; operating threshold deliberately biased toward high recall |
| Output | `{risk_present, risk_category, confidence, trigger_spans, recommended_handoff}` |
| Evaluation | Recall on high-risk classes, false-negative audit, precision/recall curve, confusion matrix, calibration curve, clinician review of false positives |
| Production rule | Any classifier failure, low confidence, or schema failure routes to clinician review rather than continuing the automated pipeline |

**Dataset**

The first training set should combine:

1. Public Portuguese-language mental-health or suicidality datasets where licensing permits research use, such as Boamente and SetembroBR-style social-text corpora.
2. Synthetic European Portuguese referral and session-note examples generated from realistic but non-identifiable scenarios.
3. Clinician-labelled pilot examples after pseudonymization and formal data-processing agreements.
4. A hard negative set: benign clinical messages, scheduling messages, insurance messages, and routine therapy notes that contain emotionally intense language but do not indicate acute risk.

The model should not be treated as clinically validated until it has been tested on European Portuguese clinic-style referrals and therapist-authored notes. Brazilian Portuguese public data is useful for bootstrapping, but it is not enough for production.

### 2.2 Secondary Fine-Tuning Targets

The **Patient Communication & Scheduling Drafter** should be fine-tuned with LoRA once a clinic has enough approved examples. Prompting can imitate tone superficially, but it will not reliably learn the clinic's preferred level of warmth, brevity, formality, and Portuguese phrasing. The dataset should be approved historical first-contact emails, appointment confirmations, rescheduling messages, and therapist-edited AI drafts. The target metric is edit distance from the final approved message plus therapist approval rate.

The **Report, Treatment Review & Evidence Pack Writer** should use RAG first, then fine-tuning only after the team has accumulated a dataset of signed reports and rejected drafts. Fine-tuning is useful here because report style, section ordering, evidentiary phrasing, and insurer/EAP evidence-pack conventions become highly practice-specific. The dataset should contain pseudonymized session notes, protocol coverage maps, retrieved protocol snippets, draft reports, final signed reports, and edit annotations.

A practical fine-tuning path is:

1. **Phase A:** Prompting + RAG only.
2. **Phase B:** LoRA adapter on clinic communication style.
3. **Phase C:** Risk classifier fine-tune and threshold calibration.
4. **Phase D:** Report-writer supervised fine-tune on pseudonymized, therapist-approved examples.
5. **Phase E:** Preference tuning from therapist edit/reject/approve feedback, only if consent and governance are strong enough.

### 2.3 Target for RAG

The highest-priority RAG target is the **Clinical Documentation & Protocol Matcher**, followed by the **Report, Treatment Review & Evidence Pack Writer**.

These agents cannot rely on model memory because the relevant knowledge is local to each therapist and clinic: uploaded protocols, preferred templates, previous approved reports, insurer requirements, and patient-specific longitudinal history. RAG is more appropriate than fine-tuning for this knowledge because the content changes frequently, must remain tenant-isolated, and must be cited back to source documents.

**RAG corpora**

| Corpus | Used by | Contents | Storage rules |
|---|---|---|---|
| **Therapist protocol library** | Protocol Matcher, Report Writer | CBT intake protocol, trauma assessment framework, ADHD assessment protocol, screening-score interpretation rules, therapist checklists | Tenant- and therapist-isolated; never used to train global models |
| **Practice memory** | Communication Drafter, Report Writer | Approved emails, approved reports, edited drafts, preferred wording, clinic templates | Tenant-isolated; opt-in for adapter fine-tuning only after pseudonymization |
| **Patient longitudinal workspace** | Protocol Matcher, Report Writer, Risk Reviewer | Approved notes, previous scores, goals, treatment-plan reviews, attendance record, consent records | Patient-scoped retrieval only; strict access control |
| **Portuguese clinical reference** | Report Writer, Intake Collector | Public DGS/OPP guidance, scoring rules, clinic-approved psychoeducation text | Read-only; versioned; citations required |
| **Insurer/EAP evidence rules** | Evidence Pack Writer | Required forms, attendance proof, fields, report structure, claim-support formats | Versioned by insurer and effective date |

**Vector database and embedding strategy**

Use **PostgreSQL + pgvector** rather than Pinecone or Chroma for the first production version. The system already needs PostgreSQL for patient records, audit logs, tenant isolation, transactions, backups, and row-level security. Keeping vectors inside Postgres reduces architecture complexity and avoids moving health data into a separate managed vector service.

Recommended implementation:

- **Embedding model:** `BAAI/bge-m3`, because it supports multilingual retrieval and can handle dense, sparse, and multi-vector retrieval modes.
- **Vector dimension:** 1024-dimensional dense vectors for pgvector.
- **Chunking:**  
  - Protocols: 300–600 token semantic chunks, 50-token overlap, section-aware.  
  - Reports: chunk by heading/section, not fixed window.  
  - Session notes: one chunk per approved note section plus metadata for date, session number, and protocol.  
  - Scoring rubrics: one chunk per scoring rule or interpretation band.
- **Metadata filters:** `tenant_id`, `clinic_id`, `therapist_id`, `patient_id`, `document_type`, `protocol_id`, `source_version`, `consent_scope`, `effective_date`, `language`, `created_at`.
- **Index:** HNSW cosine index for most MVP and early-production workloads; IVFFlat only if benchmarking shows better cost/performance at larger scale.
- **Retrieval pattern:** hybrid retrieval using pgvector dense similarity plus PostgreSQL full-text search/BM25-style lexical retrieval. Rerank the top 20 chunks with a cross-encoder or BGE reranker, then pass the top 5–8 source chunks to the LLM.
- **Citation requirement:** every report sentence that states a clinical fact must map to either a source-note span, retrieved protocol chunk, approved score, or template rule.

**RAG flow**

1. The Orchestrator receives a therapist-authored note.
2. The Protocol Matcher builds a query from `presenting_problem`, `selected_protocol`, `session_number`, and note text.
3. Retrieval applies metadata filters first, then semantic search.
4. Reranking selects the most relevant protocol sections and patient-history notes.
5. The Protocol Matcher emits a structured coverage map with source references.
6. The Report Writer retrieves templates and prior approved reports.
7. The Report Writer drafts a report with source references.
8. A citation validator rejects unsupported claims before the draft reaches the therapist.

## 3. Agent Orchestration & Handoffs

### 3.1 State Management

Use **LangGraph with a typed shared state graph**, not a free-form AutoGen-style chat. Lumen is a regulated workflow, not a brainstorming conversation. The workflow has known states, mandatory human gates, strict handoff schemas, and safety-critical failure modes. A conversational multi-agent pattern would make the system harder to audit and harder to constrain.

The recommended orchestration pattern is a **hierarchical supervisor inside a LangGraph state machine**:

- The **Workflow Orchestrator & Governance Controller** is the supervisor node.
- Each specialist agent is a graph node.
- Deterministic routers decide the next node based on validated state, not free-form agent conversation.
- Human approval is represented as an explicit graph node, not an informal pause.
- Every graph transition writes an append-only audit event.
- Long-running workflows are checkpointed after each node.

**Core workflow graph**

```text
NEW_REFERRAL
  -> Referral Intake Normalizer
  -> Clinical Signal & Completeness Extractor
  -> Risk, Urgency & Suitability Reviewer
      -> if elevated/unknown risk: Human Clinical Review
      -> if standard risk: Therapist Matching & Capacity Planner
  -> Human Match Approval
  -> Patient Communication & Scheduling Drafter
  -> Human Send Approval
  -> Consent & Pre-Session Intake Collector
  -> Therapist Preparation Brief

SESSION_COMPLETED
  -> Clinical Documentation & Protocol Matcher
  -> Risk, Urgency & Suitability Reviewer
      -> if elevated/unknown risk: Human Clinical Review
      -> if standard risk: Report, Treatment Review & Evidence Pack Writer
  -> Therapist Review and Sign-off
  -> Governed Patient Record + Practice Memory Update
```

**Storage split**

| State type | Technology | Purpose |
|---|---|---|
| Workflow state | LangGraph checkpoint store backed by PostgreSQL | Resume interrupted workflows and preserve node-by-node state |
| Application records | PostgreSQL tables + JSONB | Patients, referrals, therapist profiles, appointments, documents, approvals |
| RAG memory | PostgreSQL + pgvector | Protocols, templates, report chunks, patient history, clinical references |
| Raw files | MinIO/S3-compatible EU object storage | Uploaded PDFs, DOCX files, scanned images, Excel batches |
| Audit log | Append-only PostgreSQL table | Regulatory traceability of every model/tool action |
| Ephemeral working memory | Redis with TTL | Temporary ASR text, repair attempts, short-lived draft state |

### 3.2 Handoff Protocols

Agents should not pass natural-language blobs to one another. Every handoff should use strictly typed Pydantic schemas and should be rejected if validation fails.

**Universal agent envelope**

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional
from uuid import UUID
from datetime import datetime

class EvidenceRef(BaseModel):
    source_type: Literal["referral", "session_note", "protocol", "template", "score", "calendar", "insurer_rule"]
    source_id: UUID | str
    span_start: Optional[int] = None
    span_end: Optional[int] = None
    quote_hash: Optional[str] = None
    relevance_score: Optional[float] = Field(default=None, ge=0, le=1)

class AgentError(BaseModel):
    code: str
    message: str
    recoverable: bool
    retry_count: int = 0

class AgentEnvelope(BaseModel):
    workflow_id: UUID
    tenant_id: UUID
    patient_id: Optional[UUID] = None
    agent_name: str
    schema_version: str
    status: Literal["ok", "needs_human_review", "blocked", "failed"]
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef] = []
    errors: list[AgentError] = []
    created_at: datetime
```

**Key payload schemas**

```python
class ReferralRecord(BaseModel):
    referral_id: UUID
    source_channel: Literal["email", "voicemail", "whatsapp", "doctoralia", "excel", "webform"]
    patient_name: Optional[str]
    date_of_birth: Optional[str]
    contact_email: Optional[str]
    contact_phone: Optional[str]
    insurer: Optional[str]
    referring_entity: Optional[str]
    raw_text_ref: str
    dedupe_candidates: list[UUID] = []
    extraction_confidence: float = Field(ge=0, le=1)

class ClinicalSignals(BaseModel):
    presenting_concern: Optional[str]
    language_preference: Optional[str]
    modality_preference: Optional[Literal["in_person", "online", "hybrid", "unknown"]]
    availability_text: Optional[str]
    age_band: Optional[Literal["child", "adolescent", "adult", "older_adult", "unknown"]]
    missing_required_fields: list[str]
    source_spans: list[EvidenceRef]

class RiskReview(BaseModel):
    risk_present: bool
    risk_category: Literal["none", "self_harm", "suicidality", "acute_crisis", "safeguarding", "unknown"]
    urgency: Literal["standard", "elevated", "urgent", "unknown"]
    confidence: float = Field(ge=0, le=1)
    trigger_spans: list[EvidenceRef]
    required_handoff: Literal["continue", "clinician_review", "director_review"]

class TherapistMatchRecommendation(BaseModel):
    ranked_matches: list[dict]
    excluded_therapists: list[dict]
    rationale: str
    hard_constraints_checked: list[str]
    requires_human_approval: bool = True

class CommunicationDraft(BaseModel):
    channel: Literal["email", "sms", "whatsapp"]
    subject: Optional[str]
    body: str
    proposed_slots: list[str] = []
    prohibited_content_check_passed: bool
    requires_human_send: bool = True

class ProtocolCoverageMap(BaseModel):
    protocol_id: UUID
    session_note_id: UUID
    covered_steps: list[dict]
    partial_steps: list[dict]
    missing_steps: list[dict]
    extracted_scores: list[dict]
    unsupported_inferences: list[str]
    evidence: list[EvidenceRef]

class ReportDraft(BaseModel):
    report_type: Literal["session_summary", "assessment_report", "treatment_review", "evidence_pack", "discharge_summary"]
    markdown_body: str
    claim_evidence_map: list[dict]
    unsupported_claims: list[str]
    requires_therapist_signoff: bool = True
```

**Handoff rules**

- Every agent receives only the minimum necessary payload.
- Every output must validate against its Pydantic schema before the next node is allowed to run.
- Every clinical extraction must carry source spans or evidence references.
- Every patient-facing message must be tagged `requires_human_send = True`.
- Every clinical report must be tagged `requires_therapist_signoff = True`.
- No model may write directly to the final patient record; only an approved human action can persist a final clinical document.
- Any agent output with `unsupported_claims` or missing evidence is blocked from progression.

### 3.3 Error Handling

The system should fail closed. In this product, the safe failure mode is to stop automation and route the case to a human review queue.

| Failure mode | Detection method | Automated response | Human-facing result |
|---|---|---|---|
| Invalid JSON or schema failure | Pydantic validation error | Retry once with a schema-repair prompt; if still invalid, mark failed | Task appears in admin review queue |
| Low extraction confidence | Confidence below threshold or missing critical fields | Generate missing-field task; do not continue to matching | Admin sees exactly what is missing |
| Risk classifier timeout/failure | Tool timeout, model server error, missing output | Fail closed as `risk_unknown` | Clinician/director review required |
| Elevated or unknown risk | Risk score above threshold or ambiguous positive | Stop normal pipeline | Clinician receives trigger spans and referral/session context |
| RAG retrieval returns weak evidence | Top-k similarity below threshold or no source chunks | Do not draft evidence-grounded report | Therapist asked to select protocol or upload source |
| Unsupported report claim | Citation validator finds claim without source | Remove claim or block draft | Therapist sees unsupported-claim warning |
| Calendar API conflict | Slot no longer available or API write fails | Re-query calendar and regenerate slots | Admin confirms revised slots manually |
| Duplicate referral suspected | Dedupe similarity above threshold | Block automatic record creation | Admin chooses merge, ignore, or create new patient |
| Model hallucination pattern detected | LLM output contains diagnosis, promise of outcome, or uncited clinical statement | Output rejected by policy checker | Therapist receives safe draft or no draft |
| Tenant isolation violation | Query attempts cross-tenant retrieval | Hard block, security event | Compliance/security alert |
| Object parsing failure | PDF/OCR/docx parser error | Fallback OCR/parser; if still failed, request manual upload or review | Admin sees parse failure with file name |
| Human rejection | Therapist/admin rejects draft | Store rejection and reason | Draft not used; feedback enters practice memory only if allowed |

### 3.4 Confidence Thresholds

Use explicit thresholds by agent rather than a single global score.

| Agent | Suggested threshold | Action below threshold |
|---|---:|---|
| Referral Intake Normalizer | 0.85 for identity/contact fields | Admin review before patient record creation |
| Clinical Signal Extractor | 0.80 for presenting concern; 0.90 for insurer/contact fields | Missing-field task |
| Risk Reviewer | Low threshold for positive class, calibrated for recall | Clinician review on positive or unknown |
| Therapist Matching Planner | 0.75 ranking confidence | Director/admin approval required |
| Communication Drafter | 0.80 tone/safety confidence | Human edit required |
| Protocol Matcher | 0.80 coverage confidence | Therapist selects protocol or confirms missing steps |
| Report Writer | 0.90 citation-validity requirement | Block unsupported claims |

### 3.5 Production Deployment Topology

```text
Client UI
  -> FastAPI Gateway
      -> Auth / RBAC / Tenant Policy
      -> LangGraph Orchestrator
          -> Tool nodes
          -> Model-serving endpoints
          -> Human-approval nodes
      -> PostgreSQL + pgvector
      -> MinIO/S3 EU object storage
      -> Redis ephemeral cache
      -> Audit-log writer
      -> Observability stack
```

**Model-serving layout**

- Small LLM endpoint: `Qwen3-8B` for extraction, drafting, and explanations.
- Medium LLM endpoint: `Qwen3-14B` for protocol matching and report drafts.
- Classifier endpoint: BERT/XLM-R risk classifier with CPU fallback.
- ASR endpoint: Whisper large-v3 with no persistent audio storage.
- Embedding endpoint: BGE-M3 for document ingestion and query embedding.
- Optional reranker endpoint: BGE reranker or equivalent cross-encoder.

### 3.6 Why LangGraph Over AutoGen

AutoGen is useful for open-ended multi-agent discussion. Lumen is different. It needs a deterministic, auditable workflow where each agent has constrained authority, explicit inputs, typed outputs, and mandatory human gates. LangGraph is the better fit because it supports graph-based control flow, checkpointing, human-in-the-loop pauses, and stateful execution. The system can still use an LLM as the orchestrator's routing assistant, but the final route decision should be constrained by code and policy, not by free-form conversation.

### 3.7 Minimum Viable Technical Build

For the academic prototype, build the smallest version that still demonstrates the architecture:

1. Streamlit UI with two workflows: referral intake and session-note-to-report.
2. FastAPI backend with LangGraph orchestration.
3. PostgreSQL + pgvector database.
4. Local model serving through LM Studio/Ollama for `Qwen3-8B` and `Qwen3-14B`.
5. BGE-M3 embedding pipeline for uploaded protocol documents.
6. Pydantic schemas for every handoff.
7. Human approval screen for email draft and report draft.
8. Audit log table showing every agent invocation, model name, confidence score, and approval status.
9. Risk classifier stub in v0, upgraded to a fine-tuned Portuguese BERT classifier if time allows.
10. Evaluation report measuring extraction accuracy, retrieval precision@k, report citation fidelity, and human edit rate.
