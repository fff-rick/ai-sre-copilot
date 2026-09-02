#!/usr/bin/env python3
"""Verify PostgreSQL checkpoint recovery across independent runtime instances."""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "investigation" / "src"))

from ai_sre_investigation.domain import (  # noqa: E402
    Alert,
    Investigation,
    Severity,
    TimeWindow,
)
from ai_sre_investigation.fakes import FakeModelClient, FakeToolClient  # noqa: E402
from ai_sre_investigation.repository import PostgresInvestigationRepository  # noqa: E402
from ai_sre_investigation.service import InvestigationService  # noqa: E402
from ai_sre_investigation.workflow import InvestigationWorkflow  # noqa: E402

RESPONSES: dict[str, dict[str, object]] = {
    "prometheus.query": {"error_rate": 0.17},
    "loki.query_range": {"messages": ["connection refused to inventory"]},
    "tempo.search_traces": {"slow_dependency": "inventory"},
    "releases.list": {"release": "payment-v42"},
}


def evidence_id(tool_name: str) -> str:
    canonical = json.dumps(
        RESPONSES[tool_name], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    seed = f"{tool_name}:fake://{tool_name}:{digest}"
    return f"ev-{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


def model_payload() -> dict[str, object]:
    return {
        "hypotheses": [
            {
                "statement": "Inventory dependency is unavailable.",
                "rank": 1,
                "confidence": 0.91,
                "supporting_evidence_ids": [evidence_id("tempo.search_traces")],
                "contradicting_evidence_ids": [],
                "next_checks": [],
            }
        ]
    }


async def wait_for_completion(
    repository: PostgresInvestigationRepository, investigation_id: str
) -> None:
    for _ in range(100):
        record = await repository.get(investigation_id)
        if record and record.status == "COMPLETED":
            return
        await asyncio.sleep(0.05)
    raise AssertionError("restarted worker did not complete the investigation")


async def verify(database_url: str) -> None:
    now = datetime.now(UTC)
    identifier = f"inv-restart-{uuid4()}"
    investigation = Investigation(
        investigation_id=identifier,
        trace_id=uuid4().hex[:16],
        alert=Alert(
            alert_id="restart-acceptance",
            service="payment",
            severity=Severity.CRITICAL,
            summary="Payment errors increased.",
            time_window=TimeWindow(start=now - timedelta(minutes=15), end=now),
            source_ref="acceptance://stage3/restart",
        ),
        created_at=now,
        updated_at=now,
    )
    pool = PostgresInvestigationRepository.pool(database_url)
    await pool.open(wait=True)
    repository = PostgresInvestigationRepository(pool)
    await repository.setup()
    await repository.create(investigation)
    first_tools = FakeToolClient(RESPONSES)
    serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
    try:
        async with AsyncPostgresSaver.from_conn_string(
            database_url, serde=serializer
        ) as first_saver:
            await first_saver.setup()
            first_workflow = InvestigationWorkflow(
                model=FakeModelClient(model_payload()),
                tools=first_tools,
                checkpointer=first_saver,
                now=lambda: now,
                interrupt_after=["verify"],
            )
            interrupted = await first_workflow.run(investigation)
            assert interrupted["status"] == "VERIFYING"
            assert len(first_tools.requests) == 4
            await repository.save_state(identifier, interrupted)

        # The first database connection and graph instance are gone: this is a process boundary.
        second_tools = FakeToolClient({})
        async with AsyncPostgresSaver.from_conn_string(
            database_url, serde=serializer
        ) as second_saver:
            second_workflow = InvestigationWorkflow(
                model=FakeModelClient(model_payload()),
                tools=second_tools,
                checkpointer=second_saver,
                cancel_check=repository.is_cancel_requested,
                now=lambda: now,
            )
            restarted = InvestigationService(
                repository=repository, workflow=second_workflow, poll_seconds=0.02
            )
            await restarted.start()
            try:
                await wait_for_completion(repository, identifier)
            finally:
                await restarted.stop()
            assert not second_tools.requests, "completed collection was replayed after restart"
    finally:
        async with pool.connection() as connection:
            for table in ("checkpoint_writes", "checkpoints", "checkpoint_blobs"):
                await connection.execute(
                    f"DELETE FROM {table} WHERE thread_id = %s",
                    (identifier,),
                )
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
    args = parser.parse_args()
    asyncio.run(verify(args.database_url))
    print("stage 3 PostgreSQL restart verification passed")


if __name__ == "__main__":
    main()
