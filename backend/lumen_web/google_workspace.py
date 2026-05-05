"""Google Workspace provider boundary for Gmail and Calendar."""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET_DIR = REPO_ROOT / "backend" / "secret"
TOKEN_FILE_NAME = "google_token.json"

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GOOGLE_WORKSPACE_SCOPES = [GMAIL_SEND_SCOPE, CALENDAR_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE]
GOOGLE_WORKSPACE_SCOPE_LABELS = ["gmail.send", "calendar.readonly", "calendar.events"]

_LAST_PROVIDER_ERROR: str | None = None


class GoogleWorkspaceError(RuntimeError):
    """Raised when the configured Google Workspace provider cannot complete an operation."""


@dataclass(frozen=True)
class GoogleWorkspaceSettings:
    enabled: bool
    client_secret_path: Path
    token_path: Path
    calendar_id: str
    timezone: str


def settings() -> GoogleWorkspaceSettings:
    return GoogleWorkspaceSettings(
        enabled=os.getenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false").strip().lower() == "true",
        client_secret_path=_configured_client_secret_path(),
        token_path=Path(os.getenv("LUMEN_GOOGLE_TOKEN_PATH") or SECRET_DIR / TOKEN_FILE_NAME),
        calendar_id=os.getenv("LUMEN_GOOGLE_CALENDAR_ID", "primary").strip() or "primary",
        timezone=os.getenv("LUMEN_GOOGLE_TIMEZONE", "Europe/Lisbon").strip() or "Europe/Lisbon",
    )


def is_enabled() -> bool:
    return settings().enabled


def provider_error_message(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    text = re.sub(r"ya29\.[A-Za-z0-9._-]+", "[redacted-token]", text)
    text = re.sub(r"refresh_token[\"'=:\s]+[A-Za-z0-9._/-]+", "refresh_token=[redacted]", text, flags=re.I)
    text = re.sub(r"client_secret[\"'=:\s]+[A-Za-z0-9._/-]+", "client_secret=[redacted]", text, flags=re.I)
    return text[:500]


def google_workspace_status(refresh: bool = True) -> dict[str, Any]:
    cfg = settings()
    token_present = cfg.token_path.exists()
    token_valid = False
    authorized = False
    dependency_available = True
    error = _LAST_PROVIDER_ERROR

    try:
        _google_imports()
    except GoogleWorkspaceError as exc:
        dependency_available = False
        error = provider_error_message(exc)

    if token_present and dependency_available:
        try:
            credentials = _load_credentials(cfg, refresh=refresh)
            token_valid = bool(credentials.valid)
            authorized = token_valid and _credentials_have_scopes(credentials)
        except GoogleWorkspaceError as exc:
            error = provider_error_message(exc)

    return {
        "enabled": cfg.enabled,
        "authorized": authorized,
        "token_present": token_present,
        "token_valid": token_valid,
        "dependencies_available": dependency_available,
        "client_secret_present": cfg.client_secret_path.exists(),
        "calendar_id": cfg.calendar_id,
        "timezone": cfg.timezone,
        "configured_scopes": GOOGLE_WORKSPACE_SCOPE_LABELS,
        "scope_urls": GOOGLE_WORKSPACE_SCOPES,
        "last_provider_error": error,
    }


def send_approved_draft(
    *,
    recipient_email: str,
    subject: str | None,
    body: str,
    sender: str = "me",
) -> dict[str, Any]:
    gmail = _build_gmail_service()
    message = EmailMessage()
    message["To"] = recipient_email
    message["Subject"] = subject or "Message from Lumen Clinic"
    message.set_content(body)

    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    try:
        result = gmail.users().messages().send(userId=sender, body={"raw": encoded}).execute()
    except Exception as exc:  # pragma: no cover - exercised with mocked provider tests
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Gmail send failed: {provider_error_message(exc)}") from exc

    _clear_error()
    return {
        "provider": "gmail",
        "message_id": result.get("id"),
        "thread_id": result.get("threadId"),
        "raw_response": _public_response(result),
    }


def query_calendar_busy(
    *,
    time_min: datetime,
    time_max: datetime,
    calendar_id: str | None = None,
) -> list[dict[str, datetime]]:
    cfg = settings()
    calendar = _build_calendar_service()
    calendar_ref = calendar_id or cfg.calendar_id
    body = {
        "timeMin": _google_datetime(time_min),
        "timeMax": _google_datetime(time_max),
        "timeZone": cfg.timezone,
        "items": [{"id": calendar_ref}],
    }
    try:
        result = calendar.freebusy().query(body=body).execute()
    except Exception as exc:  # pragma: no cover - exercised with mocked provider tests
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Google Calendar free/busy failed: {provider_error_message(exc)}") from exc

    _clear_error()
    calendar_result = (result.get("calendars") or {}).get(calendar_ref) or {}
    return [
        {"start": _parse_google_datetime(item.get("start")), "end": _parse_google_datetime(item.get("end"))}
        for item in calendar_result.get("busy") or []
        if item.get("start") and item.get("end")
    ]


def create_appointment_event(
    *,
    appointment_id: str,
    tenant_id: str,
    referral_id: str | None,
    starts_at: datetime,
    ends_at: datetime,
    patient_email: str | None = None,
    therapist_email: str | None = None,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    cfg = settings()
    calendar = _build_calendar_service()
    calendar_ref = calendar_id or cfg.calendar_id
    attendees = [{"email": email} for email in [patient_email, therapist_email] if email]
    body: dict[str, Any] = {
        "summary": "Clinic appointment",
        "description": "Appointment created from Lumen after human approval.",
        "start": {"dateTime": _google_datetime(starts_at), "timeZone": cfg.timezone},
        "end": {"dateTime": _google_datetime(ends_at), "timeZone": cfg.timezone},
        "extendedProperties": {
            "private": {
                "lumen_appointment_id": appointment_id,
                "lumen_referral_id": referral_id or "",
                "lumen_tenant_id": tenant_id,
            }
        },
    }
    if attendees:
        body["attendees"] = attendees

    try:
        request = calendar.events().insert(
            calendarId=calendar_ref,
            body=body,
            sendUpdates="all" if attendees else "none",
        )
        result = request.execute()
    except Exception as exc:  # pragma: no cover - exercised with mocked provider tests
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Google Calendar event creation failed: {provider_error_message(exc)}") from exc

    _clear_error()
    return {
        "provider": "google_calendar",
        "calendar_id": calendar_ref,
        "event_id": result.get("id"),
        "event_link": result.get("htmlLink"),
        "raw_response": _public_response(result),
    }


def test_calendar_read(window_minutes: int = 30) -> dict[str, Any]:
    start = datetime.now(timezone.utc) + timedelta(minutes=5)
    end = start + timedelta(minutes=max(1, window_minutes))
    busy = query_calendar_busy(time_min=start, time_max=end)
    return {
        "ok": True,
        "time_min": start.isoformat(),
        "time_max": end.isoformat(),
        "busy_count": len(busy),
        "calendar_id": settings().calendar_id,
    }


def _configured_client_secret_path() -> Path:
    explicit = os.getenv("LUMEN_GOOGLE_CLIENT_SECRET_PATH")
    if explicit:
        return Path(explicit)
    matches = sorted(SECRET_DIR.glob("client_secret_*.json"))
    if matches:
        return matches[0]
    return SECRET_DIR / "client_secret.json"


def _load_credentials(cfg: GoogleWorkspaceSettings, *, refresh: bool):
    Credentials, GoogleAuthRequest, _ = _google_imports()
    if not cfg.token_path.exists():
        raise GoogleWorkspaceError("Google token file is not present.")
    credentials = Credentials.from_authorized_user_file(str(cfg.token_path), GOOGLE_WORKSPACE_SCOPES)
    if credentials.expired and credentials.refresh_token and refresh:
        credentials.refresh(GoogleAuthRequest())
        cfg.token_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.token_path.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise GoogleWorkspaceError("Google token is not valid; run scripts/google_workspace_auth.py.")
    return credentials


def _build_gmail_service():
    _, _, build = _google_imports()
    return build("gmail", "v1", credentials=_load_credentials(settings(), refresh=True), cache_discovery=False)


def _build_calendar_service():
    _, _, build = _google_imports()
    return build("calendar", "v3", credentials=_load_credentials(settings(), refresh=True), cache_discovery=False)


def _google_imports():
    try:
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - depends on optional environment packages
        raise GoogleWorkspaceError(
            "Google API dependencies are not installed. Install google-api-python-client, "
            "google-auth-httplib2, and google-auth-oauthlib."
        ) from exc
    return Credentials, GoogleAuthRequest, build


def _credentials_have_scopes(credentials: Any) -> bool:
    has_scopes = getattr(credentials, "has_scopes", None)
    if callable(has_scopes):
        return bool(has_scopes(GOOGLE_WORKSPACE_SCOPES))
    granted = set(getattr(credentials, "scopes", None) or [])
    return set(GOOGLE_WORKSPACE_SCOPES).issubset(granted) if granted else bool(credentials.valid)


def _google_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_google_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _public_response(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key in {"id", "threadId", "htmlLink", "status"}}


def _remember_error(exc: BaseException) -> None:
    global _LAST_PROVIDER_ERROR
    _LAST_PROVIDER_ERROR = provider_error_message(exc)


def _clear_error() -> None:
    global _LAST_PROVIDER_ERROR
    _LAST_PROVIDER_ERROR = None
