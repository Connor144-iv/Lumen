const routes = {
  "/": { page: "overview", title: "Referral Journey Dashboard", label: "Clinic operations" },
  "/overview": { page: "overview", title: "Referral Journey Dashboard", label: "Clinic operations" },
  "/new-referral": { page: "new-referral", title: "New referral", label: "Clinic intake" },
  "/workflows": { page: "new-referral", title: "New referral", label: "Clinic intake" },
  "/workbench": { page: "workbench", title: "Workbench", label: "Referral operations" },
  "/referrals": { page: "workbench", title: "Workbench", label: "Referral operations" },
  "/review": { page: "review", title: "Review inbox", label: "Governance" },
  "/therapists": { page: "therapists", title: "Therapists", label: "Network" },
  "/intake": { page: "intake", title: "Intake & scheduling", label: "Pre-session" },
  "/clinical": { page: "clinical", title: "Clinical", label: "Documentation" },
  "/integrations": { page: "integrations", title: "Integrations", label: "Channels" },
  "/system": { page: "system", title: "System / Agents", label: "Agent control" },
  "/documentation": {
    page: "documentation",
    title: "Documentation",
    label: "Therapist workspace",
    therapistOnly: true,
  },
  "/my-patients": {
    page: "my-patients",
    title: "My patients",
    label: "Therapist workspace",
    therapistOnly: true,
  },
};

const form = document.querySelector("#workflow-form");
const runButton = document.querySelector("#run-button");
const resetButton = document.querySelector("#reset-button");
const demoResetButton = document.querySelector("#demo-reset-button");
const exportButton = document.querySelector("#export-button");
const refreshHealthButton = document.querySelector("#refresh-health-button");
const refreshProductButton = document.querySelector("#refresh-product-button");
const viewTraceLink = document.querySelector("#view-trace-link");
const statusStrip = document.querySelector("#status-strip");
const monitorTitle = document.querySelector("#monitor-title");
const jobIdLabel = document.querySelector("#job-id-label");
const timeline = document.querySelector("#timeline");
const handoffs = document.querySelector("#handoffs");
const finalOutput = document.querySelector("#final-output");
const resultJson = document.querySelector("#result-json");
const newReferralResultList = document.querySelector("#new-referral-result-list");
const exampleButtons = document.querySelector("#example-buttons");
const modelHealthList = document.querySelector("#model-health-list");
const referralList = document.querySelector("#referral-list");
const referralFilterForm = document.querySelector("#referral-filter-form");
const referralDetail = document.querySelector("#referral-detail");
const reviewTaskList = document.querySelector("#review-task-list");
const escalationList = document.querySelector("#escalation-list");
const therapistList = document.querySelector("#therapist-list");
const therapistForm = document.querySelector("#therapist-form");
const intakeWorkspace = document.querySelector("#intake-workspace");
const intakeTrackerList = document.querySelector("#intake-tracker-list");
const schedulingList = document.querySelector("#scheduling-list");
const clinicalWorkspace = document.querySelector("#clinical-workspace");
const sessionNoteForm = document.querySelector("#session-note-form");
const reportDraftForm = document.querySelector("#report-draft-form");
const clinicalLibraryForm = document.querySelector("#clinical-library-form");
const clinicalLibraryList = document.querySelector("#clinical-library-list");
const referralImportForm = document.querySelector("#referral-import-form");
const importBatchList = document.querySelector("#import-batch-list");
const integrationHealthList = document.querySelector("#integration-health-list");
const googleWorkspaceList = document.querySelector("#google-workspace-list");
const gmailInboxList = document.querySelector("#gmail-inbox-list");
const gmailSyncButton = document.querySelector("#gmail-sync-button");
const gmailInboxRefreshButton = document.querySelector("#gmail-inbox-refresh");
const securityPostureList = document.querySelector("#security-posture-list");
const feedbackMetricsList = document.querySelector("#feedback-metrics-list");
const therapistContextLabel = document.querySelector("#therapist-context-label");
const myPatientsList = document.querySelector("#my-patients-list");
const myPatientsCount = document.querySelector("#my-patients-count");
const pageTitle = document.querySelector("#page-title");
const sectionLabel = document.querySelector("#section-label");
const recentWorkflowList = document.querySelector("#recent-workflow-list");
const overviewReferralList = document.querySelector("#overview-referral-list");
const overviewActionList = document.querySelector("#overview-action-list");
const overviewReviewList = document.querySelector("#overview-review-list");
const overviewHealthStrip = document.querySelector("#overview-health-strip");
const referralJourneyBoard = document.querySelector("#referral-journey-board");
const metricReferrals = document.querySelector("#metric-referrals");
const metricReviews = document.querySelector("#metric-reviews");
const metricTherapists = document.querySelector("#metric-therapists");
const metricModels = document.querySelector("#metric-models");
const metricWorkflows = document.querySelector("#metric-workflows");
const metricActionQueue = document.querySelector("#metric-action-queue");
const metricBlocked = document.querySelector("#metric-blocked");
const metricReady = document.querySelector("#metric-ready");
const metricReadyCount = document.querySelector("#metric-ready-count");
const metricNewReferrals = document.querySelector("#metric-new-referrals");
const metricLastSync = document.querySelector("#metric-last-sync");
const metricTherapistActive = document.querySelector("#metric-therapist-active");
const metricTherapistCapacity = document.querySelector("#metric-therapist-capacity");
const metricTherapistFull = document.querySelector("#metric-therapist-full");
const metricTherapistMissing = document.querySelector("#metric-therapist-missing");
const metricTherapistIncomplete = document.querySelector("#metric-therapist-incomplete");
const therapistDetail = document.querySelector("#therapist-detail");
const agentRegistryList = document.querySelector("#agent-registry-list");

let activeSource = null;
let pollTimer = null;
let currentResult = null;
let latestReferrals = [];
let latestJourney = null;
let latestReviewTasks = [];
let latestTherapists = [];
let latestTherapistCalendarCapacity = null;
let latestWorkflows = [];
let latestAppointments = [];
let latestSecurityContext = null;
let currentTherapist = null;
let currentTherapistPatients = [];
let securityContextLoaded = false;
let selectedReferralId = null;
let selectedTherapistId = null;
let selectedCalendarTherapistFilter = null;
let therapistCalendarWeekStart = null;
let therapistCalendarWeekPinnedByUser = false;
let lastReviewOutcome = null;

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
  navigate("/new-referral");
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
    setActiveJob(body.job_id);
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

demoResetButton?.addEventListener("click", resetCleanDemoPath);

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
  payload.active = formData.get("active") === "on";
  payload.availability_blocks = buildAvailabilityBlocks(formData);
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

if (gmailSyncButton) {
  gmailSyncButton.addEventListener("click", async () => {
    await syncGmailInbox();
  });
}

if (gmailInboxRefreshButton) {
  gmailInboxRefreshButton.addEventListener("click", async () => {
    await loadGmailInbox();
  });
}

function navigate(path) {
  const route = routes[path] || routes["/"];
  const nextPath = path === "/overview" ? "/" : path;
  if (window.location.pathname !== nextPath) {
    window.history.pushState({}, "", nextPath);
  }
  applyRoute(route === routes["/"] ? nextPath : path);
}

function applyRoute(path) {
  let route = routes[path] || routes["/"];
  if (route.therapistOnly && securityContextLoaded && !hasTherapistWorkspaceAccess()) {
    route = routes["/"];
    if (window.location.pathname !== "/") {
      window.history.replaceState({}, "", "/");
    }
  }
  document.querySelectorAll("[data-page]").forEach((page) => {
    page.classList.toggle("is-active", page.dataset.page === route.page);
  });
  document.querySelectorAll("[data-page-link]").forEach((link) => {
    link.classList.toggle("is-active", link.dataset.pageLink === route.page);
  });
  pageTitle.textContent = route.title;
  sectionLabel.textContent = route.label;
  document.title = `Lumen | ${route.title}`;
  if (route.page === "my-patients" && hasTherapistWorkspaceAccess()) {
    loadMyTherapistPatients();
  }
}

function hasTherapistWorkspaceAccess() {
  return latestSecurityContext?.user?.role === "therapist" && Boolean(currentTherapist);
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
  renderNewReferralResult(job);
}

function renderNewReferralResult(job) {
  if (!newReferralResultList) return;
  newReferralResultList.replaceChildren();
  const referralId = job.referral_id || job.result?.referral_id || job.result?.referral?.id;
  const item = recordItem({
    title: referralId ? `Referral ${shortId(referralId)} captured` : friendlyWorkflowType(job.workflow_type || "workflow"),
    status: job.status,
    body: job.error || "Workflow completed or reached the next human gate.",
    meta: [
      referralId ? `referral ${shortId(referralId)}` : null,
      job.result?.risk_review?.risk_category ? `risk ${job.result.risk_review.risk_category}` : null,
      job.result?.next_action,
    ],
  });
  if (referralId) {
    const actions = document.createElement("div");
    actions.className = "actions tight";
    actions.appendChild(actionButton("Open in Workbench", () => openReferralWorkbench(referralId)));
    item.appendChild(actions);
  }
  newReferralResultList.appendChild(item);
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
  navigate("/new-referral");
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
  if (metricModels) metricModels.textContent = "-";
  try {
    const response = await fetch("/api/health/models");
    const data = await readResponseBody(response);
    if (!response.ok) throw new Error(data.detail || "Model health check failed.");
    const checks = data.checks || [];
    const available = checks.filter((check) => check.status === "ok" || check.status === "configured").length;
    if (metricModels) metricModels.textContent = `${available}/${checks.length}`;
    renderAgentRegistry(checks);
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
    if (metricModels) metricModels.textContent = "0";
    renderAgentRegistry([]);
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

function renderAgentRegistry(modelChecks) {
  if (!agentRegistryList) return;
  const modelByRole = new Map((modelChecks || []).map((check) => [String(check.role || "").toLowerCase(), check]));
  const small = modelByRole.get("small") || modelByRole.get("small model");
  const medium = modelByRole.get("medium") || modelByRole.get("medium model");
  const communication = modelByRole.get("communication") || modelByRole.get("communication model");
  const agents = [
    { name: "Referral Intake Normalizer", model: small },
    { name: "Completeness Extractor", model: small },
    { name: "Risk Reviewer", model: small },
    { name: "Therapist Matching Planner", model: medium || small },
    { name: "Communication Drafter", model: communication || medium },
    { name: "Intake Collector", model: small },
    { name: "Prep Brief Generator", model: medium || communication },
  ];
  agentRegistryList.replaceChildren(
    ...agents.map((agent) =>
      recordItem({
        title: agent.name,
        status: agent.model?.status || "configured",
        body: agent.model?.message || "Enabled for the local demo workflow.",
        meta: [
          agent.model?.model ? `model ${agent.model.model}` : "model configured by environment",
          agent.model?.provider,
        ],
      }),
    ),
  );
}

async function refreshProductWorkspace() {
  await Promise.all([
    loadReferrals(),
    loadReviewTasks(),
    loadEscalations(),
    loadIntakeTracker(),
    loadScheduling(),
    loadTherapists(),
    loadWorkflows(),
    loadClinicalLibrary(),
    loadIntegrationHealth(),
    loadImportBatches(),
    loadSecurityPosture(),
    loadFeedbackMetrics(),
  ]);
  await loadReferralJourney();
}

async function loadSecurityContext() {
  try {
    const response = await fetch("/api/security/context");
    const data = await readResponseBody(response);
    if (!response.ok) throw new Error(data.detail || "Security context is unavailable.");
    latestSecurityContext = data;
    securityContextLoaded = true;
    if (latestSecurityContext?.user?.role === "therapist") {
      await loadMyTherapist();
    } else {
      currentTherapist = null;
      currentTherapistPatients = [];
      renderMyTherapistPatients();
    }
  } catch (error) {
    latestSecurityContext = null;
    currentTherapist = null;
    currentTherapistPatients = [];
    securityContextLoaded = true;
    renderMyTherapistPatients(friendlyClientError(error));
  }
  renderRoleAwareNavigation();
  applyRoute(window.location.pathname);
}

async function loadMyTherapist() {
  try {
    const response = await fetch("/api/me/therapist");
    const data = await readResponseBody(response);
    if (!response.ok) throw new Error(data.detail || "Therapist profile is unavailable.");
    currentTherapist = data.therapist || null;
    renderTherapistWorkspaceContext();
  } catch (error) {
    currentTherapist = null;
    renderTherapistWorkspaceContext(friendlyClientError(error));
  }
}

async function loadMyTherapistPatients() {
  if (!myPatientsList || !hasTherapistWorkspaceAccess()) return;
  myPatientsList.replaceChildren(emptyState("Loading patients..."));
  try {
    const response = await fetch("/api/me/therapist/patients");
    const data = await readResponseBody(response);
    if (!response.ok) throw new Error(data.detail || "Patients are unavailable.");
    currentTherapistPatients = data.patients || [];
    renderMyTherapistPatients();
  } catch (error) {
    currentTherapistPatients = [];
    renderMyTherapistPatients(friendlyClientError(error));
  }
}

function renderRoleAwareNavigation() {
  const enabled = hasTherapistWorkspaceAccess();
  document.querySelectorAll("[data-therapist-only]").forEach((element) => {
    element.hidden = !enabled;
  });
  renderTherapistWorkspaceContext();
}

function renderTherapistWorkspaceContext(errorMessage) {
  if (!therapistContextLabel) return;
  if (errorMessage) {
    therapistContextLabel.textContent = "Unavailable";
  } else if (currentTherapist?.name) {
    therapistContextLabel.textContent = currentTherapist.name;
  } else {
    therapistContextLabel.textContent = "Therapist context";
  }
}

function renderMyTherapistPatients(errorMessage) {
  if (!myPatientsList) return;
  const patients = currentTherapistPatients || [];
  if (myPatientsCount) {
    myPatientsCount.textContent = errorMessage ? "Unavailable" : `${patients.length} patient${patients.length === 1 ? "" : "s"}`;
  }
  if (errorMessage) {
    myPatientsList.replaceChildren(emptyState(errorMessage));
    return;
  }
  if (!patients.length) {
    myPatientsList.replaceChildren(emptyState("No patients are assigned yet."));
    return;
  }
  myPatientsList.replaceChildren(...patients.map((patient) => myPatientCard(patient)));
}

function myPatientCard(patient) {
  return recordItem({
    title: patient.display_name || patient.name || `Patient ${shortId(patient.id)}`,
    status: patient.status || "active",
    body: patient.contact_email || "No contact email recorded.",
    meta: [patient.language, patient.next_appointment_at ? `next ${formatDate(patient.next_appointment_at)}` : null],
  });
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
  if (metricNewReferrals) {
    metricNewReferrals.textContent = countRecentReferrals(latestReferrals);
  }

  renderReferralLists();
  renderSchedulingList();
  renderOverviewActionQueue();
  renderSelectedTherapist();
}

async function loadReferralJourney() {
  if (!referralJourneyBoard) return;
  const response = await fetch("/api/referral-journey");
  if (!response.ok) return;
  latestJourney = await readResponseBody(response);
  const metrics = latestJourney.metrics || {};
  metricReferrals.textContent = metrics.active_referrals ?? latestReferrals.length;
  if (metricBlocked) metricBlocked.textContent = metrics.blocked_referrals ?? "-";
  if (metricActionQueue) metricActionQueue.textContent = metrics.needs_action ?? "-";
  if (metricReady) metricReady.textContent = `${metrics.first_session_ready ?? 0} ready`;
  if (metricReadyCount) metricReadyCount.textContent = metrics.first_session_ready ?? "-";
  renderReferralJourneyBoard(latestJourney);
  renderOverviewActionQueue();
  renderJourneyReferralFocus();
}

function renderReferralJourneyBoard(data) {
  referralJourneyBoard.replaceChildren();
  const stages = data.stages || [];
  if (!stages.length) {
    referralJourneyBoard.appendChild(emptyState("No active referral stages found."));
    return;
  }
  stages.forEach((stage) => {
    const lane = document.createElement("section");
    lane.className = "journey-lane";

    const header = document.createElement("div");
    header.className = "journey-lane-header";
    const title = document.createElement("h4");
    title.textContent = stage.label;
    const count = document.createElement("span");
    count.className = "panel-stat";
    count.textContent = stage.count || 0;
    const description = document.createElement("p");
    description.textContent = stage.description || "";
    const blockedCount = (stage.referrals || []).filter((referral) => (referral.blockers || []).length).length;
    const blocked = document.createElement("span");
    blocked.className = "journey-lane-blocked";
    blocked.textContent = blockedCount ? `${blockedCount} blocked` : "Clear";
    header.append(title, count, description, blocked);

    const list = document.createElement("div");
    list.className = "record-list journey-list";
    const referrals = stage.referrals || [];
    if (!referrals.length) {
      list.appendChild(emptyState("No active referrals."));
    } else {
      referrals.forEach((referral) => list.appendChild(journeyReferralCard(referral)));
    }

    lane.append(header, list);
    referralJourneyBoard.appendChild(lane);
  });
}

function journeyReferralCard(referral) {
  const title = referral.patient_name || referral.contact_email || `Referral ${shortId(referral.id)}`;
  const action = referral.next_action_label || referral.next_action || "Next action pending";
  const item = document.createElement("article");
  item.className = "journey-card clickable";
  item.dataset.status = referral.status || "pending";
  item.dataset.severity = journeyReferralSeverity(referral);
  item.setAttribute("tabindex", "0");
  item.setAttribute("role", "button");
  item.setAttribute("aria-label", `${title}: ${action}`);

  const titleElement = document.createElement("h4");
  titleElement.className = "journey-card-title";
  titleElement.textContent = title;

  const actionElement = document.createElement("p");
  actionElement.className = "journey-card-action";
  actionElement.textContent = action;

  item.append(titleElement, actionElement);
  item.addEventListener("click", () => openReferralWorkbench(referral.id));
  item.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openReferralWorkbench(referral.id);
    }
  });
  return item;
}

function journeyReferralSeverity(referral) {
  const severities = (referral.blockers || []).map((blocker) => blocker.severity);
  if (severities.includes("danger")) return "danger";
  if (severities.includes("warning")) return "warning";
  if (severities.includes("info")) return "info";
  if (["first_session_ready", "prep_brief_ready", "intake_complete"].includes(referral.status)) return "success";
  return "neutral";
}

function journeyReferralCards() {
  if (!latestJourney?.stages) return [];
  return latestJourney.stages.flatMap((stage) => stage.referrals || []);
}

function renderJourneyReferralFocus() {
  if (!overviewReferralList) return;
  const activeCards = journeyReferralCards().slice(0, 4);
  renderCollection(overviewReferralList, activeCards, "No active referrals yet.", (referral) => referralCard(referral, true));
}

function renderReferralLists() {
  const filtered = filterReferrals(latestReferrals);
  renderCollection(referralList, filtered, "No referrals match these filters.", (referral) => referralCard(referral, false));
  renderCollection(overviewReferralList, latestReferrals.slice(0, 8), "No referrals yet.", (referral) =>
    referralCard(referral, true),
  );
}

function renderOverviewActionQueue() {
  if (!overviewActionList) return;
  const actionableNextActions = new Set([
    "review_referral",
    "review_missing_info",
    "clinical_review",
    "run_matching",
    "approve_match",
    "approve_slots",
    "approve_contact",
    "confirm_appointment",
    "start_intake",
    "complete_intake",
    "generate_prep_brief",
  ]);

  const sourceReferrals = journeyReferralCards().length ? journeyReferralCards() : latestReferrals || [];
  const actionableReferrals = sourceReferrals
    .filter((referral) => actionableNextActions.has(referral.next_action))
    .sort((a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime())
    .slice(0, 6);
  const reviewItems = (latestReviewTasks || []).slice(0, 6);
  const combined = [
    ...reviewItems.map((task) => ({ kind: "task", item: task })),
    ...actionableReferrals.map((referral) => ({ kind: "referral", item: referral })),
  ].slice(0, 10);

  if (metricActionQueue) {
    metricActionQueue.textContent = latestJourney?.metrics?.needs_action ?? combined.length;
  }

  renderCollection(overviewActionList, combined, "No items need attention right now.", (entry) => {
    if (entry.kind === "task") return needsAttentionTaskCard(entry.item);
    return needsAttentionReferralCard(entry.item);
  });
}

function needsAttentionTaskCard(task) {
  const item = recordItem({
    title: friendlyTaskType(task.task_type),
    status: task.status,
    body: task.reason,
    meta: [
      "human gate",
      task.referral_id ? `referral ${shortId(task.referral_id)}` : "no referral",
      task.payload_key,
    ],
  });
  if (task.referral_id) {
    item.classList.add("clickable");
    item.addEventListener("click", () => openReferralWorkbench(task.referral_id));
  }
  return item;
}

function needsAttentionReferralCard(referral) {
  const item = referralCard(referral, true);
  item.appendChild(
    keyValue(
      "Consequence",
      referral.next_action_label || referral.next_action || "Admin action is needed before this referral can progress.",
    ),
  );
  return item;
}

function countRecentReferrals(referrals) {
  const now = new Date();
  const sevenDaysAgo = now.getTime() - 7 * 24 * 60 * 60 * 1000;
  return referrals.filter((referral) => {
    const created = new Date(referral.created_at || referral.updated_at || 0).getTime();
    return Number.isFinite(created) && created >= sevenDaysAgo;
  }).length;
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
  const title = referral.patient_name || referral.contact_email || `Referral ${shortId(referral.id)}`;
  const contact = [referral.contact_email, referral.contact_phone].filter(Boolean).join(" | ");
  const item = recordItem({
    title,
    status: referral.status,
    body: referral.next_action_label || referral.next_action || "Next action pending",
    meta: [
      referral.status_label || referral.status,
      referral.source_channel,
      contact || referral.insurer,
      referral.risk_category || "risk pending",
      ...(referral.secondary_flags || []),
      formatDate(referral.updated_at),
    ],
  });
  item.classList.add("clickable");
  item.addEventListener("click", () => openReferralWorkbench(referral.id, jumpToDetail));
  return item;
}

async function openReferralWorkbench(referralId, jumpToDetail = true) {
  if (jumpToDetail) navigate("/workbench");
  await loadReferralDetail(referralId);
}

async function loadReferralDetail(referralId) {
  const response = await fetch(`/api/referrals/${referralId}`);
  if (!response.ok) return;
  const referral = await readResponseBody(response);
  selectedReferralId = referral.id;
  referralDetail.replaceChildren();

  const workbenchState = referral.workbench_state || {};
  const header = renderWorkbenchSummary(referral, workbenchState);
  const progress = renderReferralProgress(referral, workbenchState);
  const emailWorkflow = workbenchState.email_workflow || null;

  const disclosure = { collapsible: true, open: true };
  const readinessSection = operationSection("First-session readiness", disclosure);
  renderSimpleList(
    readinessSection.body,
    referral.readiness_blockers || [],
    "Appointment, intake, and prep brief gates are complete.",
    (blocker) => recordItem({ title: blocker, status: "open", body: "Must be resolved before first session readiness.", meta: [] }),
  );

  const fieldsSection = operationSection("Extracted fields", disclosure);
  fieldsSection.body.append(
    keyValue("Patient", referral.patient_name || "Missing"),
    keyValue("Date of birth", referral.date_of_birth || "Missing"),
    keyValue("Contact", [referral.contact_email, referral.contact_phone].filter(Boolean).join(" | ") || "Missing"),
    keyValue("Insurer", referral.insurer || "Missing"),
    keyValue("Language / modality", [referral.language_preference, referral.modality_preference].filter(Boolean).join(" / ") || "Not recorded"),
    keyValue("Referrer", referral.referring_entity || "Not recorded"),
  );

  const missingSection = operationSection("Missing information", disclosure);
  renderSimpleList(
    missingSection.body,
    referral.missing_fields || [],
    "No missing fields recorded.",
    (field) => recordItem({ title: field.replaceAll("_", " "), status: "missing", body: "Admin review field", meta: [] }),
  );

  const missingReplySection = operationSection("Record patient reply", { collapsible: true, open: Boolean(referral.missing_fields?.length) });
  missingReplySection.section.id = "referral-missing-reply";
  renderMissingInfoReplyForm(missingReplySection.body, referral);

  const riskSection = operationSection("Risk and suitability", disclosure);
  riskSection.body.append(
    recordItem({
      title: referral.risk_category || "Risk pending",
      status: referral.risk_present ? "needs_clinical_review" : referral.risk_category ? "ok" : "open",
      body: referral.risk_present ? "Risk signal requires clinical review before matching." : "No elevated risk recorded in the current referral record.",
      meta: [referral.urgency || "urgency pending"],
    }),
  );

  const taskSection = operationSection("Review tasks", disclosure);
  taskSection.section.id = "referral-review-tasks";
  const openReviewTasks = (referral.review_tasks || []).filter((task) => task.status === "open");
  renderSimpleList(taskSection.body, openReviewTasks, "No open review tasks for this referral.", reviewTaskCard);

  const outputSection = operationSection("Agent outputs", { collapsible: true, open: false });
  renderAgentOutputs(outputSection.body, workbenchState.agent_outputs || []);

  const matchSection = operationSection("Deterministic match", disclosure);
  renderMatchSummary(matchSection.body, referral.match_summary);

  const appointmentSection = operationSection("Appointment proposals", disclosure);
  appointmentSection.section.id = "referral-appointments";
  const intakeSection = operationSection("Intake status", disclosure);
  intakeSection.section.id = "referral-intake";
  const briefSection = operationSection("Prep briefs", disclosure);
  briefSection.section.id = "referral-prep";
  const documentSection = operationSection("Documents", disclosure);
  documentSection.section.id = "referral-documents";
  renderReferralDocuments(documentSection.body, referral);
  const communicationSection = operationSection("Communication thread", { collapsible: true, open: false });
  renderCommunicationThread(communicationSection.body, referral);

  const raw = document.createElement("details");
  raw.className = "handoff";
  const rawSummary = document.createElement("summary");
  rawSummary.textContent = "Raw referral source";
  const rawText = document.createElement("pre");
  rawText.textContent = referral.raw_text || "";
  raw.append(rawSummary, rawText);

  const activitySection = operationSection("Activity timeline", { collapsible: true, open: false });
  renderActivityTimeline(activitySection.body, workbenchState.activity || []);

  if (emailWorkflow) {
    const emailPanel = renderEmailWorkflowPanel(referral, emailWorkflow);
    const technical = document.createElement("details");
    technical.className = "handoff";
    const technicalSummary = document.createElement("summary");
    technicalSummary.textContent = "Technical details";
    technical.append(
      technicalSummary,
      outputSection.section,
      matchSection.section,
      documentSection.section,
      communicationSection.section,
      raw,
      activitySection.section,
    );
    referralDetail.append(
      header,
      emailPanel,
      missingReplySection.section,
      appointmentSection.section,
      intakeSection.section,
      briefSection.section,
      technical,
    );
  } else {
    referralDetail.append(
      header,
      progress,
      readinessSection.section,
      fieldsSection.section,
      missingSection.section,
      missingReplySection.section,
      riskSection.section,
      taskSection.section,
      outputSection.section,
      matchSection.section,
      appointmentSection.section,
      documentSection.section,
      communicationSection.section,
      intakeSection.section,
      briefSection.section,
      raw,
      activitySection.section,
    );
  }
  await loadReferralOperations(referral.id, appointmentSection.body, intakeSection.body, briefSection.body, referral);
}

async function loadScheduling() {
  if (!schedulingList) return;
  const response = await fetch("/api/appointments");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  latestAppointments = data.appointments || [];
  renderSchedulingList();
  renderTherapistList();
  renderSelectedTherapist();
}

function renderSchedulingList() {
  if (!schedulingList) return;
  const referralsById = new Map((latestReferrals || []).map((referral) => [referral.id, referral]));
  const appointments = (latestAppointments || [])
    .filter((appointment) => appointment.status !== "cancelled")
    .slice(0, 12);

  renderCollection(schedulingList, appointments, "No appointments scheduled yet.", (appointment) =>
    appointmentCard(appointment, referralsById),
  );
}

function appointmentCard(appointment, referralsById) {
  const referral = appointment.referral_id ? referralsById.get(appointment.referral_id) : null;
  const title = referral?.patient_name || (appointment.referral_id ? `Referral ${shortId(appointment.referral_id)}` : "Appointment");
  const starts = appointment.starts_at ? formatDate(appointment.starts_at) : "Time pending";
  const ends = appointment.ends_at ? formatDate(appointment.ends_at) : "";
  const body = ends ? `${starts} to ${ends}` : starts;
  const item = recordItem({
    title,
    status: appointment.status,
    body,
    meta: [
      referral?.status_label || referral?.status,
      referral?.next_action_label,
      appointment.therapist_id ? `therapist ${shortId(appointment.therapist_id)}` : null,
      appointment.source,
    ],
  });
  if (appointment.referral_id) {
    item.classList.add("clickable");
    item.addEventListener("click", async () => {
      navigate("/workbench");
      await loadReferralDetail(appointment.referral_id);
    });
  }
  return item;
}

function makeReferralDraggable(item, referral) {
  if (!referral?.id) return;
  item.draggable = true;
  item.classList.add("draggable-record");
  item.addEventListener("dragstart", (event) => {
    setDragPayload(event, { type: "referral", referral_id: referral.id });
  });
  item.addEventListener("dragend", () => item.classList.remove("is-dragging"));
}

function makeAppointmentDraggable(item, appointment) {
  if (!appointment?.id) return;
  item.draggable = true;
  item.classList.add("draggable-record");
  item.addEventListener("dragstart", (event) => {
    setDragPayload(event, { type: "appointment", appointment_id: appointment.id });
  });
  item.addEventListener("dragend", () => item.classList.remove("is-dragging"));
}

function setDragPayload(event, payload) {
  const text = JSON.stringify(payload);
  event.currentTarget.classList.add("is-dragging");
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("application/json", text);
  event.dataTransfer.setData("text/plain", text);
}

function parseDragPayload(event) {
  const raw = event.dataTransfer.getData("application/json") || event.dataTransfer.getData("text/plain");
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function createDraggedAppointmentProposal(referralId, therapistId, slot) {
  const response = await fetch("/api/appointments/proposals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      referral_id: referralId,
      therapist_id: therapistId,
      starts_at: slot.starts_at,
      ends_at: slot.ends_at,
    }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not create slot approval.");
    return;
  }
  setStatus("completed", "Slot proposal created and queued for admin approval.");
  await refreshProductWorkspace();
  if (selectedReferralId === referralId) await loadReferralDetail(referralId);
}

async function requestDraggedAppointmentReschedule(appointmentId, slot) {
  const response = await fetch(`/api/appointments/${encodeURIComponent(appointmentId)}/reschedule-request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      starts_at: slot.starts_at,
      ends_at: slot.ends_at,
      reason: "Appointment reschedule requested from therapist calendar drag and drop.",
    }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not request appointment reschedule.");
    return;
  }
  setStatus("completed", "Reschedule request queued for admin approval.");
  await refreshProductWorkspace();
  const appointment = (latestAppointments || []).find((item) => item.id === appointmentId);
  if (appointment?.referral_id && selectedReferralId === appointment.referral_id) {
    await loadReferralDetail(appointment.referral_id);
  }
}

async function loadReferralOperations(referralId, appointmentBody, intakeBody, briefBody, referral = null) {
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
    renderIntakeSubmissionReviews(intakeBody, referral?.review_tasks || [], data);
    renderIntakeWorkspace(intakeWorkspace, data, true);
    renderPrepBriefs(briefBody, data.prep_briefs || []);
  }
}

function renderEmailWorkflowPanel(referral, workflow) {
  const section = document.createElement("section");
  section.className = "operation-section email-workflow-panel";
  section.id = "email-workflow-panel";

  const title = document.createElement("div");
  title.className = "section-heading";
  title.append(
    heading("Email referral"),
    metaRow([workflow.status?.replaceAll("_", " "), workflow.next_action_label]),
  );

  const steps = [
    ["email_received", "Email received"],
    ["facts_extracted", "Facts extracted"],
    ["first_response_prepared", "First response prepared"],
    ["waiting_for_reply", "Waiting for reply"],
    ["appointment_confirmation", "Appointment confirmation"],
    ["confirmed", "Confirmed"],
  ];
  const progress = document.createElement("section");
  progress.className = "progress-path";
  const currentIndex = steps.findIndex(([key]) => !workflow.progress?.[key]);
  steps.forEach(([key, label], index) => {
    const item = document.createElement("div");
    item.className = "progress-step";
    const complete = Boolean(workflow.progress?.[key]);
    item.dataset.state = complete ? "complete" : index === currentIndex ? "current" : "pending";
    const marker = document.createElement("span");
    marker.className = "progress-marker";
    marker.textContent = complete ? "OK" : String(index + 1);
    const text = document.createElement("strong");
    text.textContent = label;
    item.append(marker, text);
    progress.appendChild(item);
  });

  const facts = workflow.facts || {};
  const factsGrid = document.createElement("div");
  factsGrid.className = "workbench-state-grid";
  factsGrid.append(
    keyValue("Patient", facts.patient_name || "Missing"),
    keyValue("DOB", facts.date_of_birth || "Missing"),
    keyValue("Contact", [facts.contact_email, facts.contact_phone].filter(Boolean).join(" | ") || "Missing"),
    keyValue("Insurer", facts.insurer || "Missing"),
    keyValue("Referrer", facts.referring_entity || "Not recorded"),
    keyValue("Held appointment", workflow.held_appointment?.starts_at ? formatDate(workflow.held_appointment.starts_at) : "Not held"),
  );

  const body = document.createElement("div");
  body.className = "email-workflow-body";
  body.append(progress, factsGrid);

  if (workflow.no_match) {
    body.appendChild(recordItem({
      title: "Therapist matching needs review",
      status: "blocked",
      body: workflow.no_match.label,
      meta: [`${workflow.no_match.excluded_count || 0} excluded therapists`],
    }));
  }

  if (workflow.held_appointment) {
    body.appendChild(recordItem({
      title: "Held appointment",
      status: workflow.held_appointment.status,
      body: `${formatDate(workflow.held_appointment.starts_at)} to ${formatDate(workflow.held_appointment.ends_at)}`,
      meta: [
        workflow.held_appointment.therapist_id ? `therapist ${shortId(workflow.held_appointment.therapist_id)}` : null,
        workflow.held_appointment.google_calendar_event_id ? "Google Calendar linked" : "local hold",
      ],
    }));
  }

  if (workflow.draft) {
    const draft = recordItem({
      title: workflow.draft.subject || "Patient email draft",
      status: workflow.draft.status,
      body: workflow.draft.body,
      meta: [
        workflow.draft.recipient_email,
        workflow.gmail?.message_id ? `gmail ${shortId(workflow.gmail.message_id)}` : null,
        workflow.gmail?.sent_at ? `sent ${formatDate(workflow.gmail.sent_at)}` : null,
      ],
    });
    body.appendChild(draft);
  }

  const taskWrap = document.createElement("div");
  taskWrap.id = "email-workflow-review-tasks";
  renderSimpleList(taskWrap, workflow.review_tasks || [], "No email workflow review tasks are open.", reviewTaskCard);
  body.appendChild(taskWrap);

  section.append(title, body);
  return section;
}

function renderMissingInfoReplyForm(container, referral) {
  const form = document.createElement("form");
  form.className = "inline-form missing-reply-form";
  form.append(
    formField("Patient name", "patient_name", referral.patient_name || ""),
    formField("Email", "contact_email", referral.contact_email || ""),
    formField("Phone", "contact_phone", referral.contact_phone || ""),
    formField("Date of birth", "date_of_birth", referral.date_of_birth || ""),
    formField("Insurer", "insurer", referral.insurer || ""),
    formField("Referring entity", "referring_entity", referral.referring_entity || ""),
  );
  const notes = formField("Reply notes", "notes", "", true);
  form.appendChild(notes);
  const actions = document.createElement("div");
  actions.className = "actions tight";
  actions.appendChild(actionButton("Apply patient reply", () => recordMissingInfoReply(referral.id, form)));
  form.appendChild(actions);
  container.appendChild(form);
}

function formField(labelText, name, value = "", multiline = false) {
  const label = document.createElement("label");
  label.className = "field";
  const caption = document.createElement("span");
  caption.textContent = labelText;
  const input = document.createElement(multiline ? "textarea" : "input");
  input.name = name;
  input.value = value || "";
  if (multiline) input.rows = 3;
  label.append(caption, input);
  return label;
}

function renderWorkbenchSummary(referral, state) {
  const header = document.createElement("div");
  header.className = "workbench-summary";

  const titleBlock = document.createElement("div");
  titleBlock.className = "workbench-title";
  titleBlock.append(
    heading(referral.patient_name || "Unnamed referral"),
    metaRow([
      state.stage_label || referral.status_label || referral.status,
      referral.source_channel,
      referral.urgency || "urgency pending",
    ]),
  );

  const stateGrid = document.createElement("div");
  stateGrid.className = "workbench-state-grid";
  stateGrid.append(
    keyValue("Stage", state.stage_label || referral.status_label || referral.status || "Pending"),
    keyValue("Blocker", state.primary_blocker?.label || "No active blocker"),
    keyValue("Owner", state.owner || "Admin"),
    keyValue("Matched therapist", matchedTherapistLabel(referral)),
    keyValue("Next action", state.primary_action_label || referral.next_action_label || "Review referral"),
    keyValue("Risk", referral.risk_category || (referral.risk_present ? "Review needed" : "Not flagged")),
    keyValue("Open gates", String((referral.review_tasks || []).filter((task) => task.status === "open").length)),
  );

  const primary = workbenchPrimaryButton(referral, state);
  const actions = document.createElement("div");
  actions.className = "workbench-actions";
  if (primary) actions.appendChild(primary);

  const secondaryActions = secondaryWorkbenchActions(referral, state);
  if (secondaryActions.length) {
    const more = document.createElement("details");
    more.className = "more-actions";
    const summary = document.createElement("summary");
    summary.textContent = "More actions";
    const body = document.createElement("div");
    body.className = "actions tight";
    secondaryActions.forEach((button) => body.appendChild(button));
    more.append(summary, body);
    actions.appendChild(more);
  }

  const blockerRow = metaRow((state.blockers || []).slice(0, 4).map((blocker) => blocker.label));
  blockerRow.classList.add("workbench-blockers");

  header.append(titleBlock, stateGrid, blockerRow, actions);
  if (lastReviewOutcome?.referralId === referral.id) {
    const outcome = document.createElement("p");
    outcome.className = "workbench-outcome";
    outcome.textContent = lastReviewOutcome.message;
    header.appendChild(outcome);
  }
  return header;
}

function matchedTherapistLabel(referral) {
  const match = (referral.match_summary?.ranked_matches || [])[0];
  if (!match) return "Not matched";
  return match.name || (match.therapist_id ? `Therapist ${shortId(match.therapist_id)}` : "Not matched");
}

function renderReferralProgress(referral, state = {}) {
  const facts = state.progress || {};
  const steps = [
    { id: "captured", label: "Captured", complete: facts.captured !== false },
    { id: "reviewed", label: "Reviewed", complete: Boolean(facts.reviewed) },
    { id: "matched", label: "Matched", complete: Boolean(facts.matched) },
    { id: "contacted", label: "Contacted", complete: Boolean(facts.contacted) },
    { id: "appointment_confirmed", label: "Appointment confirmed", complete: Boolean(facts.appointment_confirmed) },
    { id: "intake_complete", label: "Intake complete", complete: Boolean(facts.intake_complete) },
    { id: "prep_brief_ready", label: "Prep brief ready", complete: Boolean(facts.prep_brief_ready) },
  ];
  const currentIndex = steps.findIndex((step) => !step.complete);
  const wrapper = document.createElement("section");
  wrapper.className = "progress-path";
  wrapper.dataset.status = referral.status || "";
  steps.forEach((step, index) => {
    const item = document.createElement("div");
    item.className = "progress-step";
    const isComplete = step.complete;
    item.dataset.state = isComplete ? "complete" : index === currentIndex ? "current" : "pending";
    const marker = document.createElement("span");
    marker.className = "progress-marker";
    marker.textContent = isComplete ? "OK" : String(index + 1);
    const label = document.createElement("strong");
    label.textContent = step.label;
    item.append(marker, label);
    wrapper.appendChild(item);
  });
  return wrapper;
}

function workbenchPrimaryButton(referral, state) {
  const primaryAction = state.primary_action || referral.next_action;
  const allowedActions = state.allowed_actions || [];
  const actionId = primaryAction === "revise_agent_output" ? allowedActions[0] : primaryAction;
  if (!actionId || ["ready", "closed", "wait_patient_reply", "wait_extraction"].includes(actionId)) return null;
  const action = workbenchActionDefinitions(referral)[actionId];
  if (!action) return null;
  const button = actionButton(state.primary_action_label || action.label, action.handler);
  button.classList.remove("secondary", "compact");
  return button;
}

function secondaryWorkbenchActions(referral, state) {
  const definitions = workbenchActionDefinitions(referral);
  const primaryAction = state.primary_action === "revise_agent_output" ? (state.allowed_actions || [])[0] : state.primary_action;
  const allowed = new Set(state.email_workflow ? [...(state.allowed_actions || [])] : [...(state.allowed_actions || []), ...defaultWorkbenchActionIds()]);
  allowed.delete(primaryAction);
  return [...allowed]
    .map((actionId) => definitions[actionId])
    .filter(Boolean)
    .map((action) => actionButton(action.label, action.handler));
}

function defaultWorkbenchActionIds() {
  return [
    "draft_missing_info",
    "record_missing_reply",
    "clinical_review",
    "suitability_review",
    "duplicate_review",
    "run_match",
    "propose_slots",
    "draft_first_contact",
    "start_intake",
    "draft_intake_packet",
    "draft_intake_reminder",
    "generate_prep_brief",
  ];
}

function workbenchActionDefinitions(referral) {
  return {
    review_gate: { label: "Open review task", handler: () => scrollToDetailSection(document.getElementById("email-workflow-review-tasks") ? "email-workflow-review-tasks" : "referral-review-tasks") },
    review_referral: { label: "Review referral", handler: () => scrollToDetailSection(document.getElementById("email-workflow-review-tasks") ? "email-workflow-review-tasks" : "referral-review-tasks") },
    review_missing_info: { label: "Resolve missing information", handler: () => draftMissingInfo(referral.id) },
    draft_missing_info: { label: "Draft missing info", handler: () => draftMissingInfo(referral.id) },
    record_missing_reply: { label: "Record missing reply", handler: () => scrollToDetailSection("referral-missing-reply") },
    retry_extraction: { label: "Retry extraction", handler: () => retryReferralExtraction(referral.id) },
    continue_email_workflow: { label: "Continue from email", handler: () => continueEmailWorkflow(referral.id) },
    review_first_response: { label: "Review first response", handler: () => scrollToDetailSection("email-workflow-review-tasks") },
    send_email: { label: "Send email to patient", handler: () => scrollToDetailSection("email-workflow-review-tasks") },
    sync_replies: { label: "Sync replies", handler: () => syncGmailForReferral(referral.id) },
    resolve_reply: { label: "Resolve patient reply", handler: () => scrollToDetailSection("email-workflow-review-tasks") },
    resolve_match: { label: "Resolve therapist match", handler: () => scrollToDetailSection("email-workflow-panel") },
    clinical_review: { label: "Clinical review", handler: () => requestClinicalReview(referral.id) },
    suitability_review: { label: "Suitability review", handler: () => requestSuitabilityReview(referral.id) },
    duplicate_review: { label: "Duplicate review", handler: () => requestDuplicateReview(referral.id) },
    run_matching: { label: "Run matching", handler: () => runReferralMatch(referral.id) },
    run_match: { label: "Run matching", handler: () => runReferralMatch(referral.id) },
    approve_match: { label: "Open match approval", handler: () => scrollToDetailSection("referral-review-tasks") },
    approve_slots: { label: "Open slot approval", handler: () => scrollToDetailSection("referral-review-tasks") },
    approve_contact: { label: "Open contact approval", handler: () => scrollToDetailSection("referral-review-tasks") },
    propose_slots: { label: "Propose slots", handler: () => proposeReferralSlots(referral.id) },
    draft_first_contact: { label: "Draft first contact", handler: () => draftFirstContact(referral.id) },
    record_patient_reply: { label: "Record patient reply", handler: () => scrollToDetailSection("referral-appointments") },
    confirm_appointment: { label: "Open appointment review", handler: () => scrollToDetailSection("referral-review-tasks") },
    start_intake: { label: "Start intake", handler: () => startReferralIntake(referral.id) },
    complete_intake: { label: "Open intake", handler: () => scrollToDetailSection("referral-intake") },
    draft_intake_packet: { label: "Draft intake packet", handler: () => draftIntakePacket(referral.id) },
    draft_intake_reminder: { label: "Draft reminder", handler: () => draftIntakeReminder(referral.id) },
    generate_prep_brief: { label: "Prep brief", handler: () => generateReferralPrepBrief(referral.id) },
  };
}

function scrollToDetailSection(id) {
  const element = document.getElementById(id);
  if (!element) return;
  element.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderAgentOutputs(container, outputs) {
  renderSimpleList(container, outputs, "No agent outputs prepared yet.", (output) => {
    const item = recordItem({
      title: output.title || output.type?.replaceAll("_", " ") || "Agent output",
      status: output.status || "draft",
      body: output.body,
      meta: [
        output.type ? output.type.replaceAll("_", " ") : null,
        output.review_status ? `review ${output.review_status.replaceAll("_", " ")}` : null,
        output.updated_at ? formatDate(output.updated_at) : null,
      ],
    });
    if (output.review_reason) {
      item.appendChild(keyValue("Reviewer instruction", output.review_reason));
    }
    return item;
  });
}

function renderActivityTimeline(container, activity) {
  renderSimpleList(container, activity, "No activity recorded for this referral yet.", (item) =>
    recordItem({
      title: item.title,
      status: item.status || item.type,
      body: item.body,
      meta: [
        ...(item.meta || []),
        item.type ? item.type.replaceAll("_", " ") : null,
        formatDate(item.created_at),
      ],
    }),
  );
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

async function recordMissingInfoReply(referralId, form = null) {
  if (!form) {
    scrollToDetailSection("referral-missing-reply");
    return;
  }
  const formData = new FormData(form);
  const updates = {};
  ["patient_name", "contact_email", "insurer", "contact_phone", "date_of_birth", "referring_entity"].forEach((key) => {
    const value = String(formData.get(key) || "").trim();
    if (value) updates[key] = value;
  });
  const notes = String(formData.get("notes") || "").trim();
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

async function retryReferralExtraction(referralId) {
  const response = await fetch(`/api/referrals/${referralId}/retry-extraction`, { method: "POST" });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not retry extraction.");
    return;
  }
  resetRunState();
  jobIdLabel.textContent = body.job_id;
  setActiveJob(body.job_id);
  setStatus(body.status, `${statusLabel(body.status)} - ${shortId(body.job_id)}`);
  openEventStream(body.job_id, body.events_url);
  startPolling(body.status_url);
  await loadReferralDetail(referralId);
}

async function continueEmailWorkflow(referralId) {
  const response = await fetch(`/api/referrals/${referralId}/continue-email-workflow`, { method: "POST" });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not continue the email workflow.");
    return;
  }
  const result = body.result || {};
  const statusText = result.reason ? `${body.status}: ${result.reason}` : `Email workflow ${body.status || "continued"}`;
  setStatus(body.status === "blocked" ? "failed" : "completed", statusText);
  if (body.status === "blocked" || body.status === "partial") {
    window.alert(statusText);
  }
  await refreshProductWorkspace();
  await loadReferralDetail(referralId);
}

async function syncGmailForReferral(referralId) {
  await syncGmailInbox();
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

async function uploadIntakeTemplateFile(templateId, itemKey) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".txt,.pdf,.docx,.csv,.xlsx,.json";
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    const payload = new FormData();
    payload.append("file", file);
    const response = await fetch(`/api/intake/templates/${encodeURIComponent(templateId)}/items/${encodeURIComponent(itemKey)}/file`, {
      method: "POST",
      body: payload,
    });
    const body = await readResponseBody(response);
    if (!response.ok) {
      window.alert(body.detail || "Could not upload template file.");
      return;
    }
    if (selectedReferralId) await loadReferralDetail(selectedReferralId);
    await loadIntakeTracker();
  });
  input.click();
}

function openDocumentDownload(documentId) {
  if (!documentId) return;
  window.open(`/api/documents/${encodeURIComponent(documentId)}/download`, "_blank", "noreferrer");
}

async function loadReviewTasks() {
  const response = await fetch("/api/review-tasks?status=open");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  latestReviewTasks = data.tasks || [];
  metricReviews.textContent = latestReviewTasks.length;

  renderCollection(reviewTaskList, latestReviewTasks, "No open review tasks.", reviewTaskCard);
  if (overviewReviewList) {
    renderCollection(overviewReviewList, latestReviewTasks.slice(0, 4), "No open review tasks.", (task) =>
      recordItem({
        title: friendlyTaskType(task.task_type),
        status: task.status,
        body: task.reason,
        meta: [task.payload_key, task.referral_id ? `referral ${shortId(task.referral_id)}` : "no referral"],
      }),
    );
  }

  renderOverviewActionQueue();
}

async function loadEscalations() {
  if (!escalationList) return;
  const response = await fetch("/api/escalations");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  renderCollection(escalationList, data.items || [], "No escalated referrals or tasks.", escalationCard);
}

function escalationCard(item) {
  const referral = item.referral || {};
  const task = item.task || {};
  const card = recordItem({
    title: referral.patient_name || friendlyTaskType(task.task_type) || "Escalation",
    status: item.status || task.status || referral.status,
    body: item.reason || task.reason || "Escalation requires admin recovery.",
    meta: [
      item.type === "review_task" ? friendlyTaskType(task.task_type) : "Referral escalation",
      referral.id ? `referral ${shortId(referral.id)}` : null,
      item.updated_at ? formatDate(item.updated_at) : null,
    ],
  });
  if (referral.id) {
    const actions = document.createElement("div");
    actions.className = "actions tight";
    actions.appendChild(actionButton("Open referral", () => openReferralWorkbench(referral.id)));
    card.appendChild(actions);
  }
  return card;
}

async function resetCleanDemoPath() {
  const response = await fetch("/api/demo/clean-referral/reset", { method: "POST" });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not reset the clean demo path.");
    return;
  }
  const referralId = body.referral?.id;
  setStatus("completed", "Clean demo referral reset.");
  await refreshProductWorkspace();
  if (referralId) {
    navigate("/workbench");
    await loadReferralDetail(referralId);
  }
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
      navigate("/workbench");
      await loadReferralDetail(referral.id);
    });
    return item;
  });
}

function reviewTaskCard(task) {
  const item = recordItem({
    title: friendlyTaskType(task.task_type),
    status: task.status,
    body: task.reason,
    meta: [
      task.payload_key,
      task.referral_id ? `referral ${shortId(task.referral_id)}` : "no referral",
      task.reviewed_at ? `reviewed ${formatDate(task.reviewed_at)}` : null,
    ],
  });

  if (task.rejection_reason) {
    item.appendChild(keyValue(task.status === "changes_requested" ? "Requested change" : "Decision note", task.rejection_reason));
  }
  if (task.provider_error) {
    item.appendChild(keyValue("Provider error", task.provider_error));
  }
  if (task.source_payload) {
    appendAttachmentSummary(item, task.source_payload);
  }

  if (task.draft_text) {
    const editor = document.createElement("textarea");
    editor.rows = 5;
    editor.value = task.draft_text;
    editor.dataset.taskEditor = task.id;
    item.appendChild(editor);
  }

  const actions = document.createElement("div");
  actions.className = "actions tight";
  if (task.referral_id && task.referral_id !== selectedReferralId) {
    actions.appendChild(actionButton("Open referral", () => openReferralWorkbench(task.referral_id)));
  }
  if (task.status !== "open") {
    if (task.status === "changes_requested" && task.referral_id === selectedReferralId) {
      actions.appendChild(actionButton("Open revise path", () => scrollToDetailSection("referral-review-tasks")));
    }
    item.appendChild(actions);
    return item;
  }
  if (task.task_type === "intake_submission_review" && task.referral_id) {
    actions.appendChild(actionButton("Open intake review", () => openReferralWorkbench(task.referral_id)));
    item.appendChild(actions);
    return item;
  }
  const approveLabel =
    task.task_type === "send_approval"
      ? "Send email to patient"
      : task.task_type === "appointment_confirmation_approval"
        ? "Create Google Calendar event"
        : "Approve";
  const approve = actionButton(approveLabel, () => submitReviewAction(task.id, "approve"));
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
    setStatus("failed", "Review action failed");
    window.alert(body.detail || "Review action failed.");
    return;
  }
  if (body.message) {
    const referralId = body.referral?.id || body.task?.referral_id || selectedReferralId;
    lastReviewOutcome = referralId ? { referralId, message: body.message } : null;
    setStatus("completed", body.message);
  }
  await refreshProductWorkspace();
  const detailReferralId = body.referral?.id || selectedReferralId;
  if (detailReferralId) {
    if (body.referral?.id && window.location.pathname === "/review") navigate("/workbench");
    await loadReferralDetail(detailReferralId);
  }
  if (body.resumed_job) {
    resetRunState();
    jobIdLabel.textContent = body.resumed_job.job_id;
    setActiveJob(body.resumed_job.job_id);
    setStatus(body.resumed_job.status, `${statusLabel(body.resumed_job.status)} - ${shortId(body.resumed_job.job_id)}`);
    openEventStream(body.resumed_job.job_id, `/api/events/${body.resumed_job.job_id}`);
    startPolling(`/api/status/${body.resumed_job.job_id}`);
  }
}

async function loadTherapists() {
  const [response, capacityResponse] = await Promise.all([
    fetch("/api/therapists"),
    fetch("/api/therapists/calendar-capacity"),
  ]);
  if (!response.ok) return;
  const data = await readResponseBody(response);
  latestTherapistCalendarCapacity = capacityResponse.ok ? await readResponseBody(capacityResponse) : null;
  latestTherapists = data.therapists || [];
  if (!selectedTherapistId && latestTherapists.length) {
    selectedTherapistId = latestTherapists[0].id;
  }
  renderTherapistMetrics();
  renderTherapistList();
  renderSelectedTherapist();
}

function buildAvailabilityBlocks(formData) {
  const blocks = [];
  for (const index of [1, 2]) {
    const weekday = String(formData.get(`availability_weekday_${index}`) || "");
    const start = String(formData.get(`availability_start_${index}`) || "");
    const end = String(formData.get(`availability_end_${index}`) || "");
    const modality = String(formData.get(`availability_modality_${index}`) || "online");
    if (weekday && start && end) {
      blocks.push({ weekday, start, end, modality });
    }
  }
  return blocks;
}

function renderTherapistMetrics() {
  const activeTherapists = latestTherapists.filter((therapist) => therapist.active);
  const summaries = activeTherapists.map(therapistCapacitySummary);
  const remaining = roundHours(summaries.reduce((total, summary) => total + Math.max(0, summary.remaining), 0));
  const fullyBooked = summaries.filter((summary) => summary.active && summary.capacity > 0 && summary.remaining <= 0).length;
  const syncIssues = summaries.filter((summary) => ["failed", "sync_issue"].includes(summary.syncStatus)).length
    + (latestTherapistCalendarCapacity?.unmatched_calendar_events || []).length
    + (latestTherapistCalendarCapacity?.malformed_calendar_events || []).length;
  const incomplete = activeTherapists.filter((therapist) => therapistMatchingDataIncomplete(therapist)).length;

  if (metricTherapists) metricTherapists.textContent = activeTherapists.length;
  if (metricTherapistActive) metricTherapistActive.textContent = activeTherapists.length;
  if (metricTherapistCapacity) metricTherapistCapacity.textContent = remaining;
  if (metricTherapistFull) metricTherapistFull.textContent = fullyBooked;
  if (metricTherapistMissing) metricTherapistMissing.textContent = syncIssues;
  if (metricTherapistIncomplete) metricTherapistIncomplete.textContent = incomplete;
}

function renderTherapistList() {
  if (!therapistList) return;
  renderCollection(therapistList, latestTherapists, "No therapist profiles found.", (therapist) => {
    const summary = therapistCapacitySummary(therapist);
    const nextSlot = nextAvailabilityLabel(therapist);
    const item = recordItem({
      title: therapist.name,
      status: therapist.active ? summary.syncStatus || "active" : "inactive",
      body: therapist.specialties.join(", ") || "No specialties recorded",
      meta: [
        `${summary.used}/${summary.capacity}h contact`,
        `${summary.remaining}h left`,
        nextSlot,
        summary.syncStatus ? `sync ${summary.syncStatus.replaceAll("_", " ")}` : null,
        therapist.languages.join(", "),
        therapist.modalities.join(", "),
      ],
    });
    item.classList.add("clickable", "therapist-card");
    item.classList.toggle("is-selected", therapist.id === selectedTherapistId);
    item.addEventListener("click", () => {
      selectedTherapistId = therapist.id;
      selectedCalendarTherapistFilter = therapist.id;
      therapistCalendarWeekPinnedByUser = false;
      renderTherapistList();
      renderSelectedTherapist();
    });
    return item;
  });
}

function renderSelectedTherapist() {
  if (!therapistDetail) return;
  const therapist = latestTherapists.find((item) => item.id === selectedTherapistId) || latestTherapists[0];
  therapistDetail.replaceChildren();
  if (!therapist) {
    therapistDetail.appendChild(emptyState("Select a therapist to review capacity, availability, assigned referrals, and matching history."));
    return;
  }
  selectedTherapistId = therapist.id;
  if (!selectedCalendarTherapistFilter) selectedCalendarTherapistFilter = therapist.id;
  if (!therapistCalendarWeekStart) therapistCalendarWeekStart = startOfWeek(new Date());
  const summary = therapistCapacitySummary(therapist);
  const calendarSummary = therapistCalendarSummary(therapist.id);
  const assignedReferrals = referralsForTherapist(therapist.id);

  const header = document.createElement("div");
  header.className = "therapist-profile-header";
  header.append(
    heading(therapist.name),
    metaRow([
      therapist.active ? "active" : "inactive",
      therapist.email,
      `${summary.remaining}h remaining`,
      calendarSummary?.last_sync ? `synced ${formatDate(calendarSummary.last_sync)}` : null,
    ]),
  );

  const profileGrid = document.createElement("div");
  profileGrid.className = "detail-card-grid";
  profileGrid.append(
    detailCard("Profile", [
      keyValue("Email", therapist.email || "Not recorded"),
      keyValue("Status", therapist.active ? "Active" : "Inactive"),
      keyValue("Default mode", therapist.modalities.join(", ") || "Not recorded"),
    ]),
    detailCard("Matching criteria", [
      keyValue("Specialties", therapist.specialties.join(", ") || "Missing"),
      keyValue("Languages", therapist.languages.join(", ") || "Missing"),
      keyValue("Insurers", therapist.insurers.join(", ") || "Missing"),
    ]),
    detailCard("Calendar capacity", [
      keyValue("Sync status", (calendarSummary?.sync_status || summary.syncStatus || "manual").replaceAll("_", " ")),
      keyValue("Weekly cap", `${summary.capacity} hours`),
      keyValue("Patient contact used", `${summary.used} hours`),
      keyValue("Remaining", `${summary.remaining} hours`),
    ]),
  );

  const calendarSection = operationSection("Calendar capacity");
  renderTherapistWeekCalendar(calendarSection.body, therapist);

  const assignedSection = operationSection("Assigned patients and referrals");
  renderSimpleList(
    assignedSection.body,
    assignedReferrals,
    "No assigned referrals found for this therapist.",
    (referral) => {
      const item = referralCard(referral, true);
      makeReferralDraggable(item, referral);
      item.appendChild(keyValue("First session", firstSessionForReferral(referral.id, therapist.id)));
      return item;
    },
  );

  const syncSection = operationSection("Calendar sync issues");
  renderSimpleList(
    syncSection.body,
    calendarIssuesForTherapist(therapist.id, calendarSummary),
    "No calendar sync issues detected for this therapist.",
    calendarIssueCard,
  );

  const historySection = operationSection("Recent matching history");
  const recommended = latestReferrals.filter((referral) => topMatchTherapistId(referral) === therapist.id).length;
  const approved = latestReferrals.filter((referral) => topMatchTherapistId(referral) === therapist.id && ["match_approved", "slot_options_ready", "awaiting_patient_contact", "contact_sent", "awaiting_patient_reply", "appointment_confirmed", "intake_packet_sent", "intake_incomplete", "intake_complete", "prep_brief_ready", "first_session_ready"].includes(referral.status)).length;
  historySection.body.append(
    recordItem({
      title: "Matching outcomes",
      status: recommended ? "active" : "configured",
      body: `${recommended} recommendations, ${approved} approved or progressed.`,
      meta: ["derived from referral match summaries"],
    }),
  );

  therapistDetail.append(
    header,
    profileGrid,
    calendarSection.section,
    assignedSection.section,
    syncSection.section,
    historySection.section,
  );
}

function therapistCapacitySummary(therapist) {
  const calendarSummary = therapistCalendarSummary(therapist.id);
  const capacity = Number(calendarSummary?.weekly_patient_contact_cap_hours ?? 20);
  const used = Number(calendarSummary?.weekly_patient_contact_hours_used ?? 0);
  const remaining = Number(calendarSummary?.weekly_patient_contact_hours_remaining ?? Math.max(0, capacity - referralsForTherapist(therapist.id).length));
  const assigned = Number(calendarSummary?.active_appointments?.length ?? referralsForTherapist(therapist.id).length);
  return {
    active: therapist.active,
    capacity,
    used,
    assigned,
    remaining,
    syncStatus: calendarSummary?.sync_status || (latestTherapistCalendarCapacity?.google_enabled ? "ready" : "manual"),
  };
}

function therapistMatchingDataIncomplete(therapist) {
  return !(therapist.specialties || []).length
    || !(therapist.languages || []).length
    || !(therapist.modalities || []).length;
}

function therapistCalendarSummary(therapistId) {
  return (latestTherapistCalendarCapacity?.therapists || []).find((item) => item.therapist_id === therapistId) || null;
}

function referralsForTherapist(therapistId) {
  const appointmentReferralIds = new Set(
    (latestAppointments || [])
      .filter((appointment) => appointment.therapist_id === therapistId && appointment.referral_id && appointment.status !== "cancelled")
      .map((appointment) => appointment.referral_id),
  );
  return (latestReferrals || []).filter((referral) => appointmentReferralIds.has(referral.id) || topMatchTherapistId(referral) === therapistId);
}

function appointmentsForTherapist(therapistId) {
  return (latestAppointments || []).filter(
    (appointment) => appointment.therapist_id === therapistId && appointment.status !== "cancelled",
  );
}

function topMatchTherapistId(referral) {
  return (referral.match_summary?.ranked_matches || [])[0]?.therapist_id || null;
}

function firstSessionForReferral(referralId, therapistId) {
  const appointment = (latestAppointments || []).find(
    (item) => item.referral_id === referralId && item.therapist_id === therapistId && item.status === "confirmed",
  );
  return appointment?.starts_at ? formatDate(appointment.starts_at) : "Not confirmed";
}

function nextAvailabilityLabel(therapist) {
  const calendarSummary = therapistCalendarSummary(therapist.id);
  if (calendarSummary?.next_available_slot?.starts_at) {
    return `Next: ${formatDate(calendarSummary.next_available_slot.starts_at)}`;
  }
  if (latestTherapistCalendarCapacity?.provider_error) return "Calendar unavailable";
  const block = (therapist.availability_blocks || [])[0];
  if (!block) return "Next: default 08:00";
  return `Next: ${block.weekday || "manual"} ${block.start || ""}`;
}

function renderTherapistWeekCalendar(container, defaultTherapist) {
  container.replaceChildren();
  const filterValue = validCalendarTherapistFilter(selectedCalendarTherapistFilter || defaultTherapist.id);
  selectedCalendarTherapistFilter = filterValue;
  let weekStart = therapistCalendarWeekStart || startOfWeek(new Date());
  if (!therapistCalendarWeekPinnedByUser) {
    weekStart = autoAlignedTherapistWeek(filterValue, weekStart);
  }
  therapistCalendarWeekStart = weekStart;
  const weekEnd = addDays(weekStart, 7);

  const toolbar = document.createElement("div");
  toolbar.className = "calendar-toolbar";
  const range = document.createElement("strong");
  range.textContent = `${formatCalendarDay(weekStart)} - ${formatCalendarDay(addDays(weekStart, 6))}`;

  const filter = document.createElement("select");
  filter.className = "calendar-filter";
  filter.appendChild(new Option("All therapists", "all"));
  latestTherapists.forEach((therapist) => filter.appendChild(new Option(therapist.name, therapist.id)));
  filter.value = filterValue;
  filter.addEventListener("change", () => {
    selectedCalendarTherapistFilter = filter.value;
    if (filter.value !== "all") selectedTherapistId = filter.value;
    therapistCalendarWeekPinnedByUser = false;
    renderTherapistList();
    renderSelectedTherapist();
  });

  toolbar.append(
    calendarToolbarButton("Previous week", () => {
      therapistCalendarWeekStart = addDays(weekStart, -7);
      therapistCalendarWeekPinnedByUser = true;
      renderSelectedTherapist();
    }),
    calendarToolbarButton("Today", () => {
      therapistCalendarWeekStart = startOfWeek(new Date());
      therapistCalendarWeekPinnedByUser = true;
      renderSelectedTherapist();
    }),
    calendarToolbarButton("Next week", () => {
      therapistCalendarWeekStart = addDays(weekStart, 7);
      therapistCalendarWeekPinnedByUser = true;
      renderSelectedTherapist();
    }),
    range,
    filter,
  );

  const grid = document.createElement("div");
  grid.className = "week-calendar";
  const timeColumn = document.createElement("div");
  timeColumn.className = "week-time-column";
  timeColumn.appendChild(document.createElement("span"));
  for (let hour = 8; hour < 21; hour += 1) {
    const label = document.createElement("span");
    label.textContent = `${String(hour).padStart(2, "0")}:00`;
    timeColumn.appendChild(label);
  }
  grid.appendChild(timeColumn);

  const events = calendarWeekEvents(filterValue, weekStart, weekEnd);
  for (let offset = 0; offset < 7; offset += 1) {
    const day = addDays(weekStart, offset);
    grid.appendChild(calendarDayColumn(day, events, filterValue));
  }

  const legend = document.createElement("div");
  legend.className = "calendar-legend";
  [
    ["confirmed", "Confirmed/local"],
    ["proposed", "Proposed"],
    ["busy", "Google busy"],
  ].forEach(([status, label]) => {
    const item = document.createElement("span");
    item.dataset.status = status;
    item.textContent = label;
    legend.appendChild(item);
  });

  container.append(toolbar, legend, grid);
  if (latestTherapistCalendarCapacity?.provider_error) {
    container.appendChild(emptyState(latestTherapistCalendarCapacity.provider_error));
  }
}

function autoAlignedTherapistWeek(filterValue, currentWeekStart) {
  if (!filterValue || filterValue === "all") return currentWeekStart;
  const currentWeekEnd = addDays(currentWeekStart, 7);
  const appointments = localCalendarAppointmentsForTherapist(filterValue);
  const hasVisibleLocalAppointment = appointments.some((appointment) => {
    const start = parseDate(appointment.starts_at);
    const end = parseDate(appointment.ends_at);
    return start && end && rangesOverlap(start, end, currentWeekStart, currentWeekEnd);
  });
  if (hasVisibleLocalAppointment) return currentWeekStart;
  const now = new Date();
  const nextAppointment = appointments
    .map((appointment) => ({ appointment, start: parseDate(appointment.starts_at) }))
    .filter((item) => item.start && item.start >= now)
    .sort((a, b) => a.start - b.start)[0];
  return nextAppointment?.start ? startOfWeek(nextAppointment.start) : currentWeekStart;
}

function localCalendarAppointmentsForTherapist(therapistId) {
  const summary = therapistCalendarSummary(therapistId);
  const source = summary?.active_appointments?.length
    ? summary.active_appointments
    : appointmentsForTherapist(therapistId);
  return (source || []).filter((appointment) => ["proposed", "confirmed"].includes(appointment.status));
}

function validCalendarTherapistFilter(value) {
  if (value === "all") return "all";
  if (latestTherapists.some((therapist) => therapist.id === value)) return value;
  return latestTherapists[0]?.id || "all";
}

function calendarToolbarButton(label, handler) {
  const button = actionButton(label, handler);
  button.classList.add("calendar-nav-button");
  return button;
}

function calendarWeekEvents(filterValue, weekStart, weekEnd) {
  const referralById = new Map((latestReferrals || []).map((referral) => [referral.id, referral]));
  const therapists = filterValue === "all"
    ? latestTherapists
    : latestTherapists.filter((therapist) => therapist.id === filterValue);
  const events = [];
  const busySource = filterValue === "all"
    ? (latestTherapistCalendarCapacity?.therapists || [])[0]
    : therapistCalendarSummary(filterValue);

  (busySource?.busy_periods || []).forEach((period) => {
    const start = parseDate(period.start);
    const end = parseDate(period.end);
    if (!start || !end || !rangesOverlap(start, end, weekStart, weekEnd)) return;
    events.push({
      type: "busy",
      status: "busy",
      title: period.summary || "Google busy",
      start,
      end,
      meta: filterValue === "all" ? "Shared Google Calendar" : therapistName(filterValue),
      source: period.source || "google_calendar",
    });
  });

  therapists.forEach((therapist) => {
    const summary = therapistCalendarSummary(therapist.id);
    (summary?.active_appointments || appointmentsForTherapist(therapist.id)).forEach((appointment) => {
      const start = parseDate(appointment.starts_at);
      const end = parseDate(appointment.ends_at);
      if (!start || !end || !rangesOverlap(start, end, weekStart, weekEnd)) return;
      const referral = appointment.referral_id ? referralById.get(appointment.referral_id) : null;
      events.push({
        type: "appointment",
        status: appointment.status || "proposed",
        title: appointment.status === "confirmed" ? "Confirmed appointment" : "Proposed slot",
        start,
        end,
        therapistId: therapist.id,
        therapistName: therapist.name,
        referralId: appointment.referral_id,
        appointment,
        patientName: referral?.patient_name || (appointment.referral_id ? `Referral ${shortId(appointment.referral_id)}` : "Local appointment"),
      });
    });
  });

  return events.sort((a, b) => a.start - b.start || eventWeight(a) - eventWeight(b));
}

function calendarDayColumn(day, events, filterValue) {
  const column = document.createElement("div");
  column.className = "week-day-column";
  const header = document.createElement("div");
  header.className = "week-day-header";
  header.append(heading(day.toLocaleDateString(undefined, { weekday: "short" })), pill(String(day.getDate())));

  const body = document.createElement("div");
  body.className = "week-day-body";
  for (let hour = 8; hour < 21; hour += 1) {
    const row = document.createElement("div");
    row.className = "week-hour-row";
    body.appendChild(row);
  }
  if (filterValue !== "all") {
    attachBlankCalendarDropTarget(body, day, filterValue);
  }
  events.filter((event) => occursOnDay(event, day) && eventIntersectsCalendarHours(event, day)).forEach((event, index) => {
    body.appendChild(calendarEventElement(event, day, index));
  });
  column.append(header, body);
  return column;
}

function calendarEventElement(event, day, index) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = `week-calendar-event is-${event.type}`;
  element.dataset.status = event.status;
  const bounds = eventBoundsForDay(event, day);
  element.style.top = `${bounds.top}%`;
  element.style.height = `${bounds.height}%`;
  element.style.left = `${6 + (index % 2) * 4}%`;
  element.style.right = `${6 + (index % 3) * 3}%`;
  element.title = calendarEventTooltip(event);
  const title = document.createElement("strong");
  title.textContent = event.type === "appointment" ? event.patientName : event.title;
  const time = document.createElement("span");
  time.textContent = `${formatTime(event.start)}-${formatTime(event.end)}`;
  const meta = document.createElement("small");
  meta.textContent = event.type === "appointment" ? `${event.title} | ${event.therapistName}` : event.meta || event.source || "";
  element.append(title, time, meta);

  if (event.type === "appointment") {
    makeAppointmentDraggable(element, event.appointment);
  }
  element.addEventListener("click", async () => {
    if (event.type === "appointment" && event.referralId) {
      await openReferralWorkbench(event.referralId);
    } else if (event.type === "busy") {
      window.alert(`${event.title}\n${formatDate(event.start)} to ${formatDate(event.end)}`);
    }
  });
  return element;
}

function attachBlankCalendarDropTarget(body, day, therapistId) {
  body.addEventListener("dragover", (event) => {
    event.preventDefault();
    body.classList.add("is-drop-target");
  });
  body.addEventListener("dragleave", (event) => {
    if (!body.contains(event.relatedTarget)) body.classList.remove("is-drop-target");
  });
  body.addEventListener("drop", async (event) => {
    event.preventDefault();
    body.classList.remove("is-drop-target");
    const payload = parseDragPayload(event);
    if (!payload) return;
    const slot = slotFromCalendarDrop(day, body, event);
    if (!slot) return;
    if (payload.type === "referral" && payload.referral_id) {
      await createDraggedAppointmentProposal(payload.referral_id, therapistId, slot);
      return;
    }
    if (payload.type === "appointment" && payload.appointment_id) {
      await requestDraggedAppointmentReschedule(payload.appointment_id, slot);
    }
  });
}

function slotFromCalendarDrop(day, body, event) {
  const rect = body.getBoundingClientRect();
  if (!rect.height) return null;
  const totalMinutes = 13 * 60;
  const rawMinutes = ((event.clientY - rect.top) / rect.height) * totalMinutes;
  const startMinutes = Math.min(totalMinutes - 60, Math.max(0, Math.round(rawMinutes / 10) * 10));
  const startsAt = new Date(day);
  startsAt.setHours(8, 0, 0, 0);
  startsAt.setMinutes(startsAt.getMinutes() + startMinutes);
  const endsAt = new Date(startsAt.getTime() + 60 * 60000);
  return { starts_at: startsAt.toISOString(), ends_at: endsAt.toISOString() };
}

function eventBoundsForDay(event, day) {
  const dayStart = new Date(day);
  dayStart.setHours(8, 0, 0, 0);
  const dayEnd = new Date(day);
  dayEnd.setHours(21, 0, 0, 0);
  const clippedStart = event.start < dayStart ? dayStart : event.start;
  const clippedEnd = event.end > dayEnd ? dayEnd : event.end;
  const totalMinutes = 13 * 60;
  const startMinutes = Math.max(0, (clippedStart - dayStart) / 60000);
  const duration = Math.max(20, (clippedEnd - clippedStart) / 60000);
  return {
    top: (startMinutes / totalMinutes) * 100,
    height: Math.min(100 - (startMinutes / totalMinutes) * 100, (duration / totalMinutes) * 100),
  };
}

function eventIntersectsCalendarHours(event, day) {
  const dayStart = new Date(day);
  dayStart.setHours(8, 0, 0, 0);
  const dayEnd = new Date(day);
  dayEnd.setHours(21, 0, 0, 0);
  return rangesOverlap(event.start, event.end, dayStart, dayEnd);
}

function eventWeight(event) {
  return { busy: 0, confirmed: 1, proposed: 2 }[event.status] ?? 4;
}

function occursOnDay(event, day) {
  const start = new Date(day);
  start.setHours(0, 0, 0, 0);
  const end = addDays(start, 1);
  return rangesOverlap(event.start, event.end, start, end);
}

function rangesOverlap(startA, endA, startB, endB) {
  return startA < endB && endA > startB;
}

function startOfWeek(date) {
  const value = new Date(date);
  value.setHours(0, 0, 0, 0);
  const day = value.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  value.setDate(value.getDate() + diff);
  return value;
}

function addDays(date, days) {
  const value = new Date(date);
  value.setDate(value.getDate() + days);
  return value;
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatCalendarDay(value) {
  return value.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatTime(value) {
  return value.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function therapistName(therapistId) {
  return latestTherapists.find((therapist) => therapist.id === therapistId)?.name || "Therapist";
}

function calendarEventTooltip(event) {
  return [
    event.type === "appointment" ? event.patientName : event.title,
    `${formatDate(event.start)} to ${formatDate(event.end)}`,
    event.therapistName || event.meta,
  ].filter(Boolean).join("\n");
}

function defaultAvailabilityBlocks() {
  return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((weekday) => ({
    weekday,
    start: "08:00",
    end: "21:00",
    modality: "online",
  }));
}

function calendarBusyCard(period) {
  const start = period.start ? formatDate(period.start) : "Start unknown";
  const end = period.end ? formatDate(period.end) : "End unknown";
  return recordItem({
    title: period.summary || "Google Calendar busy block",
    status: "manual",
    body: `${start} to ${end}`,
    meta: [period.source || "google_calendar"],
  });
}

function calendarSlotDropTarget(slot, therapist) {
  const item = recordItem({
    title: slot.starts_at ? formatDate(slot.starts_at) : "Available slot",
    status: "ready",
    body: "60-minute session with a 10-minute post-session buffer.",
    meta: [
      slot.ends_at ? `ends ${formatDate(slot.ends_at)}` : null,
      slot.buffer_until ? `buffer until ${formatDate(slot.buffer_until)}` : "10 min buffer",
      slot.weekday,
    ],
  });
  item.classList.add("calendar-slot");
  item.addEventListener("dragover", (event) => {
    event.preventDefault();
    item.classList.add("is-drop-target");
  });
  item.addEventListener("dragleave", () => item.classList.remove("is-drop-target"));
  item.addEventListener("drop", async (event) => {
    event.preventDefault();
    item.classList.remove("is-drop-target");
    const payload = parseDragPayload(event);
    if (!payload) return;
    if (payload.type === "referral" && payload.referral_id) {
      await createDraggedAppointmentProposal(payload.referral_id, therapist.id, slot);
      return;
    }
    if (payload.type === "appointment" && payload.appointment_id) {
      await requestDraggedAppointmentReschedule(payload.appointment_id, slot);
    }
  });
  return item;
}

function calendarIssuesForTherapist(therapistId, summary) {
  const issues = [...(summary?.sync_errors || [])];
  (latestTherapistCalendarCapacity?.unmatched_calendar_events || [])
    .filter((event) => !event.lumen_therapist_id || event.lumen_therapist_id === therapistId)
    .forEach((event) => issues.push({
      code: "unmatched_calendar_event",
      message: event.summary || "Lumen Calendar event has no matching local appointment.",
      event_id: event.id,
    }));
  (latestTherapistCalendarCapacity?.malformed_calendar_events || [])
    .filter((event) => !event.lumen_therapist_id || event.lumen_therapist_id === therapistId)
    .forEach((event) => issues.push({
      code: "malformed_calendar_event",
      message: event.summary || "Calendar event is missing required Lumen metadata.",
      event_id: event.id,
    }));
  if (latestTherapistCalendarCapacity?.provider_error) {
    issues.push({ code: "provider_error", message: latestTherapistCalendarCapacity.provider_error });
  }
  return issues;
}

function calendarIssueCard(issue) {
  return recordItem({
    title: String(issue.code || "calendar issue").replaceAll("_", " "),
    status: "sync_issue",
    body: issue.message || "Calendar sync needs admin attention.",
    meta: [
      issue.appointment_id ? `appointment ${shortId(issue.appointment_id)}` : null,
      issue.event_id ? `event ${shortId(issue.event_id)}` : null,
    ],
  });
}

function renderAvailabilityGrid(container, blocks) {
  container.replaceChildren();
  const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const grid = document.createElement("div");
  grid.className = "availability-grid";
  const sourceBlocks = (blocks || []).length ? blocks : defaultAvailabilityBlocks();
  weekdays.forEach((weekday) => {
    const cell = document.createElement("div");
    cell.className = "availability-cell";
    const label = document.createElement("strong");
    label.textContent = weekday.slice(0, 3);
    cell.appendChild(label);
    const dayBlocks = sourceBlocks.filter((block) => String(block.weekday || "").toLowerCase() === weekday.toLowerCase());
    if (!dayBlocks.length) {
      cell.appendChild(pill("Unavailable"));
    } else {
      dayBlocks.forEach((block) => cell.appendChild(pill(`${block.start}-${block.end} ${formatModality(block.modality)}`)));
    }
    grid.appendChild(cell);
  });
  container.appendChild(grid);
  container.appendChild(emptyState((blocks || []).length
    ? "All times use the clinic timezone configured for this demo environment."
    : "Default availability is used for this demo when no custom weekly blocks are set."));
}

function detailCard(title, children) {
  const card = document.createElement("article");
  card.className = "detail-card";
  card.appendChild(heading(title));
  children.forEach((child) => card.appendChild(child));
  return card;
}

function formatModality(value) {
  return String(value || "online").replaceAll("_", " ");
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
  const checks = data.checks || [];
  renderCollection(integrationHealthList, checks, "No integration checks yet.", integrationHealthCard);
  await loadGoogleWorkspaceStatus();
  await loadGmailInbox();
  if (overviewHealthStrip) {
    overviewHealthStrip.replaceChildren(...checks.map(integrationHealthCard));
  }
  if (metricLastSync) {
    metricLastSync.textContent = new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date());
  }
}

function integrationHealthCard(check) {
  return recordItem({
    title: check.name,
    status: check.status,
    body: check.message,
    meta: [check.last_seen ? formatDate(check.last_seen) : null],
  });
}

async function loadGmailInbox() {
  if (!gmailInboxList) return;
  const response = await fetch("/api/integrations/gmail-inbox?limit=60");
  if (!response.ok) {
    gmailInboxList.replaceChildren(
      recordItem({
        title: "Inbound Gmail",
        status: "failed",
        body: "Inbound Gmail messages are unavailable.",
        meta: [],
      }),
    );
    return;
  }
  const data = await readResponseBody(response);
  const messages = data.messages || [];
  renderCollection(gmailInboxList, messages, "No inbound Gmail messages yet.", gmailInboxCard);
}

async function syncGmailInbox() {
  if (gmailSyncButton) gmailSyncButton.disabled = true;
  try {
    const response = await fetch("/api/integrations/gmail-sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_results: 25, include_recent_read: true }),
    });
    const body = await readResponseBody(response);
    if (!response.ok) {
      window.alert(body.detail || "Gmail sync failed.");
      return;
    }
    const processed = body.processed?.length || 0;
    const skipped = body.skipped?.length || 0;
    const errors = body.errors?.length || 0;
    const total = body.total_seen || 0;
    const unread = body.unread_seen || 0;
    const recent = body.recent_seen || 0;
    const firstError = body.errors?.[0]?.error ? `\nFirst error: ${body.errors[0].error}` : "";
    const message = total
      ? `Gmail sync finished. ${processed} processed, ${skipped} skipped, ${errors} errors. Checked ${unread} unread and ${recent} recent inbox messages.${firstError}`
      : "Gmail sync finished. No unread or recent inbox messages were found.";
    window.alert(message);
    await Promise.all([loadGmailInbox(), loadReviewTasks(), loadReferrals()]);
  } catch (error) {
    window.alert(`Gmail sync failed before the server responded: ${error?.message || error}`);
  } finally {
    if (gmailSyncButton) gmailSyncButton.disabled = false;
  }
}

function gmailInboxCard(message) {
  const meta = [
    message.sender_email || message.from || "unknown sender",
    message.date ? formatDate(message.date) : null,
    message.status || message.document_type,
    message.reply_type ? `reply ${message.reply_type}` : null,
    message.referral_id ? `referral ${shortId(message.referral_id)}` : null,
  ];
  const item = recordItem({
    title: message.subject || "Inbound Gmail",
    status: message.status || message.document_type,
    body: message.body || message.snippet || "No message body captured.",
    meta,
  });
  const actions = document.createElement("div");
  actions.className = "actions tight";
  if (message.referral_id) {
    actions.appendChild(actionButton("Open referral", () => openReferralWorkbench(message.referral_id)));
  } else if (message.document_id) {
    actions.appendChild(actionButton("Create referral", () => convertGmailInboxMessage(message.document_id)));
  }
  item.appendChild(actions);
  return item;
}

async function convertGmailInboxMessage(documentId) {
  const response = await fetch("/api/integrations/gmail-inbox/convert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_id: documentId }),
  });
  const body = await readResponseBody(response);
  if (!response.ok) {
    window.alert(body.detail || "Could not create a referral from the inbound email.");
    return;
  }
  await refreshProductWorkspace();
  if (body.referral?.id) {
    await openReferralWorkbench(body.referral.id);
  }
}

async function loadGoogleWorkspaceStatus() {
  if (!googleWorkspaceList) return;
  const response = await fetch("/api/integrations/google/status");
  if (!response.ok) {
    googleWorkspaceList.replaceChildren(
      recordItem({
        title: "Google Workspace",
        status: "failed",
        body: "Google Workspace status is unavailable.",
        meta: [],
      }),
    );
    return;
  }
  const status = await readResponseBody(response);
  const accountMismatch = status.account_matches_expected === false;
  const connectionStatus = status.enabled
    ? status.authorized
      ? accountMismatch
        ? "failed"
        : "ready"
      : status.last_provider_error
        ? "failed"
        : "not_authorized"
    : "manual";
  googleWorkspaceList.replaceChildren(
    recordItem({
      title: "Gmail send",
      status: connectionStatus,
      body: status.enabled
        ? status.authorized
          ? accountMismatch
            ? `Connected to ${status.gmail_email_address || "unknown Gmail account"}; expected ${status.expected_gmail_account}.`
            : "Approved patient-facing drafts send through Gmail."
          : "Run the local Google authorization script before enabling live send."
        : "Google Workspace is disabled; approvals use the local/manual workflow.",
      meta: [
        status.token_present ? "token present" : "no token",
        status.gmail_email_address ? `gmail ${status.gmail_email_address}` : null,
        status.expected_gmail_account ? `expected ${status.expected_gmail_account}` : null,
        status.configured_scopes?.includes("gmail.send") ? "gmail.send" : null,
      ],
    }),
    recordItem({
      title: "Google Calendar",
      status: connectionStatus,
      body: status.enabled
        ? status.authorized
          ? "Slot proposals check free/busy and approved appointments create Calendar events."
          : "Calendar free/busy and event creation are blocked until authorization succeeds."
        : "Therapist availability blocks and local appointments are the current source of truth.",
      meta: [status.calendar_id ? `calendar ${status.calendar_id}` : null, status.timezone, status.enabled ? status.last_provider_error : null],
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

function operationSection(title, options = {}) {
  const section = options.collapsible ? document.createElement("details") : document.createElement("section");
  section.className = options.collapsible ? "operation-section collapsible-section" : "operation-section";
  if (options.collapsible && options.open !== false) section.open = true;
  const body = document.createElement("div");
  body.className = "record-list embedded-list";
  if (options.collapsible) {
    const summary = document.createElement("summary");
    const headingEl = document.createElement("h4");
    headingEl.textContent = title;
    summary.appendChild(headingEl);
    section.append(summary, body);
  } else {
    const headingEl = document.createElement("h4");
    headingEl.textContent = title;
    section.append(headingEl, body);
  }
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
      meta: [
        draft.channel,
        draft.requires_human_send ? "requires approval" : "no approval required",
        draft.sent_at ? `sent ${formatDate(draft.sent_at)}` : null,
        draft.gmail_message_id ? `gmail ${shortId(draft.gmail_message_id)}` : null,
        draft.last_provider_error,
      ],
    });
    return item;
  });
}

function renderCommunicationThread(container, referral) {
  const entries = [
    ...(referral.communication_drafts || []).map((draft) => ({ type: "draft", item: draft })),
    ...(referral.missing_info_replies || []).map((reply) => ({ type: "missing_reply", item: reply })),
    ...(referral.patient_replies || []).map((reply) => ({ type: "patient_reply", item: reply })),
  ].sort((a, b) => new Date(b.item.created_at || b.item.updated_at || 0) - new Date(a.item.created_at || a.item.updated_at || 0));

  renderSimpleList(container, entries, "No referral-specific communication recorded yet.", (entry) => {
    if (entry.type === "draft") {
      const draft = entry.item;
      return recordItem({
        title: draft.subject || "Agent-drafted message",
        status: draft.status,
        body: draft.body,
        meta: [
          draft.channel,
          draft.requires_human_send ? "approval required" : "no approval required",
          draft.sent_at ? `sent ${formatDate(draft.sent_at)}` : formatDate(draft.created_at),
          draft.gmail_message_id ? `gmail ${shortId(draft.gmail_message_id)}` : null,
          draft.last_provider_error,
        ],
      });
    }
    const documentRecord = entry.item;
    const metadata = documentRecord.metadata || {};
    return recordItem({
      title: entry.type === "missing_reply" ? "Missing-information reply" : "Patient reply",
      status: documentRecord.document_type,
      body: metadata.notes || metadata.reply_type || documentRecord.title,
      meta: [metadata.source, metadata.reply_type, formatDate(documentRecord.created_at)],
    });
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

function renderReferralDocuments(container, referral) {
  const seen = new Set();
  const documents = [
    ...(referral.documents || []),
    ...(referral.patient_replies || []),
    ...(referral.missing_info_replies || []),
  ].filter((documentRecord) => {
    if (!documentRecord?.id || seen.has(documentRecord.id)) return false;
    seen.add(documentRecord.id);
    return true;
  });
  renderDocuments(container, documents, "No referral documents, replies, intake uploads, or waiver records yet.");
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
      meta: [
        appointment.source,
        appointment.ends_at ? `ends ${new Date(appointment.ends_at).toLocaleTimeString()}` : null,
        appointment.google_calendar_event_id ? `calendar ${shortId(appointment.google_calendar_event_id)}` : null,
        appointment.google_calendar_synced_at ? `synced ${formatDate(appointment.google_calendar_synced_at)}` : null,
        appointment.calendar_sync_issue ? "calendar sync issue" : null,
        appointment.last_provider_error,
      ],
    });
    if (appointment.google_calendar_event_link) {
      const link = document.createElement("a");
      link.href = appointment.google_calendar_event_link;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "Open Calendar event";
      item.appendChild(link);
    }
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

  renderIntakeTemplateFiles(container, data, includeActions);

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
    const row = recordItem({
      title: documentRecord.title,
      status: "completed",
      body: documentRecord.document_type.replaceAll("_", " "),
      meta: [
        documentRecord.metadata?.size_bytes ? `${documentRecord.metadata.size_bytes} bytes` : null,
        documentRecord.metadata?.virus_scan?.status || "stored",
      ],
    });
    const actions = document.createElement("div");
    actions.className = "actions tight";
    actions.appendChild(actionButton("Download", () => openDocumentDownload(documentRecord.id)));
    row.appendChild(actions);
    container.appendChild(row);
  });

  (data.communication_drafts || []).slice(0, 2).forEach((draft) => {
    const row = recordItem({
      title: draft.subject || "Intake reminder draft",
      status: draft.status,
      body: draft.body,
      meta: [draft.channel, draft.requires_human_send ? "requires approval" : null],
    });
    appendAttachmentSummary(row, draft);
    container.appendChild(row);
  });
}

function renderIntakeTemplateFiles(container, data, includeActions) {
  const template = data.template;
  if (!template) return;
  const specs = template.required_items || [];
  if (!specs.length) return;
  const section = document.createElement("div");
  section.className = "detail-stack intake-template-files";
  section.appendChild(heading("Template files"));
  specs.forEach((spec) => {
    const file = spec.template_file;
    const key = spec.key || spec.label;
    const row = recordItem({
      title: spec.label || key || "Template file",
      status: file ? "configured" : "missing",
      body: file ? file.file_name : "Upload the active blank file for this intake item.",
      meta: [
        spec.type || "form",
        file?.size_bytes ? `${file.size_bytes} bytes` : null,
        file?.uploaded_at ? `uploaded ${formatDate(file.uploaded_at)}` : null,
      ],
    });
    if (includeActions && key) {
      const actions = document.createElement("div");
      actions.className = "actions tight";
      actions.appendChild(actionButton(file ? "Replace file" : "Upload file", () => uploadIntakeTemplateFile(template.id, key)));
      if (file?.document_id) actions.appendChild(actionButton("Download", () => openDocumentDownload(file.document_id)));
      row.appendChild(actions);
    }
    section.appendChild(row);
  });
  container.appendChild(section);
}

function appendAttachmentSummary(container, payload) {
  const manifest = payload.outbound_attachment_manifest || [];
  const missing = payload.missing_template_files || [];
  const sent = payload.sent_attachment_records || [];
  if (manifest.length) {
    container.appendChild(keyValue("Attachments", manifest.map((item) => item.file_name).join(", ")));
  }
  if (sent.length) {
    container.appendChild(keyValue("Sent attachments", sent.map((item) => item.file_name).join(", ")));
  }
  missing.forEach((item) => {
    container.appendChild(keyValue("Setup warning", `${item.item_label || item.item_key}: ${item.reason || "Missing template file"}`));
  });
}

function renderIntakeSubmissionReviews(container, tasks, intakeData) {
  const openTasks = (tasks || []).filter((task) => task.task_type === "intake_submission_review" && task.status === "open");
  if (!openTasks.length) return;
  const section = document.createElement("div");
  section.className = "detail-stack intake-review-stack";
  section.appendChild(heading("Intake submissions to review"));
  openTasks.forEach((task) => section.appendChild(intakeSubmissionReviewCard(task, intakeData)));
  container.appendChild(section);
}

function intakeSubmissionReviewCard(task, intakeData) {
  const payload = task.source_payload || {};
  const documents = payload.documents || [];
  const documentRecord = documents[0] || {};
  const attachmentErrors = payload.attachment_errors || [];
  const card = recordItem({
    title: documentRecord.title || "Patient intake reply",
    status: "needs mapping",
    body: task.reason,
    meta: [
      documentRecord.document_type?.replaceAll("_", " "),
      documentRecord.metadata?.file_name || null,
      documentRecord.metadata?.size_bytes ? `${documentRecord.metadata.size_bytes} bytes` : null,
    ],
  });

  if (payload.reply_text) {
    const details = document.createElement("details");
    details.className = "handoff";
    const summary = document.createElement("summary");
    summary.textContent = "Patient reply";
    const pre = document.createElement("pre");
    pre.textContent = payload.reply_text;
    details.append(summary, pre);
    card.appendChild(details);
  }
  attachmentErrors.forEach((error) => {
    card.appendChild(keyValue(error.file_name || "Attachment", error.error || "Could not store attachment"));
  });

  if (!documentRecord.id && !payload.document_id) {
    const actions = document.createElement("div");
    actions.className = "actions tight";
    actions.appendChild(actionButton("Mark reviewed", () => submitReviewAction(task.id, "approve")));
    card.appendChild(actions);
    return card;
  }

  const form = document.createElement("form");
  form.className = "inline-review-form";
  const documentActions = document.createElement("div");
  documentActions.className = "actions tight";
  documentActions.appendChild(actionButton("Download file", () => openDocumentDownload(documentRecord.id || payload.document_id)));
  card.appendChild(documentActions);

  const itemSelect = document.createElement("select");
  itemSelect.appendChild(new Option("Map to checklist item", ""));
  (intakeData.items || []).forEach((item) => {
    const label = `${item.label} (${item.item_type}, ${item.status})`;
    itemSelect.appendChild(new Option(label, item.id));
  });

  const consentSelect = document.createElement("select");
  consentSelect.appendChild(new Option("Map to consent", ""));
  (intakeData.consents || []).forEach((consent) => {
    const label = `${consent.scope.replaceAll("_", " ")} (${consent.status})`;
    consentSelect.appendChild(new Option(label, consent.id));
  });

  const questionnaireInput = document.createElement("input");
  questionnaireInput.type = "text";
  questionnaireInput.placeholder = "Questionnaire name for JSON answers";
  questionnaireInput.value = "intake_questionnaire";

  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "secondary compact";
  submit.textContent = "Approve mapping";

  form.append(itemSelect, consentSelect, questionnaireInput, submit);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!itemSelect.value && !consentSelect.value) {
      window.alert("Select a checklist item or consent before approving.");
      return;
    }
    submitReviewAction(task.id, "approve", {
      document_id: documentRecord.id || payload.document_id,
      intake_item_id: itemSelect.value || null,
      consent_id: consentSelect.value || null,
      questionnaire_name: questionnaireInput.value.trim() || null,
    });
  });
  card.appendChild(form);
  return card;
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

function setActiveJob(jobId) {
  if (!viewTraceLink) return;
  if (!jobId) {
    viewTraceLink.hidden = true;
    viewTraceLink.textContent = "View trace";
    return;
  }
  viewTraceLink.hidden = false;
  viewTraceLink.textContent = `View trace ${shortId(jobId)}`;
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
  setActiveJob(null);
  if (newReferralResultList) {
    newReferralResultList.replaceChildren(emptyState("Run a referral workflow to see captured status, missing items, risk, and the Workbench link."));
  }
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

function friendlyTaskType(value) {
  const labels = {
    admin_missing_info_review: "Missing information review",
    missing_info_message_approval: "Missing-info message approval",
    duplicate_resolution: "Duplicate review",
    clinical_risk_review: "Clinical risk review",
    suitability_review: "Suitability review",
    match_approval: "Therapist match approval",
    slot_offer_approval: "Slot offer approval",
    send_approval: "Patient message approval",
    appointment_confirmation_approval: "Appointment confirmation approval",
    appointment_reschedule_approval: "Appointment reschedule approval",
    intake_reminder_approval: "Intake reminder approval",
    intake_exception_approval: "Intake exception approval",
    intake_submission_review: "Intake submission review",
    inbound_reply_review: "Inbound reply review",
    therapist_note_approval: "Therapist note approval",
    post_session_risk_review: "Post-session risk review",
    report_signoff: "Report signoff",
  };
  return labels[value] || String(value || "review task").replaceAll("_", " ");
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

function roundHours(value) {
  const numeric = Number(value || 0);
  return Math.round(numeric * 10) / 10;
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
loadSecurityContext();
loadExamples();
loadModelHealth();
refreshProductWorkspace();
