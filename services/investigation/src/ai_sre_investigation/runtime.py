"""Production resource lifecycle for PostgreSQL, model, and tool adapters."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ai_sre_investigation.config import Settings
from ai_sre_investigation.embedding_client import OpenAICompatibleEmbeddingClient
from ai_sre_investigation.knowledge import KnowledgeRetriever
from ai_sre_investigation.knowledge_postgres import PostgresKnowledgeRepository
from ai_sre_investigation.model_client import OpenAICompatibleModelClient
from ai_sre_investigation.remediation import PostgresRemediationRepository
from ai_sre_investigation.remediation_service import RemediationService
from ai_sre_investigation.repository import PostgresInvestigationRepository
from ai_sre_investigation.service import InvestigationService
from ai_sre_investigation.tool_gateway_client import GrpcToolClient
from ai_sre_investigation.workflow import InvestigationWorkflow


@asynccontextmanager
async def production_service(  # pragma: no cover - exercised by compose acceptance
    settings: Settings,
) -> AsyncIterator[InvestigationService | None]:
    """Create a durable runtime only when every mandatory credential is present."""

    if not all(
        (
            settings.database_url,
            settings.tool_gateway_token,
            settings.model_base_url,
            settings.model_api_key,
            settings.model_id,
        )
    ):
        yield None
        return

    database_url = str(settings.database_url)
    pool = PostgresInvestigationRepository.pool(database_url)
    await pool.open(wait=True)
    repository = PostgresInvestigationRepository(pool)
    await repository.setup()
    remediation_repository = PostgresRemediationRepository(pool)
    await remediation_repository.setup()
    knowledge_repository = PostgresKnowledgeRepository(pool)
    await knowledge_repository.setup()
    # Builtin-only deserialization prevents checkpoint rows from constructing Python objects.
    serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
    async with AsyncPostgresSaver.from_conn_string(database_url, serde=serializer) as checkpointer:
        await checkpointer.setup()
        model = OpenAICompatibleModelClient(
            base_url=str(settings.model_base_url),
            api_key=str(settings.model_api_key),
            model=str(settings.model_id),
            timeout_seconds=settings.model_timeout_seconds,
        )
        tools = GrpcToolClient(
            settings.tool_gateway_target,
            str(settings.tool_gateway_token),
            actor_id="investigation-service",
        )
        embedding_client: OpenAICompatibleEmbeddingClient | None = None
        knowledge: KnowledgeRetriever | None = None
        if settings.embedding_model_id:
            embedding_client = OpenAICompatibleEmbeddingClient(
                base_url=str(settings.embedding_base_url or settings.model_base_url),
                api_key=str(settings.embedding_api_key or settings.model_api_key),
                model=settings.embedding_model_id,
                timeout_seconds=settings.embedding_timeout_seconds,
                dimensions=settings.embedding_dimensions,
            )
            knowledge = KnowledgeRetriever(knowledge_repository, embedding_client)
        workflow = InvestigationWorkflow(
            model=model,
            tools=tools,
            checkpointer=checkpointer,
            cancel_check=repository.is_cancel_requested,
            knowledge=knowledge,
            event_sink=repository.append_event,
        )
        remediation = RemediationService(
            investigations=repository,
            repository=remediation_repository,
            tools=tools,
            mutations=tools,
            allowed_namespace=settings.mutation_allowed_namespace,
            validation_delay_seconds=settings.remediation_validation_delay_seconds,
        )
        service = InvestigationService(
            repository=repository, workflow=workflow, remediation=remediation
        )
        await service.start()
        try:
            yield service
        finally:
            await service.stop()
            await model.close()
            if embedding_client is not None:
                await embedding_client.close()
            await tools.close()
            await pool.close()
