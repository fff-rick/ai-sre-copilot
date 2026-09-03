import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_sre_investigation.domain import Evidence, EvidenceReliability
from ai_sre_investigation.embedding_client import HashEmbeddingClient
from ai_sre_investigation.knowledge import (
    InMemoryKnowledgeRepository,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentType,
    KnowledgeImporter,
    KnowledgeRetriever,
    KnowledgeSearchFilter,
    chunk_markdown,
    clip_excerpt,
    deduplicate_evidence,
)
from ai_sre_investigation.knowledge_postgres import _keyword_query, _vector_literal


def test_catalog_import_hybrid_search_metadata_and_evidence(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "payment.md").write_text(
            "# Pool failure\n\nPayment connection pool exhausted timeout.\n\n"
            "## Response\n\nInspect slow PostgreSQL transactions.",
            encoding="utf-8",
        )
        (tmp_path / "inventory.md").write_text(
            "# Inventory\n\nCatalog downstream latency creates slow spans.", encoding="utf-8"
        )
        catalog = [
            {
                "source_id": "payment-runbook",
                "path": "payment.md",
                "title": "Payment pool runbook",
                "document_type": "runbook",
                "service": "payment",
                "environment": "testbed",
                "source_ref": "repo://payment.md",
            },
            {
                "source_id": "inventory-service",
                "path": "inventory.md",
                "title": "Inventory service",
                "document_type": "service",
                "service": "inventory",
            },
        ]
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        repository = InMemoryKnowledgeRepository()
        embeddings = HashEmbeddingClient(32)
        importer = KnowledgeImporter(repository, embeddings, max_chunk_chars=200)
        documents = await importer.import_catalog(catalog_path)

        assert len(documents) == 2
        query_vector = (await embeddings.embed(["payment pool exhausted"]))[0]
        hits = await repository.search(
            "payment pool exhausted",
            query_vector,
            KnowledgeSearchFilter(service="payment", environment="testbed"),
            5,
        )
        assert hits[0].title == "Payment pool runbook"
        assert hits[0].keyword_rank == 1
        assert hits[0].vector_rank is not None

        excluded = await repository.search(
            "payment pool exhausted",
            query_vector,
            KnowledgeSearchFilter(service="inventory"),
            5,
        )
        assert all(hit.service != "payment" for hit in excluded)

        evidence = await KnowledgeRetriever(repository, embeddings).retrieve(
            "payment pool exhausted", KnowledgeSearchFilter(service="payment")
        )
        assert evidence[0].source_type == "knowledge.runbook"
        assert evidence[0].reliability == EvidenceReliability.MEDIUM
        assert "#chunk=kc-" in evidence[0].source_ref

    asyncio.run(scenario())


def test_catalog_validation_chunking_and_embedding_count(tmp_path: Path) -> None:
    class WrongCountEmbedding:
        async def embed(self, texts: Any) -> list[list[float]]:
            del texts
            return []

    async def scenario() -> None:
        repository = InMemoryKnowledgeRepository()
        catalog = tmp_path / "catalog.json"

        catalog.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="JSON array"):
            await KnowledgeImporter(repository, HashEmbeddingClient()).import_catalog(catalog)

        catalog.write_text("[1]", encoding="utf-8")
        with pytest.raises(ValueError, match="must be objects"):
            await KnowledgeImporter(repository, HashEmbeddingClient()).import_catalog(catalog)

        catalog.write_text(
            json.dumps(
                [
                    {
                        "source_id": "escape",
                        "path": "../outside.md",
                        "title": "Escape",
                        "document_type": "runbook",
                    }
                ]
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="escapes"):
            await KnowledgeImporter(repository, HashEmbeddingClient()).import_catalog(catalog)

        empty = tmp_path / "empty.md"
        empty.write_text("  ", encoding="utf-8")
        catalog.write_text(
            json.dumps(
                [
                    {
                        "source_id": "empty",
                        "path": "empty.md",
                        "title": "Empty",
                        "document_type": "runbook",
                    }
                ]
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="is empty"):
            await KnowledgeImporter(repository, HashEmbeddingClient()).import_catalog(catalog)

        empty.write_text("# Valid\n\ncontent", encoding="utf-8")
        with pytest.raises(ValueError, match="result count"):
            await KnowledgeImporter(repository, WrongCountEmbedding()).import_catalog(catalog)

    asyncio.run(scenario())

    with pytest.raises(ValueError, match="between 200 and 4000"):
        chunk_markdown("text", 100)
    chunks = chunk_markdown("# Heading\n\n" + "word " * 100, 200)
    assert len(chunks) > 1
    assert all(0 < len(content) <= 200 for _, content in chunks)
    assert chunks[0][0] == "Heading"
    assert clip_excerpt("a   b", 10) == "a b"
    assert clip_excerpt("abcdef", 4) == "abc…"


def test_domain_guards_deduplication_and_effective_filters() -> None:
    now = datetime.now(UTC)
    digest = hashlib.sha256(b"same").hexdigest()

    def evidence(identifier: str, reliability: EvidenceReliability) -> Evidence:
        return Evidence(
            evidence_id=identifier,
            source_type="test",
            source_ref=f"test://{identifier}",
            query={},
            observed_at=now,
            content_excerpt="same",
            content_hash=digest,
            structured_facts=None,
            reliability=reliability,
        )

    result = deduplicate_evidence(
        [
            evidence("ev-0000000000000001", EvidenceReliability.LOW),
            evidence("ev-0000000000000002", EvidenceReliability.HIGH),
        ],
        limit=1,
    )
    assert result[0].evidence_id == "ev-0000000000000002"

    with pytest.raises(ValidationError, match="valid_from"):
        KnowledgeDocument(
            document_id="doc-0000000000000001",
            source_id="invalid-period",
            title="Invalid",
            document_type=KnowledgeDocumentType.INCIDENT,
            source_ref="test://invalid",
            content_hash=digest,
            imported_at=now,
            valid_from=now,
            valid_until=now - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError, match="finite"):
        KnowledgeChunk(
            chunk_id="kc-0000000000000001",
            document_id="doc-0000000000000001",
            ordinal=0,
            content="invalid vector",
            content_hash=digest,
            embedding=[float("nan")],
        )
    assert _vector_literal([1, 2.5]) == "[1.0,2.5]"
    assert _keyword_query("pool timeout") == "pool or timeout"
    with pytest.raises(ValueError, match="cannot be empty"):
        _vector_literal([])


def test_retriever_rejects_wrong_embedding_count() -> None:
    class WrongCountEmbedding:
        async def embed(self, texts: Any) -> list[list[float]]:
            del texts
            return []

    async def scenario() -> None:
        retriever = KnowledgeRetriever(InMemoryKnowledgeRepository(), WrongCountEmbedding())
        with pytest.raises(ValueError, match="result count"):
            await retriever.retrieve("query", KnowledgeSearchFilter())

    asyncio.run(scenario())
