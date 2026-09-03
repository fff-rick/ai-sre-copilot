"""Durable human approval state machine for bounded remediation."""

import asyncio
import hashlib
import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from psycopg_pool import AsyncConnectionPool
from pydantic import AwareDatetime, Field, model_validator

from ai_sre_investigation.domain import FrozenModel, RiskLevel


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class RecoveryStatus(StrEnum):
    RECOVERED = "RECOVERED"
    NOT_RECOVERED = "NOT_RECOVERED"
    UNABLE_TO_DETERMINE = "UNABLE_TO_DETERMINE"


class RemediationAction(FrozenModel):
    action_id: str = Field(pattern=r"^act-[a-zA-Z0-9_-]{1,60}$")
    tool_name: str = Field(pattern=r"^kubernetes\.(restart|scale|rollback)_deployment$")
    namespace: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
    name: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
    replicas: int | None = Field(default=None, ge=0, le=100)
    revision: int | None = Field(default=None, ge=0)
    description: str = Field(min_length=1, max_length=1_000)
    expected_effect: str = Field(min_length=1, max_length=1_000)
    rollback_plan: str = Field(min_length=1, max_length=1_000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    verification_promql: str = Field(min_length=1, max_length=4_000)
    recovery_goal: Literal["decrease", "increase"] = "decrease"

    @model_validator(mode="after")
    def typed_parameters(self) -> RemediationAction:
        if self.tool_name == "kubernetes.scale_deployment":
            if self.replicas is None or self.revision is not None:
                raise ValueError("scale requires replicas and does not accept revision")
        elif self.tool_name == "kubernetes.rollback_deployment":
            if self.revision is None or self.replicas is not None:
                raise ValueError("rollback requires revision and does not accept replicas")
        elif self.replicas is not None or self.revision is not None:
            raise ValueError("restart does not accept replicas or revision")
        return self

    @property
    def target(self) -> str:
        return f"{self.namespace}/deployment/{self.name}"

    def parameters(self) -> dict[str, Any]:
        value: dict[str, Any] = {"name": self.name, "namespace": self.namespace}
        if self.replicas is not None:
            value["replicas"] = self.replicas
        if self.revision is not None:
            value["revision"] = self.revision
        return value

    @property
    def risk_level(self) -> RiskLevel:
        if self.tool_name == "kubernetes.rollback_deployment":
            return RiskLevel.HIGH
        if self.tool_name == "kubernetes.scale_deployment" and (
            self.replicas == 0 or (self.replicas is not None and self.replicas > 10)
        ):
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM


class RemediationApproval(FrozenModel):
    approval_id: str
    investigation_id: str
    action: RemediationAction
    target: str
    parameters: dict[str, Any]
    parameters_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    risk_level: RiskLevel
    status: ApprovalStatus
    proposed_by: str
    approved_by: str | None = None
    rejected_by: str | None = None
    token_expires_at: AwareDatetime | None = None
    consumed_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ApprovalGrant(FrozenModel):
    approval: RemediationApproval
    approval_token: str = Field(min_length=32, max_length=512)


class RemediationExecution(FrozenModel):
    execution_id: str
    approval_id: str
    investigation_id: str
    tool_name: str
    target: str
    parameters_hash: str
    idempotency_key: str
    status: str
    result: dict[str, Any] | list[Any] | None = None
    safe_error: str = ""
    recovery_status: RecoveryStatus | None = None
    pre_evidence: dict[str, Any] | None = None
    post_evidence: dict[str, Any] | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None


class RemediationAuditEvent(FrozenModel):
    event_id: int
    investigation_id: str
    approval_id: str | None
    event_type: str
    actor_id: str
    outcome: str
    tool_name: str
    target: str
    parameters_hash: str
    payload: dict[str, Any]
    created_at: AwareDatetime


class RemediationRepository(Protocol):
    async def create(
        self, investigation_id: str, action: RemediationAction, proposed_by: str
    ) -> RemediationApproval: ...

    async def get(self, approval_id: str) -> RemediationApproval | None: ...

    async def list_approvals(self, investigation_id: str) -> list[RemediationApproval]: ...

    async def approve(
        self, approval_id: str, actor_id: str, expires_in_seconds: int
    ) -> ApprovalGrant: ...

    async def modify(
        self, approval_id: str, action: RemediationAction, actor_id: str
    ) -> RemediationApproval: ...

    async def reject(self, approval_id: str, actor_id: str) -> RemediationApproval: ...

    async def record_execution(self, execution: RemediationExecution) -> RemediationExecution: ...

    async def get_execution(
        self, investigation_id: str, idempotency_key: str
    ) -> RemediationExecution | None: ...

    async def list_audit(self, investigation_id: str) -> list[RemediationAuditEvent]: ...


class InMemoryRemediationRepository:
    def __init__(self) -> None:
        self._approvals: dict[str, RemediationApproval] = {}
        self._token_hashes: dict[str, str] = {}
        self._executions: dict[str, RemediationExecution] = {}
        self._audit: list[RemediationAuditEvent] = []
        self._lock = asyncio.Lock()

    async def create(
        self, investigation_id: str, action: RemediationAction, proposed_by: str
    ) -> RemediationApproval:
        async with self._lock:
            now = datetime.now(UTC)
            approval = RemediationApproval(
                approval_id=f"apr-{uuid4()}",
                investigation_id=investigation_id,
                action=action,
                target=action.target,
                parameters=action.parameters(),
                parameters_hash=parameters_hash(action.parameters()),
                risk_level=action.risk_level,
                status=ApprovalStatus.PENDING,
                proposed_by=proposed_by,
                created_at=now,
                updated_at=now,
            )
            self._approvals[approval.approval_id] = approval
            self._append_audit(approval, "approval.proposed", proposed_by, "success")
            return approval

    async def get(self, approval_id: str) -> RemediationApproval | None:
        async with self._lock:
            return self._expire(self._approvals.get(approval_id))

    async def list_approvals(self, investigation_id: str) -> list[RemediationApproval]:
        async with self._lock:
            result: list[RemediationApproval] = []
            for item in self._approvals.values():
                if item.investigation_id == investigation_id:
                    expired = self._expire(item)
                    if expired is not None:
                        result.append(expired)
            return result

    async def approve(
        self, approval_id: str, actor_id: str, expires_in_seconds: int
    ) -> ApprovalGrant:
        async with self._lock:
            approval = self._required(approval_id)
            if approval.status != ApprovalStatus.PENDING:
                raise ValueError("only pending approval can be approved")
            token = secrets.token_urlsafe(32)
            updated = approval.model_copy(
                update={
                    "status": ApprovalStatus.APPROVED,
                    "approved_by": actor_id,
                    "token_expires_at": datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
                    "updated_at": datetime.now(UTC),
                }
            )
            self._approvals[approval_id] = updated
            self._token_hashes[approval_id] = token_hash(token)
            self._append_audit(updated, "approval.approved", actor_id, "success")
            return ApprovalGrant(approval=updated, approval_token=token)

    async def modify(
        self, approval_id: str, action: RemediationAction, actor_id: str
    ) -> RemediationApproval:
        async with self._lock:
            approval = self._required(approval_id)
            if approval.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
                raise ValueError("approval can no longer be modified")
            updated = approval.model_copy(
                update={
                    "action": action,
                    "target": action.target,
                    "parameters": action.parameters(),
                    "parameters_hash": parameters_hash(action.parameters()),
                    "risk_level": action.risk_level,
                    "status": ApprovalStatus.PENDING,
                    "approved_by": None,
                    "token_expires_at": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._approvals[approval_id] = updated
            self._token_hashes.pop(approval_id, None)
            self._append_audit(updated, "approval.modified", actor_id, "success")
            return updated

    async def reject(self, approval_id: str, actor_id: str) -> RemediationApproval:
        async with self._lock:
            approval = self._required(approval_id)
            if approval.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
                raise ValueError("approval can no longer be rejected")
            updated = approval.model_copy(
                update={
                    "status": ApprovalStatus.REJECTED,
                    "rejected_by": actor_id,
                    "token_expires_at": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._approvals[approval_id] = updated
            self._token_hashes.pop(approval_id, None)
            self._append_audit(updated, "approval.rejected", actor_id, "success")
            return updated

    async def record_execution(self, execution: RemediationExecution) -> RemediationExecution:
        async with self._lock:
            self._executions[execution.idempotency_key] = execution
            return execution

    async def get_execution(
        self, investigation_id: str, idempotency_key: str
    ) -> RemediationExecution | None:
        async with self._lock:
            execution = self._executions.get(idempotency_key)
            if execution is None or execution.investigation_id != investigation_id:
                return None
            return execution

    async def list_audit(self, investigation_id: str) -> list[RemediationAuditEvent]:
        async with self._lock:
            return [item for item in self._audit if item.investigation_id == investigation_id]

    def _required(self, approval_id: str) -> RemediationApproval:
        approval = self._expire(self._approvals.get(approval_id))
        if approval is None:
            raise LookupError("approval not found")
        return approval

    def _expire(self, approval: RemediationApproval | None) -> RemediationApproval | None:
        if (
            approval is not None
            and approval.status == ApprovalStatus.APPROVED
            and approval.token_expires_at is not None
            and approval.token_expires_at <= datetime.now(UTC)
        ):
            approval = approval.model_copy(
                update={"status": ApprovalStatus.EXPIRED, "updated_at": datetime.now(UTC)}
            )
            self._approvals[approval.approval_id] = approval
            self._token_hashes.pop(approval.approval_id, None)
            self._append_audit(approval, "approval.expired", "remediation-service", "expired")
        return approval

    def _append_audit(
        self, approval: RemediationApproval, event_type: str, actor_id: str, outcome: str
    ) -> None:
        self._audit.append(
            RemediationAuditEvent(
                event_id=len(self._audit) + 1,
                investigation_id=approval.investigation_id,
                approval_id=approval.approval_id,
                event_type=event_type,
                actor_id=actor_id,
                outcome=outcome,
                tool_name=approval.action.tool_name,
                target=approval.target,
                parameters_hash=approval.parameters_hash,
                payload={},
                created_at=datetime.now(UTC),
            )
        )


class PostgresRemediationRepository:  # pragma: no cover - integration acceptance
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(_SCHEMA)

    async def create(
        self, investigation_id: str, action: RemediationAction, proposed_by: str
    ) -> RemediationApproval:
        approval_id = f"apr-{uuid4()}"
        parameters = action.parameters()
        async with self._pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """
                INSERT INTO remediation_approvals (
                    approval_id, investigation_id, action_id, tool_name, target,
                    parameters, parameters_hash, risk_level, status, proposed_by, action
                ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,'PENDING',%s,%s::jsonb)
                RETURNING *
                """,
                (
                    approval_id,
                    investigation_id,
                    action.action_id,
                    action.tool_name,
                    action.target,
                    json.dumps(parameters, sort_keys=True, separators=(",", ":")),
                    parameters_hash(parameters),
                    action.risk_level,
                    proposed_by,
                    json.dumps(action.model_dump(mode="json")),
                ),
            )
            row = await cursor.fetchone()
            await self._audit(connection, _approval(row), "approval.proposed", proposed_by)
        return _approval(row)

    async def get(self, approval_id: str) -> RemediationApproval | None:
        await self._expire_due()
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM remediation_approvals WHERE approval_id=%s", (approval_id,)
            )
            row = await cursor.fetchone()
        return _approval(row) if row else None

    async def list_approvals(self, investigation_id: str) -> list[RemediationApproval]:
        await self._expire_due()
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT * FROM remediation_approvals
                   WHERE investigation_id=%s ORDER BY created_at""",
                (investigation_id,),
            )
            rows = await cursor.fetchall()
        return [_approval(row) for row in rows]

    async def approve(
        self, approval_id: str, actor_id: str, expires_in_seconds: int
    ) -> ApprovalGrant:
        token = secrets.token_urlsafe(32)
        async with self._pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """
                UPDATE remediation_approvals
                SET status='APPROVED', approved_by=%s, rejected_by=NULL,
                    token_hash=%s, token_expires_at=now()+(%s * interval '1 second'),
                    updated_at=now()
                WHERE approval_id=%s AND status='PENDING'
                RETURNING *
                """,
                (actor_id, token_hash(token), expires_in_seconds, approval_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("only pending approval can be approved")
            approval = _approval(row)
            await self._audit(connection, approval, "approval.approved", actor_id)
        return ApprovalGrant(approval=approval, approval_token=token)

    async def modify(
        self, approval_id: str, action: RemediationAction, actor_id: str
    ) -> RemediationApproval:
        parameters = action.parameters()
        async with self._pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """
                UPDATE remediation_approvals
                SET action_id=%s, tool_name=%s, target=%s, parameters=%s::jsonb,
                    parameters_hash=%s, risk_level=%s, action=%s::jsonb,
                    status='PENDING', approved_by=NULL, token_hash=NULL,
                    token_expires_at=NULL, updated_at=now()
                WHERE approval_id=%s AND status IN ('PENDING','APPROVED')
                RETURNING *
                """,
                (
                    action.action_id,
                    action.tool_name,
                    action.target,
                    json.dumps(parameters, sort_keys=True, separators=(",", ":")),
                    parameters_hash(parameters),
                    action.risk_level,
                    json.dumps(action.model_dump(mode="json")),
                    approval_id,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("approval can no longer be modified")
            approval = _approval(row)
            await self._audit(connection, approval, "approval.modified", actor_id)
        return approval

    async def reject(self, approval_id: str, actor_id: str) -> RemediationApproval:
        async with self._pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """
                UPDATE remediation_approvals
                SET status='REJECTED', rejected_by=%s, token_hash=NULL,
                    token_expires_at=NULL, updated_at=now()
                WHERE approval_id=%s AND status IN ('PENDING','APPROVED')
                RETURNING *
                """,
                (actor_id, approval_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("approval can no longer be rejected")
            approval = _approval(row)
            await self._audit(connection, approval, "approval.rejected", actor_id)
        return approval

    async def record_execution(self, execution: RemediationExecution) -> RemediationExecution:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE remediation_executions
                SET recovery_status=%s, pre_evidence=%s::jsonb, post_evidence=%s::jsonb
                WHERE idempotency_key=%s
                RETURNING *
                """,
                (
                    execution.recovery_status,
                    json.dumps(execution.pre_evidence or {}),
                    json.dumps(execution.post_evidence or {}),
                    execution.idempotency_key,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise LookupError("execution not found")
        return _execution(row)

    async def get_execution(
        self, investigation_id: str, idempotency_key: str
    ) -> RemediationExecution | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT * FROM remediation_executions
                   WHERE investigation_id=%s AND idempotency_key=%s""",
                (investigation_id, idempotency_key),
            )
            row = await cursor.fetchone()
        return _execution(row) if row else None

    async def list_audit(self, investigation_id: str) -> list[RemediationAuditEvent]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT * FROM remediation_audit_events
                   WHERE investigation_id=%s ORDER BY event_id""",
                (investigation_id,),
            )
            rows = await cursor.fetchall()
        return [_audit_event(row) for row in rows]

    async def _expire_due(self) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                WITH expired AS (
                    UPDATE remediation_approvals
                    SET status='EXPIRED', token_hash=NULL, updated_at=now()
                    WHERE status='APPROVED' AND token_expires_at <= now()
                    RETURNING investigation_id, approval_id, tool_name, target,
                              parameters_hash
                )
                INSERT INTO remediation_audit_events (
                    investigation_id, approval_id, event_type, actor_id, outcome,
                    tool_name, target, parameters_hash
                )
                SELECT investigation_id, approval_id, 'approval.expired',
                       'remediation-service', 'expired', tool_name, target,
                       parameters_hash
                FROM expired
                """
            )

    async def _audit(
        self,
        connection: Any,
        approval: RemediationApproval,
        event_type: str,
        actor_id: str,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO remediation_audit_events (
                investigation_id, approval_id, event_type, actor_id, outcome,
                tool_name, target, parameters_hash
            ) VALUES (%s,%s,%s,%s,'success',%s,%s,%s)
            """,
            (
                approval.investigation_id,
                approval.approval_id,
                event_type,
                actor_id,
                approval.action.tool_name,
                approval.target,
                approval.parameters_hash,
            ),
        )


def parameters_hash(parameters: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(parameters), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _approval(row: Mapping[str, Any]) -> RemediationApproval:
    return RemediationApproval(
        approval_id=str(row["approval_id"]),
        investigation_id=str(row["investigation_id"]),
        action=RemediationAction.model_validate(row["action"]),
        target=str(row["target"]),
        parameters=cast(dict[str, Any], row["parameters"]),
        parameters_hash=str(row["parameters_hash"]),
        risk_level=RiskLevel(row["risk_level"]),
        status=ApprovalStatus(row["status"]),
        proposed_by=str(row["proposed_by"]),
        approved_by=cast(str | None, row["approved_by"]),
        rejected_by=cast(str | None, row["rejected_by"]),
        token_expires_at=cast(datetime | None, row["token_expires_at"]),
        consumed_at=cast(datetime | None, row["consumed_at"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


def _execution(row: Mapping[str, Any]) -> RemediationExecution:
    return RemediationExecution(
        execution_id=str(row["execution_id"]),
        approval_id=str(row["approval_id"]),
        investigation_id=str(row["investigation_id"]),
        tool_name=str(row["tool_name"]),
        target=str(row["target"]),
        parameters_hash=str(row["parameters_hash"]),
        idempotency_key=str(row["idempotency_key"]),
        status=str(row["status"]),
        result=cast(dict[str, Any] | list[Any] | None, row["result"]),
        safe_error=str(row["safe_error"]),
        recovery_status=(
            RecoveryStatus(row["recovery_status"]) if row["recovery_status"] else None
        ),
        pre_evidence=cast(dict[str, Any] | None, row["pre_evidence"]),
        post_evidence=cast(dict[str, Any] | None, row["post_evidence"]),
        started_at=cast(datetime, row["started_at"]),
        finished_at=cast(datetime | None, row["finished_at"]),
    )


def _audit_event(row: Mapping[str, Any]) -> RemediationAuditEvent:
    return RemediationAuditEvent(
        event_id=int(row["event_id"]),
        investigation_id=str(row["investigation_id"]),
        approval_id=cast(str | None, row["approval_id"]),
        event_type=str(row["event_type"]),
        actor_id=str(row["actor_id"]),
        outcome=str(row["outcome"]),
        tool_name=str(row["tool_name"]),
        target=str(row["target"]),
        parameters_hash=str(row["parameters_hash"]),
        payload=cast(dict[str, Any], row["payload"]),
        created_at=cast(datetime, row["created_at"]),
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS remediation_approvals (
    approval_id text PRIMARY KEY,
    investigation_id text NOT NULL REFERENCES investigations(investigation_id) ON DELETE CASCADE,
    action_id text NOT NULL,
    tool_name text NOT NULL,
    target text NOT NULL,
    parameters jsonb NOT NULL,
    parameters_hash char(64) NOT NULL,
    risk_level text NOT NULL,
    status text NOT NULL,
    proposed_by text NOT NULL,
    approved_by text,
    rejected_by text,
    token_hash char(64) UNIQUE,
    token_expires_at timestamptz,
    consumed_at timestamptz,
    action jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS remediation_approvals_investigation_idx
ON remediation_approvals (investigation_id, created_at);

CREATE TABLE IF NOT EXISTS remediation_executions (
    execution_id text PRIMARY KEY,
    approval_id text NOT NULL REFERENCES remediation_approvals(approval_id),
    investigation_id text NOT NULL REFERENCES investigations(investigation_id) ON DELETE CASCADE,
    tool_name text NOT NULL,
    target text NOT NULL,
    parameters_hash char(64) NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    status text NOT NULL,
    result jsonb,
    safe_error text NOT NULL DEFAULT '',
    recovery_status text,
    pre_evidence jsonb,
    post_evidence jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE TABLE IF NOT EXISTS remediation_audit_events (
    event_id bigserial PRIMARY KEY,
    investigation_id text NOT NULL REFERENCES investigations(investigation_id) ON DELETE CASCADE,
    approval_id text REFERENCES remediation_approvals(approval_id),
    event_type text NOT NULL,
    actor_id text NOT NULL,
    outcome text NOT NULL,
    tool_name text NOT NULL,
    target text NOT NULL,
    parameters_hash char(64) NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
"""
