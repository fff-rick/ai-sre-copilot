import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai_sre_investigation.config import Settings
from ai_sre_investigation.domain import (
    Alert,
    Investigation,
    InvestigationStatus,
    Severity,
    TimeWindow,
)
from ai_sre_investigation.fakes import FakeToolClient
from ai_sre_investigation.main import create_app
from ai_sre_investigation.ports import ModelRequest, ModelResponse
from ai_sre_investigation.repository import InMemoryInvestigationRepository
from ai_sre_investigation.service import InvestigationService
from ai_sre_investigation.workflow import InvestigationState, InvestigationWorkflow


class FirstEvidenceModel:
    async def complete(self, request: ModelRequest) -> ModelResponse:
        payload = json.loads(request.input_text.split("\n", 1)[0])
        evidence_id = payload["evidence"][0]["evidence_id"]
        return ModelResponse(
            data={
                "hypotheses": [
                    {
                        "statement": "The observed error rate is elevated.",
                        "rank": 1,
                        "confidence": 0.9,
                        "supporting_evidence_ids": [evidence_id],
                        "contradicting_evidence_ids": [],
                        "next_checks": [],
                    }
                ]
            },
            model_id="fake-dynamic",
            input_tokens=10,
            output_tokens=5,
        )


def alert() -> Alert:
    now = datetime.now(UTC)
    return Alert(
        alert_id="api-alert",
        service="payment",
        severity=Severity.CRITICAL,
        summary="Payment errors increased.",
        time_window=TimeWindow(start=now - timedelta(minutes=10), end=now),
        source_ref="test://alert",
    )


def make_service() -> InvestigationService:
    repository = InMemoryInvestigationRepository()
    workflow = InvestigationWorkflow(
        model=FirstEvidenceModel(),
        tools=FakeToolClient(
            {
                "prometheus.query": {"error_rate": 0.1},
                "loki.query_range": {"errors": 3},
                "tempo.search_traces": {"traces": 2},
                "releases.list": {"release": "v2"},
            }
        ),
        checkpointer=InMemorySaver(),
        cancel_check=repository.is_cancel_requested,
    )
    return InvestigationService(repository=repository, workflow=workflow, poll_seconds=0.01)


async def request(
    app: Any, method: str, path: str, json_body: dict[str, Any] | None = None
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=json_body)


def test_investigation_api_create_get_cancel_and_not_found() -> None:
    async def scenario() -> None:
        service = make_service()
        app = create_app(Settings(environment="test"), service)
        created = await request(
            app,
            "POST",
            "/api/v1/investigations",
            {"alert": alert().model_dump(mode="json")},
        )
        assert created.status_code == 202
        identifier = created.json()["investigation"]["investigation_id"]
        fetched = await request(app, "GET", f"/api/v1/investigations/{identifier}")
        assert fetched.status_code == 200
        cancelled = await request(app, "POST", f"/api/v1/investigations/{identifier}/cancel")
        assert cancelled.json()["cancel_requested"] is True
        assert (await request(app, "GET", "/api/v1/investigations/missing")).status_code == 404
        assert (
            await request(app, "POST", "/api/v1/investigations/missing/cancel")
        ).status_code == 404

        async with app.router.lifespan_context(app):
            for _ in range(100):
                record = await service.get(identifier)
                if record and record.status == InvestigationStatus.CANCELLED:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("cancelled investigation did not reach terminal state")

        repeat = await request(app, "POST", f"/api/v1/investigations/{identifier}/cancel")
        assert repeat.status_code == 200
        assert repeat.json()["cancel_requested"] is False

    asyncio.run(scenario())


def test_worker_completes_created_investigation() -> None:
    async def scenario() -> None:
        service = make_service()
        await service.start()
        try:
            created = await service.create(alert())
            for _ in range(100):
                record = await service.get(created.investigation.investigation_id)
                if record and record.status == InvestigationStatus.COMPLETED:
                    assert record.report is not None
                    assert record.report["hypotheses"][0]["rank"] == 1
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("worker did not complete")
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_unconfigured_runtime_rejects_durable_operations() -> None:
    async def scenario() -> None:
        app = create_app(Settings(environment="test"))
        async with app.router.lifespan_context(app):
            create = await request(
                app,
                "POST",
                "/api/v1/investigations",
                {"alert": alert().model_dump(mode="json")},
            )
            assert create.status_code == 503
            assert (await request(app, "GET", "/api/v1/investigations/id")).status_code == 503
            assert (
                await request(app, "POST", "/api/v1/investigations/id/cancel")
            ).status_code == 503

    asyncio.run(scenario())


def test_memory_repository_attempts_duplicates_release_and_terminal_cancel() -> None:
    async def scenario() -> None:
        repository = InMemoryInvestigationRepository()
        now = datetime.now(UTC)
        item = Investigation(
            investigation_id="inv-repository",
            trace_id="0123456789abcdef",
            alert=alert(),
            created_at=now,
            updated_at=now,
        )
        await repository.create(item)
        with pytest.raises(ValueError, match="already exists"):
            await repository.create(item)
        assert await repository.claim_next("worker") == item
        await repository.release_claim(item.investigation_id)
        assert await repository.claim_next("worker") == item
        await repository.fail_attempt(item.investigation_id, "safe failure")
        assert await repository.claim_next("worker") == item
        await repository.fail_attempt(item.investigation_id, "safe failure")
        failed = await repository.get(item.investigation_id)
        assert failed is not None
        assert failed.status == InvestigationStatus.FAILED
        assert not await repository.request_cancel(item.investigation_id)
        assert await repository.is_cancel_requested("missing")

        second = item.model_copy(update={"investigation_id": "inv-saved"})
        await repository.create(second)
        state = InvestigationState(
            investigation=second.model_dump(mode="json"),
            status=InvestigationStatus.COMPLETED,
            report={"ok": True},
        )
        await repository.save_state(second.investigation_id, state)
        saved = await repository.get(second.investigation_id)
        assert saved is not None and saved.report == {"ok": True}

    asyncio.run(scenario())
