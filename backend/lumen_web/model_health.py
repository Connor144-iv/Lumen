"""Model/provider health checks for local development and operator screens."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

from backend.lumen_agentic.config import Settings


@dataclass(frozen=True)
class ModelCheck:
    role: str
    provider: str
    model: str
    status: str
    message: str
    latency_ms: int


def check_configured_models(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or Settings()
    roles = {
        "small": settings.small_model,
        "medium": settings.medium_model,
        "communication": settings.communication_model,
    }
    checks = [_check_model(settings, role, model) for role, model in roles.items()]
    return {
        "provider": settings.provider,
        "ok": all(check.status == "ok" for check in checks),
        "checks": [asdict(check) for check in checks],
    }


def _check_model(settings: Settings, role: str, model: str) -> ModelCheck:
    started = time.perf_counter()
    provider = settings.provider.lower()
    try:
        if provider == "ollama":
            status, message = _check_ollama_model(settings.ollama_base_url, model)
        elif provider == "lmstudio":
            status, message = _check_openai_compatible_model(settings.lmstudio_base_url, model, api_key="lm-studio")
        elif provider == "openai":
            if not settings.openai_api_key:
                status, message = "unconfigured", "OPENAI_API_KEY is not set."
            else:
                status, message = _check_openai_compatible_model(
                    "https://api.openai.com/v1", model, api_key=settings.openai_api_key
                )
        elif provider == "huggingface":
            if not settings.huggingface_api_key:
                status, message = "unconfigured", "HUGGINGFACE_API_KEY is not set."
            else:
                status, message = _check_openai_compatible_model(
                    settings.huggingface_base_url,
                    model,
                    api_key=settings.huggingface_api_key,
                )
        elif provider == "anthropic":
            if not settings.anthropic_api_key:
                status, message = "unconfigured", "ANTHROPIC_API_KEY is not set."
            else:
                status, message = "configured", "Anthropic key is configured; run a workflow to validate the model."
        else:
            status, message = "unsupported", f"Unsupported LUMEN_LLM_PROVIDER: {settings.provider}"
    except Exception as exc:
        status, message = "unavailable", str(exc)

    latency_ms = int((time.perf_counter() - started) * 1000)
    return ModelCheck(role=role, provider=settings.provider, model=model, status=status, message=message, latency_ms=latency_ms)


def _check_ollama_model(base_url: str, model: str) -> tuple[str, str]:
    data = _get_json(f"{base_url.rstrip('/')}/api/tags")
    names = {item.get("name") for item in data.get("models", []) if isinstance(item, dict)}
    if model in names:
        return "ok", "Model is available from Ollama."
    return "missing", f"Model '{model}' is not listed by Ollama. Pull it or update LUMEN_*_MODEL."


def _check_openai_compatible_model(base_url: str, model: str, api_key: str) -> tuple[str, str]:
    data = _get_json(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    ids = {item.get("id") for item in data.get("data", []) if isinstance(item, dict)}
    if not ids:
        return "configured", "Provider responded, but did not return a model list."
    if model in ids:
        return "ok", "Model is available from the provider."
    return "missing", f"Model '{model}' was not returned by the provider model list."


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, object]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach model provider at {url}: {exc.reason}") from exc
    return json.loads(body)
