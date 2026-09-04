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
        event_sink=repository.append_event,
    )
    return InvestigationService(repository=repository, workflow=workflow, poll_seconds=0.01)


async def request(
    app: Any,
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=json_body, headers=headers)


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


def test_bounded_worker_pool_completes_five_concurrent_investigations() -> None:
    async def scenario() -> None:
        service = make_service()
        service._worker_count = 5
        await service.start()
        first_workers = tuple(service._workers.values())
        await service.start()
        assert tuple(service._workers.values()) == first_workers
        try:
            created = await asyncio.gather(*(service.create(alert()) for _ in range(5)))
            identifiers = [item.investigation.investigation_id for item in created]
            for _ in range(200):
                records = await asyncio.gather(*(service.get(item) for item in identifiers))
                if all(
                    record is not None and record.status == InvestigationStatus.COMPLETED
                    for record in records
                ):
                    assert len({record.investigation.trace_id for record in records if record}) == 5
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("five concurrent investigations did not complete")
        finally:
            await service.stop()
        assert not service._workers
        assert not service._current_ids
        assert all(worker.done() for worker in first_workers)

    asyncio.run(scenario())


def test_worker_pool_rejects_unbounded_concurrency() -> None:
    with pytest.raises(ValueError, match="worker_count"):
        InvestigationService(
            repository=InMemoryInvestigationRepository(),
            workflow=make_service().workflow,
            worker_count=17,
        )


def test_read_model_timeline_evidence_and_sse_replay() -> None:
    async def scenario() -> None:
        service = make_service()
        app = create_app(Settings(environment="test"), service)
        async with app.router.lifespan_context(app):
            created = await request(
                app,
                "POST",
                "/api/v1/investigations",
                {"alert": alert().model_dump(mode="json")},
            )
            identifier = created.json()["investigation"]["investigation_id"]
            for _ in range(100):
                record = await service.get(identifier)
                if record and record.status == InvestigationStatus.COMPLETED:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("worker did not complete")

            listed = await request(app, "GET", "/api/v1/investigations?limit=10")
            assert listed.status_code == 200
            assert listed.json()["items"][0]["investigation"]["investigation_id"] == identifier

            timeline = await request(app, "GET", f"/api/v1/investigations/{identifier}/timeline")
            event_items = timeline.json()["items"]
            assert event_items[0]["event_type"] == "investigation.created"
            assert event_items[-1]["event_type"] == "investigation.finished"

            report = (await request(app, "GET", f"/api/v1/investigations/{identifier}")).json()[
                "report"
            ]
            evidence_id = report["evidence"][0]["evidence_id"]
            evidence = await request(
                app,
                "GET",
                f"/api/v1/investigations/{identifier}/evidence/{evidence_id}",
            )
            assert evidence.json()["evidence"]["evidence_id"] == evidence_id
            assert (
                await request(
                    app,
                    "GET",
                    f"/api/v1/investigations/{identifier}/evidence/ev-0000000000000000",
                )
            ).status_code == 404

            replay = await request(
                app,
                "GET",
                f"/api/v1/investigations/{identifier}/events",
                headers={"Last-Event-ID": str(event_items[0]["event_id"])},
            )
            assert replay.headers["content-type"].startswith("text/event-stream")
            assert "event: investigation" in replay.text
            assert f"id: {event_items[0]['event_id']}" not in replay.text

        assert (
            await request(app, "GET", "/api/v1/investigations/missing/timeline")
        ).status_code == 404
        assert (
            await request(app, "GET", "/api/v1/investigations/missing/evidence/ev-x")
        ).status_code == 404
        assert (
            await request(app, "GET", "/api/v1/investigations/missing/events")
        ).status_code == 404

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
        assert (await repository.list_investigations())[0].investigation == item
        created_event = await repository.append_event(
            item.investigation_id,
            "investigation.tested",
            InvestigationStatus.SCOPING,
            {"ok": True},
        )
        assert (await repository.list_events(item.investigation_id))[0] == created_event
        assert not await repository.list_events(item.investigation_id, created_event.event_id)
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
