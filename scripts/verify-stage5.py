"""Cross-process phase-5 acceptance against PostgreSQL and a kind cluster."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ai_sre_investigation.domain import Alert, Investigation, Severity, TimeWindow
from ai_sre_investigation.ports import MutationRequest
from ai_sre_investigation.remediation import (
    PostgresRemediationRepository,
    RemediationAction,
)
from ai_sre_investigation.repository import PostgresInvestigationRepository
from ai_sre_investigation.tool_gateway_client import GrpcToolClient, ToolGatewayError


def action(
    identifier: str,
    tool_name: str,
    *,
    replicas: int | None = None,
    revision: int | None = None,
) -> RemediationAction:
    return RemediationAction(
        action_id=identifier,
        tool_name=tool_name,
        namespace="ai-sre-test",
        name="remediation-fixture",
        replicas=replicas,
        revision=revision,
        description="Stage 5 isolated mutation acceptance.",
        expected_effect="The typed Deployment mutation is visible in kind.",
        rollback_plan="Restore the previous template and replica count.",
        verification_promql="up",
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--token", required=True)
    arguments = parser.parse_args()

    pool = PostgresInvestigationRepository.pool(arguments.database_url)
    await pool.open(wait=True)
    investigations = PostgresInvestigationRepository(pool)
    approvals = PostgresRemediationRepository(pool)
    await investigations.setup()
    await approvals.setup()
    now = datetime.now(UTC)
    investigation_id = f"inv-stage5-{uuid4()}"
    run_id = uuid4().hex[:8]
    investigation = Investigation(
        investigation_id=investigation_id,
        trace_id=uuid4().hex[:16],
        alert=Alert(
            alert_id="stage5-kind",
            service="remediation-fixture",
            severity=Severity.WARNING,
            summary="Phase 5 mutation acceptance",
            time_window=TimeWindow(start=now - timedelta(minutes=5), end=now),
            source_ref="acceptance://stage5/kind",
        ),
        created_at=now,
        updated_at=now,
    )
    await investigations.create(investigation)
    async with pool.connection() as connection:
        await connection.execute(
            """UPDATE investigations
               SET run_requested=false, status='COMPLETED'
               WHERE investigation_id=%s""",
            (investigation_id,),
        )
    client = GrpcToolClient(arguments.target, arguments.token, actor_id="stage5-acceptance")

    async def grant(item: RemediationAction, expires: int = 900) -> tuple[str, str]:
        approval = await approvals.create(investigation_id, item, "acceptance-proposer")
        approved = await approvals.approve(approval.approval_id, "acceptance-approver", expires)
        return approval.approval_id, approved.approval_token

    async def execute(item: RemediationAction, approval_token: str, idempotency_key: str):
        return await client.execute_mutation(
            MutationRequest(
                investigation_id=investigation_id,
                trace_id=investigation.trace_id,
                actor_id="acceptance-approver",
                approval_token=approval_token,
                idempotency_key=idempotency_key,
                tool_name=item.tool_name,
                parameters=item.parameters(),
            )
        )

    try:
        expired_action = action("act-expired", "kubernetes.restart_deployment")
        _, expired_token = await grant(expired_action, -1)
        try:
            await execute(expired_action, expired_token, f"stage5:{run_id}:expired")
        except ToolGatewayError as error:
            assert error.code == "TOOL_ERROR_CODE_PERMISSION_DENIED"
        else:
            raise AssertionError("expired token was accepted")

        original = action("act-modified-old", "kubernetes.scale_deployment", replicas=4)
        modified_approval_id, old_token = await grant(original)
        await approvals.modify(
            modified_approval_id,
            action("act-modified-new", "kubernetes.scale_deployment", replicas=5),
            "acceptance-approver",
        )
        try:
            await execute(original, old_token, f"stage5:{run_id}:modified")
        except ToolGatewayError as error:
            assert error.code == "TOOL_ERROR_CODE_PERMISSION_DENIED"
        else:
            raise AssertionError("token survived an approval parameter modification")

        scale = action("act-scale", "kubernetes.scale_deployment", replicas=2)
        scale_approval_id, scale_token = await grant(scale)
        tampered = action("act-scale-tampered", "kubernetes.scale_deployment", replicas=10)
        try:
            await execute(tampered, scale_token, f"stage5:{run_id}:tamper")
        except ToolGatewayError as error:
            assert error.code == "TOOL_ERROR_CODE_PERMISSION_DENIED"
        else:
            raise AssertionError("parameter tampering was accepted")

        first = await execute(scale, scale_token, f"stage5:{run_id}:scale")
        replay = await execute(scale, scale_token, f"stage5:{run_id}:scale")
        assert first.approval_id == scale_approval_id
        assert first.status == "SUCCEEDED" and not first.replayed
        assert replay.replayed and replay.execution_id == first.execution_id

        restart = action("act-restart", "kubernetes.restart_deployment")
        _, restart_token = await grant(restart)
        restarted = await execute(restart, restart_token, f"stage5:{run_id}:restart")
        assert restarted.status == "SUCCEEDED"

        rollback = action("act-rollback", "kubernetes.rollback_deployment", revision=1)
        _, rollback_token = await grant(rollback)
        rolled_back = await execute(rollback, rollback_token, f"stage5:{run_id}:rollback")
        assert rolled_back.status == "SUCCEEDED"

        async with pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT event_type FROM remediation_audit_events
                WHERE investigation_id=%s ORDER BY event_id
                """,
                (investigation_id,),
            )
            event_types = [row["event_type"] for row in await cursor.fetchall()]
        assert "approval.expired" in event_types
        assert "mutation.binding_rejected" in event_types
        assert event_types.count("mutation.authorized") == 3
        assert event_types.count("mutation.succeeded") == 3
        print(
            json.dumps(
                {
                    "status": "passed",
                    "investigation_id": investigation_id,
                    "executions": [
                        first.execution_id,
                        restarted.execution_id,
                        rolled_back.execution_id,
                    ],
                    "audit_events": len(event_types),
                }
            )
        )
    finally:
        await client.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
