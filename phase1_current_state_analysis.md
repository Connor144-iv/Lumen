# Current-State Analysis: Therapy Clinics in Lisbon

## Purpose

This document provides a grounded “before” picture of the operational problem area for a small private therapy clinic in Lisbon. It focuses only on the current state: the business context, existing manual workflow, and key pain points. It does **not** propose any solution or describe future AI/automation functionality.

---

## 1. Semi-Fictional Business & Problem Area

### 1.1 Business Profile

**Business name:** Clínica Tejo Psicologia  
**Location:** Lisbon, Portugal  
**Type:** Small private outpatient mental-health clinic  
**Size:** 6 clinicians, 1 administrative assistant, 1 clinic director who also sees patients  
**Patient groups:** Adults, adolescents, corporate/EAP-referred patients, insurer-reimbursed patients, and self-pay clients  

Clínica Tejo Psicologia is a small private psychology practice operating in Lisbon. It provides outpatient therapy and psychological assessment services. Its clinicians offer treatment across common areas such as anxiety, depression, trauma, ADHD assessment, adolescent mental health, couples therapy, and work-related stress.

The clinic’s revenue comes from a mixture of:

- Self-pay therapy sessions
- Insurance-reimbursed sessions
- Employee Assistance Programme referrals
- Formal psychological assessments and written reports

The clinic is clinically competent and has strong patient demand, but its operating model is not built for scale. Most coordination work is handled manually by one administrator, with therapists often stepping in to manage referrals, scheduling, intake review, documentation, and reporting.

### 1.2 Process in Focus

The process being analysed is the **end-to-end patient administration and clinical documentation workflow**, from the moment a referral or patient enquiry arrives through to intake, scheduling, session documentation, reporting, billing support, and discharge.

The focus is not the therapy itself. The focus is the surrounding operational workflow that enables therapy to happen safely, efficiently, and compliantly.

### 1.3 Overarching Business Problem

Clínica Tejo faces rising demand but lacks the operational infrastructure to manage that demand efficiently. Patient information arrives through multiple unconnected channels, including email, phone, WhatsApp, insurer forms, EAP Excel files, and online booking platforms. Clinical documentation is created manually after sessions, often from memory or brief notes, and formal reports can take hours to complete.

The result is a clinic where clinical expertise exists, demand exists, and revenue opportunity exists, but operational capacity is constrained by:

- Fragmented intake channels
- Manual referral processing
- Repetitive scheduling coordination
- Inconsistent pre-session information collection
- Heavy post-session documentation workload
- Weak longitudinal visibility across patient history
- Sensitive patient data spread across disconnected tools

This creates a realistic business problem: the clinic is not primarily limited by patient demand. It is limited by administrative throughput, documentation burden, and the reliability of its operating processes.

---

## 2. End-to-End Existing Workflow — As-Is State

### 2.1 Workflow Summary

The current workflow can be divided into two major phases:

1. **Pre-session workflow:** referral intake, information checking, triage, matching, first contact, scheduling, and pre-session intake.
2. **Post-session workflow:** session notes, clinical documentation, assessment scoring, report drafting, treatment review, insurance support, and discharge.

Both phases rely heavily on human coordination, manual copying of information, and informal knowledge held by the administrator or therapists.

---

### 2.2 Detailed As-Is Workflow

| Step | Process Name | Human Actor / Role | Tools / Systems Used | Time / Effort Estimate |
|---:|---|---|---|---|
| 1 | Referral or enquiry arrives | Admin assistant, therapist, clinic director | Gmail/Outlook, phone voicemail, WhatsApp, Doctoralia, insurer email, EAP Excel files | 5–20 minutes per referral to notice, open, and understand |
| 2 | Referral consolidation | Admin assistant | Excel tracker, shared Google Sheet, inbox folders, copied WhatsApp notes | 1–2 hours per week; Monday EAP batches can take 2–4 hours |
| 3 | Minimum information check | Admin assistant or therapist | Email thread, referral PDF, phone notes, WhatsApp audio/images, patient file folder | 5–15 minutes per patient; longer if key fields are missing |
| 4 | Urgency and suitability review | Therapist or clinic director | Referral text, informal clinical judgement, phone call with referrer if needed | 5–20 minutes per referral; urgent cases interrupt clinical work |
| 5 | Therapist matching | Admin assistant, clinic director, relevant therapist | Shared calendar, therapist capacity tracker, memory of specialties | 5–15 minutes per referral |
| 6 | First patient contact | Admin assistant or therapist | Phone, email templates, WhatsApp, manual copy/paste | 10–25 minutes per patient |
| 7 | Scheduling and rescheduling | Admin assistant, therapist, patient | Google Calendar, Doctoralia, email, WhatsApp, phone | 10–30 minutes per booked appointment |
| 8 | Pre-session intake | Patient, admin assistant, therapist | PDF forms, paper forms, Google Forms, email attachments, scanned documents | 10–20 minutes admin handling; 15–30 minutes therapist review or in-session collection |
| 9 | First session preparation | Therapist | Intake forms, referral documents, previous emails, notes in folder | 10–20 minutes before first session; often compressed or skipped |
| 10 | Therapy session | Therapist | Paper notebook, typed notes, EHR-lite tool, sometimes no structured tool | 50–60 minutes per session |
| 11 | Post-session note writing | Therapist | Word/Google Docs, EHR notes field, handwritten notes later typed | 10–30 minutes per session |
| 12 | Assessment scoring and protocol checking | Therapist | PHQ-9, GAD-7, Conners/Brown scales, PDFs, spreadsheets, protocol documents | 10–45 minutes depending on assessment complexity |
| 13 | Formal report drafting | Therapist | Word templates, previous reports, copied sections, scoring rubrics, PDFs | 1–4 hours for structured assessments; 15–45 minutes for routine summaries |
| 14 | Treatment plan review | Therapist, sometimes supervisor | Prior session notes, score history, calendar history, informal memory | 20–60 minutes every few sessions |
| 15 | Insurance/EAP billing support | Admin assistant, therapist | Insurer portals, Excel claims sheets, email, session attendance records | 10–30 minutes per claim batch; longer if evidence is missing |
| 16 | Discharge and follow-up | Therapist, admin assistant | Word discharge summary, email to referrer/GP/EAP, calendar reminders | 20–60 minutes; often incomplete or inconsistent |

---

### 2.3 Narrative Walkthrough of the Existing Process

#### Step 1–2: Referral Arrival and Consolidation

New patient demand reaches the clinic through several channels. Some patients contact the clinic directly by email or phone. Others arrive through insurer emails, EAP Excel files, WhatsApp messages, informal GP referrals, or online directories.

The admin assistant checks these channels throughout the day and manually transfers relevant details into a spreadsheet or shared tracker. If a therapist receives a message directly, they may forward it to the administrator or handle it themselves. This creates a fragmented intake environment where there is no single source of truth.

#### Step 3–5: Information Check, Triage, and Matching

Once a referral is noticed, the clinic checks whether it has enough information to proceed. Common missing fields include patient date of birth, contact information, preferred language, insurer details, presenting concern, availability, and urgency indicators.

The therapist or clinic director may review the referral to determine whether the case appears urgent, whether the clinic is appropriate, and which therapist is the best fit. This judgement relies heavily on memory, informal knowledge of therapist specialisms, and manual calendar checking.

#### Step 6–7: First Contact and Scheduling

The clinic contacts the patient by phone, email, or WhatsApp. Staff often write a new message manually or adapt an old email. Scheduling usually requires several rounds of back-and-forth because availability, treatment modality, therapist fit, and patient preference must all align.

No-shows and rescheduling generate additional manual work. Reminders may be sent inconsistently depending on the administrator’s workload and the therapist’s habits.

#### Step 8–9: Pre-Session Intake and Preparation

Before the first session, the patient may be asked to complete intake forms or send relevant documents. These forms may be paper-based, PDF-based, or sent by email. In many cases, intake information is incomplete or only gathered verbally during the first session.

The therapist reviews available information before the session where time allows. Under high workload, this preparation may be compressed, incomplete, or done immediately before the appointment.

#### Step 10–13: Session Delivery and Documentation

During the session, the therapist may take handwritten or typed notes. After the session, they must convert those notes into a structured clinical record. If the patient is undergoing assessment, the therapist may also need to score questionnaires, compare the session content with an assessment protocol, and write a formal report.

This is one of the most time-consuming parts of the workflow. Reports often require repeated formatting, copying from templates, checking against scoring rules, and ensuring that clinical claims are supported by session material or test results.

#### Step 14–16: Treatment Review, Billing, Discharge, and Follow-Up

Across multiple sessions, the therapist reviews whether the patient is progressing and whether the treatment plan should change. This requires looking back through previous notes, screening scores, clinical observations, and patient goals.

For insurer or EAP-covered patients, the clinic may also need to prepare claims evidence, attach reports, submit attendance confirmations, or answer insurer queries. At discharge, the therapist may prepare a summary for the patient, GP, EAP, or referrer, although this is often delayed or skipped when workload is high.

---

## 3. Key Pain Points

### Pain Point 1: Fragmented Referral Intake Creates Lost Patients and Lost Revenue

**Where it occurs:** Steps 1–6  
**Affected roles:** Admin assistant, therapists, clinic director  
**Main tools involved:** Email, phone, WhatsApp, Excel, Doctoralia, insurer/EAP files  

Referrals arrive through too many disconnected channels. A single week may include direct patient emails, voicemail messages, WhatsApp audio notes, insurer authorization emails, EAP spreadsheets, and informal referrals from GPs or other clinicians.

Because these channels are not unified, the clinic does not have a reliable single queue of incoming demand. The administrator must manually check, interpret, and copy referral details into a spreadsheet or patient tracker. If a therapist receives a referral directly, it may remain in their inbox or phone until they remember to forward it.

This creates several business risks:

- Referrals are missed or answered late.
- Patients contact another provider before the clinic responds.
- EAP or insurer batches accumulate without timely action.
- Therapists waste time clarifying missing information.
- The clinic loses revenue despite having available clinical capacity.

The problem is especially serious for small clinics because each new referral may represent a long-term patient relationship, not just a single appointment.

---

### Pain Point 2: Clinicians Spend High-Value Clinical Time on Low-Value Administrative Work

**Where it occurs:** Steps 3–7 and 11–16  
**Affected roles:** Therapists, clinic director, admin assistant  
**Main tools involved:** Email, calendar, Word/Google Docs, spreadsheets, patient records, insurer portals  

Therapists are frequently pulled into tasks that do not require their full clinical expertise. These include checking referral completeness, drafting first-contact messages, clarifying insurer information, coordinating appointment times, writing routine summaries, formatting reports, and preparing billing evidence.

This creates a poor allocation of labour. The clinic’s most expensive and scarce resource is clinician time, but much of that time is spent on coordination and documentation rather than patient care.

The consequences include:

- Fewer available clinical hours
- Longer working days for therapists
- Delayed reports and treatment plans
- Increased risk of burnout
- Reduced ability to absorb new demand
- Lower revenue per clinician than the clinic could otherwise support

The clinic does not lack demand. It lacks operational leverage around clinicians’ time.

---

### Pain Point 3: Clinical Documentation Is Slow, Inconsistent, and Often Delayed

**Where it occurs:** Steps 10–14  
**Affected roles:** Therapists, supervisors, clinic director  
**Main tools involved:** Paper notes, Word/Google Docs, EHR-lite tools, PDFs, scoring templates, prior reports  

Clinical documentation is one of the heaviest burdens in the current workflow. After each session, the therapist must write notes, structure the clinical record, update the treatment plan where needed, score assessment tools, and sometimes produce a formal report.

The work is repetitive but sensitive. Notes and reports need to be clinically accurate, legally defensible, and consistent with the therapist’s protocol. However, each therapist may use different templates, language, note structures, and assessment habits.

This produces several operational issues:

- Notes are written late, sometimes from memory.
- Report quality varies across therapists.
- Formal assessments take days or weeks to complete.
- Treatment plan reviews are harder because historical notes are not standardized.
- Missing information may only be discovered when writing the report.
- Patient care decisions can be delayed by documentation backlog.

The workflow depends heavily on the discipline and availability of individual therapists, rather than on a reliable clinic-wide process.

---

### Pain Point 4: Triage, Matching, and Risk Review Rely on Informal Judgement and Memory

**Where it occurs:** Steps 4–5 and 11–14  
**Affected roles:** Therapists, clinic director, admin assistant  
**Main tools involved:** Referral text, therapist memory, shared calendars, handwritten notes, prior records  

The clinic must decide whether a patient is urgent, whether the clinic is suitable, and which therapist is the best fit. In the current workflow, this is mostly handled through informal review.

Matching may depend on:

- Therapist availability
- Clinical specialty
- Patient age group
- Preferred language
- Modality preference
- Insurance compatibility
- Whether the case appears urgent
- Whether the patient needs a higher level of care

Because this logic is not consistently structured, errors can happen. A patient may be assigned to a therapist who is not the best fit. A risk signal may be hidden inside a voicemail, informal note, or long email. A mismatch may only become visible during or after the first session.

This creates both operational and clinical risks:

- Therapist capacity becomes uneven.
- Patients may need to be reassigned.
- First sessions may be used to correct intake mistakes.
- Urgent cases may not be escalated quickly enough.
- The clinic has limited auditability over why a patient was routed in a certain way.

The problem is not that therapists lack judgement. The problem is that the process around that judgement is inconsistent and difficult to scale.

---

### Pain Point 5: Sensitive Health Data Is Spread Across Weakly Governed Systems

**Where it occurs:** Steps 1–3, 8, 11–13, and 15–16  
**Affected roles:** Admin assistant, therapists, clinic director  
**Main tools involved:** Email, WhatsApp, spreadsheets, PDFs, local folders, shared drives, insurer portals  

The clinic handles sensitive health information across many everyday tools. Referral details may sit in email inboxes. Patient concerns may be described in WhatsApp messages. Intake forms may be attached as PDFs. Session notes may be stored in Word documents or local folders. Billing evidence may be sent through insurer portals or email threads.

This fragmented data handling creates governance problems:

- It is hard to know where all patient data is stored.
- Access control is inconsistent.
- Deletion and retention are difficult to manage.
- Audit trails are incomplete.
- Sensitive information may be duplicated across inboxes, downloads, and spreadsheets.
- Staff may rely on informal workarounds during busy periods.

This is particularly serious because therapy data is health data. The clinic must protect confidentiality, maintain appropriate records, and handle patient information in a way that is compliant with professional and legal obligations.

The current workflow makes compliance harder because the data architecture is not intentional. It has grown organically from whichever tools were convenient at the time.

---

## 4. Current-State Diagnosis

Clínica Tejo Psicologia is not failing because the clinical service is weak. It is constrained because its operating model has not kept pace with demand.

The current state is characterized by:

- High patient demand
- Limited administrative capacity
- Fragmented referral channels
- Manual scheduling coordination
- Inconsistent intake completeness
- Heavy therapist documentation workload
- Informal triage and matching processes
- Sensitive health data spread across disconnected tools

The result is a clinic that depends on individual effort and memory rather than reliable workflow infrastructure. The highest-value professionals in the business spend substantial time on work that is necessary but operationally repetitive. This reduces clinical capacity, slows patient onboarding, delays reports, increases compliance risk, and limits the clinic’s ability to grow.

The clearest operational bottleneck is the combined burden of **referral intake before the first session** and **clinical documentation after the session**. These two areas consume time, create risk, and directly affect revenue, patient experience, and clinician workload.
