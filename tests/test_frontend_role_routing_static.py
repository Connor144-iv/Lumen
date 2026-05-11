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
    assert "/audio/transcribe" in app_js


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
    assert "Back to Dashboard" in index_html
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
