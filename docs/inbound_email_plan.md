# Inbound Gmail Reply Ingestion Plan

## Goal
Add a minimal, safe Gmail reply ingestion path to capture patient replies (missing-info and scheduling/unclear) without auto-classification. Keep changes small and avoid schema refactors.

## Phase 0 (Gate: Required Before Any Ingestion)
1. Reauthorize Google Workspace so the local token includes `gmail.modify`.
2. Run a real Gmail smoke test that searches and reads one unread email from `lumenpatientdemo@gmail.com`.
3. Do not implement ingestion until Phase 0 passes.

## Phase 1 (Minimal Ingestion)
- Read unread Gmail messages and attempt matching.
- Process missing-info replies into the existing missing-info flow.
- For scheduling or unclear replies, store and route to admin review (no auto-classification).
- Only mark messages as processed after the DB write succeeds.

## Reply Matching Order (Updated)
1. Gmail `threadId` -> `CommunicationDraft.gmail_thread_id`.
2. Referral ID found in subject/body (exact token match).
3. Sender email + active missing-info/referral status.
4. Otherwise store as unmatched inbound email for admin review.

## Data + Idempotency
- Store Gmail message ID in `Document.metadata_json` or in a lightweight inbound email document record.
- Skip already-processed Gmail message IDs.
- Only remove the `UNREAD` label after the DB transaction is committed.

## Required Implementation Pieces
- **Google provider helpers**: Add minimal Gmail read helpers in `backend/lumen_web/google_workspace.py` using `gmail.modify`.
- **Repository ingestion**: Add a new ingestion helper in `backend/lumen_web/repositories.py` that:
  - Uses the matching order above.
  - Calls `record_missing_info_reply()` for missing-info replies.
  - Adds a real `record_patient_reply()` path for Gmail replies (do not reuse `record_simulated_patient_reply()`).
  - Stores Gmail message ID in the document metadata for idempotency.
- **Unmatched inbound**: Store as a document record (e.g., `document_type="inbound_email_unmatched"`) with Gmail metadata for admin review.

## Smoke Test (Phase 0)
- Reauthorize with `scripts/google_workspace_auth.py` so the token includes `gmail.modify`.
- Use the Gmail API to search and read one unread email from `lumenpatientdemo@gmail.com`.
- The test is considered passing only if:
  - The message is found.
  - The payload can be read.
  - No scope errors occur.

## Tests to Update/Add
- Update the scope label assertion in `tests/test_google_workspace_integration.py` to include `gmail.modify`.
- Add an ingestion idempotency test that processes the same Gmail message ID twice and confirms the second run is skipped.

## Risks / Unknowns
- Multi-tenant routing (single inbox vs per-tenant inbox) is not defined.
- Reply parsing: HTML vs plain text and quoted replies need a safe extraction policy.
- Drafts without a `gmail_thread_id` need a clear skip/fallback policy.
- Admin review UI for unmatched inbound emails is not defined yet.
