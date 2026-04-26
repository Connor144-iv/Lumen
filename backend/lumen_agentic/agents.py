"""Agent initialization and model binding for the Lumen backend."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Type

from pydantic import BaseModel

from .config import Settings
from .prompts import (
    CLINICAL_DOCUMENTATION_PROTOCOL_MATCHER_SYSTEM,
    CLINICAL_SIGNAL_EXTRACTOR_SYSTEM,
    CONSENT_INTAKE_COLLECTOR_SYSTEM,
    PATIENT_COMMUNICATION_DRAFTER_SYSTEM,
    REFERRAL_INTAKE_NORMALIZER_SYSTEM,
    REPORT_TREATMENT_REVIEW_WRITER_SYSTEM,
    SCHEMA_REPAIR_SYSTEM,
    THERAPIST_MATCHING_PLANNER_SYSTEM,
    WORKFLOW_ORCHESTRATOR_SYSTEM,
)
from .schemas import (
    ClinicalSignals,
    CommunicationDraft,
    ConsentIntakeSummary,
    OrchestratorDecision,
    ProtocolCoverageMap,
    ReferralRecord,
    ReportDraft,
    RiskReview,
    TherapistMatchRecommendation,
)
from .tools import EmptyRAGRetriever, RAGRetriever, build_report_formatting_tool, build_retrieval_tool


def create_chat_model(settings: Settings, model_name: str):
    """Create a chat model for Ollama, LM Studio/OpenAI-compatible, OpenAI, or Anthropic."""

    provider = settings.provider.lower()
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=model_name, base_url=settings.ollama_base_url, temperature=0)
    if provider == "lmstudio":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            base_url=settings.lmstudio_base_url,
            api_key="lm-studio",
            temperature=0,
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model_name, api_key=settings.openai_api_key, temperature=0)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model_name, api_key=settings.anthropic_api_key, temperature=0)
    raise ValueError(f"Unsupported LUMEN_LLM_PROVIDER: {settings.provider}")


def build_pgvector_retriever(settings: Settings) -> RAGRetriever:
    """Initialize the pgvector retriever specified in the Phase 3 RAG design."""

    if not settings.database_url:
        return EmptyRAGRetriever()

    from langchain_ollama import OllamaEmbeddings
    from langchain_postgres import PGVector

    embeddings = OllamaEmbeddings(model=settings.embedding_model, base_url=settings.ollama_base_url)
    vector_store = PGVector(
        embeddings=embeddings,
        connection=settings.database_url,
        collection_name=settings.rag_collection_name,
        use_jsonb=True,
    )
    from .tools import PgVectorRAGRetriever

    return PgVectorRAGRetriever(vector_store)


@dataclass
class StructuredAgent:
    """LLM-backed agent that must return one Pydantic schema."""

    name: str
    llm: Any
    system_prompt: str
    output_schema: Type[BaseModel]
    tools: list[Any] = field(default_factory=list)

    def invoke(self, payload: dict[str, Any]) -> BaseModel:
        from langchain_core.messages import HumanMessage, SystemMessage

        model = self.llm.bind_tools(self.tools) if self.tools else self.llm
        structured = model.with_structured_output(self.output_schema)
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
        ]
        try:
            result = structured.invoke(messages)
            return self._validate(result)
        except Exception as exc:
            # Phase 3 requires one schema repair attempt, then fail closed.
            repaired = self._repair(payload=payload, validation_error=str(exc))
            return self._validate(repaired)

    def _repair(self, payload: dict[str, Any], validation_error: str) -> Any:
        from langchain_core.messages import HumanMessage, SystemMessage

        repair_model = self.llm.with_structured_output(self.output_schema)
        return repair_model.invoke(
            [
                SystemMessage(content=SCHEMA_REPAIR_SYSTEM),
                HumanMessage(
                    content=json.dumps(
                        {
                            "agent_name": self.name,
                            "original_payload": payload,
                            "validation_error": validation_error,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                ),
            ]
        )

    def _validate(self, result: Any) -> BaseModel:
        if isinstance(result, self.output_schema):
            return result
        return self.output_schema.model_validate(result)


class RiskClassifierClient:
    """Replaceable v0 risk classifier adapter.

    Phase 3 calls for a fine-tuned Portuguese BERT/XLM-R classifier. This stub
    keeps the same contract so the graph can be developed before that service
    exists, and it deliberately escalates ambiguous failures.
    """

    high_risk_terms = (
        "suicidio",
        "suicida",
        "matar-me",
        "auto-mutilacao",
        "automutilacao",
        "self-harm",
        "abus",
        "violencia",
        "crise",
    )

    def classify(self, text: str) -> RiskReview:
        lowered = text.lower()
        matched = [term for term in self.high_risk_terms if term in lowered]
        if matched:
            return RiskReview(
                risk_present=True,
                risk_category="unknown",
                urgency="elevated",
                confidence=0.72,
                trigger_spans=[],
                required_handoff="clinician_review",
            )
        if not text.strip():
            return RiskReview(
                risk_present=True,
                risk_category="unknown",
                urgency="unknown",
                confidence=0.0,
                trigger_spans=[],
                required_handoff="clinician_review",
            )
        return RiskReview(
            risk_present=False,
            risk_category="none",
            urgency="standard",
            confidence=0.86,
            trigger_spans=[],
            required_handoff="continue",
        )


@dataclass
class AgentRuntime:
    orchestrator: StructuredAgent
    referral_intake: StructuredAgent
    clinical_signal: StructuredAgent
    risk_classifier: RiskClassifierClient
    therapist_matching: StructuredAgent
    communication_drafter: StructuredAgent
    consent_collector: StructuredAgent
    protocol_matcher: StructuredAgent
    report_writer: StructuredAgent
    clinical_retriever: RAGRetriever


def build_agent_runtime(settings: Settings | None = None) -> AgentRuntime:
    settings = settings or Settings()
    small_llm = create_chat_model(settings, settings.small_model)
    medium_llm = create_chat_model(settings, settings.medium_model)
    communication_llm = create_chat_model(settings, settings.communication_model)
    retriever = build_pgvector_retriever(settings)

    retrieval_tool = build_retrieval_tool(retriever)
    report_formatting_tool = build_report_formatting_tool()

    return AgentRuntime(
        orchestrator=StructuredAgent(
            name="workflow_orchestrator",
            llm=medium_llm,
            system_prompt=WORKFLOW_ORCHESTRATOR_SYSTEM,
            output_schema=OrchestratorDecision,
        ),
        referral_intake=StructuredAgent(
            name="referral_intake_normalizer",
            llm=small_llm,
            system_prompt=REFERRAL_INTAKE_NORMALIZER_SYSTEM,
            output_schema=ReferralRecord,
        ),
        clinical_signal=StructuredAgent(
            name="clinical_signal_extractor",
            llm=small_llm,
            system_prompt=CLINICAL_SIGNAL_EXTRACTOR_SYSTEM,
            output_schema=ClinicalSignals,
        ),
        risk_classifier=RiskClassifierClient(),
        therapist_matching=StructuredAgent(
            name="therapist_matching_planner",
            llm=small_llm,
            system_prompt=THERAPIST_MATCHING_PLANNER_SYSTEM,
            output_schema=TherapistMatchRecommendation,
        ),
        communication_drafter=StructuredAgent(
            name="patient_communication_drafter",
            llm=communication_llm,
            system_prompt=PATIENT_COMMUNICATION_DRAFTER_SYSTEM,
            output_schema=CommunicationDraft,
        ),
        consent_collector=StructuredAgent(
            name="consent_intake_collector",
            llm=small_llm,
            system_prompt=CONSENT_INTAKE_COLLECTOR_SYSTEM,
            output_schema=ConsentIntakeSummary,
        ),
        protocol_matcher=StructuredAgent(
            name="clinical_documentation_protocol_matcher",
            llm=medium_llm,
            system_prompt=CLINICAL_DOCUMENTATION_PROTOCOL_MATCHER_SYSTEM,
            output_schema=ProtocolCoverageMap,
            tools=[retrieval_tool],
        ),
        report_writer=StructuredAgent(
            name="report_treatment_review_writer",
            llm=medium_llm,
            system_prompt=REPORT_TREATMENT_REVIEW_WRITER_SYSTEM,
            output_schema=ReportDraft,
            tools=[retrieval_tool, report_formatting_tool],
        ),
        clinical_retriever=retriever,
    )
