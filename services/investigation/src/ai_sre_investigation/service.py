"""Application service and restartable investigation worker."""

import asyncio
import secrets
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

from ai_sre_investigation.domain import (
    Alert,
    Investigation,
    InvestigationBudget,
    InvestigationStatus,
)
from ai_sre_investigation.repository import (
    InvestigationEvent,
    InvestigationRepository,
    StoredInvestigation,
)
from ai_sre_investigation.workflow import InvestigationWorkflow


class InvestigationService:
    def __init__(
        self,
        *,
        repository: InvestigationRepository,
        workflow: InvestigationWorkflow,
        poll_seconds: float = 0.1,
    ) -> None:
        self.repository = repository
        self.workflow = workflow
        self._poll_seconds = poll_seconds
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._worker_id = f"worker-{uuid4()}"
        self._current_id: str | None = None

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run_worker(), name=self._worker_id)

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        with suppress(asyncio.CancelledError):
            await self._worker
        if self._current_id:
            await self.repository.release_claim(self._current_id)
        self._current_id = None
        self._worker = None

    async def create(
        self,
        alert: Alert,
        *,
        budget: InvestigationBudget | None = None,
        model_profile: str = "default",
    ) -> StoredInvestigation:
        now = datetime.now(UTC)
        investigation = Investigation(
            investigation_id=f"inv-{uuid4()}",
            trace_id=secrets.token_hex(8),
            alert=alert,
            budget=budget or InvestigationBudget(),
            model_profile=model_profile,
            created_at=now,
            updated_at=now,
        )
        await self.repository.create(investigation)
        await self.repository.append_event(
            investigation.investigation_id,
            "investigation.created",
            InvestigationStatus.RECEIVED,
            {"alert_id": alert.alert_id, "service": alert.service},
        )
        self._wake.set()
        record = await self.repository.get(investigation.investigation_id)
        if record is None:
            raise RuntimeError("created investigation could not be loaded")
        return record

    async def get(self, investigation_id: str) -> StoredInvestigation | None:
        return await self.repository.get(investigation_id)

    async def list_investigations(
        self, limit: int = 50, offset: int = 0
    ) -> list[StoredInvestigation]:
        return await self.repository.list_investigations(limit=limit, offset=offset)

    async def events(
        self, investigation_id: str, after_event_id: int = 0, limit: int = 100
    ) -> list[InvestigationEvent]:
        return await self.repository.list_events(
            investigation_id, after_event_id=after_event_id, limit=limit
        )

    async def cancel(self, investigation_id: str) -> bool:
        changed = await self.repository.request_cancel(investigation_id)
        self._wake.set()
        return changed

    async def _run_worker(self) -> None:
        while True:
            item = await self.repository.claim_next(self._worker_id)
            if item is None:
                self._wake.clear()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self._poll_seconds)
                continue
            self._current_id = item.investigation_id
            heartbeat = asyncio.create_task(
                self._renew_lease(item.investigation_id),
                name=f"{self._worker_id}-lease",
            )
            try:
                snapshot = await self.workflow.graph.aget_state(
                    {"configurable": {"thread_id": item.investigation_id}}
                )
                if snapshot.values:
                    state = await self.workflow.resume(item.investigation_id)
                else:
                    state = await self.workflow.run(item)
                await self.repository.save_state(item.investigation_id, state)
            except asyncio.CancelledError:
                await self.repository.release_claim(item.investigation_id)
                raise
            except Exception:
                await self.repository.fail_attempt(
                    item.investigation_id, "investigation attempt failed"
                )
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
                self._current_id = None

    async def _renew_lease(self, investigation_id: str) -> None:
        while True:
            await asyncio.sleep(1)
            if not await self.repository.renew_claim(investigation_id, self._worker_id):
                return
