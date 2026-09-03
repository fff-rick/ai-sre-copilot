#!/usr/bin/env python3
"""Verify pgvector hybrid retrieval and durable investigation events."""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "investigation" / "src"))

from ai_sre_investigation.domain import (  # noqa: E402
    Alert,
    Investigation,
    InvestigationStatus,
    Severity,
    TimeWindow,
)
from ai_sre_investigation.embedding_client import HashEmbeddingClient  # noqa: E402
from ai_sre_investigation.knowledge import (  # noqa: E402
    KnowledgeImporter,
    KnowledgeSearchFilter,
)
from ai_sre_investigation.knowledge_postgres import (  # noqa: E402
    PostgresKnowledgeRepository,
)
from ai_sre_investigation.repository import (  # noqa: E402
    PostgresInvestigationRepository,
)
from ai_sre_investigation.workflow import InvestigationState  # noqa: E402


async def verify(database_url: str) -> None:
    pool = PostgresInvestigationRepository.pool(database_url)
    await pool.open(wait=True)
    investigations = PostgresInvestigationRepository(pool)
    knowledge = PostgresKnowledgeRepository(pool)
    identifier = f"inv-stage4-{uuid4()}"
    try:
        await investigations.setup()
        await knowledge.setup()
        embeddings = HashEmbeddingClient(64)
        imported = await KnowledgeImporter(knowledge, embeddings).import_catalog(
            ROOT / "knowledge" / "catalog.json"
        )
        assert len(imported) == 4

        query = "payment connection pool exhausted timeout"
        vector = (await embeddings.embed([query]))[0]
        hits = await knowledge.search(
            query,
            vector,
            KnowledgeSearchFilter(service="payment", environment="testbed"),
            5,
        )
        assert hits
        assert hits[0].source_ref == "repo://knowledge/runbooks/payment-database.md"
        assert any(hit.keyword_rank == 1 for hit in hits)
        assert hits[0].vector_rank is not None
        assert all(hit.service in {None, "payment"} for hit in hits)

        now = datetime.now(UTC)
        investigation = Investigation(
            investigation_id=identifier,
            trace_id=uuid4().hex[:16],
            alert=Alert(
                alert_id="stage4-postgres",
                service="payment",
                severity=Severity.WARNING,
                summary="Payment pool wait increased.",
                time_window=TimeWindow(start=now - timedelta(minutes=10), end=now),
                source_ref="acceptance://stage4/postgres",
            ),
            created_at=now,
            updated_at=now,
        )
        await investigations.create(investigation)
        first = await investigations.append_event(
            identifier,
            "investigation.created",
            InvestigationStatus.RECEIVED,
            {"service": "payment"},
        )
        await investigations.append_event(
            identifier,
            "node.scope.completed",
            InvestigationStatus.SCOPING,
            {"node": "scope"},
        )

        # A fresh repository reads the same monotonic event stream after a process boundary.
        restarted = PostgresInvestigationRepository(pool)
        replay = await restarted.list_events(identifier, after_event_id=first.event_id)
        assert len(replay) == 1
        assert replay[0].event_type == "node.scope.completed"
        await restarted.save_state(
            identifier,
            InvestigationState(status=InvestigationStatus.COMPLETED, report={"ok": True}),
        )
        finished = await restarted.list_events(identifier, after_event_id=replay[0].event_id)
        snapshot = await restarted.get(identifier)
        assert len(finished) == 1 and finished[0].event_type == "investigation.finished"
        assert snapshot is not None and snapshot.report == {"ok": True}
        listed = await restarted.list_investigations(limit=100)
        assert any(item.investigation.investigation_id == identifier for item in listed)
    finally:
        async with pool.connection() as connection:
            await connection.execute(
                "DELETE FROM investigations WHERE investigation_id = %s", (identifier,)
            )
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default="postgresql://ai_sre:local-development-only@127.0.0.1:5432/ai_sre",
    )
    arguments = parser.parse_args()
    asyncio.run(verify(arguments.database_url))
    print("stage 4 PostgreSQL retrieval and event verification passed")


if __name__ == "__main__":
    main()
