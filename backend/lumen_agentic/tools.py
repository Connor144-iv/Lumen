"""Tool schemas and deterministic tool implementations for Lumen agents."""

from __future__ import annotations

import hashlib
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .schemas import EvidenceRef, ReportDraft


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalQuery(ToolModel):
    """Pydantic schema for the RAG retrieval tool.

    The metadata fields mirror the Phase 3 tenant/patient isolation rules. The
    retriever must filter by these before similarity search.
    """

    query_text: str = Field(min_length=1)
    tenant_id: str
    clinic_id: str | None = None
    therapist_id: str | None = None
    patient_id: str | None = None
    document_types: list[
        Literal["protocol", "template", "session_note", "score", "clinical_reference", "insurer_rule"]
    ] = Field(default_factory=list)
    protocol_id: str | None = None
    consent_scope: str | None = None
    top_k: int = Field(default=8, ge=1, le=20)
    min_score: float = Field(default=0.35, ge=0, le=1)

    def metadata_filter(self) -> dict[str, Any]:
        filters: dict[str, Any] = {"tenant_id": self.tenant_id}
        for key in ("clinic_id", "therapist_id", "patient_id", "protocol_id", "consent_scope"):
            value = getattr(self, key)
            if value:
                filters[key] = value
        if self.document_types:
            filters["document_type"] = {"$in": self.document_types}
        return filters


class RetrievedChunk(ToolModel):
    chunk_id: str
    source_type: Literal["protocol", "template", "session_note", "score", "clinical_reference", "insurer_rule"]
    source_id: str
    text: str
    score: float = Field(ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def evidence_ref(self) -> EvidenceRef:
        return EvidenceRef(
            source_type="protocol" if self.source_type == "clinical_reference" else self.source_type,
            source_id=self.source_id,
            quote_hash=hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            relevance_score=self.score,
        )


class RetrievalResult(ToolModel):
    query: RetrievalQuery
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    weak_evidence: bool = False

    @property
    def evidence_refs(self) -> list[EvidenceRef]:
        return [chunk.evidence_ref() for chunk in self.chunks]


class ReportCitationValidationInput(ToolModel):
    """Schema for the citation validator used by the report writer."""

    report: ReportDraft
    min_claims_with_evidence_ratio: float = Field(default=1.0, ge=0, le=1)


class ReportCitationValidationResult(ToolModel):
    valid: bool
    claims_checked: int
    unsupported_claims: list[str] = Field(default_factory=list)
    message: str


class ReportFormattingInput(ToolModel):
    """Simple formatting tool for output normalization before human review."""

    title: str
    sections: list[dict[str, str]]
    include_evidence_footer: bool = True


class ReportFormattingResult(ToolModel):
    markdown: str


class RAGRetriever(Protocol):
    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Return tenant-scoped chunks for a validated retrieval query."""


class EmptyRAGRetriever:
    """Fail-closed retriever used when pgvector is not configured."""

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        return RetrievalResult(query=query, chunks=[], weak_evidence=True)


class PgVectorRAGRetriever:
    """Thin adapter around LangChain PGVector.

    Phase 3 recommends PostgreSQL + pgvector so vectors remain under the same
    tenant isolation, backup, and audit regime as application records.
    """

    def __init__(self, vector_store: Any):
        self.vector_store = vector_store

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        docs_with_scores = self.vector_store.similarity_search_with_score(
            query.query_text,
            k=query.top_k,
            filter=query.metadata_filter(),
        )
        chunks: list[RetrievedChunk] = []
        for doc, raw_score in docs_with_scores:
            score = _normalise_similarity(raw_score)
            metadata = dict(doc.metadata or {})
            chunks.append(
                RetrievedChunk(
                    chunk_id=str(metadata.get("chunk_id", metadata.get("id", ""))),
                    source_type=metadata.get("document_type", "protocol"),
                    source_id=str(metadata.get("source_id", metadata.get("document_id", ""))),
                    text=doc.page_content,
                    score=score,
                    metadata=metadata,
                )
            )
        strong_chunks = [chunk for chunk in chunks if chunk.score >= query.min_score]
        return RetrievalResult(query=query, chunks=strong_chunks, weak_evidence=not strong_chunks)


def _normalise_similarity(raw_score: float) -> float:
    """Convert common distance outputs into a 0..1 relevance score."""

    if 0 <= raw_score <= 1:
        return 1 - raw_score
    return max(0.0, min(1.0, raw_score))


def validate_report_citations(input_data: ReportCitationValidationInput) -> ReportCitationValidationResult:
    report = input_data.report
    unsupported = list(report.unsupported_claims)
    checked = len(report.claim_evidence_map)

    for item in report.claim_evidence_map:
        claim = str(item.get("claim", "")).strip()
        evidence = item.get("evidence", [])
        if claim and not evidence:
            unsupported.append(claim)

    valid_claims = max(0, checked - len(unsupported))
    ratio = 1.0 if checked == 0 else valid_claims / checked
    valid = not unsupported and ratio >= input_data.min_claims_with_evidence_ratio
    message = "All report claims have evidence." if valid else "Report contains unsupported claims."
    return ReportCitationValidationResult(
        valid=valid,
        claims_checked=checked,
        unsupported_claims=unsupported,
        message=message,
    )


def format_report_markdown(input_data: ReportFormattingInput) -> ReportFormattingResult:
    lines = [f"# {input_data.title.strip()}", ""]
    for section in input_data.sections:
        heading = section.get("heading", "Section").strip()
        body = section.get("body", "").strip()
        lines.extend([f"## {heading}", "", body, ""])
    if input_data.include_evidence_footer:
        lines.extend(["---", "Draft only. Requires therapist review and sign-off."])
    return ReportFormattingResult(markdown="\n".join(lines).strip())


def build_retrieval_tool(retriever: RAGRetriever):
    from langchain_core.tools import StructuredTool

    def _retrieve(**kwargs: Any) -> dict[str, Any]:
        query = RetrievalQuery.model_validate(kwargs)
        return retriever.retrieve(query).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_retrieve,
        name="retrieve_clinical_context",
        description=(
            "Tenant-scoped hybrid RAG retrieval for protocols, templates, "
            "patient history, scores, and insurer rules."
        ),
        args_schema=RetrievalQuery,
    )


def build_report_formatting_tool():
    from langchain_core.tools import StructuredTool

    def _format(**kwargs: Any) -> dict[str, Any]:
        input_data = ReportFormattingInput.model_validate(kwargs)
        return format_report_markdown(input_data).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_format,
        name="format_report_markdown",
        description="Normalize report sections into markdown before therapist review.",
        args_schema=ReportFormattingInput,
    )

