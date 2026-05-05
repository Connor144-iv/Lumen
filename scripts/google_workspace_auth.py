"""Authorize the local Google Workspace integration."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.lumen_web.google_workspace import GOOGLE_WORKSPACE_SCOPES, settings


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise SystemExit(
            "Google OAuth dependency is missing. Install google-auth-oauthlib before running this script."
        ) from exc

    cfg = settings()
    if not cfg.client_secret_path.exists():
        raise SystemExit(f"Google OAuth client secret file was not found at {cfg.client_secret_path}.")

    flow = InstalledAppFlow.from_client_secrets_file(str(cfg.client_secret_path), GOOGLE_WORKSPACE_SCOPES)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    cfg.token_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.token_path.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Google Workspace token saved to {cfg.token_path}.")


if __name__ == "__main__":
    main()
