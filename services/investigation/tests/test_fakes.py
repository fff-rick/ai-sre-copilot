import asyncio

import pytest

from ai_sre_investigation.fakes import FakeModelClient, FakeToolClient
from ai_sre_investigation.ports import ModelRequest, ToolRequest


def test_fake_model_records_requests() -> None:
    client = FakeModelClient({"hypotheses": []})
    request = ModelRequest(
        system_instructions="Return evidence-backed hypotheses.",
        input_text="alert payload",
        response_schema="HypothesisListV1",
    )

    response = asyncio.run(client.complete(request))

    assert response.data == {"hypotheses": []}
    assert client.requests == [request]


def test_fake_tool_rejects_unregistered_tools() -> None:
    client = FakeToolClient({})
    request = ToolRequest(
        investigation_id="inv-1",
        tool_name="prometheus.query",
        arguments={"query": "up"},
    )

    with pytest.raises(LookupError, match="unregistered fake tool"):
        asyncio.run(client.execute_read(request))


def test_fake_tool_returns_registered_fixture() -> None:
    client = FakeToolClient({"prometheus.query": {"value": 1}})
    request = ToolRequest(
        investigation_id="inv-1",
        tool_name="prometheus.query",
        arguments={"query": "up"},
    )

    response = asyncio.run(client.execute_read(request))

    assert response.data == {"value": 1}
    assert response.source_ref == "fake://prometheus.query"
