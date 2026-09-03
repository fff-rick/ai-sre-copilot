#!/usr/bin/env python3
"""Run the stage-4 retrieval baseline independently from the investigation LLM."""

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_sre_investigation.embedding_client import HashEmbeddingClient
from ai_sre_investigation.knowledge import (
    InMemoryKnowledgeRepository,
    KnowledgeImporter,
    KnowledgeSearchFilter,
)


async def run(catalog: Path, dataset: Path) -> dict[str, Any]:
    embeddings = HashEmbeddingClient(64)
    repository = InMemoryKnowledgeRepository()
    documents = await KnowledgeImporter(repository, embeddings).import_catalog(catalog)
    definition = json.loads(dataset.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    for case in definition["cases"]:
        vector = (await embeddings.embed([case["query"]]))[0]
        hits = await repository.search(
            case["query"],
            vector,
            KnowledgeSearchFilter(
                service=case.get("service"), environment=case.get("environment")
            ),
            5,
        )
        expected = set(case["expected_source_refs"])
        ranks = [
            rank
            for rank, hit in enumerate(hits, 1)
            if hit.source_ref in expected
        ]
        first_rank = min(ranks) if ranks else None
        results.append(
            {
                "id": case["id"],
                "language": case["language"],
                "first_relevant_rank": first_rank,
                "retrieved": [hit.source_ref for hit in hits],
                "recall_at_1": bool(first_rank and first_rank <= 1),
                "recall_at_3": bool(first_rank and first_rank <= 3),
                "recall_at_5": bool(first_rank and first_rank <= 5),
                "reciprocal_rank": 1 / first_rank if first_rank else 0,
            }
        )

    def metrics(items: list[dict[str, Any]]) -> dict[str, float | int]:
        count = len(items)
        return {
            "cases": count,
            "recall_at_1": sum(item["recall_at_1"] for item in items) / count,
            "recall_at_3": sum(item["recall_at_3"] for item in items) / count,
            "recall_at_5": sum(item["recall_at_5"] for item in items) / count,
            "mrr": sum(item["reciprocal_rank"] for item in items) / count,
        }

    by_language: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_language[result["language"]].append(result)
    return {
        "dataset": definition["dataset"],
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "postgres-compatible-simple-keyword+exact-cosine+rrf-k60",
        "embedding": "offline-feature-hash-v1-64d",
        "documents": len(documents),
        "overall": metrics(results),
        "by_language": {key: metrics(value) for key, value in sorted(by_language.items())},
        "cases": results,
    }


def markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    rows = [
        "# Stage 4 Retrieval Baseline",
        "",
        f"- Dataset: `{report['dataset']}`",
        f"- Strategy: `{report['strategy']}`",
        f"- Embedding: `{report['embedding']}`",
        f"- Documents: {report['documents']}",
        "",
        "| Scope | Cases | Recall@1 | Recall@3 | Recall@5 | MRR |",
        "|---|---:|---:|---:|---:|---:|",
        (
            f"| overall | {overall['cases']} | {overall['recall_at_1']:.3f} | "
            f"{overall['recall_at_3']:.3f} | {overall['recall_at_5']:.3f} | "
            f"{overall['mrr']:.3f} |"
        ),
    ]
    for language, metrics in report["by_language"].items():
        rows.append(
            f"| language={language} | {metrics['cases']} | {metrics['recall_at_1']:.3f} | "
            f"{metrics['recall_at_3']:.3f} | {metrics['recall_at_5']:.3f} | "
            f"{metrics['mrr']:.3f} |"
        )
    rows.extend(
        [
            "",
            "This is a deterministic pipeline baseline, not a claim about production embedding quality.",
            "The language split intentionally exposes the `simple` tokenizer limitation.",
        ]
    )
    return "\n".join(rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    arguments = parser.parse_args()
    report = asyncio.run(run(arguments.catalog, arguments.dataset))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    if arguments.markdown_output:
        arguments.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(rendered)
    if report["overall"]["recall_at_5"] < 1:
        raise SystemExit("Recall@5 gate failed")


if __name__ == "__main__":
    main()
