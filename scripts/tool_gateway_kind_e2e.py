"""Cross-language smoke test for the two Kubernetes read tools."""

import argparse
import asyncio
import json

from ai_sre_investigation.ports import ToolRequest
from ai_sre_investigation.tool_gateway_client import GrpcToolClient


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--token", required=True)
    arguments = parser.parse_args()
    client = GrpcToolClient(arguments.target, arguments.token, actor_id="stage2-kind")
    try:
        workload, events = await asyncio.gather(
            client.execute_read(
                ToolRequest(
                    investigation_id="stage2-kind",
                    trace_id="fedcba9876543210",
                    tool_name="kubernetes.get_workload",
                    arguments={
                        "namespace": "ai-sre-stage2",
                        "kind": "Deployment",
                        "name": "gateway-fixture",
                    },
                )
            ),
            client.execute_read(
                ToolRequest(
                    investigation_id="stage2-kind",
                    trace_id="fedcba9876543210",
                    tool_name="kubernetes.list_events",
                    arguments={
                        "namespace": "ai-sre-stage2",
                        "involved_object_kind": "Deployment",
                        "involved_object_name": "gateway-fixture",
                        "limit": 10,
                    },
                )
            ),
        )
        assert isinstance(workload.data, dict)
        assert workload.data["kind"] == "Deployment"
        assert isinstance(events.data, dict)
        event_items = events.data["events"]
        assert isinstance(event_items, list) and event_items
        print(
            json.dumps(
                {
                    "status": "passed",
                    "live_tools": [workload.tool_name, events.tool_name],
                    "event_count": len(event_items),
                }
            )
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
