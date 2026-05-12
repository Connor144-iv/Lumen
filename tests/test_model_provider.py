from __future__ import annotations

from types import SimpleNamespace

from backend.lumen_agentic.agents import StructuredAgent, create_chat_model
from backend.lumen_agentic.config import Settings
from backend.lumen_agentic.schemas import ClinicalSignals
from backend.lumen_web import model_health


def test_settings_reads_huggingface_environment(monkeypatch) -> None:
    monkeypatch.setenv("LUMEN_LLM_PROVIDER", "huggingface")
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf_demo_token")
    monkeypatch.setenv("LUMEN_SMALL_MODEL", "openai/gpt-oss-120b:fastest")

    settings = Settings()

    assert settings.provider == "huggingface"
    assert settings.huggingface_api_key == "hf_demo_token"
    assert settings.small_model == "openai/gpt-oss-120b:fastest"
    assert settings.huggingface_base_url == "https://router.huggingface.co/v1"


def test_create_chat_model_supports_huggingface_router() -> None:
    settings = Settings(
        provider="huggingface",
        huggingface_api_key="hf_demo_token",
        small_model="openai/gpt-oss-120b:fastest",
    )

    model = create_chat_model(settings, settings.small_model)

    assert model.model_name == "openai/gpt-oss-120b:fastest"
    assert str(model.openai_api_base) == "https://router.huggingface.co/v1"


def test_model_health_checks_huggingface_openai_compatible_models(monkeypatch) -> None:
    calls = []

    def fake_get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, object]:
        calls.append((url, headers))
        return {"data": [{"id": "openai/gpt-oss-120b:fastest"}]}

    monkeypatch.setattr(model_health, "_get_json", fake_get_json)

    result = model_health.check_configured_models(
        Settings(
            provider="huggingface",
            huggingface_api_key="hf_demo_token",
            small_model="openai/gpt-oss-120b:fastest",
            medium_model="openai/gpt-oss-120b:fastest",
            communication_model="openai/gpt-oss-120b:fastest",
        )
    )

    assert result["provider"] == "huggingface"
    assert result["ok"] is True
    assert calls
    assert calls[0][0] == "https://router.huggingface.co/v1/models"
    assert calls[0][1] == {"Authorization": "Bearer hf_demo_token"}


class FakePlainJsonLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.messages = []
        self.structured_output_called = False

    def with_structured_output(self, _schema):
        self.structured_output_called = True
        raise AssertionError("Hugging Face agents should not use response_format schemas.")

    def invoke(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(content=self.responses.pop(0))


def test_huggingface_structured_agent_uses_plain_json_and_local_validation() -> None:
    llm = FakePlainJsonLLM(
        [
            """
            ```json
            {
              "presenting_concern": "anxiety",
              "language_preference": null,
              "modality_preference": "unknown",
              "availability_text": null,
              "age_band": "adult",
              "missing_required_fields": [],
              "source_spans": []
            }
            ```
            """
        ]
    )
    agent = StructuredAgent(
        name="clinical_signal_extractor",
        llm=llm,
        system_prompt="Extract facts.",
        output_schema=ClinicalSignals,
        provider="huggingface",
    )

    result = agent.invoke({"raw_text": "My name is Simon and I feel anxious."})

    assert result.presenting_concern == "anxiety"
    assert result.age_band == "adult"
    assert llm.structured_output_called is False
    assert "Return exactly one valid JSON object" in llm.messages[0][0].content


def test_huggingface_structured_agent_repairs_invalid_plain_json_once() -> None:
    llm = FakePlainJsonLLM(
        [
            "not json",
            """
            {
              "presenting_concern": null,
              "language_preference": null,
              "modality_preference": "unknown",
              "availability_text": null,
              "age_band": "unknown",
              "missing_required_fields": ["patient_name"],
              "source_spans": []
            }
            """,
        ]
    )
    agent = StructuredAgent(
        name="clinical_signal_extractor",
        llm=llm,
        system_prompt="Extract facts.",
        output_schema=ClinicalSignals,
        provider="huggingface",
    )

    result = agent.invoke({"raw_text": "Hello."})

    assert result.missing_required_fields == ["patient_name"]
    assert len(llm.messages) == 2
    assert llm.structured_output_called is False
