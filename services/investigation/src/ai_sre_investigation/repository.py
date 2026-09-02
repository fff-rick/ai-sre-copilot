"""Investigation discovery and leasing separate from LangGraph checkpoints."""

import asyncio
import json
from collections.abc import Mapping
from typing import Any, Protocol, cast

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ConfigDict, Field

from ai_sre_investigation.domain import FrozenModel, Investigation, InvestigationStatus
from ai_sre_investigation.workflow import InvestigationState


class StoredInvestigation(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    investigation: Investigation
    status: InvestigationStatus
    report: dict[str, Any] | None = None
    cancel_requested: bool = False
    last_error: str | None = None
    attempts: int = Field(default=0, ge=0)


class InvestigationRepository(Protocol):
    async def create(self, investigation: Investigation) -> None: ...

    async def get(self, investigation_id: str) -> StoredInvestigation | None: ...

    async def claim_next(self, worker_id: str) -> Investigation | None: ...

    async def save_state(self, investigation_id: str, state: InvestigationState) -> None: ...

    async def fail_attempt(self, investigation_id: str, safe_error: str) -> None: ...

    async def release_claim(self, investigation_id: str) -> None: ...

    async def renew_claim(self, investigation_id: str, worker_id: str) -> bool: ...

    async def request_cancel(self, investigation_id: str) -> bool: ...

    async def is_cancel_requested(self, investigation_id: str) -> bool: ...


class InMemoryInvestigationRepository:
    """Deterministic repository for API and worker tests."""

    def __init__(self) -> None:
        self._records: dict[str, StoredInvestigation] = {}
        self._claimed: set[str] = set()
        self._lock = asyncio.Lock()

    async def create(self, investigation: Investigation) -> None:
        async with self._lock:
            if investigation.investigation_id in self._records:
                raise ValueError("investigation already exists")
            self._records[investigation.investigation_id] = StoredInvestigation(
                investigation=investigation, status=investigation.status
            )

    async def get(self, investigation_id: str) -> StoredInvestigation | None:
        async with self._lock:
            return self._records.get(investigation_id)

    async def claim_next(self, worker_id: str) -> Investigation | None:
        del worker_id
        async with self._lock:
            for identifier, record in self._records.items():
                if identifier not in self._claimed and record.status not in _TERMINAL:
                    self._claimed.add(identifier)
                    self._records[identifier] = record.model_copy(
                        update={"attempts": record.attempts + 1}
                    )
                    return record.investigation
        return None

    async def save_state(self, investigation_id: str, state: InvestigationState) -> None:
        async with self._lock:
            record = self._records[investigation_id]
            status = InvestigationStatus(state["status"])
            self._records[investigation_id] = record.model_copy(
                update={"status": status, "report": state.get("report")}
            )
            self._claimed.discard(investigation_id)

    async def fail_attempt(self, investigation_id: str, safe_error: str) -> None:
        async with self._lock:
            record = self._records[investigation_id]
            terminal = record.attempts >= 3
            self._records[investigation_id] = record.model_copy(
                update={
                    "status": InvestigationStatus.FAILED if terminal else record.status,
                    "last_error": safe_error,
                }
            )
            self._claimed.discard(investigation_id)

    async def release_claim(self, investigation_id: str) -> None:
        async with self._lock:
            self._claimed.discard(investigation_id)

    async def renew_claim(self, investigation_id: str, worker_id: str) -> bool:
        del worker_id
        async with self._lock:
            return investigation_id in self._claimed

    async def request_cancel(self, investigation_id: str) -> bool:
        async with self._lock:
            record = self._records.get(investigation_id)
            if record is None or record.status in _TERMINAL:
                return False
            self._records[investigation_id] = record.model_copy(update={"cancel_requested": True})
            return True

    async def is_cancel_requested(self, investigation_id: str) -> bool:
        record = await self.get(investigation_id)
        return record.cancel_requested if record else True


class PostgresInvestigationRepository:  # pragma: no cover - exercised by stage-3 integration
    """PostgreSQL work queue with bounded attempts and expiring leases."""

    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    @classmethod
    def pool(cls, database_url: str) -> AsyncConnectionPool[Any]:
        return AsyncConnectionPool(
            conninfo=database_url,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )

    async def setup(self) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(_CREATE_TABLE)

    async def create(self, investigation: Investigation) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO investigations (investigation_id, investigation, status)
                VALUES (%s, %s::jsonb, %s)
                """,
                (
                    investigation.investigation_id,
                    json.dumps(investigation.model_dump(mode="json")),
                    investigation.status,
                ),
            )

    async def get(self, investigation_id: str) -> StoredInvestigation | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT investigation, status, report, cancel_requested, last_error, attempts
                FROM investigations WHERE investigation_id = %s
                """,
                (investigation_id,),
            )
            row = await cursor.fetchone()
        return _stored(row) if row else None

    async def claim_next(self, worker_id: str) -> Investigation | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                WITH candidate AS (
                    SELECT investigation_id
                    FROM investigations
                    WHERE run_requested
                      AND status NOT IN ('COMPLETED', 'CANCELLED', 'FAILED')
                      AND attempts < 3
                      AND (lease_expires IS NULL OR lease_expires < now())
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE investigations AS i
                SET lease_owner = %s,
                    lease_expires = now() + interval '5 seconds',
                    attempts = attempts + 1,
                    updated_at = now()
                FROM candidate
                WHERE i.investigation_id = candidate.investigation_id
                RETURNING i.investigation
                """,
                (worker_id,),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return Investigation.model_validate(row["investigation"])

    async def save_state(self, investigation_id: str, state: InvestigationState) -> None:
        status = InvestigationStatus(state["status"])
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE investigations
                SET status = %s,
                    report = %s::jsonb,
                    run_requested = %s,
                    lease_owner = NULL,
                    lease_expires = NULL,
                    updated_at = now()
                WHERE investigation_id = %s
                """,
                (
                    status,
                    json.dumps(state.get("report")),
                    status not in _TERMINAL,
                    investigation_id,
                ),
            )

    async def fail_attempt(self, investigation_id: str, safe_error: str) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE investigations
                SET status = CASE WHEN attempts >= 3 THEN 'FAILED' ELSE status END,
                    run_requested = attempts < 3,
                    last_error = %s,
                    lease_owner = NULL,
                    lease_expires = NULL,
                    updated_at = now()
                WHERE investigation_id = %s
                """,
                (safe_error[:500], investigation_id),
            )

    async def release_claim(self, investigation_id: str) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE investigations SET lease_owner = NULL, lease_expires = NULL
                WHERE investigation_id = %s
                """,
                (investigation_id,),
            )

    async def renew_claim(self, investigation_id: str, worker_id: str) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE investigations
                SET lease_expires = now() + interval '5 seconds', updated_at = now()
                WHERE investigation_id = %s AND lease_owner = %s
                RETURNING investigation_id
                """,
                (investigation_id, worker_id),
            )
            return await cursor.fetchone() is not None

    async def request_cancel(self, investigation_id: str) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE investigations SET cancel_requested = true, updated_at = now()
                WHERE investigation_id = %s
                  AND status NOT IN ('COMPLETED', 'CANCELLED', 'FAILED')
                RETURNING investigation_id
                """,
                (investigation_id,),
            )
            return await cursor.fetchone() is not None

    async def is_cancel_requested(self, investigation_id: str) -> bool:
        record = await self.get(investigation_id)
        return record.cancel_requested if record else True


def _stored(row: Mapping[str, Any]) -> StoredInvestigation:
    return StoredInvestigation(
        investigation=Investigation.model_validate(row["investigation"]),
        status=InvestigationStatus(row["status"]),
        report=cast(dict[str, Any] | None, row["report"]),
        cancel_requested=bool(row["cancel_requested"]),
        last_error=cast(str | None, row["last_error"]),
        attempts=int(row["attempts"]),
    )


_TERMINAL = {
    InvestigationStatus.COMPLETED,
    InvestigationStatus.CANCELLED,
    InvestigationStatus.FAILED,
}

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS investigations (
    investigation_id text PRIMARY KEY,
    investigation jsonb NOT NULL,
    status text NOT NULL,
    report jsonb,
    cancel_requested boolean NOT NULL DEFAULT false,
    run_requested boolean NOT NULL DEFAULT true,
    lease_owner text,
    lease_expires timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS investigations_claim_idx
ON investigations (run_requested, status, lease_expires, created_at);
"""
