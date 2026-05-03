const routes = {
  "/": { page: "overview", title: "Overview", label: "Clinic operations" },
  "/overview": { page: "overview", title: "Overview", label: "Clinic operations" },
  "/workflows": { page: "workflows", title: "Workflow trace", label: "Developer demo" },
  "/referrals": { page: "referrals", title: "Referral queue", label: "Admin intake" },
  "/review": { page: "review", title: "Review inbox", label: "Governance" },
  "/therapists": { page: "therapists", title: "Therapists", label: "Network" },
  "/intake": { page: "intake", title: "Intake", label: "Pre-session" },
  "/clinical": { page: "clinical", title: "Clinical", label: "Documentation" },
  "/integrations": { page: "integrations", title: "Integrations", label: "Channels" },
  "/system": { page: "system", title: "System", label: "Runtime" },
};

const form = document.querySelector("#workflow-form");
const runButton = document.querySelector("#run-button");
const resetButton = document.querySelector("#reset-button");
const exportButton = document.querySelector("#export-button");
const refreshHealthButton = document.querySelector("#refresh-health-button");
const refreshProductButton = document.querySelector("#refresh-product-button");
const statusStrip = document.querySelector("#status-strip");
const monitorTitle = document.querySelector("#monitor-title");
const jobIdLabel = document.querySelector("#job-id-label");
const timeline = document.querySelector("#timeline");
const handoffs = document.querySelector("#handoffs");
const finalOutput = document.querySelector("#final-output");
const resultJson = document.querySelector("#result-json");
const exampleButtons = document.querySelector("#example-buttons");
const modelHealthList = document.querySelector("#model-health-list");
const referralList = document.querySelector("#referral-list");
const referralFilterForm = document.querySelector("#referral-filter-form");
const referralDetail = document.querySelector("#referral-detail");
const reviewTaskList = document.querySelector("#review-task-list");
const therapistList = document.querySelector("#therapist-list");
const therapistForm = document.querySelector("#therapist-form");
const intakeWorkspace = document.querySelector("#intake-workspace");
const intakeTrackerList = document.querySelector("#intake-tracker-list");
const clinicalWorkspace = document.querySelector("#clinical-workspace");
const sessionNoteForm = document.querySelector("#session-note-form");
const reportDraftForm = document.querySelector("#report-draft-form");
const clinicalLibraryForm = document.querySelector("#clinical-library-form");
const clinicalLibraryList = document.querySelector("#clinical-library-list");
const referralImportForm = document.querySelector("#referral-import-form");
const importBatchList = document.querySelector("#import-batch-list");
const integrationHealthList = document.querySelector("#integration-health-list");
const securityPostureList = document.querySelector("#security-posture-list");
const feedbackMetricsList = document.querySelector("#feedback-metrics-list");
const pageTitle = document.querySelector("#page-title");
const sectionLabel = document.querySelector("#section-label");
const recentWorkflowList = document.querySelector("#recent-workflow-list");
const overviewReferralList = document.querySelector("#overview-referral-list");
const overviewReviewList = document.querySelector("#overview-review-list");
const metricReferrals = document.querySelector("#metric-referrals");
const metricReviews = document.querySelector("#metric-reviews");
const metricTherapists = document.querySelector("#metric-therapists");
const metricModels = document.querySelector("#metric-models");
const metricWorkflows = document.querySelector("#metric-workflows");

let activeSource = null;
let pollTimer = null;
let currentResult = null;
let latestReferrals = [];
let latestReviewTasks = [];
let latestTherapists = [];
let latestWorkflows = [];
let selectedReferralId = null;

document.querySelectorAll("[data-nav]").forEach((link) => {
  link.addEventListener("click", (event) => {
    const url = new URL(link.href, window.location.origin);
    if (url.origin !== window.location.origin) return;
    event.preventDefault();
    navigate(url.pathname);
  });
});

window.addEventListener("popstate", () => applyRoute(window.location.pathname));

form.addEventListener("change", (event) => {
  if (event.target.name === "workflow_type") {
    updateWorkflowSections(event.target.value);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resetRunState();
  navigate("/workflows");
  runButton.disabled = true;
  setStatus("queued", "Submitting workflow");

  try {
    const request = buildRequest();
    const response = await fetch("/api/run-workflow", request);
    const body = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(body.detail || "Unable to start workflow.");
    }
    jobIdLabel.textContent = body.job_id;
    openEventStream(body.job_id, body.events_url);
    startPolling(body.status_url);
    await refreshProductWorkspace();
  } catch (error) {
    setStatus("failed", "Failed to start workflow");
    appendTimelineEvent({
      type: "error",
      status: "failed",
      node: "client",
      message: friendlyClientError(error),
      tools: [],
    });
    runButton.disabled = false;
  }
});

resetButton.addEventListener("click", () => {
  form.reset();
  updateWorkflowSections("new_referral");
  resetRunState();
  setStatus("idle", "Idle");
});

exportButton.addEventListener("click", () => {
  if (!currentResult) return;
  const blob = new Blob([JSON.stringify(currentResult, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `lumen-workflow-${currentResult.job_id || "result"}.json`;
  link.click();
  URL.revokeObjectURL(url);
});

refreshHealthButton.addEventListener("click", () => loadModelHealth());
refreshProductButton.addEventListener("click", () => refreshProductWorkspace());

referralFilterForm.addEventListener("change", () => renderReferralLists());

therapistForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(therapistForm);
  const payload = Object.fromEntries(formData.entries());
  payload.specialties = csvList(payload.specialties);
  payload.languages = csvList(payload.languages);
  payload.modalities = csvList(payload.modalities);
  payload.insurers = csvList(payload.insurers);
  payload.age_groups = ["adult"];
  payload.capacity_per_week = Number(payload.capacity_per_week || 0);
  try {
    payload.availability_blocks = payload.availability_blocks ? JSON.parse(payload.availability_blocks) : [];
  } catch {
    payload.availability_blocks = [];
  }
  const response = await fetch("/api/therapists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Therapist could not be saved.");
    return;
  }
  therapistForm.reset();
  await loadTherapists();
});

sessionNoteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(sessionNoteForm);
  const payload = Object.fromEntries(formData.entries());
  const referralId = String(payload.referral_id || "").trim();
  delete payload.referral_id;
  payload.therapist_id = String(payload.therapist_id || "").trim() || null;
  payload.title = String(payload.title || "Session note").trim() || "Session note";
  const response = await fetch(`/api/referrals/${encodeURIComponent(referralId)}/session-notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Session note could not be saved.");
    return;
  }
  sessionNoteForm.reset();
  await loadPatientWorkspace(body.session_note.patient_id);
});

clinicalLibraryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(clinicalLibraryForm);
  const payload = Object.fromEntries(formData.entries());
  payload.metadata = {};
  const response = await fetch("/api/clinical-library", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Clinical source could not be saved.");
    return;
  }
  clinicalLibraryForm.reset();
  await loadClinicalLibrary();
});

reportDraftForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(reportDraftForm);
  const payload = Object.fromEntries(formData.entries());
  const referralId = String(payload.referral_id || "").trim();
  delete payload.referral_id;
  const response = await fetch(`/api/referrals/${encodeURIComponent(referralId)}/reports/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Report draft could not be created.");
    return;
  }
  reportDraftForm.reset();
  await loadPatientWorkspace(body.report_draft.patient_id);
});

referralImportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(referralImportForm);
  const response = await fetch("/api/integrations/referral-batches", {
    method: "POST",
    body: formData,
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Referral import failed.");
    return;
  }
  referralImportForm.reset();
  await Promise.all([loadImportBatches(), loadReferrals()]);
});

function navigate(path) {
  const route = routes[path] || routes["/"];
  const nextPath = path === "/overview" ? "/" : path;
  if (window.location.pathname !== nextPath) {
    window.history.pushState({}, "", nextPath);
  }
  applyRoute(route === routes["/"] ? nextPath : path);
}

function applyRoute(path) {
  const route = routes[path] || routes["/"];
  document.querySelectorAll("[data-page]").forEach((page) => {
    page.classList.toggle("is-active", page.dataset.page === route.page);
  });
  document.querySelectorAll("[data-page-link]").forEach((link) => {
    link.classList.toggle("is-active", link.dataset.pageLink === route.page);
  });
  pageTitle.textContent = route.title;
  sectionLabel.textContent = route.label;
  document.title = `Lumen | ${route.title}`;
}

function updateWorkflowSections(workflowType) {
  document.querySelectorAll("[data-workflow-section]").forEach((section) => {
    section.hidden = section.dataset.workflowSection !== workflowType;
  });
}

function buildRequest() {
  const formData = new FormData(form);
  const file = formData.get("file");
  const hasFile = file && file.name;

  if (hasFile) {
    return { method: "POST", body: formData };
  }

  const payload = Object.fromEntries(formData.entries());
  delete payload.file;
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

function openEventStream(jobId, eventsUrl) {
  closeEventStream();
  activeSource = new EventSource(eventsUrl);
  const eventTypes = ["workflow", "agent", "handoff", "human_review", "error", "complete"];

  eventTypes.forEach((eventType) => {
    activeSource.addEventListener(eventType, (message) => {
      const event = JSON.parse(message.data);
      handleWorkflowEvent(event, jobId);
    });
  });

  activeSource.addEventListener("heartbeat", (message) => {
    const event = JSON.parse(message.data);
    setStatus(event.status, statusLabel(event.status));
  });

  activeSource.onerror = () => {
    closeEventStream();
    appendTimelineEvent({
      type: "workflow",
      status: "running",
      node: "sse",
      message: "Live stream disconnected; status polling is still active.",
      tools: [],
    });
  };
}

function handleWorkflowEvent(event, jobId) {
  if (event.type !== "complete" || event.status === "failed") {
    appendTimelineEvent(event);
  }
  if (event.type === "handoff" || event.type === "human_review" || event.type === "error") {
    appendHandoff(event);
  }
  setStatus(event.status, statusLabel(event.status));

  const terminalEvent = event.type === "complete" || (event.type === "error" && event.node === "workflow");
  if (terminalEvent) {
    closeEventStream();
    fetchStatus(`/api/status/${jobId}`);
    runButton.disabled = false;
  }
}

function startPolling(statusUrl) {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => fetchStatus(statusUrl), 2000);
}

async function fetchStatus(statusUrl) {
  const response = await fetch(statusUrl);
  if (!response.ok) return;
  const job = await readResponseBody(response);
  setStatus(job.status, statusLabel(job.status));
  if (["completed", "needs_review", "failed"].includes(job.status)) {
    clearInterval(pollTimer);
    pollTimer = null;
    renderFinalResult(job);
    runButton.disabled = false;
    await refreshProductWorkspace();
    if (job.referral_id) {
      await loadReferralDetail(job.referral_id);
    }
  }
}

function appendTimelineEvent(event) {
  if (event.type === "heartbeat") return;
  const item = document.createElement("li");
  item.className = "event";
  item.dataset.status = event.status || "running";

  const title = document.createElement("div");
  title.className = "event-title";
  const node = document.createElement("span");
  node.textContent = event.agent || event.node || event.type;
  const type = document.createElement("span");
  type.className = "pill";
  type.textContent = event.type || "event";
  title.append(node, type);

  const message = document.createElement("p");
  message.className = "event-message";
  message.textContent = event.message || "Step updated.";

  const meta = document.createElement("div");
  meta.className = "meta-row";
  if (event.node) meta.appendChild(pill(event.node));
  if (typeof event.confidence === "number") meta.appendChild(pill(`confidence ${Math.round(event.confidence * 100)}%`));
  (event.tools || []).forEach((tool) => meta.appendChild(pill(tool)));

  item.append(title, message, meta);
  timeline.appendChild(item);
  item.scrollIntoView({ block: "nearest" });
}

function appendHandoff(event) {
  const detail = document.createElement("details");
  detail.className = "handoff";
  if (event.type === "error" || event.type === "human_review") detail.open = true;

  const summary = document.createElement("summary");
  summary.textContent = `${event.node || event.type}: ${event.message || "Updated"}`;

  const payload = document.createElement("pre");
  payload.textContent = JSON.stringify(event.payload || {}, null, 2);

  detail.append(summary, payload);
  handoffs.prepend(detail);
}

function renderFinalResult(job) {
  currentResult = job;
  exportButton.disabled = false;
  finalOutput.hidden = false;
  resultJson.textContent = JSON.stringify(job.result || { error: job.error }, null, 2);
}

async function loadExamples() {
  const response = await fetch("/api/examples");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  exampleButtons.replaceChildren();
  data.examples.forEach((example) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary compact";
    button.textContent = example.name;
    button.addEventListener("click", () => applyExample(example.payload));
    exampleButtons.appendChild(button);
  });
}

function applyExample(payload) {
  navigate("/workflows");
  form.reset();
  const workflow = payload.workflow_type || "new_referral";
  const workflowInput = form.querySelector(`[name="workflow_type"][value="${workflow}"]`);
  if (workflowInput) workflowInput.checked = true;
  updateWorkflowSections(workflow);

  Object.entries(payload).forEach(([key, value]) => {
    const input = form.elements.namedItem(key);
    if (!input || key === "workflow_type") return;
    input.value = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  });
}

async function loadModelHealth() {
  modelHealthList.textContent = "Checking models...";
  metricModels.textContent = "-";
  try {
    const response = await fetch("/api/health/models");
    const data = await readResponseBody(response);
    if (!response.ok) throw new Error(data.detail || "Model health check failed.");
    const checks = data.checks || [];
    const available = checks.filter((check) => check.status === "ok" || check.status === "configured").length;
    metricModels.textContent = `${available}/${checks.length}`;
    modelHealthList.replaceChildren(
      ...checks.map((check) =>
        recordItem({
          title: `${check.role}: ${check.model}`,
          status: check.status,
          body: check.message,
          meta: [check.provider, `${check.latency_ms} ms`],
        }),
      ),
    );
  } catch (error) {
    metricModels.textContent = "0";
    modelHealthList.replaceChildren(
      recordItem({
        title: "Model health unavailable",
        status: "failed",
        body: friendlyClientError(error),
        meta: [],
      }),
    );
  }
}

async function refreshProductWorkspace() {
  await Promise.all([
    loadReferrals(),
    loadReviewTasks(),
    loadIntakeTracker(),
    loadTherapists(),
    loadWorkflows(),
    loadClinicalLibrary(),
    loadIntegrationHealth(),
    loadImportBatches(),
    loadSecurityPosture(),
    loadFeedbackMetrics(),
  ]);
}

async function loadWorkflows() {
  const response = await fetch("/api/workflows?limit=8");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  latestWorkflows = data.workflows || [];
  metricWorkflows.textContent = latestWorkflows.length;
  renderCollection(recentWorkflowList, latestWorkflows, "No workflow runs yet.", (workflow) =>
    recordItem({
      title: friendlyWorkflowType(workflow.workflow_type),
      status: workflow.status,
      body: workflow.input_summary || workflow.job_id,
      meta: [shortId(workflow.job_id), formatDate(workflow.created_at)],
    }),
  );
}

async function loadReferrals() {
  const response = await fetch("/api/referrals");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  latestReferrals = data.referrals || [];
  metricReferrals.textContent = latestReferrals.length;

  renderReferralLists();
}

function renderReferralLists() {
  const filtered = filterReferrals(latestReferrals);
  renderCollection(referralList, filtered, "No referrals match these filters.", (referral) => referralCard(referral, false));
  renderCollection(overviewReferralList, latestReferrals.slice(0, 4), "No referrals yet.", (referral) =>
    referralCard(referral, true),
  );
}

function filterReferrals(referrals) {
  const formData = new FormData(referralFilterForm);
  const status = String(formData.get("status") || "");
  const source = String(formData.get("source") || "");
  const flag = String(formData.get("flag") || "");
  const nextAction = String(formData.get("next_action") || "");
  return referrals.filter((referral) => {
    if (status && referral.status !== status) return false;
    if (source && referral.source_channel !== source) return false;
    if (flag && !(referral.secondary_flags || []).includes(flag)) return false;
    if (nextAction && referral.next_action !== nextAction) return false;
    return true;
  });
}

function referralCard(referral, jumpToDetail) {
  const item = recordItem({
    title: referral.patient_name || referral.input_summary || "Unnamed referral",
    status: referral.status,
    body: referral.contact_email || referral.insurer || referral.source_channel,
    meta: [
      referral.status_label || referral.status,
      referral.source_channel,
      referral.risk_category || "risk pending",
      referral.next_action_label,
      formatDate(referral.updated_at),
    ],
  });
  item.classList.add("clickable");
  item.addEventListener("click", async () => {
    if (jumpToDetail) navigate("/referrals");
    await loadReferralDetail(referral.id);
  });
  return item;
}

async function loadReferralDetail(referralId) {
  const response = await fetch(`/api/referrals/${referralId}`);
  if (!response.ok) return;
  const referral = await readResponseBody(response);
  selectedReferralId = referral.id;
  referralDetail.replaceChildren();

  const header = document.createElement("div");
  header.className = "detail-stack";
  const actions = document.createElement("div");
  actions.className = "actions tight";
  actions.append(
    actionButton("Draft missing info", () => draftMissingInfo(referral.id)),
    actionButton("Record missing reply", () => recordMissingInfoReply(referral.id)),
    actionButton("Clinical review", () => requestClinicalReview(referral.id)),
    actionButton("Suitability review", () => requestSuitabilityReview(referral.id)),
    actionButton("Duplicate review", () => requestDuplicateReview(referral.id)),
    actionButton("Run deterministic match", () => runReferralMatch(referral.id)),
    actionButton("Propose slots", () => proposeReferralSlots(referral.id)),
    actionButton("Draft first contact", () => draftFirstContact(referral.id)),
    actionButton("Start intake", () => startReferralIntake(referral.id)),
    actionButton("Draft intake packet", () => draftIntakePacket(referral.id)),
    actionButton("Draft reminder", () => draftIntakeReminder(referral.id)),
    actionButton("Prep brief", () => generateReferralPrepBrief(referral.id)),
  );
  header.append(
    heading(referral.patient_name || "Unnamed referral"),
    metaRow([
      referral.status_label || referral.status,
      referral.next_action_label,
      referral.source_channel,
      referral.urgency || "urgency pending",
      ...(referral.secondary_flags || []),
    ]),
    keyValue("Contact", [referral.contact_email, referral.contact_phone].filter(Boolean).join(" | ") || "Missing"),
    actions,
  );

  const readinessSection = operationSection("First-session readiness");
  renderSimpleList(
    readinessSection.body,
    referral.readiness_blockers || [],
    "Appointment, intake, and prep brief gates are complete.",
    (blocker) => recordItem({ title: blocker, status: "open", body: "Must be resolved before first session readiness.", meta: [] }),
  );

  const fieldsSection = operationSection("Extracted fields");
  fieldsSection.body.append(
    keyValue("Patient", referral.patient_name || "Missing"),
    keyValue("Date of birth", referral.date_of_birth || "Missing"),
    keyValue("Contact", [referral.contact_email, referral.contact_phone].filter(Boolean).join(" | ") || "Missing"),
    keyValue("Insurer", referral.insurer || "Missing"),
    keyValue("Language / modality", [referral.language_preference, referral.modality_preference].filter(Boolean).join(" / ") || "Not recorded"),
    keyValue("Referrer", referral.referring_entity || "Not recorded"),
  );

  const missingSection = operationSection("Missing information");
  renderSimpleList(
    missingSection.body,
    referral.missing_fields || [],
    "No missing fields recorded.",
    (field) => recordItem({ title: field.replaceAll("_", " "), status: "missing", body: "Admin review field", meta: [] }),
  );

  const riskSection = operationSection("Risk and suitability");
  riskSection.body.append(
    recordItem({
      title: referral.risk_category || "Risk pending",
      status: referral.risk_present ? "needs_clinical_review" : referral.risk_category ? "ok" : "open",
      body: referral.risk_present ? "Risk signal requires clinical review before matching." : "No elevated risk recorded in the current referral record.",
      meta: [referral.urgency || "urgency pending"],
    }),
  );

  const taskSection = operationSection("Review tasks");
  renderSimpleList(taskSection.body, referral.review_tasks || [], "No review tasks for this referral.", reviewTaskCard);

  const matchSection = operationSection("Deterministic match");
  renderMatchSummary(matchSection.body, referral.match_summary);

  const appointmentSection = operationSection("Appointment proposals");
  const intakeSection = operationSection("Intake status");
  const briefSection = operationSection("Prep briefs");
  const draftSection = operationSection("Communication drafts");
  renderCommunicationDrafts(draftSection.body, referral.communication_drafts || []);
  const missingReplySection = operationSection("Missing-info replies");
  renderDocuments(missingReplySection.body, referral.missing_info_replies || [], "No missing-information replies recorded.");
  const patientReplySection = operationSection("Patient reply history");
  renderDocuments(patientReplySection.body, referral.patient_replies || [], "No patient replies recorded.");

  const raw = document.createElement("details");
  raw.className = "handoff";
  const rawSummary = document.createElement("summary");
  rawSummary.textContent = "Raw referral source";
  const rawText = document.createElement("pre");
  rawText.textContent = referral.raw_text || "";
  raw.append(rawSummary, rawText);

  const activitySection = operationSection("Activity and workflow traces");
  renderSimpleList(
    activitySection.body,
    referral.workflow_runs || [],
    "No workflow traces attached to this referral.",
    (workflow) =>
      recordItem({
        title: friendlyWorkflowType(workflow.workflow_type),
        status: workflow.status,
        body: workflow.input_summary || workflow.job_id,
        meta: [shortId(workflow.job_id), formatDate(workflow.created_at)],
      }),
  );

  referralDetail.append(
    header,
    readinessSection.section,
    fieldsSection.section,
    missingSection.section,
    riskSection.section,
    taskSection.section,
    matchSection.section,
    appointmentSection.section,
    draftSection.section,
    missingReplySection.section,
    patientReplySection.section,
    intakeSection.section,
    briefSection.section,
    raw,
    activitySection.section,
  );
  await loadReferralOperations(referral.id, appointmentSection.body, intakeSection.body, briefSection.body);
}

async function loadReferralOperations(referralId, appointmentBody, intakeBody, briefBody) {
  const [appointmentResponse, intakeResponse] = await Promise.all([
    fetch(`/api/appointments?referral_id=${encodeURIComponent(referralId)}`),
    fetch(`/api/referrals/${referralId}/intake`),
  ]);
  if (appointmentResponse.ok) {
    const data = await readResponseBody(appointmentResponse);
    renderAppointments(appointmentBody, data.appointments || []);
  }
  if (intakeResponse.ok) {
    const data = await readResponseBody(intakeResponse);
    renderIntakeWorkspace(intakeBody, data, false);
    renderIntakeWorkspace(intakeWorkspace, data, true);
    renderPrepBriefs(briefBody, data.prep_briefs || []);
  }
}

async function runReferralMatch(referralId) {
  const response = await fetch(`/api/referrals/${referralId}/match`, { method: "POST" });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Matching failed.");
    return;
  }
  await loadReferrals();
  await loadReferralDetail(referralId);
}

async function proposeReferralSlots(referralId) {
  const response = await fetch(`/api/referrals/${referralId}/appointment-proposals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit: 3 }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not propose slots.");
    return;
  }
  await loadReferralDetail(referralId);
}

async function draftMissingInfo(referralId) {
  const note = window.prompt("Optional note for the missing-information draft", "") || "";
  const response = await fetch(`/api/referrals/${referralId}/missing-info-draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recipient: "patient", note }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not draft missing-information message.");
    return;
  }
  await refreshProductWorkspace();
  await loadReferralDetail(referralId);
}

async function recordMissingInfoReply(referralId) {
  const email = window.prompt("Patient/referrer email from reply, if provided", "") || "";
  const insurer = window.prompt("Insurer from reply, if provided", "") || "";
  const phone = window.prompt("Phone from reply, if provided", "") || "";
  const dob = window.prompt("Date of birth from reply, if provided", "") || "";
  const notes = window.prompt("Reply notes", "") || "";
  const updates = {};
  if (email) updates.contact_email = email;
  if (insurer) updates.insurer = insurer;
  if (phone) updates.contact_phone = phone;
  if (dob) updates.date_of_birth = dob;
  const response = await fetch(`/api/referrals/${referralId}/missing-info-replies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source: "patient", updates, notes }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not record missing-information reply.");
    return;
  }
  await refreshProductWorkspace();
  await loadReferralDetail(referralId);
}

async function requestClinicalReview(referralId) {
  const response = await fetch(`/api/referrals/${referralId}/clinical-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: "Clinical risk or suitability review is required before matching." }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not create clinical review task.");
    return;
  }
  await refreshProductWorkspace();
  await loadReferralDetail(referralId);
}

async function requestSuitabilityReview(referralId) {
  const reason = window.prompt("Reason for suitability review", "Suitability review is required before therapist matching.");
  if (reason === null) return;
  const response = await fetch(`/api/referrals/${referralId}/suitability-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not create suitability review task.");
    return;
  }
  await refreshProductWorkspace();
  await loadReferralDetail(referralId);
}

async function requestDuplicateReview(referralId) {
  const candidate = window.prompt("Duplicate candidate referral ID, if known", "") || "";
  const reason = window.prompt("Reason for duplicate review", "Potential duplicate referral requires admin resolution before matching.") || "";
  const response = await fetch(`/api/referrals/${referralId}/duplicate-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candidate_referral_id: candidate || null, reason }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not create duplicate review task.");
    return;
  }
  await refreshProductWorkspace();
  await loadReferralDetail(referralId);
}

async function draftFirstContact(referralId) {
  const note = window.prompt("Optional note for first-contact draft", "") || "";
  const response = await fetch(`/api/referrals/${referralId}/contact-draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not draft first-contact message.");
    return;
  }
  await refreshProductWorkspace();
  await loadReferralDetail(referralId);
}

async function simulatePatientReply(referralId, appointmentId, replyType) {
  const response = await fetch(`/api/referrals/${referralId}/patient-replies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reply_type: replyType,
      appointment_id: appointmentId,
      notes: "Simulated clinic-admin patient reply.",
    }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not record simulated patient reply.");
    return;
  }
  await refreshProductWorkspace();
  await loadReferralDetail(referralId);
}

async function confirmAppointment(appointmentId) {
  const response = await fetch(`/api/appointments/${appointmentId}/confirm`, { method: "POST" });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not confirm appointment.");
    return;
  }
  if (selectedReferralId) await loadReferralDetail(selectedReferralId);
}

async function startReferralIntake(referralId) {
  const response = await fetch(`/api/referrals/${referralId}/intake`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not start intake.");
    return;
  }
  navigate("/intake");
  renderIntakeWorkspace(intakeWorkspace, body, true);
  await loadReferralDetail(referralId);
}

async function draftIntakePacket(referralId) {
  const note = window.prompt("Optional note for intake packet", "") || "";
  const response = await fetch(`/api/referrals/${referralId}/intake-packet-draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not draft intake packet.");
    return;
  }
  await refreshProductWorkspace();
  await loadReferralDetail(referralId);
}

async function completeIntakeItem(itemId) {
  const response = await fetch(`/api/intake/items/${itemId}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes: "Completed from workspace" }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) window.alert(body.detail || "Could not complete item.");
  if (selectedReferralId) await loadReferralDetail(selectedReferralId);
}

async function requestIntakeItemException(itemId) {
  const reason = window.prompt("Reason for waiving this intake item", "Admin-approved intake exception.");
  if (reason === null) return;
  const response = await fetch(`/api/intake/items/${itemId}/exception-request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not request intake exception.");
    return;
  }
  await refreshProductWorkspace();
  if (selectedReferralId) await loadReferralDetail(selectedReferralId);
}

async function completeConsent(consentId) {
  const response = await fetch(`/api/consent-records/${consentId}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const body = await readResponseBody(response);
  if (!response.ok) window.alert(body.detail || "Could not complete consent.");
  if (selectedReferralId) await loadReferralDetail(selectedReferralId);
}

async function requestConsentException(consentId) {
  const reason = window.prompt("Reason for waiving this consent requirement", "Admin-approved consent exception.");
  if (reason === null) return;
  const response = await fetch(`/api/consent-records/${consentId}/exception-request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not request consent exception.");
    return;
  }
  await refreshProductWorkspace();
  if (selectedReferralId) await loadReferralDetail(selectedReferralId);
}

async function saveScreeningQuestionnaire(referralId) {
  const mood = Number(window.prompt("Mood difficulty 0-3", "1") || 0);
  const anxiety = Number(window.prompt("Anxiety difficulty 0-3", "1") || 0);
  const sleep = Number(window.prompt("Sleep difficulty 0-3", "1") || 0);
  const response = await fetch(`/api/referrals/${referralId}/questionnaires`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      questionnaire_name: "generic_screening",
      answers: { mood, anxiety, sleep },
    }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) window.alert(body.detail || "Could not save questionnaire.");
  if (selectedReferralId) await loadReferralDetail(selectedReferralId);
}

async function generateReferralPrepBrief(referralId) {
  const response = await fetch(`/api/referrals/${referralId}/prep-brief`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not generate prep brief.");
    return;
  }
  await loadReferralDetail(referralId);
}

function prepareSessionNoteFromReferral(referral) {
  sessionNoteForm.elements.referral_id.value = referral.id;
  sessionNoteForm.elements.title.value = `${referral.patient_name || "Patient"} session note`;
  navigate("/clinical");
}

function prepareReportDraftFromReferral(referral) {
  reportDraftForm.elements.referral_id.value = referral.id;
  reportDraftForm.elements.title.value = `${referral.patient_name || "Patient"} session summary`;
  reportDraftForm.elements.request_text.value = "Create a concise evidence-grounded session summary.";
  navigate("/clinical");
}

async function draftIntakeReminder(referralId) {
  const response = await fetch(`/api/referrals/${referralId}/intake-reminder`, { method: "POST" });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not draft an intake reminder.");
    return;
  }
  await refreshProductWorkspace();
  await loadReferralDetail(referralId);
}

async function uploadIntakeDocument(referralId, item) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".txt,.pdf,.docx,.csv,.xlsx,.json";
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    const payload = new FormData();
    payload.append("file", file);
    payload.append("item_id", item.id);
    payload.append("document_type", item.item_type === "consent" ? "consent_document" : "intake_document");
    const response = await fetch(`/api/referrals/${referralId}/documents`, {
      method: "POST",
      body: payload,
    });
    const body = await readResponseBody(response);
    if (!response.ok) {
      window.alert(body.detail || "Could not upload document.");
      return;
    }
    if (selectedReferralId) await loadReferralDetail(selectedReferralId);
  });
  input.click();
}

async function loadReviewTasks() {
  const response = await fetch("/api/review-tasks?status=open");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  latestReviewTasks = data.tasks || [];
  metricReviews.textContent = latestReviewTasks.length;

  renderCollection(reviewTaskList, latestReviewTasks, "No open review tasks.", reviewTaskCard);
  renderCollection(overviewReviewList, latestReviewTasks.slice(0, 4), "No open review tasks.", (task) =>
    recordItem({
      title: task.task_type.replaceAll("_", " "),
      status: task.status,
      body: task.reason,
      meta: [task.payload_key, task.referral_id ? `referral ${shortId(task.referral_id)}` : "no referral"],
    }),
  );
}

async function loadIntakeTracker() {
  if (!intakeTrackerList) return;
  const response = await fetch("/api/intake/tracker");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  renderCollection(intakeTrackerList, data.items || [], "No active intake items yet.", (row) => {
    const referral = row.referral || {};
    const item = recordItem({
      title: referral.patient_name || referral.id || "Referral",
      status: row.intake_status,
      body: `${row.missing_count || 0} missing, ${row.completed_count || 0} completed, ${row.waived_count || 0} waived`,
      meta: [referral.status_label || referral.status, referral.next_action_label],
    });
    item.classList.add("clickable");
    item.addEventListener("click", async () => {
      navigate("/referrals");
      await loadReferralDetail(referral.id);
    });
    return item;
  });
}

function reviewTaskCard(task) {
  const item = recordItem({
    title: task.task_type.replaceAll("_", " "),
    status: task.status,
    body: task.reason,
    meta: [task.payload_key, task.referral_id ? `referral ${shortId(task.referral_id)}` : "no referral"],
  });

  if (task.draft_text) {
    const editor = document.createElement("textarea");
    editor.rows = 5;
    editor.value = task.draft_text;
    editor.dataset.taskEditor = task.id;
    item.appendChild(editor);
  }

  const actions = document.createElement("div");
  actions.className = "actions tight";
  const approve = actionButton("Approve", () => submitReviewAction(task.id, "approve"));
  const reject = actionButton("Reject", () => {
    const reason = window.prompt("Reason for rejection");
    if (reason !== null) submitReviewAction(task.id, "reject", { rejection_reason: reason });
  });
  const changes = actionButton("Request changes", () => {
    const reason = window.prompt("Requested changes");
    if (reason !== null) submitReviewAction(task.id, "request_changes", { rejection_reason: reason });
  });
  const escalate = actionButton("Escalate", () => submitReviewAction(task.id, "escalate"));
  actions.append(approve, reject, changes, escalate);
  item.appendChild(actions);
  return item;
}

async function submitReviewAction(taskId, action, extra = {}) {
  const editor = document.querySelector(`[data-task-editor="${taskId}"]`);
  const payload = {
    action,
    ...extra,
  };
  if (editor && action === "approve") {
    payload.final_text = editor.value;
  }

  const response = await fetch(`/api/review-tasks/${taskId}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    navigate("/workflows");
    appendTimelineEvent({
      type: "error",
      status: "failed",
      node: "review",
      message: body.detail || "Review action failed.",
      tools: [],
    });
    return;
  }
  await refreshProductWorkspace();
  if (selectedReferralId) {
    await loadReferralDetail(selectedReferralId);
  }
  if (body.resumed_job) {
    navigate("/workflows");
    resetRunState();
    jobIdLabel.textContent = body.resumed_job.job_id;
    setStatus(body.resumed_job.status, statusLabel(body.resumed_job.status));
    openEventStream(body.resumed_job.job_id, `/api/events/${body.resumed_job.job_id}`);
    startPolling(`/api/status/${body.resumed_job.job_id}`);
  }
}

async function loadTherapists() {
  const response = await fetch("/api/therapists");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  latestTherapists = data.therapists || [];
  metricTherapists.textContent = latestTherapists.filter((therapist) => therapist.active).length;

  renderCollection(therapistList, latestTherapists, "No therapist profiles found.", (therapist) =>
    recordItem({
      title: therapist.name,
      status: therapist.active ? "active" : "inactive",
      body: therapist.specialties.join(", ") || "No specialties recorded",
      meta: [
        `${therapist.capacity_per_week}/week`,
        therapist.languages.join(", "),
        therapist.modalities.join(", "),
      ],
    }),
  );
}

async function loadClinicalLibrary() {
  const response = await fetch("/api/clinical-library");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  renderCollection(clinicalLibraryList, data.records || [], "No clinical library sources yet.", (record) =>
    recordItem({
      title: record.title,
      status: record.status,
      body: record.body,
      meta: [record.record_type.replaceAll("_", " "), record.version ? `version ${record.version}` : null],
    }),
  );
}

async function loadIntegrationHealth() {
  const response = await fetch("/api/integrations/health");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  renderCollection(integrationHealthList, data.checks || [], "No integration checks yet.", (check) =>
    recordItem({
      title: check.name,
      status: check.status,
      body: check.message,
      meta: [check.last_seen ? formatDate(check.last_seen) : null],
    }),
  );
}

async function loadImportBatches() {
  const response = await fetch("/api/integrations/referral-batches");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  renderCollection(importBatchList, data.batches || [], "No referral imports yet.", (batch) =>
    recordItem({
      title: batch.file_name || "Referral import",
      status: batch.status,
      body: `${batch.imported_count || 0} imported, ${batch.error_count || 0} errors`,
      meta: [batch.source_channel, batch.total_rows ? `${batch.total_rows} rows` : null, formatDate(batch.created_at)],
    }),
  );
}

async function loadSecurityPosture() {
  const response = await fetch("/api/security/posture");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  securityPostureList.replaceChildren(
    recordItem({
      title: "Audit and review controls",
      status: data.open_review_tasks ? "open" : "ok",
      body: `${data.audit_events || 0} audit events, ${data.open_review_tasks || 0} open review tasks, ${data.signed_reports || 0} signed reports`,
      meta: [`retention ${data.retention_policy?.default_days || 0} days`, data.retention_policy?.audit_log_policy],
    }),
    recordItem({
      title: "Model data policy",
      status: data.model_data_policy?.send_phi_to_external_provider ? "needs_review" : "ok",
      body: `Provider ${data.model_data_policy?.provider || "unknown"}`,
      meta: [data.model_data_policy?.send_phi_to_external_provider ? "external PHI enabled" : "external PHI disabled"],
    }),
  );
}

async function loadFeedbackMetrics() {
  const response = await fetch("/api/feedback/metrics");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  feedbackMetricsList.replaceChildren(
    recordItem({
      title: "Draft feedback",
      status: data.feedback_count ? "active" : "configured",
      body: `${data.feedback_count || 0} feedback records, ${data.practice_memory_eligible || 0} eligible for practice memory`,
      meta: [`${data.signed_report_count || 0} signed reports`, `${data.drafts_with_unsupported_claims || 0} unsupported drafts`],
    }),
  );
}

async function loadPatientWorkspace(patientId) {
  if (!patientId) return;
  const response = await fetch(`/api/patients/${encodeURIComponent(patientId)}/workspace`);
  if (!response.ok) return;
  const data = await readResponseBody(response);
  renderPatientWorkspace(data);
  navigate("/clinical");
}

function renderPatientWorkspace(data) {
  clinicalWorkspace.replaceChildren();
  const patient = data.patient || {};
  const header = document.createElement("div");
  header.className = "detail-stack";
  header.append(
    heading(patient.display_name || patient.id || "Patient workspace"),
    metaRow([patient.language, patient.contact_email, `${data.referrals?.length || 0} referrals`]),
  );
  clinicalWorkspace.appendChild(header);

  (data.session_notes || []).forEach((note) => {
    const item = recordItem({
      title: note.title,
      status: note.status,
      body: note.body,
      meta: [note.therapist_id ? `therapist ${shortId(note.therapist_id)}` : null, note.approved_at ? `approved ${formatDate(note.approved_at)}` : null],
    });
    if (note.status !== "approved") {
      const actions = document.createElement("div");
      actions.className = "actions tight";
      actions.appendChild(actionButton("Approve note", () => approveSessionNote(note.id, patient.id)));
      item.appendChild(actions);
    }
    clinicalWorkspace.appendChild(item);
  });

  (data.report_drafts || []).forEach((report) => {
    const item = recordItem({
      title: report.title,
      status: report.status,
      body: report.body,
      meta: [
        report.report_type.replaceAll("_", " "),
        `${report.claim_evidence_map?.length || 0} evidence links`,
        report.unsupported_claims?.length ? `${report.unsupported_claims.length} unsupported` : null,
      ],
    });
    item.appendChild(reportEvidenceDetails(report));
    const actions = document.createElement("div");
    actions.className = "actions tight";
    if (report.status !== "signed_off") {
      const editor = document.createElement("textarea");
      editor.value = report.body || "";
      item.appendChild(editor);
      actions.appendChild(actionButton("Save draft", () => updateReportDraft(report.id, patient.id, editor.value)));
      if (!(report.unsupported_claims || []).length) {
        actions.appendChild(actionButton("Sign off", () => signOffReportDraft(report.id, patient.id)));
      }
    } else {
      actions.appendChild(actionButton("Export Markdown", () => exportReportDraft(report.id)));
    }
    item.appendChild(actions);
    clinicalWorkspace.appendChild(item);
  });

  (data.scores || []).forEach((score) => {
    clinicalWorkspace.appendChild(
      recordItem({
        title: score.instrument_name,
        status: score.status,
        body: `Score ${score.score_summary?.total_score ?? 0}`,
        meta: [`${score.score_summary?.answered_items ?? 0} answers`, score.recorded_at ? formatDate(score.recorded_at) : null],
      }),
    );
  });

  (data.documents || []).slice(0, 6).forEach((documentRecord) => {
    clinicalWorkspace.appendChild(
      recordItem({
        title: documentRecord.title,
        status: "completed",
        body: documentRecord.document_type.replaceAll("_", " "),
        meta: [documentRecord.metadata?.parser || "stored", documentRecord.created_at ? formatDate(documentRecord.created_at) : null],
      }),
    );
  });
}

async function approveSessionNote(noteId, patientId) {
  const response = await fetch(`/api/session-notes/${noteId}/approve`, { method: "POST" });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Session note could not be approved.");
    return;
  }
  await loadPatientWorkspace(patientId);
}

async function signOffReportDraft(reportId, patientId) {
  const response = await fetch(`/api/report-drafts/${reportId}/sign-off`, { method: "POST" });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Report draft could not be signed off.");
    return;
  }
  await loadPatientWorkspace(patientId);
}

async function updateReportDraft(reportId, patientId, bodyText) {
  const response = await fetch(`/api/report-drafts/${reportId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body: bodyText, usable_for_practice_memory: false }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Report draft could not be updated.");
    return;
  }
  await loadPatientWorkspace(patientId);
  await loadFeedbackMetrics();
}

function exportReportDraft(reportId) {
  window.location.href = `/api/report-drafts/${encodeURIComponent(reportId)}/export?format=markdown`;
}

function reportEvidenceDetails(report) {
  const detail = document.createElement("details");
  detail.className = "handoff";
  const summary = document.createElement("summary");
  summary.textContent = "Evidence and unsupported claims";
  const payload = document.createElement("pre");
  payload.textContent = JSON.stringify(
    {
      claim_evidence_map: report.claim_evidence_map || [],
      unsupported_claims: report.unsupported_claims || [],
      retrieval_summary: report.retrieval_summary || {},
    },
    null,
    2,
  );
  detail.append(summary, payload);
  return detail;
}

function operationSection(title) {
  const section = document.createElement("section");
  section.className = "operation-section";
  const headingEl = document.createElement("h4");
  headingEl.textContent = title;
  const body = document.createElement("div");
  body.className = "record-list embedded-list";
  section.append(headingEl, body);
  return { section, body };
}

function renderSimpleList(container, items, emptyText, renderer) {
  container.replaceChildren();
  if (!items.length) {
    container.appendChild(emptyState(emptyText));
    return;
  }
  items.forEach((item) => container.appendChild(renderer(item)));
}

function renderCommunicationDrafts(container, drafts) {
  renderSimpleList(container, drafts, "No communication drafts yet.", (draft) => {
    const item = recordItem({
      title: draft.subject || "Draft message",
      status: draft.status,
      body: draft.body,
      meta: [draft.channel, draft.requires_human_send ? "requires approval" : "no approval required"],
    });
    return item;
  });
}

function renderDocuments(container, documents, emptyText) {
  renderSimpleList(container, documents, emptyText, (documentRecord) => {
    const metadata = documentRecord.metadata || {};
    const bodyParts = [
      metadata.reply_type ? `Reply type: ${metadata.reply_type}` : null,
      metadata.source ? `Source: ${metadata.source}` : null,
      metadata.notes || null,
    ].filter(Boolean);
    return recordItem({
      title: documentRecord.title,
      status: documentRecord.document_type,
      body: bodyParts.join(" | ") || documentRecord.document_type.replaceAll("_", " "),
      meta: [formatDate(documentRecord.created_at)],
    });
  });
}

function renderMatchSummary(container, matchSummary) {
  container.replaceChildren();
  const ranked = matchSummary?.ranked_matches || [];
  const excluded = matchSummary?.excluded_therapists || [];
  if (!ranked.length && !excluded.length) {
    container.appendChild(emptyState("Run deterministic matching to rank therapists."));
    return;
  }
  ranked.slice(0, 3).forEach((match) => {
    container.appendChild(
      recordItem({
        title: match.name || match.therapist_id || "Therapist",
        status: match.excluded ? "excluded" : "active",
        body: match.rationale || (match.reasons || []).join(", ") || "Eligible match",
        meta: [
          typeof match.score === "number" ? `score ${match.score}` : null,
          match.capacity_per_week ? `${match.capacity_used_this_week || 0}/${match.capacity_per_week} used` : null,
        ],
      }),
    );
  });
  excluded.slice(0, 3).forEach((match) => {
    container.appendChild(
      recordItem({
        title: match.name || match.therapist_id || "Excluded therapist",
        status: "excluded",
        body: (match.exclusion_reasons || []).join(", ") || "Excluded by hard constraints",
        meta: [typeof match.score === "number" ? `score ${match.score}` : null],
      }),
    );
  });
}

function renderAppointments(container, appointments) {
  container.replaceChildren();
  if (!appointments.length) {
    container.appendChild(emptyState("No appointment proposals yet."));
    return;
  }
  appointments.forEach((appointment) => {
    const item = recordItem({
      title: appointment.starts_at ? new Date(appointment.starts_at).toLocaleString() : "Unscheduled proposal",
      status: appointment.status,
      body: appointment.therapist_id ? `Therapist ${shortId(appointment.therapist_id)}` : "No therapist assigned",
      meta: [appointment.source, appointment.ends_at ? `ends ${new Date(appointment.ends_at).toLocaleTimeString()}` : null],
    });
    if (appointment.status === "proposed") {
      const actions = document.createElement("div");
      actions.className = "actions tight";
      if (selectedReferralId) {
        actions.append(
          actionButton("Simulate accepted reply", () => simulatePatientReply(selectedReferralId, appointment.id, "accepted_slot")),
          actionButton("Simulate declined", () => simulatePatientReply(selectedReferralId, appointment.id, "declined")),
        );
      }
      item.appendChild(actions);
    }
    container.appendChild(item);
  });
}

function renderIntakeWorkspace(container, data, includeActions) {
  container.replaceChildren();
  if (!data || data.status === "not_started") {
    container.appendChild(emptyState("Intake has not started for this referral."));
    return;
  }
  const header = document.createElement("div");
  header.className = "detail-stack";
  header.append(
    heading(data.template?.name || "Intake checklist"),
    metaRow([data.status, `${data.items?.length || 0} items`, `${data.consents?.length || 0} consents`]),
  );
  if (includeActions && data.referral?.id) {
    const actions = document.createElement("div");
    actions.className = "actions tight";
    actions.append(
      actionButton("Save screening", () => saveScreeningQuestionnaire(data.referral.id)),
      actionButton("Draft reminder", () => draftIntakeReminder(data.referral.id)),
      actionButton("Generate prep brief", () => generateReferralPrepBrief(data.referral.id)),
    );
    header.appendChild(actions);
  }
  container.appendChild(header);

  (data.items || []).forEach((item) => {
    const row = recordItem({
      title: item.label,
      status: item.status,
      body: item.item_type,
      meta: [item.due_at ? `due ${formatDate(item.due_at)}` : null],
    });
    if (includeActions && !["completed", "waived"].includes(item.status)) {
      const actions = document.createElement("div");
      actions.className = "actions tight";
      actions.append(
        actionButton("Upload file", () => uploadIntakeDocument(data.referral.id, item)),
        actionButton("Mark complete", () => completeIntakeItem(item.id)),
        actionButton("Request waiver", () => requestIntakeItemException(item.id)),
      );
      row.appendChild(actions);
    }
    container.appendChild(row);
  });

  (data.consents || []).forEach((consent) => {
    const row = recordItem({
      title: consent.scope.replaceAll("_", " "),
      status: consent.status,
      body: "Consent record",
      meta: [consent.expires_at ? `expires ${formatDate(consent.expires_at)}` : "no expiry"],
    });
    if (includeActions && !["completed", "waived"].includes(consent.status)) {
      const actions = document.createElement("div");
      actions.className = "actions tight";
      actions.append(
        actionButton("Complete consent", () => completeConsent(consent.id)),
        actionButton("Request waiver", () => requestConsentException(consent.id)),
      );
      row.appendChild(actions);
    }
    container.appendChild(row);
  });

  (data.questionnaires || []).forEach((questionnaire) => {
    container.appendChild(
      recordItem({
        title: questionnaire.questionnaire_name,
        status: questionnaire.status,
        body: `Score ${questionnaire.score_summary?.total_score ?? 0}`,
        meta: [`${questionnaire.score_summary?.answered_items ?? 0} answers`],
      }),
    );
  });

  (data.documents || []).forEach((documentRecord) => {
    container.appendChild(
      recordItem({
        title: documentRecord.title,
        status: "completed",
        body: documentRecord.document_type.replaceAll("_", " "),
        meta: [
          documentRecord.metadata?.size_bytes ? `${documentRecord.metadata.size_bytes} bytes` : null,
          documentRecord.metadata?.virus_scan?.status || "stored",
        ],
      }),
    );
  });

  (data.communication_drafts || []).slice(0, 2).forEach((draft) => {
    container.appendChild(
      recordItem({
        title: draft.subject || "Intake reminder draft",
        status: draft.status,
        body: draft.body,
        meta: [draft.channel, draft.requires_human_send ? "requires approval" : null],
      }),
    );
  });
}

function renderPrepBriefs(container, briefs) {
  container.replaceChildren();
  if (!briefs.length) {
    container.appendChild(emptyState("No therapist prep brief generated yet."));
    return;
  }
  briefs.slice(0, 2).forEach((brief) => {
    const detail = document.createElement("details");
    detail.className = "handoff";
    const summary = document.createElement("summary");
    summary.textContent = brief.title;
    const pre = document.createElement("pre");
    pre.textContent = brief.body;
    detail.append(summary, pre);
    container.appendChild(detail);
  });
}

function renderCollection(container, items, emptyText, renderer) {
  container.replaceChildren();
  if (!items.length) {
    container.appendChild(emptyState(emptyText));
    return;
  }
  items.forEach((item) => container.appendChild(renderer(item)));
}

function actionButton(label, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary compact";
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function recordItem({ title, status, body, meta }) {
  const item = document.createElement("article");
  item.className = "record-item";
  item.dataset.status = status || "idle";
  item.append(heading(title), pill(status || "pending"));
  if (body) {
    const paragraph = document.createElement("p");
    paragraph.textContent = body;
    item.appendChild(paragraph);
  }
  item.appendChild(metaRow(meta || []));
  return item;
}

function heading(text) {
  const element = document.createElement("h4");
  element.textContent = text;
  return element;
}

function keyValue(label, value) {
  const wrapper = document.createElement("div");
  wrapper.className = "key-value";
  const key = document.createElement("span");
  key.textContent = label;
  const val = document.createElement("strong");
  val.textContent = value;
  wrapper.append(key, val);
  return wrapper;
}

function metaRow(items) {
  const row = document.createElement("div");
  row.className = "meta-row";
  items.filter(Boolean).forEach((item) => row.appendChild(pill(item)));
  return row;
}

function emptyState(text) {
  const element = document.createElement("p");
  element.className = "empty-state";
  element.textContent = text;
  return element;
}

function pill(text) {
  const element = document.createElement("span");
  element.className = "pill";
  element.textContent = text;
  return element;
}

function setStatus(status, label) {
  statusStrip.dataset.status = status || "idle";
  monitorTitle.textContent = label || "Idle";
}

function statusLabel(status) {
  const labels = {
    queued: "Queued",
    running: "Running",
    ok: "Running",
    completed: "Completed",
    needs_human_review: "Needs human review",
    needs_review: "Needs human review",
    failed: "Failed",
    idle: "Idle",
  };
  return labels[status] || "Running";
}

function resetRunState() {
  closeEventStream();
  clearInterval(pollTimer);
  pollTimer = null;
  currentResult = null;
  timeline.replaceChildren();
  handoffs.replaceChildren();
  finalOutput.hidden = true;
  resultJson.textContent = "";
  exportButton.disabled = true;
  jobIdLabel.textContent = "No job";
}

function closeEventStream() {
  if (activeSource) {
    activeSource.close();
    activeSource = null;
  }
}

function friendlyWorkflowType(value) {
  return String(value || "workflow").replaceAll("_", " ");
}

function shortId(value) {
  return value ? String(value).slice(0, 8) : "";
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function csvList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function friendlyClientError(error) {
  const message = error?.message || String(error);
  if (message.toLowerCase().includes("failed to fetch")) {
    return "The Lumen server is not reachable. Confirm the FastAPI process is running.";
  }
  return message;
}

async function readResponseBody(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

applyRoute(window.location.pathname);
loadExamples();
loadModelHealth();
refreshProductWorkspace();
