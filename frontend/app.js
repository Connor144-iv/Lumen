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
const referralDetail = document.querySelector("#referral-detail");
const reviewTaskList = document.querySelector("#review-task-list");
const therapistList = document.querySelector("#therapist-list");

let activeSource = null;
let pollTimer = null;
let currentResult = null;

form.addEventListener("change", (event) => {
  if (event.target.name === "workflow_type") {
    updateWorkflowSections(event.target.value);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resetRunState();
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
  try {
    const response = await fetch("/api/health/models");
    const data = await readResponseBody(response);
    if (!response.ok) throw new Error(data.detail || "Model health check failed.");
    modelHealthList.replaceChildren(
      ...data.checks.map((check) =>
        recordItem({
          title: `${check.role}: ${check.model}`,
          status: check.status,
          body: check.message,
          meta: [check.provider, `${check.latency_ms} ms`],
        }),
      ),
    );
  } catch (error) {
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
  await Promise.all([loadReferrals(), loadReviewTasks(), loadTherapists()]);
}

async function loadReferrals() {
  const response = await fetch("/api/referrals");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  referralList.replaceChildren();
  if (!data.referrals.length) {
    referralList.appendChild(emptyState("No referrals yet."));
    return;
  }
  data.referrals.forEach((referral) => {
    const item = recordItem({
      title: referral.patient_name || referral.input_summary || "Unnamed referral",
      status: referral.status,
      body: referral.contact_email || referral.insurer || referral.source_channel,
      meta: [referral.source_channel, referral.risk_category || "risk pending"],
    });
    item.classList.add("clickable");
    item.addEventListener("click", () => loadReferralDetail(referral.id));
    referralList.appendChild(item);
  });
}

async function loadReferralDetail(referralId) {
  const response = await fetch(`/api/referrals/${referralId}`);
  if (!response.ok) return;
  const referral = await readResponseBody(response);
  referralDetail.replaceChildren();

  const header = document.createElement("div");
  header.className = "detail-stack";
  header.append(
    heading(referral.patient_name || "Unnamed referral"),
    metaRow([referral.status, referral.source_channel, referral.urgency || "urgency pending"]),
    keyValue("Contact", [referral.contact_email, referral.contact_phone].filter(Boolean).join(" | ") || "Missing"),
    keyValue("Insurer", referral.insurer || "Missing"),
    keyValue("Missing fields", (referral.missing_fields || []).join(", ") || "None recorded"),
  );

  const raw = document.createElement("details");
  raw.className = "handoff";
  const rawSummary = document.createElement("summary");
  rawSummary.textContent = "Source text";
  const rawText = document.createElement("pre");
  rawText.textContent = referral.raw_text || "";
  raw.append(rawSummary, rawText);

  const draft = referral.communication_drafts?.[0];
  if (draft) {
    header.append(keyValue("Latest draft", `${draft.channel}${draft.subject ? ` | ${draft.subject}` : ""}`));
    const draftPre = document.createElement("pre");
    draftPre.textContent = draft.body;
    header.appendChild(draftPre);
  }

  referralDetail.append(header, raw);
}

async function loadReviewTasks() {
  const response = await fetch("/api/review-tasks?status=open");
  if (!response.ok) return;
  const data = await readResponseBody(response);
  reviewTaskList.replaceChildren();
  if (!data.tasks.length) {
    reviewTaskList.appendChild(emptyState("No open review tasks."));
    return;
  }
  data.tasks.forEach((task) => reviewTaskList.appendChild(reviewTaskCard(task)));
}

function reviewTaskCard(task) {
  const item = recordItem({
    title: task.task_type.replaceAll("_", " "),
    status: task.status,
    body: task.reason,
    meta: [task.payload_key, task.referral_id ? `referral ${task.referral_id.slice(0, 8)}` : "no referral"],
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
  const escalate = actionButton("Escalate", () => submitReviewAction(task.id, "escalate"));
  actions.append(approve, reject, escalate);
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
  if (body.resumed_job) {
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
  therapistList.replaceChildren();
  data.therapists.forEach((therapist) => {
    therapistList.appendChild(
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
  });
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

loadExamples();
loadModelHealth();
refreshProductWorkspace();
