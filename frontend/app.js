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
};

const form = document.querySelector("#workflow-form");
const runButton = document.querySelector("#run-button");
const resetButton = document.querySelector("#reset-button");
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
const securityPostureList = document.querySelector("#security-posture-list");
const feedbackMetricsList = document.querySelector("#feedback-metrics-list");
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
let latestWorkflows = [];
let latestAppointments = [];
let selectedReferralId = null;
let selectedTherapistId = null;
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
  const progress = renderReferralProgress(referral.status);

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
  taskSection.section.id = "referral-review-tasks";
  renderSimpleList(taskSection.body, referral.review_tasks || [], "No review tasks for this referral.", reviewTaskCard);

  const outputSection = operationSection("Agent outputs");
  renderAgentOutputs(outputSection.body, workbenchState.agent_outputs || []);

  const matchSection = operationSection("Deterministic match");
  renderMatchSummary(matchSection.body, referral.match_summary);

  const appointmentSection = operationSection("Appointment proposals");
  appointmentSection.section.id = "referral-appointments";
  const intakeSection = operationSection("Intake status");
  intakeSection.section.id = "referral-intake";
  const briefSection = operationSection("Prep briefs");
  briefSection.section.id = "referral-prep";
  const communicationSection = operationSection("Communication thread");
  renderCommunicationThread(communicationSection.body, referral);

  const raw = document.createElement("details");
  raw.className = "handoff";
  const rawSummary = document.createElement("summary");
  rawSummary.textContent = "Raw referral source";
  const rawText = document.createElement("pre");
  rawText.textContent = referral.raw_text || "";
  raw.append(rawSummary, rawText);

  const activitySection = operationSection("Activity timeline");
  renderActivityTimeline(activitySection.body, workbenchState.activity || []);

  referralDetail.append(
    header,
    progress,
    readinessSection.section,
    fieldsSection.section,
    missingSection.section,
    riskSection.section,
    taskSection.section,
    outputSection.section,
    matchSection.section,
    appointmentSection.section,
    communicationSection.section,
    intakeSection.section,
    briefSection.section,
    raw,
    activitySection.section,
  );
  await loadReferralOperations(referral.id, appointmentSection.body, intakeSection.body, briefSection.body);
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

function renderReferralProgress(status) {
  const steps = [
    { id: "new_referral", label: "Captured", statuses: ["new_referral", "normalising", "needs_admin_review", "waiting_for_missing_info"] },
    { id: "reviewed", label: "Reviewed", statuses: ["needs_clinical_review", "clinical_escalation_review", "ready_for_matching"] },
    { id: "matched", label: "Matched", statuses: ["match_recommended", "match_approved"] },
    { id: "contacted", label: "Contacted", statuses: ["slot_options_ready", "awaiting_patient_contact", "contact_sent", "awaiting_patient_reply"] },
    { id: "appointment_confirmed", label: "Appointment confirmed", statuses: ["appointment_confirmed"] },
    { id: "intake_complete", label: "Intake complete", statuses: ["intake_packet_sent", "intake_incomplete", "intake_complete"] },
    { id: "prep_brief_ready", label: "Prep brief ready", statuses: ["prep_brief_ready", "first_session_ready"] },
  ];
  const currentIndex = Math.max(
    0,
    steps.findIndex((step) => step.statuses.includes(status)),
  );
  const wrapper = document.createElement("section");
  wrapper.className = "progress-path";
  steps.forEach((step, index) => {
    const item = document.createElement("div");
    item.className = "progress-step";
    item.dataset.state = index < currentIndex ? "complete" : index === currentIndex ? "current" : "pending";
    const marker = document.createElement("span");
    marker.className = "progress-marker";
    marker.textContent = index < currentIndex ? "OK" : String(index + 1);
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
  if (!actionId || ["ready", "closed", "wait_patient_reply"].includes(actionId)) return null;
  const action = workbenchActionDefinitions(referral)[actionId];
  if (!action) return null;
  const button = actionButton(state.primary_action_label || action.label, action.handler);
  button.classList.remove("secondary", "compact");
  return button;
}

function secondaryWorkbenchActions(referral, state) {
  const definitions = workbenchActionDefinitions(referral);
  const primaryAction = state.primary_action === "revise_agent_output" ? (state.allowed_actions || [])[0] : state.primary_action;
  const allowed = new Set([...(state.allowed_actions || []), ...defaultWorkbenchActionIds()]);
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
    review_gate: { label: "Open review task", handler: () => scrollToDetailSection("referral-review-tasks") },
    review_referral: { label: "Review referral", handler: () => scrollToDetailSection("referral-review-tasks") },
    review_missing_info: { label: "Resolve missing information", handler: () => draftMissingInfo(referral.id) },
    draft_missing_info: { label: "Draft missing info", handler: () => draftMissingInfo(referral.id) },
    record_missing_reply: { label: "Record missing reply", handler: () => recordMissingInfoReply(referral.id) },
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
      meta: [item.type ? item.type.replaceAll("_", " ") : null, formatDate(item.created_at)],
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
    item.appendChild(actions);
    return item;
  }
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
  const response = await fetch("/api/therapists");
  if (!response.ok) return;
  const data = await readResponseBody(response);
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
  const summaries = latestTherapists.map(therapistCapacitySummary);
  const remaining = summaries.reduce((total, summary) => total + Math.max(0, summary.remaining), 0);
  const fullyBooked = summaries.filter((summary) => summary.active && summary.capacity > 0 && summary.remaining <= 0).length;
  const missingAvailability = activeTherapists.filter((therapist) => !(therapist.availability_blocks || []).length).length;
  const incomplete = activeTherapists.filter((therapist) => therapistMatchingDataIncomplete(therapist)).length;

  if (metricTherapists) metricTherapists.textContent = activeTherapists.length;
  if (metricTherapistActive) metricTherapistActive.textContent = activeTherapists.length;
  if (metricTherapistCapacity) metricTherapistCapacity.textContent = remaining;
  if (metricTherapistFull) metricTherapistFull.textContent = fullyBooked;
  if (metricTherapistMissing) metricTherapistMissing.textContent = missingAvailability;
  if (metricTherapistIncomplete) metricTherapistIncomplete.textContent = incomplete;
}

function renderTherapistList() {
  if (!therapistList) return;
  renderCollection(therapistList, latestTherapists, "No therapist profiles found.", (therapist) => {
    const summary = therapistCapacitySummary(therapist);
    const nextSlot = nextAvailabilityLabel(therapist);
    const item = recordItem({
      title: therapist.name,
      status: therapist.active ? "active" : "inactive",
      body: therapist.specialties.join(", ") || "No specialties recorded",
      meta: [
        `${summary.capacity}/week capacity`,
        `${summary.assigned} assigned`,
        nextSlot,
        therapist.languages.join(", "),
        therapist.modalities.join(", "),
      ],
    });
    item.classList.add("clickable", "therapist-card");
    item.classList.toggle("is-selected", therapist.id === selectedTherapistId);
    item.addEventListener("click", () => {
      selectedTherapistId = therapist.id;
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
  const summary = therapistCapacitySummary(therapist);
  const assignedReferrals = referralsForTherapist(therapist.id);
  const bookings = appointmentsForTherapist(therapist.id);

  const header = document.createElement("div");
  header.className = "therapist-profile-header";
  header.append(
    heading(therapist.name),
    metaRow([therapist.active ? "active" : "inactive", therapist.email, `${summary.remaining} remaining`]),
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
    detailCard("Capacity", [
      keyValue("Weekly capacity", `${summary.capacity} sessions`),
      keyValue("Currently assigned", `${summary.assigned} referrals`),
      keyValue("Remaining", `${summary.remaining} sessions`),
    ]),
  );

  const availabilitySection = operationSection("Weekly availability");
  renderAvailabilityGrid(availabilitySection.body, therapist.availability_blocks || []);

  const bookingsSection = operationSection("Manual bookings / blocked time");
  renderSimpleList(
    bookingsSection.body,
    bookings,
    "No local proposed, confirmed, or blocked appointments recorded.",
    (appointment) => appointmentCard(appointment, new Map(latestReferrals.map((referral) => [referral.id, referral]))),
  );

  const assignedSection = operationSection("Assigned patients and referrals");
  renderSimpleList(
    assignedSection.body,
    assignedReferrals,
    "No assigned referrals found for this therapist.",
    (referral) => {
      const item = referralCard(referral, true);
      item.appendChild(keyValue("First session", firstSessionForReferral(referral.id, therapist.id)));
      return item;
    },
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

  therapistDetail.append(header, profileGrid, availabilitySection.section, bookingsSection.section, assignedSection.section, historySection.section);
}

function therapistCapacitySummary(therapist) {
  const capacity = Number(therapist.capacity_per_week || 0);
  const assigned = referralsForTherapist(therapist.id).length;
  return {
    active: therapist.active,
    capacity,
    assigned,
    remaining: capacity - assigned,
  };
}

function therapistMatchingDataIncomplete(therapist) {
  return !therapist.capacity_per_week
    || !(therapist.specialties || []).length
    || !(therapist.languages || []).length
    || !(therapist.modalities || []).length
    || !(therapist.availability_blocks || []).length;
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
  const block = (therapist.availability_blocks || [])[0];
  if (!block) return "No availability set";
  return `Next: ${block.weekday || "manual"} ${block.start || ""}`;
}

function renderAvailabilityGrid(container, blocks) {
  container.replaceChildren();
  const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const grid = document.createElement("div");
  grid.className = "availability-grid";
  weekdays.forEach((weekday) => {
    const cell = document.createElement("div");
    cell.className = "availability-cell";
    const label = document.createElement("strong");
    label.textContent = weekday.slice(0, 3);
    cell.appendChild(label);
    const dayBlocks = blocks.filter((block) => String(block.weekday || "").toLowerCase() === weekday.toLowerCase());
    if (!dayBlocks.length) {
      cell.appendChild(pill("Unavailable"));
    } else {
      dayBlocks.forEach((block) => cell.appendChild(pill(`${block.start}-${block.end} ${formatModality(block.modality)}`)));
    }
    grid.appendChild(cell);
  });
  container.appendChild(grid);
  container.appendChild(emptyState("All times use the clinic timezone configured for this demo environment."));
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
        meta: [draft.channel, draft.requires_human_send ? "approval required" : "no approval required", formatDate(draft.created_at)],
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
    intake_reminder_approval: "Intake reminder approval",
    intake_exception_approval: "Intake exception approval",
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
