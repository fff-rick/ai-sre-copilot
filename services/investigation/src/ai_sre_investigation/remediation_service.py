"""Application-level orchestration around durable approval and gateway execution."""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ai_sre_investigation.domain import InvestigationStatus
from ai_sre_investigation.ports import (
    MutationClient,
    MutationRequest,
    ToolClient,
    ToolRequest,
)
from ai_sre_investigation.remediation import (
    ApprovalGrant,
    ApprovalStatus,
    RecoveryStatus,
    RemediationAction,
    RemediationApproval,
    RemediationAuditEvent,
    RemediationExecution,
    RemediationRepository,
)
from ai_sre_investigation.repository import InvestigationRepository


class RemediationService:
    def __init__(
        self,
        *,
        investigations: InvestigationRepository,
        repository: RemediationRepository,
        tools: ToolClient,
        mutations: MutationClient,
        allowed_namespace: str,
        validation_delay_seconds: float = 0,
    ) -> None:
        self._investigations = investigations
        self._repository = repository
        self._tools = tools
        self._mutations = mutations
        self._allowed_namespace = allowed_namespace
        self._validation_delay_seconds = validation_delay_seconds

    async def propose(
        self,
        investigation_id: str,
        action: RemediationAction,
        actor_id: str,
        role: str,
    ) -> RemediationApproval:
        self._require_role(role, {"investigator", "admin"})
        record = await self._investigations.get(investigation_id)
        if record is None:
            raise LookupError("investigation not found")
        if record.status != InvestigationStatus.COMPLETED:
            raise ValueError("investigation must complete before remediation is proposed")
        if action.namespace != self._allowed_namespace:
            raise PermissionError("remediation target is outside the isolated namespace")
        approval = await self._repository.create(investigation_id, action, actor_id)
        await self._transition(
            investigation_id,
            InvestigationStatus.WAITING_APPROVAL,
            "remediation.proposed",
            {"approval_id": approval.approval_id, "risk_level": approval.risk_level},
        )
        return approval

    async def list_approvals(self, investigation_id: str) -> list[RemediationApproval]:
        investigation = await self._investigations.get(investigation_id)
        if investigation is None:
            raise LookupError("investigation not found")
        approvals = await self._repository.list_approvals(investigation_id)
        if (
            investigation.status == InvestigationStatus.WAITING_APPROVAL
            and approvals
            and all(
                item.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}
                for item in approvals
            )
        ):
            await self._transition(
                investigation_id,
                InvestigationStatus.COMPLETED,
                "remediation.expired",
                {"approval_ids": [item.approval_id for item in approvals]},
            )
        return approvals

    async def approve(
        self,
        investigation_id: str,
        approval_id: str,
        actor_id: str,
        role: str,
        expires_in_seconds: int,
    ) -> ApprovalGrant:
        self._require_role(role, {"approver"})
        approval = await self._owned(investigation_id, approval_id)
        if approval.proposed_by == actor_id:
            raise PermissionError("proposer cannot approve their own remediation")
        grant = await self._repository.approve(approval_id, actor_id, expires_in_seconds)
        await self._transition(
            investigation_id,
            InvestigationStatus.WAITING_APPROVAL,
            "remediation.approved",
            {
                "approval_id": approval_id,
                "expires_at": grant.approval.token_expires_at,
            },
        )
        return grant

    async def modify(
        self,
        investigation_id: str,
        approval_id: str,
        action: RemediationAction,
        actor_id: str,
        role: str,
    ) -> RemediationApproval:
        self._require_role(role, {"approver"})
        await self._owned(investigation_id, approval_id)
        if action.namespace != self._allowed_namespace:
            raise PermissionError("remediation target is outside the isolated namespace")
        approval = await self._repository.modify(approval_id, action, actor_id)
        await self._transition(
            investigation_id,
            InvestigationStatus.WAITING_APPROVAL,
            "remediation.modified",
            {"approval_id": approval_id, "parameters_hash": approval.parameters_hash},
        )
        return approval

    async def reject(
        self,
        investigation_id: str,
        approval_id: str,
        actor_id: str,
        role: str,
    ) -> RemediationApproval:
        self._require_role(role, {"approver"})
        await self._owned(investigation_id, approval_id)
        approval = await self._repository.reject(approval_id, actor_id)
        await self._transition(
            investigation_id,
            InvestigationStatus.COMPLETED,
            "remediation.rejected",
            {"approval_id": approval_id},
        )
        return approval

    async def execute(
        self,
        investigation_id: str,
        approval_id: str,
        approval_token: str,
        idempotency_key: str,
        actor_id: str,
        role: str,
    ) -> RemediationExecution:
        self._require_role(role, {"approver"})
        approval = await self._owned(investigation_id, approval_id)
        record = await self._investigations.get(investigation_id)
        if record is None:
            raise LookupError("investigation not found")
        existing = await self._repository.get_execution(investigation_id, idempotency_key)
        if existing is not None and existing.recovery_status is not None:
            return existing

        pre = await self._observe(record.investigation.trace_id, approval)
        await self._transition(
            investigation_id,
            InvestigationStatus.EXECUTING,
            "remediation.executing",
            {"approval_id": approval_id, "idempotency_key": idempotency_key},
        )
        try:
            response = await self._mutations.execute_mutation(
                MutationRequest(
                    investigation_id=investigation_id,
                    trace_id=record.investigation.trace_id,
                    actor_id=actor_id,
                    approval_token=approval_token,
                    idempotency_key=idempotency_key,
                    tool_name=approval.action.tool_name,
                    parameters=approval.parameters,
                )
            )
        except Exception:
            await self._transition(
                investigation_id,
                InvestigationStatus.WAITING_APPROVAL,
                "remediation.execution_rejected",
                {"approval_id": approval_id, "idempotency_key": idempotency_key},
            )
            raise
        if (
            response.approval_id != approval_id
            or response.tool_name != approval.action.tool_name
            or response.target != approval.target
            or response.parameters_hash != approval.parameters_hash
        ):
            raise RuntimeError("gateway returned an unexpected approval binding")
        if response.replayed:
            replay = await self._repository.get_execution(investigation_id, idempotency_key)
            if replay is not None and replay.recovery_status is not None:
                return replay

        await self._transition(
            investigation_id,
            InvestigationStatus.VALIDATING,
            "remediation.validating",
            {"approval_id": approval_id, "execution_id": response.execution_id},
        )
        if self._validation_delay_seconds:
            await asyncio.sleep(self._validation_delay_seconds)
        post = await self._observe(record.investigation.trace_id, approval)
        recovery = recovery_status(pre, post, approval.action.recovery_goal)
        now = datetime.now(UTC)
        execution = RemediationExecution(
            execution_id=response.execution_id,
            approval_id=response.approval_id,
            investigation_id=response.investigation_id,
            tool_name=response.tool_name,
            target=response.target,
            parameters_hash=response.parameters_hash,
            idempotency_key=response.idempotency_key,
            status=response.status,
            result=(dict(response.data) if isinstance(response.data, Mapping) else response.data),
            safe_error=response.safe_error,
            recovery_status=recovery,
            pre_evidence=pre,
            post_evidence=post,
            started_at=now,
            finished_at=now,
        )
        execution = await self._repository.record_execution(execution)
        await self._transition(
            investigation_id,
            InvestigationStatus.COMPLETED,
            "remediation.validated",
            {
                "approval_id": approval_id,
                "execution_id": response.execution_id,
                "recovery_status": recovery,
            },
        )
        return execution

    async def audit(self, investigation_id: str) -> list[RemediationAuditEvent]:
        if await self._investigations.get(investigation_id) is None:
            raise LookupError("investigation not found")
        return await self._repository.list_audit(investigation_id)

    async def _owned(self, investigation_id: str, approval_id: str) -> RemediationApproval:
        approval = await self._repository.get(approval_id)
        if approval is None or approval.investigation_id != investigation_id:
            raise LookupError("approval not found")
        return approval

    async def _observe(self, trace_id: str, approval: RemediationApproval) -> dict[str, Any]:
        observed_at = datetime.now(UTC)
        try:
            response = await self._tools.execute_read(
                ToolRequest(
                    investigation_id=approval.investigation_id,
                    trace_id=trace_id,
                    tool_name="prometheus.query",
                    arguments={"promql": approval.action.verification_promql},
                )
            )
            data: Any = response.data
            payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
            return {
                "observed_at": observed_at.isoformat(),
                "source_ref": response.source_ref,
                "query": approval.action.verification_promql,
                "data": data,
                "content_hash": hashlib.sha256(payload.encode()).hexdigest(),
            }
        except Exception as error:
            return {
                "observed_at": observed_at.isoformat(),
                "query": approval.action.verification_promql,
                "error": type(error).__name__,
            }

    async def _transition(
        self,
        investigation_id: str,
        status: InvestigationStatus,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await self._investigations.set_status(investigation_id, status)
        await self._investigations.append_event(investigation_id, event_type, status, payload)

    @staticmethod
    def _require_role(actual: str, allowed: set[str]) -> None:
        if actual not in allowed:
            raise PermissionError("caller role is not allowed for this operation")


def recovery_status(pre: dict[str, Any], post: dict[str, Any], goal: str) -> RecoveryStatus:
    before = _scalar(pre.get("data"))
    after = _scalar(post.get("data"))
    if before is None or after is None:
        return RecoveryStatus.UNABLE_TO_DETERMINE
    if (goal == "decrease" and after < before) or (goal == "increase" and after > before):
        return RecoveryStatus.RECOVERED
    return RecoveryStatus.NOT_RECOVERED


def _scalar(value: Any) -> float | None:
    try:
        if isinstance(value, dict) and "value" in value:
            return float(value["value"])
        result = value["data"]["result"]
        if isinstance(result, list) and result:
            sample = result[0].get("value")
            if isinstance(sample, list) and len(sample) == 2:
                return float(sample[1])
    except KeyError, TypeError, ValueError:
        return None
    return None
