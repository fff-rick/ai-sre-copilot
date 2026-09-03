"""Knowledge ingestion, bounded chunking, and hybrid retrieval contracts."""

import asyncio
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import AwareDatetime, Field, model_validator

from ai_sre_investigation.domain import Evidence, EvidenceReliability, FrozenModel

_WORD = re.compile(r"[\w.-]+", re.UNICODE)


class KnowledgeDocumentType(StrEnum):
    RUNBOOK = "runbook"
    SERVICE = "service"
    INCIDENT = "incident"


class KnowledgeDocument(FrozenModel):
    document_id: str = Field(pattern=r"^doc-[a-f0-9]{16}$")
    source_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}$")
    title: str = Field(min_length=1, max_length=500)
    document_type: KnowledgeDocumentType
    service: str | None = Field(default=None, max_length=253)
    environment: str | None = Field(default=None, max_length=100)
    version: str = Field(default="1", min_length=1, max_length=100)
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None
    source_ref: str = Field(min_length=1, max_length=2_000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    imported_at: AwareDatetime

    @model_validator(mode="after")
    def valid_period(self) -> KnowledgeDocument:
        if self.valid_from and self.valid_until and self.valid_from >= self.valid_until:
            raise ValueError("valid_from must be before valid_until")
        return self


class KnowledgeChunk(FrozenModel):
    chunk_id: str = Field(pattern=r"^kc-[a-f0-9]{16}$")
    document_id: str = Field(pattern=r"^doc-[a-f0-9]{16}$")
    ordinal: int = Field(ge=0)
    heading: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=1, max_length=4_000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    embedding: list[float] = Field(min_length=1, max_length=4_096)

    @model_validator(mode="after")
    def finite_embedding(self) -> KnowledgeChunk:
        if any(not math.isfinite(value) for value in self.embedding):
            raise ValueError("embedding values must be finite")
        return self


class KnowledgeSearchFilter(FrozenModel):
    service: str | None = Field(default=None, max_length=253)
    environment: str | None = Field(default=None, max_length=100)
    document_types: list[KnowledgeDocumentType] = Field(default_factory=list, max_length=3)
    effective_at: AwareDatetime | None = None


class KnowledgeSearchHit(FrozenModel):
    chunk_id: str
    document_id: str
    title: str
    document_type: KnowledgeDocumentType
    service: str | None
    environment: str | None
    version: str
    source_ref: str
    heading: str | None
    content: str
    content_hash: str
    score: float = Field(ge=0)
    keyword_rank: int | None = Field(default=None, ge=1)
    vector_rank: int | None = Field(default=None, ge=1)


class EmbeddingClient(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class KnowledgeRepository(Protocol):
    async def replace_document(
        self, document: KnowledgeDocument, chunks: Sequence[KnowledgeChunk]
    ) -> None: ...

    async def search(
        self,
        query: str,
        query_embedding: Sequence[float],
        filters: KnowledgeSearchFilter,
        limit: int,
    ) -> list[KnowledgeSearchHit]: ...


class KnowledgeRetriever:
    """Turn hybrid-search hits into ordinary, citable investigation evidence."""

    def __init__(self, repository: KnowledgeRepository, embeddings: EmbeddingClient) -> None:
        self._repository = repository
        self._embeddings = embeddings

    async def retrieve(
        self, query: str, filters: KnowledgeSearchFilter, limit: int = 5
    ) -> list[Evidence]:
        vectors = await self._embeddings.embed([query])
        if len(vectors) != 1:
            raise ValueError("embedding provider returned an unexpected result count")
        hits = await self._repository.search(query, vectors[0], filters, limit)
        observed_at = datetime.now(UTC)
        return deduplicate_evidence(
            [
                Evidence(
                    evidence_id=f"ev-{hashlib.sha256(hit.chunk_id.encode()).hexdigest()[:16]}",
                    source_type=f"knowledge.{hit.document_type}",
                    source_ref=f"{hit.source_ref}#chunk={hit.chunk_id}",
                    query={
                        "text": query,
                        "service": filters.service,
                        "environment": filters.environment,
                    },
                    observed_at=observed_at,
                    content_excerpt=clip_excerpt(hit.content),
                    content_hash=hit.content_hash,
                    structured_facts={
                        "chunk_id": hit.chunk_id,
                        "document_id": hit.document_id,
                        "title": hit.title,
                        "heading": hit.heading,
                        "version": hit.version,
                        "rrf_score": hit.score,
                        "keyword_rank": hit.keyword_rank,
                        "vector_rank": hit.vector_rank,
                    },
                    reliability=_knowledge_reliability(hit.document_type),
                )
                for hit in hits
            ]
        )


class KnowledgeImporter:
    """Import a JSON catalog and Markdown files through validated domain contracts."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        embeddings: EmbeddingClient,
        *,
        max_chunk_chars: int = 1_800,
    ) -> None:
        self._repository = repository
        self._embeddings = embeddings
        self._max_chunk_chars = max_chunk_chars

    async def import_catalog(self, catalog_path: Path) -> list[KnowledgeDocument]:
        root, raw = await asyncio.to_thread(_load_catalog, catalog_path)
        if not isinstance(raw, list):
            raise ValueError("knowledge catalog must be a JSON array")
        imported: list[KnowledgeDocument] = []
        for entry in raw:
            if not isinstance(entry, Mapping):
                raise ValueError("knowledge catalog entries must be objects")
            relative = Path(str(entry["path"]))
            content = await asyncio.to_thread(_read_document, root, relative)
            if not content:
                raise ValueError(f"knowledge document is empty: {relative}")
            source_id = str(entry["source_id"])
            digest = hashlib.sha256(content.encode()).hexdigest()
            document = KnowledgeDocument(
                document_id=f"doc-{hashlib.sha256(source_id.encode()).hexdigest()[:16]}",
                source_id=source_id,
                title=str(entry["title"]),
                document_type=KnowledgeDocumentType(str(entry["document_type"])),
                service=_optional_text(entry.get("service")),
                environment=_optional_text(entry.get("environment")),
                version=str(entry.get("version", "1")),
                valid_from=entry.get("valid_from"),
                valid_until=entry.get("valid_until"),
                source_ref=str(entry.get("source_ref", relative.as_posix())),
                content_hash=digest,
                imported_at=datetime.now(UTC),
            )
            pieces = chunk_markdown(content, self._max_chunk_chars)
            vectors = await self._embeddings.embed([piece[1] for piece in pieces])
            if len(vectors) != len(pieces):
                raise ValueError("embedding provider returned an unexpected result count")
            chunks = [
                KnowledgeChunk(
                    chunk_id=(
                        "kc-"
                        + hashlib.sha256(f"{source_id}:{ordinal}:{piece[1]}".encode()).hexdigest()[
                            :16
                        ]
                    ),
                    document_id=document.document_id,
                    ordinal=ordinal,
                    heading=piece[0],
                    content=piece[1],
                    content_hash=hashlib.sha256(piece[1].encode()).hexdigest(),
                    embedding=vectors[ordinal],
                )
                for ordinal, piece in enumerate(pieces)
            ]
            await self._repository.replace_document(document, chunks)
            imported.append(document)
        return imported


class InMemoryKnowledgeRepository:
    """Deterministic exact-search implementation used by tests and offline evals."""

    def __init__(self) -> None:
        self._documents: dict[str, KnowledgeDocument] = {}
        self._chunks: dict[str, KnowledgeChunk] = {}

    async def replace_document(
        self, document: KnowledgeDocument, chunks: Sequence[KnowledgeChunk]
    ) -> None:
        stale = [
            key for key, item in self._chunks.items() if item.document_id == document.document_id
        ]
        for key in stale:
            del self._chunks[key]
        self._documents[document.document_id] = document
        self._chunks.update({item.chunk_id: item for item in chunks})

    async def search(
        self,
        query: str,
        query_embedding: Sequence[float],
        filters: KnowledgeSearchFilter,
        limit: int,
    ) -> list[KnowledgeSearchHit]:
        candidates = [
            (chunk, self._documents[chunk.document_id])
            for chunk in self._chunks.values()
            if _matches(self._documents[chunk.document_id], filters)
            and len(chunk.embedding) == len(query_embedding)
        ]
        terms = _tokens(query)
        keyword = sorted(
            candidates,
            key=lambda item: (
                -sum(
                    (_tokens(item[1].title + " " + item[0].content).get(term, 0)) for term in terms
                ),
                item[0].chunk_id,
            ),
        )
        keyword = [
            item
            for item in keyword
            if any(term in _tokens(item[1].title + " " + item[0].content) for term in terms)
        ][: max(limit * 4, 20)]
        vector = sorted(
            candidates,
            key=lambda item: (-_cosine(item[0].embedding, query_embedding), item[0].chunk_id),
        )[: max(limit * 4, 20)]
        return _fuse_hits(keyword, vector, limit)


def chunk_markdown(content: str, max_chars: int = 1_800) -> list[tuple[str | None, str]]:
    if max_chars < 200 or max_chars > 4_000:
        raise ValueError("max_chars must be between 200 and 4000")
    heading: str | None = None
    blocks: list[tuple[str | None, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            blocks.append((heading, "\n\n".join(buffer).strip()))
            buffer.clear()

    for paragraph in re.split(r"\n\s*\n", content):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        first, _, rest = paragraph.partition("\n")
        if first.startswith("#") and first.lstrip("#").startswith(" "):
            flush()
            heading = first.lstrip("#").strip()
            paragraph = rest.strip()
            if not paragraph:
                continue
        while len(paragraph) > max_chars:
            flush()
            split_at = paragraph.rfind(" ", 0, max_chars)
            if split_at < max_chars // 2:
                split_at = max_chars
            blocks.append((heading, paragraph[:split_at].strip()))
            paragraph = paragraph[split_at:].strip()
        buffered = sum(len(item) for item in buffer) + max(0, len(buffer) * 2)
        if buffer and buffered + len(paragraph) > max_chars:
            flush()
        if paragraph:
            buffer.append(paragraph)
    flush()
    return blocks


def clip_excerpt(content: str, limit: int = 4_000) -> str:
    normalized = re.sub(r"[ \t]+", " ", content).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def deduplicate_evidence(items: Sequence[Evidence], limit: int = 20) -> list[Evidence]:
    """Keep the most reliable copy for identical normalized evidence content."""

    priority = {
        EvidenceReliability.HIGH: 3,
        EvidenceReliability.MEDIUM: 2,
        EvidenceReliability.LOW: 1,
    }
    unique: dict[str, Evidence] = {}
    order: list[str] = []
    for item in items:
        key = item.content_hash
        if key not in unique:
            unique[key] = item
            order.append(key)
        elif priority[item.reliability] > priority[unique[key].reliability]:
            unique[key] = item
    return [unique[key] for key in order[:limit]]


def _knowledge_reliability(document_type: KnowledgeDocumentType) -> EvidenceReliability:
    if document_type == KnowledgeDocumentType.SERVICE:
        return EvidenceReliability.HIGH
    if document_type == KnowledgeDocumentType.RUNBOOK:
        return EvidenceReliability.MEDIUM
    return EvidenceReliability.LOW


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _load_catalog(catalog_path: Path) -> tuple[Path, object]:
    resolved = catalog_path.resolve()
    return resolved.parent, json.loads(resolved.read_text(encoding="utf-8"))


def _read_document(root: Path, relative: Path) -> str:
    source_path = (root / relative).resolve()
    if not source_path.is_relative_to(root):
        raise ValueError("knowledge document path escapes catalog directory")
    return source_path.read_text(encoding="utf-8").strip()


def _tokens(value: str) -> Counter[str]:
    return Counter(match.group(0).lower() for match in _WORD.finditer(value))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _matches(document: KnowledgeDocument, filters: KnowledgeSearchFilter) -> bool:
    if filters.service and document.service not in {None, filters.service}:
        return False
    if filters.environment and document.environment not in {None, filters.environment}:
        return False
    if filters.document_types and document.document_type not in filters.document_types:
        return False
    at = filters.effective_at
    return not at or (
        (document.valid_from is None or document.valid_from <= at)
        and (document.valid_until is None or document.valid_until > at)
    )


def _fuse_hits(
    keyword: Sequence[tuple[KnowledgeChunk, KnowledgeDocument]],
    vector: Sequence[tuple[KnowledgeChunk, KnowledgeDocument]],
    limit: int,
    rrf_k: int = 60,
) -> list[KnowledgeSearchHit]:
    keyword_ranks = {item[0].chunk_id: rank for rank, item in enumerate(keyword, 1)}
    vector_ranks = {item[0].chunk_id: rank for rank, item in enumerate(vector, 1)}
    candidates = {item[0].chunk_id: item for item in [*keyword, *vector]}
    ordered = sorted(
        candidates.values(),
        key=lambda item: (
            -(
                (1 / (rrf_k + keyword_ranks[item[0].chunk_id]))
                if item[0].chunk_id in keyword_ranks
                else 0
            )
            - (
                (1 / (rrf_k + vector_ranks[item[0].chunk_id]))
                if item[0].chunk_id in vector_ranks
                else 0
            ),
            item[0].chunk_id,
        ),
    )
    return [
        KnowledgeSearchHit(
            chunk_id=chunk.chunk_id,
            document_id=document.document_id,
            title=document.title,
            document_type=document.document_type,
            service=document.service,
            environment=document.environment,
            version=document.version,
            source_ref=document.source_ref,
            heading=chunk.heading,
            content=chunk.content,
            content_hash=chunk.content_hash,
            score=(
                (1 / (rrf_k + keyword_ranks[chunk.chunk_id]))
                if chunk.chunk_id in keyword_ranks
                else 0
            )
            + (
                (1 / (rrf_k + vector_ranks[chunk.chunk_id]))
                if chunk.chunk_id in vector_ranks
                else 0
            ),
            keyword_rank=keyword_ranks.get(chunk.chunk_id),
            vector_rank=vector_ranks.get(chunk.chunk_id),
        )
        for chunk, document in ordered[:limit]
    ]
