"""Smoke test the Lumen web workflow API with local sample payloads."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = [
    ROOT / "samples" / "new_referral_standard.json",
    ROOT / "samples" / "session_completed_report.json",
]
TERMINAL = {"completed", "needs_review", "failed"}


def post_json(base_url: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return request_json(request)


def get_json(base_url: str, path: str) -> dict:
    request = urllib.request.Request(f"{base_url.rstrip('/')}{path}")
    return request_json(request)


def request_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{exc.code} {exc.reason}: {detail}") from exc


def run_sample(base_url: str, sample_path: Path, timeout_seconds: int) -> dict:
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    started = post_json(base_url, "/api/run-workflow", payload)
    job_id = started["job_id"]
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = get_json(base_url, f"/api/status/{job_id}")
        if status["status"] in TERMINAL:
            return status
        time.sleep(2)
    raise TimeoutError(f"{sample_path.name} did not finish within {timeout_seconds} seconds.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    print(json.dumps(get_json(args.base_url, "/api/health/models"), indent=2))
    for sample in SAMPLES:
        result = run_sample(args.base_url, sample, args.timeout)
        print(f"{sample.name}: {result['status']} ({result['job_id']})")
        if result["status"] == "failed":
            print(result.get("error"))


if __name__ == "__main__":
    main()
