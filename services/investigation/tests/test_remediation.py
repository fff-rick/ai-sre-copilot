import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from ai_sre_investigation.config import Settings
from ai_sre_investigation.domain import (
    Alert,
    Investigation,
    InvestigationStatus,
    Severity,
    TimeWindow,
)
from ai_sre_investigation.fakes import FakeModelClient, FakeToolClient
from ai_sre_investigation.main import create_app
from ai_sre_investigation.ports import (
    MutationRequest,
    MutationResponse,
    ToolRequest,
    ToolResponse,
)
from ai_sre_investigation.remediation import (
    ApprovalStatus,
    InMemoryRemediationRepository,
    RecoveryStatus,
    RemediationAction,
    parameters_hash,
)
from ai_sre_investigation.remediation_service import RemediationService, recovery_status
from ai_sre_investigation.repository import InMemoryInvestigationRepository
from ai_sre_investigation.service import InvestigationService
from ai_sre_investigation.workflow import InvestigationWorkflow


class SequenceTools:
    def __init__(self, values: list[Any]) -> None:
        self.values = values
        self.calls: list[ToolRequest] = []

    async def execute_read(self, request: ToolRequest) -> ToolResponse:
        self.calls.append(request)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return ToolResponse(
            tool_name=request.tool_name,
            data=value,
            source_ref="fake://prometheus",
        )


class RecordingMutations:
    def __init__(self) -> None:
        self.calls: list[MutationRequest] = []
        self.accepted_token = ""
        self.approval_id = "apr-placeholder"

    async def execute_mutation(self, request: MutationRequest) -> MutationResponse:
        self.calls.append(request)
        if request.approval_token != self.accepted_token:
            raise PermissionError("approval token is invalid")
        parameters = dict(request.parameters)
        return MutationResponse(
            execution_id="exec-stage5",
            approval_id=self.approval_id,
            investigation_id=request.investigation_id,
            tool_name=request.tool_name,
            target=f"{parameters['namespace']}/deployment/{parameters['name']}",
            parameters_hash=parameters_hash(parameters),
            idempotency_key=request.idempotency_key,
            status="SUCCEEDED",
            data={"changed": True},
        )


def action(**updates: Any) -> RemediationAction:
    values: dict[str, Any] = {
        "action_id": "act-restart-1",
        "tool_name": "kubernetes.restart_deployment",
        "namespace": "ai-sre-test",
        "name": "payment",
        "description": "Restart the isolated payment Deployment.",
        "expected_effect": "Replace unhealthy pods.",
        "rollback_plan": "Roll back to the previous ReplicaSet.",
        "evidence_ids": ["ev-0123456789abcdef"],
        "verification_promql": 'sum(rate(http_requests_total{status=~"5.."}[5m]))',
    }
    values.update(updates)
    return RemediationAction.model_validate(values)


async def configured_service(
    tool_values: list[Any] | None = None,
) -> tuple[InvestigationService, RemediationService, RecordingMutations, str]:
    investigations = InMemoryInvestigationRepository()
    now = datetime.now(UTC)
    item = Investigation(
        investigation_id="inv-stage5",
        trace_id="0123456789abcdef",
        alert=Alert(
            alert_id="stage5-alert",
            service="payment",
            severity=Severity.CRITICAL,
            summary="Elevated error rate.",
            time_window=TimeWindow(start=now - timedelta(minutes=5), end=now),
            source_ref="test://stage5",
        ),
        created_at=now,
        updated_at=now,
    )
    await investigations.create(item)
    await investigations.set_status(item.investigation_id, InvestigationStatus.COMPLETED)
    repository = InMemoryRemediationRepository()
    tools = SequenceTools(tool_values or [{"value": 10}, {"value": 1}])
    mutations = RecordingMutations()
    remediation = RemediationService(
        investigations=investigations,
        repository=repository,
        tools=tools,
        mutations=mutations,
        allowed_namespace="ai-sre-test",
    )
    workflow = InvestigationWorkflow(
        model=FakeModelClient({"hypotheses": []}),
        tools=FakeToolClient({}),
        checkpointer=InMemorySaver(),
    )
    service = InvestigationService(
        repository=investigations, workflow=workflow, remediation=remediation
    )
    return service, remediation, mutations, item.investigation_id


def test_approval_modify_reject_expire_and_roles() -> None:
    async def scenario() -> None:
        _, remediation, _, investigation_id = await configured_service()
        with pytest.raises(PermissionError):
            await remediation.propose(investigation_id, action(), "viewer-1", "viewer")
        with pytest.raises(PermissionError, match="outside"):
            await remediation.propose(
                investigation_id,
                action(namespace="production"),
                "investigator-1",
                "investigator",
            )
        approval = await remediation.propose(
            investigation_id, action(), "investigator-1", "investigator"
        )
        assert approval.status == ApprovalStatus.PENDING
        assert (await remediation.list_approvals(investigation_id)) == [approval]
        with pytest.raises(PermissionError):
            await remediation.approve(
                investigation_id,
                approval.approval_id,
                "investigator-2",
                "investigator",
                900,
            )
        with pytest.raises(PermissionError, match="own"):
            await remediation.approve(
                investigation_id,
                approval.approval_id,
                "investigator-1",
                "approver",
                900,
            )
        grant = await remediation.approve(
            investigation_id, approval.approval_id, "approver-1", "approver", 900
        )
        modified = await remediation.modify(
            investigation_id,
            approval.approval_id,
            action(
                tool_name="kubernetes.scale_deployment",
                replicas=3,
                action_id="act-scale-1",
            ),
            "approver-1",
            "approver",
        )
        assert modified.status == ApprovalStatus.PENDING
        assert modified.parameters_hash != grant.approval.parameters_hash
        second = await remediation.approve(
            investigation_id, approval.approval_id, "approver-2", "approver", 900
        )
        assert second.approval_token != grant.approval_token
        rejected = await remediation.reject(
            investigation_id, approval.approval_id, "approver-2", "approver"
        )
        assert rejected.status == ApprovalStatus.REJECTED
        with pytest.raises(ValueError):
            await remediation.reject(
                investigation_id, approval.approval_id, "approver-2", "approver"
            )
        assert len(await remediation.audit(investigation_id)) == 5
        with pytest.raises(LookupError):
            await remediation.list_approvals("missing")

        repository = InMemoryRemediationRepository()
        expiring = await repository.create(investigation_id, action(), "proposer")
        await repository.approve(expiring.approval_id, "approver", -1)
        expired = await repository.get(expiring.approval_id)
        assert expired is not None and expired.status == ApprovalStatus.EXPIRED
        assert (await repository.list_audit(investigation_id))[-1].event_type == "approval.expired"

    asyncio.run(scenario())


def test_execution_captures_evidence_and_is_idempotent() -> None:
    async def scenario() -> None:
        service, remediation, mutations, investigation_id = await configured_service()
        approval = await remediation.propose(
            investigation_id, action(), "investigator", "investigator"
        )
        grant = await remediation.approve(
            investigation_id, approval.approval_id, "approver", "approver", 900
        )
        mutations.accepted_token = grant.approval_token
        mutations.approval_id = approval.approval_id
        execution = await remediation.execute(
            investigation_id,
            approval.approval_id,
            grant.approval_token,
            "idem-stage5-python",
            "approver",
            "approver",
        )
        assert execution.recovery_status == RecoveryStatus.RECOVERED
        assert execution.pre_evidence and execution.pre_evidence["content_hash"]
        assert execution.post_evidence and execution.post_evidence["content_hash"]
        repeated = await remediation.execute(
            investigation_id,
            approval.approval_id,
            grant.approval_token,
            "idem-stage5-python",
            "approver",
            "approver",
        )
        assert repeated == execution
        assert len(mutations.calls) == 1
        record = await service.get(investigation_id)
        assert record is not None and record.status.value == "COMPLETED"
        assert [event.event_type for event in await service.events(investigation_id)][-3:] == [
            "remediation.executing",
            "remediation.validating",
            "remediation.validated",
        ]

    asyncio.run(scenario())


def test_execution_failure_returns_to_waiting_and_recovery_classification() -> None:
    async def scenario() -> None:
        service, remediation, _, investigation_id = await configured_service(
            [RuntimeError("prometheus unavailable")]
        )
        approval = await remediation.propose(
            investigation_id, action(), "investigator", "investigator"
        )
        await remediation.approve(
            investigation_id, approval.approval_id, "approver", "approver", 900
        )
        with pytest.raises(PermissionError):
            await remediation.execute(
                investigation_id,
                approval.approval_id,
                "wrong-token-value-that-is-long-enough",
                "idem-stage5-failure",
                "approver",
                "approver",
            )
        record = await service.get(investigation_id)
        assert record is not None and record.status.value == "WAITING_APPROVAL"

    asyncio.run(scenario())
    assert (
        recovery_status(
            {"data": {"data": {"result": [{"value": [1, "1"]}]}}},
            {"data": {"data": {"result": [{"value": [2, "2"]}]}}},
            "increase",
        )
        == RecoveryStatus.RECOVERED
    )
    assert recovery_status({"data": {"value": 1}}, {"data": {"value": 1}}, "decrease") == (
        RecoveryStatus.NOT_RECOVERED
    )
    assert recovery_status({"error": "x"}, {"data": []}, "decrease") == (
        RecoveryStatus.UNABLE_TO_DETERMINE
    )


def test_typed_action_rejects_arbitrary_or_mismatched_arguments() -> None:
    with pytest.raises(ValidationError):
        action(tool_name="shell.exec")
    with pytest.raises(ValidationError):
        action(replicas=3)
    with pytest.raises(ValidationError):
        action(tool_name="kubernetes.scale_deployment")
    rollback = action(
        tool_name="kubernetes.rollback_deployment",
        revision=0,
        action_id="act-rollback-1",
    )
    assert rollback.risk_level.value == "high"
    assert rollback.parameters()["revision"] == 0


def test_remediation_http_contract_and_error_mapping() -> None:
    async def request(
        app: Any,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        role: str = "investigator",
        actor: str = "investigator",
    ) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(
                method,
                path,
                json=dict(body) if body else None,
                headers={"X-Actor-ID": actor, "X-Actor-Role": role},
            )

    async def scenario() -> None:
        service, _, mutations, investigation_id = await configured_service()
        app = create_app(Settings(environment="test"), service)
        base = f"/api/v1/investigations/{investigation_id}/approvals"
        proposed = await request(app, "POST", base, {"action": action().model_dump(mode="json")})
        assert proposed.status_code == 202
        approval_id = proposed.json()["approval_id"]
        assert (await request(app, "GET", base)).json()[0]["approval_id"] == approval_id
        denied = await request(
            app,
            "POST",
            f"{base}/{approval_id}/approve",
            {"expires_in_seconds": 900},
        )
        assert denied.status_code == 403
        approved = await request(
            app,
            "POST",
            f"{base}/{approval_id}/approve",
            {"expires_in_seconds": 900},
            role="approver",
            actor="approver",
        )
        assert approved.status_code == 200
        token = approved.json()["approval_token"]
        mutations.accepted_token = token
        mutations.approval_id = approval_id
        executed = await request(
            app,
            "POST",
            f"{base}/{approval_id}/execute",
            {"approval_token": token, "idempotency_key": "idem-stage5-http"},
            role="approver",
            actor="approver",
        )
        assert executed.status_code == 200
        assert executed.json()["recovery_status"] == "RECOVERED"
        audit = await request(
            app,
            "GET",
            f"/api/v1/investigations/{investigation_id}/remediation-audit",
        )
        assert audit.status_code == 200 and len(audit.json()) == 2
        assert (
            await request(app, "GET", "/api/v1/investigations/missing/approvals")
        ).status_code == 404

        unconfigured, _, _, _ = await configured_service()
        unconfigured.remediation = None
        unavailable = await request(
            create_app(Settings(environment="test"), unconfigured), "GET", base
        )
        assert unavailable.status_code == 503

    asyncio.run(scenario())
