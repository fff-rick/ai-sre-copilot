"""CLI entrypoint for idempotent knowledge catalog ingestion."""

import argparse
import asyncio
from pathlib import Path

from ai_sre_investigation.config import get_settings
from ai_sre_investigation.embedding_client import OpenAICompatibleEmbeddingClient
from ai_sre_investigation.knowledge import KnowledgeImporter
from ai_sre_investigation.knowledge_postgres import PostgresKnowledgeRepository
from ai_sre_investigation.repository import PostgresInvestigationRepository


def main() -> None:  # pragma: no cover - exercised by stage-4 integration gate
    parser = argparse.ArgumentParser(description="Import the AI-SRE knowledge catalog")
    parser.add_argument("catalog", type=Path, help="Path to catalog.json")
    arguments = parser.parse_args()
    asyncio.run(_import(arguments.catalog))


async def _import(  # pragma: no cover - exercised by stage-4 integration gate
    catalog: Path,
) -> None:
    settings = get_settings()
    base_url = settings.embedding_base_url or settings.model_base_url
    api_key = settings.embedding_api_key or settings.model_api_key
    if not all((settings.database_url, base_url, api_key, settings.embedding_model_id)):
        raise SystemExit("database and embedding base URL, API key, and model ID are required")
    pool = PostgresInvestigationRepository.pool(str(settings.database_url))
    await pool.open(wait=True)
    client = OpenAICompatibleEmbeddingClient(
        base_url=str(base_url),
        api_key=str(api_key),
        model=str(settings.embedding_model_id),
        timeout_seconds=settings.embedding_timeout_seconds,
        dimensions=settings.embedding_dimensions,
    )
    try:
        repository = PostgresKnowledgeRepository(pool)
        await repository.setup()
        imported = await KnowledgeImporter(repository, client).import_catalog(catalog)
        print(f"imported {len(imported)} knowledge documents")
    finally:
        await client.close()
        await pool.close()


if __name__ == "__main__":  # pragma: no cover
    main()
