# Lumen Product Plan Gap Analysis

Date: 2026-04-27

Scope: compared `LUMEN_COMPLETE_PRODUCT_PLAN.md` against the current FastAPI app, SQLAlchemy models, frontend pages, migrations, and tests.

## Summary

Phases 0-7 are introduced in the app, but several are still MVP slices. Phases 8-11 have only early scaffolding. Lumen is not clinic-handover ready because authentication/RBAC enforcement, real integrations, production deployment, backup/restore, consent-aware retrieval, and evaluation loops are not complete.

## Gaps By Phase

| Phase | Current state | Not introduced or still materially incomplete |
|---:|---|---|
| 0 | Mostly introduced | No major app gap; model validation is manual/health-check oriented rather than a broader startup/runtime readiness workflow. |
| 1 | Introduced | Local default is SQLite; no Docker/local PostgreSQL setup, production DB deployment path, or strict tenant enforcement across every service boundary. |
| 2 | Introduced | Referral filters are limited; duplicate detection is only scaffolding; no real contact/send action; full lifecycle actions for `contacted` and `closed` are missing. |
| 3 | Introduced | No real authentication-backed role enforcement; communication draft editing is limited; review history is not surfaced across patient/referral/workflow views; note/report sign-off is not consistently modeled as review tasks. |
| 4 | Introduced | No clinic-level matching rule configuration; no LLM rationale layer over deterministic facts; no real calendar import/sync; appointment confirmation can bypass a stored review task. |
| 5 | Introduced | No intake-template management UI; questionnaire support is generic, not specific PHQ-9/GAD-7 templates; uploads have policy checks but not real antivirus; patient-facing collection/reminder sending is not implemented. |
| 6 | Introduced | No MinIO/S3 object storage; no pgvector or embedding pipeline; PDF/DOCX/XLSX parsing is metadata-only or absent; retrieval is keyword MVP; consent-aware and access-controlled retrieval is incomplete. |
| 7 | Introduced | Protocol coverage map and post-session risk review are not persisted in the product UI; selected protocol/template is not strictly required for all report generation; PDF/DOCX export is missing; formal insurer/EAP formats are not specialized. |
| 8 | Early slice | Real email webhook/polling ingestion, XLSX batch parsing, calendar integration, outbound email sending, WhatsApp/SMS, Doctoralia workflow, and import-error resolution UI are missing. |
| 9 | Early slice | No login/session auth, RBAC middleware/service enforcement, patient-level access rules, audit immutability enforcement, retention actions, encryption strategy, backup/restore, data export/deletion workflows, or privacy/security docs. |
| 10 | Not introduced | No containerization, staging environment, CI/CD, deployment architecture, production migration flow, observability dashboards, error tracking, operational runbook, pilot onboarding, or pilot success metric workflow. |
| 11 | Early slice | Draft feedback metrics exist, but no practice-memory retrieval store, evaluation datasets, extraction accuracy reports, retrieval precision checks, citation fidelity checks, risk classifier evaluation, or fine-tuning pipeline. |

## Highest-Priority Remaining Work

1. Add real authentication and enforce tenant/role checks at the service layer.
2. Replace keyword retrieval with tenant-scoped pgvector embeddings and consent-aware filters.
3. Add real channel integrations: email ingestion, XLSX import, calendar sync, and approved outbound email.
4. Add production deployment assets: containers, migrations in deploy flow, backup/restore, logs, metrics, and runbook.
5. Build evaluation datasets and quality reports before using practice memory or fine-tuning.
