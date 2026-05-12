"""Runtime configuration for local and production model serving."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    """Environment-backed settings with local-first defaults from Phase 3."""

    provider: str = field(default_factory=lambda: os.getenv("LUMEN_LLM_PROVIDER", "ollama"))
    small_model: str = field(default_factory=lambda: os.getenv("LUMEN_SMALL_MODEL", "qwen3:8b"))
    medium_model: str = field(default_factory=lambda: os.getenv("LUMEN_MEDIUM_MODEL", "qwen3:14b-q4_K_M"))
    communication_model: str = field(default_factory=lambda: os.getenv("LUMEN_COMMUNICATION_MODEL", "mistral:7b-instruct"))
    embedding_model: str = field(default_factory=lambda: os.getenv("LUMEN_EMBEDDING_MODEL", "bge-m3"))

    llm_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LUMEN_LLM_TIMEOUT_SECONDS", "120")))

    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    lmstudio_base_url: str = field(default_factory=lambda: os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"))
    huggingface_base_url: str = field(default_factory=lambda: os.getenv("HUGGINGFACE_BASE_URL", "https://router.huggingface.co/v1"))
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    huggingface_api_key: str | None = field(default_factory=lambda: os.getenv("HUGGINGFACE_API_KEY"))
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))

    database_url: str | None = field(default_factory=lambda: os.getenv("DATABASE_URL"))
    rag_collection_name: str = field(default_factory=lambda: os.getenv("LUMEN_RAG_COLLECTION", "lumen_clinical_memory"))
    min_retrieval_score: float = field(default_factory=lambda: float(os.getenv("LUMEN_MIN_RETRIEVAL_SCORE", "0.35")))
