from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "frontend" / "app.js"
INDEX_HTML = ROOT / "frontend" / "index.html"


def test_documentation_frontend_uses_scoped_current_user_endpoints() -> None:
    app_js = APP_JS.read_text()

    assert "/api/me/therapist" in app_js
    assert "/api/documentation/patients" in app_js
    assert "/api/documentation/therapists/all/patients/overview" in app_js
    assert "/api/documentation/patients/${encodeURIComponent(patientKey)}/dashboard" in app_js
    assert "/progress-overview/generate" in app_js
    assert "/api/documentation/sessions?patient_id=" in app_js
    assert "/api/documentation/sessions/" in app_js
    assert "/uploads/extract" in app_js


def test_progress_refresh_uses_complete_response_and_loading_state() -> None:
    app_js = APP_JS.read_text()

    assert "progressOverviewGenerationInFlight" in app_js
    assert 'patientDashboardProgress.replaceChildren(emptyState("Generating complete progress overview..."))' in app_js
    assert "isCompleteProgressOverview(data.progress_overview)" in app_js
    assert "throw new Error(\"Progress overview response was incomplete" in app_js
    assert "latestPatientDashboard.progress_overview = data.progress_overview" in app_js
    assert "if (section.trajectory)" in app_js


def test_documentation_frontend_does_not_render_old_manual_identity_controls() -> None:
    source = INDEX_HTML.read_text() + APP_JS.read_text()

    forbidden_controls = [
        "Choose or add therapist",
        "Therapist name",
        "Use therapist",
        "Choose or add patient",
        "Use patient",
    ]
    for control in forbidden_controls:
        assert control not in source


def test_frontend_marks_admin_and_therapist_workspaces_for_role_routing() -> None:
    index_html = INDEX_HTML.read_text()
    app_js = APP_JS.read_text()

    assert 'href="/static/styles.css"' in index_html
    assert 'src="/static/app.js"' in index_html
    assert 'href="/documentation" data-nav data-page-link="documentation" data-therapist-only' in index_html
    assert 'href="/my-patients" data-nav data-page-link="my-patients" data-therapist-only' in index_html
    assert 'data-page-link="overview" data-admin-only' in index_html
    assert 'data-page-link="therapists" data-admin-only' in index_html
    assert "isTherapistUser() && !route.therapistOnly" in app_js
    assert "route.therapistOnly && isAdminUser()" in app_js
    assert "const adminEnabled = isAdminUser();" in app_js
    assert 'href="/new-referral" data-nav data-admin-only hidden' in index_html
    assert "refreshWorkspaceForCurrentRole" in app_js
    assert 'class="workspace-grid therapist-patients-grid"' in index_html
    assert "Patient Overview" in index_html
    assert "/patients/:patientKey/dashboard" in app_js
    assert "openDocumentationSessionFromDashboard" in app_js
    assert "lumen.documentation.selectedSession" in app_js
    assert "routeForPath(window.location.pathname).page === \"my-patients\"" in app_js
    assert "renderDashboardTranscriptEditor" not in app_js
    assert "renderDashboardNoteEditor" not in app_js


def test_frontend_does_not_render_admin_workspace_before_role_context() -> None:
    index_html = INDEX_HTML.read_text()
    app_js = APP_JS.read_text()

    assert '<section class="page is-active" data-page="overview"' not in index_html
    assert "applyRoute(window.location.pathname);\n  await loadSecurityContext();" not in app_js
    assert "await loadSecurityContext();\n  if (isAdminUser())" in app_js


def test_dev_identity_switcher_requires_explicit_local_opt_in() -> None:
    app_js = APP_JS.read_text()

    assert "lumen.enableDevUserSwitcher" in app_js
    assert 'get("devUserSwitcher") === "1"' in app_js
    assert 'localStorage.getItem(DEV_SWITCHER_STORAGE_KEY) === "true"' in app_js
    assert 'refreshProductButton.addEventListener("click", () => refreshWorkspaceForCurrentRole())' in app_js


def test_visible_app_copy_uses_professional_labels() -> None:
    source = INDEX_HTML.read_text() + APP_JS.read_text()

    assert "Viewing as" in source
    assert "Clinic administrator" in source
    assert "Reset workflow state" in source
    assert "Sync Gmail" in source
    for label in ["Dev only user", "Demo admin", "Developer demo", "demo-only", "local demo workflow"]:
        assert label not in source


def test_workbench_does_not_render_patient_reply_simulation_controls() -> None:
    app_js = APP_JS.read_text()

    assert "Simulate accepted reply" not in app_js
    assert "Simulate declined" not in app_js


def test_intake_workspace_uses_packet_state_to_gate_email_actions() -> None:
    app_js = APP_JS.read_text()

    assert "data.intake_packet_state ? `packet ${data.intake_packet_state.replaceAll" in app_js
    assert "if (data.can_draft_intake_packet)" in app_js
    assert "Draft intake packet" in app_js
    assert "if (data.can_draft_intake_reminder)" in app_js
    assert 'actionButton("Draft reminder"' in app_js
    assert 'actions.append(\n      actionButton("Save screening"' not in app_js


def test_workbench_renders_completion_patient_files_and_confirmed_appointments() -> None:
    app_js = APP_JS.read_text()
    styles = (ROOT / "frontend" / "styles.css").read_text()

    assert "Referral complete" in app_js
    assert 'button.classList.add("success")' in app_js
    assert "button.success" in styles
    assert 'operationSection("Appointments"' in app_js
    assert 'appointment.status === "confirmed"' in app_js
    assert "No confirmed appointments yet." in app_js
    assert "Patient files" in app_js
    assert "data.patient_files || []" in app_js
    assert "Template files" not in app_js


def test_overview_system_and_workbench_trace_static_contracts() -> None:
    index_html = INDEX_HTML.read_text()
    app_js = APP_JS.read_text()

    assert "overview-action-list" not in index_html
    assert "action-queue-title" not in index_html
    assert "overview-health-strip" not in index_html
    assert "overview-health-title" not in index_html
    assert '<details class="advanced-trace">' not in index_html
    assert 'href="/workbench" data-nav>Open Workbench</a>' in index_html
    assert "Select a referral in Workbench to inspect persisted agent activity" in index_html
    assert 'id="workbench-gmail-sync-button"' in index_html
    assert "workbenchGmailSyncButton.addEventListener" in app_js

    assert 'operationSection("Advanced trace"' in app_js
    assert "renderWorkbenchAdvancedTrace" in app_js
    assert "workbenchState.advanced_trace?.workflow_runs" in app_js
    assert "renderSimpleList(reviewSection.body, reviewableTasks" in app_js
    assert "reviewTaskCard" in app_js
    assert "agent-registry-card" in app_js
    assert "Turns inbound referral text into structured patient" in app_js
    assert "Summarizes referral, intake, risk, appointment, and patient context" in app_js
    assert "/api/review-tasks/${taskId}/actions" in app_js
