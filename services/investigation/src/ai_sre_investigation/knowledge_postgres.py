"""PostgreSQL + pgvector implementation of the knowledge repository."""

import json
import re
from collections.abc import Sequence
from typing import Any

from psycopg_pool import AsyncConnectionPool

from ai_sre_investigation.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentType,
    KnowledgeSearchFilter,
    KnowledgeSearchHit,
)


class PostgresKnowledgeRepository:  # pragma: no cover - covered by stage-4 integration gate
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(_SCHEMA)

    async def replace_document(
        self, document: KnowledgeDocument, chunks: Sequence[KnowledgeChunk]
    ) -> None:
        async with self._pool.connection() as connection, connection.transaction():
            await connection.execute(
                """
                INSERT INTO knowledge_documents (
                    document_id, source_id, title, document_type, service, environment,
                    version, valid_from, valid_until, source_ref, content_hash, imported_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (document_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    title = excluded.title,
                    document_type = excluded.document_type,
                    service = excluded.service,
                    environment = excluded.environment,
                    version = excluded.version,
                    valid_from = excluded.valid_from,
                    valid_until = excluded.valid_until,
                    source_ref = excluded.source_ref,
                    content_hash = excluded.content_hash,
                    imported_at = excluded.imported_at
                """,
                (
                    document.document_id,
                    document.source_id,
                    document.title,
                    document.document_type,
                    document.service,
                    document.environment,
                    document.version,
                    document.valid_from,
                    document.valid_until,
                    document.source_ref,
                    document.content_hash,
                    document.imported_at,
                ),
            )
            await connection.execute(
                "DELETE FROM knowledge_chunks WHERE document_id = %s", (document.document_id,)
            )
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO knowledge_chunks (
                        chunk_id, document_id, ordinal, heading, content, content_hash,
                        embedding, embedding_dimensions
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s)
                    """,
                    [
                        (
                            chunk.chunk_id,
                            chunk.document_id,
                            chunk.ordinal,
                            chunk.heading,
                            chunk.content,
                            chunk.content_hash,
                            _vector_literal(chunk.embedding),
                            len(chunk.embedding),
                        )
                        for chunk in chunks
                    ],
                )

    async def search(
        self,
        query: str,
        query_embedding: Sequence[float],
        filters: KnowledgeSearchFilter,
        limit: int,
    ) -> list[KnowledgeSearchHit]:
        if not query.strip():
            raise ValueError("knowledge query cannot be empty")
        if limit < 1 or limit > 50:
            raise ValueError("knowledge result limit must be between 1 and 50")
        where = [
            "(%s::text IS NULL OR d.service IS NULL OR d.service = %s)",
            "(%s::text IS NULL OR d.environment IS NULL OR d.environment = %s)",
            "(%s::timestamptz IS NULL OR d.valid_from IS NULL OR d.valid_from <= %s)",
            "(%s::timestamptz IS NULL OR d.valid_until IS NULL OR d.valid_until > %s)",
        ]
        parameters: list[Any] = [
            filters.service,
            filters.service,
            filters.environment,
            filters.environment,
            filters.effective_at,
            filters.effective_at,
            filters.effective_at,
            filters.effective_at,
        ]
        if filters.document_types:
            where.append("d.document_type = ANY(%s)")
            parameters.append([str(item) for item in filters.document_types])
        candidate_limit = max(20, limit * 4)
        keyword_query = _keyword_query(query)
        parameters.extend(
            [
                keyword_query,
                keyword_query,
                candidate_limit,
                _vector_literal(query_embedding),
                len(query_embedding),
                candidate_limit,
                limit,
            ]
        )
        statement = _SEARCH.format(where=" AND ".join(where))
        async with self._pool.connection() as connection:
            cursor = await connection.execute(statement, parameters)
            rows = await cursor.fetchall()
        return [
            KnowledgeSearchHit(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                title=row["title"],
                document_type=KnowledgeDocumentType(row["document_type"]),
                service=row["service"],
                environment=row["environment"],
                version=row["version"],
                source_ref=row["source_ref"],
                heading=row["heading"],
                content=row["content"],
                content_hash=row["content_hash"],
                score=float(row["score"]),
                keyword_rank=row["keyword_rank"],
                vector_rank=row["vector_rank"],
            )
            for row in rows
        ]


def _vector_literal(vector: Sequence[float]) -> str:
    if not vector:
        raise ValueError("embedding vector cannot be empty")
    return json.dumps([float(value) for value in vector], separators=(",", ":"))


def _keyword_query(query: str) -> str:
    """Use safe websearch OR syntax so one missing term does not erase keyword recall."""

    terms = re.findall(r"[\w.-]+", query, re.UNICODE)[:32]
    return " or ".join(terms) if terms else query


_SEARCH = """
WITH filtered AS MATERIALIZED (
    SELECT c.chunk_id, c.document_id, c.heading, c.content, c.content_hash,
           c.search_vector, c.embedding, c.embedding_dimensions,
           d.title, d.document_type, d.service, d.environment, d.version, d.source_ref
    FROM knowledge_chunks c
    JOIN knowledge_documents d USING (document_id)
    WHERE {where}
),
keyword AS (
    SELECT chunk_id,
           row_number() OVER (
               ORDER BY ts_rank_cd(search_vector, websearch_to_tsquery('simple', %s)) DESC,
                        chunk_id
           ) AS keyword_rank
    FROM filtered
    WHERE search_vector @@ websearch_to_tsquery('simple', %s)
    LIMIT %s
),
semantic AS (
    SELECT chunk_id,
           row_number() OVER (ORDER BY embedding <=> %s::vector, chunk_id) AS vector_rank
    FROM filtered
    WHERE embedding_dimensions = %s
    LIMIT %s
),
candidate AS (
    SELECT chunk_id FROM keyword
    UNION
    SELECT chunk_id FROM semantic
)
SELECT f.chunk_id, f.document_id, f.title, f.document_type, f.service, f.environment,
       f.version, f.source_ref, f.heading, f.content, f.content_hash,
       k.keyword_rank, s.vector_rank,
       COALESCE(1.0 / (60 + k.keyword_rank), 0.0)
         + COALESCE(1.0 / (60 + s.vector_rank), 0.0) AS score
FROM candidate c
JOIN filtered f USING (chunk_id)
LEFT JOIN keyword k USING (chunk_id)
LEFT JOIN semantic s USING (chunk_id)
ORDER BY score DESC, f.chunk_id
LIMIT %s
"""


_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_documents (
    document_id text PRIMARY KEY,
    source_id text NOT NULL UNIQUE,
    title text NOT NULL,
    document_type text NOT NULL CHECK (document_type IN ('runbook', 'service', 'incident')),
    service text,
    environment text,
    version text NOT NULL,
    valid_from timestamptz,
    valid_until timestamptz,
    source_ref text NOT NULL,
    content_hash text NOT NULL,
    imported_at timestamptz NOT NULL,
    CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from < valid_until)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    heading text,
    content text NOT NULL,
    content_hash text NOT NULL,
    embedding vector NOT NULL,
    embedding_dimensions integer NOT NULL CHECK (embedding_dimensions > 0),
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(heading, '') || ' ' || content)
    ) STORED,
    UNIQUE (document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS knowledge_chunks_search_idx
ON knowledge_chunks USING gin (search_vector);
CREATE INDEX IF NOT EXISTS knowledge_documents_filter_idx
ON knowledge_documents (service, environment, document_type, valid_from, valid_until);
"""
