"""Cross-language smoke test against the live stage-1 observability testbed."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ai_sre_investigation.ports import ToolRequest
from ai_sre_investigation.tool_gateway_client import GrpcToolClient


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--token", required=True)
    arguments = parser.parse_args()
    client = GrpcToolClient(
        arguments.target, arguments.token, actor_id="stage2-acceptance"
    )
    now = datetime.now(UTC)
    start = now - timedelta(minutes=15)

    async def call(tool_name: str, tool_arguments: dict[str, Any]) -> Any:
        return await client.execute_read(
            ToolRequest(
                investigation_id="stage2-e2e",
                trace_id="0123456789abcdef",
                tool_name=tool_name,
                arguments=tool_arguments,
            )
        )

    try:
        prometheus, loki, tempo_search, releases, commit = await asyncio.gather(
            call("prometheus.query", {"promql": "up"}),
            call(
                "loki.query_range",
                {
                    "logql": '{service_name=~".+"}',
                    "start": start,
                    "end": now,
                    "limit": 20,
                },
            ),
            call(
                "tempo.search_traces",
                {"traceql": "{ true }", "start": start, "end": now, "limit": 20},
            ),
            call(
                "releases.list",
                {
                    "service": "payment",
                    "start": now - timedelta(days=7),
                    "end": now,
                    "limit": 20,
                },
            ),
            call("git.get_commit", {"revision": "HEAD", "max_changed_files": 20}),
        )
        assert isinstance(tempo_search.data, dict)
        traces = tempo_search.data.get("traces")
        assert isinstance(traces, list) and traces, "Tempo search returned no traces"
        trace_id = traces[0]["traceID"]
        tempo_trace = await call("tempo.get_trace", {"trace_id": trace_id})
        results = [prometheus, loki, tempo_search, tempo_trace, releases, commit]
        assert all(result.source_ref for result in results)
        assert all(
            result.data is not None or result.artifact is not None for result in results
        )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "live_tools": [result.tool_name for result in results],
                    "trace_id": trace_id,
                },
                ensure_ascii=False,
            )
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
