# Lumen UI Mockups Guidance

These mockups are directional references for the Lumen admin UI redesign. They are not pixel-perfect specifications.

Codex should use them to understand:

- Page structure and hierarchy
- Relative importance of sections
- Intended admin workflow flow
- Card/table layout patterns
- How to reduce clutter from the current UI
- How to keep the interface aligned with the existing Lumen visual style

Do not copy the mockups exactly if the current component structure suggests a simpler implementation. Preserve existing working functionality where possible. Prioritise workflow clarity, explicit state transitions, reusable components, and minimal safe changes.

The redesign should make Lumen feel like a clinic operations product, not a developer workflow console.

## Core UX Principle

The app should be organised around operational flow:

- **Overview** shows the clinic-wide portfolio.
- **Workbench** handles one referral in detail.
- **New Referral** creates or imports referrals.
- **Therapists** manages matching data, capacity, availability, and assignments.
- **Integrations** remains mostly unchanged for now.
- **System / Agents** manages the agentic system and developer diagnostics.

The admin should always be able to answer:

- Where is this referral?
- What is blocking it?
- Who owns the next action?
- What has the agentic system done?
- What needs human approval?
- What happens after approve, reject, request changes, or escalate?
- What has happened previously?

## Target Navigation

Use this simplified MVP navigation:

1. Overview
2. Workbench
3. New Referral
4. Therapists
5. Integrations
6. System / Agents

Remove these as standalone MVP pages:

- Review Inbox
- Intake & Scheduling

Their useful functions should be absorbed into Overview, Workbench, and Therapists.

Remove global top-right `View trace` as an operational action. Trace/debug functions should live inside `New Referral` or `System / Agents`.

---

# 1. Overview

## Purpose

The Overview page is the admin command centre. It should show the overall state of clinic operations and route the admin to the right next place.

It should not become a detailed workbench for processing every referral.

## Contents

### Top metrics

Show compact operational metrics, such as:

- Active referrals
- Open human gates / review tasks
- Active therapists
- Blocked referrals
- First sessions ready
- New referrals today or this week

### Referral journey board

Keep the journey board as the main visual map of where referrals are now.

Suggested columns:

- Captured / triage
- Clinical review
- Matching
- Contact & scheduling
- Intake & prep
- Ready

Each column should show count, short description, and compact referral cards.

### Referral queue

Move the referral queue from the current Referrals page into Overview.

The queue should be compact and prioritised. It should show:

- Patient/referral name
- Current stage
- Blocker
- Owner
- Next action
- Age/date
- Risk or missing-information indicators

Clicking a referral should open it in Workbench.

### Needs attention

Use this section to replace the separate Review Inbox page.

It should show cross-referral tasks such as:

- Missing information reviews
- Draft message approvals
- Clinical escalations
- Match approvals
- Slot approvals
- Send approvals
- Intake blockers
- Failed workflows
- Ageing referrals

Each task should show:

- Referral/patient
- Task type
- Why it needs attention
- Owner
- Consequence or next step
- Link/open action into Workbench

Do not build this as a full approval workspace for MVP. The detailed decision should happen in Workbench.

### System health strip

Show only operational health, not developer detail.

Suggested health items:

- Email capture: OK / Manual / Failed
- Outbound email: Ready / Simulated / Not configured
- Calendar: Connected / Manual availability / Conflict detected
- Models: OK / Degraded / Offline
- Workflow runner: OK / Failed jobs
- Last sync / refresh time

Clicking a health item can route to Integrations or System / Agents.

## Design intent

The Overview should answer:

- How much work is in the system?
- Where are referrals stuck?
- What needs attention now?
- Is anything clinically or operationally risky?
- Are the supporting systems healthy?

---

# 2. Workbench

## Purpose

The Workbench is the main page for processing one referral from capture to `first_session_ready`.

Rename the current `Referrals` page to `Workbench`.

Remove the referral queue from this page. The Workbench should focus only on the selected referral.

## Contents

### Referral header

At the top, show a strong case header:

- Patient/referral name
- Current primary stage
- Current blocker
- Owner
- Next action
- Risk status
- Open human gates
- Link/open in new tab if useful

The header should make the current state obvious without relying on dense status chips.

### Journey progress

Show a horizontal or compact progress path:

- Captured
- Reviewed
- Matched
- Contacted
- Appointment confirmed
- Intake complete
- Prep brief ready

Each step should show completed/current/blocked state.

### Extracted information

Show structured referral information:

- Patient name
- Email
- Phone
- DOB
- Source channel
- Insurer
- Location
- Language
- Modality
- Presenting concern
- Availability
- Missing or uncertain fields

Use clear labels and edit actions. Avoid overwhelming the user with raw implementation tags.

### Blockers and review tasks

Show active blockers and human gates related to this referral:

- Missing information
- Duplicate candidate
- Clinical risk review
- Suitability review
- Match approval
- Slot approval
- Send approval
- Appointment confirmation approval
- Intake exception approval

Each task should make clear:

- Why it exists
- What action is needed
- What happens after approval/rejection/request changes/escalation

### Communication thread

Add referral-specific communication inside the Workbench.

This should show:

- Patient messages
- Admin messages
- Agent-drafted messages
- Drafts awaiting approval
- Sent or simulated messages
- Patient replies
- Agent interpretation of replies
- Admin confirmation actions

Do not create a separate email client page for MVP.

### Therapist matching

Show:

- Recommended therapist
- Alternatives
- Excluded therapists with reasons
- Matching rationale
- Capacity/availability summary
- Approval status
- Match approval action

### Scheduling

Show referral-specific scheduling:

- Proposed slots
- Slot approval status
- Patient reply / selected slot
- Appointment confirmation
- Manual booking fallback
- Calendar conflict warnings

Scheduling data comes from therapist availability/manual bookings for MVP, not real therapist Gmail accounts.

### Documentation and intake

Add a client/referral documentation section.

It should include:

- Intake checklist
- Consent records
- Uploaded files
- Questionnaires
- Waivers/exceptions
- Intake reminders
- Prep brief preview
- Communication drafts
- Future session documents placeholder if useful

The admin workflow cannot reach `first_session_ready` unless required intake is complete or waived with authorisation.

### Agent activity and audit timeline

Show a clear timeline of what has happened:

- Agent actions
- Human decisions
- Status transitions
- Drafts created
- Approvals/rejections
- Request changes outcomes
- Escalations
- Errors or blocked transitions

This should make the agentic work visible without exposing raw developer traces.

## Design intent

The Workbench should answer:

- What is the exact state of this referral?
- What is blocking progress?
- What should the admin do next?
- What has Lumen prepared?
- What has been approved or rejected?
- What evidence/history supports the current state?

---

# 3. New Referral

## Purpose

The New Referral page is for creating, importing, or demo-running a new referral.

This should replace the current global `View trace` / workflow runner prominence.

It should be a sidebar page, not a top-right global action.

## Contents

### Manual referral entry

Include fields for:

- Source channel
- Raw referral text
- Optional tenant/patient/referral identifiers
- Optional uploaded referral text/file

### Demo examples

Include sample buttons such as:

- Standard referral
- Clinical review referral
- Incomplete referral

These are useful for class/demo workflows.

### Run referral workflow

Allow the user to submit the referral and run the initial intake/normalisation workflow.

### Initial result

After submission, show:

- Referral ID
- Captured status
- Initial stage
- Next action
- Missing items
- Risk level
- Link to open in Workbench

### Advanced trace

Keep technical trace output, payload handoffs, and JSON export behind an expandable `Advanced trace` section.

Do not make trace the dominant UI.

## Design intent

This page should answer:

- How do I add a new referral?
- What happened immediately after submission?
- Where do I go to process it?

---

# 4. Therapists

## Purpose

The Therapists page should become a network capacity and matching dashboard.

It should manage therapist data used for matching, scheduling, and assignment visibility.

Do not use raw JSON as the main availability interface.

Do not create Gmail accounts for each therapist for the MVP. Use manual/mock availability and manually recorded appointments.

## Contents

### Top metrics

Show:

- Active therapists
- Available capacity this week
- Fully booked therapists
- Therapists missing availability
- Therapists with incomplete matching data

### Therapist list

Use searchable/filterable therapist cards.

Each card should show:

- Name
- Active/inactive
- Specialties
- Languages
- Modalities
- Weekly capacity
- Current assigned count
- Next available slot
- Matching availability status

Clicking a therapist selects them.

### Selected therapist profile

Show editable details:

- Name
- Email
- Active/inactive
- Location
- Licence/metadata if useful

### Matching criteria

Show and allow editing of:

- Specialties
- Age groups
- Languages
- Modalities
- Insurers
- Exclusions or unsuitable case types

### Capacity

Show:

- Weekly capacity
- Currently assigned patients/referrals
- Remaining capacity
- Capacity utilisation indicator

### Availability grid

Use a readable weekly grid:

- Monday: 09:00–12:00 online; 14:00–17:00 in person
- Tuesday: unavailable
- Wednesday: 10:00–13:00 online

Show timezone if relevant.

### Manual bookings / blocked time

Show simple manually recorded appointments or unavailable periods.

These should be subtracted from slot proposal logic.

### Assigned patients/referrals

Add a table showing patients/referrals assigned or being assigned to each therapist.

Suggested columns:

- Patient/referral name
- Referral ID
- Current stage
- First session date/time
- Intake status
- Assigned date
- Action/menu

This is important so admins can see workload distribution.

### Recent matching history

Show:

- Recommended for X referrals
- Approved for X
- Overridden/declined for X
- Common reason for override if available

## Design intent

The Therapists page should answer:

- Can this therapist take another patient?
- When are they available?
- What cases are they suited for?
- Who is already assigned to them?
- Why would Lumen recommend or exclude them?

---

# 5. Integrations

## Purpose

Keep Integrations mostly unchanged for now.

It should manage referral import and external channel readiness, not individual referral communications.

## Current scope

Keep or lightly tidy:

- CSV/XLSX/manual batch import
- Import status/history
- Row-level import errors
- Email capture status
- Outbound email status
- Calendar/manual availability status
- Integration health cards

## Important boundary

Referral-specific communication belongs in Workbench, not Integrations.

Detailed system diagnostics belong in System / Agents.

No major mockup-driven redesign is required for this page in the current pass.

---

# 6. System / Agents

## Purpose

Rename `System (dev)` to `System / Agents` or `Agent Control`.

This page should configure, test, and monitor the agentic system.

It should not be part of everyday admin referral processing.

## Contents

### Agent registry

Show cards for each core agent:

- Referral Intake Normalizer
- Completeness Extractor
- Risk Reviewer
- Therapist Matching Planner
- Communication Drafter
- Intake Collector
- Prep Brief Generator

Each card should show:

- Enabled/disabled
- Assigned model
- Last run
- Health
- Success rate
- Error count

### Model health

Keep model health here.

Show:

- Small model
- Medium model
- Communication model
- Provider
- Latency
- Status
- Last checked

### Workflow runs

Show recent diagnostic traces:

- Workflow/run ID
- Referral/patient
- Started time
- Status
- Failed node if applicable
- Retry action
- Export JSON action

### Agent test bench

Allow synthetic tests:

- Test single agent
- Run full demo workflow
- Use sample payloads

Label this clearly as demo/developer functionality.

### Guardrails and thresholds

Display or edit basic thresholds:

- Risk escalation threshold
- Extraction confidence threshold
- Matching confidence threshold
- Send approval required
- Booking approval required
- Intake completion gate
- Unknown/elevated risk gate

### Audit/debug logs

Show:

- Failed jobs
- Blocked transitions
- Schema failures
- Model errors
- Timeouts
- Recent critical logs

## Design intent

System / Agents should answer:

- Are the agents working?
- Which model is assigned to each agent?
- What failed and where?
- Which thresholds/guardrails are active?
- Can a developer safely test the workflow?

---

# Removed / Absorbed Pages

## Review Inbox

Remove as a standalone MVP page.

Its cross-referral task visibility should move to Overview under `Needs attention`.

Its detailed approval/decision actions should move into Workbench, where the referral context is visible.

A separate approvals page can be reintroduced later if task volume becomes high.

## Intake & Scheduling

Remove as a standalone MVP page.

Split its functions:

- Therapist availability and capacity -> Therapists
- Referral-specific slot proposals, appointment confirmation, intake checklist, waivers, documents, prep brief -> Workbench
- Cross-referral intake/scheduling blockers -> Overview

## Global View Trace

Remove the top-right global `View trace` button.

Trace belongs in:

- New Referral, immediately after running a referral
- System / Agents, for diagnostics and workflow debugging

---

# Implementation Rule

Use the mockups to guide UX and layout, but prioritise:

- Clear referral state
- Clear next action
- Explicit human gates
- Visible agent activity
- Minimal safe frontend changes
- Preservation of existing backend functionality
- Reusable components
- No broad rewrite unless unavoidable

The goal is not to make the app visually perfect. The goal is to make the admin workflow understandable, trustworthy, and easy to operate.