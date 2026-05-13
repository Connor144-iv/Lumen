"""Google Workspace provider boundary for Gmail and Calendar."""

from __future__ import annotations

import base64
import importlib
import logging
import os
import re
import sys
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
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GOOGLE_WORKSPACE_SCOPES = [GMAIL_SEND_SCOPE, CALENDAR_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE, GMAIL_MODIFY_SCOPE]
GOOGLE_WORKSPACE_SCOPE_LABELS = ["gmail.send", "calendar.readonly", "calendar.events", "gmail.modify"]

_LAST_PROVIDER_ERROR: str | None = None
logger = logging.getLogger(__name__)


class GoogleWorkspaceError(RuntimeError):
    """Raised when the configured Google Workspace provider cannot complete an operation."""


class GoogleDependencyError(GoogleWorkspaceError):
    """Raised when a required Google package is not importable."""


@dataclass(frozen=True)
class GoogleWorkspaceSettings:
    enabled: bool
    client_secret_path: Path
    token_path: Path
    calendar_id: str
    timezone: str
    expected_gmail_account: str | None


def settings() -> GoogleWorkspaceSettings:
    return GoogleWorkspaceSettings(
        enabled=os.getenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false").strip().lower() == "true",
        client_secret_path=_configured_client_secret_path(),
        token_path=Path(os.getenv("LUMEN_GOOGLE_TOKEN_PATH") or SECRET_DIR / TOKEN_FILE_NAME),
        calendar_id=os.getenv("LUMEN_GOOGLE_CALENDAR_ID", "primary").strip() or "primary",
        timezone=os.getenv("LUMEN_GOOGLE_TIMEZONE", "Europe/Lisbon").strip() or "Europe/Lisbon",
        expected_gmail_account=(os.getenv("LUMEN_GOOGLE_EXPECTED_GMAIL_ACCOUNT", "").strip() or None),
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
    error: str | None = None
    gmail_email_address: str | None = None

    try:
        _google_imports()
    except GoogleDependencyError as exc:
        dependency_available = False
        error = provider_error_message(exc)
        logger.warning("Google Workspace dependency check failed: %s", error)
    except GoogleWorkspaceError as exc:
        dependency_available = False
        error = provider_error_message(exc)
        logger.warning("Google Workspace import check failed: %s", error)

    if token_present and dependency_available:
        try:
            credentials = _load_credentials(cfg, refresh=refresh)
            token_valid = bool(credentials.valid)
            authorized = token_valid and _credentials_have_scopes(credentials)
            if authorized:
                try:
                    gmail_email_address = gmail_profile_email()
                except GoogleWorkspaceError as exc:
                    error = provider_error_message(exc)
                _clear_error()
        except GoogleWorkspaceError as exc:
            error = provider_error_message(exc)
            logger.warning("Google Workspace authorization check failed: %s", error)
    elif dependency_available:
        _clear_error()

    return {
        "enabled": cfg.enabled,
        "authorized": authorized,
        "token_present": token_present,
        "token_valid": token_valid,
        "dependencies_available": dependency_available,
        "client_secret_present": cfg.client_secret_path.exists(),
        "calendar_id": cfg.calendar_id,
        "timezone": cfg.timezone,
        "gmail_email_address": gmail_email_address,
        "expected_gmail_account": cfg.expected_gmail_account,
        "account_matches_expected": _account_matches_expected(gmail_email_address, cfg.expected_gmail_account),
        "configured_scopes": GOOGLE_WORKSPACE_SCOPE_LABELS,
        "scope_urls": GOOGLE_WORKSPACE_SCOPES,
        "last_provider_error": error,
    }


def gmail_account_mismatch_message() -> str | None:
    cfg = settings()
    if not cfg.expected_gmail_account:
        return None
    actual = gmail_profile_email()
    if _account_matches_expected(actual, cfg.expected_gmail_account):
        return None
    return (
        f"Google Workspace is authorized for {actual or 'an unknown Gmail account'}, "
        f"but Lumen is configured to sync {cfg.expected_gmail_account}. "
        "Re-run scripts/google_workspace_auth.py and choose the admin clinic Gmail account."
    )


def gmail_profile_email() -> str | None:
    gmail = _build_gmail_service()
    try:
        profile = gmail.users().getProfile(userId="me").execute()
    except Exception as exc:  # pragma: no cover - exercised with mocked provider tests
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Gmail profile fetch failed: {provider_error_message(exc)}") from exc
    _clear_error()
    return str(profile.get("emailAddress") or "").strip() or None


def _account_matches_expected(actual: str | None, expected: str | None) -> bool | None:
    if not expected:
        return None
    if not actual:
        return False
    return actual.strip().lower() == expected.strip().lower()


def send_approved_draft(
    *,
    recipient_email: str,
    subject: str | None,
    body: str,
    attachments: list[dict[str, Any]] | None = None,
    sender: str = "me",
) -> dict[str, Any]:
    gmail = _build_gmail_service()
    message = EmailMessage()
    message["To"] = recipient_email
    message["Subject"] = subject or "Message from Lumen Clinic"
    message.set_content(body)
    for attachment in attachments or []:
        content = attachment.get("content") or b""
        if isinstance(content, str):
            content = content.encode("utf-8")
        content_type = str(attachment.get("content_type") or "application/octet-stream")
        maintype, _, subtype = content_type.partition("/")
        message.add_attachment(
            bytes(content),
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=str(attachment.get("file_name") or "attachment"),
        )

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


def list_unread_gmail_messages(
    *,
    sender_email: str | None = None,
    query: str | None = None,
    max_results: int = 10,
    unread_only: bool = True,
) -> list[dict[str, str]]:
    gmail = _build_gmail_service()
    terms = ["is:unread"] if unread_only else ["in:inbox"]
    if sender_email:
        terms.append(f"from:{sender_email}")
    if query:
        terms.append(query)
    try:
        result = gmail.users().messages().list(
            userId="me",
            q=" ".join(terms),
            maxResults=max(1, min(max_results, 50)),
        ).execute()
    except Exception as exc:  # pragma: no cover - exercised with mocked provider tests
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Gmail list unread failed: {provider_error_message(exc)}") from exc

    _clear_error()
    return result.get("messages") or []


def get_gmail_message(*, message_id: str, format: str = "full") -> dict[str, Any]:
    gmail = _build_gmail_service()
    try:
        result = gmail.users().messages().get(userId="me", id=message_id, format=format).execute()
    except Exception as exc:  # pragma: no cover - exercised with mocked provider tests
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Gmail message fetch failed: {provider_error_message(exc)}") from exc

    _clear_error()
    return result


def download_gmail_attachment(*, message_id: str, attachment_id: str) -> bytes:
    gmail = _build_gmail_service()
    try:
        result = (
            gmail.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
    except Exception as exc:  # pragma: no cover - exercised with mocked provider tests
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Gmail attachment download failed: {provider_error_message(exc)}") from exc

    _clear_error()
    return _decode_gmail_attachment(result.get("data") or "")


def parse_gmail_message(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload") or {}
    headers = _gmail_headers(payload)
    message_id = message.get("id")
    return {
        "message_id": message_id,
        "thread_id": message.get("threadId"),
        "snippet": message.get("snippet") or "",
        "subject": headers.get("Subject", ""),
        "from": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "body": _gmail_message_body(payload),
        "attachments": _gmail_message_attachments(payload, message_id=str(message_id or "")),
    }


def mark_gmail_message_read(*, message_id: str) -> None:
    gmail = _build_gmail_service()
    try:
        gmail.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
    except Exception as exc:  # pragma: no cover - exercised with mocked provider tests
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Gmail mark read failed: {provider_error_message(exc)}") from exc

    _clear_error()


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
    patient_name: str | None = None,
    therapist_name: str | None = None,
    therapist_id: str | None = None,
    patient_email: str | None = None,
    therapist_email: str | None = None,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    cfg = settings()
    calendar = _build_calendar_service()
    calendar_ref = calendar_id or cfg.calendar_id
    attendees = [{"email": email} for email in [patient_email, therapist_email] if email]
    body: dict[str, Any] = {
        "summary": _appointment_summary(therapist_name=therapist_name, patient_name=patient_name, referral_id=referral_id),
        "description": "Appointment created from Lumen after human approval.",
        "start": {"dateTime": _google_datetime(starts_at), "timeZone": cfg.timezone},
        "end": {"dateTime": _google_datetime(ends_at), "timeZone": cfg.timezone},
        "extendedProperties": {
            "private": {
                "lumen_appointment_id": appointment_id,
                "lumen_referral_id": referral_id or "",
                "lumen_tenant_id": tenant_id,
                "lumen_therapist_id": therapist_id or "",
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


def update_appointment_event(
    *,
    event_id: str,
    appointment_id: str,
    tenant_id: str,
    referral_id: str | None,
    starts_at: datetime,
    ends_at: datetime,
    patient_name: str | None = None,
    therapist_name: str | None = None,
    therapist_id: str | None = None,
    patient_email: str | None = None,
    therapist_email: str | None = None,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    cfg = settings()
    calendar = _build_calendar_service()
    calendar_ref = calendar_id or cfg.calendar_id
    attendees = [{"email": email} for email in [patient_email, therapist_email] if email]
    body: dict[str, Any] = {
        "summary": _appointment_summary(therapist_name=therapist_name, patient_name=patient_name, referral_id=referral_id),
        "description": "Appointment updated from Lumen after human approval.",
        "start": {"dateTime": _google_datetime(starts_at), "timeZone": cfg.timezone},
        "end": {"dateTime": _google_datetime(ends_at), "timeZone": cfg.timezone},
        "extendedProperties": {
            "private": {
                "lumen_appointment_id": appointment_id,
                "lumen_referral_id": referral_id or "",
                "lumen_tenant_id": tenant_id,
                "lumen_therapist_id": therapist_id or "",
            }
        },
    }
    if attendees:
        body["attendees"] = attendees

    try:
        result = calendar.events().patch(
            calendarId=calendar_ref,
            eventId=event_id,
            body=body,
            sendUpdates="all" if attendees else "none",
        ).execute()
    except Exception as exc:  # pragma: no cover - exercised with mocked provider tests
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Google Calendar event update failed: {provider_error_message(exc)}") from exc

    _clear_error()
    return {
        "provider": "google_calendar",
        "calendar_id": calendar_ref,
        "event_id": result.get("id") or event_id,
        "event_link": result.get("htmlLink"),
        "raw_response": _public_response(result),
    }


def list_lumen_appointment_events(
    *,
    time_min: datetime,
    time_max: datetime,
    calendar_id: str | None = None,
) -> list[dict[str, Any]]:
    cfg = settings()
    calendar = _build_calendar_service()
    calendar_ref = calendar_id or cfg.calendar_id
    try:
        result = calendar.events().list(
            calendarId=calendar_ref,
            timeMin=_google_datetime(time_min),
            timeMax=_google_datetime(time_max),
            singleEvents=True,
        ).execute()
    except Exception as exc:  # pragma: no cover - exercised with mocked provider tests
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Google Calendar event list failed: {provider_error_message(exc)}") from exc

    _clear_error()
    events = []
    for item in result.get("items") or []:
        private = ((item.get("extendedProperties") or {}).get("private") or {})
        if not private and not str(item.get("summary") or "").startswith("[Lumen]"):
            continue
        start_value = (item.get("start") or {}).get("dateTime")
        end_value = (item.get("end") or {}).get("dateTime")
        events.append(
            {
                "id": item.get("id"),
                "summary": item.get("summary"),
                "htmlLink": item.get("htmlLink"),
                "start": _parse_google_datetime(start_value) if start_value else None,
                "end": _parse_google_datetime(end_value) if end_value else None,
                "lumen_appointment_id": private.get("lumen_appointment_id"),
                "lumen_referral_id": private.get("lumen_referral_id"),
                "lumen_therapist_id": private.get("lumen_therapist_id"),
            }
        )
    return events


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
        raise GoogleWorkspaceError(f"Google token file is not present at {cfg.token_path}.")
    try:
        credentials = Credentials.from_authorized_user_file(str(cfg.token_path), GOOGLE_WORKSPACE_SCOPES)
    except Exception as exc:
        raise GoogleWorkspaceError(
            f"Google token could not be loaded from {cfg.token_path}: {provider_error_message(exc)}"
        ) from exc
    if credentials.expired and credentials.refresh_token and refresh:
        try:
            credentials.refresh(GoogleAuthRequest())
            cfg.token_path.parent.mkdir(parents=True, exist_ok=True)
            cfg.token_path.write_text(credentials.to_json(), encoding="utf-8")
        except Exception as exc:
            _remember_error(exc)
            raise GoogleWorkspaceError(f"Google token refresh failed: {provider_error_message(exc)}") from exc
    if not credentials.valid:
        raise GoogleWorkspaceError(
            f"Google token at {cfg.token_path} is not valid; run scripts/google_workspace_auth.py."
        )
    return credentials


def _build_gmail_service():
    _, _, build = _google_imports()
    try:
        return build("gmail", "v1", credentials=_load_credentials(settings(), refresh=True), cache_discovery=False)
    except GoogleWorkspaceError:
        raise
    except Exception as exc:
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Gmail service build failed: {provider_error_message(exc)}") from exc


def _build_calendar_service():
    _, _, build = _google_imports()
    try:
        return build("calendar", "v3", credentials=_load_credentials(settings(), refresh=True), cache_discovery=False)
    except GoogleWorkspaceError:
        raise
    except Exception as exc:
        _remember_error(exc)
        raise GoogleWorkspaceError(f"Google Calendar service build failed: {provider_error_message(exc)}") from exc


def _google_imports():
    _ensure_google_import_path()
    modules = {
        "google.auth.transport.requests": "google-auth",
        "google.oauth2.credentials": "google-auth",
        "googleapiclient.discovery": "google-api-python-client",
        "google_auth_httplib2": "google-auth-httplib2",
        "google_auth_oauthlib.flow": "google-auth-oauthlib",
    }
    imported: dict[str, Any] = {}
    for module_name, package_name in modules.items():
        try:
            imported[module_name] = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            missing_name = str(exc.name or "")
            requested_root = module_name.split(".", 1)[0]
            if missing_name == requested_root or module_name.startswith(f"{missing_name}."):
                raise GoogleDependencyError(
                    f"Missing Google API dependency for module '{module_name}' "
                    f"(install package '{package_name}'): {provider_error_message(exc)}"
                ) from exc
            raise GoogleWorkspaceError(
                f"Google API import failed while loading '{module_name}' because transitive module "
                f"'{missing_name}' could not be imported: {provider_error_message(exc)}"
            ) from exc
        except ImportError as exc:
            raise GoogleWorkspaceError(
                f"Google API import failed while loading '{module_name}': {provider_error_message(exc)}"
            ) from exc
        except Exception as exc:
            raise GoogleWorkspaceError(
                f"Google API import raised {exc.__class__.__name__} while loading '{module_name}': "
                f"{provider_error_message(exc)}"
            ) from exc
    return (
        imported["google.oauth2.credentials"].Credentials,
        imported["google.auth.transport.requests"].Request,
        imported["googleapiclient.discovery"].build,
    )


def _ensure_google_import_path() -> None:
    candidates = []
    virtual_env = os.getenv("VIRTUAL_ENV")
    if virtual_env:
        candidates.append(Path(virtual_env) / "Lib" / "site-packages")
    candidates.append(Path(sys.prefix) / "Lib" / "site-packages")
    candidates.append(REPO_ROOT / ".venv" / "Lib" / "site-packages")

    promoted_paths: set[str] = set()
    for site_packages in candidates:
        if not site_packages.exists():
            continue
        site_path = str(site_packages)
        normalized_site_path = os.path.normcase(os.path.abspath(site_path))
        if normalized_site_path not in promoted_paths:
            sys.path[:] = [
                existing_path
                for existing_path in sys.path
                if os.path.normcase(os.path.abspath(existing_path or ".")) != normalized_site_path
            ]
            sys.path.insert(0, site_path)
            promoted_paths.add(normalized_site_path)
        google_path = site_packages / "google"
        google_module = sys.modules.get("google")
        google_module_path = getattr(google_module, "__path__", None)
        if google_path.exists() and google_module_path is not None and str(google_path) not in google_module_path:
            google_module_path.append(str(google_path))
    importlib.invalidate_caches()


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


def _gmail_headers(payload: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in payload.get("headers") or []:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if name:
            headers[name] = value
    return headers


def _gmail_message_body(payload: dict[str, Any]) -> str:
    body = _gmail_message_body_plain(payload)
    return body.strip()


def _gmail_message_attachments(payload: dict[str, Any], *, message_id: str) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []

    def visit(part: dict[str, Any]) -> None:
        filename = str(part.get("filename") or "").strip()
        body = part.get("body") or {}
        attachment_id = str(body.get("attachmentId") or "").strip()
        if filename or attachment_id:
            attachments.append(
                {
                    "message_id": message_id,
                    "attachment_id": attachment_id or None,
                    "file_name": filename or "attachment",
                    "mime_type": str(part.get("mimeType") or "application/octet-stream"),
                    "size_bytes": body.get("size"),
                }
            )
        for child in part.get("parts") or []:
            visit(child)

    if payload:
        visit(payload)
    return attachments


def _gmail_message_body_plain(payload: dict[str, Any]) -> str:
    if not payload:
        return ""
    mime_type = str(payload.get("mimeType") or "")
    data = (payload.get("body") or {}).get("data")
    if data and mime_type.startswith("text/plain"):
        return _decode_gmail_body(data)
    parts = payload.get("parts") or []
    if parts:
        for part in parts:
            part_type = str(part.get("mimeType") or "")
            part_data = (part.get("body") or {}).get("data")
            if part_data and part_type.startswith("text/plain"):
                return _decode_gmail_body(part_data)
        for part in parts:
            nested = _gmail_message_body_plain(part)
            if nested:
                return nested
        for part in parts:
            part_type = str(part.get("mimeType") or "")
            part_data = (part.get("body") or {}).get("data")
            if part_data and part_type.startswith("text/html"):
                return _decode_gmail_body(part_data)
    if data:
        return _decode_gmail_body(data)
    return ""


def _decode_gmail_body(value: str) -> str:
    if not value:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception:
        return ""
    return decoded.decode("utf-8", errors="replace")


def _decode_gmail_attachment(value: str) -> bytes:
    if not value:
        return b""
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except Exception as exc:
        raise GoogleWorkspaceError("Gmail attachment payload was not valid base64url data.") from exc


def _appointment_summary(*, therapist_name: str | None, patient_name: str | None, referral_id: str | None) -> str:
    therapist = (therapist_name or "Unassigned").strip() or "Unassigned"
    patient = (patient_name or "Sarah O'Connor").strip() or "Sarah O'Connor"
    referral = (referral_id or "unlinked").strip() or "unlinked"
    return f"[Lumen] Therapist: {therapist} | Patient: {patient} | Referral: {referral}"


def _public_response(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key in {"id", "threadId", "htmlLink", "status"}}


def _remember_error(exc: BaseException) -> None:
    global _LAST_PROVIDER_ERROR
    _LAST_PROVIDER_ERROR = provider_error_message(exc)
    logger.warning("Google Workspace provider error: %s", _LAST_PROVIDER_ERROR)


def _clear_error() -> None:
    global _LAST_PROVIDER_ERROR
    _LAST_PROVIDER_ERROR = None
